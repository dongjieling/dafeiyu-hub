"""把归档库里已有的明文加密。

用法：
  python encrypt_archive.py --dry-run
  python encrypt_archive.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

os.environ.setdefault('HUB_DATA_DIR', str(Path(os.environ.get('APPDATA', '')) / 'dafeiyu-hub'))
sys.path.insert(0, str(Path(__file__).parent))

import crypto  # noqa: E402


def db_path() -> Path:
    return Path(os.environ['HUB_DATA_DIR']) / 'archive.db'


def migrate(dry_run: bool) -> None:
    db = db_path()
    if not db.exists():
        print('归档库不存在:', db)
        return

    con = sqlite3.connect(db, timeout=30)
    con.row_factory = sqlite3.Row

    msgs = con.execute('select id, content, meta from messages').fetchall()
    todo_msgs = [r for r in msgs if not crypto.is_encrypted(r['content'] or '')]
    print(f'messages: 总 {len(msgs)}，待加密 {len(todo_msgs)}')

    exts = con.execute('select id, content from extractions').fetchall()
    todo_exts = [r for r in exts if not crypto.is_encrypted(r['content'] or '')]
    print(f'extractions: 总 {len(exts)}，待加密 {len(todo_exts)}')

    if dry_run:
        print('（dry-run，不写入）')
        con.close()
        return

    # messages
    for r in todo_msgs:
        content = crypto.encrypt(r['content'] or '')
        meta = r['meta']
        if meta and not crypto.is_encrypted(meta):
            meta = crypto.encrypt(meta)
        con.execute('update messages set content=?, meta=? where id=?', (content, meta, r['id']))
    con.commit()
    print(f'已加密 messages {len(todo_msgs)} 条')

    # extractions
    for r in todo_exts:
        con.execute('update extractions set content=? where id=?', (crypto.encrypt(r['content'] or ''), r['id']))
    con.commit()
    print(f'已加密 extractions {len(todo_exts)} 条')

    # WAL 落盘并收缩，减少残留
    try:
        con.execute('pragma wal_checkpoint(TRUNCATE)')
        con.execute('vacuum')
    except Exception as e:
        print('收缩跳过:', e)

    sample = con.execute("select content from messages where content like 'enc:v1:%' limit 1").fetchone()
    print('抽样密文:', (sample[0][:50] + '...') if sample else '无')
    con.close()


if __name__ == '__main__':
    migrate('--dry-run' in sys.argv)
