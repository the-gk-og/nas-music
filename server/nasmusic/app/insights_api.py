"""Discovery phase 2 under /api/v1: /release-radar, /wrapped (+ /admin/wrapped), /blends."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from nasmusic.app import blends, insights
from nasmusic.app.context import AppContext, admin_device, ctx, db, device
from nasmusic.auth import Device

router = APIRouter(dependencies=[Depends(device)])
admin = APIRouter(prefix="/admin", dependencies=[Depends(admin_device)])
Conn = sqlite3.Connection


def _unavailable(fn, *a):
    try:
        return fn(*a)
    except NotImplementedError as e:
        raise HTTPException(503, "not available yet (AI engine update pending)") from e
    except insights.NotFound as e:
        raise HTTPException(404, str(e)) from e


# ---------- Release Radar ----------


@router.get("/release-radar")
def release_radar(me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return _unavailable(insights.release_radar, conn, c.cfg, me.user_id)


# ---------- Wrapped ----------


def _year(year: int | None) -> int:
    return year if year is not None else datetime.now(UTC).year


@router.get("/wrapped")
def wrapped(year: int | None = Query(None, ge=2000, le=2100), me: Device = Depends(device), conn: Conn = Depends(db),
            c: AppContext = Depends(ctx)):
    return _unavailable(insights.wrapped, conn, c.cfg, me.user_id, _year(year))


@router.get("/wrapped/years")
def wrapped_years(me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return _unavailable(insights.wrapped_years, conn, c.cfg, me.user_id)


@admin.get("/wrapped")
def admin_wrapped(year: int | None = Query(None, ge=2000, le=2100), me: Device = Depends(device),
                  conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return _unavailable(insights.wrapped_server, conn, c.cfg, me.user_id, _year(year))


# ---------- Blends ----------


def _b(fn, *a):
    try:
        return fn(*a)
    except blends.BlendError as e:
        raise HTTPException(e.status, e.detail) from e


class BlendBody(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    usernames: list[str] = Field(min_length=1, max_length=blends.MAX_MEMBERS - 1)


class InviteBody(BaseModel):
    usernames: list[str] = Field(min_length=1, max_length=blends.MAX_MEMBERS - 1)


class RenameBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.get("/blends")
def list_blends(me: Device = Depends(device), conn: Conn = Depends(db)):
    return blends.list_for(conn, me.user_id)


@router.get("/blends/candidates")
def blend_candidates(me: Device = Depends(device), conn: Conn = Depends(db)):
    return blends.candidates(conn, me.user_id)


@router.post("/blends", status_code=201)
def create_blend(body: BlendBody, me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return _b(blends.create, conn, c.cfg, me.user_id, body.name, body.usernames)


@router.get("/blends/{blend_id}")
def get_blend(blend_id: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    return _b(blends.get, conn, me.user_id, blend_id)


@router.patch("/blends/{blend_id}")
def rename_blend(blend_id: str, body: RenameBody, me: Device = Depends(device), conn: Conn = Depends(db)):
    return _b(blends.rename, conn, me.user_id, blend_id, body.name)


@router.post("/blends/{blend_id}/invite")
def invite_blend(blend_id: str, body: InviteBody, me: Device = Depends(device), conn: Conn = Depends(db)):
    return _b(blends.invite, conn, me.user_id, blend_id, body.usernames)


@router.post("/blends/{blend_id}/accept")
def accept_blend(blend_id: str, me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return _b(blends.respond, conn, c.cfg, me.user_id, blend_id, True)


@router.post("/blends/{blend_id}/decline", status_code=204)
def decline_blend(blend_id: str, me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    _b(blends.respond, conn, c.cfg, me.user_id, blend_id, False)
    return Response(status_code=204)


@router.post("/blends/{blend_id}/leave", status_code=204)
def leave_blend(blend_id: str, me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    _b(blends.leave, conn, c.cfg, me.user_id, blend_id)
    return Response(status_code=204)


@router.post("/blends/{blend_id}/refresh")
def refresh_blend(blend_id: str, me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return _b(blends.refresh, conn, c.cfg, me.user_id, blend_id)
