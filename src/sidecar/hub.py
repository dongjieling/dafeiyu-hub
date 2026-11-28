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
from pathlib import Path
from dataclasses import asdict
import ssl
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import memories
import mcp_register
import clients
import memory
import migrations
import outbox
import persona
import prompt
import patch_manager
import retry
import scope
import selftest
import sync
import telemetry
from config import CFG, CONFIG_PATH, LOG_PATH, SIDECAR_VERSION
from upstream import read_upstream
from util import init_log, log, safe_json

init_log(LOG_PATH)


def archive_stats() -> dict:
    """归档库统计：QQ / 桌面条数、上次同步时间。"""
    import sqlite3

    from config import data_path

    db = data_path((CFG.get('archive') or {}).get('db_path') or 'archive.db')
    if not db.exists():
        return {'qq': 0, 'local': 0, 'last_sync': 0}
    try:
        con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=5)
        qq = con.execute(
            "select count(*) from messages m join sessions s on s.id=m.session_id where s.source='qq'"
        ).fetchone()[0]
        local = con.execute(
            "select count(*) from messages m join sessions s on s.id=m.session_id where s.source='airi'"
        ).fetchone()[0]
        row = con.execute("select value from kv where key='qq_last_sync'").fetchone()
        con.close()
        return {'qq': qq, 'local': local, 'last_sync': float(row[0]) if row else 0}
    except Exception:
        return {'qq': 0, 'local': 0, 'last_sync': 0}


def _archive_encrypted() -> bool:
    """归档是否启用字段加密（看配置，不看数据）。"""
    return bool(((CFG.get('archive') or {}).get('encrypt', True)))


_compat_state: dict = {'report': {}, 'checked_at': 0.0}


def _lm_db_path(cfg: dict) -> Path:
    raw = (cfg.get('lm_db') or '').strip()
    if raw:
        return Path(raw)
    return Path.home() / '.astrbot' / 'data' / 'plugin_data' / 'astrbot_plugin_livingmemory' / 'livingmemory.db'


def memory_health(cfg: dict) -> dict:
    """/health 里的记忆体检数据（只读，缓存 60 秒）。"""
    global _MEMORY_HEALTH_CACHE
    now = time.time()
    if '_MEMORY_HEALTH_CACHE' in globals() and now - _MEMORY_HEALTH_CACHE[0] < 60:
        return _MEMORY_HEALTH_CACHE[1]

    import sqlite3

    from config import data_path

    out = {'archive': {}, 'lm': {}, 'ok': False}
    try:
        arc = cfg.get('archive') or {}
        db = data_path(arc.get('db_path') or 'archive.db')
        out['archive'] = migrations.archive_stats(db)
    except Exception as e:
        out['archive'] = {'error': str(e)[:80]}

    lm_db = _lm_db_path(cfg)
    out['lm_path'] = str(lm_db)
    try:
        if lm_db.exists():
            con = sqlite3.connect(f'file:{lm_db}?mode=ro', uri=True, timeout=5)
            try:
                def count(sql: str) -> int:
                    try:
                        return con.execute(sql).fetchone()[0]
                    except Exception:
                        return 0

                out['lm'] = {
                    'documents': count('select count(*) from documents'),
                    'atoms': count('select count(*) from memory_atoms'),
                    'graph_nodes': count('select count(*) from graph_nodes'),
                    'graph_edges': count('select count(*) from graph_edges'),
                }
                out['ok'] = True
            finally:
                con.close()
    except Exception as e:
        out['lm'] = {'error': str(e)[:80]}

    _MEMORY_HEALTH_CACHE = (now, out)
    return out


def startup_contracts(cfg: dict, migration_ok: bool, config_exists: bool) -> list:
    """启动时的 FATAL 检查（可单测）。"""
    from contracts import ContractResult, Severity
    from upstream import read_upstream

    out = []
    if not config_exists:
        out.append(ContractResult('hub.config', False, Severity.FATAL, detail='配置文件不存在'))

    up = read_upstream(cfg)
    if not (up.get('key') or '').strip():
        out.append(ContractResult('hub.upstream', False, Severity.FATAL,
                                  detail='上游 API Key 缺失（检查 AstrBot 配置路径）'))

    if not migration_ok:
        out.append(ContractResult('hub.archive', False, Severity.FATAL,
                                  detail='归档库迁移失败，已拒绝启动'))
    return out


def compat_report() -> dict:
    """最近一次契约检查结果（不含正文，可安全暴露）。"""
    return _compat_state['report']


