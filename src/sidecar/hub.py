"""大肥鱼中转站 · sidecar 入口。

只负责 HTTP 路由与上游转发，业务逻辑在各自模块：
  config.py   配置（唯一来源）
  persona.py  人格读取
  upstream.py 上游 Key 与消息清洗
  memory.py   召回 / 提炼 / 写回 / 归档
  knowledge.py 知识库检索
  prompt.py   提示词组装
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import memory
import persona
import prompt
import sync
from config import CFG, CONFIG_PATH, LOG_PATH, SIDECAR_VERSION
from upstream import read_upstream
from util import init_log, log, safe_json

init_log(LOG_PATH)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'DafeiyuHub/' + '0.3'

    def log_message(self, fmt, *args):
        # 健康检查与模型列表每几秒一次，不写日志，避免淹没真实事件
        if self.path.startswith('/health') or self.path.startswith('/v1/models'):
            return
        log(f'{self.address_string()} {fmt % args}')

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------------- GET ----------------
    def do_GET(self):
        if self.path.startswith('/health'):
            text = persona.read_persona(CFG)
            up = read_upstream(CFG)
            lm = memory.probe_lm(CFG)
            kb = CFG.get('knowledge') or {}
            self._json(200, {
                'status': 'ok',
                'version': SIDECAR_VERSION,
                'persona_id': CFG.get('persona_id'),
                'persona_chars': len(text),
                'upstream_model': up['model'],
                'upstream_base': up['base'],
                'upstream_key_loaded': bool(up['key']),
                'lm_connected': lm['connected'],
                'lm_reason': lm.get('reason', ''),
                'knowledge_enabled': bool(kb.get('enabled')),
                'knowledge_kbs': len(kb.get('kb_names') or []),
                'port': CFG.get('port'),
                'config_file': str(CONFIG_PATH),
            })
            return

        if self.path.startswith('/v1/models'):
            self._json(200, {
                'object': 'list',
                'data': [{'id': CFG.get('upstream_model'), 'object': 'model', 'owned_by': 'dafeiyu-hub'}],
            })
            return

        self._json(404, {'error': {'message': 'not found'}})

    # ---------------- POST ----------------
    def do_POST(self):
        if not self.path.startswith('/v1/chat/completions'):
            self._json(404, {'error': {'message': 'not found'}})
            return

        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length) if length else b'{}'
        payload = safe_json(raw, None)
        if payload is None:
            self._json(400, {'error': {'message': 'invalid json'}})
            return

        up = read_upstream(CFG)
        if not up['key']:
            self._json(500, {'error': {'message': '未找到上游 API Key（检查 AstrBot 配置路径）'}})
            return

        model = payload.get('model') or up['model']
        stream = bool(payload.get('stream'))
        from upstream import last_user_text

        user_text = last_user_text(payload.get('messages') or [])

        body = {'model': model, 'messages': prompt.build_messages(CFG, payload), 'stream': stream}
        for k in ('temperature', 'top_p', 'max_tokens', 'stop'):
            if k in payload:
                body[k] = payload[k]

        req = urllib.request.Request(
            up['base'].rstrip('/') + '/chat/completions',
            data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
            headers={'Authorization': f"Bearer {up['key']}", 'Content-Type': 'application/json'},
            method='POST',
        )
        log(f"POST {self.path} stream={stream} model={model} msgs={len(body['messages'])}")

        try:
            resp = urllib.request.urlopen(
                req, timeout=float(CFG.get('upstream_timeout') or 180), context=ssl.create_default_context()
            )
        except urllib.error.HTTPError as e:
            detail = e.read().decode('utf-8', 'ignore')[:400]
            log(f'上游 HTTP {e.code}: {detail}')
            self._json(e.code, {'error': {'message': detail}})
            return
        except Exception as e:
            log(f'上游连接失败: {e}')
            self._json(502, {'error': {'message': str(e)}})
            return

        if stream:
            self._handle_stream(resp, user_text)
            return

        data = resp.read()
        resp.close()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

        reply = ''
        try:
            reply = json.loads(data.decode('utf-8', 'ignore'))['choices'][0]['message']['content']
        except Exception:
            pass
        if user_text and reply:
            memory.schedule_memory_job(CFG, user_text, reply)

    def _handle_stream(self, resp, user_text: str) -> None:
        self.close_connection = True
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()

        chunks = []
        try:
            for line in resp:
                self.wfile.write(line)
                self.wfile.flush()
                text = line.decode('utf-8', 'ignore').strip()
                if text.startswith('data: ') and text != 'data: [DONE]':
                    try:
                        obj = json.loads(text[6:])
                        delta = obj['choices'][0]['delta'].get('content')
                        if delta:
                            chunks.append(delta)
                    except Exception:
                        pass
        except (BrokenPipeError, ConnectionResetError):
            log('客户端断开（流式）')
        finally:
            resp.close()

        reply = ''.join(chunks)
        if user_text and reply:
            memory.schedule_memory_job(CFG, user_text, reply)


def main() -> None:
    memory.init_archive(CFG)
    sync.start(CFG)
    port = int(CFG.get('port') or 8787)
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)

    text = persona.read_persona(CFG)
    up = read_upstream(CFG)
    kb = CFG.get('knowledge') or {}
    log(
        f"===== 启动 v0.3 端口 {port} 人格 {len(text)} 字 上游 {up['model']} "
        f"key={bool(up['key'])} 知识库={'开' if kb.get('enabled') else '关'}({len(kb.get('kb_names') or [])}库) ====="
    )
    print(f'[ready] http://127.0.0.1:{port}/v1', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log('收到中断信号')


if __name__ == '__main__':
    main()
