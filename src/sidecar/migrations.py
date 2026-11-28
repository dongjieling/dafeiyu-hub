"""archive.db 版本与迁移。

规则（ADR-001）：
- 迁移在监听端口前跑完
- 每次迁移前备份
- 幂等：重复跑不报错
- 库版本高于代码版本 → 拒绝（INCOMPATIBLE）
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from util import log

ARCHIVE_SCHEMA_VERSION = 3


class IncompatibleArchive(Exception):
    """库版本高于代码，拒绝操作。"""


@dataclass
class MigrationResult:
    ok: bool
    version: int
    applied: list[str]
    backup: str = ''
    error: str = ''


BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  session_key TEXT NOT NULL,
  persona_id TEXT,
  created_at REAL,
  last_active REAL,
  UNIQUE(source, session_key)
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL,
  role TEXT,
  content TEXT NOT NULL,
  created_at REAL NOT NULL,
  meta TEXT
);
CREATE TABLE IF NOT EXISTS extractions (
  id INTEGER PRIMARY KEY,
  message_id INTEGER,
  content TEXT NOT NULL,
  importance REAL DEFAULT 0.5,
  idempotency_key TEXT UNIQUE,
  lm_memory_id TEXT,
  status TEXT DEFAULT 'pending',
  error TEXT,
  created_at REAL NOT NULL,
  written_at REAL,
  retry_count INTEGER DEFAULT 0,
  next_retry_at REAL,
  last_error_at REAL,
  attempts INTEGER DEFAULT 0,
  verified_at REAL,
  topics TEXT
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


def _user_version(con: sqlite3.Connection) -> int:
    return int(con.execute('PRAGMA user_version').fetchone()[0])


def _set_version(con: sqlite3.Connection, version: int) -> None:
    con.execute(f'PRAGMA user_version = {int(version)}')


def backup_database(db: Path, dest: Path) -> None:
    """WAL 安全备份：使用 SQLite backup API。"""
    src = sqlite3.connect(db, timeout=20)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def archive_stats(db: Path) -> dict:
    """归档库的体检数据（只读）。"""
    if not db.exists():
        return {}
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=5)
    try:
        rows = dict(con.execute('select status, count(*) from extractions group by status').fetchall())
        total = sum(rows.values())
        no_topics = con.execute(
            "select count(*) from extractions where status != 'legacy' "
            "and coalesce(topics, '') = ''"
        ).fetchone()[0]
        last = con.execute('select max(created_at) from extractions').fetchone()[0]
        verified = con.execute(
            "select count(*) from extractions where status='written' and verified_at is not null"
        ).fetchone()[0]
        return {
            'total': total,
            'by_status': rows,
            'verified': verified,
            'no_topics': no_topics,
            'last_write_at': last or 0,
        }
    except Exception:
        return {}
    finally:
        con.close()


def prune_backups(db: Path, keep: int = 5) -> None:
    """只保留最近几份备份，避免无限增长。"""
    try:
        backups = sorted(db.parent.glob(db.name + '.bak_*'), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in backups[keep:]:
            old.unlink(missing_ok=True)
    except Exception as e:
        log(f'清理旧备份失败: {str(e)[:60]}')


def ensure_archive(db: Path, backup: bool = True) -> MigrationResult:
    """确保归档库结构与代码版本一致。"""
    if not db.exists():
        con = sqlite3.connect(db)
        try:
            con.execute('PRAGMA journal_mode=WAL')
            con.executescript(BASE_SCHEMA)
            _set_version(con, ARCHIVE_SCHEMA_VERSION)
            con.commit()
        finally:
            con.close()
        log(f'归档库初始化完成（version={ARCHIVE_SCHEMA_VERSION}）')
        return MigrationResult(True, ARCHIVE_SCHEMA_VERSION, ['init'])

    # 先看版本：已经是最新就不备份；只有需要迁移时才备份
    try:
        peek = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=10)
        current = _user_version(peek)
        peek.close()
    except Exception:
        current = 0
    if current == ARCHIVE_SCHEMA_VERSION:
        return MigrationResult(True, current, [], '')

    backup_path = ''
    if backup:
        backup_path = f'{db}.bak_{time.strftime("%Y%m%d%H%M%S")}'
        try:
            backup_database(db, Path(backup_path))
        except Exception as e:
            log(f'迁移前备份失败，终止迁移: {e}')
            return MigrationResult(False, _user_version(sqlite3.connect(db)), [], '', f'备份失败: {e}')
        prune_backups(db, keep=5)

    con = sqlite3.connect(db, timeout=20)
    try:
        con.execute('PRAGMA journal_mode=WAL')
        version = _user_version(con)
        if version > ARCHIVE_SCHEMA_VERSION:
            raise IncompatibleArchive(
                f'归档库版本 {version} 高于代码支持的 {ARCHIVE_SCHEMA_VERSION}，请升级程序'
            )

        applied = []

        def columns() -> set:
            return {r[1] for r in con.execute('PRAGMA table_info(extractions)')}

        def add_columns() -> None:
            cols = columns()
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

        if version == 0:
            con.executescript(BASE_SCHEMA)
            add_columns()
            applied.append('baseline-0-to-2')

        if version < 2:
            add_columns()
            # 历史遗留的 index_unconfirmed 归为 legacy_unconfirmed，不污染新指标
            moved = con.execute(
                "UPDATE extractions SET status='legacy_unconfirmed' "
                "WHERE status='index_unconfirmed' AND created_at < ?",
                (time.time() - 3600,),
            ).rowcount
            if moved:
                log(f'历史未确认记录已归档: {moved} 条')
                applied.append(f'legacy-unconfirmed-{moved}')

        if version < 3:
            add_columns()
            # 1) 旧的 verified 状态合并进 written，验证时间单独记
            old_verified = con.execute(
                "UPDATE extractions SET status='written', "
                "verified_at=coalesce(verified_at, created_at) WHERE status='verified'"
            ).rowcount
            if old_verified:
                applied.append(f'merge-verified-{old_verified}')

            # 2) 所有带 legacy 标记或旧加密格式的行统一为 legacy，不再参与指标
            legacy = con.execute(
                "UPDATE extractions SET status='legacy' "
                "WHERE status IN ('legacy_unconfirmed', 'index_unconfirmed') "
                "   OR (content LIKE 'enc:v1:%' AND coalesce(topics, '') = '')"
            ).rowcount
            if legacy:
                applied.append(f'normalize-legacy-{legacy}')

        if version < ARCHIVE_SCHEMA_VERSION:
            _set_version(con, ARCHIVE_SCHEMA_VERSION)
            applied.append(f'upgrade-{version}-to-{ARCHIVE_SCHEMA_VERSION}')

        con.commit()
        return MigrationResult(True, ARCHIVE_SCHEMA_VERSION, applied, backup_path)
    except IncompatibleArchive as e:
        return MigrationResult(False, _user_version(con), [], backup_path, str(e))
    except Exception as e:
        return MigrationResult(False, 0, [], backup_path, str(e))
    finally:
        con.close()
