"""Library + server health for the admin console. Read-only towards the music library: the only
destructive actions are removing missing tracks from the DATABASE (after a backup) and deleting cache files."""
from __future__ import annotations

import json
import os
import platform
import posixpath
import re
import shutil
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nasmusic import catalog
from nasmusic.config import Config
from nasmusic.core import iso
from nasmusic.library import art

HEALTH_TRACK = """SELECT t.id, t.path, t.title, t.album_id, t.duration_s, t.flac_bytes, t.codec, t.missing, t.mbid,
       t.year, t.genre, t.track_no, ar.name AS artist, al.title AS album
FROM tracks t JOIN artists ar ON ar.id = t.artist_id JOIN albums al ON al.id = t.album_id"""


def health_track(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "path": r["path"], "title": r["title"], "artist": r["artist"], "album": r["album"],
            "albumId": r["album_id"], "durationS": r["duration_s"], "fileBytes": r["flac_bytes"],
            "codec": r["codec"], "missing": bool(r["missing"])}


# ---------- analysis progress (shared by /admin/status and the Jinja dashboard) ----------


def analysis_progress(conn: sqlite3.Connection, configured: str, running: bool = False) -> dict:
    status: dict = {}
    srow = conn.execute("SELECT value FROM settings WHERE key = 'ai.status.model_version'").fetchone()
    if srow:
        try:
            status = json.loads(srow[0]) or {}
        except (ValueError, TypeError):
            status = {}
    mv = status.get("analysing")
    if not mv:
        row = conn.execute("SELECT model_version FROM features ORDER BY analysed_at DESC LIMIT 1").fetchone()
        mv = row[0] if row else configured
    counts = {r["status"]: r["n"] for r in conn.execute(
        "SELECT j.status, COUNT(*) AS n FROM analysis_jobs j JOIN tracks t ON t.id = j.track_id "
        "WHERE j.model_version = ? AND t.missing = 0 GROUP BY j.status", (mv,))}
    analysed = conn.execute("SELECT COUNT(*) FROM features f JOIN tracks t ON t.id = f.track_id "
                            "WHERE f.model_version = ? AND t.missing = 0", (mv,)).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM tracks WHERE missing = 0").fetchone()[0]
    hour_ago = iso(datetime.now(UTC) - timedelta(hours=1))
    rate = conn.execute("SELECT COUNT(*) FROM features WHERE model_version = ? AND analysed_at >= ?",
                        (mv, hour_ago)).fetchone()[0]
    remaining = max(0, total - analysed - counts.get("failed", 0))
    return {
        "model": mv, "configured": configured, "fallbackReason": status.get("fallback_reason"),
        "analysed": analysed, "total": total, "pending": counts.get("pending", 0), "failed": counts.get("failed", 0),
        "pct": round(100 * analysed / total, 1) if total else 0.0, "running": running,
        "ratePerHour": float(rate) if rate else None,
        "etaS": round(remaining / rate * 3600) if rate and remaining else (0 if not remaining else None),
    }


def failed_analysis(conn: sqlite3.Connection, model: str) -> list[dict]:
    rows = conn.execute(HEALTH_TRACK + " JOIN analysis_jobs j ON j.track_id = t.id "
                        "WHERE j.status = 'failed' AND j.model_version = ? ORDER BY t.path", (model,)).fetchall()
    errs = {r["track_id"]: r for r in conn.execute(
        "SELECT track_id, error, updated_at FROM analysis_jobs WHERE status = 'failed' AND model_version = ?", (model,))}
    return [{"track": health_track(r), "error": errs[r["id"]]["error"], "modelVersion": model,
             "updatedAt": errs[r["id"]]["updated_at"]} for r in rows]


def retry_failed(conn: sqlite3.Connection, model: str, track_ids: list[str] | None) -> int:
    """Forget failures so the analyser picks the tracks up again (it skips recent failures)."""
    if track_ids is None:
        return conn.execute("DELETE FROM analysis_jobs WHERE status = 'failed' AND model_version = ?",
                            (model,)).rowcount
    n = 0
    for tid in track_ids:
        n += conn.execute("DELETE FROM analysis_jobs WHERE status = 'failed' AND model_version = ? AND track_id = ?",
                          (model, tid)).rowcount
    return n


# ---------- library ----------


