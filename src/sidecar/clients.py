"""客户端 profile 与鉴权。

存储：数据目录 clients.json
{
  "auth_mode": "off" | "optional" | "required",
  "clients": [
    {"id": "airi", "name": "AIRI", "token": "", "scope": "airi:desktop",
     "persona_id": "", "model": "", "enabled": true}
  ]
}
"""

from __future__ import annotations

from dataclasses import dataclass

import json
import os
import re
import time
from pathlib import Path

from config import CFG, DATA_DIR
from util import log

PATH = DATA_DIR / 'clients.json'
ID_RE = re.compile(r'^[a-z0-9_-]{1,32}$')


def _default() -> dict:
    return {'auth_mode': 'off', 'clients': []}


def load() -> dict:
    if not PATH.exists():
        backups = sorted(PATH.parent.glob(PATH.name + '.bak_*'),
                         key=lambda p: p.stat().st_mtime, reverse=True)[:3]
        for bak in backups:
            try:
                data = json.loads(bak.read_text(encoding='utf-8'))
                log(f'客户端配置缺失，已从备份恢复: {bak.name}')
                return _normalize(data)
            except Exception:
                continue
        return _default()
    try:
        data = json.loads(PATH.read_text(encoding='utf-8'))
        return _normalize(data)
    except Exception as e:
        log(f'客户端配置读取失败，尝试备份: {str(e)[:80]}')
    # 回退最近一份备份
    for bak in sorted(PATH.parent.glob(PATH.name + '.bak_*'), key=lambda p: p.stat().st_mtime, reverse=True)[:3]:
        try:
            data = json.loads(bak.read_text(encoding='utf-8'))
            log(f'客户端配置已从备份恢复: {bak.name}')
            return _normalize(data)
        except Exception:
            continue
    # fail-closed：坏文件不静默变成 off
    log('客户端配置无法解析，鉴权临时收紧为 required')
    return {'auth_mode': 'required', 'clients': [], 'error': 'clients.json 损坏'}


def _normalize(data: dict) -> dict:
    if not isinstance(data, dict):
        return {'auth_mode': 'required', 'clients': [], 'error': '格式错误'}
    mode = str(data.get('auth_mode') or 'off')
    if mode not in ('off', 'optional', 'required'):
        mode = 'required'
    clients = data.get('clients') if isinstance(data.get('clients'), list) else []
    for c in clients:
        c.setdefault('temperature', -1)
        c.setdefault('recall_k', 0)
        c.setdefault('allow_write', False)
        c.setdefault('enabled', True)
    return {'auth_mode': mode, 'clients': clients}


def save(data: dict) -> dict:
    if PATH.exists():
        try:
            PATH.with_name(f'clients.json.bak_{time.strftime("%Y%m%d%H%M%S")}').write_bytes(PATH.read_bytes())
        except Exception:
            pass
        try:
            for old in sorted(PATH.parent.glob(PATH.name + '.bak_*'),
                              key=lambda p: p.stat().st_mtime, reverse=True)[5:]:
                old.unlink(missing_ok=True)
        except Exception:
            pass
    tmp = PATH.with_name(PATH.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, PATH)
    return data


def _num(value, default: float, lo: float = -1, hi: float = 2) -> float:
    try:
        n = float(value)
    except Exception:
        return default
    return max(lo, min(hi, n))


def restore_masked(data: dict) -> dict:
    """UI 回传的掩码 token（••••xxxx）用原值替换。"""
    old = {c.get('id'): c for c in load().get('clients') or []}
    for c in data.get('clients') or []:
        token = str(c.get('token') or '')
        if token.startswith('••••'):
            prev = old.get(str(c.get('id') or '').lower()) or {}
            c['token'] = prev.get('token') or ''
    return data


def validate(data: dict) -> tuple[dict, str]:
    mode = str(data.get('auth_mode') or 'off')
    if mode not in ('off', 'optional', 'required'):
        return data, 'auth_mode 只能是 off / optional / required'
    seen = set()
    tokens = set()
    clients = []
    for raw in data.get('clients') or []:
        cid = str(raw.get('id') or '').strip().lower()
        if not ID_RE.match(cid):
            return data, f'客户端 id 不合法: {cid or "(空)"}'
        if cid in seen:
            return data, f'客户端 id 重复: {cid}'
        seen.add(cid)
        token = str(raw.get('token') or '').strip()
        if token:
            if token in tokens:
                return data, f'客户端 token 重复: {cid}'
            tokens.add(token)
        clients.append({
            'id': cid,
            'name': str(raw.get('name') or cid)[:40],
            'token': token,
            'scope': str(raw.get('scope') or '').strip()[:120],
            'persona_id': str(raw.get('persona_id') or '').strip()[:60],
            'model': str(raw.get('model') or '').strip()[:60],
            'temperature': _num(raw.get('temperature'), -1, -1, 2),
            'recall_k': int(_num(raw.get('recall_k'), 0, 0, 20)),
            'allow_write': bool(raw.get('allow_write', False)),
            'enabled': bool(raw.get('enabled', True)),
        })
    return {'auth_mode': mode, 'clients': clients}, ''


