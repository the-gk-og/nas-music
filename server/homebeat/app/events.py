"""Listening-feedback events: idempotent storage plus side effects on the user's `user_tracks` row."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from homebeat.app import userdata
from homebeat.core import iso, now_iso

log = logging.getLogger(__name__)

EventType = Literal["play", "skip", "star", "unstar", "rate", "thumb_up", "thumb_down", "thumb_clear"]
Source = Literal["mix", "library", "favourites", "more_like", "search"]
MAX_CONTEXT_BYTES = 20_000


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
    context: dict[str, Any] | None = None


class EventBatch(BaseModel):
    events: list[Event] = Field(max_length=1000)


def _ensure_row(conn: sqlite3.Connection, uid: str, track_id: str) -> None:
    conn.execute("INSERT OR IGNORE INTO user_tracks (user_id, track_id) VALUES (?, ?)", (uid, track_id))


def star(conn: sqlite3.Connection, uid: str, track_id: str, at: str | None = None) -> bool:
    """Star a track (keeps the original star time if already starred). False if the track is unknown."""
    if not conn.execute("SELECT 1 FROM tracks WHERE id = ?", (track_id,)).fetchone():
        return False
    _ensure_row(conn, uid, track_id)
    conn.execute("UPDATE user_tracks SET starred_at = COALESCE(starred_at, ?) WHERE user_id = ? AND track_id = ?",
                 (at or now_iso(), uid, track_id))
    return True


def unstar(conn: sqlite3.Connection, uid: str, track_id: str) -> bool:
    if not conn.execute("SELECT 1 FROM tracks WHERE id = ?", (track_id,)).fetchone():
        return False
    conn.execute("UPDATE user_tracks SET starred_at = NULL WHERE user_id = ? AND track_id = ?", (uid, track_id))
    return True


def _apply(conn: sqlite3.Connection, uid: str, e: Event, at: str) -> None:
    if e.type == "star":
        star(conn, uid, e.trackId, at)
        return
    if e.type == "unstar":
        unstar(conn, uid, e.trackId)
        return
    _ensure_row(conn, uid, e.trackId)
    sets = {
        "rate": ("rating = ?", (e.rating,)),
        "thumb_down": ("blocked = 1", ()),
        "thumb_up": ("blocked = 0", ()),
        "thumb_clear": ("blocked = 0", ()),
        "play": ("play_count = play_count + 1, last_played = MAX(COALESCE(last_played, ''), ?)", (at,)),
        "skip": ("skip_count = skip_count + 1", ()),
    }
    clause, params = sets[e.type]
    conn.execute(f"UPDATE user_tracks SET {clause} WHERE user_id = ? AND track_id = ?", (*params, uid, e.trackId))


def ingest(conn: sqlite3.Connection, events: list[Event], device_id: str | None, uid: str,
           weather=None) -> dict:
    """Store new events for user `uid` and apply their side effects in event-time order. Events whose id was
    already stored count as duplicates and have no effect. Events for unknown tracks are dropped (logged).
    `context` is filtered by the user's data switches; `weather` (a homebeat.app.weather.Weather) fills
    context.weather from geohash5 when that switch is on (geohash5 itself is kept only if location is on)."""
    accepted = duplicates = unknown = 0
    received = now_iso()
    for e in events:  # naive timestamps are taken as UTC
        if e.at.tzinfo is None:
            e.at = e.at.replace(tzinfo=UTC)
    ordered = sorted(events, key=lambda e: e.at)
    settings = userdata.get_settings(conn, uid) if any(e.context for e in ordered) else {}
    contexts: dict[str, str | None] = {}
    for e in ordered:
        if not e.context:
            continue
        ctx = userdata.filter_context(e.context, settings, device_id)
        gh = e.context.get("geohash5")
        if weather is not None and gh and userdata.signal_enabled(settings, device_id, "weather"):
            w = weather.at(gh, e.at)
            if w:
                ctx = {**(ctx or {}), "weather": w}
        raw = json.dumps(ctx) if ctx else None
        contexts[e.id] = raw if raw is None or len(raw) <= MAX_CONTEXT_BYTES else None
    conn.execute("BEGIN IMMEDIATE")
    try:
        ids = list({e.trackId for e in ordered})
        known = {r[0] for r in conn.execute(
            f"SELECT id FROM tracks WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else set()
        for e in ordered:
            if e.trackId not in known:
                unknown += 1
                continue
            at = iso(e.at)
            cur = conn.execute(
                "INSERT OR IGNORE INTO events (id, user_id, device_id, type, track_id, at, position_s, duration_s, "
                "source, mix_id, rating, offline, received_at, context) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (e.id, uid, device_id, e.type, e.trackId, at, e.positionS, e.durationS, e.source, e.mixId,
                 e.rating, int(e.offline), received, contexts.get(e.id)),
            )
            if cur.rowcount == 0:
                duplicates += 1
                continue
            accepted += 1
            _apply(conn, uid, e, at)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    if unknown:
        log.warning("dropped %d events for unknown tracks", unknown)
    return {"accepted": accepted, "duplicates": duplicates}
