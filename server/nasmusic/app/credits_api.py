"""v12 song credits under /api/v1: GET /library/tracks/{id}/credits (producers, songwriters, musicians of one
track, each with how many other songs in the caller's library they are on) and GET /people/{personId}
(everything in the caller's library one person is credited on). Family-mode filtered like every list."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from nasmusic.app.context import AppContext, ctx, db, device
from nasmusic.auth import Device
from nasmusic.core import open_db
from nasmusic.library import credits

router = APIRouter(dependencies=[Depends(device)])
Conn = sqlite3.Connection


def fetcher(c: AppContext) -> credits.Fetcher:
    f = getattr(c, "credits_fetcher", None)
    if f is None:
        f = c.credits_fetcher = credits.Fetcher(lambda: open_db(c.cfg), c.http_transport)
    return f


@router.get("/library/tracks/{track_id}/credits")
def track_credits(track_id: str, me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    pending = False
    if c.cfg.credits_enabled and conn.execute("SELECT 1 FROM tracks WHERE id = ?", (track_id,)).fetchone():
        f = fetcher(c)
        pending = f.pending(track_id)
        if not pending and credits.is_due(credits.lookup_row(conn, track_id)):
            f.request(track_id)
            pending = True
    out = credits.track_credits(conn, me.user_id, track_id, pending=pending)
    if out is None:
        raise HTTPException(404, "not found")
    return out


@router.get("/people/{person_id}")
def person(person_id: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    out = credits.person(conn, me.user_id, person_id)
    if out is None:
        raise HTTPException(404, "not found")
    return out
