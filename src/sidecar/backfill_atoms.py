"""P0-2：为缺原子的 hub 文档回填记忆原子。

用法：
  python backfill_atoms.py --dry-run   只统计
  python backfill_atoms.py             实际写入
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

PLUGIN_DIR = Path.home() / '.astrbot' / 'data' / 'plugins' / 'astrbot_plugin_livingmemory'
DB_PATH = Path.home() / '.astrbot' / 'data' / 'plugin_data' / 'astrbot_plugin_livingmemory' / 'livingmemory.db'

sys.path.insert(0, str(PLUGIN_DIR.parent))
# AstrBot 后端（插件 import astrbot.api）
BACKEND_APP = Path.home() / 'AppData' / 'Local' / 'AstrBot' / 'backend' / 'app'
if BACKEND_APP.exists():
    sys.path.insert(0, str(BACKEND_APP))

from astrbot_plugin_livingmemory.core.processors.atom_classifier import classify_atoms  # noqa: E402
from astrbot_plugin_livingmemory.storage.atom_store import AtomStore  # noqa: E402


def hub_docs_without_atoms() -> list[dict]:
    con = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        'select id, text, metadata, created_at from documents order by id'
    ).fetchall()
    atom_parents = {
        r[0]
        for r in con.execute('select distinct parent_memory_id from memory_atoms where parent_memory_id is not null')
    }
    con.close()

    out = []
    for r in rows:
        try:
            md = json.loads(r['metadata'] or '{}')
        except Exception:
            md = {}
        inner = md.get('metadata') if isinstance(md.get('metadata'), dict) else md
        is_hub = str(inner.get('source') or '') == 'external_api' or bool(inner.get('external_client'))
        if not is_hub:
            continue
        if r['id'] in atom_parents:
            continue
        out.append(
            {
                'id': r['id'],
                'text': r['text'] or '',
                'created_at': r['created_at'],
                'session_id': md.get('session_id') or inner.get('session_id') or '',
                'persona_id': md.get('persona_id') or inner.get('persona_id') or 'dafeiyu',
                'importance': float(md.get('importance') or inner.get('importance') or 0.5),
                'topics': inner.get('topics') or md.get('topics') or [],
            }
        )
    return out


async def backfill(dry_run: bool) -> None:
    docs = hub_docs_without_atoms()
    print(f'缺原子的 hub 文档: {len(docs)} 条')
    if not docs:
        return
    for d in docs[:10]:
        print(f"  #{d['id']} [{d['importance']}] {d['text'][:40]}")

    if dry_run:
        print('（dry-run，不写入）')
        return

    store = AtomStore(str(DB_PATH))
    await store.initialize()
    total = 0
    try:
        for d in docs:
            facts = [line.strip() for line in d['text'].split('\n') if line.strip()] or [d['text']]
            atoms = classify_atoms(
                key_facts=facts[:5],
                topics=d['topics'] or None,
                parent_importance=d['importance'],
                session_id=d['session_id'] or None,
                persona_id=d['persona_id'],
            )
            for a in atoms:
                a.parent_memory_id = d['id']
                if d['created_at']:
                    a.created_at = d['created_at']
            ids = await store.insert_many(atoms)
            total += len(ids)
            print(f"  #{d['id']} → 生成 {len(ids)} 条原子")
    finally:
        await store.close()
    print(f'完成：共写入 {total} 条原子')


if __name__ == '__main__':
    if '--dry-run' not in sys.argv and '--maintenance' not in sys.argv:
        print('直写插件数据库需显式加 --maintenance（建议先停止 AstrBot）')
        sys.exit(2)
    if '--maintenance' in sys.argv and '--dry-run' not in sys.argv:
        import shutil
        import time
        from pathlib import Path as _P

        dst = _P(str(DB_PATH) + '.bak_' + time.strftime('%Y%m%d%H%M%S'))
        shutil.copy2(DB_PATH, dst)
        print('已备份:', dst.name)
    asyncio.run(backfill('--dry-run' in sys.argv))
