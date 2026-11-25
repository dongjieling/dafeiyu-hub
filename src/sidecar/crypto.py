"""归档库字段加密：AES-256-GCM + Windows DPAPI 保管密钥。

设计：
- 只加密敏感字段（消息内容、提炼文本），不加密结构字段
- 密钥随机 32 字节，用 DPAPI 绑定当前 Windows 账户后落盘
- 密文格式：enc:v1:<base64(nonce||ciphertext)>
- 兼容旧明文：读到不带前缀的值原样返回（迁移脚本负责加密历史）
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from config import DATA_DIR
from util import log

PREFIX = 'enc:v1:'
KEY_PATH = DATA_DIR / 'archive.key'
_key_cache: bytes | None = None


def _dpapi_protect(data: bytes) -> bytes:
    import win32crypt

    blob = win32crypt.CryptProtectData(data, 'dafeiyu-archive-key', None, None, None, 0)
    return blob


def _dpapi_unprotect(blob: bytes) -> bytes:
    import win32crypt

    _desc, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    return data


def _load_or_create_key() -> bytes:
    global _key_cache
    if _key_cache is not None:
        return _key_cache

    if KEY_PATH.exists():
        blob = KEY_PATH.read_bytes()
        try:
            _key_cache = _dpapi_unprotect(blob)
            return _key_cache
        except Exception as e:
            log(f'归档密钥解密失败（可能换了 Windows 账户）: {e}')
            raise

    key = os.urandom(32)
    try:
        wrapped = _dpapi_protect(key)
        KEY_PATH.write_bytes(wrapped)
        log(f'已生成归档密钥（DPAPI 保护）: {KEY_PATH}')
    except Exception as e:
        # 没有 DPAPI 时的降级：明文存密钥，但明确告警
        KEY_PATH.write_bytes(key)
        log(f'无法使用 DPAPI，密钥以明文存放（不安全）: {e}')
    _key_cache = key
    return key


def encrypt(text: str) -> str:
    if not text:
        return text
    if text.startswith(PREFIX):
        return text
    aead = AESGCM(_load_or_create_key())
    nonce = os.urandom(12)
    ct = aead.encrypt(nonce, text.encode('utf-8'), None)
    return PREFIX + base64.b64encode(nonce + ct).decode('ascii')


def decrypt(value: str) -> str:
    if not value or not value.startswith(PREFIX):
        return value
    raw = base64.b64decode(value[len(PREFIX):])
    aead = AESGCM(_load_or_create_key())
    return aead.decrypt(raw[:12], raw[12:], None).decode('utf-8')


def is_encrypted(value: str) -> bool:
    return bool(value) and value.startswith(PREFIX)


def key_info() -> dict:
    return {
        'key_file': str(KEY_PATH),
        'exists': KEY_PATH.exists(),
        'protected_by_dpapi': KEY_PATH.exists() and KEY_PATH.stat().st_size > 60,
    }


def _cli() -> int:
    """命令行：导出 / 导入归档密钥。

    python crypto.py --export D:\\archive.key.bak
    python crypto.py --import D:\\archive.key.bak
    python crypto.py --info
    """
    import sys

    args = sys.argv[1:]
    if not args or args[0] == '--info':
        info = key_info()
        print('密钥文件:', info['key_file'])
        print('是否存在:', info['exists'])
        print('DPAPI 保护:', info['protected_by_dpapi'])
        return 0

    if args[0] == '--export' and len(args) >= 2:
        target = Path(args[1])
        key = _load_or_create_key()
        target.write_bytes(key)
        try:
            target.chmod(0o600)
        except Exception:
            pass
        print(f'已导出密钥到 {target}')
        print('⚠️ 该文件未加密，请离线保存，不要放网盘或与归档库同目录')
        return 0

    if args[0] == '--import' and len(args) >= 2:
        source = Path(args[1])
        if not source.exists():
            print('文件不存在:', source)
            return 1
        raw = source.read_bytes()
        if len(raw) != 32:
            print('密钥长度不对（应为 32 字节）')
            return 2
        if KEY_PATH.exists():
            print('⚠️ 已存在密钥，继续会覆盖（旧数据将无法解密）')
            if '--force' not in args:
                print('如确认覆盖，加 --force')
                return 3
        KEY_PATH.write_bytes(_dpapi_protect(raw))
        print('已导入密钥并重新用 DPAPI 保护')
        return 0

    print('用法: python crypto.py [--info | --export <路径> | --import <路径> [--force]]')
    return 1


if __name__ == '__main__':
    raise SystemExit(_cli())
