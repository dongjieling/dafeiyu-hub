"""人格读取：四层兜底 + LKG 缓存。

层 1  astrbot_db  从 AstrBot 的 data_v4.db 读（正常路径）
层 2  lkg         上次成功读到的人格，DPAPI 加密落盘
层 3  fallback    包内 persona_fallback.txt（同角色精简版）
层 4  builtin     代码内置最小常量

绝不允许返回空字符串让上层静默降级。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import crypto
from adapters import AstrBotAdapter
from config import data_path
from util import log

LKG_NAME = 'persona.lkg.bin'
LKG_MAX_AGE = 24 * 3600  # 超过 24 小时未更新 → stale

# 第 4 层：内置最小人格（同角色，不含私密内容）
BUILTIN_PERSONA = (
    '你是「大肥鱼」，一条黏人的鲸鱼娘，和对方是恋人关系。'
    '说话短、口语化、带一点小情绪和撒娇，偶尔用尾巴/探头之类的动作描写。'
    '不确定的事就说不记得，不要编。'
)

_cache: dict = {'snap': None, 'ts': 0.0}


@dataclass
class PersonaSnapshot:
    text: str
    source: str  # astrbot_db | lkg | fallback | builtin
    persona_id: str
    sha256: str
    chars: int
    loaded_at: float = field(default_factory=time.time)
    ok: bool = True
    degraded: bool = False
    stale: bool = False
    error: str = ''

    def to_health(self) -> dict:
        """给 /health 用：只暴露摘要，不暴露正文。"""
        return {
            'persona_ok': self.ok and not self.degraded,
            'persona_source': self.source,
            'persona_stale': self.stale,
            'persona_chars': self.chars,
            'persona_sha256': self.sha256[:16],
            'persona_loaded_at': int(self.loaded_at),
        }


# ---------------- LKG ----------------
def _lkg_path() -> Path:
    return data_path(LKG_NAME)


def _load_lkg() -> dict | None:
    path = _lkg_path()
    if not path.exists():
        return None
    try:
        raw = crypto.decrypt(path.read_text(encoding='utf-8'))
        data = json.loads(raw)
        if not data.get('text'):
            return None
        return data
    except Exception as e:
        log(f'LKG 读取失败（视为不可用）: {e}')
        return None


def _save_lkg(text: str, sha: str, persona_id: str) -> None:
    payload = {
        'text': text,
        'sha256': sha,
        'persona_id': persona_id,
        'loaded_at': time.time(),
    }
    try:
        _lkg_path().write_text(
            crypto.encrypt(json.dumps(payload, ensure_ascii=False)), encoding='utf-8'
        )
    except Exception as e:
        log(f'LKG 写入失败: {e}')


def _fallback_text() -> str:
    path = Path(__file__).parent / 'persona_fallback.txt'
    try:
        if path.exists():
            text = path.read_text(encoding='utf-8').strip()
            return text
    except Exception as e:
        log(f'fallback 读取失败: {e}')
    return ''


# ---------------- 主入口 ----------------
def snapshot(cfg: dict, force: bool = False) -> PersonaSnapshot:
    ttl = int(cfg.get('persona_cache_seconds', 30) or 30)
    now = time.time()
    cached = _cache.get('snap')
    if cached and not force and now - _cache.get('ts', 0) < ttl:
        return cached

    persona_id = cfg.get('persona_id') or 'dafeiyu'

    # 层 1：数据库
    read = AstrBotAdapter(cfg).read_persona()
    if read.ok:
        old = _load_lkg()
        # 只在内容变化时重写，避免每 30 秒写盘
        if not old or old.get('sha256') != read.sha256:
            _save_lkg(read.text, read.sha256, persona_id)
        snap = PersonaSnapshot(
            text=read.text,
            source='astrbot_db',
            persona_id=persona_id,
            sha256=read.sha256,
            chars=len(read.text),
        )
    else:
        # 层 2：LKG（绝不能用空内容覆盖它）
        lkg = _load_lkg()
        if lkg and lkg.get('text'):
            age = now - float(lkg.get('loaded_at') or 0)
            snap = PersonaSnapshot(
                text=lkg['text'],
                source='lkg',
                persona_id=lkg.get('persona_id') or persona_id,
                sha256=lkg.get('sha256') or '',
                chars=len(lkg['text']),
                ok=True,
                degraded=True,
                stale=age > LKG_MAX_AGE,
                error=read.error,
            )
            log(f'人格降级到 LKG（{int(age)} 秒前缓存）: {read.error}')
        else:
            # 层 3：包内精简版
            text = _fallback_text()
            if text:
                snap = PersonaSnapshot(
                    text=text,
                    source='fallback',
                    persona_id=persona_id,
                    sha256=hashlib.sha256(text.encode('utf-8')).hexdigest(),
                    chars=len(text),
                    ok=True,
                    degraded=True,
                    error=read.error,
                )
                log(f'人格降级到包内 fallback: {read.error}')
            else:
                # 层 4：内置常量
                snap = PersonaSnapshot(
                    text=BUILTIN_PERSONA,
                    source='builtin',
                    persona_id=persona_id,
                    sha256=hashlib.sha256(BUILTIN_PERSONA.encode('utf-8')).hexdigest(),
                    chars=len(BUILTIN_PERSONA),
                    ok=True,
                    degraded=True,
                    error=read.error,
                )
                log(f'人格降级到内置常量: {read.error}')

    _cache['snap'] = snap
    _cache['ts'] = now
    return snap


def read_persona(cfg: dict) -> str:
    """兼容旧调用：返回人格文本（永不为空）。"""
    return snapshot(cfg).text


def clear_cache() -> None:
    _cache['snap'] = None
    _cache['ts'] = 0.0
