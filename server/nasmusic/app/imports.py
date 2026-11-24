"""Bringing listening history and playlists in from other services, and playlists out as M3U.

History (recommender signals). Every source ends in `store_listens`: a listen that matches a library track
becomes a `play` event with `imported` = the source ('listenbrainz', 'lastfm', 'spotify'), an idempotent id
('import:<source>:<user>:<ts>:<track>'), and +1 on user_tracks.play_count. A listen the server already has
(a client's own play, or the same play imported from another service, e.g. both Last.fm and ListenBrainz
scrobbled it) is skipped. Imported events are never scrobbled and can be removed per source.

  - Spotify: the account data export (my_spotify_data.zip or its JSON files): "Extended streaming history"
    (Streaming_History_Audio_*.json, endsong_*.json) and the 30-day StreamingHistory*.json. Spotify logs every
    start, so a row counts only when it was played for 30 s and half the song (or 4 minutes), like a scrobble.
  - Last.fm: user.getRecentTracks by user name (needs the server's Last.fm API key; the profile must not hide
    its listening), or a CSV made with a tool such as lastfm-to-csv (artist, album, title, date).

Playlists: an Exportify CSV (one playlist per file), the Spotify data export's Playlist*.json (many), or an
.m3u/.m3u8. Each becomes a new playlist of the matched tracks, in order; songs not in the library can go to
the user's To get list. Matching: MusicBrainz id, then artist + title (normalised: "feat.", "- Remastered",
brackets and accents don't matter; each credited artist is tried), then a unique title with the same length.
An M3U entry also matches by file path (any trailing part of it) or by this server's stream URL.
"""
from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import time
import zipfile
from collections import Counter
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import IO, Any

import httpx

from nasmusic import catalog
from nasmusic.app import playlists as pl
from nasmusic.app import toget
from nasmusic.core import iso, now_iso

SOURCES = ("listenbrainz", "lastfm", "spotify")
MIN_TRACK_S = 30.0
ENOUGH_S = 240.0
CHUNK = 1000
TOP_MISSING = 50
MAX_TOGET = 2000                    # To-get items one import may add
MAX_PLAYLIST = 10_000
MAX_FILE_BYTES = 200 * 1024 * 1024  # one file inside a zip
MAX_ZIP_TOTAL = 1024 * 1024 * 1024  # everything read from one zip
LASTFM_PAGE = 200
LASTFM_PAGE_DELAY_S = 0.25          # Last.fm asks for at most 5 requests a second
LASTFM_MAX = 200_000


class BadImport(Exception):
    def __init__(self, detail: str, status: int = 422):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ---------- matching ----------

_ARTIST_SPLIT = re.compile(r"\s*(?:,|;|/|\+|&| x | and | with | feat\.? | ft\.? | featuring | vs\.? )\s*",
                           re.IGNORECASE)
_STREAM_URL = re.compile(r"/api/v1/stream/([0-9a-f]{40})\b", re.IGNORECASE)


def artist_candidates(artist: str | None) -> list[str]:
    """The credit as written, then each credited artist ("A, B & C" -> A, B, C)."""
    if not artist:
        return []
    out = [artist]
    for part in _ARTIST_SPLIT.split(artist):
        if part and part not in out:
            out.append(part)
    return out


