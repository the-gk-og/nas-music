"""Listening-feedback events: idempotent storage plus side effects on `tracks`."""
from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from nasmusic.core import iso, now_iso

log = logging.getLogger(__name__)

EventType = Literal["play", "skip", "star", "unstar", "rate", "thumb_up", "thumb_down"]
Source = Literal["mix", "library", "favourites", "more_like", "search"]


class Event(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    type: EventType
    trackId: str
    at: datetime
    positionS: float | None = None
    durationS: float | None = None
    source: Source | None = None
    mixId: str | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    offline: bool = False


class EventBatch(BaseModel):
    events: list[Event] = Field(max_length=1000)


def star(conn: sqlite3.Connection, track_id: str, at: str | None = None) -> bool:
    """Star a track (keeps the original star time if already starred). False if the track is unknown."""
    cur = conn.execute(
        "UPDATE tracks SET starred_at = COALESCE(starred_at, ?) WHERE id = ?", (at or now_iso(), track_id)
    )
    return cur.rowcount == 1


def unstar(conn: sqlite3.Connection, track_id: str) -> bool:
    return conn.execute("UPDATE tracks SET starred_at = NULL WHERE id = ?", (track_id,)).rowcount == 1


def _apply(conn: sqlite3.Connection, e: Event, at: str) -> None:
    if e.type == "star":
        star(conn, e.trackId, at)
    elif e.type == "unstar":
        unstar(conn, e.trackId)
    elif e.type == "rate":
        conn.execute("UPDATE tracks SET rating = ? WHERE id = ?", (e.rating, e.trackId))
    elif e.type == "thumb_down":
        conn.execute("UPDATE tracks SET blocked = 1 WHERE id = ?", (e.trackId,))
    elif e.type == "thumb_up":
        conn.execute("UPDATE tracks SET blocked = 0 WHERE id = ?", (e.trackId,))


def ingest(conn: sqlite3.Connection, events: list[Event], device_id: str | None) -> dict:
    """Store new events and apply their side effects in event-time order. Events whose id was already
    stored count as duplicates and have no effect. Events for unknown tracks are dropped (logged)."""
    accepted = duplicates = unknown = 0
    received = now_iso()
    for e in events:  # naive timestamps are taken as UTC
        if e.at.tzinfo is None:
            e.at = e.at.replace(tzinfo=UTC)
    ordered = sorted(events, key=lambda e: e.at)
    conn.execute("BEGIN IMMEDIATE")
    try:
        known = {r[0] for r in conn.execute(
            f"SELECT id FROM tracks WHERE id IN ({','.join('?' * len({e.trackId for e in ordered}))})",
            list({e.trackId for e in ordered}),
        )} if ordered else set()
        for e in ordered:
            if e.trackId not in known:
                unknown += 1
                continue
            at = iso(e.at)
            cur = conn.execute(
                "INSERT OR IGNORE INTO events (id, device_id, type, track_id, at, position_s, duration_s, source, "
                "mix_id, rating, offline, received_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (e.id, device_id, e.type, e.trackId, at, e.positionS, e.durationS, e.source, e.mixId,
                 e.rating, int(e.offline), received),
            )
            if cur.rowcount == 0:
                duplicates += 1
                continue
            accepted += 1
            _apply(conn, e, at)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    if unknown:
        log.warning("dropped %d events for unknown tracks", unknown)
    return {"accepted": accepted, "duplicates": duplicates}
