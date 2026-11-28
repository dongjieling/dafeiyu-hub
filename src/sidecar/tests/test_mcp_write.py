"""MCP memory_save 权限与入队。"""

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

SIDECAR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SIDECAR))


class McpSaveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_mcpsave_'))
        os.environ['HUB_DATA_DIR'] = str(self.tmp)
        for n in ('config', 'crypto', 'util', 'clients', 'memory'):
            sys.modules.pop(n, None)
        self.mcp = importlib.import_module('mcp_server')
        self.memory = importlib.import_module('memory')

    def cfg(self, allow_write):
        return {'allow_write': allow_write, 'client_id': 'test', 'write_scope': 'test:desktop',
                'persona_id': 'dafeiyu'}

    def test_denied_without_permission(self):
        self.mcp.active_cfg = lambda: self.cfg(False)
        with self.assertRaises(ValueError):
            self.mcp.call_tool('memory_save', {'content': '测试写入'})

    def test_queued_with_permission(self):
        self.mcp.active_cfg = lambda: self.cfg(True)
        calls = {}

        def fake_write(cfg, items, seed):
            calls['items'] = items
            return 1

        self.memory.write_back = fake_write
        res = self.mcp.call_tool('memory_save', {'content': '她今天学了 MCP', 'importance': 0.7})
        text = res['content'][0]['text']
        self.assertIn('"queued": 1', text)
        self.assertEqual(calls['items'][0]['importance'], 0.7)


if __name__ == '__main__':
    unittest.main(verbosity=2)
