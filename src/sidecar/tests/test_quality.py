"""五期验收：去重、作用域、重要度。"""

import importlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SIDECAR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SIDECAR))


def fresh_env(tmp: Path):
    os.environ['HUB_DATA_DIR'] = str(tmp)
    for name in ('config', 'crypto', 'contracts', 'adapters', 'migrations', 'scope',
                 'quality', 'outbox', 'memory', 'telemetry'):
        sys.modules.pop(name, None)
    return (
        importlib.import_module('config'),
        importlib.import_module('quality'),
        importlib.import_module('migrations'),
        importlib.import_module('memory'),
    )


class QualityUnitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_q_'))
        self.config, self.quality, self.migrations, self.memory = fresh_env(self.tmp)

    def test_importance_weighted_and_capped(self):
        q = self.quality
        self.assertAlmostEqual(q.final_importance(1.0, 1.0), q.CAP)
        self.assertAlmostEqual(q.final_importance(0.0, 0.0), q.FLOOR)
        # 0.6*0.8 + 0.4*0.2 = 0.56
        self.assertAlmostEqual(q.final_importance(0.8, 0.2), 0.56)
        # 具体事件：importance 高、stability 低
        self.assertLess(q.final_importance(0.8, 0.1), q.final_importance(0.6, 0.9))

    def test_duplicate_detection(self):
        q = self.quality
        existing = ['他今天中午吃了牛肉面，加了两个蛋']
        self.assertTrue(q.is_duplicate({}, '他今天中午吃了牛肉面，加了两个蛋', existing))
        self.assertTrue(q.is_duplicate({}, '他今天中午吃了牛肉面加两个蛋', existing))
        self.assertFalse(q.is_duplicate({}, '他明天要去天津出差三天', existing))

    def test_scope_filter(self):
        items = [
            {'content': '本域', 'session_id': '一号:FriendMessage:1'},
            {'content': '别的群', 'session_id': '一号:GroupMessage:999'},
            {'content': '没标域'},
        ]
        kept = self.quality.filter_by_scope(items, '一号:FriendMessage:1')
        self.assertEqual([x['content'] for x in kept], ['本域', '没标域'])


class _DupLM(BaseHTTPRequestHandler):
    memories = [{'content': '他今天中午吃了牛肉面，加了两个蛋'}]
    written = []

    def do_GET(self):  # noqa: N802
        self._json({'status': 'ok'})

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get('Content-Length') or 0)
        body = json.loads((self.rfile.read(n) or b'{}').decode('utf-8'))
        if self.path.endswith('/recall'):
            self._json({'items': [dict(m) for m in type(self).memories]})
        elif self.path.endswith('/memories'):
            type(self).written.append(body)
            self._json({'memory': {'id': 'x'}})
        else:
            self._json({})

    def _json(self, obj):
        out = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


class DedupeIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_dup_'))
        self.config, self.quality, self.migrations, self.memory = fresh_env(self.tmp)
        self.db = self.tmp / 'archive.db'
        self.assertTrue(self.migrations.ensure_archive(self.db).ok)

        _DupLM.written = []
        self.server = HTTPServer(('127.0.0.1', 0), _DupLM)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        port = self.server.server_address[1]

        self.cfg = dict(self.config.CFG)
        self.cfg.update(
            lm_base=f'http://127.0.0.1:{port}',
            lm_key='k',
            write_scope='一号:FriendMessage:123456789',
            write_scope_check='warn',
            archive=dict(self.cfg.get('archive') or {}, enabled=True, db_path='archive.db'),
            outbox={'enabled': True},
        )

    def tearDown(self):
        self.server.shutdown()

    def test_duplicate_skipped(self):
        items = [{'content': '他今天中午吃了牛肉面，加了两个蛋', 'importance': 0.5}]
        queued = self.memory.write_back(self.cfg, items, 'dup1')
        self.assertEqual(queued, 0)
        self.assertEqual(_DupLM.written, [])

        con = sqlite3.connect(self.db)
        row = con.execute("select status from extractions where idempotency_key='dup1#0'").fetchone()
        con.close()
        self.assertEqual(row[0], 'duplicate')

    def test_new_content_enqueued(self):
        items = [{'content': '他明天要去天津出差三天', 'importance': 0.7}]
        queued = self.memory.write_back(self.cfg, items, 'new1')
        self.assertEqual(queued, 1)
        con = sqlite3.connect(self.db)
        row = con.execute("select status from extractions where idempotency_key='new1#0'").fetchone()
        con.close()
        self.assertEqual(row[0], 'pending')


if __name__ == '__main__':
    unittest.main(verbosity=2)
