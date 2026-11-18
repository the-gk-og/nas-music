"""Online catalogue for "Not in your library" (docs/v3-ux.md §4).

Sources (both free, no key): Deezer's public search API (albums/tracks/artists with 30 s previews and
covers) and MusicBrainz release groups (MBIDs and first-release years). Results are merged, cached in
memory for a few hours, rate-limited per host, and stored in `catalog_items` so later preview/cover
requests can be resolved from just the id. Ownership matching against the library is by MBID, else by
normalised artist + title.

No user data is sent anywhere: only the search text goes out, with a generic User-Agent.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus

import httpx

from nasmusic.core import now_iso

log = logging.getLogger(__name__)

DEEZER = "https://api.deezer.com"
MUSICBRAINZ = "https://musicbrainz.org/ws/2"
CAA = "https://coverartarchive.org"
USER_AGENT = "nas-music/0.3 (self-hosted music server; +https://musicbrainz.org/doc/MusicBrainz_API)"
CACHE_TTL_S = 6 * 3600
CACHE_MAX = 500
TIMEOUT = httpx.Timeout(6.0, connect=4.0)
PER_TYPE = 10


class CatalogError(Exception):
    pass


# ---------- normalisation + matching ----------

_BRACKETS = re.compile(r"[\(\[][^\)\]]*[\)\]]")
_SUFFIX = re.compile(r"\s+-\s+(\d{4}\s+)?(remaster|remastered|deluxe|live|mono|stereo|bonus|expanded|single|edit)"
                     r"\b.*$", re.IGNORECASE)
_FEAT = re.compile(r"\s+(feat\.?|ft\.?|featuring)\s+.*$", re.IGNORECASE)
_NONWORD = re.compile(r"[^\w]+")


def norm(s: str | None) -> str:
    """Lower-case, accent-free, without "(Remastered 2011)", "[Deluxe]", " - Live", "feat. X", a leading
    "the", or punctuation. "The Beatles" == "beatles"; "Help! (Remastered)" == "help"."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).casefold().replace("&", " and ")
    s = _BRACKETS.sub(" ", s)
    s = _SUFFIX.sub("", s)
    s = _FEAT.sub("", s)
    s = _NONWORD.sub(" ", s).strip()
    if s.startswith("the "):
        s = s[4:]
    return " ".join(s.split())


def match_key(artist: str | None, title: str | None) -> str:
    return f"{norm(artist)}|{norm(title)}"


def buy_links(artist: str | None, title: str | None) -> list[dict]:
    q = quote_plus(" ".join(x for x in (artist, title) if x))
    return [{"name": "Bandcamp", "url": f"https://bandcamp.com/search?q={q}"},
            {"name": "Qobuz", "url": f"https://www.qobuz.com/search?q={q}"}]


class LibraryIndex:
    """What the library owns, keyed for matching catalogue items (built per request; cheap)."""

    def __init__(self, conn: sqlite3.Connection):
        self.album_by_mbid: dict[str, str] = {}
        self.album_by_key: dict[str, str] = {}
        self.track_by_key: dict[str, str] = {}
        self.track_by_mbid: dict[str, str] = {}
        self.artist_by_name: dict[str, str] = {}
        for r in conn.execute(
            "SELECT al.id, al.title, al.mbid, ar.name FROM albums al JOIN artists ar ON ar.id = al.artist_id "
            "WHERE EXISTS (SELECT 1 FROM tracks t WHERE t.album_id = al.id AND t.missing = 0)"
        ):
            if r["mbid"]:
                self.album_by_mbid[r["mbid"].lower()] = r["id"]
            self.album_by_key.setdefault(match_key(r["name"], r["title"]), r["id"])
            self.artist_by_name.setdefault(norm(r["name"]), None)
        for r in conn.execute(
            "SELECT t.id, t.title, t.mbid, ar.name, t.album_artist FROM tracks t JOIN artists ar ON ar.id = t.artist_id "
            "WHERE t.missing = 0"
        ):
            self.track_by_key.setdefault(match_key(r["name"], r["title"]), r["id"])
            if r["album_artist"]:
                self.track_by_key.setdefault(match_key(r["album_artist"], r["title"]), r["id"])
            if r["mbid"]:
                self.track_by_mbid[r["mbid"].lower()] = r["id"]
        for r in conn.execute(
            "SELECT DISTINCT ar.id, ar.name FROM artists ar JOIN albums al ON al.artist_id = ar.id "
            "WHERE EXISTS (SELECT 1 FROM tracks t WHERE t.album_id = al.id AND t.missing = 0)"
        ):
            self.artist_by_name[norm(r["name"])] = r["id"]

    def owned(self, item: dict) -> str | None:
        t = item["type"]
        if t == "album":
            if item.get("mbid") and item["mbid"].lower() in self.album_by_mbid:
                return self.album_by_mbid[item["mbid"].lower()]
            return self.album_by_key.get(match_key(item.get("artist"), item["title"]))
        if t == "track":
            if item.get("mbid") and item["mbid"].lower() in self.track_by_mbid:
                return self.track_by_mbid[item["mbid"].lower()]
            return self.track_by_key.get(match_key(item.get("artist"), item["title"]))
        return self.artist_by_name.get(norm(item["title"])) or None


