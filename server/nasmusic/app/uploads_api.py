"""Uploading songs (schema v7; logic in nasmusic.library.uploads).

User:  GET    /uploads                      - can I upload, quota, my uploads and unfinished sessions (?all=true: admins)
       POST   /uploads                      - start a resumable upload {filename, bytes, visibility?}
       GET    /uploads/{id}                 - session state (offset to resume from)
       PATCH  /uploads/{id}                 - append raw bytes at `Upload-Offset` (header) / ?offset=
       DELETE /uploads/{id}                 - cancel an unfinished upload
       PATCH  /uploads/files/{id}           - change visibility (everyone | private)
       DELETE /uploads/files/{id}           - delete an uploaded file (uploader or admin; upload_root only)
Admin: GET    /admin/uploads                - per-user permission, quota and usage
       PUT    /admin/users/{id}/uploads     - {canUpload, quotaBytes} (null = back to the default)
"""
from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from nasmusic.app.context import AppContext, admin_device, ctx, db, device
from nasmusic.auth import Device
from nasmusic.library import uploads

router = APIRouter(dependencies=[Depends(device)])
admin = APIRouter(prefix="/admin", dependencies=[Depends(admin_device)])
Conn = sqlite3.Connection
Visibility = Literal["everyone", "private"]


def _err(e: uploads.UploadError):
    if e.offset is not None:
        return JSONResponse({"detail": e.detail, "offset": e.offset}, status_code=e.status,
                            headers={"Upload-Offset": str(e.offset)})
    raise HTTPException(e.status, e.detail)


def _scan(c: AppContext) -> None:
    if c.jobs is not None:
        c.jobs.request_upload_scan()


@router.get("/uploads")
def list_uploads(all: bool = False, me: Device = Depends(device), conn: Conn = Depends(db),
                 c: AppContext = Depends(ctx)):
    return uploads.list_mine(conn, c.cfg, me.user_id, all_users=all and me.is_admin)


class CreateBody(BaseModel):
    model_config = {"extra": "forbid"}
    filename: str = Field(min_length=1, max_length=512)
    bytes: int = Field(ge=1)
    visibility: Visibility = "everyone"


@router.post("/uploads", status_code=201)
def create_upload(body: CreateBody, me: Device = Depends(device), conn: Conn = Depends(db),
                  c: AppContext = Depends(ctx)):
    try:
        return uploads.create(conn, c.cfg, me.user_id, body.filename, body.bytes, body.visibility)
    except uploads.UploadError as e:
        return _err(e)


@router.get("/uploads/{session_id}")
def get_upload(session_id: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    try:
        out = uploads.get_session(conn, me.user_id, session_id)
    except uploads.UploadError as e:
        return _err(e)
    return JSONResponse(out, headers={"Upload-Offset": str(out["offset"]), "Cache-Control": "no-store"})


@router.patch("/uploads/{session_id}")
async def append_upload(session_id: str, request: Request, offset: int | None = Query(None, ge=0),
                        upload_offset: int | None = Header(None, alias="Upload-Offset", ge=0),
                        me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    off = upload_offset if upload_offset is not None else offset
    if off is None:
        raise HTTPException(422, "Upload-Offset header (or ?offset=) is required")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > uploads.MAX_CHUNK:
        raise HTTPException(413, f"chunk larger than {uploads.MAX_CHUNK} bytes")
    buf = bytearray()
    async for part in request.stream():
        buf += part
        if len(buf) > uploads.MAX_CHUNK:
            raise HTTPException(413, f"chunk larger than {uploads.MAX_CHUNK} bytes")
    try:
        out = await run_in_threadpool(uploads.append, conn, c.cfg, me.user_id, session_id, off, bytes(buf),
                                      me.username)
    except uploads.UploadError as e:
        return _err(e)
    if out["status"] == "done":
        _scan(c)
    return JSONResponse(out, headers={"Upload-Offset": str(out["offset"])})


@router.delete("/uploads/{session_id}", status_code=204)
def cancel_upload(session_id: str, me: Device = Depends(device), conn: Conn = Depends(db),
                  c: AppContext = Depends(ctx)):
    try:
        uploads.cancel(conn, c.cfg, me.user_id, session_id, me.is_admin)
    except uploads.UploadError as e:
        return _err(e)


class VisibilityBody(BaseModel):
    model_config = {"extra": "forbid"}
    visibility: Visibility


@router.patch("/uploads/files/{upload_id}")
def set_visibility(upload_id: str, body: VisibilityBody, me: Device = Depends(device), conn: Conn = Depends(db)):
    try:
        return uploads.set_visibility(conn, me.user_id, upload_id, body.visibility, me.is_admin)
    except uploads.UploadError as e:
        return _err(e)


@router.delete("/uploads/files/{upload_id}")
def delete_upload(upload_id: str, me: Device = Depends(device), conn: Conn = Depends(db),
                  c: AppContext = Depends(ctx)):
    try:
        out = uploads.delete(conn, c.cfg, me.user_id, upload_id, me.is_admin)
    except uploads.UploadError as e:
        return _err(e)
    _scan(c)
    return out


# ---------- admin ----------


@admin.get("/uploads")
def admin_uploads(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return {"enabled": c.cfg.upload_root is not None, "defaultQuotaBytes": uploads.default_quota(c.cfg) or None,
            "maxFileBytes": uploads.max_file_bytes(c.cfg), "users": uploads.admin_overview(conn, c.cfg)}


class AccessBody(BaseModel):
    model_config = {"extra": "forbid"}
    canUpload: bool | None = None
    quotaBytes: int | None = Field(None, ge=0)


@admin.put("/users/{user_id}/uploads")
def admin_set_access(user_id: str, body: AccessBody, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        raise HTTPException(404, "no such user")
    sent = body.model_fields_set
    uploads.set_access(conn, user_id,
                       allowed=body.canUpload if "canUpload" in sent else ...,
                       quota_bytes=body.quotaBytes if "quotaBytes" in sent else ...)
    return next(u for u in uploads.admin_overview(conn, c.cfg) if u["userId"] == user_id)
