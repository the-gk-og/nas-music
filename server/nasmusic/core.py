"""Small shared helpers for server core: timestamps, the settings table, DB opening, AI config."""
from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import UTC, datetime
from typing import Any

from nasmusic import db
from nasmusic.ai.api import AIConfig
from nasmusic.config import Config


def iso(dt: datetime) -> str:
    """ISO-8601 UTC with a Z suffix, second precision. All server-written timestamps use this
    format so they sort correctly as text."""
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def now_iso() -> str:
    return iso(datetime.now(UTC))


def open_db(cfg: Config) -> sqlite3.Connection:
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    return conn


def init_db(cfg: Config) -> None:
    conn = open_db(cfg)
    try:
        db.init(conn)
        server_id(conn)
    finally:
        conn.close()


def get_setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except ValueError:
        return default


def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


def server_id(conn: sqlite3.Connection) -> str:
    sid = get_setting(conn, "server_id")
    if not sid:
        sid = secrets.token_hex(16)
        # INSERT OR IGNORE so two processes starting at once agree on one id.
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('server_id', ?)", (json.dumps(sid),))
        sid = get_setting(conn, "server_id")
    return sid


def ai_config(cfg: Config) -> AIConfig:
    return AIConfig(
        library_root=cfg.library_root,
        data_dir=cfg.data_dir,
        timezone=cfg.timezone,
        lastfm_api_key=cfg.lastfm_api_key,
    )