# ---------- rate limiting + cache ----------


class MinInterval:
    """At most one request per `interval` seconds to a host (MusicBrainz asks for 1/s)."""

    def __init__(self, interval: float):
        self.interval = interval
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self, max_wait: float) -> bool:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next - now)
            if delay > max_wait:
                return False
            self._next = max(now, self._next) + self.interval
        if delay:
            time.sleep(delay)
        return True


class Catalog:
    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.transport = transport
        self.deezer_limit = MinInterval(0.12)       # Deezer allows 50 requests / 5 s
        self.mb_limit = MinInterval(1.1)
        self._cache: dict[str, tuple[float, list[dict]]] = {}
        self._lock = threading.Lock()
        self.fetches = 0

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=TIMEOUT, transport=self.transport, follow_redirects=True,
                            headers={"User-Agent": USER_AGENT, "Accept": "application/json"})

    def _get(self, c: httpx.Client, limiter: MinInterval, url: str, params: dict) -> dict:
        if not limiter.wait(max_wait=3.0):
            raise CatalogError("rate limit: try again in a moment")
        self.fetches += 1
        r = c.get(url, params=params)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and data.get("error"):
            raise CatalogError(f"upstream error: {data['error']}")
        return data

    # ----- sources -----

    def _deezer(self, c: httpx.Client, kind: str, q: str) -> list[dict]:
        data = self._get(c, self.deezer_limit, f"{DEEZER}/search/{kind}", {"q": q, "limit": PER_TYPE})
        out = []
        for d in data.get("data", []) or []:
            try:
                if kind == "album":
                    out.append({"id": f"dz:album:{d['id']}", "type": "album", "title": d["title"],
                                "artist": (d.get("artist") or {}).get("name"), "album": None, "year": None,
                                "durationS": None, "mbid": None, "cover_url": d.get("cover_xl") or d.get("cover_big"),
                                "preview_ref": f"{DEEZER}/album/{d['id']}"})
                elif kind == "track":
                    al = d.get("album") or {}
                    out.append({"id": f"dz:track:{d['id']}", "type": "track", "title": d["title"],
                                "artist": (d.get("artist") or {}).get("name"), "album": al.get("title"),
                                "year": None, "durationS": d.get("duration"), "mbid": None,
                                "cover_url": al.get("cover_xl") or al.get("cover_big"),
                                "preview_ref": f"{DEEZER}/track/{d['id']}" if d.get("preview") else None})
                else:
                    out.append({"id": f"dz:artist:{d['id']}", "type": "artist", "title": d["name"], "artist": None,
                                "album": None, "year": None, "durationS": None, "mbid": None,
                                "cover_url": d.get("picture_xl") or d.get("picture_big"),
                                "preview_ref": f"{DEEZER}/artist/{d['id']}/top?limit=1"})
            except (KeyError, TypeError):
                continue
        return out

    def _musicbrainz(self, c: httpx.Client, q: str) -> list[dict]:
        data = self._get(c, self.mb_limit, f"{MUSICBRAINZ}/release-group", {"query": q, "fmt": "json",
                                                                             "limit": PER_TYPE})
        out = []
        for rg in data.get("release-groups", []) or []:
            credit = rg.get("artist-credit") or []
            artist = "".join((a.get("name") or "") + (a.get("joinphrase") or "") for a in credit) or None
            date = rg.get("first-release-date") or ""
            out.append({"mbid": rg["id"], "title": rg.get("title") or "", "artist": artist,
                        "year": int(date[:4]) if date[:4].isdigit() else None, "type": rg.get("primary-type")})
        return out

    # ----- search -----

    def search(self, q: str) -> tuple[list[dict], str | None]:
        """(items, error). Items are catalogue dicts with cover_url/preview_ref (not yet owned-checked)."""
        key = " ".join(q.casefold().split())
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < CACHE_TTL_S:
                return hit[1], None
        errors: list[str] = []
        with self._client() as c, ThreadPoolExecutor(4) as pool:
            futs = {k: pool.submit(self._deezer, c, k, q) for k in ("album", "track", "artist")}
            mb_fut = pool.submit(self._musicbrainz, c, q)
            results: dict[str, list[dict]] = {}
            for k, f in futs.items():
                try:
                    results[k] = f.result()
                except (httpx.HTTPError, CatalogError, ValueError) as e:
                    errors.append(f"deezer: {e}")
                    results[k] = []
            try:
                mb = mb_fut.result()
            except (httpx.HTTPError, CatalogError, ValueError) as e:
                errors.append(f"musicbrainz: {e}")
                mb = []
        by_key = {match_key(r["artist"], r["title"]): r for r in mb}
        albums = results["album"]
        seen = set()
        for a in albums:
            k = match_key(a["artist"], a["title"])
            seen.add(k)
            if k in by_key:
                a["mbid"], a["year"] = by_key[k]["mbid"], by_key[k]["year"]
        for k, r in by_key.items():  # MusicBrainz-only albums (no Deezer match): no sample, CAA cover
            if k not in seen and r.get("type") in (None, "Album", "EP", "Single"):
                albums.append({"id": f"mb:rg:{r['mbid']}", "type": "album", "title": r["title"], "artist": r["artist"],
                               "album": None, "year": r["year"], "durationS": None, "mbid": r["mbid"],
                               "cover_url": f"{CAA}/release-group/{r['mbid']}/front-500", "preview_ref": None})
        items = albums + results["track"] + results["artist"]
        if errors and not items:
            raise CatalogError("; ".join(errors))
        if not errors:
            with self._lock:
                if len(self._cache) >= CACHE_MAX:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[key] = (now, items)
        return items, ("; ".join(errors) if errors else None)

    def lookup(self, conn: sqlite3.Connection, catalog_id: str) -> dict | None:
        """A catalogue item by id: from the local cache table, else (Deezer ids) from the API."""
        row = conn.execute("SELECT * FROM catalog_items WHERE id = ?", (catalog_id,)).fetchone()
        if row:
            return {"id": row["id"], "type": row["type"], "title": row["title"], "artist": row["artist"],
                    "album": row["album"], "year": row["year"], "durationS": row["duration_s"], "mbid": row["mbid"],
                    "cover_url": row["cover_url"], "preview_ref": row["preview_ref"]}
        m = re.fullmatch(r"dz:(album|track|artist):(\d+)", catalog_id)
        if not m:
            return None
        kind, num = m.groups()
        try:
            with self._client() as c:
                d = self._get(c, self.deezer_limit, f"{DEEZER}/{kind}/{num}", {})
        except (httpx.HTTPError, CatalogError, ValueError):
            return None
        if kind == "artist":
            item = {"id": catalog_id, "type": "artist", "title": d.get("name") or "", "artist": None, "album": None,
                    "year": None, "durationS": None, "mbid": None, "cover_url": d.get("picture_xl"),
                    "preview_ref": f"{DEEZER}/artist/{num}/top?limit=1"}
        else:
            al = d if kind == "album" else (d.get("album") or {})
            date = d.get("release_date") or ""
            item = {"id": catalog_id, "type": kind, "title": d.get("title") or "",
                    "artist": (d.get("artist") or {}).get("name"), "album": al.get("title") if kind == "track" else None,
                    "year": int(date[:4]) if date[:4].isdigit() else None, "durationS": d.get("duration"),
                    "mbid": None, "cover_url": al.get("cover_xl"), "preview_ref": f"{DEEZER}/{kind}/{num}"}
        store(conn, [item])
        return item


