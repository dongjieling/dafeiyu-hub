"""1b 验收测试：迁移、scope 校验、补丁管理、LM 适配器。"""

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
from unittest import mock

SIDECAR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SIDECAR))


def fresh_env(tmp: Path):
    os.environ['HUB_DATA_DIR'] = str(tmp)
    for name in ('config', 'crypto', 'contracts', 'adapters', 'migrations', 'scope', 'patch_manager'):
        sys.modules.pop(name, None)
    return (
        importlib.import_module('config'),
        importlib.import_module('migrations'),
        importlib.import_module('scope'),
        importlib.import_module('patch_manager'),
        importlib.import_module('adapters'),
    )


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_mig_'))
        self.config, self.migrations, self.scope, self.patch, self.adapters = fresh_env(self.tmp)

    def test_new_db(self):
        db = self.tmp / 'archive.db'
        res = self.migrations.ensure_archive(db)
        self.assertTrue(res.ok, res)
        con = sqlite3.connect(db)
        self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0], 1)
        tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        con.close()
        self.assertTrue({'sessions', 'messages', 'extractions', 'kv'} <= tables)

    def test_legacy_db_upgrade(self):
        db = self.tmp / 'legacy.db'
        con = sqlite3.connect(db)
        # 模拟 v0.4.7 之前的旧表：没有 retry 三列
        con.executescript(
            """
            CREATE TABLE sessions (id INTEGER PRIMARY KEY, source TEXT, session_key TEXT,
                                   persona_id TEXT, created_at REAL, last_active REAL);
            CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id INTEGER, role TEXT,
                                   content TEXT, created_at REAL, meta TEXT);
            CREATE TABLE extractions (id INTEGER PRIMARY KEY, message_id INTEGER, content TEXT,
                                      importance REAL, idempotency_key TEXT, lm_memory_id TEXT,
                                      status TEXT, error TEXT, created_at REAL, written_at REAL);
            CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT);
            """
        )
        con.commit()
        con.close()

        res = self.migrations.ensure_archive(db)
        self.assertTrue(res.ok, res)
        con = sqlite3.connect(db)
        cols = {r[1] for r in con.execute('PRAGMA table_info(extractions)')}
        version = con.execute('PRAGMA user_version').fetchone()[0]
        con.close()
        self.assertEqual(version, 1)
        self.assertTrue({'retry_count', 'next_retry_at', 'last_error_at'} <= cols)

    def test_idempotent(self):
        db = self.tmp / 'archive.db'
        self.assertTrue(self.migrations.ensure_archive(db).ok)
        self.assertTrue(self.migrations.ensure_archive(db).ok)

    def test_newer_db_refused(self):
        db = self.tmp / 'future.db'
        con = sqlite3.connect(db)
        con.executescript('CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT);')
        con.execute('PRAGMA user_version = 99')
        con.commit()
        con.close()

        res = self.migrations.ensure_archive(db, backup=False)
        self.assertFalse(res.ok)
        self.assertIn('高于', res.error)


class ScopeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_scope_'))
        self.config, self.migrations, self.scope, self.patch, self.adapters = fresh_env(self.tmp)
        self.base = dict(self.config.CFG)
        # 造一个只有别的会话的 conversations.db，用于判定「目标未出现」
        src = self.tmp / 'conversations.db'
        con = sqlite3.connect(src)
        con.execute('create table sessions (session_id text)')
        con.execute("insert into sessions values ('一号:FriendMessage:11111111')")
        con.commit()
        con.close()
        self.base['archive'] = {'qq_source_db': str(src)}

    def _check(self, value, **extra):
        cfg = dict(self.base, write_scope=value, **extra)
        return self.scope.validate(cfg)

    def test_valid(self):
        r = self._check('一号:FriendMessage:123456789')
        self.assertTrue(r.ok)

    def test_empty(self):
        r = self._check('')
        self.assertFalse(r.ok)
        self.assertEqual(r.severity.value, 'degraded')

    def test_bad_format(self):
        r = self._check('oops')
        self.assertFalse(r.ok)
        self.assertIn('格式非法', r.detail)

    def test_group_target_not_numeric(self):
        r = self._check('一号:GroupMessage:abc')
        self.assertFalse(r.ok)

    def test_unseen_target_warn_allows_write(self):
        r = self._check('一号:FriendMessage:99999999')
        self.assertTrue(r.ok)
        self.assertEqual(r.severity.value, 'warn')

    def test_unseen_target_strict_blocks(self):
        r = self._check('一号:FriendMessage:99999999', write_scope_check='strict')
        self.assertFalse(r.ok)


class PatchManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_patch_'))
        self.config, self.migrations, self.scope, self.patch, self.adapters = fresh_env(self.tmp)
        self.file = self.tmp / 'external_api.py'
        self.file.write_text(
            'def add_memory():\n    metadata["key_facts"] = key_facts\n    return metadata\n',
            encoding='utf-8',
        )

    def _status(self, version='2.14.2'):
        with mock.patch.object(self.patch, 'plugin_file', return_value=self.file), \
             mock.patch.object(self.patch, 'plugin_version', return_value=version):
            return self.patch.status()

    def _apply(self, version='2.14.2'):
        with mock.patch.object(self.patch, 'plugin_file', return_value=self.file), \
             mock.patch.object(self.patch, 'plugin_version', return_value=version):
            return self.patch.apply()

    def test_unpatched_then_apply(self):
        st = self._status()
        self.assertEqual(st.state, 'unpatched')
        self.assertTrue(st.can_auto_apply)

        after = self._apply()
        self.assertEqual(after.state, 'ok')
        self.assertIn('summary_schema_version', self.file.read_text(encoding='utf-8'))

    def test_unknown_version_no_auto_apply(self):
        st = self._status(version='9.9.9')
        self.assertEqual(st.state, 'unknown_version')
        self.assertFalse(st.can_auto_apply)

        after = self._apply(version='9.9.9')
        self.assertEqual(after.state, 'unknown_version')
        self.assertNotIn('summary_schema_version', self.file.read_text(encoding='utf-8'))

    def test_anchor_missing(self):
        self.file.write_text('nothing here\n', encoding='utf-8')
        st = self._status()
        self.assertEqual(st.state, 'anchor_missing')


class _FakeLM(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps({'status': 'ok', 'version': '2.14.2'}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class LMAdapterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_lm_'))
        self.config, self.migrations, self.scope, self.patch, self.adapters = fresh_env(self.tmp)

    def test_probe_ok(self):
        server = HTTPServer(('127.0.0.1', 0), _FakeLM)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            cfg = dict(self.config.CFG, lm_base=f'http://127.0.0.1:{port}', lm_key='k')
            r = self.adapters.LivingMemoryAdapter(cfg).probe()
            self.assertTrue(r.ok)
            self.assertEqual(r.version, '2.14.2')
        finally:
            server.shutdown()

    def test_probe_fail(self):
        cfg = dict(self.config.CFG, lm_base='http://127.0.0.1:1', lm_key='k')
        r = self.adapters.LivingMemoryAdapter(cfg).probe()
        self.assertFalse(r.ok)
        self.assertEqual(r.severity.value, 'degraded')


if __name__ == '__main__':
    unittest.main(verbosity=2)
