"""连通性自检 + 测试数据清理。

自检做五件事：
1. 配置是否完整（lm_key / persona_db / write_scope）
2. 记忆库是否在线
3. 能否写入一条带标记的测试记忆
4. 能否在 20 秒内检索回这条记忆（验证索引生效）
5. 知识库是否可检索（可选）

清理：删除所有带【自检】标记的记忆（含原子、图谱条目、FTS 重建）。
"""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.request
import uuid
from pathlib import Path

from config import CFG
from util import log

MARKER_PREFIX = '【自检】'
MARKER_TAG = 'SELFTEST-'


def _lm_db_path(cfg: dict) -> Path | None:
    raw = (cfg.get('archive') or {}).get('lm_db') or ''
    if raw:
        return Path(raw)
    # 默认推导：%USERPROFILE%/.astrbot/data/plugin_data/astrbot_plugin_livingmemory/livingmemory.db
    base = Path.home() / '.astrbot' / 'data' / 'plugin_data' / 'astrbot_plugin_livingmemory' / 'livingmemory.db'
    return base if base.exists() else None


def _post(cfg: dict, path: str, body: dict, timeout: int = 30):
    req = urllib.request.Request(
        cfg['lm_base'] + path,
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers={'Authorization': f"Bearer {cfg['lm_key']}", 'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'ignore'))


def run(cfg: dict, wait_seconds: int = 25) -> dict:
    steps = []
    marker = f'{MARKER_PREFIX}{MARKER_TAG}{uuid.uuid4().hex[:8]}'

    def add(name: str, ok: bool, detail: str = '', ms: int = 0):
        steps.append({'name': name, 'ok': ok, 'detail': detail, 'ms': ms})

    # 1. 配置
    miss = [k for k in ('lm_key', 'persona_db', 'write_scope') if not cfg.get(k)]
    add('配置完整', not miss, '缺少: ' + ', '.join(miss) if miss else 'ok')

    # 2. 记忆库在线
    t0 = time.time()
    try:
        req = urllib.request.Request(cfg['lm_base'] + '/health',
                                     headers={'Authorization': f"Bearer {cfg['lm_key']}"})
        with urllib.request.urlopen(req, timeout=5) as r:
            health = json.loads(r.read().decode('utf-8', 'ignore'))
        add('记忆库在线', True, str(health.get('status', 'ok')), int((time.time() - t0) * 1000))
    except Exception as e:
        add('记忆库在线', False, str(e)[:80], int((time.time() - t0) * 1000))
        return {'ok': False, 'marker': marker, 'steps': steps}

    # 3. 写入测试记忆
    t0 = time.time()
    content = f'{marker} 连通性测试，可删除'
    try:
        _post(cfg, '/memories', {
            'content': content,
            'importance': 0.1,
            'idempotency_key': f'selftest-{uuid.uuid4().hex[:8]}',
            'persona_id': cfg.get('persona_id') or 'dafeiyu',
            'scope': cfg.get('write_scope') or '',
            'topics': ['自检', '测试'],
            'key_facts': [content],
        })
        add('写入测试记忆', True, marker, int((time.time() - t0) * 1000))
    except Exception as e:
        add('写入测试记忆', False, str(e)[:80], int((time.time() - t0) * 1000))
        return {'ok': False, 'marker': marker, 'steps': steps}

    # 4. 等索引后检索
    t0 = time.time()
    hit = False
    waited = 0
    while waited < wait_seconds:
        try:
            res = _post(cfg, '/recall', {
                'query': marker,
                'k': 3,
                'persona_id': cfg.get('persona_id') or 'dafeiyu',
                'scope': cfg.get('write_scope') or '',
            })
            items = res.get('items') or res.get('results') or []
            if any(marker in str(it.get('content', '')) for it in items):
                hit = True
                break
        except Exception:
            pass
        time.sleep(5)
        waited += 5
    add('检索回测试记忆', hit,
        f'耗时 {waited}s' if hit else f'{wait_seconds}s 内未召回（索引可能未完成）',
        int((time.time() - t0) * 1000))

    # 5. 知识库（可选）
    kb = cfg.get('knowledge') or {}
    if kb.get('enabled'):
        from knowledge import retrieve as kb_retrieve

        t0 = time.time()
        try:
            docs = kb_retrieve(cfg, '干员')
            add('知识库检索', bool(docs), f'{len(docs)} 段', int((time.time() - t0) * 1000))
        except Exception as e:
            add('知识库检索', False, str(e)[:80], int((time.time() - t0) * 1000))

    ok = all(s['ok'] for s in steps)
    log(f"连通性自检: {'通过' if ok else '未通过'} | " + '; '.join(f"{s['name']}={'ok' if s['ok'] else 'fail'}" for s in steps))
    return {'ok': ok, 'marker': marker, 'steps': steps}


def cleanup(cfg: dict) -> dict:
    """删除带【自检】标记的记忆与相关图谱、原子。"""
    db = _lm_db_path(cfg)
    if db is None or not db.exists():
        return {'ok': False, 'error': '找不到记忆库文件', 'removed': 0}

    con = sqlite3.connect(db, timeout=20)
    con.row_factory = sqlite3.Row
    docs = con.execute(
        "select id from documents where text like ?", (f'%{MARKER_PREFIX}%',)
    ).fetchall()
    ids = [r['id'] for r in docs]
    if not ids:
        con.close()
        return {'ok': True, 'removed': 0, 'atoms': 0, 'nodes': 0}

    ph = ','.join('?' * len(ids))
    entries = [r['id'] for r in con.execute(
        f'select id from graph_entries where source_memory_id in ({ph})', ids)]
    node_ids = []
    if entries:
        eph = ','.join('?' * len(entries))
        node_ids = [r['node_id'] for r in con.execute(
            f'select distinct node_id from graph_entry_nodes where entry_id in ({eph})', entries)]
        con.execute(f'delete from graph_entry_nodes where entry_id in ({eph})', entries)
        con.execute(f'delete from graph_entries where id in ({eph})', entries)

    atoms = con.execute(f'delete from memory_atoms where parent_memory_id in ({ph})', ids).rowcount
    con.execute(f'delete from memory_sources where memory_id in ({ph})', ids)
    con.execute(f'delete from documents where id in ({ph})', ids)

    nodes = 0
    for nid in node_ids:
        left = con.execute('select count(*) from graph_entry_nodes where node_id=?', (nid,)).fetchone()[0]
        if left == 0:
            con.execute('delete from graph_nodes where id=?', (nid,))
            nodes += 1
    con.commit()
    con.close()

    for t in ('memory_atoms_fts', 'livingmemory_graph_entries_fts',
              'livingmemory_graph_nodes_fts', 'livingmemory_graph_nodes_bigram_fts',
              'livingmemory_memories_fts'):
        try:
            c2 = sqlite3.connect(db, timeout=20)
            c2.execute(f'insert into "{t}"("{t}") values(\'rebuild\')')
            c2.commit()
            c2.close()
        except Exception:
            pass

    log(f'清理测试数据: 文档 {len(ids)} 条、原子 {atoms} 条、节点 {nodes} 个')
    return {'ok': True, 'removed': len(ids), 'atoms': atoms, 'nodes': nodes}
