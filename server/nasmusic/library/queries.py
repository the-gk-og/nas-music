"""Read queries returning API-shaped dicts (see components/schemas in api/openapi.yaml).

Track dicts are per user (`starred` / `rating` are the caller's): every TRACK_SELECT query takes the
user id as its FIRST parameter. Missing tracks, and albums/artists with no present tracks, are hidden
from browsing (playlists and queues still list them, flagged `missing`)."""
from __future__ import annotations

import posixpath
import sqlite3
from pathlib import Path

from nasmusic.library import art, loudness

TRACK_COLS = """t.id, t.title, t.artist_id, ar.name AS artist, t.album_id, al.title AS album, t.track_no, t.disc_no,
       t.year, t.genre, t.duration_s, t.flac_bytes, t.missing, ut.starred_at, ut.rating,
       t.rg_track_gain, t.rg_track_peak, t.rg_album_gain, t.rg_album_peak"""
TRACK_FROM = """FROM tracks t JOIN artists ar ON ar.id = t.artist_id JOIN albums al ON al.id = t.album_id
LEFT JOIN user_tracks ut ON ut.track_id = t.id AND ut.user_id = ?"""
TRACK_SELECT = f"SELECT {TRACK_COLS}\n{TRACK_FROM}\n"

ALBUM_SELECT = """
SELECT al.id, al.title, al.artist_id, ar.name AS artist, al.year, al.mbid, al.art_path, al.added_at,
       s.n AS track_count, s.dur AS duration_s
FROM albums al JOIN artists ar ON ar.id = al.artist_id
JOIN (SELECT album_id, COUNT(*) AS n, SUM(duration_s) AS dur FROM tracks WHERE missing = 0 GROUP BY album_id) s
  ON s.album_id = al.id
"""

ARTIST_SELECT = """
SELECT ar.id, ar.name, ar.mbid, ar.sort_name, COUNT(DISTINCT al.id) AS album_count
FROM artists ar JOIN albums al ON al.artist_id = ar.id
WHERE EXISTS (SELECT 1 FROM tracks t WHERE t.album_id = al.id AND t.missing = 0)
"""

ALBUM_ORDER = "ORDER BY ar.sort_name COLLATE NOCASE, al.title COLLATE NOCASE, COALESCE(t.disc_no, 1), " \
              "t.track_no IS NULL, t.track_no, t.path"


def track_dict(r: sqlite3.Row) -> dict:
    d = {
        "id": r["id"], "title": r["title"], "artistId": r["artist_id"], "artist": r["artist"],
        "albumId": r["album_id"], "album": r["album"], "trackNo": r["track_no"], "discNo": r["disc_no"],
        "year": r["year"], "genre": r["genre"], "durationS": r["duration_s"], "flacBytes": r["flac_bytes"],
        "starred": r["starred_at"] is not None, "rating": r["rating"],
    }
    if r["missing"]:
        d["missing"] = True
    rg = loudness.replay_gain(r)
    if rg is not None:
        d["replayGain"] = rg
    return d


def album_dict(r: sqlite3.Row, data_dir: Path) -> dict:
    return {
        "id": r["id"], "title": r["title"], "artistId": r["artist_id"], "artist": r["artist"],
        "year": r["year"], "trackCount": r["track_count"], "durationS": r["duration_s"] or 0.0,
        "hasArt": art.has_art(data_dir, r["art_path"], r["id"]), "mbid": r["mbid"],
    }


def artist_dict(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "name": r["name"], "albumCount": r["album_count"], "mbid": r["mbid"]}


def artists(conn: sqlite3.Connection, offset: int, limit: int) -> list[dict]:
    rows = conn.execute(
        ARTIST_SELECT + " GROUP BY ar.id ORDER BY ar.sort_name COLLATE NOCASE, ar.id LIMIT ? OFFSET ?",
        (limit, offset),
    )
    return [artist_dict(r) for r in rows]


