"""v1.0.1 修复验收：FATAL、补丁哈希门、topics 透传、迁移备份、WAL、LKG 刷新、日志脱敏。"""

import importlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

SIDECAR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SIDECAR))


def fresh_env(tmp: Path, names=('config', 'crypto', 'contracts', 'adapters', 'migrations',
                                 'scope', 'quality', 'outbox', 'memory', 'persona')):
    os.environ['HUB_DATA_DIR'] = str(tmp)
    for n in names:
        sys.modules.pop(n, None)
    return {n: importlib.import_module(n) for n in names}


class FatalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_fatal_'))
        self.mods = fresh_env(self.tmp, ('config', 'crypto', 'contracts', 'upstream', 'hub'))
        self.hub = self.mods['hub']

    def test_missing_key_is_fatal(self):
        cfg = dict(self.mods['config'].CFG)
        cfg['astrbot_config'] = str(self.tmp / 'nope.json')
        items = self.hub.startup_contracts(cfg, migration_ok=True, config_exists=True)
        names = {i.name: i for i in items}
        self.assertIn('hub.upstream', names)
        self.assertEqual(names['hub.upstream'].severity.value, 'fatal')

    def test_migration_failure_is_fatal(self):
        cfg = dict(self.mods['config'].CFG)
        items = self.hub.startup_contracts(cfg, migration_ok=False, config_exists=True)
        names = {i.name: i for i in items}
        self.assertIn('hub.archive', names)
        self.assertEqual(names['hub.archive'].severity.value, 'fatal')

    def test_ok_has_no_fatal(self):
        cfg = dict(self.mods['config'].CFG)
        cfg_path = self.tmp / 'cmd_config.json'
        cfg_path.write_text(json.dumps({'provider_sources': [
            {'id': 'deepseek', 'key': ['sk-test'], 'api_base': 'http://127.0.0.1:1'}]}), encoding='utf-8')
        cfg['astrbot_config'] = str(cfg_path)
        self.mods['upstream'].clear_upstream_cache()
        items = self.hub.startup_contracts(cfg, migration_ok=True, config_exists=True)
        self.assertEqual([i for i in items if i.severity.value == 'fatal'], [])


class PatchHashTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_ph_'))
        self.patch = fresh_env(self.tmp, ('config', 'crypto', 'contracts', 'patch_manager'))['patch_manager']
        self.file = self.tmp / 'external_api.py'

    def _status(self, version='2.14.2'):
        with mock.patch.object(self.patch, 'plugin_file', return_value=self.file), \
             mock.patch.object(self.patch, 'plugin_version', return_value=version):
            return self.patch.status()

    def test_anchor_mismatch_blocks_auto(self):
        self.file.write_text(
            'def add():\n    metadata["key_facts"] = key_facts  # 被人改过\n', encoding='utf-8')
        st = self._status()
        self.assertEqual(st.state, 'anchor_mismatch')
        self.assertFalse(st.can_auto_apply)

    def test_known_anchor_hash_matches_constant(self):
        import hashlib

        anchor = '    metadata["key_facts"] = key_facts'
        expected = hashlib.sha256(anchor.strip().encode('utf-8')).hexdigest()[:16]
        self.assertEqual(self.patch.KNOWN_VERSIONS.get('2.14.2'), expected)

    def test_exact_anchor_is_unpatched(self):
        anchor = '    metadata["key_facts"] = key_facts'
        self.file.write_text(f'def add():\n{anchor}\n', encoding='utf-8')
        st = self._status()
        self.assertEqual(st.state, 'unpatched')
        self.assertTrue(st.can_auto_apply)


class TopicsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_topics_'))
        self.mods = fresh_env(self.tmp)
        self.db = self.tmp / 'archive.db'
        self.assertTrue(self.mods['migrations'].ensure_archive(self.db).ok)
        _CaptureLM.received = []
        self.server = HTTPServer(('127.0.0.1', 0), _CaptureLM)
        port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.cfg = dict(self.mods['config'].CFG)
        self.cfg.update(
            lm_base=f'http://127.0.0.1:{port}',
            lm_key='k',
            write_scope='一号:FriendMessage:123456789',
            write_scope_check='warn',
            archive=dict(self.cfg.get('archive') or {}, enabled=True, db_path='archive.db'),
            outbox={'enabled': True},
            logging={'log_content': False},
        )

    def tearDown(self):
        self.server.shutdown()

    def test_topics_roundtrip(self):
        item = {'content': '他今晚要去准备辩论赛', 'importance': 0.6,
                'topics': ['计划', '辩论赛', '今晚']}
        rid = self.mods['outbox'].enqueue(self.cfg, item, 'top1')
        self.assertIsNotNone(rid)
        self.mods['outbox'].tick(self.cfg)
        self.assertEqual(len(_CaptureLM.received), 1)
        self.assertEqual(_CaptureLM.received[0]['topics'], ['计划', '辩论赛', '今晚'])

    def test_log_redaction(self):
        masked = self.mods['outbox']._mask(self.cfg, '他今天中午吃了牛肉面，加了两个蛋')
        self.assertNotIn('牛肉面', masked)
        self.assertIn('字', masked)


class _CaptureLM(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get('Content-Length') or 0)
        body = json.loads((self.rfile.read(n) or b'{}').decode('utf-8'))
        if self.path.endswith('/memories'):
            type(self).received.append(body)
            out = json.dumps({'memory': {'id': 'm1'}}).encode()
        else:
            out = json.dumps({'items': []}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_mig2_'))
        self.migrations = fresh_env(self.tmp, ('config', 'crypto', 'contracts', 'migrations'))['migrations']

    def test_wal_enabled(self):
        db = self.tmp / 'a.db'
        self.assertTrue(self.migrations.ensure_archive(db).ok)
        con = sqlite3.connect(db)
        mode = con.execute('PRAGMA journal_mode').fetchone()[0]
        con.close()
        self.assertEqual(mode.lower(), 'wal')

    def test_backup_failure_aborts(self):
        db = self.tmp / 'b.db'
        con = sqlite3.connect(db)
        con.executescript('CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT);')
        con.commit()
        con.close()

        with mock.patch.object(self.migrations, 'backup_database', side_effect=OSError('disk full')):
            res = self.migrations.ensure_archive(db)
        self.assertFalse(res.ok)
        self.assertIn('备份失败', res.error)

        con = sqlite3.connect(db)
        version = con.execute('PRAGMA user_version').fetchone()[0]
        con.close()
        self.assertEqual(version, 0)  # 没有继续迁移


class LkgRefreshTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_lkg_'))
        self.mods = fresh_env(self.tmp)
        self.db = self.tmp / 'data_v4.db'
        con = sqlite3.connect(self.db)
        con.execute('create table personas (id integer primary key, persona_id text, system_prompt text)')
        con.execute("insert into personas(persona_id, system_prompt) values('dafeiyu','稳定的人格内容')")
        con.commit()
        con.close()
        self.cfg = dict(self.mods['config'].CFG)
        self.cfg.update(persona_db=str(self.db), persona_cache_seconds=0, persona_id='dafeiyu')

    def test_success_refreshes_lkg_timestamp(self):
        persona = self.mods['persona']
        persona.snapshot(self.cfg, force=True)
        lkg = persona._load_lkg()
        # 人为改老 2 天
        lkg['loaded_at'] = time.time() - 2 * 86400
        persona._lkg_path().write_text(
            persona.crypto.encrypt(json.dumps(lkg, ensure_ascii=False)), encoding='utf-8')

        # 再次成功读取：内容没变，但应刷新验证时间
        persona.snapshot(self.cfg, force=True)
        refreshed = persona._load_lkg()['loaded_at']
        self.assertGreater(refreshed, time.time() - 3600)


if __name__ == '__main__':
    unittest.main(verbosity=2)