class Matcher:
    """The library, indexed for matching outside listens and playlist entries (built once per import)."""

    def __init__(self, conn: sqlite3.Connection):
        self.by_mbid: dict[str, str] = {}
        self.by_key: dict[tuple[str, str], str] = {}
        self.by_title: dict[str, list[tuple[str, float]]] = {}
        self.by_path: dict[str, str] = {}
        self.by_file: dict[str, list[str]] = {}
        self.dur: dict[str, float] = {}
        self.ids: set[str] = set()
        rows = conn.execute("SELECT t.id, t.path, t.title, t.mbid, t.duration_s, t.album_artist, ar.name AS artist "
                            "FROM tracks t JOIN artists ar ON ar.id = t.artist_id WHERE t.missing = 0 "
                            "ORDER BY t.added_at, t.id")
        for r in rows:
            tid = r["id"]
            self.ids.add(tid)
            self.dur[tid] = float(r["duration_s"] or 0)
            if r["mbid"]:
                self.by_mbid.setdefault(r["mbid"].lower(), tid)
            title = catalog.norm(r["title"])
            for a in {r["artist"], r["album_artist"]}:
                for cand in artist_candidates(a):
                    self.by_key.setdefault((catalog.norm(cand), title), tid)
            if title:
                self.by_title.setdefault(title, []).append((tid, self.dur[tid]))
            p = r["path"].replace("\\", "/").casefold()
            self.by_path.setdefault(p, tid)
            self.by_file.setdefault(p.rsplit("/", 1)[-1], []).append(tid)

    def match(self, artist: str | None, title: str | None, mbid: str | None = None,
              duration_s: float | None = None) -> str | None:
        if mbid and mbid.lower() in self.by_mbid:
            return self.by_mbid[mbid.lower()]
        t = catalog.norm(title)
        if not t:
            return None
        for cand in artist_candidates(artist):
            tid = self.by_key.get((catalog.norm(cand), t))
            if tid:
                return tid
        if duration_s:   # same title, same length (within 3 s), and only one such song in the library
            hits = [tid for tid, d in self.by_title.get(t, []) if abs(d - duration_s) <= 3]
            if len(hits) == 1:
                return hits[0]
        return None

    def match_path(self, ref: str) -> str | None:
        """A file path or URL from an M3U: this server's stream URL, the path relative to the library root
        (any trailing part of an absolute path), or a file name that only one track has."""
        m = _STREAM_URL.search(ref)
        if m and m.group(1).lower() in self.ids:
            return m.group(1).lower()
        if "://" in ref and not ref.lower().startswith("file://"):
            return None
        p = ref[7:] if ref.lower().startswith("file://") else ref
        from urllib.parse import unquote

        parts = [x for x in unquote(p).replace("\\", "/").casefold().split("/") if x and x != "."]
        for i in range(len(parts) - 1):
            tid = self.by_path.get("/".join(parts[i:]))
            if tid:
                return tid
        if parts:
            hits = self.by_file.get(parts[-1], [])
            if len(hits) == 1:
                return hits[0]
        return None


# ---------- history: shared store ----------


def _empty_result() -> dict:
    return {"scanned": 0, "matched": 0, "imported": 0, "alreadyKnown": 0, "unmatched": 0, "tooShort": 0}


def store_listens(conn: sqlite3.Connection, uid: str, source: str, listens: Iterable[dict], matcher: Matcher,
                  res: dict | None = None, missing: Counter | None = None, progress=None) -> dict:
    """Insert listens {ts (start, unix s), artist, title, mbid?, msPlayed?} as imported plays. Updates and
    returns `res`; unmatched (artist, title) pairs are counted in `missing`."""
    res = res if res is not None else _empty_result()
    res.setdefault("tooShort", 0)
    received = now_iso()
    batch: list[dict] = []

    def flush():
        conn.execute("BEGIN IMMEDIATE")
        try:
            for li in batch:
                _store_one(conn, uid, source, li, matcher, res, missing, received)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        batch.clear()
        if progress:
            progress(res)

    for li in listens:
        batch.append(li)
        if len(batch) >= CHUNK:
            flush()
    if batch:
        flush()
    return res


