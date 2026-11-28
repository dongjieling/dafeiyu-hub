"""Durable 每轮记忆任务。

先把这一轮对话落盘成 turns 行，再异步提炼；崩溃后可恢复，
不再依赖「归档完成 → 提炼线程」之间的内存状态。
"""

from __future__ import annotations

import sqlite3
import threading
import time

from util import log

_stop = threading.Event()


def _db(cfg: dict):
    from memory import _archive_db

    return _archive_db(cfg)


def ensure_table(con: sqlite3.Connection) -> None:
    con.execute(
        '''CREATE TABLE IF NOT EXISTS turns (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             client_id TEXT,
             scope TEXT,
             persona_id TEXT,
             message_id INTEGER,
             user_text TEXT,
             reply_text TEXT,
             trace TEXT,
             model TEXT,
             temperature REAL,
             status TEXT DEFAULT 'pending',
             attempts INTEGER DEFAULT 0,
             next_at REAL,
             error TEXT,
             created_at REAL,
             updated_at REAL
           )'''
    )
    cols = {r[1] for r in con.execute('PRAGMA table_info(turns)')}
    for name, decl in (('model', 'TEXT'), ('temperature', 'REAL')):
        if name not in cols:
            con.execute(f'ALTER TABLE turns ADD COLUMN {name} {decl}')


def capture(cfg: dict, user_text: str, reply_text: str, trace: str = '') -> tuple[int, int | None] | None:
    """落盘一轮对话。返回 (turn_id, message_id)。"""
    from memory import _enc

    db = _db(cfg)
    if db is None:
        return None
    try:
        con = sqlite3.connect(db, timeout=10)
        ensure_table(con)
        now = time.time()
        cur = con.execute(
            'INSERT INTO turns (client_id, scope, persona_id, user_text, reply_text, trace, '
            ' model, temperature, status, attempts, next_at, created_at, updated_at) '
            "VALUES (?,?,?,?,?,?,?,?,'pending',0,?,?,?)",
            (
                cfg.get('client_id') or 'default',
                cfg.get('write_scope') or cfg.get('scope') or '',
                cfg.get('persona_id') or '',
                _enc(cfg, user_text or ''),
                _enc(cfg, reply_text or ''),
                trace,
                cfg.get('model') or '',
                float(cfg.get('temperature') if cfg.get('temperature') is not None else -1),
                now,
                now,
                now,
            ),
        )
        tid = cur.lastrowid
        con.commit()
        con.close()
    except Exception as e:
        log(f'对话落盘失败: {str(e)[:100]}')
        return None

    # 归档原文（失败不影响 turn 恢复）
    try:
        import memory

        mid = memory.archive_message(cfg, 'user', user_text)
        memory.archive_message(cfg, 'assistant', reply_text)
        if mid:
            con = sqlite3.connect(db, timeout=10)
            con.execute('UPDATE turns SET message_id=? WHERE id=?', (mid, tid))
            con.commit()
            con.close()
        return tid, mid
    except Exception as e:
        log(f'归档失败（turn 仍可恢复）: {str(e)[:80]}')
        return tid, None


def mark(cfg: dict, tid: int, status: str, error: str = '', attempts: int | None = None,
         next_at: float | None = None) -> None:
    db = _db(cfg)
    if db is None:
        return
    try:
        con = sqlite3.connect(db, timeout=10)
        sets = ['status=?', 'error=?', 'updated_at=?']
        args = [status, error[:300], time.time()]
        if attempts is not None:
            sets.append('attempts=?')
            args.append(attempts)
        if next_at is not None:
            sets.append('next_at=?')
            args.append(next_at)
        con.execute(f'UPDATE turns SET {", ".join(sets)} WHERE id=?', args + [tid])
        con.commit()
        con.close()
    except Exception as e:
        log(f'turn 状态更新失败: {str(e)[:80]}')


