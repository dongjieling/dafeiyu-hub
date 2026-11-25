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
        for r in rows:
            sid = r['session_id'] or ''
            if not sid:
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
            cursor = max(cursor, int(r['id']))

        _kv_set(acon, 'qq_cursor', str(cursor))
        _kv_set(acon, 'qq_last_sync', str(time.time()))
        acon.commit()
        acon.close()
        scon.close()

        if imported:
            log(f'QQ 归档同步: 新增 {imported} 条（游标 {cursor}）')
        return imported
    except Exception as e:
        log(f'QQ 归档同步失败: {str(e)[:120]}')
        return 0


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

    def loop_retry():
        time.sleep(20)
        while not _stop.is_set():
            try:
                import memory
                import retry as retry_mod

                lm_ready = memory.probe_lm(cfg, force=True)['connected']
                if not lm_ready:
                    log('记忆库未就绪，本轮跳过重试（不消耗次数）')
                else:
                    retry_mod.retry_failed(cfg, lm_ready=True)
            except Exception as e:
                log(f'重试循环异常: {e}')
            _stop.wait(retry_intv)

    threading.Thread(target=loop_sync, daemon=True, name='sync-qq').start()
    threading.Thread(target=loop_retry, daemon=True, name='retry-failed').start()
    log(f'后台任务已启动：QQ 同步每 {intv}s，失败重试每 {retry_intv}s')


def stop() -> None:
    _stop.set()