def refresh_compat(auto_patch: bool = True, startup_checks: bool = False,
                   migration_ok: bool = True) -> dict:
    """重新探测所有已知契约（1b 起含 LM / scope / 补丁）。"""
    from adapters import AstrBotAdapter, LivingMemoryAdapter
    from contracts import CompatReport

    report = CompatReport()

    def probe(name: str, fn) -> None:
        from contracts import ContractResult, Severity

        try:
            report.add(fn())
        except Exception as e:
            log(f'契约检查异常（{name}）: {e}')
            report.add(ContractResult(name, False, Severity.DEGRADED, detail=str(e)[:80]))

    probe('astrbot.persona', lambda: AstrBotAdapter(CFG).probe_persona())
    probe('livingmemory.api', lambda: LivingMemoryAdapter(CFG).probe())
    probe('astrbot.write_scope', lambda: scope.validate(CFG))

    try:
        report.add(patch_manager.probe(auto_apply=auto_patch and patch_manager.AUTO_APPLY_DEFAULT))
    except Exception as e:
        from contracts import ContractResult, Severity

        log(f'契约检查异常（补丁）: {e}')
        report.add(ContractResult('livingmemory.patch', False, Severity.DEGRADED, detail=str(e)[:80]))

    if startup_checks:
        for item in startup_contracts(CFG, migration_ok, CONFIG_PATH.exists()):
            report.add(item)

    data = report.to_dict()
    _compat_state['report'] = data['contracts']
    _compat_state['checked_at'] = time.time()
    if data['overall'] != 'ok':
        failed = [k for k, v in data['contracts'].items() if not v['ok']]
        log(f"契约检查: {data['overall']}（{', '.join(failed) or '无失败项'}）")
    return data


def start_compat_monitor(interval: int = 300) -> None:
    def loop():
        time.sleep(10)
        while True:
            try:
                refresh_compat(auto_patch=True)
            except Exception as e:
                log(f'契约监控异常: {e}')
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True, name='compat-monitor').start()
    log(f'契约监控已启动（每 {interval} 秒）')


def _save_config() -> None:
    """把当前 CFG 写回数据目录的 config.json（原子替换）。"""
    import os

    tmp = CONFIG_PATH.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(CFG, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, CONFIG_PATH)


def archive_config(cfg: dict) -> dict:
    arc = cfg.get('archive') or {}
    return {
        'scope_mode': arc.get('scope_mode') or 'private_or_whitelist',
        'whitelist_groups': [str(x) for x in (arc.get('whitelist_groups') or [])],
        'memory_edit_enabled': bool(cfg.get('memory_edit_enabled', True)),
    }


