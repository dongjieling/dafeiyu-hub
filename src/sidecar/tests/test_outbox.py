"""二期验收：outbox 状态机与崩溃恢复。"""

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
    for name in ('config', 'crypto', 'contracts', 'adapters', 'migrations', 'scope', 'outbox', 'memory'):
        sys.modules.pop(name, None)
    config = importlib.import_module('config')
    migrations = importlib.import_module('migrations')
    outbox = importlib.import_module('outbox')
    memory = importlib.import_module('memory')
    return config, migrations, outbox, memory


class _FakeLM(BaseHTTPRequestHandler):
    """可配置的假记忆库。"""

    fail = False
    received = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b'{}'
        if self.path.endswith('/memories'):
            if type(self).fail:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'{"error":"boom"}')
                return
            data = json.loads(body.decode('utf-8'))
            type(self).received.append(data)
            out = json.dumps({'memory': {'id': 'm-' + str(len(type(self).received))}}).encode()
        elif self.path.endswith('/recall'):
            data = json.loads(body.decode('utf-8'))
            query = data.get('query') or ''
            items = [{'content': c['content']} for c in type(self).received if query[:8] in c['content']]
            out = json.dumps({'items': items}).encode()
        else:
            out = b'{}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


class OutboxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_outbox_'))
        self.config, self.migrations, self.outbox, self.memory = fresh_env(self.tmp)
        self.db = self.tmp / 'archive.db'
        self.assertTrue(self.migrations.ensure_archive(self.db).ok)

        _FakeLM.fail = False
        _FakeLM.received = []
        self.server = HTTPServer(('127.0.0.1', 0), _FakeLM)
        port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

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

    def _row(self, idem):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        row = con.execute('select * from extractions where idempotency_key=?', (idem,)).fetchone()
        con.close()
        return row

    def test_enqueue_and_write(self):
        item = {'content': '他今天中午吃了拉面', 'importance': 0.6}
        self.assertIsNotNone(self.outbox.enqueue(self.cfg, item, 'k1'))
        self.assertEqual(self._row('k1')['status'], 'pending')

        self.outbox.tick(self.cfg)
        row = self._row('k1')
        self.assertEqual(row['status'], 'written')
        self.assertEqual(len(_FakeLM.received), 1)

        # 让写入后的验证到期
        con = sqlite3.connect(self.db)
        con.execute("update extractions set next_retry_at=0 where idempotency_key='k1'")
        con.commit()
        con.close()
        self.outbox.verify_index(self.cfg)
        self.assertEqual(self._row('k1')['status'], 'verified')

    def test_failure_backoff_then_dead(self):
        _FakeLM.fail = True
        item = {'content': '这条会一直失败', 'importance': 0.5}
        self.outbox.enqueue(self.cfg, item, 'k2')

        for i in range(self.outbox.MAX_ATTEMPTS):
            con = sqlite3.connect(self.db)
            con.execute("update extractions set next_retry_at=0 where idempotency_key='k2'")
            con.commit()
            con.close()
            self.outbox.tick(self.cfg)

        row = self._row('k2')
        self.assertEqual(row['status'], 'dead')
        self.assertGreaterEqual(row['attempts'], self.outbox.MAX_ATTEMPTS)

    def test_recover_sending(self):
        item = {'content': '崩溃前卡住的记录', 'importance': 0.5}
        self.outbox.enqueue(self.cfg, item, 'k3')
        con = sqlite3.connect(self.db)
        con.execute("update extractions set status='sending' where idempotency_key='k3'")
        con.commit()
        con.close()

        n = self.outbox.recover_stale(self.cfg)
        self.assertEqual(n, 1)
        self.assertEqual(self._row('k3')['status'], 'pending')

    def test_write_back_uses_outbox(self):
        """write_back 不再直接发请求，而是入队。"""
        _FakeLM.fail = False
        items = [{'content': '他喜欢喝美式', 'importance': 0.7}]
        queued = self.memory.write_back(self.cfg, items, 'seed1', None)
        self.assertEqual(queued, 1)
        self.assertEqual(self._row('seed1#0')['status'], 'pending')
        self.assertEqual(len(_FakeLM.received), 0)  # 还没发，等 worker

        self.outbox.tick(self.cfg)
        self.assertEqual(self._row('seed1#0')['status'], 'written')


if __name__ == '__main__':
    unittest.main(verbosity=2)