def artist(conn: sqlite3.Connection, artist_id: str, data_dir: Path) -> dict | None:
    r = conn.execute(ARTIST_SELECT + " AND ar.id = ? GROUP BY ar.id", (artist_id,)).fetchone()
    if r is None:
        return None
    d = artist_dict(r)
    d["albums"] = albums(conn, data_dir, artist_id=artist_id, sort="year", offset=0, limit=10_000)
    return d


_ALBUM_SORT = {
    "name": "al.title COLLATE NOCASE, al.id",
    "year": "al.year IS NULL, al.year, al.title COLLATE NOCASE, al.id",
    "added": "al.added_at DESC, al.title COLLATE NOCASE, al.id",
}


def albums(conn: sqlite3.Connection, data_dir: Path, artist_id: str | None, sort: str, offset: int,
           limit: int) -> list[dict]:
    where, params = "", []
    if artist_id:
        where, params = " WHERE al.artist_id = ?", [artist_id]
    rows = conn.execute(
        ALBUM_SELECT + where + f" ORDER BY {_ALBUM_SORT.get(sort, _ALBUM_SORT['name'])} LIMIT ? OFFSET ?",
        (*params, limit, offset),
    )
    return [album_dict(r, data_dir) for r in rows]


def album(conn: sqlite3.Connection, uid: str, album_id: str, data_dir: Path) -> dict | None:
    r = conn.execute(ALBUM_SELECT + " WHERE al.id = ?", (album_id,)).fetchone()
    if r is None:
        return None
    d = album_dict(r, data_dir)
    rows = conn.execute(
        TRACK_SELECT + " WHERE t.album_id = ? AND t.missing = 0 "
        "ORDER BY COALESCE(t.disc_no, 1), t.track_no IS NULL, t.track_no, t.path",
        (uid, album_id),
    )
    d["tracks"] = [track_dict(t) for t in rows]
    return d