@dataclass(frozen=True)
class ClientContext:
    """一次请求的客户端上下文；cfg 仍是兼容层，但边界从这里开始。"""

    id: str = 'default'
    name: str = '默认'
    token: str = ''
    scope: str = ''
    persona_id: str = ''
    model: str = ''
    temperature: float = -1.0
    recall_k: int = 0
    allow_write: bool = False
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> 'ClientContext':
        return cls(
            id=str(data.get('id') or 'default'),
            name=str(data.get('name') or '默认'),
            token=str(data.get('token') or ''),
            scope=str(data.get('scope') or ''),
            persona_id=str(data.get('persona_id') or ''),
            model=str(data.get('model') or ''),
            temperature=float(data.get('temperature', -1) if data.get('temperature') is not None else -1),
            recall_k=int(data.get('recall_k') or 0),
            allow_write=bool(data.get('allow_write', False)),
            enabled=bool(data.get('enabled', True)),
        )

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def __getitem__(self, key: str):
        return getattr(self, key)

    def to_dict(self) -> dict:
        return {
            'id': self.id, 'name': self.name, 'token': self.token, 'scope': self.scope,
            'persona_id': self.persona_id, 'model': self.model,
            'temperature': self.temperature, 'recall_k': self.recall_k,
            'allow_write': self.allow_write, 'enabled': self.enabled,
        }


def get_client(cid: str) -> dict | None:
    cid = (cid or '').strip().lower()
    for c in load().get('clients') or []:
        if c.get('id') == cid and c.get('enabled', True):
            return c
    return None


def default_client() -> ClientContext:
    return ClientContext(
        id='default',
        name='默认',
        scope=(CFG.get('write_scope') or CFG.get('scope') or ''),
        persona_id=CFG.get('persona_id') or '',
    )


def authenticate(headers) -> tuple['ClientContext | None', str]:
    """根据请求头识别客户端。返回 (client, error)。"""
    data = load()
    mode = data.get('auth_mode') or 'off'
    auth = (headers.get('Authorization') or '').strip()
    token = auth[7:].strip() if auth.lower().startswith('bearer ') else ''
    cid = (headers.get('X-Hub-Client') or '').strip().lower()

    match = None
    if token:
        for c in data.get('clients') or []:
            if c.get('token') and c['token'] == token and c.get('enabled', True):
                match = c
                break
    if match is not None:
        return ClientContext.from_dict(match), ''

    # 只有 off 模式允许用 X-Hub-Client 免 token；required/optional 一律要 token
    if mode == 'off' and cid:
        for c in data.get('clients') or []:
            if c.get('id') == cid and c.get('enabled', True):
                return ClientContext.from_dict(c), ''

    if mode == 'required':
        return None, '需要客户端 token'
    if mode == 'optional' and token:
        return None, '客户端 token 无效'
    return default_client(), ''


def effective_cfg(base: dict, client: dict) -> dict:
    cfg = dict(base)
    scope = (client.get('scope') or '').strip()
    if scope:
        cfg['write_scope'] = scope
        cfg['scope'] = scope
    persona = (client.get('persona_id') or '').strip()
    if persona:
        cfg['persona_id'] = persona
    cfg['client_id'] = client.get('id') or 'default'
    cfg['client_name'] = client.get('name') or '默认'
    cfg['allow_write'] = bool(client.get('allow_write', False))
    temp = client.get('temperature')
    if temp is not None and float(temp) >= 0:
        cfg['temperature'] = float(temp)
    recall_k = int(client.get('recall_k') or 0)
    if recall_k:
        cfg['recall_k'] = recall_k
    return cfg


def mask(clients_data: dict) -> dict:
    out = json.loads(json.dumps(clients_data))
    for c in out.get('clients') or []:
        if c.get('token'):
            c['token'] = '••••' + c['token'][-4:]
    return out
