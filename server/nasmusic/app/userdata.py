"""Per-user synced settings, listening-context signals (docs/v2.md §10), and the user's own data:
summary, export and erasure. Admins have no endpoint that reads another user's context."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from nasmusic.core import now_iso

# signal -> EventContext fields it covers. Fields not listed here are dropped on ingest.
SIGNALS: dict[str, tuple[str, ...]] = {
    "listening": ("seeks", "volumeChanges", "repeatOfPrevious", "addedFrom", "sessionId", "sessionPosition"),
    "output": ("output",),
    "device": ("deviceKind", "network", "charging", "batteryPct", "screenOn"),
    "motion": ("motion", "cadenceSpm"),
    "location": ("place", "geohash5"),
    "weather": ("weather",),
    "search": ("searchQuery",),
    "time": ("localHour", "weekday", "holiday"),
    "health": ("heartRate", "workout"),
}
FIELD_SIGNAL = {f: s for s, fields in SIGNALS.items() for f in fields}
MAX_KEYS, MAX_VALUE_BYTES = 500, 20_000


# ---------- settings ----------


def get_settings(conn: sqlite3.Connection, uid: str) -> dict[str, Any]:
    out = {}
    for r in conn.execute("SELECT key, value FROM user_settings WHERE user_id = ? ORDER BY key", (uid,)):
        try:
            out[r["key"]] = json.loads(r["value"])
        except ValueError:
            continue
    return out


# v3 appearance settings (docs/v3-ux.md §7): validated when present; other keys are free-form.
ACCENTS = ("green", "blue", "purple", "pink", "orange", "red", "teal")
_ENUMS = {"appearance.animations": ("off", "quick", "expressive"), "appearance.defaultView": ("grid", "list"),
          "appearance.albumPage": ("big", "simple")}
_SHELF_LISTS = ("home.shelves", "home.hidden", "foryou.shelves", "foryou.hidden")


def _validate(key: str, value: Any) -> None:
    base = key.split(".", 2)[2] if key.startswith("device.") and key.count(".") >= 2 else key
    if base in _ENUMS and value not in _ENUMS[base]:
        raise ValueError(f"{base} must be one of {', '.join(_ENUMS[base])}")
    if base.startswith("library.view.") and value not in ("grid", "list"):
        raise ValueError(f"{base} must be grid or list")
    if base == "appearance.accent" and not (value in ACCENTS or (
            isinstance(value, str) and len(value) == 7 and value[0] == "#"
            and all(ch in "0123456789abcdefABCDEF" for ch in value[1:]))):
        raise ValueError(f"appearance.accent must be one of {', '.join(ACCENTS)} or #rrggbb")
    if base in _SHELF_LISTS and not (isinstance(value, list) and all(isinstance(x, str) for x in value)):
        raise ValueError(f"{base} must be a list of shelf ids")


def put_settings(conn: sqlite3.Connection, uid: str, changes: dict[str, Any]) -> dict[str, Any]:
    """Merge `changes` (None deletes a key). Raises ValueError for oversized or invalid input."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        for k, v in changes.items():
            if not isinstance(k, str) or not (0 < len(k) <= 200):
                raise ValueError("setting keys must be 1-200 characters")
            if v is None:
                conn.execute("DELETE FROM user_settings WHERE user_id = ? AND key = ?", (uid, k))
                continue
            _validate(k, v)
            raw = json.dumps(v)
            if len(raw) > MAX_VALUE_BYTES:
                raise ValueError(f"value for {k} is too large")
            conn.execute("INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?) "
                         "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value", (uid, k, raw))
        if conn.execute("SELECT COUNT(*) FROM user_settings WHERE user_id = ?", (uid,)).fetchone()[0] > MAX_KEYS:
            raise ValueError("too many settings")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return get_settings(conn, uid)


def signal_enabled(settings: dict[str, Any], device_id: str | None, signal: str) -> bool:
    if device_id:
        dev = settings.get(f"device.{device_id}.context.{signal}")
        if dev is not None:
            return bool(dev)
    return bool(settings.get(f"context.{signal}", False))


def filter_context(ctx: dict | None, settings: dict[str, Any], device_id: str | None) -> dict | None:
    """Keep only fields of enabled signals (server-side enforcement of the switches)."""
    if not ctx:
        return None
    out = {k: v for k, v in ctx.items()
           if k in FIELD_SIGNAL and k != "weather" and signal_enabled(settings, device_id, FIELD_SIGNAL[k])}
    return out or None


