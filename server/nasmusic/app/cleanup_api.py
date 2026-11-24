"""Admin console "Cleanup" (schema v10): pruning reports, remove to trash, restore, purge, the delete list, the
fake-lossless verdicts, and an admin preview stream that also plays hidden and removed copies (to compare
duplicates by ear). See nasmusic.library.cleanup / nasmusic.library.lossless."""
from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from nasmusic.app.context import AppContext, admin_device, ctx, db
from nasmusic.library import cleanup, lossless
from nasmusic.stream import media_type, opus_label, original_label, snap_bitrate
from nasmusic.stream.transcode import TranscodeError

router = APIRouter(prefix="/admin", dependencies=[Depends(admin_device)])
Conn = sqlite3.Connection
Ids = list[str]


def _run(fn, *a, **k):
    try:
        return fn(*a, **k)
    except cleanup.CleanupError as e:
        raise HTTPException(e.status, e.detail) from e


@router.get("/cleanup/summary")
def summary(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    """Every report's count and size, trash and delete-list counts, fake-lossless progress, and the settings."""
    return {"reports": cleanup.reports_summary(conn), **cleanup.counts(conn), "lossless": lossless.counts(conn),
            "trashDays": c.cfg.trash_days, "deleteFiles": c.cfg.library_delete_files}


@router.get("/cleanup/reports/{report_id}")
def report(report_id: str, threshold: int | None = Query(None, ge=0, le=100_000),
           limit: int = Query(200, ge=1, le=5000), offset: int = Query(0, ge=0), conn: Conn = Depends(db)):
    items = _run(cleanup.report, conn, report_id, threshold)
    return {"total": len(items), "bytes": sum(i["bytes"] or 0 for i in items), "items": items[offset:offset + limit]}


@router.get("/cleanup/search")
def search(q: str = Query(..., min_length=1, max_length=200), conn: Conn = Depends(db)):
    return cleanup.search(conn, q.strip())


class RemoveBody(BaseModel):
    trackIds: Ids | None = Field(default=None, max_length=50_000)
    albumIds: Ids | None = Field(default=None, max_length=5_000)
    artistIds: Ids | None = Field(default=None, max_length=1_000)
    reason: str | None = Field(default=None, max_length=200)


@router.post("/cleanup/remove")
def remove(body: RemoveBody, me=Depends(admin_device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    """Move songs (or whole albums / artists) to the trash. They vanish from the library at once and can be
    restored until the trash is purged."""
    return _run(cleanup.remove, conn, c.cfg, me.user_id, body.trackIds, body.albumIds, body.artistIds, body.reason)


@router.get("/cleanup/trash")
def trash(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return {"trashDays": c.cfg.trash_days, "deleteFiles": c.cfg.library_delete_files, "batches": cleanup.trash(conn)}


@router.get("/cleanup/trash/{batch_id}")
def trash_batch(batch_id: str, conn: Conn = Depends(db)):
    items = cleanup.trash_tracks(conn, batch_id)
    if not items:
        raise HTTPException(404, "no such batch")
    return items


class RestoreBody(BaseModel):
    batchIds: Ids | None = Field(default=None, max_length=10_000)
    trackIds: Ids | None = Field(default=None, max_length=50_000)


@router.post("/cleanup/trash/restore")
def restore(body: RestoreBody, conn: Conn = Depends(db)):
    return cleanup.restore(conn, body.batchIds, body.trackIds)


class PurgeBody(BaseModel):
    batchIds: Ids = Field(min_length=1, max_length=10_000)


@router.post("/cleanup/trash/purge")
def purge(body: PurgeBody, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    """Empty these batches now instead of waiting for the trash period. Files are deleted when
    library_delete_files is on (uploads always); otherwise the songs stay hidden and join the delete list."""
    return cleanup.purge(conn, c.cfg, body.batchIds)


@router.get("/cleanup/delete-list")
def delete_list(format: Literal["json", "sh"] = "json", conn: Conn = Depends(db)):
    items = cleanup.delete_list(conn)
    if format == "sh":
        return PlainTextResponse(cleanup.delete_script(items), media_type="text/x-shellscript",
                                 headers={"Content-Disposition": 'attachment; filename="delete-list.sh"',
                                          "Cache-Control": "no-store"})
    return items


class DismissBody(BaseModel):
    trackIds: Ids = Field(min_length=1, max_length=50_000)
    dismissed: bool = True


@router.post("/cleanup/lossless/dismiss")
def dismiss_lossless(body: DismissBody, conn: Conn = Depends(db)):
    """Mark fake-lossless verdicts as fine (or flag them again)."""
    return {"updated": lossless.dismiss(conn, body.trackIds, body.dismissed)}


@router.get("/preview/{track_id}")
def preview(track_id: str, format: Literal["opus", "original"] = "original",
            bitrate: int = Query(192, ge=1, le=1000), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    """Stream any track for comparing copies by ear, including hidden duplicates and removed songs.
    `original` sends the file as stored (browsers play FLAC, MP3, AAC, Opus, WAV); `opus` transcodes."""
    row = conn.execute("SELECT id, path, mtime, codec, bitrate, sample_rate, bits_per_sample FROM tracks WHERE id = ?",
                       (track_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "not found")
    src = c.cfg.library_root / row["path"]
    if not src.is_file():
        raise HTTPException(404, "file missing")
    if format == "original":
        return FileResponse(src, media_type=media_type(row["path"]), headers={
            "Cache-Control": "no-store",
            "X-Stream-Format": original_label(row["codec"], row["bits_per_sample"], row["sample_rate"], row["bitrate"])})
    kbps = snap_bitrate(bitrate)
    try:
        out = c.opus.get(src, track_id, kbps, row["mtime"])
    except TranscodeError as e:
        raise HTTPException(500, "transcode failed") from e
    return FileResponse(out, media_type="audio/ogg", headers={"Cache-Control": "no-store",
                                                             "X-Stream-Format": opus_label(kbps)})
