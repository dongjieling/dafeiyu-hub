"""1a 验收测试：人格四层兜底与契约检查。

覆盖 ADR 六种场景：
  1 改名（persos 表不存在）
  2 空表（没有 persona 行）
  3 system_prompt=''（空内容，且不能覆盖 LKG）
  4 DB 锁住（走 LKG）
  5 fallback 缺失（走内置常量）
  6 LKG 过期（stale=True）
"""

import importlib
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

SIDECAR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SIDECAR))


def fresh_env(tmp: Path):
    os.environ['HUB_DATA_DIR'] = str(tmp)
    for name in ('config', 'crypto', 'adapters', 'contracts', 'persona', 'prompt'):
        sys.modules.pop(name, None)
    config = importlib.import_module('config')
    persona = importlib.import_module('persona')
    adapters = importlib.import_module('adapters')
    return config, persona, adapters


def make_db(path: Path, rows, table='personas'):
    con = sqlite3.connect(path)
    con.execute(
        f'CREATE TABLE IF NOT EXISTS {table} '
        '(id INTEGER PRIMARY KEY, persona_id TEXT, system_prompt TEXT, created_at TEXT, updated_at TEXT)'
    )
    for pid, prompt in rows:
        con.execute(
            f'insert into {table}(persona_id, system_prompt) values(?,?)', (pid, prompt)
        )
    con.commit()
    con.close()


class PersonaContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_persona_'))
        self.config, self.persona, self.adapters = fresh_env(self.tmp)
        self.db = self.tmp / 'data_v4.db'
        self.cfg = dict(self.config.CFG)
        self.cfg['persona_db'] = str(self.db)
        self.cfg['persona_id'] = 'dafeiyu'
        self.cfg['persona_cache_seconds'] = 0  # 每次都重新读

    def tearDown(self):
        self.persona.clear_cache()

    def _good_db(self, text='你是大肥鱼，一条鲸鱼娘。'):
        make_db(self.db, [('dafeiyu', text)])

    # 1. 表不存在
    def test_missing_table_falls_back(self):
        make_db(self.db, [('dafeiyu', 'x')], table='persona')
        snap = self.persona.snapshot(self.cfg, force=True)
        self.assertTrue(snap.degraded)
        self.assertIn(snap.source, ('fallback', 'builtin'))
        self.assertTrue(snap.text)

    # 2. 空表
    def test_empty_table_falls_back(self):
        make_db(self.db, [])
        snap = self.persona.snapshot(self.cfg, force=True)
        self.assertTrue(snap.degraded)
        self.assertIn(snap.source, ('fallback', 'builtin'))

    # 3. 空内容：降级且不覆盖 LKG
    def test_empty_prompt_does_not_overwrite_lkg(self):
        self._good_db('原始人格内容')
        first = self.persona.snapshot(self.cfg, force=True)
        self.assertEqual(first.source, 'astrbot_db')

        con = sqlite3.connect(self.db)
        con.execute("update personas set system_prompt=''")
        con.commit()
        con.close()

        snap = self.persona.snapshot(self.cfg, force=True)
        self.assertTrue(snap.degraded)
        self.assertEqual(snap.source, 'lkg')
        self.assertEqual(snap.text, '原始人格内容')

        # LKG 仍应保留原始内容
        lkg = self.persona._load_lkg()
        self.assertEqual(lkg['text'], '原始人格内容')

    # 4. DB 锁住 → LKG
    def test_locked_db_uses_lkg(self):
        self._good_db('锁定前的人格')
        self.persona.snapshot(self.cfg, force=True)

        con = sqlite3.connect(self.db, timeout=1)
        con.execute('BEGIN EXCLUSIVE')
        try:
            snap = self.persona.snapshot(self.cfg, force=True)
            self.assertTrue(snap.degraded)
            self.assertEqual(snap.source, 'lkg')
            self.assertEqual(snap.text, '锁定前的人格')
        finally:
            con.rollback()
            con.close()

    # 5. fallback 缺失 → 内置常量
    def test_missing_fallback_uses_builtin(self):
        make_db(self.db, [])
        original = self.persona._fallback_text
        self.persona._fallback_text = lambda: ''
        try:
            snap = self.persona.snapshot(self.cfg, force=True)
            self.assertEqual(snap.source, 'builtin')
            self.assertTrue(snap.text)
            self.assertTrue(snap.degraded)
        finally:
            self.persona._fallback_text = original

    # 6. LKG 过期 → stale
    def test_stale_lkg(self):
        self._good_db('两天前的人格')
        self.persona.snapshot(self.cfg, force=True)

        # 把 LKG 的 loaded_at 改到两天前
        import json as _json

        lkg = self.persona._load_lkg()
        lkg['loaded_at'] = time.time() - 2 * 86400
        self.persona._lkg_path().write_text(
            self.persona.crypto.encrypt(_json.dumps(lkg, ensure_ascii=False)), encoding='utf-8'
        )

        con = sqlite3.connect(self.db)
        con.execute("update personas set system_prompt=''")
        con.commit()
        con.close()

        snap = self.persona.snapshot(self.cfg, force=True)
        self.assertEqual(snap.source, 'lkg')
        self.assertTrue(snap.stale)

    # 契约检查
    def test_probe_persona(self):
        self._good_db('人格')
        r = self.adapters.AstrBotAdapter(self.cfg).probe_persona()
        self.assertTrue(r.ok)
        self.assertEqual(r.severity.value, 'ok')

        self.db.unlink()
        r2 = self.adapters.AstrBotAdapter(self.cfg).probe_persona()
        self.assertFalse(r2.ok)
        self.assertEqual(r2.severity.value, 'degraded')


if __name__ == '__main__':
    unittest.main(verbosity=2)
