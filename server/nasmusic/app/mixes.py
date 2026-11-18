"""Read mixes written by the AI engine (mixes / mix_tracks tables)."""
from __future__ import annotations

import sqlite3

from nasmusic.library import queries


def mix_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    mts = conn.execute(
        "SELECT track_id, reason FROM mix_tracks WHERE mix_id = ? ORDER BY position", (row["id"],)
    ).fetchall()
    tracks = queries.tracks_by_ids(conn, [m["track_id"] for m in mts])
    items = [{"track": tracks[m["track_id"]], "reason": m["reason"]} for m in mts if m["track_id"] in tracks]
    return {
        "id": row["id"], "kind": row["kind"], "title": row["title"], "createdAt": row["created_at"],
        "durationS": round(sum(i["track"]["durationS"] for i in items), 3), "tracks": items,
    }


def today(conn: sqlite3.Connection) -> list[dict]:
    """Active mixes from the nightly run (on-demand more_like mixes are excluded)."""
    rows = conn.execute(
        "SELECT * FROM mixes WHERE active = 1 AND kind != 'more_like' ORDER BY "
        "CASE kind WHEN 'daily' THEN 0 WHEN 'morning' THEN 1 WHEN 'evening' THEN 2 WHEN 'deep_cuts' THEN 3 "
        "ELSE 4 END, created_at DESC"
    ).fetchall()
    return [mix_dict(conn, r) for r in rows]


def get(conn: sqlite3.Connection, mix_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM mixes WHERE id = ?", (mix_id,)).fetchone()
    return mix_dict(conn, row) if row else None
