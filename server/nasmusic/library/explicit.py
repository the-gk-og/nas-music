"""Explicit-content flags (schema v6 `track_explicit`), filled by the resumable `explicit` job.

Sources, in priority order (the first one with a verdict wins):
1. admin  - an admin's tag override (`tag_overrides` field 'explicit': "explicit" | "clean"), single, bulk or
            per album through /admin/tags or /admin/explicit;
2. tags   - the file's own tags: iTunes `rtng` / ITUNESADVISORY (1/4 explicit, 2 clean), EXPLICIT (1/0,
            yes/no, true/false), CONTENTRATING, or "(Explicit)" / "[Clean]" style markers in title or album;
3. deezer - the matching Deezer track's explicit flag (free public API, no key, ~4 requests/s, cached;
            "not found" is asked again after 90 days, network errors are never cached);
4. lyrics - a small profanity word list over the track's lyrics (admin, embedded/sidecar, or LRCLIB's
            cache — the job never asks LRCLIB itself). Only ever gives "probable", never certain.

The library is read-only: files are opened with mutagen for reading only. Each run is bounded by a time
budget; progress lives in `track_explicit`, so the next run carries on where this one stopped.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from nasmusic import catalog
from nasmusic.core import iso, now_iso

log = logging.getLogger(__name__)

DEEZER = "https://api.deezer.com"
DEEZER_RETRY = timedelta(days=90)
DURATION_TOLERANCE_S = 5.0
VERDICTS = ("explicit", "clean")

# ---------- 2. file tags ----------

_TRUE = {"1", "true", "yes", "y", "explicit", "e", "dirty"}
_FALSE = {"0", "false", "no", "n", "clean", "c", "notexplicit", "not explicit"}
_MARK_EXPLICIT = re.compile(r"[\(\[\{]\s*(explicit|dirty|uncensored)(\s+(version|edit|lyrics|album))?\s*[\)\]\}]"
                            r"|\s-\s(explicit|dirty)(\s+(version|edit))?\s*$", re.IGNORECASE)
_MARK_CLEAN = re.compile(r"[\(\[\{]\s*(clean|censored)(\s+(version|edit|lyrics|album))?\s*[\)\]\}]"
                         r"|\s-\s(clean)(\s+(version|edit))?\s*$", re.IGNORECASE)


def _text(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        if not v:
            return None
        v = v[0]
    if hasattr(v, "text"):  # ID3 frame
        return _text(list(v.text))
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    s = str(v).strip().strip("\x00").strip()
    return s or None


def _advisory(v) -> str | None:
    """iTunes advisory: 1 or 4 explicit, 2 clean, 0 = none."""
    s = _text(v)
    if s is None:
        return None
    s = s.casefold()
    if s in ("1", "4", "explicit"):
        return "explicit"
    if s in ("2", "clean"):
        return "clean"
    return None


def _boolish(v) -> str | None:
    s = _text(v)
    if s is None:
        return None
    s = s.casefold()
    if s in _TRUE:
        return "explicit"
    if s in _FALSE:
        return "clean"
    return None


def marker(text: str | None) -> str | None:
    """"Song (Explicit)", "Album [Clean Version]", "Song - Explicit" -> explicit / clean. A bare word
    ("Clean" by itself, a song called "Explicit") is not a marker."""
    if not text:
        return None
    if _MARK_EXPLICIT.search(text):
        return "explicit"
    if _MARK_CLEAN.search(text):
        return "clean"
    return None


def flag_from_file(f) -> str | None:
    """Verdict from a mutagen file object's tags (None = the tags say nothing)."""
    tags = getattr(f, "tags", None)
    if tags is None:
        return None
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4

    if isinstance(f, MP4):
        rt = tags.get("rtng")
        if rt:
            r = _advisory(str(rt[0]))
            if r:
                return r
        for k in ("----:com.apple.iTunes:ITUNESADVISORY", "----:com.apple.iTunes:EXPLICIT"):
            if k in tags:
                r = _advisory(tags[k]) if k.endswith("ADVISORY") else _boolish(tags[k])
                if r:
                    return r
        return None
    if isinstance(tags, ID3):
        for fr in tags.getall("TXXX"):
            desc = (fr.desc or "").upper().replace(" ", "")
            if desc in ("ITUNESADVISORY",):
                r = _advisory(fr)
            elif desc in ("EXPLICIT", "CONTENTRATING", "CONTENT_RATING", "PARENTALADVISORY"):
                r = _boolish(fr)
            else:
                continue
            if r:
                return r
        return None
    # Vorbis comments (FLAC, Ogg): keys are case-insensitive.
    try:
        for k in ("itunesadvisory",):
            if k in tags:
                r = _advisory(tags[k])
                if r:
                    return r
        for k in ("explicit", "contentrating", "content_rating", "parentaladvisory"):
            if k in tags:
                r = _boolish(tags[k])
                if r:
                    return r
    except (TypeError, ValueError):
        return None
    return None


