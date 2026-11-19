"""App bug reports (logs + crash dumps) sent to this server's admins. Size-limited, 20 per user per day,
kept 30 days (the daily `cleanup` job deletes older ones)."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

from nasmusic.core import iso, now_iso

MAX_BYTES = 2 * 1024 * 1024
PER_DAY = 20
KEEP_DAYS = 30


class TooMany(Exception):
    pass


def create(conn: sqlite3.Connection, uid: str, device_id: str | None, body: dict, size: int) -> str:
    since = iso(datetime.now(UTC) - timedelta(days=1))
    n = conn.execute("SELECT COUNT(*) FROM bug_reports WHERE user_id = ? AND created_at >= ?", (uid, since)).fetchone()[0]
    if n >= PER_DAY:
        raise TooMany()
    rid = uuid.uuid4().hex
    extra = body.get("extra")
    conn.execute(
        "INSERT INTO bug_reports (id, user_id, device_id, created_at, app_version, platform, description, logs, "
        "crash, extra, bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (rid, uid, device_id, now_iso(), body.get("appVersion"), body.get("platform"), body.get("description"),
         body["logs"], body.get("crash"), json.dumps(extra) if extra is not None else None, size))
    return rid


_SELECT = ("SELECT b.*, u.username, d.name AS device_name FROM bug_reports b JOIN users u ON u.id = b.user_id "
           "LEFT JOIN devices d ON d.id = b.device_id")


def _summary(r: sqlite3.Row) -> dict:
    created = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
    return {"id": r["id"], "user": {"id": r["user_id"], "username": r["username"]}, "deviceId": r["device_id"],
            "deviceName": r["device_name"], "createdAt": r["created_at"], "appVersion": r["app_version"],
            "platform": r["platform"], "description": r["description"], "hasCrash": bool(r["crash"]),
            "bytes": r["bytes"], "expiresAt": iso(created + timedelta(days=KEEP_DAYS))}


def list_all(conn: sqlite3.Connection) -> list[dict]:
    cutoff = iso(datetime.now(UTC) - timedelta(days=KEEP_DAYS))
    return [_summary(r) for r in conn.execute(_SELECT + " WHERE b.created_at >= ? ORDER BY b.created_at DESC",
                                              (cutoff,))]


def get(conn: sqlite3.Connection, rid: str) -> dict | None:
    r = conn.execute(_SELECT + " WHERE b.id = ?", (rid,)).fetchone()
    if r is None:
        return None
    try:
        extra = json.loads(r["extra"]) if r["extra"] else None
    except ValueError:
        extra = None
    return {**_summary(r), "logs": r["logs"], "crash": r["crash"], "extra": extra}


def delete(conn: sqlite3.Connection, rid: str) -> bool:
    return conn.execute("DELETE FROM bug_reports WHERE id = ?", (rid,)).rowcount == 1
