"""记忆：召回、提炼、写回、归档。"""

from __future__ import annotations

import datetime
import json
import re
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import crypto
import migrations
import quality
import telemetry
import timeparse
import scope
from config import data_path
from upstream import read_upstream
from util import log

_lock = threading.Lock()
_probe = {'ok': False, 'reason': '', 'ts': 0.0}
PROBE_TTL = 15

EXTRACT_PROMPT = """从下面这轮对话里提取值得长期记住的信息（0-{max_n} 条）。

值得记：他的偏好、经历、关系、计划、情绪模式、重要事件
不值得：寒暄、语气词、重复内容、临时状态、AI 自己的话

**具体事件必须单独成条，不能被习惯概括吞掉。**

- 他说"我今天中午又吃的便利店"（今天是 {today}）→ 记 "他 {today} 中午又吃了便利店"（具体）
- 不要写成 "他经常吃便利店"（概括会把"今天"这个时间点丢掉）
- 如果既发生了具体事件、又能看出习惯，优先记具体事件

每条要求：
- 不超过 40 字
- 用第三人称写（"他说……""他喜欢……"）
- 时间一律写绝对日期（如 2026-11-25），不要写“今天 / 昨天 / 昨晚”；原话没有日期就按今天 {today} 换算
- 只记他这一轮亲口说过的事实；她回忆里的旧事不是新事实，不能写成“昨天……”
- 附两个分数（0-1）：
  - `importance`：这条信息对他有多重要（丢了对理解他有影响吗）
  - `stability`：多稳定（长期偏好/关系给高分，一次性事件给低分）
  具体事件 importance 可以高，但 stability 低；性格偏好相反

每条还要给 3-5 个检索关键词（topics），**必须包含一个宽泛类别词**：
- 类别词从这个集合里选：健康 饮食 工作 学习 出行 情绪 关系 金钱 计划 娱乐
- 其余词用口语里可能出现的说法（具体名词、时间）
- 例："他今晚要去准备辩论赛" → ["计划", "辩论赛", "今晚", "晚上安排"]
- 例："他下周三要去医院做胃镜" → ["健康", "医院", "胃镜", "下周三", "身体"]
- 这样问"我最近身体怎么样"也能命中
- 不要用整句话，不要标点

只输出 JSON 数组，没有就输出 []
格式：[{{"content": "...", "importance": 0.7, "stability": 0.4,
        "topics": ["类别", "关键词2", "关键词3"]}}]"""


# ---------------- 健康探测 ----------------
def probe_lm(cfg: dict, force: bool = False) -> dict:
    now = time.time()
    if not force and now - _probe['ts'] < PROBE_TTL:
        return {'connected': _probe['ok'], 'reason': _probe['reason']}

    key = cfg.get('lm_key') or ''
    if not key:
        _probe.update({'ok': False, 'reason': '未配置 API Key', 'ts': now})
        return {'connected': False, 'reason': '未配置 API Key'}

    try:
        req = urllib.request.Request(
            f"{cfg['lm_base']}/health", headers={'Authorization': f'Bearer {key}'}
        )
        with urllib.request.urlopen(req, timeout=2.5) as r:
            ok = r.status == 200
        _probe.update({'ok': ok, 'reason': '', 'ts': now})
        return {'connected': ok, 'reason': ''}
    except Exception as e:
        reason = str(e)[:80]
        _probe.update({'ok': False, 'reason': reason, 'ts': now})
        return {'connected': False, 'reason': reason}


# ---------------- 日志开关 ----------------
def _log_content(cfg: dict) -> bool:
    """是否允许把对话内容写进日志（默认关闭，防泄露）"""
    return bool(((cfg.get('logging') or {}).get('log_content')) is True)