def _claim(cfg: dict, tid: int) -> dict | None:
    db = _db(cfg)
    if db is None:
        return None
    con = sqlite3.connect(db, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        ensure_table(con)
        cur = con.execute(
            "UPDATE turns SET status='processing', updated_at=? WHERE id=? AND status='pending'",
            (time.time(), tid),
        )
        if cur.rowcount != 1:
            con.rollback()
            return None
        row = con.execute('SELECT * FROM turns WHERE id=?', (tid,)).fetchone()
        con.commit()
        return dict(row) if row else None
    finally:
        con.close()


def process_one(cfg: dict, tid: int) -> str:
    """提炼一条 turn，返回最终状态。"""
    from memory import _dec, extract, write_back

    row = _claim(cfg, tid)
    if row is None:
        return 'skip'

    run_cfg = dict(cfg)
    cid = (row.get('client_id') or '').strip()
    if cid:
        run_cfg['client_id'] = cid
    scope = (row.get('scope') or '').strip()
    if scope:
        run_cfg['write_scope'] = scope
        run_cfg['scope'] = scope
    persona = (row.get('persona_id') or '').strip()
    if persona:
        run_cfg['persona_id'] = persona
    model = (row.get('model') or '').strip()
    if model:
        run_cfg['model'] = model
    temp = row.get('temperature')
    if temp is not None and float(temp) >= 0:
        run_cfg['temperature'] = float(temp)
    # 老 turn 没存身份时，尽量从当前 profile 补全
    if cid and cid != 'default' and not (scope and persona):
        try:
            import clients as clients_mod

            profile = clients_mod.get_client(cid)
            if profile:
                run_cfg = clients_mod.effective_cfg(run_cfg, profile)
                if model:
                    run_cfg['model'] = model
        except Exception:
            pass

    user_text = _dec(run_cfg, row['user_text'] or '')
    reply_text = _dec(run_cfg, row['reply_text'] or '')
    trace = row['trace'] or ''
    try:
        import telemetry

        t0 = time.time()
        items = extract(run_cfg, user_text, reply_text)
        if items is None:
            attempts = int(row['attempts'] or 0) + 1
            if attempts < 3:
                mark(cfg, tid, 'pending', '解析失败', attempts, time.time() + 60)
            else:
                mark(cfg, tid, 'failed', '解析失败（已重试 3 次）', attempts)
            telemetry.record('extract', 'failed', int((time.time() - t0) * 1000), '解析失败', trace)
            return 'failed'
        telemetry.record('extract', 'ok', int((time.time() - t0) * 1000),
                         f'{len(items)} 条' + ('' if items else '（无需记忆）'), trace)
        if not items:
            mark(cfg, tid, 'skipped', '', int(row['attempts'] or 0))
            return 'skipped'
        mid = row['message_id']
        seed = f'airi:msg{mid}' if mid else f'turn:{tid}'
        queued = write_back(run_cfg, items, seed, mid)
        mark(cfg, tid, 'queued' if queued else 'duplicate', '', int(row['attempts'] or 0))
        telemetry.record('enqueue', 'ok' if queued else 'blocked', 0, f'{queued} 条', trace)
        return 'queued' if queued else 'duplicate'
    except Exception as e:
        attempts = int(row['attempts'] or 0) + 1
        if attempts < 3:
            mark(cfg, tid, 'pending', str(e), attempts, time.time() + 60)
        else:
            mark(cfg, tid, 'failed', str(e), attempts)
        log(f'turn 提炼异常: {str(e)[:100]}')
        return 'failed'


def process_pending(cfg: dict, limit: int = 5) -> int:
    db = _db(cfg)
    if db is None:
        return 0
    try:
        con = sqlite3.connect(db, timeout=10)
        ensure_table(con)
        rows = con.execute(
            "SELECT id FROM turns WHERE status='pending' AND coalesce(next_at,0)<=? "
            'ORDER BY id LIMIT ?',
            (time.time(), limit),
        ).fetchall()
        con.close()
    except Exception:
        return 0
    done = 0
    for (tid,) in rows:
        process_one(cfg, tid)
        done += 1
    return done


def recover(cfg: dict) -> int:
    """进程重启后把卡在 processing 的 turn 放回 pending。"""
    db = _db(cfg)
    if db is None:
        return 0
    try:
        con = sqlite3.connect(db, timeout=10)
        ensure_table(con)
        n = con.execute(
            "UPDATE turns SET status='pending', updated_at=? WHERE status='processing'",
            (time.time(),),
        ).rowcount
        con.commit()
        con.close()
        if n:
            log(f'turn 恢复: {n} 条重新排队')
        return n
    except Exception:
        return 0


def prune(cfg: dict, days: int = 30) -> int:
    """清理已完成的 turn，默认保留 30 天。"""
    db = _db(cfg)
    if db is None or days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    try:
        con = sqlite3.connect(db, timeout=10)
        ensure_table(con)
        n = con.execute(
            "DELETE FROM turns WHERE created_at < ? AND status IN ('queued','skipped','duplicate','done')",
            (cutoff,),
        ).rowcount
        con.commit()
        con.close()
        return n
    except Exception:
        return 0


def stats(cfg: dict) -> dict:
    db = _db(cfg)
    if db is None:
        return {}
    try:
        con = sqlite3.connect(db, timeout=10)
        ensure_table(con)
        rows = con.execute('SELECT status, count(*) FROM turns GROUP BY status').fetchall()
        con.close()
        return {k: v for k, v in rows}
    except Exception:
        return {}


def start(cfg: dict) -> None:
    recover(cfg)
    try:
        prune(cfg)
    except Exception:
        pass

    def loop():
        time.sleep(5)
        while not _stop.is_set():
            try:
                process_pending(cfg)
            except Exception as e:
                log(f'turn 循环异常: {str(e)[:80]}')
            _stop.wait(5)

    threading.Thread(target=loop, daemon=True, name='turns').start()
    log('turn worker 已启动（每 5 秒）')
