"""人格读取：从 AstrBot 的 data_v4.db 取 system_prompt，带缓存。"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from util import log

_cache = {'text': '', 'ts': 0.0}


def read_persona(cfg: dict) -> str:
    ttl = int(cfg.get('persona_cache_seconds', 30) or 30)
    now = time.time()
    if _cache['text'] and now - _cache['ts'] < ttl:
        return _cache['text']

    text = ''
    db_path = Path(cfg.get('persona_db') or '')
    persona_id = cfg.get('persona_id') or 'dafeiyu'

    if db_path and db_path.exists():
        try:
            con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=5)
            row = con.execute(
                'select system_prompt from personas where persona_id = ?', (persona_id,)
            ).fetchone()
            con.close()
            if row:
                text = row[0] or ''
        except Exception as e:
            log(f'读取人格失败: {e}')

    if not text:
        fallback = Path(__file__).parent / 'persona_fallback.txt'
        if fallback.exists():
            text = fallback.read_text(encoding='utf-8')
            log('使用人格兜底文件')

    _cache['text'] = text
    _cache['ts'] = now
    return text


def clear_cache() -> None:
    _cache['text'] = ''
    _cache['ts'] = 0.0
