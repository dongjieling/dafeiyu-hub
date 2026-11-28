"""MCP（stdio）服务：把记忆与人格暴露给 AstrBot / 其他 AI 客户端。

用法（由 MCP 客户端拉起）：
    python mcp_server.py

支持：initialize / tools/list / tools/call
工具：memory_recall / memory_list / persona_get / memory_health
只读，不提供写入或删除。
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import time
from pathlib import Path

# MCP 的 stdout 只能放协议 JSON；日志统一走 stderr
_PROTO = sys.stdout
sys.stdout = sys.stderr

sys.path.insert(0, str(Path(__file__).resolve().parent))

import clients as clients_mod  # noqa: E402
from adapters import LivingMemoryAdapter  # noqa: E402
from config import CFG  # noqa: E402
from memories import list_memories  # noqa: E402
from persona import read_persona  # noqa: E402

PROTOCOL_VERSION = '2024-11-05'

RESOURCES = [
    {'uri': 'persona://current', 'name': '当前人格', 'mimeType': 'text/plain'},
    {'uri': 'memory://recent', 'name': '最近记忆', 'mimeType': 'application/json'},
]

TOOLS = [
    {
        'name': 'memory_recall',
        'description': '按语义召回长期记忆；可限定日期范围（如「昨晚」就传昨天到今天）',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'query': {'type': 'string', 'description': '要查询的内容'},
                'limit': {'type': 'integer', 'default': 5},
                'date_from': {'type': 'string', 'description': '起始日期 YYYY-MM-DD（可选）'},
                'date_to': {'type': 'string', 'description': '结束日期 YYYY-MM-DD（可选）'},
            },
            'required': ['query'],
        },
    },
    {
        'name': 'memory_list',
        'description': '按关键词浏览记忆原文，可按来源、日期、重要度过滤',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'query': {'type': 'string'},
                'limit': {'type': 'integer', 'default': 20},
                'kind': {'type': 'string', 'enum': ['all', 'private', 'group', 'desktop']},
                'date_from': {'type': 'string', 'description': '起始日期 YYYY-MM-DD'},
                'date_to': {'type': 'string', 'description': '结束日期 YYYY-MM-DD'},
                'min_importance': {'type': 'number', 'default': 0},
            },
        },
    },
    {
        'name': 'persona_get',
        'description': '读取当前人格设定',
        'inputSchema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'memory_health',
        'description': '查看记忆库规模与写入队列状态',
        'inputSchema': {'type': 'object', 'properties': {}},
    },
]


def _date_ts(value: str, end: bool = False) -> float | None:
    if not value:
        return None
    try:
        d = datetime.datetime.strptime(str(value)[:10], '%Y-%m-%d')
        if end:
            d = d + datetime.timedelta(days=1) - datetime.timedelta(seconds=1)
        return d.timestamp()
    except Exception:
        return None


def active_cfg() -> dict:
    """MCP 也遵守客户端配置：HUB_CLIENT 指定 profile 时用它的 scope/人格。"""
    cid = (os.environ.get('HUB_CLIENT') or '').strip()
    if cid:
        client = clients_mod.get_client(cid)
        if client:
            return clients_mod.effective_cfg(CFG, client)
    return CFG


def call_tool(name: str, args: dict) -> dict:
    cfg = active_cfg()
    if name == 'memory_recall':
        query = str(args.get('query') or '')
        limit = int(args.get('limit') or 5)
        start = _date_ts(str(args.get('date_from') or ''))
        end = _date_ts(str(args.get('date_to') or ''), end=True)
        if start is not None or end is not None:
            import memory as memory_mod

            start = start if start is not None else 0.0
            end = end if end is not None else time.time()
            items = memory_mod.range_memories(cfg, start, end, query=query, limit=limit)
            texts = [memory_mod._fmt_memory(it) for it in items]
            return {'content': [{'type': 'text', 'text': json.dumps(texts, ensure_ascii=False)}]}
        items = LivingMemoryAdapter(cfg).recall(
            query, limit, cfg.get('write_scope') or None
        )
        return {'content': [{'type': 'text', 'text': json.dumps(items, ensure_ascii=False)}]}

    if name == 'memory_list':
        res = list_memories(
            cfg,
            str(args.get('query') or ''),
            int(args.get('limit') or 20),
            kind=str(args.get('kind') or 'all'),
            date_from=str(args.get('date_from') or ''),
            date_to=str(args.get('date_to') or ''),
            min_importance=float(args.get('min_importance') or 0),
        )
        return {'content': [{'type': 'text', 'text': json.dumps(res, ensure_ascii=False)}]}

    if name == 'persona_get':
        return {'content': [{'type': 'text', 'text': read_persona(cfg)}]}

    if name == 'memory_health':
        from hub import memory_health

        return {'content': [{'type': 'text', 'text': json.dumps(memory_health(cfg), ensure_ascii=False)}]}

    raise ValueError(f'未知工具: {name}')


def read_resource(uri: str) -> dict:
    cfg = active_cfg()
    if uri == 'persona://current':
        text = read_persona(cfg)
        return {'contents': [{'uri': uri, 'mimeType': 'text/plain', 'text': text}]}
    if uri == 'memory://recent':
        res = list_memories(cfg, '', 10)
        return {'contents': [{'uri': uri, 'mimeType': 'application/json',
                              'text': json.dumps(res, ensure_ascii=False)}]}
    raise ValueError(f'未知资源: {uri}')


def handle(msg: dict) -> dict | None:
    method = msg.get('method') or ''
    mid = msg.get('id')
    if method == 'initialize':
        return {
            'jsonrpc': '2.0',
            'id': mid,
            'result': {
                'protocolVersion': PROTOCOL_VERSION,
                'capabilities': {'tools': {}, 'resources': {}},
                'serverInfo': {'name': 'dafeiyu-memory', 'version': '1.3.0'},
            },
        }
    if method in ('notifications/initialized', 'initialized'):
        return None
    if method == 'tools/list':
        return {'jsonrpc': '2.0', 'id': mid, 'result': {'tools': TOOLS}}
    if method == 'resources/list':
        return {'jsonrpc': '2.0', 'id': mid, 'result': {'resources': RESOURCES}}
    if method == 'resources/read':
        uri = (msg.get('params') or {}).get('uri') or ''
        try:
            return {'jsonrpc': '2.0', 'id': mid, 'result': read_resource(uri)}
        except Exception as e:
            return {'jsonrpc': '2.0', 'id': mid,
                    'error': {'code': -32602, 'message': str(e)}}
    if method == 'tools/call':
        params = msg.get('params') or {}
        try:
            result = call_tool(params.get('name') or '', params.get('arguments') or {})
            return {'jsonrpc': '2.0', 'id': mid, 'result': result}
        except Exception as e:
            return {
                'jsonrpc': '2.0',
                'id': mid,
                'result': {'content': [{'type': 'text', 'text': f'错误: {e}'}], 'isError': True},
            }
    return {
        'jsonrpc': '2.0',
        'id': mid,
        'error': {'code': -32601, 'message': f'未知方法: {method}'},
    }


def _write(obj: dict) -> None:
    _PROTO.write(json.dumps(obj, ensure_ascii=False) + '\n')
    _PROTO.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            _write({'jsonrpc': '2.0', 'id': None,
                    'error': {'code': -32700, 'message': 'JSON 解析失败'}})
            continue
        out = handle(msg)
        if out is not None:
            _write(out)


if __name__ == '__main__':
    main()
