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

SESSION_EXPR = (
    "coalesce(json_extract(metadata, '$.session_id'), "
    "json_extract(metadata, '$.adapter_id'), '')"
)
IMPORTANCE_EXPR = "coalesce(json_extract(metadata, '$.importance'), 0.0)"


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
    try:
        backups = sorted(db.parent.glob(db.name + '.bak_*'), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in backups[5:]:
            old.unlink(missing_ok=True)
    except Exception:
        pass
    return str(dest)


def _kind_of(session_id: str) -> str:
    sid = session_id or ''
    if 'FriendMessage' in sid:
        return 'private'
    if 'GroupMessage' in sid:
        return 'group'
    return 'desktop'


def list_memories(
    cfg: dict,
    query: str = '',
    limit: int = 40,
    offset: int = 0,
    include_selftest: bool = False,
    kind: str = 'all',
    min_importance: float = 0.0,
    sort: str = 'new',
    date_from: str = '',
    date_to: str = '',
) -> dict:
    db = _lm_path(cfg)
    if not db.exists():
        return {'ok': False, 'error': '记忆库不存在', 'items': [], 'total': 0, 'facets': {}}
    limit = max(1, min(int(limit or 40), 100))
    offset = max(0, int(offset or 0))
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        base: list[str] = []
        args: list = []
        if not include_selftest:
            base.append("text not like '%SELFTEST-%'")
        if query.strip():
            base.append('text like ?')
            args.append(f'%{query.strip()}%')
        base_sql = (' and '.join(base)) or '1=1'

        # 分类统计（在搜索条件下，不受分类筛选影响）
        facets = {'all': 0, 'private': 0, 'group': 0, 'desktop': 0}
        for row in con.execute(
            f'select {SESSION_EXPR} as sid, count(*) as n from documents where {base_sql} group by sid',
            args,
        ):
            facets[_kind_of(row['sid'])] += row['n']
        facets['all'] = sum(facets[k] for k in ('private', 'group', 'desktop'))

        clauses = [base_sql]
        cargs = list(args)
        if kind in ('private', 'group', 'desktop'):
            if kind == 'private':
                clauses.append(f"{SESSION_EXPR} like '%FriendMessage%'")
            elif kind == 'group':
                clauses.append(f"{SESSION_EXPR} like '%GroupMessage%'")
            else:
                clauses.append(
                    f"({SESSION_EXPR} not like '%FriendMessage%' and {SESSION_EXPR} not like '%GroupMessage%')"
                )
        if min_importance and float(min_importance) > 0:
            clauses.append(f'{IMPORTANCE_EXPR} >= ?')
            cargs.append(float(min_importance))
        # created_at 可能是 '2026-11-27T10:00:00' 或 '2026-11-27 10:00:00'
        if date_from:
            clauses.append("replace(created_at, 'T', ' ') >= ?")
            cargs.append(f'{date_from} 00:00:00')
        if date_to:
            clauses.append("replace(created_at, 'T', ' ') <= ?")
            cargs.append(f'{date_to} 23:59:59')

        where = ' and '.join(clauses)
        total = con.execute(f'select count(*) from documents where {where}', cargs).fetchone()[0]
        order = f'{IMPORTANCE_EXPR} desc, id desc' if sort == 'important' else 'id desc'
        rows = con.execute(
            f'select id, doc_id, text, metadata, created_at from documents where {where} '
            f'order by {order} limit ? offset ?',
            (*cargs, limit, offset),
        ).fetchall()

        items = []
        for r in rows:
            try:
                meta = json.loads(r['metadata'] or '{}')
            except Exception:
                meta = {}
            text = (r['text'] or '').strip()
            sid = meta.get('session_id') or meta.get('adapter_id') or ''
            items.append({
                'id': r['id'],
                'doc_id': r['doc_id'],
                'text': text,
                'summary': text[:90].replace('\n', ' '),
                'importance': meta.get('importance'),
                'session_id': sid,
                'kind': _kind_of(sid),
                'created_at': r['created_at'],
                'key_facts': meta.get('key_facts') or [],
            })
        return {
            'ok': True,
            'items': items,
            'total': total,
            'facets': facets,
            'limit': limit,
            'offset': offset,
        }
    finally:
        con.close()


def cleanup_selftest(cfg: dict) -> int:
    """删除自检留下的测试记忆（一次备份，级别低于普通删除）。"""
    db = _lm_path(cfg)
    if not db.exists():
        return 0
    con = sqlite3.connect(db, timeout=20)
    try:
        ids = [r[0] for r in con.execute(
            "select id from documents where text like '%SELFTEST-%'"
        ).fetchall()]
        if not ids:
            return 0
        _backup(db)
        con.execute('begin')
        for doc_id in ids:
            con.execute('delete from documents_fts where rowid=?', (doc_id,))
            try:
                con.execute('delete from livingmemory_memories_fts where rowid=?', (doc_id,))
            except Exception:
                pass
            entry_ids = [r[0] for r in con.execute(
                'select id from graph_entries where source_memory_id=?', (doc_id,)
            ).fetchall()]
            for eid in entry_ids:
                con.execute('delete from graph_entry_nodes where entry_id=?', (eid,))
            con.execute('delete from graph_entries where source_memory_id=?', (doc_id,))
            con.execute('delete from memory_atoms where parent_memory_id=?', (doc_id,))
            con.execute('delete from memory_sources where memory_id=?', (doc_id,))
            con.execute('delete from external_api_idempotency where memory_id=?', (doc_id,))
            con.execute('delete from documents where id=?', (doc_id,))
        con.commit()
        log(f'已清理自检记忆: {len(ids)} 条')
        return len(ids)
    except Exception as e:
        con.rollback()
        log(f'清理自检记忆失败: {str(e)[:80]}')
        return 0
    finally:
        con.close()


def cleanup_index(cfg: dict) -> dict:
    """清理没有对应文档的 FTS 幽灵条目（只读扫、可写清）。"""
    db = _lm_path(cfg)
    if not db.exists():
        return {}
    out = {}
    con = sqlite3.connect(db, timeout=20)
    try:
        con.execute('begin')
        for table in ('documents_fts', 'livingmemory_memories_fts'):
            try:
                n = con.execute(
                    f'delete from {table} where rowid not in (select id from documents)'
                ).rowcount
                out[table] = n
            except Exception:
                out[table] = -1
        # 历史删除留下的关系/来源/幂等孤儿
        try:
            entry_ids = [r[0] for r in con.execute(
                'select id from graph_entries where source_memory_id not in (select id from documents)'
            ).fetchall()]
            for eid in entry_ids:
                con.execute('delete from graph_entry_nodes where entry_id=?', (eid,))
            out['graph_entries'] = len(entry_ids)
            con.execute('delete from graph_entries where source_memory_id not in (select id from documents)')
            out['graph_entry_nodes'] = con.execute(
                'delete from graph_entry_nodes where entry_id not in (select id from graph_entries)'
            ).rowcount
        except Exception as e:
            out['graph'] = str(e)[:60]
        for table in ('memory_sources', 'external_api_idempotency'):
            try:
                out[table] = con.execute(
                    f'delete from {table} where memory_id not in (select id from documents)'
                ).rowcount
            except Exception:
                out[table] = -1
        con.commit()
    except Exception as e:
        con.rollback()
        out['error'] = str(e)[:80]
    finally:
        con.close()
    return out


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
            con.execute('delete from documents_fts where rowid=?', (row['id'],))
            # 插件自己的记忆索引（存在就一起清，避免幽灵条目）
            try:
                con.execute('delete from livingmemory_memories_fts where rowid=?', (row['id'],))
            except Exception:
                pass
            con.execute('delete from memory_atoms where parent_memory_id=?', (row['id'],))
            entry_ids = [r[0] for r in con.execute(
                'select id from graph_entries where source_memory_id=?', (row['id'],)
            ).fetchall()]
            for eid in entry_ids:
                con.execute('delete from graph_entry_nodes where entry_id=?', (eid,))
            con.execute('delete from graph_entries where source_memory_id=?', (row['id'],))
            for sql in (
                'delete from memory_sources where memory_id=?',
                'delete from external_api_idempotency where memory_id=?',
            ):
                try:
                    con.execute(sql, (row['id'],))
                except Exception:
                    pass
            con.execute('delete from documents where id=?', (row['id'],))
            con.commit()
        except Exception:
            con.rollback()
            raise
        log(f'已删除记忆 #{row["id"]}（{len(row["text"] or "")}字，备份 {Path(backup).name}）')
        return {'ok': True, 'id': row['id'], 'backup': backup, 'preview': text}
    finally:
        con.close()