# ---------- data summary / export / erase ----------


def summary(conn: sqlite3.Connection, uid: str) -> dict:
    ev = conn.execute("SELECT COUNT(*), SUM(context IS NOT NULL), MIN(at), MAX(at) FROM events WHERE user_id = ?",
                      (uid,)).fetchone()
    settings = get_settings(conn, uid)
    signals = {s: {"enabled": signal_enabled(settings, None, s), "fields": list(f), "count": 0,
                   "first": None, "last": None} for s, f in SIGNALS.items()}
    for r in conn.execute("SELECT at, context FROM events WHERE user_id = ? AND context IS NOT NULL ORDER BY at",
                          (uid,)):
        try:
            ctx = json.loads(r["context"]) or {}
        except ValueError:
            continue
        for s in {FIELD_SIGNAL[k] for k in ctx if k in FIELD_SIGNAL}:
            d = signals[s]
            d["count"] += 1
            d["first"] = d["first"] or r["at"]
            d["last"] = r["at"]
    return {"events": {"count": ev[0], "withContext": ev[1] or 0, "first": ev[2], "last": ev[3]},
            "signals": signals}


def erase(conn: sqlite3.Connection, uid: str, signal: str) -> dict:
    """Erase one signal's fields from the user's events, or `all`: every event and the history derived from
    them (play/skip counts, last played, taste snapshots). Favourites, ratings, thumbs, playlists stay."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if signal == "all":
            deleted = conn.execute("DELETE FROM events WHERE user_id = ?", (uid,)).rowcount
            conn.execute("UPDATE user_tracks SET play_count = 0, skip_count = 0, last_played = NULL "
                         "WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM taste_snapshots WHERE user_id = ?", (uid,))
            conn.execute("UPDATE playback_state SET state = '{}' WHERE user_id = ?", (uid,))
            conn.execute("COMMIT")
            return {"eventsUpdated": 0, "eventsDeleted": deleted}
        fields = set(SIGNALS[signal])
        updated = 0
        for r in conn.execute("SELECT id, context FROM events WHERE user_id = ? AND context IS NOT NULL",
                              (uid,)).fetchall():
            try:
                ctx = json.loads(r["context"]) or {}
            except ValueError:
                ctx = {}
            kept = {k: v for k, v in ctx.items() if k not in fields}
            if kept != ctx:
                conn.execute("UPDATE events SET context = ? WHERE id = ?",
                             (json.dumps(kept) if kept else None, r["id"]))
                updated += 1
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"eventsUpdated": updated, "eventsDeleted": 0}


def export(conn: sqlite3.Connection, uid: str, user: dict, devices: list[dict], playlists: list[dict],
           queues: list[dict], suggestions: list[dict]) -> dict:
    events = []
    for r in conn.execute("SELECT * FROM events WHERE user_id = ? ORDER BY at", (uid,)):
        e = {"id": r["id"], "type": r["type"], "trackId": r["track_id"], "at": r["at"], "deviceId": r["device_id"],
             "positionS": r["position_s"], "durationS": r["duration_s"], "source": r["source"], "mixId": r["mix_id"],
             "rating": r["rating"], "offline": bool(r["offline"]), "receivedAt": r["received_at"]}
        if r["context"]:
            try:
                e["context"] = json.loads(r["context"])
            except ValueError:
                pass
        events.append(e)
    tracks = [{"trackId": r["track_id"], "path": r["path"], "starredAt": r["starred_at"], "rating": r["rating"],
               "blocked": bool(r["blocked"]), "playCount": r["play_count"], "skipCount": r["skip_count"],
               "lastPlayed": r["last_played"]}
              for r in conn.execute("SELECT ut.*, t.path FROM user_tracks ut JOIN tracks t ON t.id = ut.track_id "
                                    "WHERE ut.user_id = ? ORDER BY t.path", (uid,))]
    lyrics = [{"trackId": r["track_id"], "raw": r["raw"], "updatedAt": r["updated_at"]}
              for r in conn.execute("SELECT * FROM lyrics_overrides WHERE user_id = ?", (uid,))]
    return {"exportedAt": now_iso(), "user": user, "settings": get_settings(conn, uid), "devices": devices,
            "tracks": tracks, "events": events, "playlists": playlists, "queues": queues,
            "lyricsOverrides": lyrics, "suggestions": suggestions}
