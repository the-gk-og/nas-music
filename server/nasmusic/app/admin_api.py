"""Admin console API under /api/v1/admin (admin only): users, status + jobs, analysis, library health,
server health, logs (+ SSE tail), backups, now playing, server settings, bug reports. User invites and
user PATCH / reset-password live in account_api (same prefix)."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from nasmusic import auth
from nasmusic.app import backups, bugreports, health, serversettings
from nasmusic.app.account_api import device_list
from nasmusic.app.context import AppContext, admin_device, ctx, db
from nasmusic.app.logbuffer import BUFFER
from nasmusic.app.scheduler import JOB_NAMES
from nasmusic.auth import users
from nasmusic.core import get_setting
from nasmusic.library import lrclib, queries

router = APIRouter(prefix="/admin", dependencies=[Depends(admin_device)])
Conn = sqlite3.Connection
Offset = Query(0, ge=0)
Limit = Query(200, ge=1, le=1000)


# ---------- users ----------


@router.get("/users/{user_id}/detail")
def user_detail(user_id: str, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    u = users.admin_view(conn, user_id)
    if u is None:
        raise HTTPException(404, "no such user")
    events = conn.execute("SELECT COUNT(*) FROM events WHERE user_id = ?", (user_id,)).fetchone()[0]
    return {**u, "devices": device_list(conn, c, user_id, None), "eventCount": events}


@router.delete("/users/{user_id}/devices/{device_id}", status_code=204)
def revoke_user_device(user_id: str, device_id: str, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    if not auth.revoke_device(conn, device_id, user_id):
        raise HTTPException(404, "no such device")
    if c.hub:
        c.hub.disconnect_device(device_id)
    return Response(status_code=204)


# ---------- status + jobs ----------


def job_status(conn: Conn, c: AppContext) -> list[dict]:
    nxt = c.jobs.next_runs() if c.jobs else {}
    return [{"name": n, "running": bool(c.jobs and c.jobs.is_running(n)), "next": nxt.get(n),
             "last": get_setting(conn, f"job.{n}"), "history": get_setting(conn, f"jobhist.{n}", []) or []}
            for n in JOB_NAMES]


@router.get("/status")
def status(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    stats = queries.stats(conn)
    stats.pop("starred", None)
    stats.pop("blocked", None)
    running = bool(c.jobs and c.jobs.is_running("analyse"))
    return {
        "library": {**stats, "lastScan": get_setting(conn, "job.scan")},
        "analysis": health.analysis_progress(conn, c.cfg.model_version, running),
        "lyrics": {"lrclibEnabled": c.cfg.lrclib_enabled, **lrclib.stats(conn)},
        "jobs": job_status(conn, c),
    }


@router.get("/jobs")
def jobs(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return job_status(conn, c)


@router.post("/jobs/{name}", status_code=202)
def run_job(name: Literal["scan", "full_scan", "analyse", "nightly", "pretranscode", "discovery", "lyrics",
                          "cleanup"], c: AppContext = Depends(ctx)):
    if c.jobs is None:
        raise HTTPException(503, "background jobs are not running")
    return JSONResponse({"started": c.jobs.trigger(name)}, status_code=202)


@router.get("/analysis/failed")
def analysis_failed(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return health.failed_analysis(conn, health.analysis_progress(conn, c.cfg.model_version)["model"])


class Retry(BaseModel):
    trackIds: list[str] | None = Field(default=None, max_length=10_000)


@router.post("/analysis/retry")
def analysis_retry(body: Retry | None = None, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    model = health.analysis_progress(conn, c.cfg.model_version)["model"]
    n = health.retry_failed(conn, model, (body or Retry()).trackIds)
    if n and c.jobs:
        c.jobs.trigger("analyse")
    return {"retried": n}


# ---------- library health ----------


@router.get("/health/library")
def library_health(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    model = health.analysis_progress(conn, c.cfg.model_version)["model"]
    return {
        "tracks": conn.execute("SELECT COUNT(*) FROM tracks WHERE missing = 0").fetchone()[0],
        "missing": conn.execute("SELECT COUNT(*) FROM tracks WHERE missing = 1").fetchone()[0],
        "duplicateGroups": len(health.duplicates(conn)),
        "missingTags": len(health.missing_tags(conn)),
        "missingArt": len(health.missing_art(conn, c.data_dir)),
        "unanalysed": health.unanalysed_count(conn, model),
        "orphanedCache": {k: v for k, v in health.cache_report(health.cache_orphans(conn, c.data_dir)).items()
                          if k != "byKind"},
        "backups": backups.total(c.cfg),
    }


@router.get("/health/missing")
def health_missing(offset: int = Offset, limit: int = Limit, conn: Conn = Depends(db)):
    return health.missing_tracks(conn, offset, limit)


class TrackIds(BaseModel):
    trackIds: list[str] | None = Field(default=None, max_length=100_000)


@router.delete("/health/missing")
def health_remove_missing(body: TrackIds | None = None, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    ids = (body or TrackIds()).trackIds
    if not conn.execute("SELECT 1 FROM tracks WHERE missing = 1 LIMIT 1").fetchone():
        return {"removed": 0, "backup": None}
    b = backups.create(c.cfg, conn, "prune")
    return {"removed": health.remove_missing(conn, ids), "backup": b["name"]}


@router.get("/health/duplicates")
def health_duplicates(conn: Conn = Depends(db)):
    return health.duplicates(conn)


@router.get("/health/missing-tags")
def health_missing_tags(offset: int = Offset, limit: int = Limit, conn: Conn = Depends(db)):
    return health.missing_tags(conn, offset, limit)


@router.get("/health/missing-art")
def health_missing_art(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return health.missing_art(conn, c.data_dir)


@router.get("/health/unanalysed")
def health_unanalysed(offset: int = Offset, limit: int = Limit, conn: Conn = Depends(db),
                      c: AppContext = Depends(ctx)):
    return health.unanalysed(conn, health.analysis_progress(conn, c.cfg.model_version)["model"], offset, limit)


@router.get("/health/cache")
def health_cache(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return health.cache_report(health.cache_orphans(conn, c.data_dir))


@router.post("/health/cache/clean")
def health_cache_clean(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return health.clean_cache(health.cache_orphans(conn, c.data_dir))


# ---------- server ----------


@router.get("/server")
def server_health(c: AppContext = Depends(ctx)):
    return health.server(c.cfg, c.server_id, c.version, c.started_at or time.time(), c.opus)


@router.delete("/opus-cache")
def clear_opus_cache(c: AppContext = Depends(ctx)):
    return health.clear_opus(c.cfg)


@router.get("/logs")
def logs(limit: int = Query(200, ge=1, le=2000), level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO",
         after: int | None = None):
    return BUFFER.since(after, level, limit)


@router.get("/logs/stream")
async def logs_stream(request: Request, after: int | None = None,
                      level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO",
                      maxS: float = Query(3600, gt=0, le=86400)):
    async def gen():
        last = after
        started = keepalive = time.monotonic()
        yield ": nas-music log tail\n\n"
        while time.monotonic() - started < maxS:
            for rec in BUFFER.since(last, level):
                last = rec["seq"]
                yield f"id: {rec['seq']}\ndata: {json.dumps(rec)}\n\n"
            if time.monotonic() - keepalive > 15:
                keepalive = time.monotonic()
                yield ": keepalive\n\n"
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# ---------- backups ----------


@router.get("/backups")
def list_backups(c: AppContext = Depends(ctx)):
    return backups.list_all(c.cfg)


@router.post("/backups")
def create_backup(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return backups.create(c.cfg, conn, "manual")


@router.get("/backups/{name}")
def download_backup(name: str, c: AppContext = Depends(ctx)):
    p = backups.path_for(c.cfg, name)
    if p is None:
        raise HTTPException(404, "no such backup")
    return FileResponse(p, media_type="application/vnd.sqlite3", filename=name, headers={"Cache-Control": "no-store"})


@router.delete("/backups/{name}", status_code=204)
def delete_backup(name: str, c: AppContext = Depends(ctx)):
    p = backups.path_for(c.cfg, name)
    if p is None:
        raise HTTPException(404, "no such backup")
    p.unlink()
    return Response(status_code=204)


# ---------- now playing ----------


@router.get("/now-playing")
def now_playing(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    out = []
    if c.hub is None:
        return out
    for uid in list(c.hub.users):
        devs = c.hub.devices_for(uid)
        state = c.hub.snapshot(uid) or {}
        if not any(d["online"] for d in devs) and not state.get("activeDeviceId"):
            continue
        u = users.get(conn, uid)
        if u is None:
            continue
        track = queries.track(conn, uid, state["trackId"]) if state.get("trackId") else None
        out.append({"user": {"id": uid, "username": u.username}, "devices": devs, "state": state, "track": track})
    return sorted(out, key=lambda x: x["user"]["username"].casefold())


# ---------- server settings ----------


@router.get("/settings")
def get_server_settings(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return serversettings.view(c.base_cfg or c.cfg, serversettings.overrides(conn))


@router.put("/settings")
def put_server_settings(body: dict[str, Any], conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    old_tz = c.cfg.timezone
    try:
        ov = serversettings.put(conn, body)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    c.cfg = serversettings.apply(c.base_cfg or c.cfg, ov)
    c.opus.max_bytes = int(c.cfg.opus_cache_gb * 1024**3)
    if c.jobs is not None:
        c.jobs.cfg = c.cfg
        if c.cfg.timezone != old_tz:
            c.jobs.reschedule()
    return serversettings.view(c.base_cfg or c.cfg, ov)


# ---------- bug reports ----------


@router.get("/bug-reports")
def bug_reports(conn: Conn = Depends(db)):
    return bugreports.list_all(conn)


@router.get("/bug-reports/{rid}")
def bug_report(rid: str, conn: Conn = Depends(db)):
    r = bugreports.get(conn, rid)
    if r is None:
        raise HTTPException(404, "no such report")
    return r


@router.delete("/bug-reports/{rid}", status_code=204)
def delete_bug_report(rid: str, conn: Conn = Depends(db)):
    if not bugreports.delete(conn, rid):
        raise HTTPException(404, "no such report")
    return Response(status_code=204)
