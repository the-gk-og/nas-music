"""Internet radio: stations (by stream URL or from the Radio Browser community directory), per-user
favourites, and a streaming proxy that strips ICY metadata and remembers the now-playing title.

Playing through the server means the same LAN / tunnel / cast paths as music work, and HTTP-only streams
play in the HTTPS web player. Live streams are never cached or transcoded."""
from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from homebeat.core import now_iso
from homebeat.db import family
from homebeat.spoken import net

log = logging.getLogger(__name__)
RADIO_BROWSER = "https://all.api.radio-browser.info"
EXPLICIT_TAGS = {"explicit", "adult", "erotic", "xxx", "nsfw", "18+"}
PLAYLIST_TYPES = ("audio/x-scpls", "audio/scpls", "audio/x-mpegurl", "audio/mpegurl", "application/pls+xml")
HLS_TYPES = ("application/vnd.apple.mpegurl", "application/x-mpegurl")
STREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
MAX_META = 255 * 16
_TITLE = re.compile(r"StreamTitle='(.*?)';", re.S)


class StationError(Exception):
    pass


# ---------- now playing (in memory, per process) ----------


@dataclass
class NowPlaying:
    title: str | None
    at: float


_now: dict[str, NowPlaying] = {}


def now_playing(sid: str) -> NowPlaying | None:
    return _now.get(sid)


def set_now_playing(sid: str, title: str | None) -> None:
    _now[sid] = NowPlaying(title, time.time())


def parse_icy_meta(block: bytes) -> str | None:
    """`StreamTitle='Artist - Title';StreamUrl='';` -> "Artist - Title" (None when empty)."""
    text = block.rstrip(b"\0").decode("utf-8", "replace")
    if "�" in text:
        text = block.rstrip(b"\0").decode("latin-1")
    m = _TITLE.search(text)
    if not m:
        return None
    return m.group(1).strip() or None


class IcyStripper:
    """Feed raw stream bytes; get audio bytes back, with metadata blocks removed (callback per title)."""

    def __init__(self, metaint: int, on_title):
        self.metaint = metaint
        self.on_title = on_title
        self.left = metaint      # audio bytes until the next metadata length byte
        self.meta_len = -1       # >= 0 while reading a metadata block
        self.meta = bytearray()

    def feed(self, data: bytes) -> bytes:
        if self.metaint <= 0:
            return data
        out = bytearray()
        i = 0
        n = len(data)
        while i < n:
            if self.meta_len < 0 and self.left > 0:
                take = min(self.left, n - i)
                out += data[i:i + take]
                i += take
                self.left -= take
            elif self.meta_len < 0:  # length byte
                self.meta_len = data[i] * 16
                i += 1
                self.meta.clear()
                if self.meta_len == 0:
                    self.meta_len = -1
                    self.left = self.metaint
            else:
                take = min(self.meta_len - len(self.meta), n - i)
                self.meta += data[i:i + take]
                i += take
                if len(self.meta) >= self.meta_len:
                    try:
                        self.on_title(parse_icy_meta(bytes(self.meta)))
                    except Exception:  # never break the stream over metadata
                        log.exception("icy metadata callback failed")
                    self.meta_len = -1
                    self.left = self.metaint
        return bytes(out)


# ---------- stations ----------


def is_explicit_tags(tags: str | None) -> bool:
    return any(t.strip().lower() in EXPLICIT_TAGS for t in (tags or "").split(","))


def station_api(r, fav=None) -> dict:
    return {"id": r["id"], "name": r["name"], "streamUrl": r["stream_url"], "homepage": r["homepage"],
            "country": r["country"], "tags": [t for t in (r["tags"] or "").split(",") if t],
            "codec": r["codec"], "bitrate": r["bitrate"], "explicit": bool(r["explicit"]),
            "hasImage": bool(r["favicon"]), "favourite": fav is not None,
            "lastPlayedAt": fav["last_played_at"] if fav is not None else None}


def favourites(conn: sqlite3.Connection, uid: str) -> list[dict]:
    fam = " AND s.explicit = 0" if family.policy(conn, uid).enabled else ""
    rows = conn.execute("SELECT s.*, f.added_at, f.last_played_at FROM web_station_favourites f "
                        "JOIN web_stations s ON s.id = f.station_id WHERE f.user_id = ?" + fam +
                        " ORDER BY COALESCE(f.last_played_at, f.added_at) DESC", (uid,)).fetchall()
    return [station_api(r, r) for r in rows]


def get(conn: sqlite3.Connection, uid: str, sid: str):
    """(row, favourite row or None) if the user may see the station, else None."""
    r = conn.execute("SELECT * FROM web_stations WHERE id = ?", (sid,)).fetchone()
    if r is None or (r["explicit"] and family.policy(conn, uid).enabled):
        return None
    fav = conn.execute("SELECT * FROM web_station_favourites WHERE user_id = ? AND station_id = ?",
                       (uid, sid)).fetchone()
    return r, fav


