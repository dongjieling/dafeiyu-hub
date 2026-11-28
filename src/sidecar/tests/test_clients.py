"""客户端 profile 与鉴权。"""

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


class ClientsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_clients_'))
        self.mods = fresh_env(self.tmp, ('config', 'crypto', 'util', 'clients'))
        self.clients = self.mods['clients']
        self.base = dict(self.mods['config'].CFG)

    def save(self, mode='off', clients=None):
        data, err = self.clients.validate({'auth_mode': mode, 'clients': clients or []})
        self.assertEqual(err, '')
        self.clients.save(data)
        return data

    def test_validate_rejects_bad_id(self):
        _, err = self.clients.validate({'clients': [{'id': '中文'}]})
        self.assertIn('不合法', err)

    def test_off_uses_default(self):
        client, err = self.clients.authenticate({})
        self.assertEqual(err, '')
        self.assertEqual(client['id'], 'default')

    def test_required_rejects_missing(self):
        self.save('required', [{'id': 'airi', 'token': 't1'}])
        client, err = self.clients.authenticate({})
        self.assertIsNone(client)
        self.assertIn('需要', err)

    def test_token_match(self):
        self.save('required', [{'id': 'airi', 'token': 't1', 'scope': 'airi:desktop'}])
        client, err = self.clients.authenticate({'Authorization': 'Bearer t1'})
        self.assertEqual(err, '')
        self.assertEqual(client['id'], 'airi')

    def test_optional_rejects_bad_token(self):
        self.save('optional', [{'id': 'airi', 'token': 't1'}])
        client, err = self.clients.authenticate({'Authorization': 'Bearer nope'})
        self.assertIsNone(client)
        self.assertIn('无效', err)

    def test_header_id(self):
        self.save('off', [{'id': 'airi', 'token': ''}])
        client, err = self.clients.authenticate({'X-Hub-Client': 'airi'})
        self.assertEqual(client['id'], 'airi')

    def test_effective_cfg(self):
        client = {'id': 'airi', 'name': 'AIRI', 'scope': 'airi:desktop', 'persona_id': 'p2'}
        cfg = self.clients.effective_cfg(self.base, client)
        self.assertEqual(cfg['write_scope'], 'airi:desktop')
        self.assertEqual(cfg['persona_id'], 'p2')
        self.assertEqual(cfg['client_id'], 'airi')


if __name__ == '__main__':
    unittest.main(verbosity=2)
