"""本地管理接口鉴权：一个随机 token，只给桌面 UI 用。"""

from __future__ import annotations

import hmac
import secrets
from pathlib import Path

from config import DATA_DIR

TOKEN_NAME = 'admin.token'


def token_path() -> Path:
    return DATA_DIR / TOKEN_NAME


def ensure() -> str:
    path = token_path()
    if path.exists():
        token = path.read_text(encoding='utf-8').strip()
        if token:
            return token
    token = secrets.token_hex(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding='utf-8')
    return token


def check(value: str | None) -> bool:
    token = ensure()
    return bool(value) and hmac.compare_digest(str(value), token)
