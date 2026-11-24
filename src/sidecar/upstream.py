"""上游模型配置读取 + 消息清洗（桌宠消息污染三层：时间戳 / Context / ACT 标记）。"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from util import log

_upstream = {'cfg': None, 'ts': 0.0}
UPSTREAM_TTL = 300

_TS_RE = re.compile(r'^\s*\[?\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?\]?\s*')
_ACT_RE = re.compile(r'<\|[^|]*\|>')


def read_upstream(cfg: dict) -> dict:
    now = time.time()
    if _upstream['cfg'] and now - _upstream['ts'] < UPSTREAM_TTL:
        return _upstream['cfg']

    base = cfg.get('upstream_base') or 'https://api.deepseek.com/v1'
    key = ''
    src_id = cfg.get('upstream_source') or 'deepseek'
    conf_path = Path(cfg.get('astrbot_config') or '')

    if conf_path and conf_path.exists():
        try:
            data = json.loads(conf_path.read_text(encoding='utf-8-sig'))
            for src in data.get('provider_sources', []):
                if src.get('id') == src_id:
                    keys = src.get('key') or []
                    key = keys[0] if keys else ''
                    base = src.get('api_base') or base
                    break
        except Exception as e:
            log(f'读取上游配置失败: {e}')

    result = {'base': base, 'key': key, 'model': cfg.get('upstream_model') or 'deepseek-chat'}
    _upstream['cfg'] = result
    _upstream['ts'] = now
    return result


def clear_upstream_cache() -> None:
    _upstream['cfg'] = None
    _upstream['ts'] = 0.0


def clean_query(text: str) -> str:
    """剥离桌宠塞进消息里的时间戳、[Context] 块和 <|ACT|> 标记。"""
    t = text or ''
    for marker in ('[Context]', '[context]', '[SYSTEM]', '[System]', '---\n'):
        idx = t.find(marker)
        if idx > 0:
            t = t[:idx]
    t = _ACT_RE.sub('', t)
    for _ in range(3):
        new = _TS_RE.sub('', t)
        if new == t:
            break
        t = new
    return t.strip()


def last_user_text(messages: list) -> str:
    for m in reversed(messages or []):
        if m.get('role') == 'user':
            content = m.get('content')
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = [p.get('text', '') for p in content if isinstance(p, dict)]
                return ' '.join(parts)
    return ''


def build_recall_query(messages: list) -> str:
    """优先用最后一条用户消息；太短则拼上最近几条，给检索一点上下文。"""
    cleaned = clean_query(last_user_text(messages))
    if len(cleaned) >= 4:
        return cleaned[:300]

    recent = []
    for m in reversed((messages or [])[-6:]):
        c = m.get('content')
        if not isinstance(c, str):
            continue
        c = clean_query(c)
        if len(c) < 2:
            continue
        recent.append(c[:80])
        if sum(len(x) for x in recent) > 120:
            break
    return ' '.join(reversed(recent))[:300]
