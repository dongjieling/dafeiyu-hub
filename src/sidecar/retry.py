"""P0-1：重试改造成 健康门控 + 指数退避 + 死信。

规则：
- LivingMemory 未就绪时不消耗重试次数（直接跳过本轮）
- 退避序列：1min → 5min → 15min → 1h
- 第 5 次失败后标记 dead（死信），不再自动重试
- 状态暴露到 /health，界面上可见
"""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.request
from pathlib import Path

import crypto
from config import data_path
from util import log

BACKOFF_SECONDS = [60, 300, 900, 3600]
MAX_ATTEMPTS = 5


def _archive_db(cfg: dict) -> Path | None:
    arc = cfg.get('archive') or {}
    if not arc.get('enabled', True):
        return None
    return data_path(arc.get('db_path') or 'archive.db')


def ensure_columns(db: Path) -> None:
    try:
        con = sqlite3.connect(db, timeout=10)
        cols = {r[1] for r in con.execute('PRAGMA table_info(extractions)')}
        if 'retry_count' not in cols:
            con.execute('ALTER TABLE extractions ADD COLUMN retry_count INTEGER DEFAULT 0')
        if 'next_retry_at' not in cols:
            con.execute('ALTER TABLE extractions ADD COLUMN next_retry_at REAL')
        if 'last_error_at' not in cols:
            con.execute('ALTER TABLE extractions ADD COLUMN last_error_at REAL')
        con.commit()
        con.close()
    except Exception as e:
        log(f'重试列检查失败: {e}')


def stats(cfg: dict) -> dict:
    db = _archive_db(cfg)
    if db is None or not db.exists():
        return {'pending_retry': 0, 'dead_letters': 0, 'written': 0}
    try:
        con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=5)
        pending = con.execute(
            "select count(*) from extractions where status='failed' and coalesce(retry_count,0) < ?",
            (MAX_ATTEMPTS,),
        ).fetchone()[0]
        dead = con.execute("select count(*) from extractions where status='dead'").fetchone()[0]
        written = con.execute("select count(*) from extractions where status='written'").fetchone()[0]
        con.close()
        return {'pending_retry': pending, 'dead_letters': dead, 'written': written}
    except Exception:
        return {'pending_retry': 0, 'dead_letters': 0, 'written': 0}


def retry_failed(cfg: dict, lm_ready: bool) -> int:
    """LM 未就绪时直接返回，不消耗重试次数。"""
    if not lm_ready:
        return 0

    db = _archive_db(cfg)
    key = cfg.get('lm_key') or ''
    if db is None or not key:
        return 0

    ensure_columns(db)
    now = time.time()
    try:
        con = sqlite3.connect(db, timeout=10)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "select id, content, importance, idempotency_key, coalesce(retry_count,0) as rc, next_retry_at "
            "from extractions "
            "where status='failed' and coalesce(retry_count,0) < ? "
            "and (next_retry_at is null or next_retry_at <= ?) "
            "order by id limit 10",
            (MAX_ATTEMPTS, now),
        ).fetchall()
        con.close()
    except Exception as e:
        log(f'查询待重试失败: {e}')
        return 0

    if not rows:
        return 0

    ok = 0
    for r in rows:
        try:
            plain = crypto.decrypt(r['content'])
        except Exception as e:
            log(f'解密失败，跳过错信: {e}')
            continue

        body = {
            'content': plain,
            'importance': r['importance'],
            'idempotency_key': r['idempotency_key'],
            'persona_id': cfg.get('persona_id') or 'dafeiyu',
            'scope': cfg.get('write_scope') or cfg.get('scope') or '',
            'key_facts': [r['content']],
        }
        req = urllib.request.Request(
            f"{cfg['lm_base']}/memories",
            data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                res = json.loads(resp.read().decode('utf-8', 'ignore'))
            mem_id = (res.get('memory') or {}).get('id') if isinstance(res, dict) else None
            con = sqlite3.connect(db, timeout=10)
            con.execute(
                "update extractions set status='written', lm_memory_id=?, written_at=?, next_retry_at=null where id=?",
                (mem_id, time.time(), r['id']),
            )
            con.commit()
            con.close()
            ok += 1
            log(f"重试成功: {plain[:24]}…")
        except Exception as e:
            attempts = int(r['rc']) + 1
            delay = BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)]
            status = 'dead' if attempts >= MAX_ATTEMPTS else 'failed'
            con = sqlite3.connect(db, timeout=10)
            con.execute(
                'update extractions set retry_count=?, next_retry_at=?, last_error_at=?, status=?, error=? where id=?',
                (attempts, time.time() + delay, time.time(), status, str(e)[:120], r['id']),
            )
            con.commit()
            con.close()
            if status == 'dead':
                log(f"死信（第 {attempts} 次失败，不再重试）: {plain[:24]}…")
            else:
                log(f"重试失败（第 {attempts} 次，{delay}s 后再试）: {str(e)[:80]}")
    return ok
