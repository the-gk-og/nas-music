"""Podcasts, audiobooks and internet radio under /api/v1 (api/openapi.yaml, tag "spoken").

None of this is music: these routes never write `events`, `user_tracks` or anything the recommender,
Wrapped or scrobbling read. Media URLs live under /stream/ and /art/ so `?token=` works for <audio>/<img>.

  /podcasts ...            subscriptions, episodes, directory search, per-show speed / skip silence
  /audiobooks ...          the audiobooks_root library, chapters, bookmarks
  /stations ...            internet radio favourites, Radio Browser search, now playing (ICY)
  /spoken/progress         resume positions (episodes + books), newest write wins, synced across devices
  /spoken/continue         what the user is in the middle of
  /stream/episode/{id}     downloaded file, else proxied from the feed's enclosure URL (Range passed through)
  /stream/book/{id}/{part} an audiobook part (original file)
  /stream/station/{id}     the live stream through the server (ICY metadata stripped)
  /art/podcast|book|station/{id}
"""
from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from nasmusic.app.context import AppContext, ctx, db, device
from nasmusic.auth import Device
from nasmusic.config import Config
from nasmusic.core import iso, now_iso
from nasmusic.spoken import audiobooks, covers, net, podcasts, stations
from nasmusic.spoken import progress as prog

log = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(device)], tags=["spoken"])
Conn = sqlite3.Connection
PROXY_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)


def _404(what: str = "not found"):
    raise HTTPException(404, what)


# ---------- shared serving (also used by the signed cast routes in cast_api) ----------


async def proxy_url(url: str, range_header: str | None, transport=None, media_type: str | None = None):
    """Relay a remote file, passing Range through (seeking in an episode the server has not downloaded)."""
    try:
        net.check_url(url)
    except net.BadUrl as e:
        raise HTTPException(502, str(e)) from e
    c = net.async_client(transport, timeout=PROXY_TIMEOUT)
    headers = {"Accept": "*/*", "Accept-Encoding": "identity"}
    if range_header:
        headers["Range"] = range_header
    try:
        r = await c.send(c.build_request("GET", url, headers=headers), stream=True)
    except httpx.HTTPError as e:
        await c.aclose()
        raise HTTPException(502, f"upstream unreachable: {e}") from e
    if r.status_code not in (200, 206):
        await r.aclose()
        await c.aclose()
        raise HTTPException(416 if r.status_code == 416 else 502, f"upstream returned HTTP {r.status_code}")
    out_headers = {k: r.headers[k] for k in ("content-length", "content-range", "accept-ranges", "last-modified",
                                             "etag") if k in r.headers}
    if r.headers.get("content-encoding", "identity") != "identity":
        out_headers.pop("content-length", None)  # aiter_bytes() decodes; the length would be wrong
    out_headers.setdefault("accept-ranges", "bytes")
    out_headers["Cache-Control"] = "private, max-age=3600"
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip()
    if not ctype.startswith(("audio/", "video/")):
        ctype = media_type or "audio/mpeg"

    async def body():
        try:
            async for chunk in r.aiter_bytes():
                yield chunk
        except httpx.HTTPError:
            pass
        finally:
            await r.aclose()
            await c.aclose()

    return StreamingResponse(body(), status_code=r.status_code, media_type=ctype, headers=out_headers)


async def serve_episode(c: AppContext, row, range_header: str | None):
    if row["local_path"]:
        p = podcasts.podcast_dir(c.cfg) / row["local_path"]
        if p.is_file():
            ctype = row["enclosure_type"] if (row["enclosure_type"] or "").startswith(("audio/", "video/")) else None
            return FileResponse(p, media_type=ctype or audiobooks.media_type(p.name),
                                headers={"Cache-Control": "private, max-age=86400"})
    return await proxy_url(row["enclosure_url"], range_header, c.http_transport, row["enclosure_type"])


