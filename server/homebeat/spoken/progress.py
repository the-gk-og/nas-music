"""Per-user resume position for podcast episodes and audiobooks, synced across devices.

Newest write wins: a client sends the time it recorded the position (`updatedAt`, its own clock); a write
older than what is stored is ignored, so a phone coming back online cannot rewind progress made on another
device in the meantime. Nothing here is a listening *event*: the recommender/Wrapped/scrobbling never see it."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

KINDS = ("episode", "book")


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _norm_ts(v: str | None) -> str:
    """ISO-8601 UTC with milliseconds (one fixed format, so the text compares in time order)."""
    if not v:
        return _fmt(datetime.now(UTC))
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return _fmt(datetime.now(UTC))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    return _fmt(min(dt.astimezone(UTC), now))  # a clock running ahead must not pin a position forever


def as_api(r) -> dict:
    return {"kind": r["kind"], "id": r["item_id"], "positionS": r["position_s"], "durationS": r["duration_s"],
            "played": bool(r["played"]), "speed": r["speed"], "updatedAt": r["updated_at"],
            "deviceId": r["device_id"]}


def put(conn: sqlite3.Connection, uid: str, device_id: str | None, kind: str, item_id: str,
        position_s: float | None = None, duration_s: float | None = None, played: bool | None = None,
        speed: float | None = None, updated_at: str | None = None) -> tuple[bool, dict]:
    """Store a position (fields left None keep their value). Returns (applied, current state)."""
    ts = _norm_ts(updated_at)
    cur = conn.execute("SELECT * FROM spoken_progress WHERE user_id = ? AND kind = ? AND item_id = ?",
                       (uid, kind, item_id)).fetchone()
    if cur is not None and cur["updated_at"] > ts:
        return False, as_api(cur)
    pos = max(0.0, float(position_s)) if position_s is not None else (cur["position_s"] if cur else 0.0)
    dur = duration_s if duration_s is not None else (cur["duration_s"] if cur else None)
    pl = int(played) if played is not None else (cur["played"] if cur else 0)
    if played is None and dur and pos >= max(dur - 30, dur * 0.98):
        pl = 1  # listened to the end
    if played is True and position_s is None:
        pos = 0.0  # marking played resets the position (next play starts from the top)
    if played is False and position_s is None and cur is not None and cur["played"]:
        pos = 0.0
    sp = speed if speed is not None else (cur["speed"] if cur else None)
    conn.execute(
        "INSERT INTO spoken_progress (user_id, kind, item_id, position_s, duration_s, played, speed, device_id, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(user_id, kind, item_id) DO UPDATE SET "
        "position_s = excluded.position_s, duration_s = excluded.duration_s, played = excluded.played, "
        "speed = excluded.speed, device_id = excluded.device_id, updated_at = excluded.updated_at",
        (uid, kind, item_id, pos, dur, pl, sp, device_id, ts))
    return True, get(conn, uid, kind, item_id)


def get(conn: sqlite3.Connection, uid: str, kind: str, item_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM spoken_progress WHERE user_id = ? AND kind = ? AND item_id = ?",
                     (uid, kind, item_id)).fetchone()
    return as_api(r) if r else None


def many(conn: sqlite3.Connection, uid: str, kind: str, ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in conn.execute(f"SELECT * FROM spoken_progress WHERE user_id = ? AND kind = ? AND item_id IN "
                              f"({','.join('?' * len(chunk))})", (uid, kind, *chunk)):
            out[r["item_id"]] = as_api(r)
    return out


def since(conn: sqlite3.Connection, uid: str, ts: str | None, limit: int = 2000) -> list[dict]:
    rows = conn.execute("SELECT * FROM spoken_progress WHERE user_id = ? AND updated_at > ? "
                        "ORDER BY updated_at LIMIT ?", (uid, ts or "", limit))
    return [as_api(r) for r in rows]
