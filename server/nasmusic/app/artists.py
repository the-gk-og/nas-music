"""Artist pages (v7): everything the artist screen shows in one response (api/openapi.yaml ArtistPage).

Library parts (top songs by the caller's plays, albums / singles, appears-on, all tracks) are family-mode
filtered like every other list; the metadata (photo, bio, genres, years active, links) comes from the shared
`artist_info` cache (nasmusic.library.artistinfo); similar artists come from the AI's audio embeddings."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from nasmusic.app import ai_adapter
from nasmusic.library import art, artistinfo, queries

TOP_N = 10
SINGLE_MAX_TRACKS = 3


def photo_url(data_dir: Path, artist_id: str) -> str | None:
    return f"art/artist/{artist_id}" if artistinfo.art_path(data_dir, artist_id).exists() else None


def fallback_art(conn: sqlite3.Connection, data_dir: Path, artist_id: str) -> str | None:
    """An album cover for artists without a photo (newest album with art, then any album they appear on)."""
    rows = conn.execute(
        "SELECT al.id, al.art_path FROM albums al WHERE al.artist_id = ? "
        "UNION ALL SELECT al.id, al.art_path FROM albums al WHERE al.id IN "
        "(SELECT album_id FROM tracks WHERE artist_id = ? AND missing = 0)", (artist_id, artist_id)).fetchall()
    for r in rows:
        if art.has_art(data_dir, r["art_path"], r["id"]):
            return f"art/{r['id']}"
    return None


def art_url(conn: sqlite3.Connection, data_dir: Path, artist_id: str) -> str | None:
    return photo_url(data_dir, artist_id) or fallback_art(conn, data_dir, artist_id)


def _visible_artist(conn: sqlite3.Connection, uid: str, artist_id: str) -> sqlite3.Row | None:
    """The artist if the user can see any track by them (as track artist or album artist)."""
    f = queries.fam(conn, uid)
    return conn.execute(
        f"SELECT ar.id, ar.name, ar.mbid FROM artists ar WHERE ar.id = ? AND EXISTS (SELECT 1 FROM tracks t "
        f"JOIN albums al ON al.id = t.album_id WHERE (t.artist_id = ar.id OR al.artist_id = ar.id) "
        f"AND t.missing = 0{f})", (artist_id,)).fetchone()


def tracks(conn: sqlite3.Connection, uid: str, artist_id: str) -> list[dict]:
    """Every track by the artist (their albums, then appearances), album order. For shuffle / play all."""
    f = queries.fam(conn, uid)
    rows = conn.execute(
        queries.TRACK_SELECT + f" WHERE (t.artist_id = ? OR al.artist_id = ?) AND t.missing = 0{f} "
        "ORDER BY al.artist_id <> ?, al.year IS NULL, al.year, al.title COLLATE NOCASE, COALESCE(t.disc_no, 1), "
        "t.track_no IS NULL, t.track_no, t.path", (uid, artist_id, artist_id, artist_id))
    return [queries.track_dict(r) for r in rows]


def top_tracks(conn: sqlite3.Connection, uid: str, artist_id: str, n: int = TOP_N) -> list[dict]:
    """By the caller's plays; topped up with what everyone plays, then album order."""
    f = queries.fam(conn, uid)
    rows = conn.execute(
        queries.TRACK_SELECT + f" WHERE (t.artist_id = ? OR al.artist_id = ?) AND t.missing = 0{f} "
        "ORDER BY COALESCE(ut.play_count, 0) DESC, "
        "(SELECT COALESCE(SUM(u2.play_count), 0) FROM user_tracks u2 WHERE u2.track_id = t.id) DESC, "
        "ut.starred_at IS NULL, al.year IS NULL, al.year, COALESCE(t.disc_no, 1), t.track_no, t.id LIMIT ?",
        (uid, artist_id, artist_id, n))
    out = []
    for r in rows:
        d = queries.track_dict(r)
        out.append(d)
    plays = {r[0]: r[1] for r in conn.execute(
        f"SELECT track_id, play_count FROM user_tracks WHERE user_id = ? AND track_id IN "
        f"({','.join('?' * len(out))})", (uid, *[t['id'] for t in out]))} if out else {}
    for d in out:
        d["playCount"] = plays.get(d["id"], 0)
    return out


def page(conn: sqlite3.Connection, data_dir: Path, ai_cfg, uid: str, artist_id: str, *,
         info: dict, similar_limit: int = 12) -> dict | None:
    a = _visible_artist(conn, uid, artist_id)
    if a is None:
        return None
    f = queries.fam(conn, uid)
    own = queries.albums(conn, data_dir, artist_id=artist_id, sort="year", offset=0, limit=10_000, uid=uid)
    albums = [x for x in own if x["trackCount"] > SINGLE_MAX_TRACKS]
    singles = [x for x in own if x["trackCount"] <= SINGLE_MAX_TRACKS]
    appears = [queries.album_dict(r, data_dir) for r in conn.execute(
        queries.album_select(f) + " WHERE al.artist_id <> ? AND al.id IN (SELECT t.album_id FROM tracks t "
        f"WHERE t.artist_id = ? AND t.missing = 0{f}) ORDER BY al.year IS NULL, al.year DESC, al.title COLLATE NOCASE",
        (artist_id, artist_id))]
    st = conn.execute(
        f"SELECT COUNT(*), COALESCE(SUM(t.duration_s), 0), COALESCE(SUM(ut.play_count), 0) FROM tracks t "
        f"JOIN albums al ON al.id = t.album_id LEFT JOIN user_tracks ut ON ut.track_id = t.id AND ut.user_id = ? "
        f"WHERE (t.artist_id = ? OR al.artist_id = ?) AND t.missing = 0{f}", (uid, artist_id, artist_id)).fetchone()
    similar = []
    for sid, score in ai_adapter.similar_artists(conn, ai_cfg, artist_id, similar_limit * 2):
        r = conn.execute(queries.artist_select(f) + " AND ar.id = ? GROUP BY ar.id", (sid,)).fetchone()
        if r is None:
            continue
        similar.append({**queries.artist_dict(r), "similarity": score, "artUrl": art_url(conn, data_dir, sid)})
        if len(similar) >= similar_limit:
            break
    photo = photo_url(data_dir, artist_id)
    return {
        "id": a["id"], "name": a["name"], "mbid": a["mbid"],
        "artUrl": photo or fallback_art(conn, data_dir, artist_id), "photoUrl": photo,
        "info": info,
        "stats": {"trackCount": st[0], "durationS": round(st[1], 3), "playCount": st[2],
                  "albumCount": len(albums), "singleCount": len(singles)},
        "topTracks": top_tracks(conn, uid, artist_id),
        "albums": albums, "singles": singles, "appearsOn": appears, "similar": similar,
        "radioSeed": {"type": "artist", "id": a["id"], "title": a["name"]},
    }