def resolve_playlist(text: str) -> str | None:
    """First http(s) stream in a .pls / .m3u body."""
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("file") and "=" in line:
            line = line.split("=", 1)[1].strip()
        if line.startswith(("http://", "https://")):
            return line
    return None


def probe(url: str, transport=None) -> dict:
    """Check a stream URL: follow .pls/.m3u playlists, refuse HLS, read icy-name/genre/br. Returns
    {streamUrl, name?, codec?, bitrate?, tags?, homepage?}."""
    url = net.check_url(url)
    with net.client(transport, timeout=httpx.Timeout(10.0, connect=8.0)) as c:
        for _ in range(3):
            with c.stream("GET", url, headers={"Icy-MetaData": "1"}) as r:
                if r.status_code != 200:
                    raise StationError(f"stream returned HTTP {r.status_code}")
                ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
                path = httpx.URL(url).path.lower()
                if ctype in HLS_TYPES or path.endswith(".m3u8"):
                    raise StationError("HLS (.m3u8) streams are not supported; use the station's Icecast/Shoutcast URL")
                if ctype in PLAYLIST_TYPES or path.endswith((".pls", ".m3u")):
                    body = b""
                    for chunk in r.iter_bytes():
                        body += chunk
                        if len(body) > 64 * 1024:
                            break
                    nxt = resolve_playlist(body.decode("utf-8", "replace"))
                    if not nxt:
                        raise StationError("playlist has no stream URL")
                    url = net.check_url(nxt)
                    continue
                if ctype and not ctype.startswith(("audio/", "application/ogg", "video/mp2t")) \
                        and ctype != "application/octet-stream":
                    raise StationError(f"not an audio stream ({ctype})")
                h = r.headers
                br = h.get("icy-br", "").split(",")[0].strip()
                return {"streamUrl": url, "name": h.get("icy-name"), "tags": h.get("icy-genre"),
                        "homepage": h.get("icy-url"), "codec": _codec(ctype),
                        "bitrate": int(br) if br.isdigit() else None}
    raise StationError("too many playlist redirects")


def _codec(ctype: str | None) -> str | None:
    return {"audio/mpeg": "MP3", "audio/aac": "AAC", "audio/aacp": "AAC+", "audio/ogg": "OGG",
            "application/ogg": "OGG", "audio/flac": "FLAC", "audio/opus": "OPUS"}.get((ctype or "").lower())


def add(conn: sqlite3.Connection, uid: str, stream_url: str, name: str | None = None, *, homepage=None,
        favicon=None, country=None, tags=None, codec=None, bitrate=None, explicit: bool | None = None,
        radiobrowser_uuid: str | None = None) -> str:
    """Create (or reuse) the station and make it a favourite of `uid`. Returns its id."""
    stream_url = net.check_url(stream_url)
    sid = net.short_id(stream_url)
    tags_s = ",".join(t.strip() for t in (tags or "").split(",") if t.strip())[:500] or None
    expl = int(bool(explicit) or is_explicit_tags(tags_s))
    conn.execute(
        "INSERT INTO web_stations (id, stream_url, name, homepage, favicon, country, tags, codec, bitrate, explicit, "
        "radiobrowser_uuid, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET name = COALESCE(excluded.name, web_stations.name), "
        "homepage = COALESCE(excluded.homepage, web_stations.homepage), "
        "favicon = COALESCE(excluded.favicon, web_stations.favicon), "
        "country = COALESCE(excluded.country, web_stations.country), tags = COALESCE(excluded.tags, web_stations.tags), "
        "codec = COALESCE(excluded.codec, web_stations.codec), bitrate = COALESCE(excluded.bitrate, web_stations.bitrate), "
        "explicit = MAX(excluded.explicit, web_stations.explicit), "
        "radiobrowser_uuid = COALESCE(excluded.radiobrowser_uuid, web_stations.radiobrowser_uuid)",
        (sid, stream_url, (name or "").strip()[:300] or httpx.URL(stream_url).host, homepage, favicon, country,
         tags_s, codec, bitrate, expl, radiobrowser_uuid, uid, now_iso()))
    conn.execute("INSERT OR IGNORE INTO web_station_favourites (user_id, station_id, added_at) VALUES (?, ?, ?)",
                 (uid, sid, now_iso()))
    return sid


def remove(conn: sqlite3.Connection, uid: str, sid: str) -> bool:
    n = conn.execute("DELETE FROM web_station_favourites WHERE user_id = ? AND station_id = ?", (uid, sid)).rowcount
    if n and not conn.execute("SELECT 1 FROM web_station_favourites WHERE station_id = ?", (sid,)).fetchone():
        conn.execute("DELETE FROM web_stations WHERE id = ?", (sid,))
    return bool(n)


