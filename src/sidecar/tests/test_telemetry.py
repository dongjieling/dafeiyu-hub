"""四期验收：事件层。"""

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

SIDECAR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SIDECAR))


def fresh_env(tmp: Path):
    os.environ['HUB_DATA_DIR'] = str(tmp)
    sys.modules.pop('telemetry', None)
    return importlib.import_module('telemetry')


class TelemetryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_tel_'))
        self.tel = fresh_env(self.tmp)
        self.tel.clear()

    def test_record_and_recent(self):
        t = self.tel.new_trace()
        self.tel.record('extract', 'ok', 12, '2 条', t)
        self.tel.record('write', 'ok', 3, 'x', t)
        recent = self.tel.recent(5)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[-1]['stage'], 'write')
        self.assertEqual(recent[-1]['trace'], t)

    def test_failure_is_surfaced(self):
        self.tel.record('extract', 'ok', 1, '')
        self.tel.record('write', 'failed', 5, 'HTTP 500')
        fail = self.tel.last_failure()
        self.assertIsNotNone(fail)
        self.assertEqual(fail['stage'], 'write')
        self.assertEqual(fail['outcome'], 'failed')

    def test_summary_shape(self):
        self.tel.record('upstream', 'ok', 800, 'model=x', 'abc')
        self.tel.record('write', 'failed', 0, 'boom', 'abc')
        s = self.tel.summary()
        self.assertEqual(s['last']['stage'], 'write')
        self.assertEqual(s['last_failure']['outcome'], 'failed')
        self.assertEqual(s['total'], 2)
        self.assertEqual(len(s['recent']), 2)

    def test_ring_limit(self):
        for i in range(250):
            self.tel.record('x', 'ok', i, '')
        self.assertLessEqual(len(self.tel.recent(1000)), 200)


if __name__ == '__main__':
    unittest.main(verbosity=2)
