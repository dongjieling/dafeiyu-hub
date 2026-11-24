"""通用工具：日志（带轮转）、JWT 签名、安全 JSON 解析。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from pathlib import Path

_log_lock = threading.Lock()
_LOG_PATH: Path | None = None
_MAX_LOG_BYTES = 5 * 1024 * 1024


def init_log(path: Path) -> None:
    global _LOG_PATH
    _LOG_PATH = path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        _LOG_PATH = None


def _rotate_if_needed(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > _MAX_LOG_BYTES:
            backup = path.with_suffix(path.suffix + '.1')
            if backup.exists():
                backup.unlink()
            path.rename(backup)
    except Exception:
        pass


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    with _log_lock:
        if _LOG_PATH is not None:
            try:
                _rotate_if_needed(_LOG_PATH)
                with open(_LOG_PATH, 'a', encoding='utf-8') as f:
                    f.write(line + '\n')
            except Exception:
                pass
    try:
        print(line, flush=True)
    except Exception:
        pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def sign_jwt(secret: str, username: str, ttl: int = 300) -> str:
    """HS256 签名，不依赖 pyjwt。"""
    header = _b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}, separators=(',', ':')).encode())
    payload = _b64(
        json.dumps({'username': username, 'exp': int(time.time()) + ttl}, separators=(',', ':')).encode()
    )
    signing_input = f'{header}.{payload}'.encode('ascii')
    sig = hmac.new(secret.encode('utf-8'), signing_input, hashlib.sha256).digest()
    return f'{header}.{payload}.{_b64(sig)}'


def safe_json(raw: bytes, default=None):
    try:
        return json.loads(raw.decode('utf-8'))
    except Exception:
        return default


def truncate(text: str, limit: int) -> str:
    text = (text or '').strip()
    return text if len(text) <= limit else text[:limit] + '…'