def _store_one(conn, uid, source, li, matcher: Matcher, res, missing, received) -> None:
    res["scanned"] += 1
    ts = int(li.get("ts") or 0)
    tid = matcher.match(li.get("artist"), li.get("title"), li.get("mbid"))
    if not tid or ts <= 0:
        res["unmatched"] += 1
        if missing is not None and li.get("title") and li.get("artist"):
            missing[(li["artist"].strip(), li["title"].strip())] += 1
        return
    d = matcher.dur.get(tid, 0)
    played = li.get("msPlayed")
    if played is not None:
        s = played / 1000
        if s < MIN_TRACK_S or s < min(ENOUGH_S, d / 2):
            res["tooShort"] += 1
            return
    res["matched"] += 1
    at = iso(datetime.fromtimestamp(ts, UTC))
    lo = iso(datetime.fromtimestamp(ts - max(MIN_TRACK_S, d), UTC))
    hi = iso(datetime.fromtimestamp(ts + max(MIN_TRACK_S, d) * 2, UTC))
    # the same play from a client, or imported from another service (same-source repeats are real plays)
    if conn.execute("SELECT 1 FROM events WHERE user_id = ? AND track_id = ? AND type = 'play' "
                    "AND (imported IS NULL OR imported != ?) AND at BETWEEN ? AND ?",
                    (uid, tid, source, lo, hi)).fetchone():
        res["alreadyKnown"] += 1
        return
    cur = conn.execute(
        "INSERT OR IGNORE INTO events (id, user_id, device_id, type, track_id, at, position_s, duration_s, "
        "offline, received_at, imported) VALUES (?, ?, NULL, 'play', ?, ?, ?, ?, 1, ?, ?)",
        (f"import:{source}:{uid}:{ts}:{tid}", uid, tid, at, d, d, received, source))
    if cur.rowcount:
        res["imported"] += 1
        conn.execute("INSERT OR IGNORE INTO user_tracks (user_id, track_id) VALUES (?, ?)", (uid, tid))
        conn.execute("UPDATE user_tracks SET play_count = play_count + 1, "
                     "last_played = MAX(COALESCE(last_played, ''), ?) WHERE user_id = ? AND track_id = ?",
                     (at, uid, tid))
    else:
        res["alreadyKnown"] += 1


def remove_imported(conn: sqlite3.Connection, uid: str, source: str) -> int:
    """Delete the user's events imported from `source`; recompute play counts / last played."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        tids = [r[0] for r in conn.execute("SELECT DISTINCT track_id FROM events WHERE user_id = ? AND imported = ?",
                                           (uid, source))]
        n = conn.execute("DELETE FROM events WHERE user_id = ? AND imported = ?", (uid, source)).rowcount
        for tid in tids:
            c, last = conn.execute("SELECT COUNT(*), MAX(at) FROM events WHERE user_id = ? AND track_id = ? "
                                   "AND type = 'play'", (uid, tid)).fetchone()
            conn.execute("UPDATE user_tracks SET play_count = ?, last_played = ? WHERE user_id = ? AND track_id = ?",
                         (c, last, uid, tid))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return n


def imported_by_source(conn: sqlite3.Connection, uid: str) -> dict[str, int]:
    out = {s: 0 for s in SOURCES}
    for src, n in conn.execute("SELECT imported, COUNT(*) FROM events WHERE user_id = ? AND imported IS NOT NULL "
                               "GROUP BY imported", (uid,)):
        out[src] = n
    return out


def top_missing(missing: Counter, n: int = TOP_MISSING) -> list[dict]:
    return [{"artist": a, "title": t, "plays": c} for (a, t), c in missing.most_common(n)]


# ---------- To get ----------


class _ToGet:
    """Adds songs that aren't in the library to the user's To get list (capped per import)."""

    def __init__(self, conn: sqlite3.Connection, uid: str, enabled: bool):
        self.conn, self.uid, self.enabled = conn, uid, enabled
        self.index: catalog.LibraryIndex | None = None
        self.added = 0
        self.seen: set[str] = set()

    def add(self, artist: str | None, title: str | None, note: str) -> bool:
        if not self.enabled or not artist or not title or self.added >= MAX_TOGET:
            return False
        key = catalog.match_key(artist, title)
        if key in self.seen:
            return False
        self.seen.add(key)
        if self.index is None:
            self.index = catalog.LibraryIndex(self.conn)
        before = self.conn.execute("SELECT 1 FROM toget WHERE user_id = ? AND type = 'track' AND match_key = ?",
                                   (self.uid, key)).fetchone()
        item = toget.add(self.conn, self.uid, type="track", title=title, artist=artist, note=note,
                         source="manual", index=self.index)
        if before is None and item["status"] == "want":
            self.added += 1
            return True
        return False


# ---------- playlists ----------


def create_playlist(conn: sqlite3.Connection, uid: str, name: str, entries: list[dict], matcher: Matcher,
                    wants: _ToGet, origin: str) -> dict:
    """entries: {artist, title, mbid?, durationS?, path?}. Makes a playlist of the matched tracks (skipped
    when the user already has one with this name and exactly these tracks)."""
    name = (name or "").strip()[:200] or "Imported playlist"
    ids: list[str] = []
    missing: list[dict] = []
    for e in entries[:MAX_PLAYLIST]:
        tid = matcher.match_path(e["path"]) if e.get("path") else None
        tid = tid or matcher.match(e.get("artist"), e.get("title"), e.get("mbid"), e.get("durationS"))
        if tid:
            ids.append(tid)
        else:
            missing.append({"artist": e.get("artist"), "title": e.get("title") or e.get("path")})
    out: dict[str, Any] = {"name": name, "total": len(entries), "matched": len(ids), "missing": len(missing),
                           "playlistId": None, "unchanged": False, "toGetAdded": 0,
                           "missingSongs": missing[:TOP_MISSING]}
    for r in conn.execute("SELECT id FROM playlists WHERE user_id = ? AND name = ?", (uid, name)):
        if pl._playlist_ids(conn, r[0]) == ids:
            out.update(playlistId=r[0], unchanged=True)
            break
    if not out["unchanged"] and ids:
        out["playlistId"] = pl.create_playlist(conn, uid, name, ids)["id"]
    before = wants.added
    for m in missing:
        wants.add(m["artist"], m["title"], f"From {origin} playlist “{name}”")
    out["toGetAdded"] = wants.added - before
    return out


def _col(header: list[str], *names: str) -> int | None:
    low = [h.strip().casefold() for h in header]
    for n in names:
        if n in low:
            return low.index(n)
    return None


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def parse_exportify(text: str) -> list[dict]:
    """An Exportify (or similar) playlist CSV: Track Name, Artist Name(s), Album Name, Duration (ms)."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    h = rows[0]
    ti = _col(h, "track name", "title", "name", "track")
    ai = _col(h, "artist name(s)", "artist name", "artists", "artist")
    di = _col(h, "duration (ms)", "track duration (ms)", "duration_ms")
    if ti is None or ai is None:
        raise BadImport("not a playlist CSV: expected 'Track Name' and 'Artist Name(s)' columns (Exportify)")
    out = []
    for r in rows[1:]:
        if len(r) <= max(ti, ai) or not r[ti].strip():
            continue
        dur = None
        if di is not None and di < len(r):
            try:
                dur = int(float(r[di])) / 1000
            except ValueError:
                dur = None
        out.append({"title": r[ti].strip(), "artist": r[ai].strip(), "durationS": dur})
    return out


