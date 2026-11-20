"""Outbound HTTP for podcasts, covers and internet radio (the phone never contacts these hosts itself)."""
from __future__ import annotations

import hashlib
import ipaddress

import httpx

UA = "nas-music/1 (self-hosted; podcasts + radio)"
TIMEOUT = httpx.Timeout(20.0, connect=8.0)
MAX_FEED_BYTES = 30 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024


class BadUrl(ValueError):
    pass


def check_url(url: str) -> str:
    """Only http(s) to a real host: no loopback / link-local / unspecified / multicast literal addresses
    (the server proxies these URLs for users, so it must not become a way into itself)."""
    url = (url or "").strip()
    try:
        u = httpx.URL(url)
    except Exception as e:  # httpx.InvalidURL and friends
        raise BadUrl(f"invalid URL: {e}") from e
    if u.scheme not in ("http", "https") or not u.host:
        raise BadUrl("only http:// and https:// URLs are supported")
    host = u.host.strip("[]").lower()
    if host in ("localhost", "localhost.localdomain") or host.endswith(".localhost"):
        raise BadUrl("local addresses are not allowed")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return url
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        raise BadUrl("that address is not allowed")
    return url


def client(transport=None, timeout=TIMEOUT) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers={"User-Agent": UA}, follow_redirects=True, transport=transport)


def async_client(transport=None, timeout=TIMEOUT) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, headers={"User-Agent": UA}, follow_redirects=True, transport=transport)


def short_id(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(parts).encode()).hexdigest()[:16]


def get_limited(c: httpx.Client, url: str, limit: int, headers: dict | None = None) -> httpx.Response:
    """GET with a body size cap (the body is read into .content)."""
    check_url(url)
    with c.stream("GET", url, headers=headers or {}) as r:
        buf = bytearray()
        if r.status_code == 200:
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > limit:
                    raise BadUrl(f"response larger than {limit // (1024 * 1024)} MB")
        return httpx.Response(r.status_code, headers=r.headers, content=bytes(buf), request=r.request)
