"""Family mode and explicit-content flags (schema v6).

User:  GET/PUT /me/family            - the caller's family mode (enabled, hideProbable, hideUnknown; locked)
Admin: GET /admin/family             - every user's family mode
       PUT /admin/users/{id}/family  - set it, lock it on, set/clear the unlock PIN
       GET /admin/explicit           - review list (unknown / probable by default) with each source's evidence
       POST /admin/explicit          - set or clear an admin verdict on tracks and/or whole albums (bulk)

A LOCKED user may only tighten their filter. Loosening it (turning it off, or showing probable/unknown
tracks again) needs the PIN an admin set (5 wrong PINs in 15 minutes -> 429); without a PIN only an admin can.
Turning family mode off with the PIN also removes the lock. The filter itself is homebeat.db.family."""
from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from homebeat.app.context import AppContext, admin_device, ctx, db, device
from homebeat.auth import Device, hash_password, verify_password
from homebeat.core import now_iso
from homebeat.db import family
from homebeat.library import explicit, tagedit

router = APIRouter(dependencies=[Depends(device)])
admin = APIRouter(prefix="/admin", dependencies=[Depends(admin_device)])
Conn = sqlite3.Connection
PIN_PATTERN = r"^[0-9]{4,12}$"


def _write(conn: Conn, uid: str, p: family.Policy, pin_hash: str | None | type(...) = ...,
           locked_by: str | None | type(...) = ...) -> None:
    row = conn.execute("SELECT pin_hash, locked_by FROM family_mode WHERE user_id = ?", (uid,)).fetchone()
    ph = (row["pin_hash"] if row else None) if pin_hash is ... else pin_hash
    lb = (row["locked_by"] if row else None) if locked_by is ... else locked_by
    conn.execute(
        "INSERT INTO family_mode (user_id, enabled, hide_probable, hide_unknown, locked, pin_hash, locked_by, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET enabled = excluded.enabled, "
        "hide_probable = excluded.hide_probable, hide_unknown = excluded.hide_unknown, locked = excluded.locked, "
        "pin_hash = excluded.pin_hash, locked_by = excluded.locked_by, updated_at = excluded.updated_at",
        (uid, int(p.enabled), int(p.hide_probable), int(p.hide_unknown), int(p.locked), ph, lb, now_iso()))


def _notify(c: AppContext, conn: Conn, uid: str) -> dict:
    out = family.policy(conn, uid).as_api()
    if c.hub is not None:
        try:
            c.hub.notify_family(uid, out)
        except AttributeError:
            pass
    return out


# ---------- the user's own ----------


class FamilyBody(BaseModel):
    model_config = {"extra": "forbid"}
    enabled: bool | None = None
    hideProbable: bool | None = None
    hideUnknown: bool | None = None
    pin: str | None = Field(default=None, max_length=12)   # needed to loosen a locked filter


@router.get("/me/family")
def get_family(me: Device = Depends(device), conn: Conn = Depends(db)):
    return family.policy(conn, me.user_id).as_api()


@router.put("/me/family")
def put_family(body: FamilyBody, me: Device = Depends(device), conn: Conn = Depends(db),
               c: AppContext = Depends(ctx)):
    cur = family.policy(conn, me.user_id)
    new = family.Policy(
        enabled=cur.enabled if body.enabled is None else body.enabled,
        hide_probable=cur.hide_probable if body.hideProbable is None else body.hideProbable,
        hide_unknown=cur.hide_unknown if body.hideUnknown is None else body.hideUnknown,
        locked=cur.locked, has_pin=cur.has_pin)
    loosens = (cur.enabled and not new.enabled) or (cur.hide_probable and not new.hide_probable) or \
              (cur.hide_unknown and not new.hide_unknown)
    if cur.locked and loosens:
        key = f"family:{me.user_id}"
        if c.pin_limiter.blocked(key):
            raise HTTPException(429, "too many wrong PINs; try again in 15 minutes")
        row = conn.execute("SELECT pin_hash FROM family_mode WHERE user_id = ?", (me.user_id,)).fetchone()
        if not row or not row[0]:
            raise HTTPException(403, "family mode is locked by an admin")
        if not body.pin or not verify_password(body.pin, row[0]):
            c.pin_limiter.fail(key)
            raise HTTPException(403, "wrong PIN")
        if not new.enabled:  # the PIN turns it off: that unlocks it too
            new = family.Policy(enabled=False, hide_probable=new.hide_probable, hide_unknown=new.hide_unknown,
                                locked=False)
            _write(conn, me.user_id, new, locked_by=None)
            return _notify(c, conn, me.user_id)
    _write(conn, me.user_id, new)
    return _notify(c, conn, me.user_id)