def parse_spotify_playlists(data: Any) -> list[tuple[str, list[dict]]]:
    """The data export's Playlist*.json: {"playlists": [{"name", "items": [{"track": {...}}]}]}."""
    out = []
    for p in (data.get("playlists") or []) if isinstance(data, dict) else []:
        entries = []
        for it in p.get("items") or []:
            t = (it or {}).get("track") or {}
            if t.get("trackName"):
                entries.append({"title": t["trackName"], "artist": t.get("artistName")})
        out.append((str(p.get("name") or "Spotify playlist"), entries))
    return out


_EXTINF = re.compile(r"^#EXTINF:\s*(-?\d+(?:\.\d+)?)?[^,]*,(.*)$", re.IGNORECASE)


def parse_m3u(text: str) -> tuple[str | None, list[dict]]:
    """(#PLAYLIST name or None, entries {path, artist?, title?, durationS?})."""
    name = None
    info: dict = {}
    out = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("﻿")
        if not line:
            continue
        if line.startswith("#"):
            m = _EXTINF.match(line)
            if m:
                info = {}
                if m.group(1) and float(m.group(1)) > 0:
                    info["durationS"] = float(m.group(1))
                label = m.group(2).strip()
                if " - " in label:
                    info["artist"], info["title"] = (x.strip() for x in label.split(" - ", 1))
                elif label:
                    info["title"] = label
            elif line.upper().startswith("#PLAYLIST:"):
                name = line[10:].strip() or None
            continue
        out.append({"path": line, **info})
        info = {}
    return name, out


# ---------- Spotify ----------


