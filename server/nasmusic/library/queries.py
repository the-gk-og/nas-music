"""Read queries returning API-shaped dicts (see components/schemas in api/openapi.yaml).
Missing tracks, and albums/artists with no present tracks, are hidden."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from nasmusic.library import art

TRACK_SELECT = """
SELECT t.id, t.title, t.artist_id, ar.name AS artist, t.album_id, al.title AS album, t.track_no, t.disc_no,
       t.year, t.genre, t.duration_s, t.flac_bytes, t.starred_at, t.rating
FROM tracks t JOIN artists ar ON ar.id = t.artist_id JOIN albums al ON al.id = t.album_id
"""

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


def track_dict(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"], "title": r["title"], "artistId": r["artist_id"], "artist": r["artist"],
        "albumId": r["album_id"], "album": r["album"], "trackNo": r["track_no"], "discNo": r["disc_no"],
        "year": r["year"], "genre": r["genre"], "durationS": r["duration_s"], "flacBytes": r["flac_bytes"],
        "starred": r["starred_at"] is not None, "rating": r["rating"],
    }


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


def album(conn: sqlite3.Connection, album_id: str, data_dir: Path) -> dict | None:
    r = conn.execute(ALBUM_SELECT + " WHERE al.id = ?", (album_id,)).fetchone()
    if r is None:
        return None
    d = album_dict(r, data_dir)
    rows = conn.execute(
        TRACK_SELECT + " WHERE t.album_id = ? AND t.missing = 0 "
        "ORDER BY COALESCE(t.disc_no, 1), t.track_no IS NULL, t.track_no, t.path",
        (album_id,),
    )
    d["tracks"] = [track_dict(t) for t in rows]
    return d


def album_art_info(conn: sqlite3.Connection, album_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT id, art_path FROM albums WHERE id = ?", (album_id,)).fetchone()


def track(conn: sqlite3.Connection, track_id: str) -> dict | None:
    r = conn.execute(TRACK_SELECT + " WHERE t.id = ? AND t.missing = 0", (track_id,)).fetchone()
    return track_dict(r) if r else None


def tracks_by_ids(conn: sqlite3.Connection, ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = TRACK_SELECT + f" WHERE t.id IN ({','.join('?' * len(chunk))}) AND t.missing = 0"
        for r in conn.execute(q, chunk):
            out[r["id"]] = track_dict(r)
    return out


def track_file(conn: sqlite3.Connection, track_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, path, mtime, flac_bytes FROM tracks WHERE id = ? AND missing = 0", (track_id,)
    ).fetchone()


def favourites(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        TRACK_SELECT + " WHERE t.starred_at IS NOT NULL AND t.missing = 0 ORDER BY t.starred_at DESC, t.id"
    )
    return [track_dict(r) for r in rows]


def _like(q: str) -> str:
    esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def search(conn: sqlite3.Connection, q: str, limit: int, data_dir: Path) -> dict:
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
        TRACK_SELECT + f" WHERE t.missing = 0 AND {c} ORDER BY t.title COLLATE NOCASE LIMIT ?", (*p, limit)
    )
    return {
        "artists": [artist_dict(r) for r in ar_rows],
        "albums": [album_dict(r, data_dir) for r in al_rows],
        "tracks": [track_dict(r) for r in tr_rows],
    }


def stats(conn: sqlite3.Connection) -> dict:
    r = conn.execute(
        "SELECT COUNT(*) AS tracks, COALESCE(SUM(duration_s), 0) AS dur, COALESCE(SUM(flac_bytes), 0) AS bytes, "
        "SUM(starred_at IS NOT NULL) AS starred, SUM(blocked) AS blocked FROM tracks WHERE missing = 0"
    ).fetchone()
    missing = conn.execute("SELECT COUNT(*) FROM tracks WHERE missing = 1").fetchone()[0]
    albums_n = conn.execute(
        "SELECT COUNT(DISTINCT album_id) FROM tracks WHERE missing = 0"
    ).fetchone()[0]
    artists_n = conn.execute(
        "SELECT COUNT(DISTINCT al.artist_id) FROM albums al WHERE EXISTS "
        "(SELECT 1 FROM tracks t WHERE t.album_id = al.id AND t.missing = 0)"
    ).fetchone()[0]
    return {
        "tracks": r["tracks"], "albums": albums_n, "artists": artists_n, "missing": missing,
        "hours": round(r["dur"] / 3600, 1), "gb": round(r["bytes"] / 1e9, 1),
        "starred": r["starred"] or 0, "blocked": r["blocked"] or 0,
    }