# ---------- admin: per-user family mode + lock ----------


class AdminFamilyBody(BaseModel):
    model_config = {"extra": "forbid"}
    enabled: bool | None = None
    hideProbable: bool | None = None
    hideUnknown: bool | None = None
    locked: bool | None = None                    # locking turns family mode on
    pin: str | None = Field(default=None, pattern=PIN_PATTERN)   # set the unlock PIN (4-12 digits)
    clearPin: bool = False                        # admin-only unlock from now on


def _admin_row(conn: Conn, r: sqlite3.Row) -> dict:
    return {"userId": r["id"], "username": r["username"], "isAdmin": bool(r["is_admin"]),
            **family.policy(conn, r["id"]).as_api(), "lockedBy": r["locked_by_name"]}


_USERS = ("SELECT u.id, u.username, u.is_admin, lb.username AS locked_by_name FROM users u "
          "LEFT JOIN family_mode f ON f.user_id = u.id LEFT JOIN users lb ON lb.id = f.locked_by ")


@admin.get("/family")
def list_family(conn: Conn = Depends(db)):
    return [_admin_row(conn, r) for r in conn.execute(_USERS + "ORDER BY u.username COLLATE NOCASE")]


@admin.put("/users/{user_id}/family")
def admin_put_family(user_id: str, body: AdminFamilyBody, me: Device = Depends(admin_device), conn: Conn = Depends(db),
                     c: AppContext = Depends(ctx)):
    if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        raise HTTPException(404, "no such user")
    cur = family.policy(conn, user_id)
    locked = cur.locked if body.locked is None else body.locked
    enabled = cur.enabled if body.enabled is None else body.enabled
    if locked:
        enabled = True
    new = family.Policy(enabled=enabled,
                        hide_probable=cur.hide_probable if body.hideProbable is None else body.hideProbable,
                        hide_unknown=cur.hide_unknown if body.hideUnknown is None else body.hideUnknown,
                        locked=locked)
    pin_hash = ...
    if body.clearPin:
        pin_hash = None
    elif body.pin:
        pin_hash = hash_password(body.pin)
    locked_by = (me.user_id if locked and not cur.locked else None if not locked else ...)
    _write(conn, user_id, new, pin_hash=pin_hash, locked_by=locked_by)
    _notify(c, conn, user_id)
    r = conn.execute(_USERS + "WHERE u.id = ?", (user_id,)).fetchone()
    return _admin_row(conn, r)


# ---------- admin: explicit flags ----------

Status = Literal["explicit", "probable", "clean", "unknown"]


@admin.get("/explicit")
def explicit_review(status: list[Status] = Query(default=["probable", "unknown"]),
                    source: Literal["admin", "tags", "deezer", "lyrics", "none"] | None = None,
                    q: str | None = Query(None, max_length=200), limit: int = Query(100, ge=1, le=1000),
                    offset: int = Query(0, ge=0), conn: Conn = Depends(db)):
    return explicit.review(conn, list(dict.fromkeys(status)), source, q, limit, offset)


class ExplicitSet(BaseModel):
    model_config = {"extra": "forbid"}
    trackIds: list[str] = Field(default_factory=list, max_length=tagedit.MAX_TRACKS)
    albumIds: list[str] = Field(default_factory=list, max_length=500)
    value: Literal["explicit", "clean"] | None   # null = back to the automatic verdict


@admin.post("/explicit")
def explicit_set(body: ExplicitSet, me: Device = Depends(admin_device), conn: Conn = Depends(db),
                 c: AppContext = Depends(ctx)):
    """Bulk admin verdict, stored as the tag override 'explicit' (audit log included)."""
    ids = list(body.trackIds)
    try:
        for al in body.albumIds:
            ids += tagedit.album_track_ids(conn, al)
        ids = list(dict.fromkeys(ids))
        if not ids:
            raise HTTPException(422, "no tracks")
        if body.value is None:
            res = tagedit.edit(c.cfg, conn, me.user_id, ids, None, ["explicit"])
        else:
            res = tagedit.edit(c.cfg, conn, me.user_id, ids, {"explicit": body.value})
    except tagedit.EditError as e:
        raise HTTPException(e.status, e.detail) from e
    return {**res, "tracks": len(ids), "status": family.status_of(conn, ids) if len(ids) <= 500 else None}