# ---------------- 召回 ----------------
def recall(cfg: dict, query: str) -> list[str]:
    key = cfg.get('lm_key') or ''
    if not key or not (query or '').strip():
        return []

    scope_value = cfg.get('write_scope') or cfg.get('scope') or ''
    body = {
        'query': query[:500],
        'k': int(cfg.get('recall_k') or 8),
        'persona_id': cfg.get('persona_id') or 'dafeiyu',
    }
    if scope_value:
        body['scope'] = scope_value
    req = urllib.request.Request(
        f"{cfg['lm_base']}/recall",
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=float(cfg.get('lm_timeout') or 6)) as r:
            data = json.loads(r.read().decode('utf-8', 'ignore'))
    except Exception as e:
        log(f'召回失败: {str(e)[:100]}')
        return []

    items = data.get('memories') or data.get('results') or data.get('items') or []
    out = []
    for it in items:
        if isinstance(it, str):
            out.append(it)
        elif isinstance(it, dict):
            text = it.get('content') or it.get('text') or it.get('memory')
            if text:
                out.append(str(text))
    if scope_value:
        items_for_filter = [{'content': x} for x in out]
        filtered = quality.filter_by_scope(items_for_filter, scope_value)
        out = [x['content'] for x in filtered]

    out = out[:12]
    if _log_content(cfg):
        preview = int(((cfg.get('logging') or {}).get('preview_chars')) or 20)
        log(f'召回查询: [{query[:50]}] -> {len(out)} 条')
        if out:
            log('召回详情: ' + ' | '.join(x[:preview].replace('\n', ' ') for x in out))
    else:
        log(f'召回: {len(out)} 条（内容不记录）')
    return out


_DATE_RE = re.compile(r'(20\d{2})\s*[-年/.]\s*(\d{1,2})\s*[-月/.]\s*(\d{1,2})')
_MD_RE = re.compile(r'(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]')


def _event_ts(text: str, fallback: float) -> float:
    """解析事件时间：绝对日期优先；相对时间沿用写入时刻（保留小时）。"""
    t = text or ''
    # 1) 绝对日期 + 可选时间
    m = _DATE_RE.search(t)
    if m:
        hour, minute = 12, 0
        tm = re.search(r'(\d{1,2})\s*[:：]\s*(\d{2})', t)
        if tm:
            hour, minute = int(tm.group(1)), int(tm.group(2))
        try:
            return datetime.datetime(
                int(m.group(1)), int(m.group(2)), int(m.group(3)), hour, minute
            ).timestamp()
        except Exception:
            pass
    # 2) 只有 M月D日
    m = _MD_RE.search(t)
    if m:
        try:
            base = datetime.datetime.fromtimestamp(fallback)
            return datetime.datetime(base.year, int(m.group(1)), int(m.group(2)), 12, 0).timestamp()
        except Exception:
            pass
    # 3) 相对时间：写入时间通常就在事件附近，直接沿用
    return fallback


def _scope_allowed(cfg: dict, session_id: str) -> bool:
    """时间通道也要遵守 scope：只允许本人私聊与桌面。"""
    sid = (session_id or '').strip()
    if not sid:
        return False
    if sid == 'desktop' or sid.startswith('desktop:'):
        return True
    scope = (cfg.get('write_scope') or cfg.get('scope') or '').strip()
    if not scope:
        return 'GroupMessage' not in sid
    if sid == scope:
        return True
    # 其他私聊（同前缀不同 QQ）不算，避免把别人的事当他的
    return False


def _relevance(query: str, text: str) -> float:
    """粗排：字符/词重合 + 序列相似度，避免时间通道把闲聊顶到前面。"""
    import quality

    q = (query or '').strip()
    t = (text or '').strip()
    if not q or not t:
        return 0.0
    overlap = sum(1 for ch in set(q) if ch in t)
    return overlap / max(len(set(q)), 1) * 0.6 + quality.similar(q, t) * 0.4


def _today_str() -> str:
    return datetime.datetime.now().strftime('%Y-%m-%d')


