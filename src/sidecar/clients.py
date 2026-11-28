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

import json
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
        return _default()
    try:
        data = json.loads(PATH.read_text(encoding='utf-8'))
        data.setdefault('auth_mode', 'off')
        data.setdefault('clients', [])
        return data
    except Exception as e:
        log(f'客户端配置读取失败: {str(e)[:80]}')
        return _default()


def save(data: dict) -> dict:
    if PATH.exists():
        try:
            PATH.with_name(f'clients.json.bak_{time.strftime("%Y%m%d%H%M%S")}').write_bytes(PATH.read_bytes())
        except Exception:
            pass
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return data


def _num(value, default: float, lo: float = -1, hi: float = 2) -> float:
    try:
        n = float(value)
    except Exception:
        return default
    return max(lo, min(hi, n))


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


def get_client(cid: str) -> dict | None:
    cid = (cid or '').strip().lower()
    for c in load().get('clients') or []:
        if c.get('id') == cid and c.get('enabled', True):
            return c
    return None


def default_client() -> dict:
    return {
        'id': 'default',
        'name': '默认',
        'token': '',
        'scope': (CFG.get('write_scope') or CFG.get('scope') or ''),
        'persona_id': CFG.get('persona_id') or '',
        'model': '',
        'temperature': -1,
        'recall_k': 0,
        'allow_write': False,
        'enabled': True,
    }


def authenticate(headers) -> tuple[dict | None, str]:
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
    if match is None and cid:
        for c in data.get('clients') or []:
            if c.get('id') == cid and c.get('enabled', True):
                match = c
                break

    if match is not None:
        return match, ''
    if mode == 'required':
        return None, '需要客户端 token'
    if mode == 'optional' and (token or cid):
        return None, '客户端 token 或 id 无效'
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
