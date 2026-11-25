"""Offline sync manifest: what the phone should hold right now (active mixes + favourites)."""
from __future__ import annotations

import math
import sqlite3

from homebeat.core import now_iso
from homebeat.stream.transcode import OpusCache

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
    from homebeat.db import family

    for tid in family.hidden_among(conn, uid, list(items)):  # family mode: never download hidden tracks
        del items[tid]
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


# ---------- smart downloads (docs: android smart downloads) ----------

SMART_REASONS = ("favourite", "mix", "week_mix", "recent", "release_radar", "foryou", "predicted")


def smart_candidates(conn: sqlite3.Connection, uid: str, likely: dict[str, float],
                     foryou_ids: list[str] = (), radar_album_ids: list[str] = (),
                     limit: int = 1500, recent_days: int = 30, now: str | None = None) -> dict:
    """Ranked candidates for the phone's rolling offline set. Each present track appears once with every
    reason that applies: favourite, mix (active mixes), week_mix (daily mixes of the last 7 days), recent
    (played in the last `recent_days`), release_radar (library albums Release Radar found), foryou ("because
    you played" shelves) and predicted (the recommender's top picks). `score` is the AI's likelihood (0..1,
    0 when unknown). The phone decides what fits its budget; this only gathers and ranks."""
    from datetime import datetime, timedelta, timezone

    stamp = now or now_iso()
    ref = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    week_ago = (ref - timedelta(days=7)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent_since = (ref - timedelta(days=recent_days)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    reasons: dict[str, list[str]] = {}

    def add(tid: str, why: str) -> None:
        r = reasons.setdefault(tid, [])
        if why not in r:
            r.append(why)

    for r in conn.execute("SELECT track_id FROM user_tracks WHERE user_id = ? AND starred_at IS NOT NULL "
                          "ORDER BY starred_at DESC", (uid,)):
        add(r[0], "favourite")
    for r in conn.execute(
            "SELECT mt.track_id, m.active FROM mix_tracks mt JOIN mixes m ON m.id = mt.mix_id "
            "WHERE m.user_id = ? AND (m.active = 1 OR (m.kind = 'daily' AND m.created_at >= ?)) "
            "ORDER BY m.active DESC, m.created_at DESC, mt.position", (uid, week_ago)):
        add(r[0], "mix" if r[1] else "week_mix")
    plays: dict[str, tuple[int, str | None]] = {}
    for r in conn.execute(
            "SELECT track_id, play_count, last_played FROM user_tracks WHERE user_id = ? AND last_played >= ? "
            "AND blocked = 0 ORDER BY last_played DESC LIMIT 500", (uid, recent_since)):
        add(r[0], "recent")
        plays[r[0]] = (r[1], r[2])
    for tid in foryou_ids:
        add(tid, "foryou")
    for aid in radar_album_ids:
        for r in conn.execute("SELECT id FROM tracks WHERE album_id = ? AND missing = 0 ORDER BY disc_no, track_no",
                              (aid,)):
            add(r[0], "release_radar")
    top = sorted(likely.items(), key=lambda kv: -kv[1])
    for tid, _ in top[:max(0, limit // 3)]:
        add(tid, "predicted")

    # Drop missing / blocked tracks, then fill in plays and file info for the rest.
    ids = list(reasons)
    rows: dict[str, sqlite3.Row] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in conn.execute(
                f"SELECT t.id, t.duration_s, t.flac_bytes, COALESCE(ut.blocked, 0) AS blocked, "
                f"COALESCE(ut.play_count, 0) AS plays, ut.last_played FROM tracks t "
                f"LEFT JOIN user_tracks ut ON ut.track_id = t.id AND ut.user_id = ? "
                f"WHERE t.missing = 0 AND t.id IN ({','.join('?' * len(chunk))})", (uid, *chunk)):
            if not r["blocked"]:
                rows[r["id"]] = r
    items = []
    for tid in ids:
        r = rows.get(tid)
        if r is None:
            continue
        items.append({"trackId": tid, "reasons": reasons[tid], "score": float(likely.get(tid, 0.0)),
                      "plays": r["plays"], "lastPlayedAt": r["last_played"], "durationS": r["duration_s"],
                      "flacBytes": r["flac_bytes"]})
    # Favourites and current mixes first, then by likelihood; the phone re-ranks anyway.
    first = {"favourite": 0, "mix": 1}
    items.sort(key=lambda it: (min(first.get(w, 2) for w in it["reasons"]), -it["score"]))
    return {"generatedAt": stamp, "items": items[:limit]}
