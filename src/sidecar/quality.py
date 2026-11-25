"""五期：记忆质量。

三件事：
1. 写入去重：内容高度相似就不重复写
2. 作用域过滤：召回只取本域
3. 重要度重做：稳定性 + 信息量两维加权，封顶 0.85
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from util import log

# 重要度权重与上限（ADR P1-4：A 方案 + 封顶）
W_IMPORTANCE = 0.6
W_STABILITY = 0.4
CAP = 0.85
FLOOR = 0.2
DUP_RATIO = 0.88


def _norm(text: str) -> str:
    return re.sub(r'[\s，。！？、,.!?"\'（）()：:；;]', '', (text or '').lower())


def final_importance(importance: float, stability: float | None = None) -> float:
    """两维加权 + 封顶，避免满分通胀。"""
    try:
        imp = float(importance)
    except (TypeError, ValueError):
        imp = 0.5
    if stability is None:
        # 没给稳定性时按内容类型粗估：具体事件偏低，偏好偏高
        stab = imp
    else:
        try:
            stab = float(stability)
        except (TypeError, ValueError):
            stab = imp

    score = W_IMPORTANCE * imp + W_STABILITY * stab
    return round(min(max(score, FLOOR), CAP), 2)


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def is_duplicate(cfg: dict, content: str, existing: list[str]) -> bool:
    """existing 是召回回来的已有记忆文本。"""
    target = _norm(content)
    if not target:
        return True
    for old in existing:
        if not old:
            continue
        norm_old = _norm(old)
        if target == norm_old:
            return True
        if abs(len(target) - len(norm_old)) <= max(4, len(target) // 3):
            if similar(content, old) >= DUP_RATIO:
                return True
    return False


def filter_by_scope(items: list, scope: str) -> list:
    """召回结果按域过滤：没有 scope 字段的保留，有且不匹配的丢弃。"""
    if not scope:
        return items
    kept = []
    for it in items:
        s = it.get('session_id') if isinstance(it, dict) else None
        if s and s != scope:
            continue
        kept.append(it)
    return kept
