"""三期：端到端契约测试。

假 AstrBot（人格库 + 上游配置）+ 假 DeepSeek + 假 LivingMemory，
起一个真实 sidecar 进程，走完整链路：

    HTTP /v1/chat/completions
      → 读人格 → 召回记忆 → 拼提示词
      → 调上游（假 DeepSeek）
      → 返回回复
      → 归档（archive.db）
      → 提炼（假 DeepSeek 返回 JSON）
      → outbox 入队 → worker 写（假 LM）
      → 索引验证（假 LM recall）

断言每一步都真的发生了。
"""

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SIDECAR = Path(__file__).resolve().parent.parent
PYTHON = sys.executable


def free_port() -> int:
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _DeepSeek(BaseHTTPRequestHandler):
    requests = []

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get('Content-Length') or 0)
        body = json.loads((self.rfile.read(n) or b'{}').decode('utf-8'))
        type(self).requests.append(body)
        system = body['messages'][0]['content'] if body.get('messages') else ''
        if 'JSON' in system and '数组' in system:
            content = json.dumps(
                [{'content': '他今天晚饭吃了番茄牛腩饭', 'importance': 0.7,
                  'topics': ['饮食', '晚饭', '番茄牛腩饭']}],
                ensure_ascii=False,
            )
        else:
            content = '（探头）番茄牛腩饭，记下了。'
        out = json.dumps(
            {'choices': [{'message': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}]}
        ).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


class _LM(BaseHTTPRequestHandler):
    memories = []

    def do_GET(self):  # noqa: N802
        self._json({'status': 'ok', 'version': '2.14.2', 'ready': True})

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get('Content-Length') or 0)
        body = json.loads((self.rfile.read(n) or b'{}').decode('utf-8'))
        if self.path.endswith('/memories'):
            type(self).memories.append(body)
            self._json({'memory': {'id': str(len(type(self).memories))}})
        elif self.path.endswith('/recall'):
            q = body.get('query') or ''
            items = [{'content': m['content']} for m in type(self).memories if q[:6] and q[:6] in m['content']]
            self._json({'items': items})
        else:
            self._json({})

    def _json(self, obj):
        out = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