def _lm_db_path(cfg: dict) -> Path:
    raw = (cfg.get('lm_db') or '').strip()
    if raw:
        return Path(raw)
    return Path.home() / '.astrbot' / 'data' / 'plugin_data' / 'astrbot_plugin_livingmemory' / 'livingmemory.db'


def recall_detailed(cfg: dict, query: str, k: int = 16) -> list[dict]:
    """和 recall 相同的召回，但保留时间、分数等字段。"""
    key = cfg.get('lm_key') or ''
    if not key or not (query or '').strip():
        return []
    scope_value = cfg.get('write_scope') or cfg.get('scope') or ''
    body = {
        'query': query[:500],
        'k': k,
        'persona_id': cfg.get('persona_id') or 'dafeiyu',
    }
    if scope_value:
        body['scope'] = scope_value
    req = urllib.request.Request(
        f"{cfg['lm_base']}/recall",
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=float(cfg.get('lm_timeout') or 8)) as r:
            data = json.loads(r.read().decode('utf-8', 'ignore'))
    except Exception as e:
        log(f'召回失败: {str(e)[:100]}')
        return []

    items = data.get('memories') or data.get('results') or data.get('items') or []
    out = []
    for it in items:
        if isinstance(it, str):
            out.append({'text': it, 'ts': 0, 'score': 0})
        elif isinstance(it, dict):
            text = it.get('content') or it.get('text') or it.get('memory')
            if text:
                out.append({
                    'text': str(text),
                    'ts': float(it.get('create_time') or 0),
                    'score': float(it.get('score') or 0),
                    'memory_id': it.get('memory_id'),
                })
    return out


def range_memories(cfg: dict, start: float, end: float, query: str = '', limit: int = 8) -> list[dict]:
    """按「事件时间」取区间记忆，并过滤 scope。"""
    db = _lm_db_path(cfg)
    if not db.exists():
        return []
    # 写入时间可能晚于事件时间（跨午夜提炼），先放宽窗口再按事件时间过滤
    a = datetime.datetime.fromtimestamp(start - 5 * 86400).strftime('%Y-%m-%d %H:%M:%S')
    b = datetime.datetime.fromtimestamp(end + 5 * 86400).strftime('%Y-%m-%d %H:%M:%S')
    try:
        con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            'select id, text, metadata, created_at from documents '
            "where created_at between ? and ? and text not like '%SELFTEST-%' "
            'order by created_at desc limit 400',
            (a, b),
        ).fetchall()
        con.close()
    except Exception as e:
        log(f'按时间取记忆失败: {str(e)[:80]}')
        return []

    out = []
    for r in rows:
        try:
            fallback = datetime.datetime.strptime(r['created_at'][:19], '%Y-%m-%d %H:%M:%S').timestamp()
        except Exception:
            fallback = 0
        try:
            meta = json.loads(r['metadata'] or '{}')
        except Exception:
            meta = {}
        sid = meta.get('session_id') or meta.get('adapter_id') or ''
        if not _scope_allowed(cfg, str(sid)):
            continue
        text = r['text'] or ''
        ev = _event_ts(text, fallback)
        if not (start <= ev <= end):
            continue
        out.append({
            'text': text,
            'ts': ev,
            'score': _relevance(query, text),
            'memory_id': r['id'],
        })

    out.sort(key=lambda x: (x['score'], x['ts']), reverse=True)
    return out[:limit]


def _fmt_memory(item: dict) -> str:
    text = (item.get('text') or '').replace('\n', ' ').strip()
    ts = item.get('ts') or 0
    if not ts:
        return text
    when = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
    return f'[{when}] {text}'