def missing_tracks(conn: sqlite3.Connection, offset: int = 0, limit: int = 10_000) -> list[dict]:
    return [health_track(r) for r in conn.execute(
        HEALTH_TRACK + " WHERE t.missing = 1 ORDER BY t.path LIMIT ? OFFSET ?", (limit, offset))]


def remove_missing(conn: sqlite3.Connection, track_ids: list[str] | None) -> int:
    """Delete missing tracks (only ones marked missing) and everything referring to them; keep playlists and
    queues dense with the current track kept current."""
    ids = [r[0] for r in conn.execute("SELECT id FROM tracks WHERE missing = 1")]
    if track_ids is not None:
        wanted = set(track_ids)
        ids = [i for i in ids if i in wanted]
    if not ids:
        return 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        delete_track_rows(conn, ids)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return len(ids)


def detach_tracks(conn: sqlite3.Connection, ids: list[str]) -> None:
    """Take tracks out of every playlist, queue and mix (inside the caller's transaction); queues keep their
    current track current."""
    if not ids:
        return
    from nasmusic.app import collections as coll
    from nasmusic.app import playlists

    gone = set(ids)
    playlists.remove_tracks_everywhere(conn, ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for (qid,) in conn.execute(f"SELECT DISTINCT queue_id FROM queue_tracks WHERE track_id IN ({q})",
                                   chunk).fetchall():
            row = conn.execute("SELECT current_index FROM queues WHERE id = ?", (qid,)).fetchone()
            old = coll._queue_ids(conn, qid)
            cur = old[row[0]] if 0 <= row[0] < len(old) else None
            keep = [t for t in old if t not in gone]
            new_cur = keep.index(cur) if cur in keep else min(row[0], max(len(keep) - 1, 0))
            coll._write_queue(conn, qid, keep, new_cur)
        for (mid,) in conn.execute(f"SELECT DISTINCT mix_id FROM mix_tracks WHERE track_id IN ({q})",
                                   chunk).fetchall():
            rows = conn.execute("SELECT * FROM mix_tracks WHERE mix_id = ? ORDER BY position", (mid,)).fetchall()
            conn.execute("DELETE FROM mix_tracks WHERE mix_id = ?", (mid,))
            conn.executemany("INSERT INTO mix_tracks (mix_id, position, track_id, score, reason) VALUES (?,?,?,?,?)",
                             [(mid, n, r["track_id"], r["score"], r["reason"])
                              for n, r in enumerate(x for x in rows if x["track_id"] not in gone)])


def delete_track_rows(conn: sqlite3.Connection, ids: list[str]) -> None:
    """Delete tracks and everything referring to them (inside the caller's transaction), then albums and
    artists nothing references any more."""
    if not ids:
        return
    detach_tracks(conn, ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for table in ("user_tracks", "events", "features", "analysis_jobs", "lyrics_overrides", "lyrics_cache",
                      "track_dj", "blend_tracks", "jam_requests", "smart_playlist_tracks", "scrobble_queue"):
            conn.execute(f"DELETE FROM {table} WHERE track_id IN ({q})", chunk)
        conn.execute(f"DELETE FROM tracks WHERE id IN ({q})", chunk)
    conn.execute("DELETE FROM albums WHERE id NOT IN (SELECT DISTINCT album_id FROM tracks)")
    conn.execute("DELETE FROM artists WHERE id NOT IN (SELECT DISTINCT artist_id FROM albums) "
                 "AND id NOT IN (SELECT DISTINCT artist_id FROM tracks)")


def duplicates(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(HEALTH_TRACK + " WHERE t.missing = 0 ORDER BY t.path").fetchall()
    groups: list[dict] = []
    covered: set[str] = set()
    by_mbid: dict[str, list] = {}
    for r in rows:
        if r["mbid"]:
            by_mbid.setdefault(r["mbid"].lower(), []).append(r)
    for mbid, rs in sorted(by_mbid.items()):
        if len(rs) > 1:
            groups.append({"reason": "mbid", "key": mbid, "tracks": [health_track(r) for r in rs]})
            covered |= {r["id"] for r in rs}
    by_name: dict[str, list] = {}
    for r in rows:
        if r["id"] not in covered:
            by_name.setdefault(catalog.match_key(r["artist"], r["title"]), []).append(r)
    for key, rs in sorted(by_name.items()):
        rs = sorted(rs, key=lambda r: r["duration_s"] or 0)
        cluster = [rs[0]]
        for r in rs[1:] + [None]:
            if r is not None and abs((r["duration_s"] or 0) - (cluster[-1]["duration_s"] or 0)) <= 2.0:
                cluster.append(r)
                continue
            if len(cluster) > 1:
                groups.append({"reason": "name", "key": key, "tracks": [health_track(x) for x in cluster]})
            if r is not None:
                cluster = [r]
    return groups


def missing_tags(conn: sqlite3.Connection, offset: int = 0, limit: int = 10_000) -> list[dict]:
    """Heuristics on what the scanner stored: 'Unknown Artist'/'Unknown Album' placeholders, a title equal to the
    file name, and empty year/genre/track number."""
    out = []
    for r in conn.execute(HEALTH_TRACK + " WHERE t.missing = 0 ORDER BY t.path"):
        miss = []
        if r["artist"] == "Unknown Artist":
            miss.append("artist")
        if r["album"] == "Unknown Album" or (r["artist"] == "Unknown Artist"
                                             and r["album"] == posixpath.basename(posixpath.dirname(r["path"]))):
            miss.append("album")
        if r["title"] == Path(r["path"]).stem:
            miss.append("title")
        if r["year"] is None:
            miss.append("year")
        if not r["genre"]:
            miss.append("genre")
        if r["track_no"] is None:
            miss.append("trackNo")
        if miss:
            out.append({"track": health_track(r), "missing": miss})
    return out[offset:offset + limit]


def missing_art(conn: sqlite3.Connection, data_dir: Path) -> list[dict]:
    from nasmusic.library import queries

    rows = conn.execute(queries.ALBUM_SELECT + " ORDER BY ar.sort_name COLLATE NOCASE, al.title COLLATE NOCASE")
    return [queries.album_dict(r, data_dir) for r in rows if not art.has_art(data_dir, r["art_path"], r["id"])]


def unanalysed(conn: sqlite3.Connection, model: str, offset: int = 0, limit: int = 10_000) -> list[dict]:
    return [health_track(r) for r in conn.execute(
        HEALTH_TRACK + " WHERE t.missing = 0 AND NOT EXISTS (SELECT 1 FROM features f WHERE f.track_id = t.id "
        "AND f.model_version = ?) ORDER BY t.path LIMIT ? OFFSET ?", (model, limit, offset))]


def unanalysed_count(conn: sqlite3.Connection, model: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM tracks t WHERE t.missing = 0 AND NOT EXISTS (SELECT 1 FROM features f "
                        "WHERE f.track_id = t.id AND f.model_version = ?)", (model,)).fetchone()[0]


# ---------- cache orphans ----------

_OPUS = re.compile(r"^([0-9a-f]{40})_(\d+)_(\d+)\.opus$")


def cache_orphans(conn: sqlite3.Connection, data_dir: Path) -> list[tuple[str, Path, int]]:
    """(kind, path, bytes) of cache files nothing refers to any more."""
    albums = {r[0] for r in conn.execute("SELECT id FROM albums")}
    tracks = {r[0]: int(r[1]) for r in conn.execute("SELECT id, mtime FROM tracks WHERE missing = 0")}
    sugg = {r[0] for r in conn.execute("SELECT id FROM suggestions")}
    out: list[tuple[str, Path, int]] = []
    old = time.time() - 3600
    adir = art.art_dir(data_dir)
    if adir.is_dir():
        for sub in adir.iterdir():
            if not sub.is_dir():
                continue
            for f in sub.iterdir():
                if not f.is_file():
                    continue
                if f.name.startswith(".") and f.name.endswith(".tmp"):
                    if f.stat().st_mtime < old:
                        out.append(("art", f, f.stat().st_size))
                    continue
                if sub.name == "suggestions":
                    owner = f.name.split("_", 1)[0]
                    if owner not in sugg:
                        out.append(("art", f, f.stat().st_size))
                elif sub.name in ("catalog",):
                    continue  # catalogue cover cache: small, keyed by sanitised ids
                elif f.stem not in albums:
                    out.append(("art", f, f.stat().st_size))
    odir = data_dir / "opus"
    if odir.is_dir():
        for f in odir.iterdir():
            if not f.is_file():
                continue
            m = _OPUS.match(f.name)
            if m:
                tid, _, mtime = m.groups()
                if tid not in tracks or tracks[tid] != int(mtime):
                    out.append(("opus", f, f.stat().st_size))
            elif f.name.startswith(".") and f.stat().st_mtime < old:
                out.append(("opus", f, f.stat().st_size))
    return out


def cache_report(orphans: list[tuple[str, Path, int]]) -> dict:
    by: dict[str, dict] = {}
    for kind, _, size in orphans:
        d = by.setdefault(kind, {"files": 0, "bytes": 0})
        d["files"] += 1
        d["bytes"] += size
    return {"files": len(orphans), "bytes": sum(s for _, _, s in orphans), "byKind": by}


def clean_cache(orphans: list[tuple[str, Path, int]]) -> dict:
    done = []
    for kind, p, size in orphans:
        try:
            p.unlink()
            done.append((kind, p, size))
        except OSError:
            pass
    return cache_report(done)


# ---------- server ----------

_DIR_SIZE: dict[str, tuple[float, int]] = {}
_CPU_LAST: list[tuple[int, int] | None] = [None]


def dir_size(path: Path, ttl: float = 60.0) -> int:
    hit = _DIR_SIZE.get(str(path))
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    total = 0
    stack = [path]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(Path(e.path))
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    _DIR_SIZE[str(path)] = (time.monotonic(), total)
    return total


def _cpu_times() -> tuple[int, int] | None:
    try:
        parts = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
        vals = [int(x) for x in parts]
        return sum(vals), vals[3] + (vals[4] if len(vals) > 4 else 0)  # total, idle+iowait
    except (OSError, ValueError, IndexError):
        return None


def cpu_percent() -> float | None:
    a = _CPU_LAST[0] or _cpu_times()
    if a is None:
        return None
    if _CPU_LAST[0] is None:
        time.sleep(0.2)
    b = _cpu_times()
    _CPU_LAST[0] = b
    if b is None or b[0] == a[0]:
        return None
    return round(100.0 * (1 - (b[1] - a[1]) / (b[0] - a[0])), 1)


def _meminfo() -> dict:
    out = {"totalBytes": None, "availableBytes": None, "processRssBytes": None}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            if k in ("MemTotal", "MemAvailable"):
                out["totalBytes" if k == "MemTotal" else "availableBytes"] = int(v.split()[0]) * 1024
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                out["processRssBytes"] = int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return out


def _disk(path: Path) -> dict | None:
    try:
        u = shutil.disk_usage(path)
    except OSError:
        return None
    return {"path": str(path), "totalBytes": u.total, "freeBytes": u.free}


def server(cfg: Config, server_id: str, version: str, started_at: float, opus) -> dict:
    try:
        load = os.getloadavg()
    except OSError:
        load = (None, None, None)
    opus_files = list((cfg.data_dir / "opus").glob("*.opus")) if (cfg.data_dir / "opus").is_dir() else []
    return {
        "version": version, "serverId": server_id,
        "startedAt": iso(datetime.fromtimestamp(started_at, UTC)), "uptimeS": round(time.time() - started_at, 1),
        "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
        "cpu": {"cores": os.cpu_count(), "percent": cpu_percent(), "load1": load[0], "load5": load[1],
                "load15": load[2]},
        "memory": _meminfo(),
        "disks": {k: v for k, v in (("data", _disk(cfg.data_dir)), ("library", _disk(cfg.library_root))) if v},
        "dataDirBytes": dir_size(cfg.data_dir),
        "databaseBytes": sum(p.stat().st_size for p in cfg.data_dir.glob("nasmusic.db*") if p.is_file()
                             and "backup" not in p.name),
        "opusCache": {"files": len(opus_files), "bytes": sum(p.stat().st_size for p in opus_files),
                      "limitBytes": opus.max_bytes},
        "artCacheBytes": dir_size(art.art_dir(data_dir=cfg.data_dir)) if art.art_dir(cfg.data_dir).is_dir() else 0,
    }


def clear_opus(cfg: Config) -> dict:
    files = n = 0
    d = cfg.data_dir / "opus"
    if d.is_dir():
        for p in d.iterdir():
            if p.is_file():
                try:
                    size = p.stat().st_size
                    p.unlink()
                    files += 1
                    n += size
                except OSError:
                    pass
    return {"files": files, "bytes": n}
