"""上下文裁剪与时间校准提示。"""

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


class TrimTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_trim_'))
        self.prompt = fresh_env(self.tmp, ('config', 'crypto', 'prompt', 'memory', 'timeparse'))['prompt']

    def test_trim_keeps_recent_and_system(self):
        msgs = [{'role': 'system', 'content': 'sys'}]
        msgs += [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': f'm{i}'} for i in range(57)]
        out = self.prompt.trim_messages(msgs, max_messages=40, max_chars=999999)
        self.assertEqual(out[0]['role'], 'system')
        self.assertEqual(out[-1]['content'], 'm56')
        self.assertEqual(len([m for m in out if m['role'] != 'system']), 40)

    def test_trim_by_chars(self):
        msgs = [{'role': 'user', 'content': 'x' * 100} for _ in range(10)]
        out = self.prompt.trim_messages(msgs, max_messages=100, max_chars=350)
        self.assertLessEqual(len(out), 4)
        self.assertEqual(out[-1]['content'], 'x' * 100)

    def test_time_calibration_block(self):
        block = self.prompt.build_blocks({
            'memories': ['[2026-11-28 00:56] 昨晚的事'],
            'knowledge': [],
            'time_range': '2026-11-27 18:00 ~ 2026-11-28 06:00',
        })
        self.assertIn('【时间校准】', block)
        self.assertIn('2026-11-27 18:00', block)
        self.assertIn('不要拿更早的事来凑', block)

    def test_no_time_block_without_range(self):
        block = self.prompt.build_blocks({'memories': ['普通记忆'], 'knowledge': [], 'time_range': None})
        self.assertNotIn('【时间校准】', block)


if __name__ == '__main__':
    unittest.main(verbosity=2)
