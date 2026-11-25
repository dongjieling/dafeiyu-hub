"""插件补丁管理（契约 6）。

背景：hub 写入的记忆要生成原子，需要在插件的 external_api.py 里加一行标记。
但这是改第三方源码，插件升级会整目录替换，补丁会丢。

规则（ADR-001）：
- 版本门：只在「已知支持的插件版本 + 锚点哈希匹配」时自动应用
- 未知版本 → DEGRADED + 需人工确认，不自动改
- 应用前备份、应用后自检、失败回滚
"""

from __future__ import annotations

import hashlib
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from contracts import ContractResult, Severity
from util import log

PLUGIN_KEY = 'astrbot_plugin_livingmemory'
TARGET_REL = Path('core') / 'external_api.py'
ANCHOR = 'metadata["key_facts"] = key_facts'
MARKER = 'metadata["summary_schema_version"] = "v2"'

# 已知支持：版本 → 锚点行 sha256 前 16 位
KNOWN_VERSIONS = {
    '2.14.2': 'a313cf7344082cf2',
}

# 默认不自动改第三方源码；需要显式开启
AUTO_APPLY_DEFAULT = False


def anchor_hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


@dataclass
class PatchStatus:
    state: str  # ok | unpatched | unknown_version | anchor_missing | no_plugin
    version: str = ''
    file: str = ''
    detail: str = ''
    can_auto_apply: bool = False


def plugin_file() -> Path:
    return Path.home() / '.astrbot' / 'data' / 'plugins' / PLUGIN_KEY / TARGET_REL


def plugin_version() -> str:
    meta = Path.home() / '.astrbot' / 'data' / 'plugins' / PLUGIN_KEY / 'metadata.yaml'
    if not meta.exists():
        return ''
    try:
        for line in meta.read_text(encoding='utf-8', errors='ignore').splitlines():
            if line.strip().startswith('version:'):
                return line.split(':', 1)[1].strip().strip('"\'')
    except Exception:
        pass
    return ''


def status() -> PatchStatus:
    path = plugin_file()
    if not path.exists():
        return PatchStatus('no_plugin', detail='找不到插件文件')

    version = plugin_version()
    try:
        text = path.read_text(encoding='utf-8', errors='ignore')
    except Exception as e:
        return PatchStatus('no_plugin', version, str(path), f'读取失败: {e}')

    known = KNOWN_VERSIONS.get(version)
    if known is None:
        return PatchStatus(
            'unknown_version', version, str(path),
            f'插件版本 {version or "未知"} 不在已知列表，补丁需人工确认', False,
        )

    lines = text.splitlines()
    anchor_idx = next((i for i, l in enumerate(lines) if ANCHOR in l), -1)
    if anchor_idx == -1:
        return PatchStatus('anchor_missing', version, str(path), '锚点行不存在（插件结构已变）', False)

    current_hash = anchor_hash(lines[anchor_idx].strip())
    if current_hash != known:
        return PatchStatus(
            'anchor_mismatch', version, str(path),
            f'锚点哈希不匹配（期望 {known}，实际 {current_hash}），需人工确认', False,
        )
    idx = next((i for i, l in enumerate(lines) if ANCHOR in l), -1)
    if idx == -1:
        return PatchStatus('anchor_missing', version, str(path), '锚点行不存在（插件结构已变）', False)

    window = '\n'.join(lines[idx + 1: idx + 6])
    if MARKER in window:
        return PatchStatus('ok', version, str(path), '补丁在位', False)

    return PatchStatus(
        'unpatched', version, str(path),
        f'补丁缺失（锚点哈希 {anchor_hash(lines[idx].strip())}）', True,
    )


def apply() -> PatchStatus:
    """应用补丁；未知版本或哈希不匹配拒绝。"""
    st = status()
    if st.state == 'ok':
        return st
    if st.state != 'unpatched':
        log(f'补丁未应用：{st.detail}')
        return st
    if not st.can_auto_apply:
        return st

    path = Path(st.file)
    text = path.read_text(encoding='utf-8', errors='ignore')
    lines = text.splitlines(keepends=True)
    idx = next((i for i, l in enumerate(lines) if ANCHOR in l), -1)
    if idx == -1:
        return status()

    indent = lines[idx][: len(lines[idx]) - len(lines[idx].lstrip())]
    lines.insert(idx + 1, f'{indent}# [本地补丁] 让外部写入也走原子化\n')
    lines.insert(idx + 2, f'{indent}{MARKER}\n')

    stamp = time.strftime('%Y%m%d%H%M%S')
    backup = path.with_suffix(f'.py.bak_{stamp}')
    try:
        shutil.copy2(path, backup)
        path.write_text(''.join(lines), encoding='utf-8')
    except Exception as e:
        log(f'补丁写入失败，尝试回滚: {e}')
        try:
            shutil.copy2(backup, path)
        except Exception:
            pass
        return status()

    after = status()
    if after.state != 'ok':
        log(f'补丁自检失败，回滚: {after.detail}')
        try:
            shutil.copy2(backup, path)
        except Exception:
            pass
        return status()

    log(f'补丁已应用并自检通过（插件 {st.version}，备份 {backup.name}）')
    return after


def probe(auto_apply: bool = False) -> ContractResult:
    st = status()
    if st.state == 'ok':
        return ContractResult(
            name='livingmemory.patch', ok=True, severity=Severity.OK,
            source='plugin_file', version=st.version, detail='补丁在位',
        )

    if auto_apply and st.can_auto_apply:
        st = apply()
        if st.state == 'ok':
            return ContractResult(
                name='livingmemory.patch', ok=True, severity=Severity.OK,
                source='plugin_file', version=st.version, detail='补丁已自动重打',
            )

    severity = Severity.WARN if st.state == 'unpatched' else Severity.DEGRADED
    return ContractResult(
        name='livingmemory.patch', ok=False, severity=severity,
        source='plugin_file', version=st.version, detail=st.detail,
    )
