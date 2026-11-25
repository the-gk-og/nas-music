"""LRCLIB (https://lrclib.net, free, open data) lyrics fallback for tracks whose files have none.

Lookup: /api/get with artist, title, album and duration (exact match), then /api/search as a fallback
(closest duration within 3 s, synced preferred, names must match after normalisation). Results are cached
in `lyrics_cache`: found lyrics forever (until the file gains its own), "not found"/instrumental for 30 days.
Network errors are not cached. Only artist/title/album/duration are sent, with the project's User-Agent.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from homebeat import catalog
from homebeat.core import iso, now_iso

log = logging.getLogger(__name__)

BASE = "https://lrclib.net/api"
RETRY_AFTER = timedelta(days=30)
DURATION_TOLERANCE_S = 3.0
UNKNOWN_ARTISTS = {"unknown artist", ""}


def user_agent(version: str) -> str:
    return f"Homebeat/{version} (https://github.com/the-gk-og/nas-music)"


class LrclibError(Exception):
    pass


@dataclass
class Result:
    status: str                 # found | not_found | instrumental
    synced: bool = False
    raw: str | None = None
    lrclib_id: int | None = None


def strict_title(s: str | None) -> str:
    """Title comparison for lyrics: like catalog.norm but KEEPS bracketed words, so "Song (Live)" or
    "Song (Remix)" (different timings/words) don't match "Song"."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).casefold().replace("&", " and ")
    s = re.sub(r"\s+(feat\.?|ft\.?|featuring)\s+[^\(\[]*", " ", s)
    return " ".join(re.sub(r"[^\w]+", " ", s).split())


def _pick(rec: dict) -> Result:
    if rec.get("instrumental"):
        return Result("instrumental", lrclib_id=rec.get("id"))
    if rec.get("syncedLyrics"):
        return Result("found", True, rec["syncedLyrics"], rec.get("id"))
    if rec.get("plainLyrics"):
        return Result("found", False, rec["plainLyrics"], rec.get("id"))
    return Result("not_found", lrclib_id=rec.get("id"))


class Lrclib:
    def __init__(self, version: str = "0", transport: httpx.BaseTransport | None = None,
                 min_interval_s: float = 0.25):
        self.ua = user_agent(version)
        self.transport = transport
        self.min_interval = min_interval_s
        self._next = 0.0
        self._lock = threading.Lock()
        self.requests = 0

    def _throttle(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.min_interval
        if delay:
            time.sleep(delay)

    def _get(self, c: httpx.Client, path: str, params: dict) -> httpx.Response:
        self._throttle()
        self.requests += 1
        return c.get(f"{BASE}{path}", params=params)

    def lookup(self, artist: str, title: str, album: str | None, duration_s: float | None) -> Result:
        """Raises LrclibError on network/server problems (so they are not cached)."""
        try:
            with httpx.Client(timeout=httpx.Timeout(6.0, connect=4.0), transport=self.transport,
                              headers={"User-Agent": self.ua, "Accept": "application/json"}) as c:
                params = {"artist_name": artist, "track_name": title}
                if album:
                    params["album_name"] = album
                if duration_s:
                    params["duration"] = int(round(duration_s))
                r = self._get(c, "/get", params)
                if r.status_code == 200:
                    res = _pick(r.json())
                    if res.status != "not_found":
                        return res
                elif r.status_code != 404:
                    raise LrclibError(f"lrclib /get HTTP {r.status_code}")
                r = self._get(c, "/search", {"track_name": title, "artist_name": artist})
                if r.status_code != 200:
                    raise LrclibError(f"lrclib /search HTTP {r.status_code}")
                return self._best(r.json(), artist, title, duration_s)
        except (httpx.HTTPError, ValueError) as e:
            raise LrclibError(str(e)) from e

    @staticmethod
    def _best(items, artist: str, title: str, duration_s: float | None) -> Result:
        if not isinstance(items, list):
            return Result("not_found")
        want_t, want_a = strict_title(title), catalog.norm(artist)
        scored = []
        for it in items:
            if not isinstance(it, dict):
                continue
            if strict_title(it.get("trackName")) != want_t:
                continue
            a = catalog.norm(it.get("artistName"))
            if want_a and not (a == want_a or want_a in a or a in want_a):
                continue
            d = it.get("duration")
            if duration_s and isinstance(d, (int, float)) and abs(d - duration_s) > DURATION_TOLERANCE_S:
                continue
            res = _pick(it)
            if res.status == "not_found":
                continue
            dist = abs(d - duration_s) if duration_s and isinstance(d, (int, float)) else 0
            scored.append((0 if res.synced else 1, dist, res))
        if not scored:
            return Result("not_found")
        scored.sort(key=lambda x: (x[0], x[1]))
        return scored[0][2]


# ---------- cache ----------


def cached(conn: sqlite3.Connection, track_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM lyrics_cache WHERE track_id = ?", (track_id,)).fetchone()


def is_fresh(row: sqlite3.Row | None) -> bool:
    """True when the cached answer can be used without asking LRCLIB again."""
    if row is None:
        return False
    if row["status"] == "found":
        return True
    return bool(row["retry_after"]) and row["retry_after"] > now_iso()


def store(conn: sqlite3.Connection, track_id: str, res: Result) -> None:
    retry = None if res.status == "found" else iso(datetime.now(UTC) + RETRY_AFTER)
    conn.execute(
        "INSERT INTO lyrics_cache (track_id, status, synced, raw, lrclib_id, fetched_at, retry_after) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(track_id) DO UPDATE SET status = excluded.status, "
        "synced = excluded.synced, raw = excluded.raw, lrclib_id = excluded.lrclib_id, "
        "fetched_at = excluded.fetched_at, retry_after = excluded.retry_after",
        (track_id, res.status, int(res.synced), res.raw, res.lrclib_id, now_iso(), retry))


def track_meta(conn: sqlite3.Connection, track_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT t.id, t.title, t.duration_s, t.path, ar.name AS artist, al.title AS album "
        "FROM tracks t JOIN artists ar ON ar.id = t.artist_id JOIN albums al ON al.id = t.album_id "
        "WHERE t.id = ? AND t.missing = 0", (track_id,)).fetchone()


def fetch(conn: sqlite3.Connection, client: Lrclib, track_id: str) -> sqlite3.Row | None:
    """Cached answer for a track, asking LRCLIB when there is none (or it is due for a retry).
    Returns the cache row, or None when LRCLIB couldn't be reached (nothing cached)."""
    row = cached(conn, track_id)
    if is_fresh(row):
        return row
    meta = track_meta(conn, track_id)
    if meta is None:
        return None
    if meta["artist"].strip().casefold() in UNKNOWN_ARTISTS:
        res = Result("not_found")  # nothing sensible to search for
    else:
        try:
            res = client.lookup(meta["artist"], meta["title"], meta["album"], meta["duration_s"])
        except LrclibError as e:
            log.info("lrclib unavailable for %s: %s", track_id, e)
            return row  # keep any older answer; don't cache the failure
    store(conn, track_id, res)
    return cached(conn, track_id)


def stats(conn: sqlite3.Connection) -> dict:
    rows = dict(conn.execute("SELECT status, COUNT(*) FROM lyrics_cache GROUP BY status").fetchall())
    return {"cached": sum(rows.values()), "found": rows.get("found", 0),
            "notFound": rows.get("not_found", 0) + rows.get("instrumental", 0)}
