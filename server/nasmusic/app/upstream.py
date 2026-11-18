"""Outbound HTTP for proxying suggestion previews and covers (the phone never contacts third parties)."""
from __future__ import annotations

import httpx

TIMEOUT = httpx.Timeout(10.0, connect=5.0)
HEADERS = {"User-Agent": "nas-music/1 (self-hosted music server)"}
MAX_COVER_BYTES = 10 * 1024 * 1024


class UpstreamError(Exception):
    pass


def client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, headers=HEADERS, follow_redirects=True, transport=transport)


def async_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS, follow_redirects=True, transport=transport)


def _check_url(url: str) -> None:
    if not url.startswith(("https://", "http://")):
        raise UpstreamError(f"unsupported URL scheme: {url[:40]}")


async def resolve_preview(c: httpx.AsyncClient, preview_url: str) -> str:
    """Return a playable audio URL. Deezer API track URLs (`https://api.deezer.com/track/<id>`) are
    resolved to their current short-lived `preview` MP3 link; anything else is taken as a direct link."""
    _check_url(preview_url)
    if httpx.URL(preview_url).host != "api.deezer.com":
        return preview_url
    try:
        r = await c.get(preview_url)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise UpstreamError(f"deezer lookup failed: {e}") from e
    link = data.get("preview") if isinstance(data, dict) else None
    if not link:
        err = data.get("error") if isinstance(data, dict) else None
        raise UpstreamError(f"deezer returned no preview ({err or 'empty'})")
    _check_url(link)
    return link


def fetch_bytes(c: httpx.Client, url: str, limit: int = MAX_COVER_BYTES) -> bytes:
    _check_url(url)
    try:
        with c.stream("GET", url) as r:
            r.raise_for_status()
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > limit:
                    raise UpstreamError("upstream file too large")
            return bytes(buf)
    except httpx.HTTPError as e:
        raise UpstreamError(f"fetch failed: {e}") from e
