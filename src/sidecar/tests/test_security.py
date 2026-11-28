"""管理 token、scope 与隔离。"""

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


class AdminTokenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_admin_'))
        self.admin = fresh_env(self.tmp, ('config', 'crypto', 'util', 'admin'))['admin']

    def test_token_roundtrip(self):
        token = self.admin.ensure()
        self.assertTrue(token)
        self.assertTrue(self.admin.check(token))
        self.assertFalse(self.admin.check('wrong'))


class ScopeLocalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_scope_'))
        self.scope = fresh_env(self.tmp, ('config', 'crypto', 'util', 'contracts', 'scope'))['scope']

    def test_desktop_scope_allowed(self):
        res = self.scope.validate({'write_scope': 'airi:desktop'})
        self.assertTrue(res.ok, res.detail)

    def test_qq_scope_still_ok(self):
        res = self.scope.validate({'write_scope': '一号:FriendMessage:123'})
        self.assertTrue(res.ok)


class ListScopeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_list_'))
        mods = fresh_env(self.tmp, ('config', 'crypto', 'util', 'memories'))
        self.mem = mods['memories']
        self.db = self.tmp / 'lm.db'
        con = sqlite3.connect(self.db)
        con.executescript(
            'create table documents (id integer primary key autoincrement, doc_id text, text text, '
            'metadata text, created_at text, updated_at text);'
        )
        rows = [
            ('airi 的记忆', {'session_id': 'airi:desktop'}),
            ('qq 的记忆', {'session_id': '一号:FriendMessage:1'}),
        ]
        for text, meta in rows:
            con.execute('insert into documents(doc_id,text,metadata,created_at,updated_at) values(?,?,?,?,?)',
                        (text, text, json.dumps(meta), '2026-11-28 10:00:00', '2026-11-28 10:00:00'))
        con.commit()
        con.close()
        self.cfg = {'lm_db': str(self.db)}

    def test_scope_filters(self):
        res = self.mem.list_memories(self.cfg, '', 20, scope='airi:desktop')
        texts = [i['text'] for i in res['items']]
        self.assertEqual(texts, ['airi 的记忆'])


class OutboxScopeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_outbox_'))
        mods = fresh_env(self.tmp, ('config', 'crypto', 'util', 'outbox', 'adapters', 'quality'))
        self.outbox = mods['outbox']
        self.cfg = {'lm_db': str(self.tmp / 'x.db'), 'write_scope': 'global:scope'}

    def test_row_scope_wins(self):
        captured = {}

        class Adapter:
            def write(self, body):
                captured.update(body)
                return {'memory': {'id': 1}}

        row = {'content': 'x', 'importance': 0.5, 'idempotency_key': 'k', 'topics': '[]',
               'scope': 'airi:desktop', 'persona_id': 'p2'}
        self.outbox._send_one(self.cfg, row, Adapter())
        self.assertEqual(captured['scope'], 'airi:desktop')
        self.assertEqual(captured['persona_id'], 'p2')


if __name__ == '__main__':
    unittest.main(verbosity=2)