def read_file_flag(path: Path, title: str | None, album: str | None) -> str | None:
    """File tags first, then markers in the (effective) title, then the album title."""
    import mutagen

    try:
        f = mutagen.File(path)
    except Exception as e:  # unreadable file: titles may still tell
        log.debug("explicit: cannot read %s: %s", path, e)
        f = None
    r = flag_from_file(f) if f is not None else None
    return r or marker(title) or marker(album)


# ---------- 3. Deezer ----------


class DeezerError(Exception):
    pass


@dataclass
class DeezerMatch:
    flag: str          # explicit | clean | not_found
    deezer_id: int | None = None


def _deezer_flag(item: dict) -> str | None:
    ecl = item.get("explicit_content_lyrics")
    if isinstance(ecl, int):
        if ecl in (1, 4):
            return "explicit"
        if ecl in (0, 3):
            return "clean"
        return None
    v = item.get("explicit_lyrics")
    return "explicit" if v is True else "clean" if v is False else None


def best_match(items, artist: str, title: str, album: str | None, duration_s: float | None) -> DeezerMatch:
    """Pick the Deezer search hit for (artist, title): names must match after normalisation, duration within
    5 s when both are known. Ties between an explicit and a clean version go to explicit (family mode
    would rather hide a clean track than play an explicit one)."""
    if not isinstance(items, list):
        return DeezerMatch("not_found")
    want_a, want_t, want_al = catalog.norm(artist), catalog.norm(title), catalog.norm(album)
    scored = []
    for it in items:
        if not isinstance(it, dict):
            continue
        a = catalog.norm((it.get("artist") or {}).get("name"))
        t = catalog.norm(it.get("title_short") or it.get("title"))
        if not a or not t or t != want_t or not (a == want_a or want_a in a or a in want_a):
            continue
        flag = _deezer_flag(it)
        if flag is None:
            continue
        diff = 0.0
        if duration_s and it.get("duration"):
            diff = abs(float(it["duration"]) - duration_s)
            if diff > DURATION_TOLERANCE_S:
                continue
        al = catalog.norm((it.get("album") or {}).get("title"))
        scored.append((0 if want_al and al == want_al else 1, round(diff), 0 if flag == "explicit" else 1,
                       flag, it.get("id")))
    if not scored:
        return DeezerMatch("not_found")
    scored.sort(key=lambda x: x[:3])
    return DeezerMatch(scored[0][3], scored[0][4])


class Deezer:
    def __init__(self, transport: httpx.BaseTransport | None = None, min_interval_s: float = 0.25):
        self.transport = transport
        self.min_interval = min_interval_s  # Deezer allows 50 requests / 5 s; stay well under it
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

    def lookup(self, artist: str, title: str, album: str | None, duration_s: float | None) -> DeezerMatch:
        """Raises DeezerError on network/quota problems (so they are not cached). Only artist and title are
        sent, with a generic User-Agent."""
        self._throttle()
        self.requests += 1
        q = f'artist:"{artist}" track:"{catalog.norm(title) or title}"'
        try:
            with httpx.Client(timeout=httpx.Timeout(6.0, connect=4.0), transport=self.transport,
                              headers={"User-Agent": catalog.USER_AGENT, "Accept": "application/json"}) as c:
                r = c.get(f"{DEEZER}/search", params={"q": q, "limit": 15})
                if r.status_code != 200:
                    raise DeezerError(f"deezer HTTP {r.status_code}")
                data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise DeezerError(str(e)) from e
        if isinstance(data, dict) and data.get("error"):
            raise DeezerError(f"deezer error {data['error']}")
        return best_match((data or {}).get("data"), artist, title, album, duration_s)


# ---------- 4. lyrics word list ----------

# Strong words: one is enough for "probably explicit". Mild words need MILD_MIN hits in total.
_STRONG = re.compile(r"\b(?:(?:mother)?f+u+c*k+(?:in[g']?|ed|er|ers|s|a)?|cunts?|n[i1]gg(?:a|as|az|er|ers)|"
                     r"fagg?ots?|cocksuckers?|motherfuckers?)\b", re.IGNORECASE)
