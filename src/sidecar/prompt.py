"""提示词组装：人格 + 知识库 + 记忆，按位置摆放。"""

from __future__ import annotations

import threading

import knowledge
import memory
import timeparse
import persona
from upstream import build_recall_query
from util import log


def fetch_context(cfg: dict, messages: list) -> dict:
    """并行取回记忆与知识库，减少串行等待。"""
    query = build_recall_query(messages)
    result = {'query': query, 'memories': [], 'knowledge': [], 'time_range': None}
    if not query:
        return result

    rng = timeparse.detect_range(query)
    if rng:
        result['time_range'] = timeparse.describe(rng[0], rng[1])

    def get_memories():
        result['memories'] = memory.recall_for_prompt(cfg, query)

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
            '每条前面的方括号是这件事发生（或记录）的时间。'
            '回答与时间有关的问题时，按这个日期说（例如「11 月 25 号那天」），'
            '不要把旧事说成「昨天 / 最近」；如果日期和他问的对不上，就直接说那天没有记录，不要硬凑。'
            '对话历史里你之前说过的日期可能记错，涉及时间时以本段记忆的方括号日期为准，'
            '不要顺着历史里自己之前的错误说法继续编。'
            '这些是你确实知道的。如果其中有能回答上一条问题的内容，就直接说，'
            '不要因为之前说过"不知道"就继续不知道。'
            '（记忆可能过时，与他刚说的冲突时以他刚说的为准）'
        )

    time_range = ctx.get('time_range')
    if time_range:
        blocks.append(
            f'【时间校准】\n'
            f'他现在问的是：{time_range}。\n'
            '只依据上面标注了这个时间段的记忆回答；如果这一段没有记录，就直说记不清，'
            '不要拿更早的事来凑，也不要把昨天/昨晚说成别的日子。'
        )

    return '\n\n'.join(blocks)


def trim_messages(messages: list, max_messages: int = 40, max_chars: int = 24000) -> list:
    """裁剪过长的历史，保留最近的对话；system 消息不占名额。"""
    if not messages:
        return messages
    systems = [m for m in messages if m.get('role') == 'system']
    chats = [m for m in messages if m.get('role') != 'system']

    if max_messages and len(chats) > max_messages:
        chats = chats[-max_messages:]

    if max_chars and max_chars > 0:
        total = sum(len(str(m.get('content') or '')) for m in chats)
        while chats and total > max_chars:
            dropped = chats.pop(0)
            total -= len(str(dropped.get('content') or ''))

    return systems + chats


def build_messages(cfg: dict, payload: dict) -> list:
    messages = payload.get('messages') or []
    ctx_cfg = cfg.get('context') or {}
    max_messages = int(ctx_cfg.get('max_messages') or 40)
    max_chars = int(ctx_cfg.get('max_chars') or 24000)
    before = len(messages)
    messages = trim_messages(messages, max_messages, max_chars)
    if len(messages) < before:
        log(f'上下文裁剪: {before} -> {len(messages)} 条')

    # 人格四层兜底后永不为空；degraded 时显式记录，绝不静默返回原消息
    snap = persona.snapshot(cfg)
    text = snap.text
    if snap.degraded:
        log(f'人格降级: source={snap.source} stale={snap.stale} 原因={snap.error[:80]}')
    if not text.strip():
        # 理论上不会走到这里（builtin 常量非空），保留最后防线
        log('人格四层兜底全部为空，使用内置常量')
        text = persona.BUILTIN_PERSONA

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
