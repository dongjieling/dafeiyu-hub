"""配置：唯一来源。

优先级：环境变量 > config.json > 默认值
只暴露一个 CFG 对象，其他模块不要自己读文件。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / 'config.json'

# 数据目录（打包后 sidecar 目录可能只读，由宿主通过 HUB_DATA_DIR 指定可写目录）
_default_data = BASE_DIR
try:
    _env_data = os.environ.get('HUB_DATA_DIR')
    DATA_DIR = Path(_env_data) if _env_data else _default_data
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    DATA_DIR = _default_data

LOG_PATH = DATA_DIR / 'hub.log'

DEFAULTS = {
    'port': 8787,
    'persona_id': 'dafeiyu',
    'persona_db': '',
    'astrbot_config': '',
    'persona_cache_seconds': 30,
    'upstream_source': 'deepseek',
    'upstream_base': 'https://api.deepseek.com/v1',
    'upstream_model': 'deepseek-chat',
    'upstream_timeout': 180,
    'lm_base': 'http://127.0.0.1:7801/page/external',
    'lm_key': '',
    'lm_timeout': 6,
    'recall_k': 8,
    'write_scope': '',
    'scope': 'airi:desktop',
    'extract': {
        'enabled': True,
        'model': 'deepseek-chat',
        'min_chars': 6,
        'min_importance': 0.3,
        'max_per_turn': 2,
        'timeout': 45,
    },
    'archive': {'enabled': True, 'db_path': 'archive.db'},
    'knowledge': {
        'enabled': False,
        'base_url': 'http://127.0.0.1:6185/api/v1',
        'username': '',
        'jwt_secret': '',
        'kb_names': [],
        'top_k': 3,
        'timeout': 8,
        'cache_seconds': 60,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_file() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {}


def load() -> dict:
    cfg = _deep_merge(DEFAULTS, _load_file())
    # 环境变量覆盖（宿主进程传入，避免两处配置打架）
    if os.environ.get('HUB_PORT'):
        try:
            cfg['port'] = int(os.environ['HUB_PORT'])
        except ValueError:
            pass
    if os.environ.get('HUB_LM_KEY'):
        cfg['lm_key'] = os.environ['HUB_LM_KEY']
    return cfg


CFG = load()


def data_path(name: str) -> Path:
    return DATA_DIR / name
