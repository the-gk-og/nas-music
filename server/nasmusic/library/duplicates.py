"""Duplicate finder on Chromaprint fingerprints ("Song.flac" vs "Song (2).flac", FLAC vs MP3 copies).

Candidates come from shared AcoustID ids and from an inverted index of exact sub-fingerprint values (copies
of the same recording share many); each candidate pair is confirmed by the bit error rate of the best
alignment. An admin picks the copy to keep; the others are hidden (tracks.missing = 2, duplicate_hidden),
never deleted. Hiding moves playlist and queue entries to the kept copy and copies favourites/ratings/play
counts onto it (the hidden copy's own rows are left as they were)."""
from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import PurePosixPath

from nasmusic.core import now_iso
from nasmusic.library import chromaprint
from nasmusic.library.cleanup import lossless_note

MIN_SIMILARITY = 0.80        # re-encodes of one recording score > 0.9; unrelated audio ~0.5-0.6
MAX_DURATION_DIFF_S = 10
SAMPLE = slice(8, 8 + 200)   # ~25 s of sub-fingerprints after the first second
MIN_SHARED = 8
COMMON_VALUE = 40            # values shared by more tracks than this are noise (silence, test tones)
_COPY_SUFFIX = re.compile(r"(\s*\(\d+\)|\s+-\s*copy|\s+copy(\s*\d+)?|\s*\[\d+\])$", re.I)
_LOSSLESS = {"FLAC", "ALAC", "WAV", "AIFF", "APE", "WAVPACK"}


class _UF:
    def __init__(self):
        self.p: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def find_pairs(fps: dict[str, tuple], acoustids: dict[str, str] | None = None) -> dict[tuple[str, str], float]:
    """fps: {track_id: (raw uint32 array, duration_s)} -> {(a, b): similarity} for confirmed duplicate pairs."""
    index: dict[int, list[str]] = defaultdict(list)
    for tid, (raw, _d) in fps.items():
        for v in set(raw[SAMPLE].tolist()):
            index[v].append(tid)
    shared: Counter = Counter()
    for tids in index.values():
        if 1 < len(tids) <= COMMON_VALUE:
            for i, a in enumerate(tids):
                for b in tids[i + 1:]:
                    shared[(a, b) if a < b else (b, a)] += 1
    cands = {p for p, n in shared.items() if n >= MIN_SHARED}
    if acoustids:
        by_aid: dict[str, list[str]] = defaultdict(list)
        for tid, aid in acoustids.items():
            if aid and tid in fps:
                by_aid[aid].append(tid)
        for tids in by_aid.values():
            for i, a in enumerate(tids):
                for b in tids[i + 1:]:
                    cands.add((a, b) if a < b else (b, a))
    out = {}
    for a, b in cands:
        (ra, da), (rb, db) = fps[a], fps[b]
        if da and db and abs(da - db) > MAX_DURATION_DIFF_S:
            continue
        s = chromaprint.similarity(ra, rb)
        if s >= MIN_SIMILARITY:
            out[(a, b)] = round(s, 4)
    return out


def _quality(t: dict) -> tuple:
    """Higher = better copy to keep: visible, lossless, more bits/sample rate/bitrate, not a "(2)" copy,
    shorter path."""
    stem = PurePosixPath(t["path"]).stem
    real_lossless = (t["codec"] or "").upper() in _LOSSLESS and t.get("losslessVerdict") != "lossy_source"
    return (not t["hidden"], real_lossless, t["bitsPerSample"] or 0, t["sampleRate"] or 0,
            t["bitrate"] or 0, not _COPY_SUFFIX.search(stem), -len(t["path"]))


