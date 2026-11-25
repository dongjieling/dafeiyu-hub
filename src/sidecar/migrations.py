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

ARCHIVE_SCHEMA_VERSION = 1


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

    backup_path = ''
    if backup:
        backup_path = f'{db}.bak_{time.strftime("%Y%m%d%H%M%S")}'
        try:
            shutil.copy2(db, backup_path)
        except Exception as e:
            log(f'迁移前备份失败，终止迁移: {e}')
            return MigrationResult(False, _user_version(sqlite3.connect(db)), [], '', f'备份失败: {e}')

    con = sqlite3.connect(db, timeout=20)
    try:
        con.execute('PRAGMA journal_mode=WAL')
        version = _user_version(con)
        if version > ARCHIVE_SCHEMA_VERSION:
            raise IncompatibleArchive(
                f'归档库版本 {version} 高于代码支持的 {ARCHIVE_SCHEMA_VERSION}，请升级程序'
            )

        applied = []
        if version == 0:
            # 老库（v0.4.x）：补建缺失结构 + 补齐新增列 + 打版本
            con.executescript(BASE_SCHEMA)
            cols = {r[1] for r in con.execute('PRAGMA table_info(extractions)')}
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
            _set_version(con, ARCHIVE_SCHEMA_VERSION)
            applied.append('baseline-0-to-1')
        con.commit()
        return MigrationResult(True, ARCHIVE_SCHEMA_VERSION, applied, backup_path)
    except IncompatibleArchive as e:
        return MigrationResult(False, _user_version(con), [], backup_path, str(e))
    except Exception as e:
        return MigrationResult(False, 0, [], backup_path, str(e))
    finally:
        con.close()
