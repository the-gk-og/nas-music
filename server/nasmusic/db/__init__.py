"""SQLite connection helper shared by server core and the AI engine."""
from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

SCHEMA_VERSION = 11


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    # WAL + NORMAL: commits no longer fsync (only checkpoints do). Still crash-safe for the database; a power cut
    # can lose the last moments of commits. Every write (play events, last_seen, uploads) gets much cheaper.
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def schema_sql() -> str:
    return resources.files(__package__).joinpath("schema.sql").read_text()


def init(conn: sqlite3.Connection, admin_username: str = "admin", admin_password_hash: str | None = None,
         backup: bool = True) -> None:
    """Create tables if missing, migrating an older database first. Safe to call on every start.

    A v1 database is upgraded to v2 (see nasmusic.db.migrations); its per-user data is assigned to
    `admin_username`, created with `admin_password_hash` ('!' = no password yet). v2 -> v3 is additive."""
    from nasmusic.db import migrations

    sql = schema_sql()
    from_v1 = migrations.migrate_v1_to_v2(conn, sql, admin_username, admin_password_hash, make_backup=backup)
    # One backup per start: taken by the first migration that runs; later steps in the same chain skip it.
    from_v2 = migrations.migrate_v2_to_v3(conn, sql, make_backup=backup and not from_v1)
    from_v3 = migrations.migrate_v3_to_v4(conn, sql, make_backup=backup and not (from_v1 or from_v2))
    from_v4 = migrations.migrate_v4_to_v5(conn, sql, make_backup=backup and not (from_v1 or from_v2 or from_v3))
    from_v5 = migrations.migrate_v5_to_v6(conn, sql,
                                         make_backup=backup and not (from_v1 or from_v2 or from_v3 or from_v4))
    from_v6 = migrations.migrate_v6_to_v7(
        conn, sql, make_backup=backup and not (from_v1 or from_v2 or from_v3 or from_v4 or from_v5))
    from_v7 = migrations.migrate_v7_to_v8(
        conn, sql, make_backup=backup and not (from_v1 or from_v2 or from_v3 or from_v4 or from_v5 or from_v6))
    from_v8 = migrations.migrate_v8_to_v9(
        conn, sql,
        make_backup=backup and not (from_v1 or from_v2 or from_v3 or from_v4 or from_v5 or from_v6 or from_v7))
    from_v9 = migrations.migrate_v9_to_v10(
        conn, sql, make_backup=backup and not (from_v1 or from_v2 or from_v3 or from_v4 or from_v5 or from_v6
                                               or from_v7 or from_v8))
    migrations.migrate_v10_to_v11(
        conn, sql, make_backup=backup and not (from_v1 or from_v2 or from_v3 or from_v4 or from_v5 or from_v6
                                               or from_v7 or from_v8 or from_v9))
    conn.executescript(sql)
    if conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