def set_archive_config(cfg: dict, patch: dict) -> dict:
    arc = cfg.setdefault('archive', {})
    mode = (patch.get('scope_mode') or '').strip()
    if mode in ('all', 'private_only', 'private_or_whitelist'):
        arc['scope_mode'] = mode
    if 'whitelist_groups' in patch:
        groups = patch.get('whitelist_groups') or []
        arc['whitelist_groups'] = [str(g).strip() for g in groups if str(g).strip()]
    if 'memory_edit_enabled' in patch:
        cfg['memory_edit_enabled'] = bool(patch['memory_edit_enabled'])
    _save_config()
    return archive_config(cfg)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'DafeiyuHub/' + '0.3'

    def log_message(self, fmt, *args):
        # 健康检查与模型列表每几秒一次，不写日志，避免淹没真实事件
        if self.path.startswith('/health') or self.path.startswith('/v1/models'):
            return
        log(f'{self.address_string()} {fmt % args}')

    def _memory_query(self) -> dict:
        from urllib.parse import parse_qs, urlparse

        q = parse_qs(urlparse(self.path).query)
        return {
            'query': (q.get('query') or [''])[0],
            'limit': int((q.get('limit') or ['30'])[0] or 30),
            'offset': int((q.get('offset') or ['0'])[0] or 0),
            'include_selftest': (q.get('include_selftest') or ['0'])[0] in ('1', 'true'),
            'kind': (q.get('kind') or ['all'])[0],
            'min_importance': float((q.get('min_importance') or ['0'])[0] or 0),
            'sort': (q.get('sort') or ['new'])[0],
            'date_from': (q.get('date_from') or [''])[0],
            'date_to': (q.get('date_to') or [''])[0],
        }

    def _body_json(self) -> dict:
        try:
            import json as _json

            n = int(self.headers.get('Content-Length') or 0)
            if n <= 0:
                return {}
            return _json.loads(self.rfile.read(n).decode('utf-8') or '{}')
        except Exception:
            return {}

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------------- GET ----------------
    def do_GET(self):
        if self.path in ('/v1/clients', '/clients'):
            self._json(200, clients.load())
            return

        if self.path in ('/v1/mcp/status', '/mcp/status'):
            self._json(200, mcp_register.status(CFG))
            return

        if self.path in ('/v1/config/archive', '/config/archive'):
            self._json(200, archive_config(CFG))
            return

        if self.path in ('/v1/memories', '/memories') or self.path.startswith('/v1/memories?'):
            self._json(200, memories.list_memories(CFG, **self._memory_query()))
            return

        if self.path.startswith('/health'):
            snap = persona.snapshot(CFG)
            up = read_upstream(CFG)
            global _LM_PROBE_CACHE
            now = time.time()
            if '_LM_PROBE_CACHE' not in globals() or now - _LM_PROBE_CACHE[0] > 30:
                _LM_PROBE_CACHE = (now, memory.probe_lm(CFG))
            lm = _LM_PROBE_CACHE[1]
            kb = CFG.get('knowledge') or {}
            self._json(200, {
                'status': 'ok',
                'version': SIDECAR_VERSION,
                'persona_id': CFG.get('persona_id'),
                'persona_chars': snap.chars,
                'persona_ok': snap.ok and not snap.degraded,
                'persona_source': snap.source,
                'persona_stale': snap.stale,
                'persona_sha256': snap.sha256[:16],
                'persona_loaded_at': int(snap.loaded_at),
                'upstream_model': up['model'],
                'upstream_base': up['base'],
                'upstream_key_loaded': bool(up['key']),
                'lm_connected': lm['connected'],
                'lm_reason': lm.get('reason', ''),
                'knowledge_enabled': bool(kb.get('enabled')),
                'knowledge_kbs': len(kb.get('kb_names') or []),
                'port': CFG.get('port'),
                'config_file': str(CONFIG_PATH),
                'pending_retry': retry.stats(CFG)['pending_retry'],
                'dead_letters': retry.stats(CFG)['dead_letters'],
                'archive_qq': archive_stats().get('qq', 0),
                'archive_local': archive_stats().get('local', 0),
                'qq_last_sync': archive_stats().get('last_sync', 0),
                'compat': compat_report(),
                'compat_checked_at': int(_compat_state['checked_at']),
                'outbox': outbox.stats(CFG),
                'memory': memory_health(CFG),
                'pipeline': telemetry.summary(),
                'archive_encrypted': _archive_encrypted(),
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
    def _drain_body(self) -> None:
        """读完请求体，避免 keep-alive 连接里残留的 body 被当成下一个请求行。"""
        try:
            n = int(self.headers.get('Content-Length') or 0)
            if n > 0:
                self.rfile.read(n)
        except Exception:
            pass

    def do_POST(self):
        if self.path in ('/v1/config/archive', '/config/archive'):
            self._json(200, set_archive_config(CFG, self._body_json()))
            return

        if self.path in ('/v1/clients/save', '/clients/save'):
            body = self._body_json()
            data, err = clients.validate(body or {})
            if err:
                self._json(400, {'ok': False, 'error': err})
                return
            clients.save(data)
            self._json(200, {'ok': True, **data})
            return

        if self.path in ('/v1/mcp/register', '/mcp/register'):
            self._drain_body()
            self._json(200, mcp_register.register(CFG))
            return

        if self.path in ('/v1/memories/delete', '/memories/delete'):
            body = self._body_json()
            if (CFG.get('memory_edit_enabled', True) is False):
                self._json(403, {'ok': False, 'error': '记忆编辑已关闭（memory_edit_enabled=false）'})
                return
            self._json(200, memories.delete_memory(CFG, int((body or {}).get('id') or 0)))
            return

        if self.path in ('/v1/patch/status', '/patch/status'):
            self._drain_body()
            self._json(200, asdict(patch_manager.status()))
            return

        if self.path in ('/v1/patch/apply', '/patch/apply'):
            self._drain_body()
            self._json(200, asdict(patch_manager.apply()))
            return

        if self.path in ('/v1/selftest', '/selftest'):
            self._drain_body()
            self._handle_selftest()
            return
        if self.path in ('/v1/selftest/cleanup', '/selftest/cleanup'):
            self._drain_body()
            self._handle_selftest_cleanup()
            return

        if not self.path.startswith('/v1/chat/completions'):
            self._json(404, {'error': {'message': 'not found'}})
            return

        trace = telemetry.new_trace()
        self._trace = trace
        t0 = time.time()
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length) if length else b'{}'
        payload = safe_json(raw, None)
        if payload is None:
            self._json(400, {'error': {'message': 'invalid json'}})
            return

        client, auth_error = clients.authenticate(self.headers)
        if auth_error:
            telemetry.record('auth', 'denied', 0, auth_error, trace)
            self._json(401, {'error': {'message': auth_error}})
            return
        req_cfg = clients.effective_cfg(CFG, client)

        up = read_upstream(req_cfg)
        if not up['key']:
            self._json(500, {'error': {'message': '未找到上游 API Key（检查 AstrBot 配置路径）'}})
            return

        model = (client.get('model') or '').strip() or payload.get('model') or up['model']
        stream = bool(payload.get('stream'))
        from upstream import last_user_text

        user_text = last_user_text(payload.get('messages') or [])

        body = {'model': model, 'messages': prompt.build_messages(req_cfg, payload), 'stream': stream}
        for k in ('temperature', 'top_p', 'max_tokens', 'stop'):
            if k in payload:
                body[k] = payload[k]

        req = urllib.request.Request(
            up['base'].rstrip('/') + '/chat/completions',
            data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
            headers={'Authorization': f"Bearer {up['key']}", 'Content-Type': 'application/json'},
            method='POST',
        )
        log(f"POST {self.path} client={req_cfg.get('client_id')} scope={req_cfg.get('write_scope') or '-'} "
            f"stream={stream} model={model} msgs={len(body['messages'])}")

        try:
            resp = urllib.request.urlopen(
                req, timeout=float(CFG.get('upstream_timeout') or 180), context=ssl.create_default_context()
            )
        except urllib.error.HTTPError as e:
            detail = e.read().decode('utf-8', 'ignore')[:400]
            log(f'上游 HTTP {e.code}: {detail}')
            telemetry.record('upstream', 'http_error', int((time.time() - t0) * 1000),
                             f'{e.code} {detail[:80]}', trace)
            self._json(e.code, {'error': {'message': detail}})
            return
        except Exception as e:
            log(f'上游连接失败: {e}')
            telemetry.record('upstream', 'connect_error', int((time.time() - t0) * 1000),
                             str(e)[:80], trace)
            self._json(502, {'error': {'message': str(e)}})
            return

        if stream:
            self._handle_stream(resp, user_text, req_cfg)
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
        telemetry.record('upstream', 'ok', int((time.time() - t0) * 1000),
                         f'model={model} msgs={len(body["messages"])}', trace)
        if user_text and reply:
            memory.schedule_memory_job(req_cfg, memory.clean_for_extract(user_text),
                                       memory.clean_for_extract(reply), trace)

    def _handle_selftest(self) -> None:
        res = selftest.run(CFG)
        self._json(200, res)

    def _handle_selftest_cleanup(self) -> None:
        res = selftest.cleanup(CFG)
        self._json(200, res)

    def _handle_stream(self, resp, user_text: str, cfg: dict | None = None) -> None:
        self._trace = getattr(self, "_trace", "")
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
        telemetry.record('upstream_stream', 'ok' if reply else 'empty', 0,
                         f'{len(chunks)} chunks', getattr(self, '_trace', ''))
        if user_text and reply:
            memory.schedule_memory_job(cfg or CFG, memory.clean_for_extract(user_text),
                                       memory.clean_for_extract(reply), getattr(self, '_trace', ''))


def main() -> None:
    migration_ok = memory.init_archive(CFG)
    try:
        memories.cleanup_selftest(CFG)
    except Exception as e:
        log(f'清理自检记忆失败: {str(e)[:80]}')
    try:
        cleaned = memories.cleanup_index(CFG)
        if any(v > 0 for v in cleaned.values() if isinstance(v, int)):
            log(f'记忆索引清理: {cleaned}')
    except Exception as e:
        log(f'记忆索引清理失败: {str(e)[:80]}')
    report = refresh_compat(auto_patch=True, startup_checks=True, migration_ok=migration_ok)
    if report.get('overall') == 'fatal':
        failed = [k for k, v in report['contracts'].items() if v['severity'] == 'fatal']
        log(f"启动被拒绝（FATAL）: {', '.join(failed)}")
        print(f'[fatal] {", ".join(failed)}', flush=True)
        raise SystemExit(1)
    if report.get('overall') != 'ok':
        log(f"启动降级: {report.get('overall')}")
    start_compat_monitor()
    outbox.start(CFG)
    sync.start(CFG)
    port = int(CFG.get('port') or 8787)
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)

    text = persona.read_persona(CFG)
    up = read_upstream(CFG)
    kb = CFG.get('knowledge') or {}
    log(
        f"===== 启动 v{SIDECAR_VERSION} 端口 {port} 人格 {len(text)} 字 上游 {up['model']} "
        f"key={bool(up['key'])} 知识库={'开' if kb.get('enabled') else '关'}({len(kb.get('kb_names') or [])}库) ====="
    )
    print(f'[ready] http://127.0.0.1:{port}/v1', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log('收到中断信号')


if __name__ == '__main__':
    main()
