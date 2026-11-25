"""外部依赖适配器。

1a 只实现 AstrBotAdapter 的人格读取（契约 1）。
LivingMemoryAdapter 在 1b 加入。
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from contracts import ContractResult, Severity
from util import log


@dataclass
class PersonaRead:
    """一次人格读取的原始结果。"""

    text: str
    ok: bool
    error: str = ''
    sha256: str = ''


class AstrBotAdapter:
    """AstrBot 数据访问。当前只用读人格。"""

    name = 'astrbot'

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg

    # ---------------- 人格 ----------------
    def persona_db(self) -> Path:
        return Path(self.cfg.get('persona_db') or '')

    def read_persona(self) -> PersonaRead:
        """读人格。做三级结构检查：库 → 表 → 列 → 行。

        任何一步失败都返回 ok=False 并带明确原因，不抛异常。
        """
        db = self.persona_db()
        persona_id = self.cfg.get('persona_id') or 'dafeiyu'

        if not db.exists():
            return PersonaRead('', False, f'人格库不存在: {db}')

        try:
            con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=5)
        except Exception as e:
            return PersonaRead('', False, f'打不开人格库: {e}')

        try:
            tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
            if 'personas' not in tables:
                return PersonaRead('', False, 'personas 表不存在（AstrBot 结构可能已变）')

            cols = {r[1] for r in con.execute('PRAGMA table_info(personas)')}
            if 'system_prompt' not in cols:
                return PersonaRead('', False, 'personas.system_prompt 列不存在')

            row = con.execute(
                'select system_prompt from personas where persona_id = ?', (persona_id,)
            ).fetchone()
            if row is None:
                return PersonaRead('', False, f'找不到 persona_id={persona_id} 的人格行')

            text = (row[0] or '').strip()
            if not text:
                return PersonaRead('', False, '人格内容为空')

            return PersonaRead(text, True, '', hashlib.sha256(text.encode('utf-8')).hexdigest())
        except Exception as e:
            return PersonaRead('', False, f'读人格失败: {e}')
        finally:
            con.close()

    # ---------------- 契约检查 ----------------
    def probe_persona(self) -> ContractResult:
        """给 CompatMonitor / 启动握手用。"""
        read = self.read_persona()
        if read.ok:
            return ContractResult(
                name='astrbot.persona',
                ok=True,
                severity=Severity.OK,
                source='astrbot_db',
                sha256=read.sha256,
                detail=f'persona_id={self.cfg.get("persona_id")}',
            )
        return ContractResult(
            name='astrbot.persona',
            ok=False,
            severity=Severity.DEGRADED,
            source='astrbot_db',
            detail=read.error,
        )


class LivingMemoryAdapter:
    """LivingMemory 外部 API（契约 5）。"""

    name = 'livingmemory'

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg

    def _request(self, path: str, body: dict | None = None, method: str = 'POST', timeout: int = 10):
        import json
        import urllib.request

        url = (self.cfg.get('lm_base') or '').rstrip('/') + path
        data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                'Authorization': f"Bearer {self.cfg.get('lm_key') or ''}",
                'Content-Type': 'application/json',
            },
            method=method,
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8', 'ignore'))

    def health(self) -> dict:
        return self._request('/health', method='GET', timeout=5)

    def write(self, payload: dict) -> dict:
        return self._request('/memories', payload, timeout=45)

    def recall(self, query: str, k: int, scope: str | None = None) -> list:
        body = {'query': query, 'k': k, 'persona_id': self.cfg.get('persona_id') or 'dafeiyu'}
        if scope:
            body['scope'] = scope
        res = self._request('/recall', body, timeout=20)
        return res.get('items') or res.get('results') or []

    def probe(self) -> ContractResult:
        try:
            data = self.health()
            version = str(data.get('version') or data.get('plugin_version') or '')
            return ContractResult(
                name='livingmemory.api',
                ok=True,
                severity=Severity.OK,
                source='http',
                version=version,
                detail=str(data.get('status') or 'ok'),
            )
        except Exception as e:
            return ContractResult(
                name='livingmemory.api',
                ok=False,
                severity=Severity.DEGRADED,
                source='http',
                detail=f'记忆库不可达: {str(e)[:80]}',
            )