def serve_book_part(cfg: Config, conn: Conn, bid: str, part: int):
    if cfg.audiobooks_root is None:
        _404("audiobooks are not set up")
    r = conn.execute("SELECT f.path FROM audiobook_files f JOIN audiobooks b ON b.id = f.book_id "
                     "WHERE f.book_id = ? AND f.idx = ? AND b.missing = 0", (bid, part)).fetchone()
    if r is None:
        _404()
    p = cfg.audiobooks_root / r["path"]
    if not p.is_file():
        _404("file missing")
    return FileResponse(p, media_type=audiobooks.media_type(r["path"]),
                        headers={"Cache-Control": "private, max-age=86400"})


async def serve_station(c: AppContext, sid: str, url: str):
    try:
        client, r = await stations.open_stream(url, c.http_transport)
    except (stations.StationError, net.BadUrl) as e:
        raise HTTPException(502, str(e)) from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"station unreachable: {e}") from e
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if r.headers.get("icy-br"):
        headers["icy-br"] = r.headers["icy-br"]
    return StreamingResponse(stations.relay(sid, client, r), media_type=stations.media_type_of(r), headers=headers)


def cover_file(c: AppContext, conn: Conn, kind: str, ident: str, size: int):
    if kind == "podcast":
        r = conn.execute("SELECT image_url FROM podcasts WHERE id = ?", (ident,)).fetchone()
        if r is None:
            _404()
        src = covers.fetch_remote(c.data_dir, podcasts.image_key(ident), r["image_url"], c.http_transport)
        key = podcasts.image_key(ident)
    elif kind == "station":
        r = conn.execute("SELECT favicon FROM web_stations WHERE id = ?", (ident,)).fetchone()
        if r is None:
            _404()
        key = f"station_{ident}"
        src = covers.fetch_remote(c.data_dir, key, r["favicon"], c.http_transport)
    else:
        r = conn.execute("SELECT * FROM audiobooks WHERE id = ?", (ident,)).fetchone()
        if r is None:
            _404()
        key = audiobooks.image_key(ident)
        src = audiobooks.cover_source(c.cfg.audiobooks_root, c.data_dir, r)
    out = covers.resized(c.data_dir, key, src, size)
    if out is None:
        _404("no art")
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


# ---------- podcasts ----------


class SubscribeBody(BaseModel):
    feedUrl: str = Field(min_length=8, max_length=2000)


class ShowSettings(BaseModel):
    model_config = {"extra": "forbid"}
    speed: float | None = Field(default=None, ge=0.5, le=3.0)
    skipSilence: bool | None = None
    autoDownload: bool | None = None
    serverKeepLast: int | None = Field(default=None, ge=0, le=100)   # shared by every subscriber


def _show(conn: Conn, cfg: Config, uid: str, pid: str) -> dict:
    p = conn.execute("SELECT * FROM podcasts WHERE id = ?" + podcasts.visible(conn, uid).replace("p.", ""),
                     (pid,)).fetchone()
    if p is None:
        _404()
    sub = conn.execute("SELECT * FROM podcast_subscriptions WHERE user_id = ? AND podcast_id = ?",
                       (uid, pid)).fetchone()
    return podcasts.show_api(p, sub, cfg=cfg)


