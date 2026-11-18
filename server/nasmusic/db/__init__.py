"""SQLite connection helper shared by server core and the AI engine."""
from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

SCHEMA_VERSION = 2


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def schema_sql() -> str:
    return resources.files(__package__).joinpath("schema.sql").read_text()


def init(conn: sqlite3.Connection, admin_username: str = "admin", admin_password_hash: str | None = None,
         backup: bool = True) -> None:
    """Create tables if missing, migrating an older database first. Safe to call on every start.

    A v1 database is upgraded to v2 (see nasmusic.db.migrations); its per-user data is assigned to
    `admin_username`, created with `admin_password_hash` ('!' = no password yet)."""
    from nasmusic.db import migrations

    sql = schema_sql()
    migrations.migrate_v1_to_v2(conn, sql, admin_username, admin_password_hash, make_backup=backup)
    conn.executescript(sql)
    if conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
