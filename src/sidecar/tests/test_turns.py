"""Durable turn 管道。"""

import importlib
import os
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


class TurnsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_turns_'))
        mods = fresh_env(self.tmp, ('config', 'crypto', 'util', 'migrations', 'memory', 'turns', 'telemetry'))
        self.memory = mods['memory']
        self.turns = mods['turns']
        self.cfg = dict(mods['config'].CFG)
        self.cfg['archive'] = {
            'enabled': True,
            'db_path': str(self.tmp / 'archive.db'),
            'encrypt': False,
        }
        self.assertTrue(self.memory.init_archive(self.cfg))

    def test_capture_and_process(self):
        self.memory.extract = lambda cfg, u, r: [{'content': '他喜欢测试', 'importance': 0.6}]
        self.memory.write_back = lambda cfg, items, seed, mid=None: 1
        captured = self.turns.capture(self.cfg, '聊点别的', '好呀')
        self.assertIsNotNone(captured)
        tid, _ = captured
        self.assertEqual(self.turns.process_one(self.cfg, tid), 'queued')
        db = self.turns._db(self.cfg)
        import sqlite3

        con = sqlite3.connect(db)
        status = con.execute('select status from turns where id=?', (tid,)).fetchone()[0]
        con.close()
        self.assertEqual(status, 'queued')

    def test_recovery_keeps_client_identity(self):
        seen = {}

        def fake_extract(cfg, u, r):
            return [{'content': 'airi 的记忆', 'importance': 0.6}]

        def fake_write(cfg, items, seed, mid=None):
            seen['cfg'] = cfg
            return 1

        self.memory.extract = fake_extract
        self.memory.write_back = fake_write
        cfg = dict(self.cfg)
        cfg.update({'client_id': 'airi', 'write_scope': 'airi:desktop',
                    'scope': 'airi:desktop', 'persona_id': 'p2'})
        tid, _ = self.turns.capture(cfg, 'airi 说的话', '好')
        # 模拟 worker 用全局 cfg 恢复
        self.assertEqual(self.turns.process_one(self.cfg, tid), 'queued')
        self.assertEqual(seen['cfg']['write_scope'], 'airi:desktop')
        self.assertEqual(seen['cfg']['persona_id'], 'p2')

    def test_claim_is_atomic(self):
        self.memory.extract = lambda cfg, u, r: [{'content': '只写一次', 'importance': 0.6}]
        calls = []
        self.memory.write_back = lambda cfg, items, seed, mid=None: calls.append(1) or 1
        tid, _ = self.turns.capture(self.cfg, '测试原子 claim', '好')
        self.assertEqual(self.turns.process_one(self.cfg, tid), 'queued')
        self.assertEqual(self.turns.process_one(self.cfg, tid), 'skip')
        self.assertEqual(len(calls), 1)

    def test_empty_is_skipped(self):
        self.memory.extract = lambda cfg, u, r: []
        tid, _ = self.turns.capture(self.cfg, '随便说说', '嗯')
        self.assertEqual(self.turns.process_one(self.cfg, tid), 'skipped')

    def test_parse_failure_retries_then_fails(self):
        self.memory.extract = lambda cfg, u, r: None
        tid, _ = self.turns.capture(self.cfg, '说点事', '好')
        self.assertEqual(self.turns.process_one(self.cfg, tid), 'failed')
        import sqlite3

        con = sqlite3.connect(self.turns._db(self.cfg))
        status, attempts = con.execute('select status, attempts from turns where id=?', (tid,)).fetchone()
        con.close()
        self.assertEqual(status, 'pending')
        self.assertEqual(attempts, 1)

    def test_recover_processing(self):
        tid, _ = self.turns.capture(self.cfg, '恢复测试', '好')
        import sqlite3

        con = sqlite3.connect(self.turns._db(self.cfg))
        con.execute("update turns set status='processing' where id=?", (tid,))
        con.commit()
        con.close()
        self.assertEqual(self.turns.recover(self.cfg), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
