"""最小的 MCP（stdio）服务：把记忆与人格暴露给其他 AI 客户端。

用法（由 MCP 客户端拉起）：
    python mcp_server.py

支持的方法：
    initialize
    tools/list
    tools/call  ->  memory_recall / memory_list / persona_get / memory_health

只读，不提供写入或删除。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from adapters import LivingMemoryAdapter  # noqa: E402
from config import CFG  # noqa: E402
from memories import list_memories  # noqa: E402
from persona import read_persona  # noqa: E402

PROTOCOL_VERSION = '2024-11-05'

TOOLS = [
    {
        'name': 'memory_recall',
        'description': '按语义召回与查询最相关的长期记忆',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'query': {'type': 'string', 'description': '要查询的内容'},
                'limit': {'type': 'integer', 'default': 5},
            },
            'required': ['query'],
        },
    },
    {
        'name': 'memory_list',
        'description': '按关键词浏览记忆原文（最新在前）',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'query': {'type': 'string'},
                'limit': {'type': 'integer', 'default': 20},
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


def call_tool(name: str, args: dict) -> dict:
    if name == 'memory_recall':
        items = LivingMemoryAdapter(CFG).recall(
            str(args.get('query') or ''), int(args.get('limit') or 5), CFG.get('write_scope') or None
        )
        return {'content': [{'type': 'text', 'text': json.dumps(items, ensure_ascii=False)}]}
    if name == 'memory_list':
        res = list_memories(CFG, str(args.get('query') or ''), int(args.get('limit') or 20))
        return {'content': [{'type': 'text', 'text': json.dumps(res, ensure_ascii=False)}]}
    if name == 'persona_get':
        text = read_persona(CFG)
        return {'content': [{'type': 'text', 'text': text}]}
    if name == 'memory_health':
        from hub import memory_health

        return {'content': [{'type': 'text', 'text': json.dumps(memory_health(CFG), ensure_ascii=False)}]}
    raise ValueError(f'未知工具: {name}')


def handle(msg: dict) -> dict | None:
    method = msg.get('method') or ''
    mid = msg.get('id')
    if method == 'initialize':
        return {
            'jsonrpc': '2.0',
            'id': mid,
            'result': {
                'protocolVersion': PROTOCOL_VERSION,
                'capabilities': {'tools': {}},
                'serverInfo': {'name': 'dafeiyu-memory', 'version': '1.2.0'},
            },
        }
    if method in ('notifications/initialized', 'initialized'):
        return None
    if method == 'tools/list':
        return {'jsonrpc': '2.0', 'id': mid, 'result': {'tools': TOOLS}}
    if method == 'tools/call':
        params = msg.get('params') or {}
        name = params.get('name') or ''
        args = params.get('arguments') or {}
        try:
            result = call_tool(name, args)
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


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            print(json.dumps({'jsonrpc': '2.0', 'id': None,
                              'error': {'code': -32700, 'message': 'JSON 解析失败'}}, ensure_ascii=False), flush=True)
            continue
        out = handle(msg)
        if out is not None:
            print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
