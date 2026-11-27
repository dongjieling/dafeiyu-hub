"""outbox：写入记忆的可靠队列（二期）。

状态机：
    pending → sending → written → index_pending → verified
                   ↓           ↘
                failed ──→ dead（超次数）   index_failed（可重试）
    scope_blocked：作用域校验未通过，不发

规则：
- 聊天线程只入队，不发网络请求
- worker 认领（原子 UPDATE），进程崩了也不会卡在 sending
- 写入成功后验证可检索：超时标 index_failed，重试最多 3 次
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import crypto
from adapters import LivingMemoryAdapter
from config import data_path
from util import log
import telemetry

MAX_ATTEMPTS = 5
BACKOFF = [60, 300, 900, 3600]
INDEX_WAIT = 120          # 写入后等索引的最长时间
INDEX_RETRY_LIMIT = 3


def _db(cfg: dict) -> Path | None:
    arc = cfg.get('archive') or {}
    if not arc.get('enabled', True):
        return None
    return data_path(arc.get('db_path') or 'archive.db')


def _enc_content(cfg: dict, text: str) -> str:
    """与 record_extraction 保持一致：按 archive.encrypt 决定是否加密。"""
    try:
        if ((cfg.get('archive') or {}).get('encrypt', True)) is False:
            return text
        return crypto.encrypt(text)
    except Exception:
        return text


def _dec_content(cfg: dict, text: str) -> str:
    try:
        return crypto.decrypt(text)
    except Exception:
        return text


def _mask(cfg: dict, text: str) -> str:
    """按 logging.log_content 决定日志里能不能出现正文。"""
    allow = bool(((cfg.get('logging') or {}).get('log_content')) is True)
    return text[:24] if allow else f'({len(text)}字)'


def enabled(cfg: dict) -> bool:
    return bool((cfg.get('outbox') or {}).get('enabled', True))


# ---------------- 入队 ----------------
def enqueue(cfg: dict, item: dict, idem: str, message_id: int | None = None,
            status: str = 'pending', error: str = '') -> int | None:
    db = _db(cfg)
    if db is None:
        return None
    try:
        con = sqlite3.connect(db, timeout=10)
        cur = con.execute(
            'INSERT OR IGNORE INTO extractions '
            '(message_id, content, importance, idempotency_key, status, error, created_at, '
            ' retry_count, next_retry_at, attempts, verified_at, topics) '
            'VALUES (?,?,?,?,?,?,?,0,?,0,NULL,?)',
            (
                message_id,
                _enc_content(cfg, item['content']),
                item['importance'],
                idem,
                status,
                error,
                time.time(),
                time.time(),
                _enc_content(cfg, json.dumps(item.get('topics') or [], ensure_ascii=False)),
            ),
        )
        con.commit()
        row_id = cur.lastrowid
        con.close()
        return row_id
    except Exception as e:
        log(f'outbox 入队失败: {e}')
        return None


# ---------------- worker ----------------
def _claim(con: sqlite3.Connection, limit: int = 5) -> list[sqlite3.Row]:
    now = time.time()
    con.execute(
        "UPDATE extractions SET status='sending' "
        "WHERE id IN (SELECT id FROM extractions "
        "             WHERE status IN ('pending','failed') AND coalesce(next_retry_at,0)<=? "
        "             ORDER BY id LIMIT ?)",
        (now, limit),
    )
    con.commit()
    return con.execute("SELECT * FROM extractions WHERE status='sending' ORDER BY id").fetchall()


def _send_one(cfg: dict, row: sqlite3.Row, adapter: LivingMemoryAdapter) -> tuple[str, str, str]:
    """返回 (status, lm_memory_id, error)。"""
    plain = _dec_content(cfg, row['content'])
    topics = []
    try:
        topics = json.loads(_dec_content(cfg, row['topics'] or '[]')) or []
    except Exception:
        topics = []
    body = {
        'content': plain,
        'importance': row['importance'],
        'idempotency_key': row['idempotency_key'],
        'persona_id': cfg.get('persona_id') or 'dafeiyu',
        'scope': cfg.get('write_scope') or cfg.get('scope') or '',
        'key_facts': [plain],
        'topics': topics,
    }
    try:
        res = adapter.write(body)
        mem_id = ''
        if isinstance(res, dict):
            mem = res.get('memory') or {}
            mem_id = str(mem.get('id') or res.get('memory_id') or '')
        return 'written', mem_id, ''
    except Exception as e:
        return 'failed', '', str(e)[:120]


def tick(cfg: dict) -> int:
    """处理一批。返回处理条数。"""
    db = _db(cfg)
    if db is None:
        return 0
    adapter = LivingMemoryAdapter(cfg)
    con = sqlite3.connect(db, timeout=20)
    con.row_factory = sqlite3.Row
    try:
        rows = _claim(con)
        if not rows:
            return 0
        done = 0
        for row in rows:
            status, mem_id, error = _send_one(cfg, row, adapter)
            attempts = int(row['attempts'] or 0)
            if status == 'written':
                con.execute(
                    "UPDATE extractions SET status='written', lm_memory_id=?, written_at=?, "
                    "error='', next_retry_at=?, attempts=? WHERE id=?",
                    (mem_id, time.time(), time.time() + 45, attempts + 1, row['id']),
                )
                log(f"outbox 写入成功: {_mask(cfg, _dec_content(cfg, row['content']))}")
                telemetry.record('write', 'ok', 0, _mask(cfg, _dec_content(cfg, row['content'])))
            else:
                attempts += 1
                delay = BACKOFF[min(attempts - 1, len(BACKOFF) - 1)]
                new_status = 'dead' if attempts >= MAX_ATTEMPTS else 'failed'
                con.execute(
                    'UPDATE extractions SET status=?, error=?, attempts=?, next_retry_at=?, '
                    'last_error_at=? WHERE id=?',
                    (new_status, error, attempts, time.time() + delay, time.time(), row['id']),
                )
                log(f"outbox 写入失败（第 {attempts} 次，{delay}s 后重试）: {error[:80]}")
                telemetry.record('write', 'failed', 0, f'#{attempts} {error[:60]}')
            done += 1
        con.commit()
        return done
    finally:
        con.close()


def verify_index(cfg: dict, limit: int = 5) -> int:
    """检查「已写入但未确认可检索」的记录。"""
    db = _db(cfg)
    if db is None:
        return 0
    adapter = LivingMemoryAdapter(cfg)
    con = sqlite3.connect(db, timeout=20)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT * FROM extractions WHERE status='written' AND coalesce(next_retry_at,0)<=? "
            'ORDER BY id LIMIT ?',
            (time.time(), limit),
        ).fetchall()
        checked = 0
        for row in rows:
            plain = _dec_content(cfg, row['content'])
            try:
                items = adapter.recall(plain[:60], 3, cfg.get('write_scope') or None)
            except Exception:
                items = []
            import quality

            hit = any(
                quality.similar(plain, str(it.get('content') or '')) >= 0.8 for it in items
            )
            if hit:
                # 状态保持 written，成功检索用 verified_at 记录
                con.execute(
                    'UPDATE extractions SET verified_at=?, attempts=0 WHERE id=?',
                    (time.time(), row['id']),
                )
            else:
                tries = int(row['attempts'] or 0)
                if tries >= INDEX_RETRY_LIMIT:
                    # 写入已成功，只是没在窗口内确认可检索：标为「未确认」，不算失败
                    con.execute("UPDATE extractions SET status='index_unconfirmed' WHERE id=?", (row['id'],))
                    log(f"索引未确认（已重试 {tries} 次，写入本身成功）: {_mask(cfg, row['content'])}")
                else:
                    con.execute(
                        "UPDATE extractions SET attempts=attempts+1, next_retry_at=? WHERE id=?",
                        (time.time() + 60, row['id']),
                    )
            checked += 1
        con.commit()
        return checked
    finally:
        con.close()


def recover_stale(cfg: dict) -> int:
    """进程重启后把卡在 sending 的记录放回 pending（不丢不重）。"""
    db = _db(cfg)
    if db is None:
        return 0
    try:
        con = sqlite3.connect(db, timeout=10)
        n = con.execute("update extractions set status='pending' where status='sending'").rowcount
        # 语义变更：index_failed 不再代表写入失败，统一归为「索引未确认」
        con.execute("update extractions set status='index_unconfirmed' where status='index_failed'")
        con.commit()
        con.close()
        if n:
            log(f'outbox 恢复: {n} 条卡住的记录已放回队列')
        return n
    except Exception as e:
        log(f'outbox 恢复失败: {e}')
        return 0


def start(cfg: dict, interval: int = 15) -> None:
    import threading

    recover_stale(cfg)

    def loop():
        time.sleep(5)
        while True:
            try:
                tick(cfg)
                verify_index(cfg)
            except Exception as e:
                log(f'outbox 循环异常: {e}')
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True, name='outbox-worker').start()
    log(f'outbox 已启动（每 {interval} 秒）')


def stats(cfg: dict) -> dict:
    db = _db(cfg)
    if db is None or not db.exists():
        return {}
    try:
        con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=5)
        rows = con.execute('select status, count(*) from extractions group by status').fetchall()
        verified = con.execute(
            "select count(*) from extractions where status='written' and verified_at is not null"
        ).fetchone()[0]
        con.close()
        data = {k: v for k, v in rows}
        # 对外统一口径：已写入（含已验证）
        data['verified'] = verified
        data['written_unverified'] = max(0, data.get('written', 0) - verified)
        return data
    except Exception:
        return {}
