"""把中转站的 MCP 服务注册到 AstrBot（stdio）。

只改 AstrBot 的 mcp_server.json，保留已有服务器；写入前备份。
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

from config import DATA_DIR
from util import log

NAME = 'dafeiyu-memory'


def astrbot_data_dir(cfg: dict) -> Path:
    explicit = (cfg.get('astrbot_data_dir') or '').strip()
    if explicit:
        return Path(explicit)
    raw = (cfg.get('astrbot_config') or '').strip()
    if raw:
        return Path(raw).parent
    return Path.home() / '.astrbot' / 'data'


def status(cfg: dict) -> dict:
    target = astrbot_data_dir(cfg) / 'mcp_server.json'
    if not target.exists():
        return {'ok': False, 'path': str(target), 'registered': False, 'error': 'AstrBot MCP 配置不存在'}
    try:
        data = json.loads(target.read_text(encoding='utf-8'))
        servers = data.get('mcpServers') or {}
        return {'ok': True, 'path': str(target), 'registered': NAME in servers,
                'servers': sorted(servers.keys())}
    except Exception as e:
        return {'ok': False, 'path': str(target), 'registered': False, 'error': str(e)[:80]}


def register(cfg: dict) -> dict:
    target = astrbot_data_dir(cfg) / 'mcp_server.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = ''
    if target.exists():
        backup = str(target.with_name(f'mcp_server.json.bak_{time.strftime("%Y%m%d%H%M%S")}'))
        shutil.copy2(target, backup)

    data = {'mcpServers': {}}
    if target.exists():
        try:
            data = json.loads(target.read_text(encoding='utf-8'))
        except Exception:
            pass
    servers = data.setdefault('mcpServers', {})

    entry = {
        'command': sys.executable,
        'args': [str(Path(__file__).resolve().parent / 'mcp_server.py')],
        'env': {'HUB_DATA_DIR': str(DATA_DIR)},
        'active': True,
    }
    servers[NAME] = entry
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    log(f'已注册 MCP 服务到 AstrBot: {target}')
    return {'ok': True, 'path': str(target), 'backup': backup, 'servers': sorted(servers.keys())}