def _spotify_ts(row: dict) -> tuple[int, int | None]:
    """(start unix seconds, ms played) for an extended (ts, ms_played) or 30-day (endTime, msPlayed) row."""
    ms = row.get("ms_played", row.get("msPlayed"))
    ms = int(ms) if isinstance(ms, (int, float)) else None
    if row.get("ts"):
        end = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
    else:
        end = datetime.strptime(str(row["endTime"]), "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return int(end.timestamp()) - (ms or 0) // 1000, ms


def spotify_listens(rows: list) -> Iterator[dict]:
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = row.get("master_metadata_track_name") or row.get("trackName")
        artist = row.get("master_metadata_album_artist_name") or row.get("artistName")
        if not title or not artist:   # podcasts, audiobooks, local files without tags
            continue
        try:
            ts, ms = _spotify_ts(row)
        except (ValueError, TypeError, KeyError):
            continue
        yield {"ts": ts, "artist": artist, "title": title, "msPlayed": ms}


def _is_history(data: Any) -> bool:
    if not isinstance(data, list):
        return False
    first = next((x for x in data if isinstance(x, dict)), None)
    return first is None or ("ts" in first and "ms_played" in first) or ("endTime" in first and "msPlayed" in first)


def _files(name: str, f: IO[bytes]) -> Iterator[tuple[str, bytes]]:
    """(name, bytes) of each JSON/CSV in the upload (a zip is opened; other files inside are ignored)."""
    head = f.read(4)
    f.seek(0)
    if head != b"PK\x03\x04":
        yield name, f.read()
        return
    try:
        z = zipfile.ZipFile(f)
    except zipfile.BadZipFile as e:
        raise BadImport(f"{name}: not a readable zip") from e
    total = 0
    for info in sorted(z.infolist(), key=lambda i: i.filename):
        base = PurePosixPath(info.filename).name
        if info.is_dir() or base.startswith(".") or not base.lower().endswith((".json", ".csv")):
            continue
        if info.file_size > MAX_FILE_BYTES or total + info.file_size > MAX_ZIP_TOTAL:
            raise BadImport(f"{info.filename}: too large")
        total += info.file_size
        yield base, z.read(info)


def import_spotify(conn: sqlite3.Connection, uid: str, name: str, f: IO[bytes], *, add_missing: bool = True,
                   missing_min_plays: int = 0) -> dict:
    """One uploaded file: my_spotify_data.zip (or any zip of the export), a streaming-history JSON, a
    Playlist*.json, or an Exportify CSV. Returns {files, history, playlists, toGetAdded}."""
    matcher = Matcher(conn)
    wants = _ToGet(conn, uid, add_missing)
    res = _empty_result()
    missing: Counter = Counter()
    history = False
    playlists: list[dict] = []
    files: list[str] = []
    for fname, data in _files(name, f):
        low = fname.lower()
        if low.endswith(".csv"):
            files.append(fname)
            entries = parse_exportify(_decode(data))
            title = re.sub(r"\.csv$", "", fname, flags=re.IGNORECASE).replace("_", " ")
            playlists.append(create_playlist(conn, uid, title, entries, matcher, wants, "Spotify"))
            continue
        try:
            doc = json.loads(_decode(data))
        except ValueError as e:
            if low.endswith(".json"):
                raise BadImport(f"{fname}: not valid JSON") from e
            raise BadImport(f"{fname}: not a Spotify export file (.zip, .json or Exportify .csv)") from e
        if _is_history(doc):
            files.append(fname)
            history = True
            store_listens(conn, uid, "spotify", spotify_listens(doc), matcher, res, missing)
        elif isinstance(doc, dict) and "playlists" in doc:
            files.append(fname)
            for pname, entries in parse_spotify_playlists(doc):
                playlists.append(create_playlist(conn, uid, pname, entries, matcher, wants, "Spotify"))
    if not files:
        raise BadImport("no Spotify listening history or playlists found in this file")
    out: dict[str, Any] = {"files": files, "playlists": playlists, "history": None}
    if history:
        if missing_min_plays > 0:
            for (artist, title), n in missing.most_common():
                if n < missing_min_plays:
                    break
                wants.add(artist, title, f"Played {n} times on Spotify")
        out["history"] = {**res, "topMissing": top_missing(missing)}
    out["toGetAdded"] = wants.added
    return out


# ---------- Last.fm ----------


_LFM_DATES = ("%d %b %Y %H:%M", "%d %b %Y, %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M")


def _parse_when(s: str) -> int | None:
    s = s.strip()
    if not s:
        return None
    if s.isdigit():
        n = int(s)
        return n // 1000 if n > 10_000_000_000 else n
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return int((d if d.tzinfo else d.replace(tzinfo=UTC)).timestamp())
    except ValueError:
        pass
    for fmt in _LFM_DATES:
        try:
            return int(datetime.strptime(s, fmt).replace(tzinfo=UTC).timestamp())
        except ValueError:
            continue
    return None


def lastfm_csv_listens(text: str) -> list[dict]:
    """artist, album, title, date (lastfm-to-csv, no header) or a CSV with a header naming those columns
    (`uts`/`timestamp` accepted for the date; `track`/`name` for the title)."""
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        return []
    h = [c.strip().casefold() for c in rows[0]]
    ai = _col(h, "artist", "artist name", "artist_name")
    ti = _col(h, "title", "track", "track name", "name", "track_name")
    wi = _col(h, "uts", "timestamp", "date", "time", "utc_time", "listened_at")
    mi = _col(h, "track_mbid", "recording_mbid", "mbid")
    if ai is not None and ti is not None and wi is not None:
        body = rows[1:]
    else:
        ai, ti, wi, mi = 0, 2, 3, None
        body = rows
    out = []
    for r in body:
        if len(r) <= max(ai, ti, wi):
            continue
        ts = _parse_when(r[wi])
        if ts is None:
            continue
        out.append({"ts": ts, "artist": r[ai].strip(), "title": r[ti].strip(),
                    "mbid": (r[mi].strip() or None) if mi is not None and mi < len(r) else None})
    if body and not out:
        raise BadImport("not a Last.fm CSV: expected artist, album, title, date columns")
    return out


def import_lastfm_csv(conn: sqlite3.Connection, uid: str, data: bytes, *, add_missing_min_plays: int = 0) -> dict:
    matcher = Matcher(conn)
    missing: Counter = Counter()
    res = store_listens(conn, uid, "lastfm", lastfm_csv_listens(_decode(data)), matcher, None, missing)
    wants = _ToGet(conn, uid, add_missing_min_plays > 0)
    for (artist, title), n in missing.most_common():
        if n < add_missing_min_plays:
            break
        wants.add(artist, title, f"Scrobbled {n} times on Last.fm")
    return {**res, "topMissing": top_missing(missing), "toGetAdded": wants.added}


def _lastfm_page(client: httpx.Client, cfg, user: str, page: int) -> dict:
    from nasmusic.app.scrobble import LASTFM_API, ScrobbleError

    params = {"method": "user.getrecenttracks", "user": user, "api_key": cfg.lastfm_api_key, "format": "json",
              "limit": LASTFM_PAGE, "page": page}
    for attempt in range(4):
        try:
            r = client.get(LASTFM_API, params=params)
        except httpx.HTTPError as e:
            raise ScrobbleError("retry", f"Last.fm unreachable: {e}") from e
        try:
            data = r.json()
        except ValueError:
            data = {}
        code = int(data.get("error") or 0) if isinstance(data, dict) else 0
        if (r.status_code >= 500 or r.status_code == 429 or code in (8, 11, 16, 29)) and attempt < 3:
            time.sleep(LASTFM_PAGE_DELAY_S * 4 * (attempt + 1))
            continue
        if code == 6:
            raise ScrobbleError("bad", f"Last.fm has no user called {user!r}", 6)
        if code == 17:
            raise ScrobbleError("bad", "this Last.fm profile hides its listening history", 17)
        if code:
            raise ScrobbleError("bad", f"Last.fm error {code}: {data.get('message', '')}", code)
        if r.status_code >= 400:
            raise ScrobbleError("bad", f"Last.fm HTTP {r.status_code}", r.status_code)
        return data.get("recenttracks") or {}
    raise ScrobbleError("retry", "Last.fm kept failing")


def _lastfm_rows(tracks: Any) -> list[dict]:
    if isinstance(tracks, dict):
        tracks = [tracks]
    out = []
    for t in tracks or []:
        if not isinstance(t, dict) or (t.get("@attr") or {}).get("nowplaying"):
            continue
        uts = (t.get("date") or {}).get("uts")
        art = t.get("artist") or {}
        artist = art.get("#text") or art.get("name") if isinstance(art, dict) else str(art)
        if not uts or not t.get("name"):
            continue
        out.append({"ts": int(uts), "artist": artist, "title": t["name"], "mbid": t.get("mbid") or None})
    return out


def import_lastfm(conn: sqlite3.Connection, uid: str, cfg, username: str, transport=None,
                  max_scrobbles: int = LASTFM_MAX, progress=None) -> dict:
    """Page through user.getRecentTracks (newest first) and store the matches."""
    from nasmusic.app.scrobble import _client

    matcher = Matcher(conn)
    res = {**_empty_result(), "username": username}
    missing: Counter = Counter()
    page, pages = 1, 1
    with _client(transport) as client:
        while page <= pages and res["scanned"] < max_scrobbles:
            rt = _lastfm_page(client, cfg, username, page)
            pages = int((rt.get("@attr") or {}).get("totalPages") or 0)
            rows = _lastfm_rows(rt.get("track"))[: max_scrobbles - res["scanned"]]
            if not rows:
                break
            store_listens(conn, uid, "lastfm", rows, matcher, res, missing)
            res["page"], res["pages"] = page, pages
            if progress:
                progress(res)
            page += 1
            if page <= pages:
                time.sleep(LASTFM_PAGE_DELAY_S)
    res["topMissing"] = top_missing(missing)
    return res


# ---------- M3U ----------


def import_m3u(conn: sqlite3.Connection, uid: str, data: bytes, name: str | None, *, add_missing: bool = True) -> dict:
    text = _decode(data)
    pname, entries = parse_m3u(text)
    if not entries:
        raise BadImport("no entries in this playlist file")
    matcher = Matcher(conn)
    wants = _ToGet(conn, uid, add_missing)
    base = re.sub(r"\.m3u8?$", "", name or "", flags=re.IGNORECASE).strip()
    out = create_playlist(conn, uid, pname or base or "Imported playlist", entries, matcher, wants, "M3U")
    for m in out["missingSongs"]:   # a bare path: show its file name
        if not m["artist"] and m["title"] and re.search(r"[\\/]", m["title"]):
            m["title"] = re.split(r"[\\/]", m["title"])[-1]
    return out


def _m3u_label(s: str) -> str:
    return " ".join(s.replace(",", " ").split()) if s else s


def export_m3u(conn: sqlite3.Connection, uid: str, pid: str, cfg, paths: str = "library", base_url: str = "") -> str:
    """The playlist as UTF-8 M3U (#EXTM3U + #EXTINF). paths: 'library' = relative to the library root (save
    the file there for other players), 'absolute' = the server's own file paths, 'url' = this server's stream
    URLs (they need the app's sign-in)."""
    data = pl.get_playlist(conn, uid, pid)   # access check
    rows = conn.execute(
        "SELECT t.id, t.path, t.title, t.duration_s, ar.name AS artist FROM playlist_tracks e "
        "JOIN tracks t ON t.id = e.track_id JOIN artists ar ON ar.id = t.artist_id "
        "WHERE e.playlist_id = ? ORDER BY e.position", (pid,)).fetchall()
    lines = ["#EXTM3U", f"#PLAYLIST:{data['name']}"]
    for r in rows:
        lines.append(f"#EXTINF:{int(round(r['duration_s'] or 0))},{_m3u_label(r['artist'])} - {r['title']}")
        if paths == "url":
            lines.append(f"{base_url.rstrip('/')}/api/v1/stream/{r['id']}")
        elif paths == "absolute":
            lines.append(str(cfg.library_root / r["path"]))
        else:
            lines.append(r["path"])
    return "\n".join(lines) + "\n"
