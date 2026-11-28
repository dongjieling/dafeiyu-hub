"""write_scope 校验（契约 2）。

格式：`平台:消息类型:目标`
例：`一号:FriendMessage:123456789`

三种情况（ADR）：
- 格式非法                 → DEGRADED + 暂停写回
- 格式合法、目标尚未出现    → WARN + 允许写（新号/新会话常见）
- 格式合法、类型明显不匹配  → DEGRADED + 暂停写回
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from contracts import ContractResult, Severity

PATTERN = re.compile(r'^([^:]+):(FriendMessage|GroupMessage):(.+)$')
LOCAL_PATTERN = re.compile(r'^[^:]+:desktop$')


def parse(value: str):
    """返回 (platform, message_type, target) 或 None。"""
    if not value:
        return None
    m = PATTERN.match(value.strip())
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def _sessions_seen(cfg: dict, scope: str) -> bool | None:
    """在 conversations.db 里找该 session。None 表示无法判断。"""
    src = (cfg.get('archive') or {}).get('qq_source_db') or ''
    if not src:
        return None
    path = Path(src)
    if not path.exists():
        return None
    try:
        con = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5)
        row = con.execute('select 1 from sessions where session_id=? limit 1', (scope,)).fetchone()
        con.close()
        return bool(row)
    except Exception:
        return None


def validate(cfg: dict) -> ContractResult:
    """检查 write_scope 是否可用于写回。"""
    raw = (cfg.get('write_scope') or '').strip()
    if not raw:
        return ContractResult(
            name='astrbot.write_scope', ok=False, severity=Severity.DEGRADED,
            source='config', detail='未配置 write_scope，写回已暂停',
        )

    if raw == 'desktop' or LOCAL_PATTERN.match(raw):
        return ContractResult(
            name='astrbot.write_scope', ok=True, severity=Severity.OK,
            source='local', detail='本地客户端 scope',
        )

    parsed = parse(raw)
    if not parsed:
        return ContractResult(
            name='astrbot.write_scope', ok=False, severity=Severity.DEGRADED,
            source='config', detail=f'格式非法（应为 平台:消息类型:目标）: {raw[:40]}',
        )

    platform, msg_type, target = parsed

    # 类型与目标明显不匹配：群号一般是纯数字，私聊 QQ 号也是数字，
    # 但群号通常 6-12 位且以 1/2/3 开头；这里只做最基本的形态检查
    if msg_type == 'GroupMessage' and not target.isdigit():
        return ContractResult(
            name='astrbot.write_scope', ok=False, severity=Severity.DEGRADED,
            source='config', detail=f'GroupMessage 的目标不像群号: {target[:20]}',
        )

    seen = _sessions_seen(cfg, raw)
    mode = (cfg.get('write_scope_check') or 'warn').lower()

    if seen is False:
        severity = Severity.DEGRADED if mode == 'strict' else Severity.WARN
        return ContractResult(
            name='astrbot.write_scope',
            ok=mode != 'strict',
            severity=severity,
            source='conversations.db',
            version=f'{platform}/{msg_type}',
            detail='目标会话尚未出现（新号或新会话时正常）',
        )

    return ContractResult(
        name='astrbot.write_scope', ok=True, severity=Severity.OK,
        source='conversations.db' if seen else 'format',
        version=f'{platform}/{msg_type}',
        detail='ok' if seen else '格式合法（暂无会话数据可核对）',
    )


def can_write_back(cfg: dict) -> bool:
    """写回是否允许。"""
    return validate(cfg).ok
