"""归档 / 加密 / 同步 冒烟测试。

覆盖：
- 新库建表是否含重试三列（P0-2）
- 归档写入是否加密（P0-3）
- 提炼记录是否加密 + message_id（P0-3）
- QQ 同步是否真的能导入（P0-1 回归）
"""

import importlib
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

SIDECAR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SIDECAR))


def fresh_env(tmp: Path):
    """每个用例独立的 DATA_DIR，并强制重载 config/crypto。"""
    os.environ['HUB_DATA_DIR'] = str(tmp)
    for name in ('config', 'crypto', 'memory', 'sync', 'retry'):
        sys.modules.pop(name, None)
    config = importlib.import_module('config')
    memory = importlib.import_module('memory')
    sync = importlib.import_module('sync')
    crypto = importlib.import_module('crypto')
    return config, memory, sync, crypto


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_test_'))
        self.config, self.memory, self.sync, self.crypto = fresh_env(self.tmp)
        self.cfg = dict(self.config.CFG)
        self.cfg['archive'] = {
            'enabled': True,
            'db_path': 'archive.db',
            'retention_days': 0,
            'sync_qq': True,
            'qq_source_db': '',
            'encrypt': True,
        }
        self.memory.init_archive(self.cfg)
        self.db = self.tmp / 'archive.db'

    def test_new_db_has_retry_columns(self):
        """新库必须自带 retry_count / next_retry_at / last_error_at。"""
        con = sqlite3.connect(self.db)
        cols = {r[1] for r in con.execute('PRAGMA table_info(extractions)')}
        con.close()
        self.assertTrue({'retry_count', 'next_retry_at', 'last_error_at'} <= cols, cols)

    def test_archive_message_is_encrypted(self):
        mid = self.memory.archive_message(self.cfg, 'user', '他明天要去看牙医')
        con = sqlite3.connect(self.db)
        content = con.execute('select content from messages where id=?', (mid,)).fetchone()[0]
        con.close()
        self.assertTrue(content.startswith('enc:v1:'), content[:20])
        self.assertEqual(self.crypto.decrypt(content), '他明天要去看牙医')

    def test_record_extraction_encrypted_and_message_id(self):
        item = {'content': '他打算明年换工作', 'importance': 0.6}
        self.memory.record_extraction(self.cfg, item, 'idem-1', 1, 'written', None, 42)
        con = sqlite3.connect(self.db)
        row = con.execute('select content, message_id from extractions where idempotency_key=?', ("idem-1",)).fetchone()
        con.close()
        self.assertIsNotNone(row)
        self.assertTrue(row[0].startswith('enc:v1:'))
        self.assertEqual(self.crypto.decrypt(row[0]), '他打算明年换工作')
        self.assertEqual(row[1], 42)

    def test_qq_sync_imports_one(self):
        """假源库 → 同步一条 → 归档里应出现 1 条且加密。"""
        src = self.tmp / 'conversations.db'
        con = sqlite3.connect(src)
        con.execute(
            'create table messages (id integer primary key, session_id text, role text, content text, '
            'sender_id text, sender_name text, group_id text, platform text, timestamp real, metadata text)'
        )
        con.execute(
            'insert into messages values (1, "qq:test", "user", "同步冒烟测试", "1", "tester", null, "aiocqhttp", 1700000000, "{}")'
        )
        con.commit()
        con.close()

        cfg = dict(self.cfg)
        cfg['archive'] = dict(self.cfg['archive'], qq_source_db=str(src))
        imported = self.sync.sync_qq(cfg)
        self.assertEqual(imported, 1)

        con = sqlite3.connect(self.db)
        row = con.execute('select content from messages order by id desc limit 1').fetchone()[0]
        con.close()
        self.assertTrue(row.startswith('enc:v1:'), row[:20])
        self.assertEqual(self.crypto.decrypt(row), '同步冒烟测试')


if __name__ == '__main__':
    unittest.main(verbosity=2)
