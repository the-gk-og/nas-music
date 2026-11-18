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
    return conn.execute(
        "UPDATE suggestions SET status = ?, answered_at = ? WHERE id = ? AND user_id = ?",
        (status, now_iso(), suggestion_id, uid),
    ).rowcount == 1


def _row(conn: sqlite3.Connection, uid: str, suggestion_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, cover_url, preview_url FROM suggestions WHERE id = ? AND user_id = ?", (suggestion_id, uid)
    ).fetchone()


# ---------- preview ----------


async def preview(c: AppContext, conn: sqlite3.Connection, uid: str, suggestion_id: str,
                  range_header: str | None) -> StreamingResponse:
    """Stream the 30 s preview through. The Deezer link is resolved fresh on every request (its signed
    MP3 URLs expire within minutes) and the audio is never written to disk."""
    row = _row(conn, uid, suggestion_id)
    if row is None or not row["preview_url"]:
        raise HTTPException(404, "no preview")
    client = upstream.async_client(c.http_transport)
    try:
        link = await upstream.resolve_preview(client, row["preview_url"])
        headers = {"Accept-Encoding": "identity"}
        if range_header:
            headers["Range"] = range_header
        resp = await client.send(client.build_request("GET", link, headers=headers), stream=True)
    except (upstream.UpstreamError, httpx.HTTPError) as e:
        await client.aclose()
        log.warning("preview for suggestion %s unavailable: %s", suggestion_id, e)
        raise HTTPException(502, "upstream preview unavailable") from e
    if resp.status_code not in (200, 206):
        await resp.aclose()
        await client.aclose()
        log.warning("preview for suggestion %s: upstream HTTP %d", suggestion_id, resp.status_code)
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


# ---------- cover ----------


def cover_dir(data_dir: Path) -> Path:
    return art.art_dir(data_dir) / "suggestions"


def cover(c: AppContext, conn: sqlite3.Connection, uid: str, suggestion_id: str, size: int) -> Path:
    """Return a cached, resized JPEG of the suggestion's cover, fetching it if needed (blocking)."""
    row = _row(conn, uid, suggestion_id)
    if row is None or not row["cover_url"]:
        raise HTTPException(404, "no cover")
    b = art.bucket(size)
    key = hashlib.sha1(row["cover_url"].encode()).hexdigest()[:16]  # new URL -> new cache entry
    out = cover_dir(c.data_dir) / f"{suggestion_id}_{key}_{b}.jpg"
    if out.exists():
        return out
    try:
        with upstream.client(c.http_transport) as client:
            data = upstream.fetch_bytes(client, row["cover_url"])
        with Image.open(io.BytesIO(data)) as img:
            jpeg = art._to_jpeg(img, b)
    except (upstream.UpstreamError, UnidentifiedImageError, OSError, ValueError) as e:
        log.warning("cover for suggestion %s unavailable: %s", suggestion_id, e)
        raise HTTPException(502, "upstream cover unavailable") from e
    art._atomic_write(out, jpeg)
    return out
