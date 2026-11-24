"""Outbound HTTP for podcasts, covers and internet radio (the phone never contacts these hosts itself)."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

import httpx

UA = "nas-music/1 (self-hosted; podcasts + radio)"
TIMEOUT = httpx.Timeout(20.0, connect=8.0)
MAX_FEED_BYTES = 30 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024


class BadUrl(ValueError):
    pass


# Home-network addresses (the NAS's own web UI, the router, other machines) are refused unless an admin vouched
# for this feed or station: wrap the fetch in `local_allowed(...)` (see nasmusic.spoken.trust).
_LOCAL_OK: ContextVar[bool] = ContextVar("nasmusic_local_ok", default=False)
_CGNAT = ipaddress.ip_network("100.64.0.0/10")  # Tailscale and carrier NAT
_LOCAL_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal", ".intranet", ".localdomain")


@contextmanager
def local_allowed(ok: bool = True) -> Iterator[None]:
    token = _LOCAL_OK.set(bool(ok))
    try:
        yield
    finally:
        _LOCAL_OK.reset(token)


def _unwrap(ip: ipaddress.IPv4Address | ipaddress.IPv6Address):
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def _bad_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    ip = _unwrap(ip)
    return ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved


def _home_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    ip = _unwrap(ip)
    return ip.is_private or (ip.version == 4 and ip in _CGNAT)


def _home_name(host: str) -> bool:
    return "." not in host or host.endswith(_LOCAL_SUFFIXES)


def check_url(url: str) -> str:
    """Only http(s) to a real host: never loopback / link-local / unspecified / multicast addresses (the server
    proxies these URLs for users, so it must not become a way into itself), and no home-network address or
    name (192.168.x, 10.x, nas.local, ...) unless `local_allowed()` is in effect."""
    url = (url or "").strip()
    try:
        u = httpx.URL(url)
    except Exception as e:  # httpx.InvalidURL and friends
        raise BadUrl(f"invalid URL: {e}") from e
    if u.scheme not in ("http", "https") or not u.host:
        raise BadUrl("only http:// and https:// URLs are supported")
    host = u.host.strip("[]").lower().rstrip(".")
    if host in ("localhost", "localhost.localdomain") or host.endswith(".localhost"):
        raise BadUrl("local addresses are not allowed")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if _home_name(host) and not _LOCAL_OK.get():
            raise BadUrl("home-network addresses are only allowed for feeds and stations an admin added") from None
        return url
    if _bad_ip(ip):
        raise BadUrl("that address is not allowed")
    if _home_ip(ip) and not _LOCAL_OK.get():
        raise BadUrl("home-network addresses are only allowed for feeds and stations an admin added")
    return url


def _check_resolved(infos) -> None:
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        except (ValueError, IndexError):
            continue
        if _bad_ip(ip):
            raise BadUrl("that host points at a local address")
        if _home_ip(ip) and not _LOCAL_OK.get():
            raise BadUrl("that host points at a home-network address")


def _blocked(request: httpx.Request, e: BadUrl) -> httpx.RequestError:
    return httpx.ConnectError(f"refused {request.url.host}: {e}", request=request)


def _check_request(request: httpx.Request, resolve: bool) -> None:
    """Every request a client makes, redirects included: the same rules as check_url, plus (on the real network)
    the host's DNS answers, so a name pointing at 127.0.0.1 or a redirect to http://localhost is refused."""
    try:
        check_url(str(request.url))
        if resolve:
            try:
                infos = socket.getaddrinfo(request.url.host, request.url.port or 443, type=socket.SOCK_STREAM)
            except (socket.gaierror, UnicodeError):
                return  # let the connection itself fail with its usual error
            _check_resolved(infos)
    except BadUrl as e:
        raise _blocked(request, e) from e


def client(transport=None, timeout=TIMEOUT) -> httpx.Client:
    resolve = transport is None
    return httpx.Client(timeout=timeout, headers={"User-Agent": UA}, follow_redirects=True, transport=transport,
                        event_hooks={"request": [lambda r: _check_request(r, resolve)]})


def async_client(transport=None, timeout=TIMEOUT) -> httpx.AsyncClient:
    resolve = transport is None

    async def hook(request: httpx.Request) -> None:
        try:
            check_url(str(request.url))
            if resolve:
                try:
                    infos = await asyncio.get_running_loop().getaddrinfo(
                        request.url.host, request.url.port or 443, type=socket.SOCK_STREAM)
                except (socket.gaierror, UnicodeError):
                    return
                _check_resolved(infos)
        except BadUrl as e:
            raise _blocked(request, e) from e

    return httpx.AsyncClient(timeout=timeout, headers={"User-Agent": UA}, follow_redirects=True,
                             transport=transport, event_hooks={"request": [hook]})


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
        # The body above is already decoded (gzip/deflate removed), so drop the encoding headers; otherwise
        # reading .content would decode it a second time ("incorrect header check").
        headers = [(k, v) for k, v in r.headers.multi_items()
                   if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
        return httpx.Response(r.status_code, headers=headers, content=bytes(buf), request=r.request)
