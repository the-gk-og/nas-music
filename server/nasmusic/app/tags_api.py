"""Admin tag editing under /api/v1/admin/tags (admins only). Edits are DB overrides layered on the file
tags (the library is read-only); see nasmusic.library.tagedit."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from nasmusic.app.context import AppContext, admin_device, ctx, db
from nasmusic.auth import Device
from nasmusic.library import art, musicbrainz, overrides, tagedit

router = APIRouter(prefix="/admin/tags", dependencies=[Depends(admin_device)])
Conn = sqlite3.Connection
MAX_COVER_BYTES = 15 * 1024 * 1024


class TagValues(BaseModel):
    """Fields to set. Omitted = unchanged; null or "" = clear the tag (lyrics: back to the file's)."""
    model_config = {"extra": "forbid"}
    title: str | None = Field(default=None, max_length=500)
    artist: str | None = Field(default=None, max_length=500)
    albumArtist: str | None = Field(default=None, max_length=500)
    album: str | None = Field(default=None, max_length=500)
    trackNo: int | None = Field(default=None, ge=0, le=999)
    discNo: int | None = Field(default=None, ge=0, le=999)
    year: int | None = Field(default=None, ge=0, le=9999)
    genre: str | None = Field(default=None, max_length=500)
    lyrics: str | None = Field(default=None, max_length=200_000)


RevertField = str


class TagEdit(BaseModel):
    trackIds: list[str] = Field(default_factory=list, max_length=tagedit.MAX_TRACKS)
    set: TagValues | None = None
    revert: list[RevertField] = Field(default_factory=list, max_length=20)


class AlbumEdit(BaseModel):
    set: TagValues | None = None
    revert: list[RevertField] = Field(default_factory=list, max_length=20)


def _db_set(v: TagValues | None) -> dict:
    if v is None:
        return {}
    return {overrides.FROM_API[k]: val for k, val in v.model_dump(exclude_unset=True).items()}


def _db_revert(fields: list[str]) -> list[str]:
    out = []
    for f in fields:
        if f == "*":
            out.append("*")
        elif f in overrides.FROM_API:
            out.append(overrides.FROM_API[f])
        else:
            raise HTTPException(422, f"unknown field {f!r}")
    return out


def _run(fn, *a, **k):
    try:
        return fn(*a, **k)
    except tagedit.EditError as e:
        raise HTTPException(e.status, e.detail) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/tracks/{track_id}")
def track_tags(track_id: str, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return _run(tagedit.detail, c.cfg, conn, track_id)


@router.post("/edit")
def edit(body: TagEdit, me: Device = Depends(admin_device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    """Single-track or bulk edit/revert. Returns {batchId, changed}."""
    return _run(tagedit.edit, c.cfg, conn, me.user_id, body.trackIds, _db_set(body.set), _db_revert(body.revert))


@router.post("/albums/{album_id}")
def edit_album(album_id: str, body: AlbumEdit, me: Device = Depends(admin_device), conn: Conn = Depends(db),
               c: AppContext = Depends(ctx)):
    """Edit every track of an album (album-level fields: album, albumArtist, year, genre, ...)."""
    ids = _run(tagedit.album_track_ids, conn, album_id)
    return _run(tagedit.edit, c.cfg, conn, me.user_id, ids, _db_set(body.set), _db_revert(body.revert))


@router.put("/albums/{album_id}/cover")
async def put_cover(album_id: str, request: Request, me: Device = Depends(admin_device), conn: Conn = Depends(db),
                    c: AppContext = Depends(ctx)):
    """Upload a cover for an album: the raw image as the request body (image/jpeg, image/png, image/webp)."""
    data = await request.body()
    if not data:
        raise HTTPException(422, "empty body")
    if len(data) > MAX_COVER_BYTES:
        raise HTTPException(413, "image too large")
    ids = _run(tagedit.album_track_ids, conn, album_id)
    try:
        sha = art.store_upload(c.data_dir, data)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    res = _run(tagedit.edit, c.cfg, conn, me.user_id, ids, {"cover": sha})
    new_album = conn.execute("SELECT album_id FROM tracks WHERE id = ?", (ids[0],)).fetchone()[0]
    return {**res, "albumId": new_album}


@router.delete("/albums/{album_id}/cover")
def delete_cover(album_id: str, me: Device = Depends(admin_device), conn: Conn = Depends(db),
                 c: AppContext = Depends(ctx)):
    ids = _run(tagedit.album_track_ids, conn, album_id)
    return _run(tagedit.edit, c.cfg, conn, me.user_id, ids, None, ["cover"])


@router.get("/history")
def history(trackId: str | None = None, albumId: str | None = None, limit: int = Query(100, ge=1, le=1000),
            offset: int = Query(0, ge=0), conn: Conn = Depends(db)):
    return tagedit.history(conn, trackId, albumId, limit, offset)


@router.get("/musicbrainz")
def musicbrainz_search(trackId: str | None = None, albumId: str | None = None,
                       q: str | None = Query(None, max_length=300), artist: str | None = Query(None, max_length=300),
                       conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    """Suggestions from MusicBrainz. trackId -> recordings (per release); albumId -> releases.
    `q`/`artist` override the search terms (default: the current effective tags)."""
    try:
        if trackId:
            r = conn.execute("SELECT t.title, ar.name AS artist, al.title AS album FROM tracks t "
                             "JOIN artists ar ON ar.id = t.artist_id JOIN albums al ON al.id = t.album_id "
                             "WHERE t.id = ?", (trackId,)).fetchone()
            if r is None:
                raise HTTPException(404, "no such track")
            return {"kind": "recordings", "results": musicbrainz.search_recordings(
                q or r["title"], artist if artist is not None else r["artist"], None if q else r["album"],
                transport=c.http_transport)}
        if albumId:
            r = conn.execute("SELECT al.title, ar.name AS artist FROM albums al JOIN artists ar ON ar.id = al.artist_id "
                             "WHERE al.id = ?", (albumId,)).fetchone()
            if r is None:
                raise HTTPException(404, "no such album")
            return {"kind": "releases", "results": musicbrainz.search_releases(
                q or r["title"], artist if artist is not None else r["artist"], transport=c.http_transport)}
    except musicbrainz.LookupError_ as e:
        raise HTTPException(502, str(e)) from e
    raise HTTPException(422, "trackId or albumId required")


@router.get("/musicbrainz/release/{mbid}")
def musicbrainz_release(mbid: str, c: AppContext = Depends(ctx)):
    if not all(ch in "0123456789abcdef-" for ch in mbid.lower()) or len(mbid) != 36:
        raise HTTPException(422, "bad MBID")
    try:
        return musicbrainz.release(mbid, transport=c.http_transport)
    except musicbrainz.LookupError_ as e:
        raise HTTPException(502, str(e)) from e