_MILD = re.compile(r"\b(?:shit(?:s|ty|ting)?|bullshit|bitch(?:es|in[g']?)?|assholes?|dickheads?|pussy|whores?|"
                   r"sluts?|bastards?)\b", re.IGNORECASE)
MILD_MIN = 3


def lyrics_verdict(text: str | None) -> tuple[str, int]:
    """("probable" | "clean" | "none", number of word-list hits)."""
    if not text or not text.strip():
        return "none", 0
    strong = len(_STRONG.findall(text))
    mild = len(_MILD.findall(text))
    hits = strong + mild
    if strong or mild >= MILD_MIN:
        return "probable", hits
    return "clean", hits


def _lyrics_text(conn: sqlite3.Connection, library_root: Path, track_id: str, path: str) -> str | None:
    from nasmusic.library import lyrics, overrides

    raw = overrides.lyrics_override(conn, track_id)
    if raw:
        parsed = lyrics.from_raw(raw, "library")
        if parsed:
            return "\n".join(ln["text"] for ln in parsed.lines)
    try:
        found = lyrics.read_all(library_root / path)
    except Exception:
        found = {}
    for k in ("embedded", "lrc_file"):
        if k in found:
            return "\n".join(ln["text"] for ln in found[k].lines)
    r = conn.execute("SELECT raw FROM lyrics_cache WHERE track_id = ? AND status = 'found'", (track_id,)).fetchone()
    if r and r[0]:
        parsed = lyrics.from_raw(r[0], "lrclib")
        if parsed:
            return "\n".join(ln["text"] for ln in parsed.lines)
    return None


# ---------- precedence ----------


def resolve(admin: str | None, tags_flag: str | None, deezer_flag: str | None,
            lyrics_flag: str | None) -> tuple[str, str | None]:
    """(status, source) from the evidence, highest priority first."""
    if admin in VERDICTS:
        return admin, "admin"
    if tags_flag in VERDICTS:
        return tags_flag, "tags"
    if deezer_flag in VERDICTS:
        return deezer_flag, "deezer"
    if lyrics_flag == "probable":
        return "probable", "lyrics"
    return "unknown", None