class E2ETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='hub_e2e_'))
        _DeepSeek.requests = []
        _LM.memories = []

        cls.ds = HTTPServer(('127.0.0.1', 0), _DeepSeek)
        cls.lm = HTTPServer(('127.0.0.1', 0), _LM)
        threading.Thread(target=cls.ds.serve_forever, daemon=True).start()
        threading.Thread(target=cls.lm.serve_forever, daemon=True).start()
        ds_port = cls.ds.server_address[1]
        lm_port = cls.lm.server_address[1]

        # 假 AstrBot：人格库 + cmd_config
        persona_db = cls.tmp / 'data_v4.db'
        con = sqlite3.connect(persona_db)
        con.execute('create table personas (id integer primary key, persona_id text, system_prompt text)')
        con.execute("insert into personas(persona_id, system_prompt) values('dafeiyu','你是大肥鱼，一条黏人的鲸鱼娘。')")
        con.commit()
        con.close()

        astrbot_cfg = cls.tmp / 'cmd_config.json'
        astrbot_cfg.write_text(
            json.dumps({'provider_sources': [{'id': 'deepseek', 'key': ['sk-test'],
                                              'api_base': f'http://127.0.0.1:{ds_port}/v1'}]}),
            encoding='utf-8',
        )

        cls.port = free_port()
        cfg = {
            'port': cls.port,
            'persona_id': 'dafeiyu',
            'persona_db': str(persona_db),
            'astrbot_config': str(astrbot_cfg),
            'persona_cache_seconds': 0,
            'upstream_source': 'deepseek',
            'upstream_base': f'http://127.0.0.1:{ds_port}/v1',
            'upstream_model': 'deepseek-chat',
            'upstream_timeout': 30,
            'lm_base': f'http://127.0.0.1:{lm_port}/page/external',
            'lm_key': 'lm_test',
            'lm_timeout': 5,
            'recall_k': 5,
            'scope': 'airi:desktop',
            'write_scope': '一号:FriendMessage:123456789',
            'write_scope_check': 'warn',
            'extract': {'enabled': True, 'model': 'deepseek-chat', 'min_chars': 2,
                        'min_importance': 0.3, 'max_per_turn': 3, 'timeout': 30},
            'archive': {'enabled': True, 'db_path': 'archive.db', 'retention_days': 0,
                        'sync_qq': False, 'encrypt': True, 'qq_source_db': ''},
            'logging': {'log_content': True, 'preview_chars': 30},
            'knowledge': {'enabled': False, 'base_url': '', 'username': '', 'jwt_secret': '',
                          'kb_names': [], 'top_k': 3, 'timeout': 5, 'cache_seconds': 60},
            'outbox': {'enabled': True},
            'maintenance_mode': False,
        }
        (cls.tmp / 'config.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')

        env = dict(os.environ, HUB_DATA_DIR=str(cls.tmp), HUB_PORT=str(cls.port),
                   HUB_VERSION='1.0.0-e2e', PYTHONIOENCODING='utf-8')
        cls.proc = subprocess.Popen(
            [PYTHON, '-X', 'utf8', str(SIDECAR / 'hub.py')],
            cwd=str(SIDECAR), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        for _ in range(60):
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{cls.port}/health', timeout=1)
                break
            except Exception:
                time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.proc.terminate()
            cls.proc.wait(timeout=10)
        except Exception:
            cls.proc.kill()
        cls.ds.shutdown()
        cls.lm.shutdown()

    def _chat(self):
        body = {
            'model': 'deepseek-chat',
            'messages': [{'role': 'user', 'content': '我今天晚饭吃了番茄牛腩饭'}],
            'max_tokens': 50,
        }
        req = urllib.request.Request(
            f'http://127.0.0.1:{self.port}/v1/chat/completions',
            data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer x'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode('utf-8'))

    def test_full_chain(self):
        res = self._chat()
        self.assertIn('番茄牛腩饭', res['choices'][0]['message']['content'])

        # 1. 上游收到的提示词里有人格与记忆块
        chat_req = [r for r in _DeepSeek.requests if 'JSON' not in r['messages'][0]['content']]
        self.assertTrue(chat_req)
        system = chat_req[0]['messages'][0]['content']
        self.assertIn('大肥鱼', system)
        self.assertIn('番茄牛腩饭', json.dumps(chat_req[0]['messages'], ensure_ascii=False))

        # 2. 归档写入（异步线程，轮询等待）
        con = sqlite3.connect(self.tmp / 'archive.db')
        roles = []
        for _ in range(30):
            roles = [r[0] for r in con.execute('select role from messages order by id')]
            if 'user' in roles and 'assistant' in roles:
                break
            time.sleep(0.3)
        self.assertIn('user', roles)
        self.assertIn('assistant', roles)

        # 3. 提炼 + outbox 写入假 LM
        for _ in range(40):
            if _LM.memories:
                break
            time.sleep(0.5)
        self.assertTrue(_LM.memories, '假记忆库没有收到写入')
        self.assertIn('番茄牛腩饭', _LM.memories[0]['content'])

        # 4. outbox 状态推进
        status = None
        for _ in range(40):
            row = con.execute('select status from extractions order by id desc limit 1').fetchone()
            status = row[0] if row else None
            if status in ('written', 'verified'):
                break
            time.sleep(0.5)
        con.close()
        self.assertIn(status, ('written', 'verified'), status)

    def test_health_compat(self):
        with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/health', timeout=5) as r:
            d = json.loads(r.read().decode('utf-8'))
        self.assertEqual(d['persona_source'], 'astrbot_db')
        self.assertIn('astrbot.persona', d['compat'])
        self.assertIn('livingmemory.api', d['compat'])
        self.assertIn('outbox', d)


if __name__ == '__main__':
    unittest.main(verbosity=2)
