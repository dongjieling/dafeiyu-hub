"""契约层：外部依赖的检查结果与严重度。

设计目标（ADR-001）：
- 所有外部访问返回统一的 ContractResult
- 启动与运行时共用同一套严重度语义
- 只有 FATAL 拒绝启动；DEGRADED 降级运行但必须显式可见
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    OK = 'ok'
    WARN = 'warn'
    DEGRADED = 'degraded'
    FATAL = 'fatal'


@dataclass
class ContractResult:
    """单个契约的检查结果。"""

    name: str
    ok: bool
    severity: Severity = Severity.OK
    source: str = ''
    version: str = ''
    sha256: str = ''
    detail: str = ''
    checked_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'ok': self.ok,
            'severity': self.severity.value,
            'source': self.source,
            'version': self.version,
            'sha256': self.sha256,
            'detail': self.detail,
            'checked_at': self.checked_at,
        }


class CompatReport:
    """多个契约的汇总。"""

    def __init__(self) -> None:
        self.results: dict[str, ContractResult] = {}

    def add(self, result: ContractResult) -> None:
        self.results[result.name] = result

    @property
    def overall(self) -> Severity:
        if any(r.severity is Severity.FATAL for r in self.results.values()):
            return Severity.FATAL
        if any(r.severity is Severity.DEGRADED or not r.ok for r in self.results.values()):
            return Severity.DEGRADED
        if any(r.severity is Severity.WARN for r in self.results.values()):
            return Severity.WARN
        return Severity.OK

    @property
    def can_start(self) -> bool:
        return self.overall is not Severity.FATAL

    def to_dict(self) -> dict:
        return {
            'overall': self.overall.value,
            'can_start': self.can_start,
            'contracts': {k: v.to_dict() for k, v in self.results.items()},
        }