def recall_for_prompt(cfg: dict, query: str) -> list[str]:
    """给提示词用的召回：带日期，且时间相关问题优先取区间内的记忆。"""
    rng = timeparse.detect_range(query)
    detailed = recall_detailed(cfg, query, k=min(max(int(cfg.get('recall_k') or 8) * 2, 12), 20))
    merged = []
    if rng:
        merged.extend(range_memories(cfg, rng[0], rng[1], query=query))
        merged.extend(detailed)
        log(f'时间召回: {timeparse.describe(rng[0], rng[1])}')
    else:
        merged = detailed

    seen = set()
    out = []
    for item in merged:
        key = (item.get('text') or '').strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(_fmt_memory(item))
        if len(out) >= 12:
            break
    if _log_content(cfg):
        log(f'带日期召回: {len(out)} 条')
    return out


# ---------------- 提炼 ----------------
def _strip_fence(text: str) -> str:
    t = (text or '').strip()
    if t.startswith('```'):
        t = t.split('\n', 1)[-1]
        if t.endswith('```'):
            t = t[:-3]
    return t.strip()


def clean_for_extract(text: str) -> str:
    """去掉客户端前缀（时间戳、Context 块、ACT/DELAY 标记），避免污染提炼。"""
    import re

    if not text:
        return ''
    s = re.sub(r'\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\]', '', text)
    s = re.sub(r'\[Context\][\s\S]*?(?=\n\n|$)', '', s)
    s = re.sub(r'<\|ACT [^|]*\|>', '', s)
    s = re.sub(r'<\|DELAY \d+\|>', '', s)
    s = re.sub(r'- system:[\w\-\.]+', '', s)
    # AIRI 会把系统指令拼进用户消息里，整段切掉
    s = re.split(r'每次回复都要以\s*ACT', s)[0]
    s = re.split(r'如果回复过程中情绪发生变化', s)[0]
    s = re.sub(r'\n{3,}', '\n\n', s)
    return s.strip()


def parse_extract_json(text: str) -> list:
    """把提炼返回的内容解析成列表：容错处理空内容、代码围栏、截断。

    返回 [] 表示这次没有可用的提炼结果（调用方会记录日志）。
    """
    import re

    if not text or not text.strip():
        return []
    cleaned = _strip_fence(text)
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, list) else []
    except Exception:
        pass
    # 从第一个 [ 开始，尽量救出完整的对象
    start = cleaned.find('[')
    if start == -1:
        return []
    body = cleaned[start:]
    for candidate in (body, body + ']', body[: body.rfind('}') + 1] + ']'):
        try:
            data = json.loads(candidate)
            if isinstance(data, list):
                return data
        except Exception:
            continue
    return []


