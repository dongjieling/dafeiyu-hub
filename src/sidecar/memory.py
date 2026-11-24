"""记忆：召回、提炼、写回、归档。"""

from __future__ import annotations

import json
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from config import data_path
from upstream import read_upstream
from util import log

_lock = threading.Lock()
_probe = {'ok': False, 'reason': '', 'ts': 0.0}
PROBE_TTL = 15

EXTRACT_PROMPT = """从下面这轮对话里提取值得长期记住的信息（0-{max_n} 条）。

值得记：他的偏好、经历、关系、计划、情绪模式、重要事件
不值得：寒暄、语气词、重复内容、临时状态、AI 自己的话

每条要求：
- 不超过 40 字
- 用第三人称写（"他说……""他喜欢……"）
- 附重要性分数 0-1，越稳定越重要

只输出 JSON 数组，没有就输出 []
格式：[{{"content": "...", "importance": 0.7}}]"""


# ---------------- 健康探测 ----------------
def probe_lm(cfg: dict, force: bool = False) -> dict:
    now = time.time()
    if not force and now - _probe['ts'] < PROBE_TTL:
        return {'connected': _probe['ok'], 'reason': _probe['reason']}

    key = cfg.get('lm_key') or ''
    if not key:
        _probe.update({'ok': False, 'reason': '未配置 API Key', 'ts': now})
        return {'connected': False, 'reason': '未配置 API Key'}

    try:
        req = urllib.request.Request(
            f"{cfg['lm_base']}/health", headers={'Authorization': f'Bearer {key}'}
        )
        with urllib.request.urlopen(req, timeout=2.5) as r:
            ok = r.status == 200
        _probe.update({'ok': ok, 'reason': '', 'ts': now})
        return {'connected': ok, 'reason': ''}
    except Exception as e:
        reason = str(e)[:80]
        _probe.update({'ok': False, 'reason': reason, 'ts': now})
        return {'connected': False, 'reason': reason}


# ---------------- 召回 ----------------
def recall(cfg: dict, query: str) -> list[str]:
    key = cfg.get('lm_key') or ''
    if not key or not (query or '').strip():
        return []

    body = {
        'query': query[:500],
        'k': int(cfg.get('recall_k') or 8),
        'persona_id': cfg.get('persona_id') or 'dafeiyu',
    }
    req = urllib.request.Request(
        f"{cfg['lm_base']}/recall",
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=float(cfg.get('lm_timeout') or 6)) as r:
            data = json.loads(r.read().decode('utf-8', 'ignore'))
    except Exception as e:
        log(f'召回失败: {str(e)[:100]}')
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
    out = out[:12]
    log(f'召回查询: [{query[:50]}] -> {len(out)} 条')
    if out:
        log('召回详情: ' + ' | '.join(x[:24].replace('\n', ' ') for x in out))
    return out


# ---------------- 提炼 ----------------
def _strip_fence(text: str) -> str:
    t = (text or '').strip()
    if t.startswith('```'):
        t = t.split('\n', 1)[-1]
        if t.endswith('```'):
            t = t[:-3]
    return t.strip()