def groups(conn: sqlite3.Connection) -> list[dict]:
    """Duplicate groups among present and hidden tracks, biggest/most similar first."""
    rows = conn.execute(
        "SELECT f.track_id, f.raw, f.duration_s, l.acoustid FROM fingerprints f JOIN tracks t ON t.id = f.track_id "
        "LEFT JOIN acoustid_lookups l ON l.track_id = f.track_id "
        "WHERE t.missing IN (0, 2) AND f.raw IS NOT NULL AND f.mtime = t.mtime").fetchall()
    fps = {r["track_id"]: (chromaprint.from_blob(r["raw"]), r["duration_s"]) for r in rows}
    pairs = find_pairs(fps, {r["track_id"]: r["acoustid"] for r in rows})
    uf = _UF()
    for a, b in pairs:
        uf.union(a, b)
    members: dict[str, list[str]] = defaultdict(list)
    for tid in {x for p in pairs for x in p}:
        members[uf.find(tid)].append(tid)
    all_ids = [t for v in members.values() for t in v]
    info = {}
    for i in range(0, len(all_ids), 500):
        chunk = all_ids[i:i + 500]
        info.update({r["id"]: r for r in conn.execute(
            "SELECT t.id, t.path, t.title, t.duration_s, t.codec, t.bitrate, t.sample_rate, t.bits_per_sample, "
            "t.flac_bytes, t.missing, ar.name AS artist, al.title AS album, h.kept_id, c.verdict, c.source_guess, "
            "c.cutoff_hz FROM tracks t JOIN artists ar ON ar.id = t.artist_id JOIN albums al ON al.id = t.album_id "
            "LEFT JOIN lossless_checks c ON c.track_id = t.id AND c.mtime = t.mtime "
            f"LEFT JOIN duplicate_hidden h ON h.track_id = t.id WHERE t.id IN ({','.join('?' * len(chunk))})",
            chunk)})
    out = []
    for gid, tids in members.items():
        tracks = [{"id": t, "path": info[t]["path"], "title": info[t]["title"], "artist": info[t]["artist"],
                   "album": info[t]["album"], "durationS": info[t]["duration_s"], "codec": info[t]["codec"],
                   "bitrate": info[t]["bitrate"], "sampleRate": info[t]["sample_rate"],
                   "bitsPerSample": info[t]["bits_per_sample"], "bytes": info[t]["flac_bytes"],
                   "hidden": info[t]["missing"] == 2, "keptId": info[t]["kept_id"],
                   # v11 fake-lossless checker: ok | lossy_source | suspect | upsampled | unknown | error | None
                   "losslessVerdict": info[t]["verdict"],
                   "losslessNote": lossless_note(info[t]["verdict"], info[t]["source_guess"], info[t]["cutoff_hz"])}
                  for t in tids if t in info]
        if len(tracks) < 2:
            continue
        tracks.sort(key=_quality, reverse=True)
        for i, t in enumerate(tracks):
            t["recommended"] = i == 0
        sims = [s for (a, b), s in pairs.items() if a in tids and b in tids]
        out.append({"groupId": min(tids), "similarity": min(sims) if sims else None,
                    "hiddenCount": sum(t["hidden"] for t in tracks), "tracks": tracks})
    out.sort(key=lambda g: (g["hiddenCount"] == len(g["tracks"]) - 1, -len(g["tracks"]), g["tracks"][0]["path"]))
    return out


class DuplicateError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def resolve(conn: sqlite3.Connection, user_id: str, keep_id: str, hide_ids: list[str]) -> dict:
    """Keep `keep_id`, hide `hide_ids`. Playlists, queues and favourites move to the kept copy."""
    hide_ids = [h for h in dict.fromkeys(hide_ids) if h != keep_id]
    if not hide_ids:
        raise DuplicateError(422, "nothing to hide")
    ids = [keep_id, *hide_ids]
    found = {r[0] for r in conn.execute(f"SELECT id FROM tracks WHERE id IN ({','.join('?' * len(ids))})", ids)}
    if len(found) != len(ids):
        raise DuplicateError(404, "unknown track id")
    now = now_iso()
    conn.execute("BEGIN IMMEDIATE")
    try:
        _unhide(conn, [keep_id])
        for h in hide_ids:
            conn.execute("INSERT INTO duplicate_hidden (track_id, kept_id, hidden_by, hidden_at) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT(track_id) DO UPDATE SET kept_id = excluded.kept_id, "
                         "hidden_by = excluded.hidden_by, hidden_at = excluded.hidden_at", (h, keep_id, user_id, now))
            conn.execute("UPDATE tracks SET missing = 2 WHERE id = ?", (h,))
            conn.execute("UPDATE playlist_tracks SET track_id = ? WHERE track_id = ?", (keep_id, h))
            conn.execute("UPDATE queue_tracks SET track_id = ? WHERE track_id = ?", (keep_id, h))
            _merge_user_tracks(conn, h, keep_id)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"hidden": len(hide_ids), "keptId": keep_id}


def _merge_user_tracks(conn: sqlite3.Connection, src: str, dst: str) -> None:
    for r in conn.execute("SELECT * FROM user_tracks WHERE track_id = ?", (src,)).fetchall():
        conn.execute(
            "INSERT INTO user_tracks (user_id, track_id, starred_at, rating, blocked, play_count, skip_count, "
            "last_played) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(user_id, track_id) DO UPDATE SET "
            "starred_at = COALESCE(user_tracks.starred_at, excluded.starred_at), "
            "rating = COALESCE(user_tracks.rating, excluded.rating), "
            "blocked = MAX(user_tracks.blocked, excluded.blocked), "
            "play_count = user_tracks.play_count + excluded.play_count, "
            "skip_count = user_tracks.skip_count + excluded.skip_count, "
            "last_played = MAX(COALESCE(user_tracks.last_played, ''), COALESCE(excluded.last_played, ''))",
            (r["user_id"], dst, r["starred_at"], r["rating"], r["blocked"], r["play_count"], r["skip_count"],
             r["last_played"]))


def _unhide(conn: sqlite3.Connection, ids: list[str]) -> int:
    if not ids:
        return 0
    ph = ",".join("?" * len(ids))
    conn.execute(f"DELETE FROM duplicate_hidden WHERE track_id IN ({ph})", ids)
    # Back to present; if the file is gone the next scan marks it missing (1) again.
    return conn.execute(f"UPDATE tracks SET missing = 0 WHERE missing = 2 AND id IN ({ph})", ids).rowcount


def unhide(conn: sqlite3.Connection, track_ids: list[str]) -> dict:
    conn.execute("BEGIN IMMEDIATE")
    try:
        n = _unhide(conn, list(dict.fromkeys(track_ids)))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"unhidden": n}
