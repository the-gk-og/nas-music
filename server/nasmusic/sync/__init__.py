"""Offline sync manifest: what the phone should hold right now (active mixes + favourites)."""
from __future__ import annotations

import math
import sqlite3

from nasmusic.core import now_iso
from nasmusic.stream.transcode import OpusCache

SYNC_KBPS = 160            # Opus bitrate the app downloads with (the /stream default)
OGG_OVERHEAD = 1.02        # container overhead on top of the nominal bitrate, for estimates


def estimate_opus_bytes(duration_s: float, kbps: int = SYNC_KBPS) -> int:
    return math.ceil(duration_s * kbps * 1000 / 8 * OGG_OVERHEAD)


def manifest(conn: sqlite3.Connection, uid: str, favourites_format: str = "opus",
             opus: OpusCache | None = None) -> dict:
    """Mix tracks are always Opus; favourites use `favourites_format`. A track that is both a favourite
    and in a mix appears once, as a favourite (so a FLAC favourite isn't downgraded), with its mixIds.
    `bytes` is what /stream returns for that format: exact for FLAC and for Opus already in the
    transcode cache, otherwise a duration x bitrate estimate (bytesExact=false)."""
    items: dict[str, dict] = {}
    files: dict[str, sqlite3.Row] = {}
    for r in conn.execute(
        "SELECT mt.track_id, mt.mix_id, t.duration_s, t.flac_bytes, t.mtime FROM mix_tracks mt "
        "JOIN mixes m ON m.id = mt.mix_id JOIN tracks t ON t.id = mt.track_id "
        "WHERE m.user_id = ? AND m.active = 1 AND t.missing = 0 ORDER BY m.created_at DESC, mt.mix_id, mt.position",
        (uid,),
    ):
        files[r["track_id"]] = r
        it = items.setdefault(r["track_id"], {"trackId": r["track_id"], "format": "opus", "why": "mix",
                                             "mixIds": []})
        if r["mix_id"] not in it["mixIds"]:
            it["mixIds"].append(r["mix_id"])
    for r in conn.execute(
        "SELECT t.id AS track_id, t.duration_s, t.flac_bytes, t.mtime FROM user_tracks ut "
        "JOIN tracks t ON t.id = ut.track_id "
        "WHERE ut.user_id = ? AND ut.starred_at IS NOT NULL AND t.missing = 0 ORDER BY ut.starred_at DESC, t.id",
        (uid,),
    ):
        files[r["track_id"]] = r
        it = items.get(r["track_id"])
        if it is None:
            items[r["track_id"]] = {"trackId": r["track_id"], "format": favourites_format, "why": "favourite",
                                    "mixIds": []}
        else:
            it["format"], it["why"] = favourites_format, "favourite"
    for tid, it in items.items():
        f = files[tid]
        it["durationS"] = f["duration_s"]
        if it["format"] == "flac":
            it["bytes"], it["bytesExact"] = f["flac_bytes"], True
            continue
        size = None
        if opus is not None:
            try:
                size = opus.path_for(tid, SYNC_KBPS, f["mtime"]).stat().st_size
            except OSError:
                size = None
        if size is not None:
            it["bytes"], it["bytesExact"] = size, True
        else:
            it["bytes"], it["bytesExact"] = estimate_opus_bytes(f["duration_s"]), False
    return {"generatedAt": now_iso(), "items": list(items.values())}


def sync_tracks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every present track that can appear in any active user's manifest (mix tracks first, then favourites)."""
    return conn.execute(
        "SELECT t.id, t.path, t.mtime, "
        "  EXISTS (SELECT 1 FROM user_tracks ut JOIN users u ON u.id = ut.user_id "
        "          WHERE ut.track_id = t.id AND ut.starred_at IS NOT NULL AND u.disabled = 0) AS fav "
        "FROM tracks t WHERE t.missing = 0 AND (t.id IN ("
        "  SELECT mt.track_id FROM mix_tracks mt JOIN mixes m ON m.id = mt.mix_id JOIN users u ON u.id = m.user_id "
        "  WHERE m.active = 1 AND u.disabled = 0"
        ") OR t.id IN (SELECT ut.track_id FROM user_tracks ut JOIN users u ON u.id = ut.user_id "
        "              WHERE ut.starred_at IS NOT NULL AND u.disabled = 0)) ORDER BY fav, t.id"
    ).fetchall()
