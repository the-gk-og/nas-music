"""Per-user data access for the AI engine (schema v2, docs/v2.md §2 and §10).

Everything about listening is per user. The engine reads one user's `events` and `user_tracks` at a
time, and writes `mixes`, `taste_snapshots` and `suggestions` with that `user_id`. The only
cross-user data it ever reads is an anonymous popularity count per track (`popularity`), used as a
cold-start prior for new users.

Transitional: until server core's v1 → v2 migration lands, a database without `user_tracks` is
treated as a single implicit user (`user_id = None`) with the v1 columns (`tracks.starred_at` ...).
Remove `legacy` once every deployment is on schema v2.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from functools import cached_property
from typing import Any

log = logging.getLogger("homebeat.ai.store")


@dataclass
class UserTrack:
    starred: bool
    rating: int
    blocked: bool
    play_count: int
    skip_count: int
    last_played: str | None


class Store:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ------------------------------------------------------------ schema detection

    def _columns(self, table: str) -> set[str]:
        return {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}

    @cached_property
    def legacy(self) -> bool:
        """True on a pre-migration v1 database (no per-user tables)."""
        return not self._columns("user_tracks")

    @cached_property
    def has_context(self) -> bool:
        return "context" in self._columns("events")

    @cached_property
    def has_user_settings(self) -> bool:
        return bool(self._columns("user_settings"))

    # ------------------------------------------------------------ users

    def active_users(self) -> list[str | None]:
        if self.legacy:
            return [None]
        return [r[0] for r in self.conn.execute(
            "SELECT id FROM users WHERE disabled = 0 ORDER BY created_at, id")]

    def user_exists(self, user_id: str | None) -> bool:
        if self.legacy:
            return user_id is None
        return self.conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is not None

    def user_settings(self, user_id: str | None) -> dict[str, Any]:
        """The user's `user_settings` rows (JSON values), or {}."""
        if user_id is None or not self.has_user_settings:
            return {}
        out: dict[str, Any] = {}
        for key, value in self.conn.execute("SELECT key, value FROM user_settings WHERE user_id = ?", (user_id,)):
            try:
                out[key] = json.loads(value)
            except (TypeError, ValueError):
                out[key] = value
        return out

    # ------------------------------------------------------------ per-user reads

    def user_tracks(self, user_id: str | None) -> dict[str, UserTrack]:
        if self.legacy:
            rows = self.conn.execute(
                "SELECT id, starred_at, rating, blocked, 0, 0, NULL FROM tracks "
                "WHERE starred_at IS NOT NULL OR rating IS NOT NULL OR blocked != 0")
        else:
            rows = self.conn.execute(
                "SELECT track_id, starred_at, rating, blocked, play_count, skip_count, last_played "
                "FROM user_tracks WHERE user_id = ?", (user_id,))
        return {r[0]: UserTrack(r[1] is not None, int(r[2] or 0), bool(r[3]), int(r[4] or 0), int(r[5] or 0), r[6])
                for r in rows}

    def events(self, user_id: str | None) -> list[tuple]:
        """(track_id, type, at, position_s, duration_s, context JSON or None), oldest first."""
        ctx = "context" if self.has_context else "NULL"
        if self.legacy:
            return self.conn.execute(
                f"SELECT track_id, type, at, position_s, duration_s, {ctx} FROM events "
                "ORDER BY at, received_at").fetchall()
        return self.conn.execute(
            f"SELECT track_id, type, at, position_s, duration_s, {ctx} FROM events WHERE user_id = ? "
            "ORDER BY at, received_at", (user_id,)).fetchall()

    def radio_feedback(self, user_id: str | None) -> list[tuple]:
        """Steering of AI radio (schema v6): (track_id, action, scope, at, context JSON), oldest first.
        Empty before v6 or on legacy databases."""
        if self.legacy or not self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'radio_feedback'").fetchone():
            return []
        return self.conn.execute("SELECT track_id, action, scope, at, context FROM radio_feedback WHERE user_id = ? "
                                 "ORDER BY at", (user_id,)).fetchall()

    # ------------------------------------------------------------ the one cross-user read

    def popularity(self, exclude_user: str | None) -> dict[str, int]:
        """Anonymous play counts per track summed over *other* users. No user ids leave this function."""
        if self.legacy:
            return {}
        return {r[0]: int(r[1]) for r in self.conn.execute(
            "SELECT track_id, SUM(play_count) FROM user_tracks WHERE user_id != ? AND play_count > 0 "
            "GROUP BY track_id", (exclude_user,))}

    # ------------------------------------------------------------ owner columns for writes

    def owner(self, user_id: str | None) -> tuple[str, str, tuple]:
        """(', user_id', ', ?', (user_id,)) on v2; empty on legacy. For INSERT statements."""
        if self.legacy:
            return "", "", ()
        return ", user_id", ", ?", (user_id,)

    def where_owner(self, user_id: str | None, alias: str = "") -> tuple[str, tuple]:
        """(' AND user_id = ?', (user_id,)) on v2; empty on legacy. For WHERE clauses."""
        if self.legacy:
            return "", ()
        col = f"{alias}.user_id" if alias else "user_id"
        return f" AND {col} = ?", (user_id,)
