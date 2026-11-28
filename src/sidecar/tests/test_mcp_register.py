"""MCP 注册到 AstrBot 的单元测试。"""

import importlib
import json
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


class McpRegisterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_mcpreg_'))
        self.mods = fresh_env(self.tmp, ('config', 'crypto', 'util', 'clients', 'mcp_register'))
        self.reg = self.mods['mcp_register']
        self.data = self.tmp / 'astrbot-data'
        self.data.mkdir()
        self.cfg = {'astrbot_config': str(self.data / 'cmd_config.json')}

    def test_register_creates_entry(self):
        (self.data / 'mcp_server.json').write_text(
            json.dumps({'mcpServers': {'other': {'command': 'node', 'args': ['x.js']}}}),
            encoding='utf-8',
        )
        res = self.reg.register(self.cfg)
        self.assertTrue(res['ok'])
        data = json.loads((self.data / 'mcp_server.json').read_text(encoding='utf-8'))
        self.assertIn('other', data['mcpServers'])
        entry = data['mcpServers']['dafeiyu-memory']
        self.assertTrue(entry['args'][0].endswith('mcp_server.py'))
        self.assertIn('HUB_DATA_DIR', entry['env'])
        self.assertEqual(entry['env'].get('HUB_CLIENT'), 'astrbot')
        self.assertTrue(res['backup'])

    def test_status(self):
        self.assertFalse(self.reg.status(self.cfg)['registered'])
        self.reg.register(self.cfg)
        self.assertTrue(self.reg.status(self.cfg)['registered'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
