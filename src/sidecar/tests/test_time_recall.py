"""时间感知召回：相对时间解析与区间记忆。"""

import datetime
import importlib
import os
import sqlite3
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


class TimeParseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_time_'))
        self.tp = fresh_env(self.tmp, ('config', 'crypto', 'timeparse'))['timeparse']
        self.now = datetime.datetime(2026, 11, 28, 12, 0).timestamp()

    def fmt(self, ts):
        return datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')

    def test_last_night(self):
        rng = self.tp.detect_range('你记得昨天晚上的事情吗', self.now)
        self.assertEqual(self.fmt(rng[0]), '2026-11-27 18:00')
        self.assertEqual(self.fmt(rng[1]), '2026-11-28 06:00')

    def test_yesterday_full_day(self):
        rng = self.tp.detect_range('昨天吃了什么', self.now)
        self.assertEqual(self.fmt(rng[0]), '2026-11-27 00:00')
        self.assertEqual(self.fmt(rng[1]), '2026-11-27 23:59')

    def test_today(self):
        rng = self.tp.detect_range('今天做了什么', self.now)
        self.assertEqual(self.fmt(rng[0]), '2026-11-28 00:00')

    def test_last_week(self):
        rng = self.tp.detect_range('上周去哪了', self.now)
        self.assertEqual(self.fmt(rng[0]), '2026-11-16 00:00')
        self.assertEqual(self.fmt(rng[1]), '2026-11-22 23:59')

    def test_no_range(self):
        self.assertIsNone(self.tp.detect_range('你喜欢吃什么', self.now))


class RangeMemoriesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub_range_'))
        self.mem = fresh_env(self.tmp, ('config', 'crypto', 'memory', 'timeparse'))['memory']
        self.db = self.tmp / 'lm.db'
        con = sqlite3.connect(self.db)
        con.executescript(
            'create table documents (id integer primary key autoincrement, doc_id text, text text, '
            'metadata text, created_at text, updated_at text);'
        )
        rows = [
            ('昨晚十点吃了火锅', '2026-11-27 22:00:00'),
            ('凌晨一点还在聊', '2026-11-28 01:00:00'),
            ('今天中午吃面', '2026-11-28 12:00:00'),
            ('【自检】SELFTEST-1', '2026-11-27 23:00:00'),
            ('上周的事', '2026-11-20 10:00:00'),
        ]
        for text, ts in rows:
            con.execute('insert into documents(doc_id,text,metadata,created_at,updated_at) values(?,?,?,?,?)',
                        (text, text, '{}', ts, ts))
        con.commit()
        con.close()
        self.cfg = {'lm_db': str(self.db)}

    def test_range_excludes_selftest_and_other_days(self):
        start = datetime.datetime(2026, 11, 27, 18, 0).timestamp()
        end = datetime.datetime(2026, 11, 28, 6, 0).timestamp()
        items = self.mem.range_memories(self.cfg, start, end)
        texts = [i['text'] for i in items]
        self.assertIn('昨晚十点吃了火锅', texts)
        self.assertIn('凌晨一点还在聊', texts)
        self.assertNotIn('今天中午吃面', texts)
        self.assertNotIn('上周的事', texts)
        self.assertFalse(any('SELFTEST' in t for t in texts))

    def test_format_has_date(self):
        text = self.mem._fmt_memory({'text': '测试内容', 'ts': datetime.datetime(2026, 11, 27, 22, 0).timestamp()})
        self.assertTrue(text.startswith('[2026-11-27 22:00]'))
        self.assertIn('测试内容', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
