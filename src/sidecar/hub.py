"""
大肥鱼中转站 · Python sidecar
- OpenAI 兼容接口（供 AIRI / AstrBot 连接）
- 人格注入：从 AstrBot 数据库实时读取
- 上游转发：DeepSeek（key 从 AstrBot 配置读取，不重复保存）
- 健康检查：本地服务 / 记忆库 / 上游模型 三项状态

启动：python -X utf8 hub.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).parent

# 数据目录：打包后 sidecar 所在目录可能只读，由宿主通过 HUB_DATA_DIR 指定可写目录
DATA_DIR = Path(os.environ.get('HUB_DATA_DIR') or BASE_DIR)
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    DATA_DIR = BASE_DIR

CONFIG_PATH = BASE_DIR / 'config.json'
LOG_PATH = DATA_DIR / 'hub.log'

_log_lock = threading.Lock()


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    with _log_lock:
        try:
            with open(LOG_PATH, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception:
            pass
    print(line, flush=True)


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        except Exception as e:
            log(f'配置读取失败，使用默认值: {e}')
    return {}


CFG = load_config()

PERSONA_DB = Path(CFG.get('persona_db', ''))
CMD_CONFIG = Path(CFG.get('astrbot_config', ''))
PERSONA_ID = CFG.get('persona_id', 'dafeiyu')
UPSTREAM_SOURCE = CFG.get('upstream_source', 'deepseek')
FALLBACK_BASE = CFG.get('upstream_base', 'https://api.deepseek.com/v1')
FALLBACK_MODEL = CFG.get('upstream_model', 'deepseek-chat')
LM_BASE = CFG.get('lm_base', 'http://127.0.0.1:7801/page/external')
LM_KEY = CFG.get('lm_key', '')
PORT = int(os.environ.get('HUB_PORT') or CFG.get('port', 8787))

EXTRACT_CFG = CFG.get('extract', {}) or {}
EXTRACT_ENABLED = bool(EXTRACT_CFG.get('enabled', True))
EXTRACT_MODEL = EXTRACT_CFG.get('model', FALLBACK_MODEL)
EXTRACT_MIN_CHARS = int(EXTRACT_CFG.get('min_chars', 6))
EXTRACT_MIN_IMPORTANCE = float(EXTRACT_CFG.get('min_importance', 0.3))
EXTRACT_MAX = int(EXTRACT_CFG.get('max_per_turn', 2))
SCOPE = CFG.get('scope', 'airi:desktop')
# 写入用的作用域：默认跟随 QQ 私聊域，这样 QQ 侧召回也能看到桌宠聊的内容
WRITE_SCOPE = CFG.get('write_scope', SCOPE)

ARCHIVE_CFG = CFG.get('archive', {}) or {}
ARCHIVE_ENABLED = bool(ARCHIVE_CFG.get('enabled', True))
ARCHIVE_DB = DATA_DIR / ARCHIVE_CFG.get('db_path', 'archive.db')

# ---------------- 归档库 ----------------
_archive_lock = threading.Lock()


def init_archive() -> None:
    if not ARCHIVE_ENABLED:
        return
    # 首次使用数据目录时，把旧位置（sidecar 目录）的归档搬过来
    if DATA_DIR != BASE_DIR and not ARCHIVE_DB.exists():
        legacy = BASE_DIR / 'archive.db'
        if legacy.exists():
            try:
                import shutil

                shutil.copy2(legacy, ARCHIVE_DB)
                log(f'已迁移旧归档库到: {ARCHIVE_DB}')
            except Exception as e:
                log(f'归档库迁移失败: {e}')
    try:
        con = sqlite3.connect(ARCHIVE_DB, timeout=10)
        con.executescript(
            '''
            CREATE TABLE IF NOT EXISTS sessions (
              id INTEGER PRIMARY KEY,
              source TEXT NOT NULL,
              session_key TEXT NOT NULL,
              persona_id TEXT,
              created_at REAL NOT NULL,
              last_active REAL,
              UNIQUE(source, session_key)
            );
            CREATE TABLE IF NOT EXISTS messages (
              id INTEGER PRIMARY KEY,
              session_id INTEGER NOT NULL,
              role TEXT NOT NULL,
              content TEXT NOT NULL,
              created_at REAL NOT NULL,
              meta TEXT
            );
            CREATE TABLE IF NOT EXISTS extractions (
              id INTEGER PRIMARY KEY,
              message_id INTEGER,
              content TEXT NOT NULL,
              importance REAL DEFAULT 0.5,
              idempotency_key TEXT UNIQUE,
              lm_memory_id TEXT,
              status TEXT DEFAULT 'pending',
              error TEXT,
              created_at REAL NOT NULL,
              written_at REAL
            );
            CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
            '''
        )
        con.commit()
        con.close()
        log(f'归档库就绪: {ARCHIVE_DB.name}')
    except Exception as e:
        log(f'归档库初始化失败: {e}')


def archive_message(role: str, content: str) -> int | None:
    if not ARCHIVE_ENABLED or not content:
        return None
    try:
        with _archive_lock:
            con = sqlite3.connect(ARCHIVE_DB, timeout=10)
            now = time.time()
            con.execute(
                'INSERT OR IGNORE INTO sessions (source, session_key, persona_id, created_at, last_active) '
                "VALUES ('airi','desktop',?,?,?)",
                (PERSONA_ID, now, now),
            )
            sid = con.execute(
                "SELECT id FROM sessions WHERE source='airi' AND session_key='desktop'"
            ).fetchone()[0]
            con.execute(
                'UPDATE sessions SET last_active=? WHERE id=?', (now, sid)
            )
            cur = con.execute(
                'INSERT INTO messages (session_id, role, content, created_at) VALUES (?,?,?,?)',
                (sid, role, content, now),
            )
            con.commit()
            mid = cur.lastrowid
            con.close()
            return mid
    except Exception as e:
        log(f'归档写入失败: {e}')
        return None

# ---------------- 人格读取 ----------------
_persona = {'text': '', 'ts': 0.0}
PERSONA_TTL = 30


def read_persona() -> str:
    now = time.time()
    if _persona['text'] and now - _persona['ts'] < PERSONA_TTL:
        return _persona['text']

    text = ''
    if PERSONA_DB.exists():
        try:
            con = sqlite3.connect(f'file:{PERSONA_DB}?mode=ro', uri=True, timeout=5)
            row = con.execute(
                'select system_prompt from personas where persona_id = ?', (PERSONA_ID,)
            ).fetchone()
            con.close()
            if row:
                text = row[0] or ''
        except Exception as e:
            log(f'读取人格失败: {e}')

    if not text:
        fb = BASE_DIR / 'persona_fallback.txt'
        if fb.exists():
            text = fb.read_text(encoding='utf-8')
            log('使用人格兜底文件')

    _persona['text'] = text
    _persona['ts'] = now
    return text


# ---------------- 上游配置 ----------------
_upstream = {'cfg': None, 'ts': 0.0}


def read_upstream() -> dict:
    now = time.time()
    if _upstream['cfg'] and now - _upstream['ts'] < 300:
        return _upstream['cfg']

    base, key = FALLBACK_BASE, ''
    if CMD_CONFIG.exists():
        try:
            data = json.loads(CMD_CONFIG.read_text(encoding='utf-8-sig'))
            for src in data.get('provider_sources', []):
                if src.get('id') == UPSTREAM_SOURCE:
                    keys = src.get('key') or []
                    key = keys[0] if keys else ''
                    base = src.get('api_base') or base
                    break
        except Exception as e:
            log(f'读取上游配置失败: {e}')

    cfg = {'base': base, 'key': key, 'model': FALLBACK_MODEL}
    _upstream['cfg'] = cfg
    _upstream['ts'] = now
    return cfg


# ---------------- 记忆库探测 ----------------
_lm_probe = {'ok': False, 'reason': '', 'ts': 0.0}
LM_PROBE_TTL = 15


def probe_lm(force: bool = False) -> dict:
    now = time.time()
    if not force and now - _lm_probe['ts'] < LM_PROBE_TTL:
        return {'connected': _lm_probe['ok'], 'reason': _lm_probe['reason']}

    if not LM_KEY:
        _lm_probe.update({'ok': False, 'reason': '未配置 API Key', 'ts': now})
        return {'connected': False, 'reason': '未配置 API Key'}
    try:
        req = urllib.request.Request(
            f'{LM_BASE}/health', headers={'Authorization': f'Bearer {LM_KEY}'}
        )
        with urllib.request.urlopen(req, timeout=2.5) as r:
            ok = r.status == 200
        _lm_probe.update({'ok': ok, 'reason': '', 'ts': now})
        return {'connected': ok, 'reason': ''}
    except Exception as e:
        reason = str(e)[:80]
        _lm_probe.update({'ok': False, 'reason': reason, 'ts': now})
        return {'connected': False, 'reason': reason}


# ---------------- 记忆召回 ----------------
def recall_memories(query: str, k: int | None = None) -> list[str]:
    """调用 LivingMemory 的 recall 接口，失败时返回空列表（不阻塞聊天）"""
    if not LM_KEY or not query.strip():
        return []
    payload = {
        'query': query[:500],
        'k': int(k or CFG.get('recall_k', 5)),
        'persona_id': PERSONA_ID,
    }
    try:
        req = urllib.request.Request(
            f'{LM_BASE}/recall',
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={
                'Authorization': f'Bearer {LM_KEY}',
                'Content-Type': 'application/json',
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode('utf-8', 'ignore'))
    except Exception as e:
        log(f'召回失败: {str(e)[:120]}')
        return []

    items = data.get('memories') or data.get('results') or data.get('items') or []
    out = []
    for it in items:
        if isinstance(it, str):
            out.append(it)
        elif isinstance(it, dict):
            text = it.get('content') or it.get('text') or it.get('memory')
            if text:
                out.append(str(text))
    return out[:8]


def last_user_text(messages: list) -> str:
    for m in reversed(messages):
        if m.get('role') == 'user':
            content = m.get('content')
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = [p.get('text', '') for p in content if isinstance(p, dict)]
                return ' '.join(parts)
    return ''


# ---------------- 提示词注入 ----------------
def build_messages(payload: dict) -> list:
    persona = read_persona()
    messages = payload.get('messages') or []
    if not persona:
        return messages

    # 召回相关记忆（失败不影响主流程）
    memories = recall_memories(last_user_text(messages))
    if memories:
        log(f'召回 {len(memories)} 条记忆')

    memory_block = ''
    if memories:
        lines = '\n'.join(f'- {m}' for m in memories)
        memory_block = f'\n\n【你记得关于他的事】\n{lines}\n（自然地用上，别罗列，别复述）'

    system_text = persona + memory_block

    out, injected = [], False
    for m in messages:
        if m.get('role') == 'system' and not injected:
            content = m.get('content')
            if isinstance(content, str):
                out.append({'role': 'system', 'content': system_text + '\n\n' + content})
            else:
                out.append({'role': 'system', 'content': system_text})
                out.append(m)
            injected = True
        else:
            out.append(m)

    if not injected:
        out.insert(0, {'role': 'system', 'content': system_text})
    return out


# ---------------- 记忆写入（提炼 + 回写 LM） ----------------
EXTRACT_PROMPT = """从下面这轮对话里提取值得长期记住的信息（0-{max_n} 条）。

