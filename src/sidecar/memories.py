"""记忆浏览与删除（直接读写 LivingMemory 的库）。

LivingMemory 的外部 API 不提供删除，所以这里在本地维护模式下：
- 删除前用 SQLite backup API 备份
- 同步清理 documents / documents_fts / memory_atoms / graph_entries
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from util import log


def _lm_path(cfg: dict) -> Path:
    raw = (cfg.get('lm_db') or '').strip()
    if raw:
        return Path(raw)
    return Path.home() / '.astrbot' / 'data' / 'plugin_data' / 'astrbot_plugin_livingmemory' / 'livingmemory.db'


def _backup(db: Path) -> str:
    dest = db.with_name(f'{db.name}.bak_{time.strftime("%Y%m%d%H%M%S")}')
    src = sqlite3.connect(db, timeout=20)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return str(dest)


def list_memories(cfg: dict, query: str = '', limit: int = 30, offset: int = 0,
                  include_selftest: bool = False) -> dict:
    db = _lm_path(cfg)
    if not db.exists():
        return {'ok': False, 'error': '记忆库不存在', 'items': [], 'total': 0}
    limit = max(1, min(int(limit or 30), 100))
    offset = max(0, int(offset or 0))
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        clauses = []
        args: list = []
        if not include_selftest:
            clauses.append("text not like '%SELFTEST-%'")
        if query.strip():
            clauses.append('text like ?')
            args.append(f'%{query.strip()}%')
        where = ('where ' + ' and '.join(clauses)) if clauses else ''
        total = con.execute(f'select count(*) from documents {where}', args).fetchone()[0]
        rows = con.execute(
            f'select id, doc_id, text, metadata, created_at from documents {where} '
            'order by id desc limit ? offset ?',
            (*args, limit, offset),
        ).fetchall()
        items = []
        for r in rows:
            try:
                meta = json.loads(r['metadata'] or '{}')
            except Exception:
                meta = {}
            text = (r['text'] or '').strip()
            items.append({
                'id': r['id'],
                'doc_id': r['doc_id'],
                'text': text[:300],
                'importance': meta.get('importance'),
                'session_id': meta.get('session_id') or meta.get('adapter_id') or '',
                'created_at': r['created_at'],
            })
        return {'ok': True, 'items': items, 'total': total, 'limit': limit, 'offset': offset}
    finally:
        con.close()


def delete_memory(cfg: dict, doc_id: int) -> dict:
    db = _lm_path(cfg)
    if not db.exists():
        return {'ok': False, 'error': '记忆库不存在'}
    con = sqlite3.connect(db, timeout=20)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute('select id, doc_id, text from documents where id=?', (doc_id,)).fetchone()
        if not row:
            return {'ok': False, 'error': '这条记忆不存在'}

        backup = _backup(db)
        text = (row['text'] or '')[:40]
        con.execute('begin')
        try:
            # contentless FTS：contentless_delete=1 支持按 rowid 删除
            con.execute('delete from documents_fts where rowid=?', (row['id'],))
            con.execute('delete from memory_atoms where parent_memory_id=?', (row['id'],))
            con.execute('delete from graph_entries where source_memory_id=?', (row['id'],))
            con.execute('delete from documents where id=?', (row['id'],))
            con.commit()
        except Exception:
            con.rollback()
            raise
        log(f'已删除记忆 #{row["id"]}（{len(row["text"] or "")}字，备份 {Path(backup).name}）')
        return {'ok': True, 'id': row['id'], 'backup': backup, 'preview': text}
    finally:
        con.close()
