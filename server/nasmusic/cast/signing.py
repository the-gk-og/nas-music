"""Short-lived signed media URLs for speakers.

Speakers (Chromecast, DLNA renderers, Home Assistant players) fetch audio themselves and can't send our bearer
token, and a session token must never appear in a URL. Instead each URL carries an HMAC over
(kind, id, format, expiry): it unlocks exactly one track (or one album's art), in one format, until it
expires.

    /api/v1/cast/media/<trackId>?f=<fmt>&exp=<unix>&sig=<b64url>
    /api/v1/cast/art/<albumId>?exp=<unix>&sig=<b64url>

The key is derived from the server's persistent `web_secret`, so URLs survive restarts until they expire.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from urllib.parse import quote, urlencode

SIG_BYTES = 16  # 128-bit tag
MAX_TTL_S = 24 * 3600  # never sign for longer than a day


def derive_key(web_secret: bytes) -> bytes:
    return hmac.new(web_secret, b"nasmusic cast url v1", hashlib.sha256).digest()


def _msg(kind: str, ident: str, fmt: str, exp: int) -> bytes:
    return f"{kind}\n{ident}\n{fmt}\n{exp}".encode()


def sign(key: bytes, kind: str, ident: str, fmt: str, exp: int) -> str:
    tag = hmac.new(key, _msg(kind, ident, fmt, exp), hashlib.sha256).digest()[:SIG_BYTES]
    return base64.urlsafe_b64encode(tag).rstrip(b"=").decode()


def verify(
    key: bytes,
    kind: str,
    ident: str,
    fmt: str,
    exp: int | str | None,
    sig: str | None,
    now: float | None = None,
) -> bool:
    try:
        exp_i = int(exp)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if not sig or exp_i < (now if now is not None else time.time()):
        return False
    return hmac.compare_digest(sign(key, kind, ident, fmt, exp_i), sig)


def expiry(ttl_s: float, now: float | None = None) -> int:
    return int((now if now is not None else time.time()) + max(1.0, min(float(ttl_s), MAX_TTL_S)))


def media_url(base: str, key: bytes, track_id: str, fmt: str, ttl_s: float, now: float | None = None) -> str:
    exp = expiry(ttl_s, now)
    q = urlencode({"f": fmt, "exp": exp, "sig": sign(key, "media", track_id, fmt, exp)})
    return f"{base.rstrip('/')}/api/v1/cast/media/{quote(track_id, safe='')}?{q}"


def art_url(base: str, key: bytes, album_id: str, ttl_s: float, now: float | None = None) -> str:
    exp = expiry(ttl_s, now)
    q = urlencode({"exp": exp, "sig": sign(key, "art", album_id, "", exp)})
    return f"{base.rstrip('/')}/api/v1/cast/art/{quote(album_id, safe='')}?{q}"
