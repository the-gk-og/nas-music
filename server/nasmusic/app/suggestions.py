"""Album suggestions (written by the AI's weekly discovery): listing, answers, and proxied
preview audio / cover art so the phone never talks to third parties."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import sqlite3
from pathlib import Path

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from PIL import Image, UnidentifiedImageError

from nasmusic.app import upstream
from nasmusic.app.context import AppContext
from nasmusic.core import now_iso
from nasmusic.library import art

log = logging.getLogger(__name__)

ORDER = (
    "ORDER BY CASE status WHEN 'new' THEN 0 WHEN 'want' THEN 1 WHEN 'have' THEN 2 ELSE 3 END, "
    "score DESC NULLS LAST, created_at DESC"
)
PASS_HEADERS = ("content-length", "content-range", "accept-ranges")


def to_dict(r: sqlite3.Row) -> dict:
    try:
        links = json.loads(r["buy_links"]) if r["buy_links"] else []
    except ValueError:
        links = []
    return {
        "id": r["id"], "artist": r["artist"], "album": r["album"], "year": r["year"],
        "artistMbid": r["artist_mbid"], "releaseGroupMbid": r["release_group_mbid"],
        "hasCover": bool(r["cover_url"]), "hasPreview": bool(r["preview_url"]),
        "coverUrl": r["cover_url"], "previewUrl": r["preview_url"],
        "buyLinks": links, "reason": r["reason"], "status": r["status"], "createdAt": r["created_at"],
    }


def list_all(conn: sqlite3.Connection, uid: str) -> list[dict]:
    return [to_dict(r) for r in conn.execute(f"SELECT * FROM suggestions WHERE user_id = ? {ORDER}", (uid,))]


def answer(conn: sqlite3.Connection, uid: str, suggestion_id: str, status: str) -> bool:
    """Save the answer; "want" also copies the album into the user's To get list (v3)."""
    ok = conn.execute(
        "UPDATE suggestions SET status = ?, answered_at = ? WHERE id = ? AND user_id = ?",
        (status, now_iso(), suggestion_id, uid),
    ).rowcount == 1
    if ok and status == "want":
        from nasmusic.app import toget

        toget.from_suggestion(conn, uid, suggestion_id)
    return ok


def _row(conn: sqlite3.Connection, uid: str, suggestion_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, cover_url, preview_url FROM suggestions WHERE id = ? AND user_id = ?", (suggestion_id, uid)
    ).fetchone()


# ---------- preview ----------


async def stream_preview(c: AppContext, preview_url: str | None, range_header: str | None,
                         label: str) -> StreamingResponse:
    """Stream a 30 s sample through. A Deezer API link is resolved fresh on every request (its signed MP3
    URLs expire within minutes) and the audio is never written to disk."""
    if not preview_url:
        raise HTTPException(404, "no preview")
    client = upstream.async_client(c.http_transport)
    try:
        link = await upstream.resolve_preview(client, preview_url)
        headers = {"Accept-Encoding": "identity"}
        if range_header:
            headers["Range"] = range_header
        resp = await client.send(client.build_request("GET", link, headers=headers), stream=True)
    except (upstream.UpstreamError, httpx.HTTPError) as e:
        await client.aclose()
        log.warning("preview for %s unavailable: %s", label, e)
        raise HTTPException(502, "upstream preview unavailable") from e
    if resp.status_code not in (200, 206):
        await resp.aclose()
        await client.aclose()
        log.warning("preview for %s: upstream HTTP %d", label, resp.status_code)
        raise HTTPException(502, "upstream preview unavailable")

    async def body():
        try:
            async for chunk in resp.aiter_bytes():  # identity encoding requested
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    out = {k: resp.headers[k] for k in PASS_HEADERS if k in resp.headers}
    out["Cache-Control"] = "no-store"
    return StreamingResponse(body(), status_code=resp.status_code, media_type="audio/mpeg", headers=out)


async def preview(c: AppContext, conn: sqlite3.Connection, uid: str, suggestion_id: str,
                  range_header: str | None) -> StreamingResponse:
    row = _row(conn, uid, suggestion_id)
    if row is None or not row["preview_url"]:
        raise HTTPException(404, "no preview")
    return await stream_preview(c, row["preview_url"], range_header, f"suggestion {suggestion_id}")


# ---------- cover ----------


def cover_dir(data_dir: Path) -> Path:
    return art.art_dir(data_dir) / "suggestions"


def cached_cover(c: AppContext, cache_dir: Path, name: str, url: str | None, size: int) -> Path:
    """Fetch `url` once per (url, size bucket), resize to JPEG and cache it under cache_dir (blocking)."""
    if not url:
        raise HTTPException(404, "no cover")
    b = art.bucket(size)
    key = hashlib.sha1(url.encode()).hexdigest()[:16]  # new URL -> new cache entry
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)[:120]
    out = cache_dir / f"{safe}_{key}_{b}.jpg"
    if out.exists():
        return out
    try:
        with upstream.client(c.http_transport) as client:
            data = upstream.fetch_bytes(client, url)
        with Image.open(io.BytesIO(data)) as img:
            jpeg = art._to_jpeg(img, b)
    except (upstream.UpstreamError, UnidentifiedImageError, OSError, ValueError) as e:
        log.warning("cover %s unavailable: %s", name, e)
        raise HTTPException(502, "upstream cover unavailable") from e
    art._atomic_write(out, jpeg)
    return out


def cover(c: AppContext, conn: sqlite3.Connection, uid: str, suggestion_id: str, size: int) -> Path:
    """Return a cached, resized JPEG of the suggestion's cover, fetching it if needed (blocking)."""
    row = _row(conn, uid, suggestion_id)
    if row is None or not row["cover_url"]:
        raise HTTPException(404, "no cover")
    return cached_cover(c, cover_dir(c.data_dir), suggestion_id, row["cover_url"], size)
