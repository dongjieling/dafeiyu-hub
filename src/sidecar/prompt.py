"""提示词组装：人格 + 知识库 + 记忆，按位置摆放。"""

from __future__ import annotations

import threading

import knowledge
import memory
import persona
from upstream import build_recall_query
from util import log


def fetch_context(cfg: dict, messages: list) -> dict:
    """并行取回记忆与知识库，减少串行等待。"""
    query = build_recall_query(messages)
    result = {'query': query, 'memories': [], 'knowledge': []}
    if not query:
        return result

    def get_memories():
        result['memories'] = memory.recall(cfg, query)

    def get_kb():
        result['knowledge'] = knowledge.retrieve(cfg, query)

    threads = [threading.Thread(target=get_memories), threading.Thread(target=get_kb)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    return result


def build_blocks(ctx: dict) -> str:
    blocks = []

    kb_items = ctx.get('knowledge') or []
    if kb_items:
        kb_text = '\n\n'.join(f'【资料{i + 1}】\n{x}' for i, x in enumerate(kb_items))
        blocks.append(
            '【你可能用得上的设定资料】\n'
            f'{kb_text}\n'
            '（问到相关话题时自然地用上，别照抄原文，别主动背诵）'
        )

    memories = ctx.get('memories') or []
    if memories:
        lines = '\n'.join(f'- {m}' for m in memories)
        blocks.append(
            '【你现在想起来了的关于他的事】\n'
            f'{lines}\n'
            '这些是你确实知道的。如果其中有能回答上一条问题的内容，就直接说，'
            '不要因为之前说过"不知道"就继续不知道。'
            '（记忆可能过时，与他刚说的冲突时以他刚说的为准）'
        )

    return '\n\n'.join(blocks)


def build_messages(cfg: dict, payload: dict) -> list:
    messages = payload.get('messages') or []
    text = persona.read_persona(cfg)
    if not text:
        return messages

    ctx = fetch_context(cfg, messages)
    if ctx['memories']:
        log(f"召回 {len(ctx['memories'])} 条记忆")
    block = build_blocks(ctx)

    # 人格放最前；原有 system 合并进去
    out = [{'role': 'system', 'content': text}]
    for m in messages:
        if m.get('role') == 'system':
            extra = m.get('content')
            if isinstance(extra, str) and extra.strip():
                out[0]['content'] = out[0]['content'] + '\n\n' + extra
        else:
            out.append(m)

    # 记忆/知识库放在最后一条用户消息之前：靠近提问，不被长历史冲淡
    if block:
        insert_at = len(out)
        for i in range(len(out) - 1, -1, -1):
            if out[i].get('role') == 'user':
                insert_at = i
                break
        out.insert(insert_at, {'role': 'system', 'content': block})

    return out
