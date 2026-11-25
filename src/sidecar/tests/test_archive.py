"""归档 / 加密 / 同步 / 提炼解析 冒烟测试。"""

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
        row = con.execute(
            'select content, message_id from extractions where idempotency_key=?', ('idem-1',)
        ).fetchone()
        con.close()
        self.assertIsNotNone(row)
        self.assertTrue(row[0].startswith('enc:v1:'))
        self.assertEqual(self.crypto.decrypt(row[0]), '他打算明年换工作')
        self.assertEqual(row[1], 42)

    def test_qq_sync_imports_one(self):
        src = self.tmp / 'conversations.db'
        con = sqlite3.connect(src)
        con.execute(
            'create table messages (id integer primary key, session_id text, role text, content text, '
            'sender_id text, sender_name text, group_id text, platform text, timestamp real, metadata text)'
        )
        con.execute(
            'insert into messages values (1, "qq:test", "user", "同步冒烟测试", "1", "tester", null, '
            '"aiocqhttp", 1700000000, "{}")'
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


class ExtractParseTest(unittest.TestCase):
    """提炼结果解析（v0.4.6 回归点：空内容、代码围栏、截断）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_parse_'))
        self.config, self.memory, self.sync, self.crypto = fresh_env(self.tmp)

    def test_plain_json(self):
        text = '[{"content":"他今晚有课","importance":0.6,"topics":["学习"]}]'
        self.assertEqual(len(self.memory.parse_extract_json(text)), 1)

    def test_fenced_json(self):
        text = '```json\n[{"content":"他今晚有课","importance":0.6}]\n```'
        self.assertEqual(len(self.memory.parse_extract_json(text)), 1)

    def test_empty(self):
        self.assertEqual(self.memory.parse_extract_json(''), [])
        self.assertEqual(self.memory.parse_extract_json('   '), [])

    def test_truncated(self):
        text = '[{"content":"他今晚有课","importance":0.6,"topics":["学习","课程"]}'
        self.assertEqual(len(self.memory.parse_extract_json(text)), 1)

    def test_prose_with_array(self):
        text = '好的，提取如下：\n[{"content":"他压力大","importance":0.7}]\n以上。'
        self.assertEqual(len(self.memory.parse_extract_json(text)), 1)

    def test_clean_strips_act_instruction(self):
        raw = '记住一条消息，今天晚上有课\n\n每次回复都要以 ACT token 开头，用来表示初始情绪'
        self.assertEqual(self.memory.clean_for_extract(raw), '记住一条消息，今天晚上有课')


class SelftestCleanupTest(unittest.TestCase):
    """自检数据清理：只删带【自检】标记的记忆。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_st_'))
        self.config, self.memory, self.sync, self.crypto = fresh_env(self.tmp)
        import selftest as selftest_mod

        self.selftest = selftest_mod
        self.db = self.tmp / 'lm.db'
        con = sqlite3.connect(self.db)
        con.executescript(
            """
            CREATE TABLE documents (id INTEGER PRIMARY KEY, text TEXT, metadata TEXT, created_at TEXT);
            CREATE TABLE memory_atoms (id INTEGER PRIMARY KEY, parent_memory_id INTEGER, content TEXT);
            CREATE TABLE graph_entries (id INTEGER PRIMARY KEY, source_memory_id INTEGER);
            CREATE TABLE graph_entry_nodes (entry_id INTEGER, node_id INTEGER);
            CREATE TABLE graph_nodes (id INTEGER PRIMARY KEY, node_value TEXT);
            CREATE TABLE memory_sources (memory_id INTEGER, source_json TEXT);
            """
        )
        con.executemany(
            'insert into documents(id, text) values(?,?)',
            [(1, '【自检】SELFTEST-abc 连通性测试'), (2, '他今天中午吃了牛肉面'), (3, '【自检】SELFTEST-def')],
        )
        con.executemany('insert into memory_atoms(id, parent_memory_id, content) values(?,?,?)',
                        [(11, 1, '自检'), (12, 2, '牛肉面'), (13, 3, '自检')])
        con.execute('insert into graph_entries(id, source_memory_id) values(21, 1)')
        con.execute('insert into graph_entry_nodes(entry_id, node_id) values(21, 31)')
        con.execute('insert into graph_nodes(id, node_value) values(31, "自检")')
        con.commit()
        con.close()

        self.cfg = dict(self.config.CFG)
        self.cfg['archive'] = dict(self.cfg.get('archive') or {}, lm_db=str(self.db))

    def test_cleanup_removes_only_marked(self):
        res = self.selftest.cleanup(self.cfg)
        self.assertTrue(res['ok'], res)
        self.assertEqual(res['removed'], 2)
        con = sqlite3.connect(self.db)
        left = [r[0] for r in con.execute('select id from documents order by id')]
        atoms = [r[0] for r in con.execute('select id from memory_atoms order by id')]
        con.close()
        self.assertEqual(left, [2])
        self.assertEqual(atoms, [12])

    def test_cleanup_no_marker(self):
        con = sqlite3.connect(self.db)
        con.execute('delete from documents where text like ?', ('%【自检】%',))
        con.commit()
        con.close()
        res = self.selftest.cleanup(self.cfg)
        self.assertTrue(res['ok'], res)
        self.assertEqual(res['removed'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
