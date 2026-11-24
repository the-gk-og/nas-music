"""Classical music (schema v9): composers, and works with their movements (see nasmusic.library.classical).

Clients show these when the user turns on classical mode (`library.classical` in /me/settings); tracks carry
`work` / `workId` / `movement` / `movementNo` / `composer` whether or not it is on."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from nasmusic.app.context import AppContext, ctx, db, device
from nasmusic.auth import Device
from nasmusic.library import art, classical
from nasmusic.library.queries import TRACK_SELECT, fam, track_dict

router = APIRouter(dependencies=[Depends(device)])
Conn = sqlite3.Connection

_WORKS = """
SELECT t.album_id, t.work, MIN(t.composer) AS composer, al.title AS album, ar.name AS artist, al.year,
       al.art_path, COUNT(*) AS n, SUM(t.duration_s) AS dur,
       MIN(COALESCE(t.disc_no, 1) * 10000 + COALESCE(t.track_no, 0)) AS first_pos
FROM tracks t JOIN albums al ON al.id = t.album_id JOIN artists ar ON ar.id = al.artist_id
WHERE t.missing = 0 AND t.work IS NOT NULL AND t.work != ''{fam}{where}
GROUP BY t.album_id, t.work
"""


def _work_dict(r: sqlite3.Row, data_dir) -> dict:
    return {"id": classical.work_id(r["album_id"], r["work"]), "title": r["work"], "composer": r["composer"],
            "albumId": r["album_id"], "album": r["album"], "artist": r["artist"], "year": r["year"],
            "trackCount": r["n"], "durationS": round(r["dur"] or 0.0, 3),
            "hasArt": art.has_art(data_dir, r["art_path"], r["album_id"])}


@router.get("/library/composers")
def composers(me: Device = Depends(device), conn: Conn = Depends(db)):
    rows = conn.execute(
        "SELECT MIN(t.composer) AS name, COUNT(DISTINCT t.album_id || char(0) || t.work) AS works, "
        "COUNT(DISTINCT t.album_id) AS albums, COUNT(*) AS n FROM tracks t "
        f"WHERE t.missing = 0 AND t.work != '' AND t.composer IS NOT NULL AND t.composer != ''"
        f"{fam(conn, me.user_id)} GROUP BY t.composer COLLATE NOCASE ORDER BY t.composer COLLATE NOCASE")
    return [{"name": r["name"], "workCount": r["works"], "albumCount": r["albums"], "trackCount": r["n"]}
            for r in rows]


@router.get("/library/works")
def works(composer: str | None = Query(None, max_length=500), albumId: str | None = None,
          offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000), me: Device = Depends(device),
          conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    where, args = "", []
    if composer is not None:
        where += " AND t.composer = ? COLLATE NOCASE"
        args.append(composer)
    if albumId is not None:
        where += " AND t.album_id = ?"
        args.append(albumId)
    order = "first_pos" if albumId is not None else "composer COLLATE NOCASE, t.work COLLATE NOCASE, al.year"
    rows = conn.execute(_WORKS.format(fam=fam(conn, me.user_id), where=where) + f" ORDER BY {order} LIMIT ? OFFSET ?",
                        (*args, limit, offset))
    return [_work_dict(r, c.data_dir) for r in rows]


@router.get("/library/works/{work_id}")
def work(work_id: str, me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    parts = classical.split_work_id(work_id)
    if parts is None:
        raise HTTPException(404, "not found")
    album_id, _ = parts
    f = fam(conn, me.user_id)
    for r in conn.execute(_WORKS.format(fam=f, where=" AND t.album_id = ?"), (album_id,)):
        if classical.work_id(album_id, r["work"]) != work_id:
            continue
        rows = conn.execute(
            TRACK_SELECT + f" WHERE t.album_id = ? AND t.work = ? AND t.missing = 0{f} "
            "ORDER BY t.movement_no IS NULL, t.movement_no, COALESCE(t.disc_no, 1), t.track_no, t.path",
            (me.user_id, album_id, r["work"]))
        return {**_work_dict(r, c.data_dir), "tracks": [track_dict(t) for t in rows]}
    raise HTTPException(404, "not found")
