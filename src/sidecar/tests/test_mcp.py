"""MCP stdio 冒烟测试：初始化、列工具、调用。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SIDECAR = Path(__file__).resolve().parent.parent


class McpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_mcp_'))
        env = dict(os.environ)
        env['HUB_DATA_DIR'] = str(self.tmp)
        env['PYTHONPATH'] = str(SIDECAR)
        self.proc = subprocess.Popen(
            [sys.executable, '-X', 'utf8', str(SIDECAR / 'mcp_server.py')],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            env=env,
        )

    def tearDown(self):
        self.proc.kill()
        self.proc.wait(timeout=5)

    def send(self, msg):
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + '\n')
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        return json.loads(line)

    def test_initialize_and_tools(self):
        res = self.send({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}})
        self.assertEqual(res['result']['protocolVersion'], '2024-11-05')
        self.assertIn('tools', res['result']['capabilities'])

        res = self.send({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}})
        names = {t['name'] for t in res['result']['tools']}
        self.assertEqual(names, {'memory_recall', 'memory_list', 'persona_get', 'memory_health'})

    def test_resources(self):
        res = self.send({'jsonrpc': '2.0', 'id': 10, 'method': 'resources/list', 'params': {}})
        uris = {r['uri'] for r in res['result']['resources']}
        self.assertIn('persona://current', uris)
        res = self.send({'jsonrpc': '2.0', 'id': 11, 'method': 'resources/read',
                         'params': {'uri': 'persona://current'}})
        self.assertTrue(res['result']['contents'][0]['text'])

    def test_unknown_method(self):
        res = self.send({'jsonrpc': '2.0', 'id': 3, 'method': 'nope'})
        self.assertEqual(res['error']['code'], -32601)


if __name__ == '__main__':
    unittest.main(verbosity=2)
