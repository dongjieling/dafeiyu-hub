"""核心逻辑单元测试：python -m unittest discover -s tests"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from upstream import build_recall_query, clean_query, last_user_text  # noqa: E402
import prompt  # noqa: E402


class TestCleanQuery(unittest.TestCase):
    def test_strip_timestamp(self):
        self.assertEqual(clean_query('[2026-11-24 16:08] 我体测跑多少米'), '我体测跑多少米')

    def test_strip_context_block(self):
        raw = '[2026-11-24 16:08] 我体测跑多少米\n[Context]\n- system:airi-runtime-prompt'
        self.assertEqual(clean_query(raw), '我体测跑多少米')

    def test_strip_act_markers(self):
        raw = '<|ACT {"emotion":"awkward"}|><|DELAY 1|> 哦对，那是昨天吃鸡丝面。'
        self.assertEqual(clean_query(raw), '哦对，那是昨天吃鸡丝面。')

    def test_strip_all_three(self):
        raw = '[2026-11-24 16:09] <|ACT {"a":1}|> 我中午吃的什么\n[Context]\n- system:x'
        self.assertEqual(clean_query(raw), '我中午吃的什么')

    def test_empty_and_plain(self):
        self.assertEqual(clean_query(''), '')
        self.assertEqual(clean_query('普通一句话'), '普通一句话')


class TestRecallQuery(unittest.TestCase):
    def test_prefers_last_user(self):
        msgs = [
            {'role': 'user', 'content': '前面的话'},
            {'role': 'assistant', 'content': '好的'},
            {'role': 'user', 'content': '[2026-11-24 16:20] 我体测跑多少米\n[Context]'},
        ]
        self.assertEqual(build_recall_query(msgs), '我体测跑多少米')

    def test_falls_back_to_recent_when_too_short(self):
        msgs = [
            {'role': 'user', 'content': '我今天体测了'},
            {'role': 'assistant', 'content': '累不累'},
            {'role': 'user', 'content': '嗯'},
        ]
        q = build_recall_query(msgs)
        self.assertIn('体测', q)  # 短消息时拼上上文

    def test_handles_multipart_content(self):
        msgs = [{'role': 'user', 'content': [{'type': 'text', 'text': '你好啊'}]}]
        self.assertEqual(last_user_text(msgs), '你好啊')


class TestPromptBlocks(unittest.TestCase):
    def test_memory_block_position_and_wording(self):
        ctx = {'memories': ['他体测跑了1000米'], 'knowledge': []}
        block = prompt.build_blocks(ctx)
        self.assertIn('他体测跑了1000米', block)
        self.assertIn('想起来了', block)
        self.assertIn('不要因为之前说过', block)

    def test_knowledge_block(self):
        ctx = {'memories': [], 'knowledge': ['罗德岛是制药公司']}
        block = prompt.build_blocks(ctx)
        self.assertIn('设定资料', block)
        self.assertIn('罗德岛是制药公司', block)

    def test_both_blocks(self):
        ctx = {'memories': ['记忆A'], 'knowledge': ['知识B']}
        block = prompt.build_blocks(ctx)
        self.assertLess(block.index('知识B'), block.index('记忆A'))

    def test_empty(self):
        self.assertEqual(prompt.build_blocks({'memories': [], 'knowledge': []}), '')


class TestBuildMessages(unittest.TestCase):
    def setUp(self):
        self.cfg = {'persona_id': 'test', 'persona_db': '', 'persona_cache_seconds': 0}
        self._orig = (prompt.persona.read_persona, prompt.fetch_context)
        prompt.persona.read_persona = lambda cfg: '你是一只测试用的鲸鱼'
        prompt.fetch_context = lambda cfg, msgs: {'memories': ['他喜欢面条'], 'knowledge': []}

    def tearDown(self):
        prompt.persona.read_persona, prompt.fetch_context = self._orig

    def test_persona_first_and_memory_before_last_user(self):
        payload = {'messages': [
            {'role': 'user', 'content': '第一句'},
            {'role': 'assistant', 'content': '回应'},
            {'role': 'user', 'content': '最后一句'},
        ]}
        out = prompt.build_messages(self.cfg, payload)
        self.assertEqual(out[0]['role'], 'system')
        self.assertIn('鲸鱼', out[0]['content'])
        # 记忆块应插在最后一条 user 之前
        contents = [m.get('content') for m in out]
        mem_idx = next(i for i, c in enumerate(contents) if '他喜欢面条' in (c or ''))
        last_user_idx = max(i for i, m in enumerate(out) if m.get('role') == 'user')
        self.assertLess(mem_idx, last_user_idx)

    def test_merges_existing_system(self):
        payload = {'messages': [
            {'role': 'system', 'content': '额外规则'},
            {'role': 'user', 'content': '问题'},
        ]}
        out = prompt.build_messages(self.cfg, payload)
        self.assertEqual(out[0]['role'], 'system')
        self.assertIn('额外规则', out[0]['content'])
        self.assertIn('鲸鱼', out[0]['content'])
        # 原 system 消息不应重复出现
        self.assertEqual(sum(1 for m in out if m.get('role') == 'system'), 2)  # 人格 + 记忆块

    def test_no_persona_returns_original(self):
        prompt.persona.read_persona = lambda cfg: ''
        payload = {'messages': [{'role': 'user', 'content': 'hi'}]}
        out = prompt.build_messages(self.cfg, payload)
        self.assertEqual(out, payload['messages'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
