"""v1.1 隐私白名单 + 记忆浏览/删除。"""

import importlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

SIDECAR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SIDECAR))


def fresh_env(tmp: Path, names):
    os.environ['HUB_DATA_DIR'] = str(tmp)
    for n in names:
        sys.modules.pop(n, None)
    return {n: importlib.import_module(n) for n in names}


class ScopeFilterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_scope_'))
        self.sync = fresh_env(self.tmp, ('config', 'crypto', 'sync'))['sync']

    def cfg(self, mode, groups=None):
        return {'archive': {'scope_mode': mode, 'whitelist_groups': groups or []}}

    def test_friend_always_allowed(self):
        self.assertTrue(self.sync.session_allowed(self.cfg('private_only'), '一号:FriendMessage:123'))

    def test_group_requires_whitelist(self):
        cfg = self.cfg('private_or_whitelist', ['1126415196'])
        self.assertTrue(self.sync.session_allowed(cfg, '一号:GroupMessage:1126415196'))
        self.assertFalse(self.sync.session_allowed(cfg, '一号:GroupMessage:999'))

    def test_private_only_rejects_groups(self):
        self.assertFalse(self.sync.session_allowed(self.cfg('private_only', ['1126415196']), '一号:GroupMessage:1126415196'))

    def test_all_mode(self):
        self.assertTrue(self.sync.session_allowed(self.cfg('all'), '一号:GroupMessage:999'))

    def test_default_is_private_or_whitelist(self):
        self.assertFalse(self.sync.session_allowed({}, '一号:GroupMessage:999'))
        self.assertTrue(self.sync.session_allowed({}, '一号:FriendMessage:1'))


class MemoriesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_mem_'))
        self.mods = fresh_env(self.tmp, ('config', 'crypto', 'memories'))
        self.mem = self.mods['memories']
        self.db = self.tmp / 'livingmemory.db'
        con = sqlite3.connect(self.db)
        con.executescript(
            '''
            create table documents (
                id integer primary key autoincrement, doc_id text, text text,
                metadata text, created_at text, updated_at text
            );
            create virtual table documents_fts using fts5(
                search_text, content='', contentless_delete=1, tokenize='unicode61'
            );
            create table memory_atoms (
                id integer primary key autoincrement, parent_memory_id integer, content text
            );
            create table graph_entries (
                id integer primary key autoincrement, source_memory_id integer, content text
            );
            create table graph_entry_nodes (entry_id integer, node_id integer);
            create table memory_sources (memory_id integer, source_json text);
            create table external_api_idempotency (idempotency_key text, memory_id integer);
            '''
        )
        for i, text in enumerate(['他今天中午吃了牛肉面', '他下周三去辩论赛'], 1):
            con.execute(
                'insert into documents(doc_id,text,metadata,created_at,updated_at) values(?,?,?,?,?)',
                (f'doc-{i}', text, json.dumps({'importance': 0.6, 'session_id': '一号:FriendMessage:1'}),
                 '2026-11-27T10:00:00', '2026-11-27T10:00:00'),
            )
            con.execute('insert into documents_fts(rowid, search_text) values(?,?)', (i, text))
            con.execute('insert into memory_atoms(parent_memory_id, content) values(?,?)', (i, text))
            con.execute('insert into graph_entries(id, source_memory_id, content) values(?,?,?)', (i, i, text))
            con.execute('insert into graph_entry_nodes(entry_id, node_id) values(?,?)', (i, i))
            con.execute('insert into memory_sources(memory_id, source_json) values(?,?)', (i, '[]'))
            con.execute('insert into external_api_idempotency(idempotency_key, memory_id) values(?,?)', (f'k{i}', i))
        con.commit()
        con.close()
        self.cfg = {'lm_db': str(self.db)}

    def test_list_and_search(self):
        res = self.mem.list_memories(self.cfg)
        self.assertTrue(res['ok'])
        self.assertEqual(res['total'], 2)
        res2 = self.mem.list_memories(self.cfg, query='牛肉面')
        self.assertEqual(res2['total'], 1)
        self.assertIn('牛肉面', res2['items'][0]['text'])

    def test_delete_cleans_all_tables(self):
        res = self.mem.delete_memory(self.cfg, 1)
        self.assertTrue(res['ok'])
        con = sqlite3.connect(self.db)
        self.assertEqual(con.execute('select count(*) from documents').fetchone()[0], 1)
        self.assertEqual(con.execute('select count(*) from documents_fts').fetchone()[0], 1)
        self.assertEqual(con.execute('select count(*) from memory_atoms where parent_memory_id=1').fetchone()[0], 0)
        self.assertEqual(con.execute('select count(*) from graph_entries where source_memory_id=1').fetchone()[0], 0)
        self.assertEqual(con.execute('select count(*) from graph_entry_nodes where entry_id=1').fetchone()[0], 0)
        self.assertEqual(con.execute('select count(*) from memory_sources where memory_id=1').fetchone()[0], 0)
        self.assertEqual(con.execute('select count(*) from external_api_idempotency where memory_id=1').fetchone()[0], 0)
        con.close()
        self.assertTrue(Path(res['backup']).exists())

    def test_delete_missing(self):
        self.assertFalse(self.mem.delete_memory(self.cfg, 999)['ok'])


class MemoryFilterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_memf_'))
        self.mem = fresh_env(self.tmp, ('config', 'crypto', 'memories'))['memories']
        self.db = self.tmp / 'lm.db'
        con = sqlite3.connect(self.db)
        con.executescript(
            '''
            create table documents (id integer primary key autoincrement, doc_id text, text text,
                metadata text, created_at text, updated_at text);
            create virtual table documents_fts using fts5(search_text, content='', contentless_delete=1, tokenize='unicode61');
            create table memory_atoms (id integer primary key autoincrement, parent_memory_id integer, content text);
            create table graph_entries (id integer primary key autoincrement, source_memory_id integer, content text);
            '''
        )
        data = [
            ('私聊里说的牛肉面', 0.8, '一号:FriendMessage:1'),
            ('群里聊的辩论赛', 0.4, '一号:GroupMessage:9'),
            ('桌面宠物说的电影', 0.6, ''),
        ]
        for i, (text, imp, sid) in enumerate(data, 1):
            con.execute(
                'insert into documents(doc_id,text,metadata,created_at,updated_at) values(?,?,?,?,?)',
                (f'd{i}', text, json.dumps({'importance': imp, 'session_id': sid}),
                 '2026-11-27T10:00:00', '2026-11-27T10:00:00'),
            )
        con.commit()
        con.close()
        self.cfg = {'lm_db': str(self.db)}

    def test_kind_filter_and_facets(self):
        res = self.mem.list_memories(self.cfg, kind='private')
        self.assertEqual(res['total'], 1)
        self.assertEqual(res['facets'], {'all': 3, 'private': 1, 'group': 1, 'desktop': 1})

    def test_min_importance_and_sort(self):
        res = self.mem.list_memories(self.cfg, min_importance=0.6, sort='important')
        self.assertEqual(res['total'], 2)
        self.assertGreaterEqual(res['items'][0]['importance'], res['items'][1]['importance'])


class MemoryDateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_memd_'))
        self.mem = fresh_env(self.tmp, ('config', 'crypto', 'memories'))['memories']
        self.db = self.tmp / 'lm.db'
        con = sqlite3.connect(self.db)
        con.executescript(
            '''
            create table documents (id integer primary key autoincrement, doc_id text, text text,
                metadata text, created_at text, updated_at text);
            create virtual table documents_fts using fts5(search_text, content='', contentless_delete=1, tokenize='unicode61');
            create table memory_atoms (id integer primary key autoincrement, parent_memory_id integer, content text);
            create table graph_entries (id integer primary key autoincrement, source_memory_id integer, content text);
            '''
        )
        for i, (text, day) in enumerate([('今天的', '2026-11-27T10:00:00'),
                                          ('上周的', '2026-11-18T10:00:00'),
                                          ('上月的', '2026-10-30T10:00:00')], 1):
            con.execute('insert into documents(doc_id,text,metadata,created_at,updated_at) values(?,?,?,?,?)',
                        (f'd{i}', text, '{}', day, day))
        con.commit()
        con.close()
        self.cfg = {'lm_db': str(self.db)}

    def test_date_range(self):
        res = self.mem.list_memories(self.cfg, date_from='2026-11-27', date_to='2026-11-27')
        self.assertEqual(res['total'], 1)
        self.assertEqual(res['items'][0]['text'], '今天的')

    def test_open_range(self):
        res = self.mem.list_memories(self.cfg, date_from='2026-11-19')
        self.assertEqual(res['total'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