def admin_flags(conn: sqlite3.Connection, track_ids: list[str] | None = None) -> dict[str, str]:
    q = "SELECT track_id, value FROM tag_overrides WHERE field = 'explicit'"
    out: dict[str, str] = {}
    if track_ids is None:
        rows = conn.execute(q).fetchall()
    else:
        rows = []
        for i in range(0, len(track_ids), 500):
            chunk = track_ids[i:i + 500]
            rows += conn.execute(q + f" AND track_id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
    for r in rows:
        try:
            v = json.loads(r[1])
        except ValueError:
            continue
        if v in VERDICTS:
            out[r[0]] = v
    return out


def _ensure_rows(conn: sqlite3.Connection, ids: list[str], now: str) -> None:
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        conn.execute(f"INSERT OR IGNORE INTO track_explicit (track_id, updated_at) SELECT id, ? FROM tracks "
                     f"WHERE id IN ({','.join('?' * len(chunk))})", (now, *chunk))


def recompute(conn: sqlite3.Connection, track_ids: list[str] | None = None) -> int:
    """Re-derive status/source from the stored evidence (+ admin overrides). Returns rows changed. Call it
    after an admin edit; the job calls it for what it touched."""
    now = now_iso()
    admin = admin_flags(conn, track_ids)
    if track_ids is None:
        _ensure_rows(conn, list(admin), now)
        rows = conn.execute("SELECT * FROM track_explicit").fetchall()
    else:
        ids = list(dict.fromkeys(track_ids))
        _ensure_rows(conn, ids, now)
        rows = []
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows += conn.execute(f"SELECT * FROM track_explicit WHERE track_id IN ({','.join('?' * len(chunk))})",
                                 chunk).fetchall()
    changed = 0
    for r in rows:
        status, source = resolve(admin.get(r["track_id"]), r["tags_flag"], r["deezer_flag"], r["lyrics_flag"])
        if (status, source) != (r["status"], r["source"]):
            conn.execute("UPDATE track_explicit SET status = ?, source = ?, updated_at = ? WHERE track_id = ?",
                         (status, source, now, r["track_id"]))
            changed += 1
    return changed


# ---------- the job ----------


def run(conn: sqlite3.Connection, library_root: Path, deezer: Deezer | None = None,
        time_budget_s: float | None = None, deezer_limit: int | None = None) -> dict:
    """One bounded pass: (1) read file tags of new/changed tracks, (2) ask Deezer about tracks the tags
    don't decide, (3) run the word list over lyrics of tracks still undecided. `deezer=None` skips (2)."""
    t0 = time.monotonic()
    now = now_iso()

    def out_of_time() -> bool:
        return time_budget_s is not None and time.monotonic() - t0 > time_budget_s

    out = {"tagsRead": 0, "deezerAsked": 0, "deezerMatched": 0, "lyricsChecked": 0, "deezerUnreachable": False,
           "complete": False}
    admin = admin_flags(conn)

    # 1. file tags (+ title/album markers), for tracks never read or whose file changed.
    rows = conn.execute(
        "SELECT t.id, t.path, t.mtime, t.title, al.title AS album FROM tracks t JOIN albums al ON al.id = t.album_id "
        "LEFT JOIN track_explicit x ON x.track_id = t.id "
        "WHERE t.missing = 0 AND (x.track_id IS NULL OR x.tags_mtime IS NULL OR x.tags_mtime != t.mtime) "
        "ORDER BY t.added_at, t.id").fetchall()
    touched: list[str] = []
    pending_tags = len(rows)
    for r in rows:
        if out_of_time():
            break
        flag = read_file_flag(library_root / r["path"], r["title"], r["album"])
        conn.execute(
            "INSERT INTO track_explicit (track_id, tags_flag, tags_mtime, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(track_id) DO UPDATE SET tags_flag = excluded.tags_flag, tags_mtime = excluded.tags_mtime, "
            "updated_at = excluded.updated_at", (r["id"], flag, r["mtime"], now))
        touched.append(r["id"])
        out["tagsRead"] += 1
    if touched:
        recompute(conn, touched)
    pending_tags -= out["tagsRead"]

    # 2. Deezer, for tracks the admin and the tags leave open.
    pending_deezer = 0
    if deezer is not None and not out_of_time():
        retry = iso(datetime.now(UTC) - DEEZER_RETRY)
        rows = conn.execute(
            "SELECT t.id, t.title, t.duration_s, ar.name AS artist, al.title AS album FROM track_explicit x "
            "JOIN tracks t ON t.id = x.track_id JOIN artists ar ON ar.id = t.artist_id "
            "JOIN albums al ON al.id = t.album_id "
            "WHERE t.missing = 0 AND x.tags_flag IS NULL AND (x.deezer_flag IS NULL OR "
            "(x.deezer_flag = 'not_found' AND (x.deezer_at IS NULL OR x.deezer_at < ?))) ORDER BY t.added_at, t.id",
            (retry,)).fetchall()
        rows = [r for r in rows if r["id"] not in admin]
        pending_deezer = len(rows)
        fails = 0
        done: list[str] = []
        for r in rows:
            if out_of_time() or (deezer_limit is not None and out["deezerAsked"] >= deezer_limit):
                break
            artist = (r["artist"] or "").strip()
            if artist.casefold() in ("", "unknown artist", "various artists"):
                m = DeezerMatch("not_found")
            else:
                try:
                    m = deezer.lookup(artist, r["title"], r["album"], r["duration_s"])
                    fails = 0
                except DeezerError as e:
                    fails += 1
                    log.info("explicit: deezer unavailable for %s: %s", r["id"], e)
                    if fails >= 3:
                        out["deezerUnreachable"] = True
                        break
                    continue
                out["deezerAsked"] += 1
            out["deezerMatched"] += m.flag != "not_found"
            conn.execute("UPDATE track_explicit SET deezer_flag = ?, deezer_id = ?, deezer_at = ? WHERE track_id = ?",
                         (m.flag, m.deezer_id, now_iso(), r["id"]))
            done.append(r["id"])
        if done:
            recompute(conn, done)
        pending_deezer -= len(done)

    # 3. lyrics word list, last resort for what is still undecided.
    pending_lyrics = 0
    if not out_of_time():
        rows = conn.execute(
            "SELECT t.id, t.path, x.lyrics_at FROM track_explicit x JOIN tracks t ON t.id = x.track_id "
            "LEFT JOIN lyrics_cache lc ON lc.track_id = t.id "
            "WHERE t.missing = 0 AND x.status = 'unknown' AND x.source IS NULL AND (x.lyrics_flag IS NULL OR "
            "(x.lyrics_flag = 'none' AND lc.status = 'found' AND lc.fetched_at > COALESCE(x.lyrics_at, ''))) "
            "ORDER BY t.added_at, t.id").fetchall()
        rows = [r for r in rows if r["id"] not in admin]
        pending_lyrics = len(rows)
        done = []
        for r in rows:
            if out_of_time():
                break
            verdict, hits = lyrics_verdict(_lyrics_text(conn, library_root, r["id"], r["path"]))
            conn.execute("UPDATE track_explicit SET lyrics_flag = ?, lyrics_hits = ?, lyrics_at = ? WHERE track_id = ?",
                         (verdict, hits, now_iso(), r["id"]))
            done.append(r["id"])
            out["lyricsChecked"] += 1
        if done:
            recompute(conn, done)
        pending_lyrics -= len(done)

    out["remaining"] = {"tags": max(0, pending_tags), "deezer": max(0, pending_deezer),
                        "lyrics": max(0, pending_lyrics)}
    out["complete"] = not any(out["remaining"].values()) and not out["deezerUnreachable"]
    out["counts"] = counts(conn)
    return out


def counts(conn: sqlite3.Connection) -> dict:
    rows = dict(conn.execute(
        "SELECT COALESCE(x.status, 'unknown'), COUNT(*) FROM tracks t LEFT JOIN track_explicit x ON x.track_id = t.id "
        "WHERE t.missing = 0 GROUP BY 1").fetchall())
    return {s: rows.get(s, 0) for s in ("explicit", "probable", "clean", "unknown")}


# ---------- admin review ----------


def detail_row(r: sqlite3.Row | None, admin: str | None = None) -> dict:
    if r is None:
        return {"status": "unknown", "source": None, "admin": admin, "tags": None, "deezer": None, "lyrics": None,
                "lyricsHits": None}
    return {"status": r["status"], "source": r["source"], "admin": admin, "tags": r["tags_flag"],
            "deezer": r["deezer_flag"], "deezerId": r["deezer_id"], "lyrics": r["lyrics_flag"],
            "lyricsHits": r["lyrics_hits"], "updatedAt": r["updated_at"]}


def detail(conn: sqlite3.Connection, track_id: str) -> dict:
    r = conn.execute("SELECT * FROM track_explicit WHERE track_id = ?", (track_id,)).fetchone()
    return detail_row(r, admin_flags(conn, [track_id]).get(track_id))


def review(conn: sqlite3.Connection, statuses: list[str], source: str | None, q: str | None, limit: int,
           offset: int) -> dict:
    """Tracks for the admin review list, with their evidence. statuses: explicit/probable/clean/unknown."""
    where = ["t.missing = 0", f"COALESCE(x.status, 'unknown') IN ({','.join('?' * len(statuses))})"]
    params: list = list(statuses)
    if source:
        if source == "none":
            where.append("x.source IS NULL")
        else:
            where.append("x.source = ?")
            params.append(source)
    if q:
        for w in q.split()[:6]:
            esc = w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("(t.title LIKE ? ESCAPE '\\' OR ar.name LIKE ? ESCAPE '\\' OR al.title LIKE ? ESCAPE '\\')")
            params += [f"%{esc}%"] * 3
    base = ("FROM tracks t JOIN artists ar ON ar.id = t.artist_id JOIN albums al ON al.id = t.album_id "
            "LEFT JOIN track_explicit x ON x.track_id = t.id WHERE " + " AND ".join(where))
    total = conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT t.id, t.title, ar.name AS artist, al.title AS album, t.album_id, x.* {base} "
        "ORDER BY CASE COALESCE(x.status, 'unknown') WHEN 'probable' THEN 0 WHEN 'unknown' THEN 1 "
        "WHEN 'explicit' THEN 2 ELSE 3 END, ar.sort_name COLLATE NOCASE, al.title COLLATE NOCASE, "
        "COALESCE(t.disc_no, 1), t.track_no, t.id LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
    admin = admin_flags(conn, [r["id"] for r in rows])
    items = []
    for r in rows:
        ev = detail_row(r if r["track_id"] else None, admin.get(r["id"]))
        items.append({"trackId": r["id"], "title": r["title"], "artist": r["artist"], "album": r["album"],
                      "albumId": r["album_id"], **ev})
    return {"total": total, "items": items, "counts": counts(conn)}