def played(conn: sqlite3.Connection, uid: str, sid: str) -> None:
    conn.execute("UPDATE web_station_favourites SET last_played_at = ? WHERE user_id = ? AND station_id = ?",
                 (now_iso(), uid, sid))


# ---------- Radio Browser ----------


def search_directory(conn: sqlite3.Connection, uid: str, name: str | None = None, tag: str | None = None,
                     country: str | None = None, limit: int = 40, transport=None,
                     base: str = RADIO_BROWSER) -> list[dict]:
    params = {"limit": str(limit * 2), "hidebroken": "true", "order": "clickcount", "reverse": "true"}
    if name:
        params["name"] = name
    if tag:
        params["tag"] = tag
    if country:
        params["countrycode" if len(country) == 2 else "country"] = country
    with net.client(transport, timeout=httpx.Timeout(10.0, connect=5.0)) as c:
        r = c.get(f"{base.rstrip('/')}/json/stations/search", params=params)
        r.raise_for_status()
        data = r.json()
    fam = family.policy(conn, uid).enabled
    favs = {r[0] for r in conn.execute("SELECT station_id FROM web_station_favourites WHERE user_id = ?", (uid,))}
    out, seen = [], set()
    for s in data if isinstance(data, list) else []:
        url = s.get("url_resolved") or s.get("url")
        if not url or s.get("hls") in (1, "1", True) or url in seen:
            continue
        expl = is_explicit_tags(s.get("tags"))
        if fam and expl:
            continue
        seen.add(url)
        try:
            sid = net.short_id(net.check_url(url))
        except net.BadUrl:
            continue
        out.append({"id": sid, "name": (s.get("name") or "").strip(), "streamUrl": url,
                    "homepage": s.get("homepage") or None, "imageUrl": s.get("favicon") or None,
                    "country": s.get("countrycode") or s.get("country") or None,
                    "tags": [t for t in (s.get("tags") or "").split(",") if t][:8],
                    "codec": s.get("codec") or None, "bitrate": s.get("bitrate") or None, "explicit": expl,
                    "radiobrowserUuid": s.get("stationuuid"), "favourite": sid in favs})
        if len(out) >= limit:
            break
    return out


def count_click(uuid: str, transport=None, base: str = RADIO_BROWSER) -> None:
    """Radio Browser asks clients to report plays (it ranks stations by clicks). Best effort."""
    try:
        with net.client(transport, timeout=httpx.Timeout(5.0, connect=3.0)) as c:
            c.get(f"{base.rstrip('/')}/json/url/{uuid}")
    except httpx.HTTPError:
        pass


# ---------- proxy ----------


async def open_stream(url: str, transport=None) -> tuple[httpx.AsyncClient, httpx.Response]:
    """Connect upstream asking for ICY metadata. The caller must close both."""
    url = net.check_url(url)
    c = net.async_client(transport, timeout=STREAM_TIMEOUT)
    try:
        req = c.build_request("GET", url, headers={"Icy-MetaData": "1", "Accept": "*/*"})
        r = await c.send(req, stream=True)
    except BaseException:
        await c.aclose()
        raise
    if r.status_code != 200:
        await r.aclose()
        await c.aclose()
        raise StationError(f"station returned HTTP {r.status_code}")
    return c, r


async def relay(sid: str, c: httpx.AsyncClient, r: httpx.Response) -> AsyncIterator[bytes]:
    try:
        metaint = int(r.headers.get("icy-metaint") or 0)
    except ValueError:
        metaint = 0
    strip = IcyStripper(metaint, lambda t: set_now_playing(sid, t))
    try:
        async for chunk in r.aiter_bytes():
            out = strip.feed(chunk)
            if out:
                yield out
    except (httpx.HTTPError, asyncio.CancelledError):
        pass
    finally:
        await r.aclose()
        await c.aclose()


def media_type_of(r: httpx.Response) -> str:
    ctype = (r.headers.get("content-type") or "audio/mpeg").split(";")[0].strip()
    return ctype if ctype.startswith(("audio/", "application/ogg")) else "audio/mpeg"


async def fetch_now_playing(sid: str, url: str, transport=None, timeout_s: float = 6.0) -> str | None:
    """Read just enough of the stream to see one ICY metadata block (nobody is listening through us)."""
    async def go():
        c, r = await open_stream(url, transport)
        try:
            metaint = int(r.headers.get("icy-metaint") or 0)
            if metaint <= 0:
                return None
            got: list[str | None] = []
            strip = IcyStripper(metaint, got.append)
            read = 0
            async for chunk in r.aiter_bytes():
                strip.feed(chunk)
                read += len(chunk)
                if got or read > metaint + MAX_META + 1:
                    break
            if got:
                set_now_playing(sid, got[0])
                return got[0]
            return None
        finally:
            await r.aclose()
            await c.aclose()

    try:
        return await asyncio.wait_for(go(), timeout_s)
    except (TimeoutError, httpx.HTTPError, StationError, net.BadUrl, ValueError):
        return None
