"""Automatic tag fixing and the duplicate finder, under /api/v1/admin (admins only).
See nasmusic.library.autotag / duplicates. Accepting writes tag overrides (never the files)."""
from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from nasmusic.app.context import AppContext, admin_device, ctx, db
from nasmusic.app.tags_api import TagValues
from nasmusic.auth import Device
from nasmusic.library import autotag, duplicates, overrides, tagedit

router = APIRouter(prefix="/admin", dependencies=[Depends(admin_device)])
Conn = sqlite3.Connection
MAX_IDS = 5000


class AutotagValues(TagValues):
    model_config = {"extra": "forbid"}
    mbRecordingId: str | None = Field(default=None, max_length=36)
    mbReleaseGroupId: str | None = Field(default=None, max_length=36)
    mbArtistId: str | None = Field(default=None, max_length=36)
    mbAlbumArtistId: str | None = Field(default=None, max_length=36)


class AcceptBody(BaseModel):
    trackIds: list[str] = Field(default_factory=list, max_length=MAX_IDS)
    groupIds: list[str] = Field(default_factory=list, max_length=MAX_IDS)
    minConfidence: float | None = Field(default=None, ge=0, le=1)
    edits: dict[str, AutotagValues] = Field(default_factory=dict)
    fields: list[str] | None = Field(default=None, max_length=20)
    cover: bool | None = None


class SelectBody(BaseModel):
    trackIds: list[str] = Field(default_factory=list, max_length=MAX_IDS)
    groupIds: list[str] = Field(default_factory=list, max_length=MAX_IDS)


class TrackIds(BaseModel):
    trackIds: list[str] = Field(min_length=1, max_length=MAX_IDS)


class ResolveBody(BaseModel):
    keepTrackId: str
    hideTrackIds: list[str] = Field(min_length=1, max_length=100)
    # v10: also move the other copies to the trash (deleted when it is purged) instead of only hiding them.
    remove: bool = False


def _db_fields(names: list[str]) -> list[str]:
    out = []
    for n in names:
        f = overrides.FROM_API.get(n)
        if f is None or f not in autotag.SUGGEST_FIELDS:
            raise HTTPException(422, f"unknown field {n!r}")
        out.append(f)
    return out


@router.get("/autotag/status")
def status(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    st = autotag.status(conn, c.cfg)
    st["running"] = bool(c.jobs and c.jobs.is_running("autotag"))
    return st


@router.get("/autotag/groups")
def groups(status: Literal["pending", "accepted", "rejected"] = "pending",
           minConfidence: float = Query(0.0, ge=0, le=1), q: str | None = Query(None, max_length=200),
           limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
           conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return autotag.groups(conn, c.cfg, status, minConfidence, q, limit, offset)


@router.post("/autotag/accept")
def accept(body: AcceptBody, me: Device = Depends(admin_device), conn: Conn = Depends(db),
           c: AppContext = Depends(ctx)):
    """Accept pending suggestions picked by trackIds / groupIds and/or every track at >= minConfidence."""
    if not body.trackIds and not body.groupIds and body.minConfidence is None:
        raise HTTPException(422, "trackIds, groupIds or minConfidence required")
    edits = {tid: {overrides.FROM_API[k]: v for k, v in e.model_dump(exclude_unset=True).items()}
             for tid, e in body.edits.items()}
    try:
        return autotag.accept(c.cfg, conn, me.user_id, track_ids=body.trackIds, group_ids=body.groupIds,
                              min_confidence=body.minConfidence, edits=edits,
                              fields=_db_fields(body.fields) if body.fields is not None else None,
                              cover=body.cover, transport=c.http_transport)
    except tagedit.EditError as e:
        raise HTTPException(e.status, e.detail) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.post("/autotag/reject")
def reject(body: SelectBody, me: Device = Depends(admin_device), conn: Conn = Depends(db)):
    if not body.trackIds and not body.groupIds:
        raise HTTPException(422, "trackIds or groupIds required")
    return autotag.reject(conn, me.user_id, track_ids=body.trackIds, group_ids=body.groupIds)


@router.post("/autotag/reopen")
def reopen(body: TrackIds, me: Device = Depends(admin_device), conn: Conn = Depends(db),
           c: AppContext = Depends(ctx)):
    """Back to pending; for accepted suggestions the overrides the accept wrote are reverted."""
    return autotag.reopen(c.cfg, conn, me.user_id, body.trackIds)


@router.post("/autotag/rematch")
def rematch(body: TrackIds, conn: Conn = Depends(db)):
    return autotag.rematch(conn, body.trackIds)


@router.get("/autotag/cover/{release_mbid}")
def cover(release_mbid: str, c: AppContext = Depends(ctx)):
    """Front cover of a release from the Cover Art Archive (proxied and cached; the web CSP allows 'self' only)."""
    try:
        data = autotag.fetch_cover(c.cfg, release_mbid.lower(), c.http_transport)
    except autotag.CoverError as e:
        raise HTTPException(502, str(e)) from e
    if not data:
        raise HTTPException(404, "no cover")
    mt = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    return Response(data, media_type=mt, headers={"Cache-Control": "private, max-age=86400"})


@router.get("/duplicates")
def duplicate_groups(conn: Conn = Depends(db)):
    return duplicates.groups(conn)


@router.post("/duplicates/resolve")
def resolve(body: ResolveBody, me: Device = Depends(admin_device), conn: Conn = Depends(db),
            c: AppContext = Depends(ctx)):
    try:
        out = duplicates.resolve(conn, me.user_id, body.keepTrackId, body.hideTrackIds)
    except duplicates.DuplicateError as e:
        raise HTTPException(e.status, e.detail) from e
    if body.remove:
        from nasmusic.library import cleanup

        hide = [h for h in body.hideTrackIds if h != body.keepTrackId]
        r = cleanup.remove(conn, c.cfg, me.user_id, track_ids=hide, reason="duplicate copy")
        out.update(removed=r["removed"], batchId=r["batchId"], purgeAfter=r["purgeAfter"])
    return out


@router.post("/duplicates/unhide")
def unhide(body: TrackIds, conn: Conn = Depends(db)):
    return duplicates.unhide(conn, body.trackIds)
