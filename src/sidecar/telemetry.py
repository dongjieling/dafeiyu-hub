"""四期：结构化事件与流水线追踪。

目标（ADR-001 四期）：
- 每轮请求有 trace_id，记录每个阶段的 outcome 与耗时
- /health 能回答「最后一轮卡在哪一层」
- 状态页显示最近若干条事件
"""

from __future__ import annotations

import time
import uuid
from collections import deque

from util import log

_MAX = 200
_events: deque = deque(maxlen=_MAX)
_failures: deque = deque(maxlen=50)


def new_trace() -> str:
    return uuid.uuid4().hex[:8]


def record(stage: str, outcome: str = 'ok', latency_ms: int = 0,
           detail: str = '', trace_id: str = '') -> dict:
    ev = {
        'ts': time.time(),
        'trace': trace_id,
        'stage': stage,
        'outcome': outcome,
        'ms': int(latency_ms),
        'detail': (detail or '')[:120],
    }
    _events.append(ev)
    if outcome != 'ok':
        _failures.append(ev)
        log(f'流水线异常 {stage}/{outcome}: {ev["detail"]}')
    else:
        log(f'流水线 {stage} ok {ev["ms"]}ms {ev["detail"]}')
    return ev


def recent(n: int = 20) -> list:
    return list(_events)[-n:]


def last() -> dict | None:
    return _events[-1] if _events else None


def last_failure() -> dict | None:
    return _failures[-1] if _failures else None


def summary() -> dict:
    last_ev = last()
    fail = last_failure()
    return {
        'last': last_ev,
        'last_failure': fail,
        'recent': recent(10),
        'total': len(_events),
    }


def clear() -> None:
    _events.clear()
    _failures.clear()
