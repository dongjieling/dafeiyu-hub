"""知识库：通过 AstrBot 面板接口检索文档片段。"""

from __future__ import annotations

import json
import time
import urllib.request

from util import log, sign_jwt

_cache = {'key': '', 'items': [], 'ts': 0.0}


def retrieve(cfg: dict, query: str) -> list[str]:
    kb = cfg.get('knowledge') or {}
    if not kb.get('enabled'):
        return []
    kb_names = kb.get('kb_names') or []
    secret = kb.get('jwt_secret') or ''
    if not kb_names or not secret or not (query or '').strip():
        return []

    now = time.time()
    ttl = float(kb.get('cache_seconds') or 60)
    if _cache['items'] and _cache['key'] == query and now - _cache['ts'] < ttl:
        return _cache['items']

    token = sign_jwt(secret, kb.get('username') or '', ttl=300)
    body = {'query': query[:300], 'kb_names': kb_names, 'top_k': int(kb.get('top_k') or 3)}
    # 路径里的 kb_id 服务端未使用，实际取 body 的 kb_names
    url = f"{kb.get('base_url') or 'http://127.0.0.1:6185/api/v1'}/knowledge-bases/_/retrieve"
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=float(kb.get('timeout') or 8)) as r:
            data = json.loads(r.read().decode('utf-8', 'ignore'))
    except Exception as e:
        log(f'知识库检索失败: {str(e)[:100]}')
        return []

    payload = data.get('data') if isinstance(data, dict) else None
    results = (payload or {}).get('results') or []
    out = []
    for it in results[: int(kb.get('top_k') or 3)]:
        text = (it.get('text') or it.get('content') or '').strip()
        if text:
            out.append(text[:400])

    _cache.update({'key': query, 'items': out, 'ts': now})
    if out:
        allow = bool(((cfg.get('logging') or {}).get('log_content')) is True)
        if allow:
            log(f'知识库命中 {len(out)} 条: ' + ' | '.join(x[:32].replace('\n', ' ') for x in out))
        else:
            log(f'知识库命中 {len(out)} 条（内容不记录）')
    return out


def clear_cache() -> None:
    _cache.update({'key': '', 'items': [], 'ts': 0.0})