def extract(cfg: dict, user_text: str, reply_text: str) -> list[dict]:
    ex = cfg.get('extract') or {}
    up = read_upstream(cfg)
    if not up['key']:
        return []

    max_n = int(ex.get('max_per_turn') or 2)
    min_imp = float(ex.get('min_importance') or 0.3)
    body = {
        'model': ex.get('model') or up['model'],
        'messages': [
            {'role': 'system', 'content': EXTRACT_PROMPT.format(max_n=max_n)},
            {'role': 'user', 'content': f'他说：{user_text[:600]}\n她说：{reply_text[:600]}'},
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
        with urllib.request.urlopen(
            req, timeout=float(ex.get('timeout') or 45), context=ssl.create_default_context()
        ) as r:
            data = json.loads(r.read().decode('utf-8', 'ignore'))
        items = json.loads(_strip_fence(data['choices'][0]['message']['content']))
        if not isinstance(items, list):
            return []
    except Exception as e:
        log(f'提炼失败: {str(e)[:100]}')
        return []

    out = []
    for it in items[:max_n]:
        if not isinstance(it, dict):
            continue
        content = str(it.get('content', '')).strip()
        try:
            importance = float(it.get('importance', 0.5) or 0.5)
        except (TypeError, ValueError):
            importance = 0.5
        if len(content) < 4 or importance < min_imp:
            continue
        if content.startswith(('她', '我', '你')):
            log(f'丢弃非事实条目: {content[:30]}')
            continue
        out.append({'content': content[:120], 'importance': round(min(max(importance, 0.0), 1.0), 2)})
    return out


# ---------------- 写回 ----------------
def write_back(cfg: dict, items: list[dict], seed: str) -> int:
    key = cfg.get('lm_key') or ''
    if not items or not key:
        return 0
    written = 0
    for idx, item in enumerate(items):
        body = {
            'content': item['content'],
            'importance': item['importance'],
            'idempotency_key': f'{seed}#{idx}',
            'persona_id': cfg.get('persona_id') or 'dafeiyu',
            'scope': cfg.get('write_scope') or cfg.get('scope') or '',
            'topics': [item['content'][:12]],
            'key_facts': [item['content']],
        }
        req = urllib.request.Request(
            f"{cfg['lm_base']}/memories",
            data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                res = json.loads(r.read().decode('utf-8', 'ignore'))
            mem_id = (res.get('memory') or {}).get('id') if isinstance(res, dict) else None
            written += 1
            record_extraction(cfg, item, f'{seed}#{idx}', mem_id, 'written', None)
            log(f"写回记忆: {item['content'][:30]}… (重要性 {item['importance']})")
        except Exception as e:
            record_extraction(cfg, item, f'{seed}#{idx}', None, 'failed', str(e)[:120])
            log(f'写回失败: {str(e)[:120]}')
    return written


# ---------------- 归档 ----------------
def init_archive(cfg: dict) -> None:
    arc = cfg.get('archive') or {}
    if not arc.get('enabled', True):
        return
    db = data_path(arc.get('db_path') or 'archive.db')
    try:
        con = sqlite3.connect(db, timeout=10)
        con.executescript(
            '''
            CREATE TABLE IF NOT EXISTS sessions (
              id INTEGER PRIMARY KEY, source TEXT NOT NULL, session_key TEXT NOT NULL,
              persona_id TEXT, created_at REAL NOT NULL, last_active REAL,
              UNIQUE(source, session_key));
            CREATE TABLE IF NOT EXISTS messages (
              id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL, role TEXT NOT NULL,
              content TEXT NOT NULL, created_at REAL NOT NULL, meta TEXT);
            CREATE TABLE IF NOT EXISTS extractions (
              id INTEGER PRIMARY KEY, message_id INTEGER, content TEXT NOT NULL,
              importance REAL DEFAULT 0.5, idempotency_key TEXT UNIQUE, lm_memory_id TEXT,
              status TEXT DEFAULT 'pending', error TEXT, created_at REAL NOT NULL, written_at REAL);
            CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
            '''
        )
        con.commit()
        con.close()
        log(f'归档库就绪: {db.name}')
    except Exception as e:
        log(f'归档库初始化失败: {e}')


def _archive_db(cfg: dict) -> Path | None:
    arc = cfg.get('archive') or {}
    if not arc.get('enabled', True):
        return None
    return data_path(arc.get('db_path') or 'archive.db')


def archive_message(cfg: dict, role: str, content: str) -> int | None:
    db = _archive_db(cfg)
    if db is None or not content:
        return None
    try:
        with _lock:
            con = sqlite3.connect(db, timeout=10)
            now = time.time()
            con.execute(
                'INSERT OR IGNORE INTO sessions (source, session_key, persona_id, created_at, last_active) '
                "VALUES ('airi','desktop',?,?,?)",
                (cfg.get('persona_id') or 'dafeiyu', now, now),
            )
            sid = con.execute(
                "SELECT id FROM sessions WHERE source='airi' AND session_key='desktop'"
            ).fetchone()[0]
            con.execute('UPDATE sessions SET last_active=? WHERE id=?', (now, sid))
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


def record_extraction(cfg, item, idem, mem_id, status, error) -> None:
    db = _archive_db(cfg)
    if db is None:
        return
    try:
        with _lock:
            con = sqlite3.connect(db, timeout=10)
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


# ---------------- 后台任务 ----------------
def schedule_memory_job(cfg: dict, user_text: str, reply_text: str) -> None:
    ex = cfg.get('extract') or {}
    enabled = bool(ex.get('enabled', True))
    min_chars = int(ex.get('min_chars') or 6)

    def worker():
        try:
            mid = archive_message(cfg, 'user', user_text)
            archive_message(cfg, 'assistant', reply_text)

            if not enabled or len((user_text or '').strip()) < min_chars:
                return
            items = extract(cfg, user_text, reply_text)
            if not items:
                return
            write_back(cfg, items, f'airi:msg{mid or int(time.time())}')
        except Exception as e:
            log(f'记忆任务异常: {e}')

    threading.Thread(target=worker, daemon=True).start()
