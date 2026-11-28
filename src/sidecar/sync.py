"""后台同步任务：

1. sync_qq   —— 把 AstrBot 的 QQ 原始对话导入归档库（增量）
2. retry_failed —— 重试写回失败的记忆
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import crypto
from config import data_path
from util import log

_stop = threading.Event()


# ---------------- 通用 ----------------
def _archive_db(cfg: dict) -> Path | None:
    arc = cfg.get('archive') or {}
    if not arc.get('enabled', True):
        return None
    return data_path(arc.get('db_path') or 'archive.db')


def _kv_get(con, key: str, default: str = '') -> str:
    row = con.execute('select value from kv where key=?', (key,)).fetchone()
    return row[0] if row else default


def _kv_set(con, key: str, value: str) -> None:
    con.execute('insert into kv(key,value) values(?,?) on conflict(key) do update set value=excluded.value',
                (key, value))


def session_allowed(cfg: dict, session_id: str) -> bool:
    """按配置过滤要归档的会话。

    scope_mode:
      all                  全部
      private_only         只有私聊
      private_or_whitelist 私聊 + 白名单群（默认）
    """
    arc = cfg.get('archive') or {}
    mode = (arc.get('scope_mode') or 'private_or_whitelist').strip()
    if mode == 'all':
        return True
    parts = (session_id or '').split(':')
    kind = parts[1] if len(parts) > 1 else ''
    target = parts[2] if len(parts) > 2 else ''
    if kind == 'FriendMessage':
        return True
    if kind == 'GroupMessage':
        if mode == 'private_only':
            return False
        allowed = {str(x) for x in (arc.get('whitelist_groups') or [])}
        return target in allowed
    return mode == 'all'


# ---------------- QQ 对话同步 ----------------
def sync_qq(cfg: dict) -> int:
    """把 QQ 侧新消息追加到归档库，返回导入条数。"""
    arc = cfg.get('archive') or {}
    if not arc.get('sync_qq', True):
        return 0

    src = Path(qq_source_path(cfg))
    if not str(src) or str(src) == '.':
        return 0
    if not src.exists():
        log(f'QQ 同步未配置或路径不存在: {src}')
        return 0

    db = _archive_db(cfg)
    if db is None:
        return 0

    try:
        scon = sqlite3.connect(f'file:{src}?mode=ro', uri=True, timeout=10)
        scon.row_factory = sqlite3.Row
        acon = sqlite3.connect(db, timeout=10)
        cursor = int(_kv_get(acon, 'qq_cursor', '0') or 0)

        imported = 0
        rows_all = []
        while len(rows_all) < 5000:
            batch = scon.execute(
                'select id, session_id, role, content, sender_id, sender_name, group_id, platform, timestamp '
                'from messages where id > ? order by id limit 500',
                (cursor,),
            ).fetchall()
            if not batch:
                break
            rows_all.extend(batch)
            cursor = max(cursor, int(batch[-1]['id']))
        rows = rows_all
        cursor = int(_kv_get(acon, 'qq_cursor', '0') or 0)
        now = time.time()
        skipped = 0
        for r in rows:
            # 游标先前进：以后即使跳过，也不会反复卡在同一批
            cursor = max(cursor, int(r['id']))
            sid = r['session_id'] or ''
            if not sid:
                continue
            if not session_allowed(cfg, sid):
                skipped += 1
                continue
            acon.execute(
                'insert or ignore into sessions(source, session_key, persona_id, created_at, last_active) '
                "values('qq',?,?,?,?)",
                (sid, cfg.get('persona_id') or 'dafeiyu', r['timestamp'] or now, r['timestamp'] or now),
            )
            row = acon.execute(
                "select id from sessions where source='qq' and session_key=?", (sid,)
            ).fetchone()
            if not row:
                continue
            meta = json.dumps(
                {
                    'sender_id': r['sender_id'],
                    'sender_name': r['sender_name'],
                    'group_id': r['group_id'],
                    'platform': r['platform'],
                    'src_id': r['id'],
                },
                ensure_ascii=False,
            )
            cur = acon.execute(
                'insert into messages(session_id, role, content, created_at, meta) values(?,?,?,?,?)',
                (
                    row[0],
                    r['role'],
                    _enc_field(cfg, r['content'] or ''),
                    r['timestamp'] or now,
                    _enc_field(cfg, meta),
                ),
            )
            if cur.rowcount:
                imported += 1
                if imported % 200 == 0:
                    acon.commit()
            cursor = max(cursor, int(r['id']))

        _kv_set(acon, 'qq_cursor', str(cursor))
        _kv_set(acon, 'qq_last_sync', str(time.time()))
        acon.commit()
        acon.close()
        scon.close()

        if imported or skipped:
            log(f'QQ 归档同步: 新增 {imported} 条' + (f'，跳过 {skipped} 条（不在白名单）' if skipped else '') + f'（游标 {cursor}）')
        return imported
    except Exception as e:
        log(f'QQ 归档同步失败: {str(e)[:120]}')
        return 0


def _enc_field(cfg: dict, text: str) -> str:
    """按配置加密字段；失败时退回明文，避免影响同步。"""
    try:
        if ((cfg.get('archive') or {}).get('encrypt', True)) is False:
            return text
        return crypto.encrypt(text)
    except Exception as e:
        log(f'加密失败（已回退明文）: {e}')
        return text


def qq_source_path(cfg: dict) -> str:
    return ((cfg.get('archive') or {}).get('qq_source_db')) or ''


# ---------------- 调度 ----------------
def start(cfg: dict) -> None:
    intv = int(((cfg.get('archive') or {}).get('sync_interval_seconds')) or 300)
    retry_intv = 60

    def loop_sync():
        time.sleep(5)
        while not _stop.is_set():
            try:
                sync_qq(cfg)
            except Exception as e:
                log(f'同步循环异常: {e}')
            _stop.wait(intv)

    threading.Thread(target=loop_sync, daemon=True, name='sync-qq').start()
    log(f'后台任务已启动：QQ 同步每 {intv}s')


def stop() -> None:
    _stop.set()
