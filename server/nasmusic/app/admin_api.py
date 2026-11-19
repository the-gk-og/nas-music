"""Admin console API under /api/v1/admin (admin only). Paths and shapes follow the web console's client
(web/src/lib/api/admin.ts) so the UI works unchanged; a few extras (SSE log tail, /server, user detail,
per-device revoke) are additions. Invites and user PATCH / reset-password live in account_api."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from nasmusic import auth
from nasmusic.app import backups, bugreports, health, serversettings
from nasmusic.app.account_api import device_list
from nasmusic.app.context import AppContext, admin_device, ctx, db
from nasmusic.app.logbuffer import BUFFER
from nasmusic.app.scheduler import JOB_NAMES
from nasmusic.auth import users
from nasmusic.core import get_setting, set_setting
from nasmusic.library import lrclib, queries

router = APIRouter(prefix="/admin", dependencies=[Depends(admin_device)])
Conn = sqlite3.Connection
JobName = Literal["scan", "full_scan", "analyse", "nightly", "pretranscode", "discovery", "release_radar", "blends", "lyrics",
                  "cleanup", "backup"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
KEEP_BACKUPS = 5


def _model(conn: Conn, c: AppContext) -> str:
    return health.analysis_progress(conn, c.cfg.model_version)["model"]


# ---------- users ----------


@router.get("/users/{user_id}/devices")
def user_devices(user_id: str, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    if users.get(conn, user_id) is None:
        raise HTTPException(404, "no such user")
    return device_list(conn, c, user_id, None)


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


# ---------- jobs ----------


def _summary(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    parts = []
    for k, v in result.items():
        if k in ("error_paths",) or isinstance(v, (dict, list)):
            if isinstance(v, (dict, list)) and k not in ("error_paths",):
                parts.append(f"{k}={len(v)}")
            continue
        parts.append(f"{k}={v}")
    return ", ".join(parts)[:200] or None


def job_status(conn: Conn, c: AppContext, name: str, nxt: dict) -> dict:
    last = get_setting(conn, f"job.{name}")
    running = bool(c.jobs and c.jobs.is_running(name))
    state = "running" if running else ("failed" if last and last.get("ok") is False else "idle")
    last = last or {}
    return {"name": name, "state": state, "running": running, "startedAt": last.get("startedAt"),
            "finishedAt": None if running else last.get("finishedAt"), "ok": None if running else last.get("ok"),
            "durationS": last.get("durationS"), "error": last.get("error"), "result": last.get("result"),
            "nextRunAt": nxt.get(name), "next": nxt.get(name), "last": last or None,
            "history": get_setting(conn, f"jobhist.{name}", []) or []}


def all_jobs(conn: Conn, c: AppContext) -> list[dict]:
    nxt = c.jobs.next_runs(iso_format=True) if c.jobs else {}
    return [job_status(conn, c, n, nxt) for n in JOB_NAMES]


@router.get("/jobs")
def jobs(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return all_jobs(conn, c)


@router.post("/jobs/{name}/run")
def run_job(name: JobName, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    """Start a job now. 409 if it is already running. Returns its JobStatus (state running)."""
    if c.jobs is None:
        raise HTTPException(503, "background jobs are not running")
    if not c.jobs.trigger(name, "manual"):
        raise HTTPException(409, f"{name} is already running")
    for _ in range(50):  # wait (briefly) until the worker holds the job lock so the status says "running"
        if c.jobs.is_running(name):
            break
        time.sleep(0.01)
    st = job_status(conn, c, name, c.jobs.next_runs(iso_format=True))
    if not st["running"]:  # finished already (fast jobs)
        return st
    return {**st, "state": "running"}


@router.get("/jobs/history")
def job_history(limit: int = Query(50, ge=1, le=500), conn: Conn = Depends(db)):
    runs = []
    for n in JOB_NAMES:
        for rec in get_setting(conn, f"jobhist.{n}", []) or []:
            runs.append({"id": f"{n}:{rec.get('startedAt')}", "name": n, "startedAt": rec.get("startedAt"),
                         "finishedAt": rec.get("finishedAt"), "ok": rec.get("ok"), "durationS": rec.get("durationS"),
                         "error": rec.get("error"), "trigger": rec.get("trigger") or "manual",
                         "summary": _summary(rec.get("result"))})
    return sorted(runs, key=lambda r: r["startedAt"] or "", reverse=True)[:limit]


# ---------- status (dashboard) ----------


@router.get("/status")
def status(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    srv = health.server(c.cfg, c.server_id, c.version, c.started_at or time.time(), c.opus)
    mem = srv["memory"]
    stats = queries.stats(conn)
    for k in ("starred", "blocked"):
        stats.pop(k, None)
    running = bool(c.jobs and c.jobs.is_running("analyse"))
    a = health.analysis_progress(conn, c.cfg.model_version, running)
    disk_names = {"library": "Music library (read-only)", "data": "Data volume"}
    return {
        "version": c.version, "uptimeS": srv["uptimeS"], "cpuPct": srv["cpu"]["percent"],
        "load": [x for x in (srv["cpu"]["load1"], srv["cpu"]["load5"], srv["cpu"]["load15"]) if x is not None],
        "memUsedBytes": (mem["totalBytes"] - mem["availableBytes"]) if mem["totalBytes"] and mem["availableBytes"]
        is not None else None,
        "memTotalBytes": mem["totalBytes"],
        "disks": [{"name": disk_names.get(k, k), "path": d["path"], "usedBytes": d["totalBytes"] - d["freeBytes"],
                   "totalBytes": d["totalBytes"]} for k, d in sorted(srv["disks"].items(), key=lambda kv: kv[0] != "library")],
        "dataBytes": srv["dataDirBytes"],
        "library": {**stats, "lastScan": get_setting(conn, "job.scan")},
        "analysis": {**a, "done": a["analysed"], "modelVersion": a["model"]},
        "lyrics": {"lrclibEnabled": c.cfg.lrclib_enabled, **lrclib.stats(conn)},
        "jobs": all_jobs(conn, c),
    }


# ---------- failed files ----------


def failed_files(conn: Conn, c: AppContext) -> list[dict]:
    out = []
    for f in health.failed_analysis(conn, _model(conn, c)):
        out.append({"id": f["track"]["id"], "path": f["track"]["path"], "stage": "analyse",
                    "error": f["error"] or "analysis failed", "attempts": 1, "lastTriedAt": f["updatedAt"],
                    "trackId": f["track"]["id"]})
    scan = get_setting(conn, "job.scan") or {}
    for p in ((scan.get("result") or {}).get("error_paths") or []):
        out.append({"id": "scan:" + hashlib.sha1(p.encode()).hexdigest()[:16], "path": p, "stage": "scan",
                    "error": "unreadable audio file or tags", "attempts": 1, "lastTriedAt": scan.get("finishedAt"),
                    "trackId": None})
    return out


@router.get("/library/failed")
def library_failed(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return failed_files(conn, c)


def _retry(conn: Conn, c: AppContext, item_id: str | None) -> dict:
    items = failed_files(conn, c)
    if item_id is not None:
        items = [i for i in items if i["id"] == item_id]
        if not items:
            raise HTTPException(404, "not found")
    analyse = [i["trackId"] for i in items if i["stage"] == "analyse"]
    n = health.retry_failed(conn, _model(conn, c), analyse) if analyse else 0
    scans = sum(1 for i in items if i["stage"] == "scan")
    if c.jobs:
        if n:
            c.jobs.trigger("analyse", "manual")
        if scans:
            c.jobs.trigger("full_scan", "manual")  # unreadable files are retried by every scan; force a re-read
    return {"queued": n + scans}


@router.post("/library/failed/retry")
def retry_all(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return _retry(conn, c, None)


@router.post("/library/failed/{item_id}/retry")
def retry_one(item_id: str, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return _retry(conn, c, item_id)


# ---------- health checks ----------

CHECKS = {
    "missing": ("Missing files", "Tracks in the database whose file is gone from the library.",
                {"label": "Remove from database", "destructive": True}),
    "duplicates": ("Duplicates", "The same recording more than once (same MusicBrainz id, or same artist, title "
                   "and duration within 2 s).", None),
    "missing_tags": ("Missing tags", "No artist, album or title tag, or no year, genre or track number.", None),
    "missing_art": ("Missing cover art", "Albums without a cover image in the folder or embedded art.", None),
    "unanalysed": ("Never analysed", "Present tracks without audio analysis for the current model.",
                   {"label": "Queue for analysis", "destructive": False}),
    "orphan_cache": ("Orphaned caches", "Opus and art cache files for tracks/albums that no longer exist.",
                     {"label": "Clean", "destructive": True}),
    "old_backups": ("Old backups", f"Database backups beyond the newest {KEEP_BACKUPS}.",
                    {"label": "Delete old backups", "destructive": True}),
}


def _items(conn: Conn, c: AppContext, check: str) -> list[dict]:
    if check == "missing":
        return [{"id": t["id"], "title": t["title"], "detail": " — ".join(x for x in (t["artist"], t["album"]) if x),
                 "path": t["path"], "bytes": t["fileBytes"], "group": None} for t in health.missing_tracks(conn)]
    if check == "duplicates":
        out = []
        for g in health.duplicates(conn):
            first = g["tracks"][0]
            out.append({"id": f"{g['reason']}:{g['key']}", "title": f"{first['title']} — {first['artist']}",
                        "detail": f"{len(g['tracks'])} copies", "path": None, "bytes": None,
                        "group": " · ".join(t["path"] for t in g["tracks"]), "trackIds": [t["id"] for t in g["tracks"]],
                        "paths": [t["path"] for t in g["tracks"]]})
        return out
    if check == "missing_tags":
        return [{"id": x["track"]["id"], "title": x["track"]["title"], "detail": "no " + " / ".join(x["missing"]),
                 "path": x["track"]["path"], "bytes": None, "group": None, "missing": x["missing"]}
                for x in health.missing_tags(conn)]
    if check == "missing_art":
        return [{"id": a["id"], "title": a["title"], "detail": a["artist"], "path": None, "bytes": None, "group": None}
                for a in health.missing_art(conn, c.data_dir)]
    if check == "unanalysed":
        return [{"id": t["id"], "title": t["title"], "detail": t["artist"], "path": t["path"], "bytes": None,
                 "group": None} for t in health.unanalysed(conn, _model(conn, c))]
    if check == "orphan_cache":
        return [{"id": f"{kind}:{p.name}", "title": p.name, "detail": kind, "path": None, "bytes": size, "group": None}
                for kind, p, size in health.cache_orphans(conn, c.data_dir)]
    if check == "old_backups":
        return [{"id": b["name"], "title": b["name"], "detail": b["kind"], "path": None, "bytes": b["bytes"],
                 "group": None} for b in backups.list_all(c.cfg)[KEEP_BACKUPS:]]
    raise HTTPException(404, "unknown check")


@router.get("/health")
def health_checks(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    out = []
    for cid, (title, desc, fix) in CHECKS.items():
        if cid == "unanalysed":
            count, size = health.unanalysed_count(conn, _model(conn, c)), None
        else:
            items = _items(conn, c, cid)
            count = len(items)
            size = sum(i["bytes"] or 0 for i in items) if cid in ("orphan_cache", "old_backups") else None
        out.append({"id": cid, "title": title, "description": desc, "count": count, "bytes": size,
                    "fix": fix if count else None})
    return out


@router.get("/health/{check}")
def health_items(check: str, limit: int = Query(200, ge=1, le=5000), offset: int = Query(0, ge=0),
                 conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return _items(conn, c, check)[offset:offset + limit]


class FixBody(BaseModel):
    ids: list[str] | None = Field(default=None, max_length=100_000)


@router.post("/health/{check}/fix")
def health_fix(check: str, body: FixBody | None = None, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    """Fix a check (all items, or `ids`). Only checks with a `fix` in GET /admin/health can be fixed. The music
    files are never touched: 'missing' removes database rows (after an automatic backup)."""
    if check not in CHECKS:
        raise HTTPException(404, "unknown check")
    if CHECKS[check][2] is None:
        raise HTTPException(400, "this check cannot be fixed automatically (the library is read-only)")
    ids = (body or FixBody()).ids
    wanted = set(ids) if ids else None
    if check == "missing":
        if not conn.execute("SELECT 1 FROM tracks WHERE missing = 1 LIMIT 1").fetchone():
            return {"fixed": 0, "freedBytes": None}
        b = backups.create(c.cfg, conn, "prune")
        return {"fixed": health.remove_missing(conn, ids), "freedBytes": None, "backup": b["name"]}
    if check == "unanalysed":
        n = health.unanalysed_count(conn, _model(conn, c))
        if c.jobs and n:
            c.jobs.trigger("analyse", "manual")
        return {"fixed": n, "freedBytes": None}
    if check == "orphan_cache":
        orphans = [o for o in health.cache_orphans(conn, c.data_dir)
                   if wanted is None or f"{o[0]}:{o[1].name}" in wanted]
        rep = health.clean_cache(orphans)
        return {"fixed": rep["files"], "freedBytes": rep["bytes"]}
    # old_backups
    old = backups.list_all(c.cfg)[KEEP_BACKUPS:]
    fixed = freed = 0
    for b in old:
        if wanted is None or b["name"] in wanted:
            p = backups.path_for(c.cfg, b["name"])
            if p:
                p.unlink()
                fixed += 1
                freed += b["bytes"]
    return {"fixed": fixed, "freedBytes": freed}


# ---------- server ----------


@router.get("/server")
def server_health(c: AppContext = Depends(ctx)):
    return health.server(c.cfg, c.server_id, c.version, c.started_at or time.time(), c.opus)


@router.delete("/opus-cache")
def clear_opus_cache(c: AppContext = Depends(ctx)):
    return health.clear_opus(c.cfg)


# ---------- logs ----------


def _filter(records: list[dict], q: str | None) -> list[dict]:
    if not q:
        return records
    n = q.casefold()
    return [r for r in records if n in r["message"].casefold() or n in r["logger"].casefold()]


@router.get("/logs")
def logs(limit: int = Query(200, ge=1, le=2000), level: LogLevel = "INFO", after: int | None = None,
         q: str | None = Query(None, max_length=200)):
    return _filter(BUFFER.since(after, level), q)[-limit:]


@router.get("/logs/stream")
async def logs_stream(request: Request, after: int | None = None, level: LogLevel = "INFO",
                      q: str | None = Query(None, max_length=200), maxS: float = Query(3600, gt=0, le=86400)):
    async def gen():
        last = after
        started = keepalive = time.monotonic()
        yield ": nas-music log tail\n\n"
        while time.monotonic() - started < maxS:
            batch = BUFFER.since(last, level)
            if batch:
                last = batch[-1]["seq"]
            for rec in _filter(batch, q):
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

NOTES_KEY = "backup.notes"


def _with_notes(conn: Conn, items: list[dict]) -> list[dict]:
    notes = get_setting(conn, NOTES_KEY, {}) or {}
    return [{**b, "id": b["name"], "note": notes.get(b["name"])} for b in items]


@router.get("/backups")
def list_backups(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return _with_notes(conn, backups.list_all(c.cfg))


class BackupBody(BaseModel):
    note: str | None = Field(default=None, max_length=200)


@router.post("/backups")
def create_backup(body: BackupBody | None = None, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    b = backups.create(c.cfg, conn, "manual")
    note = (body or BackupBody()).note
    if note:
        notes = get_setting(conn, NOTES_KEY, {}) or {}
        notes[b["name"]] = note.strip()
        set_setting(conn, NOTES_KEY, notes)
    return _with_notes(conn, [b])[0]


def _download(c: AppContext, name: str):
    p = backups.path_for(c.cfg, name)
    if p is None:
        raise HTTPException(404, "no such backup")
    return FileResponse(p, media_type="application/vnd.sqlite3", filename=name, headers={"Cache-Control": "no-store"})


@router.get("/backups/{backup_id}/download")
def download_backup(backup_id: str, c: AppContext = Depends(ctx)):
    return _download(c, backup_id)


@router.get("/backups/{backup_id}")
def download_backup_legacy(backup_id: str, c: AppContext = Depends(ctx)):
    return _download(c, backup_id)


@router.delete("/backups/{backup_id}", status_code=204)
def delete_backup(backup_id: str, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    p = backups.path_for(c.cfg, backup_id)
    if p is None:
        raise HTTPException(404, "no such backup")
    p.unlink()
    notes = get_setting(conn, NOTES_KEY, {}) or {}
    if notes.pop(backup_id, None) is not None:
        set_setting(conn, NOTES_KEY, notes)
    return Response(status_code=204)


# ---------- now playing ----------


@router.get("/now-playing")
def now_playing(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    """One row per online device (plus an offline active player), across all users."""
    from nasmusic.connect import position_now

    rows = []
    if c.hub is None:
        return rows
    for uid in list(c.hub.users):
        u = users.get(conn, uid)
        if u is None:
            continue
        state = c.hub.snapshot(uid) or {}
        for d in c.hub.devices_for(uid):
            active = d["isActive"]
            track = queries.track(conn, uid, state["trackId"]) if active and state.get("trackId") else None
            rows.append({"userId": uid, "username": u.username, "deviceId": d["id"], "deviceName": d["name"],
                         "kind": d["kind"], "online": d["online"], "isActive": active,
                         "playing": bool(active and state.get("playing")),
                         "positionMs": position_now(state) if active else 0, "track": track})
    return sorted(rows, key=lambda r: (r["username"].casefold(), not r["isActive"], r["deviceName"].casefold()))


# ---------- server settings ----------


@router.get("/settings")
def get_server_settings(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return serversettings.view(c.base_cfg or c.cfg, serversettings.overrides(conn))


@router.put("/settings")
def put_server_settings(body: dict[str, Any], conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    """Partial update. Everything applies immediately, so restartRequired is always empty (kept for the UI)."""
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
    v = serversettings.view(c.base_cfg or c.cfg, ov)
    return {"values": {k: v[k] for k in ("lanUrl", "tunnelUrl", "timezone", "opusCacheMaxGb", "lrclib", "serverName")},
            "restartRequired": [], "overrides": v["overrides"], "fromConfig": v["fromConfig"]}


# ---------- bug reports ----------


@router.get("/reports")
def reports(conn: Conn = Depends(db)):
    return bugreports.list_all(conn)


@router.get("/reports/{rid}")
def report(rid: str, conn: Conn = Depends(db)):
    r = bugreports.get(conn, rid)
    if r is None:
        raise HTTPException(404, "no such report")
    return r


class ReportPatch(BaseModel):
    status: Literal["open", "closed"]


@router.patch("/reports/{rid}")
def patch_report(rid: str, body: ReportPatch, conn: Conn = Depends(db)):
    if not bugreports.set_status(conn, rid, body.status):
        raise HTTPException(404, "no such report")
    full = bugreports.get(conn, rid)
    return {k: v for k, v in full.items() if k not in ("logs", "crash", "context", "description")}


@router.delete("/reports/{rid}", status_code=204)
def delete_report(rid: str, conn: Conn = Depends(db)):
    if not bugreports.delete(conn, rid):
        raise HTTPException(404, "no such report")
    return Response(status_code=204)