值得记：他的偏好、经历、关系、计划、情绪模式、重要事件
不值得：寒暄、语气词、重复内容、临时状态、AI 自己的话

每条要求：
- 不超过 40 字
- 用第三人称写（"他说……""他喜欢……"）
- 附重要性分数 0-1，越稳定越重要

只输出 JSON 数组，没有就输出 []
格式：[{{"content": "...", "importance": 0.7}}]"""


def _strip_code_fence(text: str) -> str:
    t = text.strip()
    if t.startswith('```'):
        t = t.split('\n', 1)[-1]
        if t.endswith('```'):
            t = t[:-3]
    return t.strip()


def extract_memories(user_text: str, reply_text: str) -> list[dict]:
    up = read_upstream()
    if not up['key']:
        return []
    prompt = EXTRACT_PROMPT.format(max_n=EXTRACT_MAX)
    convo = f'他说：{user_text[:600]}\n她说：{reply_text[:600]}'
    body = {
        'model': EXTRACT_MODEL,
        'messages': [
            {'role': 'system', 'content': prompt},
            {'role': 'user', 'content': convo},
        ],
        'temperature': 0.1,
        'max_tokens': 300,
    }
    req = urllib.request.Request(
        up['base'].rstrip('/') + '/chat/completions',
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers={'Authorization': f"Bearer {up['key']}", 'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=45, context=ssl.create_default_context()) as r:
            data = json.loads(r.read().decode('utf-8', 'ignore'))
        text = _strip_code_fence(data['choices'][0]['message']['content'])
        items = json.loads(text)
        if not isinstance(items, list):
            return []
        out = []
        for it in items[:EXTRACT_MAX]:
            if not isinstance(it, dict):
                continue
            content = str(it.get('content', '')).strip()
            importance = float(it.get('importance', 0.5) or 0.5)
            if len(content) < 4:
                continue
            if importance < EXTRACT_MIN_IMPORTANCE:
                continue
            # 过滤描述她自己/双方关系的内容（提示词要求第三人称写"他"，但模型偶尔会跑偏）
            if content.startswith(('她', '我', '你')):
                log(f'丢弃非事实条目: {content[:30]}')
                continue
            out.append({'content': content[:120], 'importance': round(min(max(importance, 0.0), 1.0), 2)})
        return out
    except Exception as e:
        log(f'提炼失败: {str(e)[:120]}')
        return []


def write_memories(items: list[dict], key_seed: str) -> int:
    if not items or not LM_KEY:
        return 0
    written = 0
    for idx, item in enumerate(items):
        payload = {
            'content': item['content'],
            'importance': item['importance'],
            'idempotency_key': f'{key_seed}#{idx}',
            'persona_id': PERSONA_ID,
            'scope': WRITE_SCOPE,
        }
        req = urllib.request.Request(
            f'{LM_BASE}/memories',
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={'Authorization': f'Bearer {LM_KEY}', 'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                res = json.loads(r.read().decode('utf-8', 'ignore'))
            mem_id = (res.get('memory') or {}).get('id') if isinstance(res, dict) else None
            written += 1
            _record_extraction(item, f'{key_seed}#{idx}', mem_id, 'written', None)
            log(f"写回记忆: {item['content'][:30]}… (重要性 {item['importance']})")
        except Exception as e:
            _record_extraction(item, f'{key_seed}#{idx}', None, 'failed', str(e)[:120])
            log(f'写回失败: {str(e)[:120]}')
    return written


def _record_extraction(item: dict, idem: str, mem_id, status: str, error) -> None:
    if not ARCHIVE_ENABLED:
        return
    try:
        with _archive_lock:
            con = sqlite3.connect(ARCHIVE_DB, timeout=10)
            con.execute(
                'INSERT OR IGNORE INTO extractions '
                '(content, importance, idempotency_key, lm_memory_id, status, error, created_at, written_at) '
                'VALUES (?,?,?,?,?,?,?,?)',
                (item['content'], item['importance'], idem, mem_id, status, error,
                 time.time(), time.time() if status == 'written' else None),
            )
            con.commit()
            con.close()
    except Exception as e:
        log(f'提炼记录写入失败: {e}')


def schedule_memory_job(user_text: str, reply_text: str) -> None:
    """后台线程：归档原文（总是执行）→ 提炼并写回（按开关与长度门槛）"""

    def worker():
        try:
            # 原文归档不受提炼开关影响，保证一句话都不丢
            if ARCHIVE_ENABLED:
                mid = archive_message('user', user_text)
                archive_message('assistant', reply_text)
            else:
                mid = None

            if not EXTRACT_ENABLED:
                return
            if len(user_text.strip()) < EXTRACT_MIN_CHARS:
                return

            items = extract_memories(user_text, reply_text)
            if not items:
                return
            seed = f'airi:msg{mid or int(time.time())}'
            write_memories(items, seed)
        except Exception as e:
            log(f'记忆任务异常: {e}')

    threading.Thread(target=worker, daemon=True).start()


# ---------------- HTTP ----------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'DafeiyuHub/0.1'

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

    def do_GET(self):
        if self.path.startswith('/health'):
            persona = read_persona()
            up = read_upstream()
            lm = probe_lm()
            self._json(200, {
                'status': 'ok',
                'persona_id': PERSONA_ID,
                'persona_chars': len(persona),
                'upstream_model': up['model'],
                'upstream_base': up['base'],
                'upstream_key_loaded': bool(up['key']),
                'lm_connected': lm['connected'],
                'lm_reason': lm.get('reason', ''),
                'port': PORT,
            })
            return
        if self.path.startswith('/v1/models'):
            self._json(200, {
                'object': 'list',
                'data': [{'id': FALLBACK_MODEL, 'object': 'model', 'owned_by': 'dafeiyu-hub'}],
            })
            return
        self._json(404, {'error': {'message': 'not found'}})

    def do_POST(self):
        if not self.path.startswith('/v1/chat/completions'):
            self._json(404, {'error': {'message': 'not found'}})
            return

        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length) if length else b'{}'
        try:
            payload = json.loads(raw.decode('utf-8'))
        except Exception as e:
            self._json(400, {'error': {'message': f'invalid json: {e}'}})
            return

        up = read_upstream()
        if not up['key']:
            self._json(500, {'error': {'message': '未找到上游 API Key（检查 AstrBot 配置）'}})
            return

        model = payload.get('model') or up['model']
        stream = bool(payload.get('stream'))
        user_text = last_user_text(payload.get('messages') or [])
        body = {'model': model, 'messages': build_messages(payload), 'stream': stream}
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
            resp = urllib.request.urlopen(req, timeout=180, context=ssl.create_default_context())
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
            reply_text = ''.join(chunks)
            if user_text and reply_text:
                schedule_memory_job(user_text, reply_text)
            return

        data = resp.read()
        resp.close()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

        reply_text = ''
        try:
            reply_text = json.loads(data.decode('utf-8', 'ignore'))['choices'][0]['message']['content']
        except Exception:
            pass
        if user_text and reply_text:
            schedule_memory_job(user_text, reply_text)


def main() -> None:
    init_archive()
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    persona = read_persona()
    up = read_upstream()
    log(f'===== 启动 端口 {PORT} 人格 {len(persona)} 字 上游 {up["model"]} key={bool(up["key"])} =====')
    print(f'[ready] http://127.0.0.1:{PORT}/v1', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log('收到中断信号')


if __name__ == '__main__':
    main()