def extract(cfg: dict, user_text: str, reply_text: str) -> list[dict]:
    ex = cfg.get('extract') or {}
    up = read_upstream(cfg)
    if not up['key']:
        return []

    max_n = int(ex.get('max_per_turn') or 2)
    min_imp = float(ex.get('min_importance') or 0.3)
    body = {
        'model': ex.get('model') or up['model'],
        'messages': [
            {'role': 'system', 'content': EXTRACT_PROMPT.format(max_n=max_n, today=_today_str())},
            {'role': 'user', 'content': f'他说：{clean_for_extract(user_text)[:600]}\n她说：{clean_for_extract(reply_text)[:600]}'},
        ],
        'temperature': 0.1,
        'max_tokens': 700,
    }
    req = urllib.request.Request(
        up['base'].rstrip('/') + '/chat/completions',
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers={'Authorization': f"Bearer {up['key']}", 'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(
            req, timeout=float(ex.get('timeout') or 45), context=ssl.create_default_context()
        ) as r:
            data = json.loads(r.read().decode('utf-8', 'ignore'))
        raw = (data.get('choices') or [{}])[0].get('message', {}).get('content') or ''
        if not (raw or '').strip():
            log('提炼返回空内容，跳过本轮')
            return []
        items = parse_extract_json(raw)
        if not items:
            if '[]' in raw.replace(' ', '') or not raw.strip():
                log('提炼结果为空（本轮没有需要记的内容）')
            else:
                log(f'提炼无可解析结果，返回片段: {raw[:60]!r}')
            return []
    except Exception as e:
        log(f'提炼失败: {str(e)[:100]}')
        return []

    out = []
    for it in items[:max_n]:
        if not isinstance(it, dict):
            continue
        content = str(it.get('content', '')).strip()
        try:
            importance = float(it.get('importance', 0.5) or 0.5)
        except (TypeError, ValueError):
            importance = 0.5
        if len(content) < 4 or importance < min_imp:
            continue
        if content.startswith(('她', '我', '你')):
            log(f'丢弃非事实条目: {content[:30]}')
            continue
        topics = it.get('topics')
        if not isinstance(topics, list):
            topics = []
        topics = [str(x).strip()[:12] for x in topics if str(x).strip()][:5]
        score = quality.final_importance(importance, it.get('stability'))
        out.append(
            {
                'content': content[:120],
                'importance': score,
                'topics': topics,
            }
        )
    return out


# ---------------- 写回 ----------------
def write_back(cfg: dict, items: list[dict], seed: str, message_id: int | None = None) -> int:
    key = cfg.get('lm_key') or ''
    if not items or not key:
        return 0

    if not scope.can_write_back(cfg):
        detail = scope.validate(cfg).detail
        log(f'写回已暂停（作用域校验未通过）: {detail}')
        for idx, item in enumerate(items):
            record_extraction(cfg, item, f'{seed}#{idx}', None, 'scope_blocked', detail, message_id)
        return 0

    # 写前去重：先查本地 extractions（不受 LM 索引延迟影响），再补一次召回
    scope_value = cfg.get('write_scope') or cfg.get('scope') or ''
    existing: list[str] = []
    try:
        existing = recall(cfg, items[0]['content'][:40])[:5]
    except Exception:
        existing = []
    try:
        db = _archive_db(cfg)
        if db is not None:
            con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=5)
            rows = con.execute(
                "select content from extractions where status != 'duplicate' "
                'order by id desc limit 50'
            ).fetchall()
            con.close()
            existing.extend(_dec(cfg, r[0]) for r in rows if r[0])
    except Exception as e:
        log(f'本地去重查询失败: {e}')

    kept = []
    for idx, item in enumerate(items):
        if quality.is_duplicate(cfg, item['content'], existing):
            if _log_content(cfg):
                log(f"重复记忆已跳过: {item['content'][:30]}")
            else:
                log(f"重复记忆已跳过（{len(item['content'])}字，内容不记录）")
            record_extraction(cfg, item, f'{seed}#{idx}', None, 'duplicate', '与已有记忆高度相似', message_id)
            continue
        kept.append((idx, item))
    if not kept:
        return 0

    # outbox：只入队，由后台 worker 负责发送与重试
    import outbox

    if outbox.enabled(cfg):
        queued = 0
        for idx, item in kept:
            if outbox.enqueue(cfg, item, f'{seed}#{idx}', message_id):
                queued += 1
        if queued:
            log(f'已入队 {queued} 条待写入记忆（跳过 {len(items) - queued} 条重复）')
        return queued
    written = 0
    for idx, item in enumerate(items):
        body = {
            'content': item['content'],
            'importance': item['importance'],
            'idempotency_key': f'{seed}#{idx}',
            'persona_id': cfg.get('persona_id') or 'dafeiyu',
            'scope': cfg.get('write_scope') or cfg.get('scope') or '',
            'topics': (item.get('topics') or []) + [item['content'][:12]],
            'key_facts': [item['content']],
        }
        req = urllib.request.Request(
            f"{cfg['lm_base']}/memories",
            data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                res = json.loads(r.read().decode('utf-8', 'ignore'))
            mem_id = (res.get('memory') or {}).get('id') if isinstance(res, dict) else None
            written += 1
            record_extraction(cfg, item, f'{seed}#{idx}', mem_id, 'written', None, message_id)
            log(f"写回记忆: {item['content'][:30]}… (重要性 {item['importance']})")
        except Exception as e:
            record_extraction(cfg, item, f'{seed}#{idx}', None, 'failed', str(e)[:120], message_id)
            log(f'写回失败: {str(e)[:120]}')
    return written


# ---------------- 归档 ----------------
def init_archive(cfg: dict) -> bool:
    """初始化/迁移归档库。返回 False 表示迁移失败（调用方应视为 FATAL）。"""
    arc = cfg.get('archive') or {}
    if not arc.get('enabled', True):
        return True
    db = data_path(arc.get('db_path') or 'archive.db')

    # 迁移先跑：建表、补列、打 user_version；版本高于代码则拒绝
    res = migrations.ensure_archive(db)
    if not res.ok:
        log(f'归档库迁移失败: {res.error}')
        return False
    if res.applied:
        log(f'归档库迁移: {", ".join(res.applied)} → version {res.version}')

    try:
        con = sqlite3.connect(db, timeout=10)
        con.executescript(
            '''
            CREATE TABLE IF NOT EXISTS sessions (
              id INTEGER PRIMARY KEY, source TEXT NOT NULL, session_key TEXT NOT NULL,
              persona_id TEXT, created_at REAL NOT NULL, last_active REAL,
              UNIQUE(source, session_key));
            CREATE TABLE IF NOT EXISTS messages (
              id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL, role TEXT NOT NULL,
              content TEXT NOT NULL, created_at REAL NOT NULL, meta TEXT);
            CREATE TABLE IF NOT EXISTS extractions (
              id INTEGER PRIMARY KEY, message_id INTEGER, content TEXT NOT NULL,
              importance REAL DEFAULT 0.5, idempotency_key TEXT UNIQUE, lm_memory_id TEXT,
              status TEXT DEFAULT 'pending', error TEXT, created_at REAL NOT NULL, written_at REAL,
              retry_count INTEGER DEFAULT 0, next_retry_at REAL, last_error_at REAL);
            CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
            '''
        )
        con.commit()
        con.close()
        log(f'归档库就绪: {db.name}')
        _ensure_extra_columns(db)
        _cleanup_archive(cfg, db)
        return True
    except Exception as e:
        log(f'归档库初始化失败: {e}')
        return False


def _archive_db(cfg: dict) -> Path | None:
    arc = cfg.get('archive') or {}
    if not arc.get('enabled', True):
        return None
    return data_path(arc.get('db_path') or 'archive.db')


def _dec(cfg: dict, text: str) -> str:
    try:
        return crypto.decrypt(text)
    except Exception:
        return text


def _enc(cfg: dict, text: str) -> str:
    """按配置决定是否加密字段；出错时退回明文，避免影响主流程。"""
    try:
        if ((cfg.get('archive') or {}).get('encrypt', True)) is False:
            return text
        return crypto.encrypt(text)
    except Exception as e:
        log(f'加密失败（已回退明文）: {e}')
        return text


def archive_message(cfg: dict, role: str, content: str) -> int | None:
    db = _archive_db(cfg)
    if db is None or not content:
        return None
    try:
        with _lock:
            con = sqlite3.connect(db, timeout=10)
            now = time.time()
            con.execute(
                'INSERT OR IGNORE INTO sessions (source, session_key, persona_id, created_at, last_active) '
                "VALUES ('airi','desktop',?,?,?)",
                (cfg.get('persona_id') or 'dafeiyu', now, now),
            )
            sid = con.execute(
                "SELECT id FROM sessions WHERE source='airi' AND session_key='desktop'"
            ).fetchone()[0]
            con.execute('UPDATE sessions SET last_active=? WHERE id=?', (now, sid))
            cur = con.execute(
                'INSERT INTO messages (session_id, role, content, created_at) VALUES (?,?,?,?)',
                (sid, role, _enc(cfg, content), now),
            )
            con.commit()
            mid = cur.lastrowid
            con.close()
            return mid
    except Exception as e:
        log(f'归档写入失败: {e}')
        return None


def record_extraction(cfg, item, idem, mem_id, status, error, message_id: int | None = None) -> None:
    db = _archive_db(cfg)
    if db is None:
        return
    try:
        with _lock:
            con = sqlite3.connect(db, timeout=10)
            con.execute(
                'INSERT OR IGNORE INTO extractions '
                '(message_id, content, importance, idempotency_key, lm_memory_id, status, error, '
                ' created_at, written_at, next_retry_at) '
                'VALUES (?,?,?,?,?,?,?,?,?,?)',
                (
                    message_id,
                    _enc(cfg, item['content']),
                    item['importance'],
                    idem,
                    mem_id,
                    status,
                    error,
                    time.time(),
                    time.time() if status == 'written' else None,
                    time.time() + 60 if status == 'failed' else None,
                ),
            )
            con.commit()
            con.close()
    except Exception as e:
        log(f'提炼记录写入失败: {e}')


# ---------------- 后台任务 ----------------
def schedule_memory_job(cfg: dict, user_text: str, reply_text: str, trace: str = '') -> None:
    ex = cfg.get('extract') or {}
    enabled = bool(ex.get('enabled', True))
    min_chars = int(ex.get('min_chars') or 6)

    def worker():
        t_start = time.time()
        try:
            mid = archive_message(cfg, 'user', user_text)
            archive_message(cfg, 'assistant', reply_text)
            telemetry.record('archive', 'ok', int((time.time() - t_start) * 1000), f'mid={mid}', trace)

            if not enabled or len((user_text or '').strip()) < min_chars:
                telemetry.record('extract', 'skipped', 0, '太短或未启用', trace)
                return
            t_ex = time.time()
            items = extract(cfg, user_text, reply_text)
            telemetry.record('extract', 'ok', int((time.time() - t_ex) * 1000),
                             f'{len(items)} 条' + ('' if items else '（无需记忆）'), trace)
            if not items:
                return
            queued = write_back(cfg, items, f'airi:msg{mid or int(time.time())}', mid)
            telemetry.record('enqueue', 'ok' if queued else 'blocked',
                             int((time.time() - t_start) * 1000), f'{queued} 条', trace)
        except Exception as e:
            log(f'记忆任务异常: {e}')

    threading.Thread(target=worker, daemon=True).start()


def _ensure_extra_columns(db) -> None:
    """老库升级：补齐重试相关列（无条件执行，不等重试循环）。"""
    try:
        con = sqlite3.connect(db, timeout=10)
        cols = {r[1] for r in con.execute('PRAGMA table_info(extractions)')}
        added = []
        for name, decl in (
            ('retry_count', 'INTEGER DEFAULT 0'),
            ('next_retry_at', 'REAL'),
            ('last_error_at', 'REAL'),
            ('attempts', 'INTEGER DEFAULT 0'),
            ('verified_at', 'REAL'),
            ('topics', 'TEXT'),
        ):
            if name not in cols:
                con.execute(f'ALTER TABLE extractions ADD COLUMN {name} {decl}')
                added.append(name)
        if added:
            con.commit()
            log(f'归档库升级: 新增列 {", ".join(added)}')
        con.close()
    except Exception as e:
        log(f'归档库补列失败: {e}')


def _cleanup_archive(cfg: dict, db) -> None:
    """按保留天数删除旧原文（0 表示永久保留）"""
    days = int(((cfg.get('archive') or {}).get('retention_days')) or 0)
    if days <= 0:
        return
    cutoff = time.time() - days * 86400
    try:
        with _lock:
            con = sqlite3.connect(db, timeout=10)
            removed = con.execute('DELETE FROM messages WHERE created_at < ?', (cutoff,)).rowcount
            con.execute('DELETE FROM sessions WHERE last_active < ?', (cutoff,))
            con.commit()
            con.close()
        if removed:
            log(f'归档清理: 删除 {removed} 条超过 {days} 天的原文')
    except Exception as e:
        log(f'归档清理失败: {e}')