@router.get("/podcasts")
def list_podcasts(me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return {"podcasts": podcasts.subscriptions(conn, c.cfg, me.user_id)}


@router.post("/podcasts", status_code=201)
def subscribe(body: SubscribeBody, me: Device = Depends(device), conn: Conn = Depends(db),
              c: AppContext = Depends(ctx)):
    try:
        pid = podcasts.subscribe(conn, me.user_id, body.feedUrl, c.http_transport)
    except net.BadUrl as e:
        raise HTTPException(422, str(e)) from e
    except podcasts.PodcastError as e:
        raise HTTPException(422, str(e)) from e
    if c.jobs is not None:
        c.jobs.trigger("podcasts", "subscribe")  # download the newest episodes in the background
    if podcasts.visible(conn, me.user_id) and \
            conn.execute("SELECT explicit FROM podcasts WHERE id = ?", (pid,)).fetchone()[0]:
        podcasts.unsubscribe(conn, c.cfg, me.user_id, pid)
        raise HTTPException(403, "hidden by family mode")
    return _show(conn, c.cfg, me.user_id, pid)


@router.get("/podcasts/search")
def search_podcasts(q: str = Query(..., min_length=1, max_length=200), limit: int = Query(25, ge=1, le=50),
                    me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return podcasts.search(conn, c.cfg, me.user_id, q, limit, c.http_transport)


@router.get("/podcasts/episodes")
def new_episodes(filter: Literal["all", "unplayed", "in_progress", "downloaded"] = "unplayed",
                 offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500),
                 me: Device = Depends(device), conn: Conn = Depends(db)):
    """Latest episodes across every subscribed show."""
    return {"episodes": podcasts.episodes(conn, me.user_id, None, filter, offset, limit)}


@router.get("/podcasts/episodes/{episode_id}")
def get_episode(episode_id: str, me: Device = Depends(device), conn: Conn = Depends(db),
                c: AppContext = Depends(ctx)):
    r = podcasts.episode_row(conn, me.user_id, episode_id)
    if r is None:
        _404()
    out = podcasts.episode_api(r, prog.get(conn, me.user_id, "episode", episode_id), {"title": r["ptitle"]},
                               full=True)
    out["chapters"] = podcasts.chapters(conn, c.cfg, r, c.http_transport)
    out["hasChapters"] = bool(out["chapters"])
    sub = conn.execute("SELECT speed, skip_silence FROM podcast_subscriptions WHERE user_id = ? AND podcast_id = ?",
                       (me.user_id, r["podcast_id"])).fetchone()
    out["speed"] = sub["speed"] if sub else 1.0
    out["skipSilence"] = bool(sub["skip_silence"]) if sub else False
    return out


@router.get("/podcasts/{podcast_id}")
def get_podcast(podcast_id: str, me: Device = Depends(device), conn: Conn = Depends(db),
                c: AppContext = Depends(ctx)):
    return _show(conn, c.cfg, me.user_id, podcast_id)


@router.patch("/podcasts/{podcast_id}")
def patch_podcast(podcast_id: str, body: ShowSettings, me: Device = Depends(device), conn: Conn = Depends(db),
                  c: AppContext = Depends(ctx)):
    sub = conn.execute("SELECT 1 FROM podcast_subscriptions WHERE user_id = ? AND podcast_id = ?",
                       (me.user_id, podcast_id)).fetchone()
    if sub is None:
        _404("not subscribed")
    for col, v in (("speed", body.speed), ("skip_silence", body.skipSilence), ("auto_download", body.autoDownload)):
        if v is not None:
            conn.execute(f"UPDATE podcast_subscriptions SET {col} = ? WHERE user_id = ? AND podcast_id = ?",
                         (float(v) if col == "speed" else int(v), me.user_id, podcast_id))
    if body.serverKeepLast is not None:
        conn.execute("UPDATE podcasts SET keep_last = ? WHERE id = ?", (body.serverKeepLast, podcast_id))
        if c.jobs is not None:
            c.jobs.trigger("podcasts", "settings")
    return _show(conn, c.cfg, me.user_id, podcast_id)


@router.delete("/podcasts/{podcast_id}", status_code=204)
def unsubscribe(podcast_id: str, me: Device = Depends(device), conn: Conn = Depends(db),
                c: AppContext = Depends(ctx)):
    if not podcasts.unsubscribe(conn, c.cfg, me.user_id, podcast_id):
        _404("not subscribed")
    return Response(status_code=204)


@router.get("/podcasts/{podcast_id}/episodes")
def show_episodes(podcast_id: str, filter: Literal["all", "unplayed", "in_progress", "downloaded"] = "all",
                  offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000),
                  me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    _show(conn, c.cfg, me.user_id, podcast_id)  # 404 / family mode
    return {"episodes": podcasts.episodes(conn, me.user_id, podcast_id, filter, offset, limit)}


@router.post("/podcasts/{podcast_id}/refresh")
def refresh_podcast(podcast_id: str, me: Device = Depends(device), conn: Conn = Depends(db),
                    c: AppContext = Depends(ctx)):
    _show(conn, c.cfg, me.user_id, podcast_id)
    res = podcasts.poll_one(conn, podcast_id, c.http_transport, force=True)
    if res.get("new") and c.jobs is not None:
        c.jobs.trigger("podcasts", "refresh")
    return res


@router.post("/podcasts/{podcast_id}/played")
def mark_show_played(podcast_id: str, me: Device = Depends(device), conn: Conn = Depends(db),
                     c: AppContext = Depends(ctx)):
    _show(conn, c.cfg, me.user_id, podcast_id)
    return {"marked": podcasts.mark_all_played(conn, me.user_id, me.id, podcast_id)}


@router.get("/stream/episode/{episode_id}")
async def stream_episode(episode_id: str, request: Request, me: Device = Depends(device), conn: Conn = Depends(db),
                         c: AppContext = Depends(ctx)):
    r = podcasts.episode_row(conn, me.user_id, episode_id)
    if r is None:
        _404()
    return await serve_episode(c, r, request.headers.get("range"))


@router.get("/art/podcast/{podcast_id}")
def podcast_art(podcast_id: str, size: int = Query(512, ge=16, le=4096), conn: Conn = Depends(db),
                c: AppContext = Depends(ctx)):
    return cover_file(c, conn, "podcast", podcast_id, size)


# ---------- audiobooks ----------


@router.get("/audiobooks")
def list_books(sort: Literal["title", "author", "added", "recent"] = "title", me: Device = Depends(device),
               conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    order = {"title": "b.title COLLATE NOCASE", "author": "b.author COLLATE NOCASE, b.title COLLATE NOCASE",
             "added": "b.added_at DESC", "recent": "COALESCE(sp.updated_at, '') DESC, b.title COLLATE NOCASE"}[sort]
    rows = conn.execute(
        "SELECT b.*, sp.updated_at AS sp_at FROM audiobooks b LEFT JOIN spoken_progress sp ON sp.user_id = ? "
        f"AND sp.kind = 'book' AND sp.item_id = b.id WHERE b.missing = 0 ORDER BY {order}", (me.user_id,)).fetchall()
    pr = prog.many(conn, me.user_id, "book", [r["id"] for r in rows])
    return {"enabled": c.cfg.audiobooks_root is not None,
            "books": [audiobooks.book_api(r, pr.get(r["id"])) for r in rows]}


@router.get("/audiobooks/{book_id}")
def get_book(book_id: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    return audiobooks.detail(conn, me.user_id, book_id, prog.get(conn, me.user_id, "book", book_id)) or _404()


class BookmarkBody(BaseModel):
    positionS: float = Field(ge=0)
    note: str | None = Field(default=None, max_length=1000)


@router.post("/audiobooks/{book_id}/bookmarks", status_code=201)
def add_bookmark(book_id: str, body: BookmarkBody, me: Device = Depends(device), conn: Conn = Depends(db)):
    if conn.execute("SELECT 1 FROM audiobooks WHERE id = ? AND missing = 0", (book_id,)).fetchone() is None:
        _404()
    bid = uuid.uuid4().hex
    conn.execute("INSERT INTO audiobook_bookmarks (id, user_id, book_id, position_s, note, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (bid, me.user_id, book_id, body.positionS, body.note, now_iso()))
    return {"id": bid, "positionS": body.positionS, "note": body.note,
            "createdAt": conn.execute("SELECT created_at FROM audiobook_bookmarks WHERE id = ?", (bid,)).fetchone()[0]}


@router.delete("/audiobooks/{book_id}/bookmarks/{bookmark_id}", status_code=204)
def delete_bookmark(book_id: str, bookmark_id: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    n = conn.execute("DELETE FROM audiobook_bookmarks WHERE id = ? AND user_id = ? AND book_id = ?",
                     (bookmark_id, me.user_id, book_id)).rowcount
    if not n:
        _404()
    return Response(status_code=204)


@router.get("/stream/book/{book_id}/{part}")
def stream_book(book_id: str, part: int, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return serve_book_part(c.cfg, conn, book_id, part)


@router.get("/art/book/{book_id}")
def book_art(book_id: str, size: int = Query(512, ge=16, le=4096), conn: Conn = Depends(db),
             c: AppContext = Depends(ctx)):
    return cover_file(c, conn, "book", book_id, size)


# ---------- internet radio ----------


class StationBody(BaseModel):
    streamUrl: str = Field(min_length=8, max_length=2000)
    name: str | None = Field(default=None, max_length=300)
    homepage: str | None = Field(default=None, max_length=2000)
    imageUrl: str | None = Field(default=None, max_length=2000)
    country: str | None = Field(default=None, max_length=100)
    tags: list[str] | None = Field(default=None, max_length=30)
    codec: str | None = Field(default=None, max_length=30)
    bitrate: int | None = Field(default=None, ge=0, le=10000)
    explicit: bool | None = None
    radiobrowserUuid: str | None = Field(default=None, max_length=64)
    probe: bool = True   # connect once to check it is an audio stream (and follow .pls/.m3u)


@router.get("/stations")
def list_stations(me: Device = Depends(device), conn: Conn = Depends(db)):
    return {"stations": stations.favourites(conn, me.user_id)}


@router.get("/stations/search")
def search_stations(q: str | None = Query(None, max_length=200), tag: str | None = Query(None, max_length=100),
                    country: str | None = Query(None, max_length=100), limit: int = Query(40, ge=1, le=100),
                    me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    if not (q or tag or country):
        raise HTTPException(422, "give q, tag or country")
    try:
        return {"results": stations.search_directory(conn, me.user_id, q, tag, country, limit, c.http_transport,
                                                     c.cfg.radio_browser_url)}
    except (httpx.HTTPError, ValueError) as e:
        raise HTTPException(502, f"Radio Browser unavailable: {e}") from e


@router.post("/stations", status_code=201)
def add_station(body: StationBody, me: Device = Depends(device), conn: Conn = Depends(db),
                c: AppContext = Depends(ctx)):
    url, info = body.streamUrl, {}
    try:
        if body.probe:
            info = stations.probe(url, c.http_transport)
            url = info["streamUrl"]
        sid = stations.add(conn, me.user_id, url, body.name or info.get("name"), homepage=body.homepage or info.get("homepage"),
                           favicon=body.imageUrl, country=body.country,
                           tags=",".join(body.tags) if body.tags is not None else info.get("tags"),
                           codec=body.codec or info.get("codec"), bitrate=body.bitrate or info.get("bitrate"),
                           explicit=body.explicit, radiobrowser_uuid=body.radiobrowserUuid)
    except net.BadUrl as e:
        raise HTTPException(422, str(e)) from e
    except stations.StationError as e:
        raise HTTPException(422, str(e)) from e
    except httpx.HTTPError as e:
        raise HTTPException(422, f"could not connect to the stream: {e}") from e
    got = stations.get(conn, me.user_id, sid)
    if got is None:  # explicit station while family mode is on
        stations.remove(conn, me.user_id, sid)
        raise HTTPException(403, "hidden by family mode")
    return stations.station_api(*got)


@router.get("/stations/{station_id}")
def get_station(station_id: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    got = stations.get(conn, me.user_id, station_id)
    if got is None:
        _404()
    return stations.station_api(*got)


@router.delete("/stations/{station_id}", status_code=204)
def remove_station(station_id: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    if not stations.remove(conn, me.user_id, station_id):
        _404("not a favourite")
    return Response(status_code=204)


@router.get("/stations/{station_id}/now-playing")
async def station_now_playing(station_id: str, me: Device = Depends(device), conn: Conn = Depends(db),
                              c: AppContext = Depends(ctx)):
    got = stations.get(conn, me.user_id, station_id)
    if got is None:
        _404()
    np = stations.now_playing(station_id)
    if np is None or time.time() - np.at > 60:
        await stations.fetch_now_playing(station_id, got[0]["stream_url"], c.http_transport)
        np = stations.now_playing(station_id)
    return {"stationId": station_id, "title": np.title if np else None,
            "updatedAt": None if np is None else iso(datetime.fromtimestamp(np.at, UTC))}


@router.get("/stream/station/{station_id}")
async def stream_station(station_id: str, me: Device = Depends(device), conn: Conn = Depends(db),
                         c: AppContext = Depends(ctx)):
    got = stations.get(conn, me.user_id, station_id)
    if got is None:
        _404()
    stations.played(conn, me.user_id, station_id)
    if got[0]["radiobrowser_uuid"]:
        await run_in_threadpool(stations.count_click, got[0]["radiobrowser_uuid"], c.http_transport,
                                c.cfg.radio_browser_url)
    return await serve_station(c, station_id, got[0]["stream_url"])


@router.get("/art/station/{station_id}")
def station_art(station_id: str, size: int = Query(256, ge=16, le=4096), conn: Conn = Depends(db),
                c: AppContext = Depends(ctx)):
    return cover_file(c, conn, "station", station_id, size)


# ---------- progress ----------


class ProgressItem(BaseModel):
    kind: Literal["episode", "book"]
    id: str = Field(min_length=1, max_length=64)
    positionS: float | None = Field(default=None, ge=0)
    durationS: float | None = Field(default=None, ge=0)
    played: bool | None = None
    speed: float | None = Field(default=None, ge=0.5, le=3.0)
    updatedAt: str | None = Field(default=None, max_length=40)   # when the client recorded it (its clock)


class ProgressBatch(BaseModel):
    items: list[ProgressItem] = Field(min_length=1, max_length=500)


def _exists(conn: Conn, uid: str, kind: str, item_id: str) -> bool:
    if kind == "episode":
        return podcasts.episode_row(conn, uid, item_id) is not None
    return conn.execute("SELECT 1 FROM audiobooks WHERE id = ?", (item_id,)).fetchone() is not None


@router.get("/spoken/progress")
def get_progress(since: str | None = Query(None, max_length=40), me: Device = Depends(device),
                 conn: Conn = Depends(db)):
    return {"items": prog.since(conn, me.user_id, since)}


@router.put("/spoken/progress")
def put_progress(body: ProgressBatch, me: Device = Depends(device), conn: Conn = Depends(db)):
    out = []
    for it in body.items:
        if not _exists(conn, me.user_id, it.kind, it.id):
            out.append({"kind": it.kind, "id": it.id, "applied": False, "progress": None})
            continue
        applied, cur = prog.put(conn, me.user_id, me.id, it.kind, it.id, it.positionS, it.durationS, it.played,
                                it.speed, it.updatedAt)
        out.append({"kind": it.kind, "id": it.id, "applied": applied, "progress": cur})
    return {"items": out}


@router.get("/spoken/continue")
def continue_listening(limit: int = Query(20, ge=1, le=100), me: Device = Depends(device), conn: Conn = Depends(db)):
    """Episodes and books the user is in the middle of, most recent first."""
    rows = conn.execute("SELECT * FROM spoken_progress WHERE user_id = ? AND played = 0 AND position_s > 5 "
                        "ORDER BY updated_at DESC LIMIT ?", (me.user_id, limit * 2)).fetchall()
    out = []
    for r in rows:
        pr = prog.as_api(r)
        if r["kind"] == "episode":
            e = podcasts.episode_row(conn, me.user_id, r["item_id"])
            if e is not None:
                out.append({"kind": "episode", "episode": podcasts.episode_api(e, pr, {"title": e["ptitle"]}),
                            "progress": pr})
        else:
            b = conn.execute("SELECT * FROM audiobooks WHERE id = ? AND missing = 0", (r["item_id"],)).fetchone()
            if b is not None:
                out.append({"kind": "book", "book": audiobooks.book_api(b, pr), "progress": pr})
        if len(out) >= limit:
            break
    return {"items": out}