def album_art_info(conn: sqlite3.Connection, album_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT id, art_path FROM albums WHERE id = ?", (album_id,)).fetchone()


def track(conn: sqlite3.Connection, uid: str, track_id: str) -> dict | None:
    r = conn.execute(TRACK_SELECT + " WHERE t.id = ? AND t.missing = 0", (uid, track_id)).fetchone()
    return track_dict(r) if r else None


def tracks_by_ids(conn: sqlite3.Connection, uid: str, ids: list[str], include_missing: bool = False) -> dict[str, dict]:
    out: dict[str, dict] = {}
    uniq = list(dict.fromkeys(ids))
    for i in range(0, len(uniq), 500):
        chunk = uniq[i:i + 500]
        q = TRACK_SELECT + f" WHERE t.id IN ({','.join('?' * len(chunk))})"
        if not include_missing:
            q += " AND t.missing = 0"
        for r in conn.execute(q, (uid, *chunk)):
            out[r["id"]] = track_dict(r)
    return out


def track_file(conn: sqlite3.Connection, track_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, path, mtime, flac_bytes, duration_s FROM tracks WHERE id = ? AND missing = 0", (track_id,)
    ).fetchone()


def track_info(conn: sqlite3.Connection, uid: str, track_id: str) -> dict | None:
    r = conn.execute(
        f"SELECT {TRACK_COLS}, t.path, t.folder, t.album_artist, t.composer, t.lyricist, t.comment, t.codec, "
        "t.bitrate, t.sample_rate, t.bits_per_sample, t.channels, t.added_at, "
        "COALESCE(ut.play_count, 0) AS play_count, COALESCE(ut.skip_count, 0) AS skip_count, ut.last_played "
        f"{TRACK_FROM} WHERE t.id = ? AND t.missing = 0", (uid, track_id),
    ).fetchone()
    if r is None:
        return None
    return {
        **track_dict(r), "fileName": posixpath.basename(r["path"]), "folder": r["folder"],
        "albumArtist": r["album_artist"], "composer": r["composer"], "lyricist": r["lyricist"],
        "comment": r["comment"], "codec": r["codec"], "bitrateKbps": r["bitrate"], "sampleRate": r["sample_rate"],
        "bitsPerSample": r["bits_per_sample"], "channels": r["channels"], "fileBytes": r["flac_bytes"],
        "addedAt": r["added_at"], "playCount": r["play_count"], "skipCount": r["skip_count"],
        "lastPlayed": r["last_played"],
    }


_TRACK_SORT = {
    "title": "t.title COLLATE NOCASE, ar.sort_name COLLATE NOCASE, t.id",
    "added": "t.added_at DESC, al.title COLLATE NOCASE, COALESCE(t.disc_no, 1), t.track_no, t.id",
    "year": "t.year IS NULL, t.year DESC, al.title COLLATE NOCASE, COALESCE(t.disc_no, 1), t.track_no, t.id",
}


def all_tracks(conn: sqlite3.Connection, uid: str, sort: str, offset: int, limit: int) -> list[dict]:
    rows = conn.execute(
        TRACK_SELECT + f" WHERE t.missing = 0 ORDER BY {_TRACK_SORT[sort]} LIMIT ? OFFSET ?", (uid, limit, offset))
    return [track_dict(r) for r in rows]


# ---------- smart lists ----------

LIST_KINDS = ("favourites", "recently_added", "recently_played", "most_played", "not_played")
_LISTS = {
    "favourites": ("ut.starred_at IS NOT NULL", "ut.starred_at DESC, t.id"),
    "recently_added": ("1", "t.added_at DESC, al.title COLLATE NOCASE, COALESCE(t.disc_no, 1), t.track_no, t.id"),
    "recently_played": ("ut.last_played IS NOT NULL", "ut.last_played DESC, t.id"),
    "most_played": ("ut.play_count > 0", "ut.play_count DESC, ut.last_played DESC, t.id"),
    "not_played": ("COALESCE(ut.play_count, 0) = 0",
                   "ar.sort_name COLLATE NOCASE, al.title COLLATE NOCASE, COALESCE(t.disc_no, 1), t.track_no, t.id"),
}


def smart_list(conn: sqlite3.Connection, uid: str, kind: str, offset: int = 0, limit: int = 10_000) -> list[dict]:
    where, order = _LISTS[kind]
    rows = conn.execute(TRACK_SELECT + f" WHERE t.missing = 0 AND {where} ORDER BY {order} LIMIT ? OFFSET ?",
                        (uid, limit, offset))
    return [track_dict(r) for r in rows]


def favourites(conn: sqlite3.Connection, uid: str) -> list[dict]:
    return smart_list(conn, uid, "favourites")


# ---------- folders + genres ----------


def folders(conn: sqlite3.Connection, uid: str, path: str) -> dict | None:
    path = path.strip("/")
    counts = conn.execute("SELECT folder, COUNT(*) AS n FROM tracks WHERE missing = 0 GROUP BY folder").fetchall()
    prefix = f"{path}/" if path else ""
    children: dict[str, int] = {}
    exists = path == ""
    for r in counts:
        f = r["folder"]
        if f == path:
            exists = True
            continue
        if f.startswith(prefix):
            exists = True
            name = f[len(prefix):].split("/", 1)[0]
            children[name] = children.get(name, 0) + r["n"]
    if not exists:
        return None
    rows = conn.execute(TRACK_SELECT + " WHERE t.folder = ? AND t.missing = 0 ORDER BY t.path", (uid, path))
    parent = None if path == "" else posixpath.dirname(path)
    return {
        "path": path, "parent": parent,
        "folders": [{"name": n, "path": f"{prefix}{n}", "trackCount": c}
                    for n, c in sorted(children.items(), key=lambda kv: kv[0].casefold())],
        "tracks": [track_dict(r) for r in rows],
    }


def genres(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT MIN(genre) AS name, COUNT(*) AS n, COUNT(DISTINCT album_id) AS albums FROM tracks "
        "WHERE missing = 0 AND genre IS NOT NULL AND genre != '' GROUP BY genre COLLATE NOCASE "
        "ORDER BY genre COLLATE NOCASE")
    return [{"name": r["name"], "trackCount": r["n"], "albumCount": r["albums"]} for r in rows]


def genre_tracks(conn: sqlite3.Connection, uid: str, genre: str, offset: int, limit: int) -> list[dict]:
    rows = conn.execute(
        TRACK_SELECT + f" WHERE t.missing = 0 AND t.genre = ? COLLATE NOCASE {ALBUM_ORDER} LIMIT ? OFFSET ?",
        (uid, genre, limit, offset))
    return [track_dict(r) for r in rows]


# ---------- search + stats ----------


def _like(q: str) -> str:
    esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def search(conn: sqlite3.Connection, uid: str, q: str, limit: int, data_dir: Path) -> dict:
    """Case-insensitive substring search on each word (all words must match)."""
    words = [w for w in q.split() if w][:8]
    if not words:
        return {"artists": [], "albums": [], "tracks": []}

    def cond(cols: list[str]) -> tuple[str, list[str]]:
        parts, params = [], []
        for w in words:
            parts.append("(" + " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in cols) + ")")
            params.extend([_like(w)] * len(cols))
        return " AND ".join(parts), params

    c, p = cond(["ar.name"])
    ar_rows = conn.execute(
        ARTIST_SELECT + f" AND {c} GROUP BY ar.id ORDER BY ar.sort_name COLLATE NOCASE LIMIT ?", (*p, limit)
    )
    c, p = cond(["al.title", "ar.name"])
    al_rows = conn.execute(ALBUM_SELECT + f" WHERE {c} ORDER BY al.title COLLATE NOCASE LIMIT ?", (*p, limit))
    c, p = cond(["t.title", "ar.name", "al.title"])
    tr_rows = conn.execute(
        TRACK_SELECT + f" WHERE t.missing = 0 AND {c} ORDER BY t.title COLLATE NOCASE LIMIT ?", (uid, *p, limit)
    )
    return {
        "artists": [artist_dict(r) for r in ar_rows],
        "albums": [album_dict(r, data_dir) for r in al_rows],
        "tracks": [track_dict(r) for r in tr_rows],
    }


def stats(conn: sqlite3.Connection, uid: str | None = None) -> dict:
    r = conn.execute(
        "SELECT COUNT(*) AS tracks, COALESCE(SUM(duration_s), 0) AS dur, COALESCE(SUM(flac_bytes), 0) AS bytes "
        "FROM tracks WHERE missing = 0"
    ).fetchone()
    missing = conn.execute("SELECT COUNT(*) FROM tracks WHERE missing = 1").fetchone()[0]
    albums_n = conn.execute("SELECT COUNT(DISTINCT album_id) FROM tracks WHERE missing = 0").fetchone()[0]
    artists_n = conn.execute(
        "SELECT COUNT(DISTINCT al.artist_id) FROM albums al WHERE EXISTS "
        "(SELECT 1 FROM tracks t WHERE t.album_id = al.id AND t.missing = 0)"
    ).fetchone()[0]
    starred = blocked = 0
    if uid:
        u = conn.execute("SELECT SUM(starred_at IS NOT NULL), SUM(blocked) FROM user_tracks WHERE user_id = ?",
                         (uid,)).fetchone()
        starred, blocked = u[0] or 0, u[1] or 0
    users_n = conn.execute("SELECT COUNT(*) FROM users WHERE disabled = 0").fetchone()[0]
    return {
        "tracks": r["tracks"], "albums": albums_n, "artists": artists_n, "missing": missing,
        "hours": round(r["dur"] / 3600, 1), "gb": round(r["bytes"] / 1e9, 1),
        "starred": starred, "blocked": blocked, "users": users_n,
    }