def store(conn: sqlite3.Connection, items: list[dict]) -> None:
    now = now_iso()
    conn.executemany(
        "INSERT INTO catalog_items (id, type, title, artist, album, year, duration_s, mbid, cover_url, preview_ref, "
        "fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET title = excluded.title, "
        "artist = excluded.artist, album = excluded.album, year = COALESCE(excluded.year, catalog_items.year), "
        "duration_s = excluded.duration_s, mbid = COALESCE(excluded.mbid, catalog_items.mbid), "
        "cover_url = COALESCE(excluded.cover_url, catalog_items.cover_url), "
        "preview_ref = COALESCE(excluded.preview_ref, catalog_items.preview_ref), fetched_at = excluded.fetched_at",
        [(i["id"], i["type"], i["title"], i.get("artist"), i.get("album"), i.get("year"), i.get("durationS"),
          i.get("mbid"), i.get("cover_url"), i.get("preview_ref"), now) for i in items])


def api_item(i: dict, owned_id: str | None, toget: dict | None = None) -> dict:
    return {"id": i["id"], "type": i["type"], "title": i["title"], "artist": i.get("artist"), "album": i.get("album"),
            "year": i.get("year"), "durationS": i.get("durationS"), "mbid": i.get("mbid"),
            "artUrl": f"catalog/{i['id']}/cover" if i.get("cover_url") else None,
            "hasPreview": bool(i.get("preview_ref")), "owned": owned_id is not None, "ownedId": owned_id,
            "toGet": toget, "links": buy_links(i.get("artist") if i["type"] != "artist" else i["title"],
                                               None if i["type"] == "artist" else i["title"])}
