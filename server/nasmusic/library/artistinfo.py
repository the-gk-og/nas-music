"""Artist pages: photo, bio, genres, years active and links, from free sources, cached in `artist_info` (v7).

Sources (all free, no keys):
- MusicBrainz (https://musicbrainz.org/doc/MusicBrainz_API): the artist (by its MBID from tags, else a strict
  name search), life-span, area, genres/tags and URL relations (Wikidata, Wikipedia, official site, ...).
  At most 1 request/second, with the project's generic User-Agent (shared limiter with the tag editor).
- Wikidata: the English Wikipedia article and the image (P18), when MusicBrainz links a Wikidata item.
- Wikipedia REST summary: the bio (first paragraph, CC BY-SA, credited with a link to the article).
- Wikimedia Commons: the photo. Only Commons files are used (they are all freely licensed); the licence,
  author and file page are stored and shown next to the photo. A 600 px thumbnail is cached under
  <data_dir>/artist-art/<artist_id>.jpg. No photo -> clients fall back to an album cover.

Refresh policy: found -> 90 days, not found -> 30 days, error -> 1 day. Lookups run in a background thread
(one at a time) so the page never waits on the network; the page reports `status: "pending"` meanwhile.
"""
from __future__ import annotations

import html
import json
import logging
import re
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx

from nasmusic.library import musicbrainz

log = logging.getLogger(__name__)

WIKIDATA = "https://www.wikidata.org/w/api.php"
WIKIPEDIA_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
COMMONS = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = musicbrainz.USER_AGENT
REFRESH_DAYS = {"ok": 90, "not_found": 30, "error": 1}
IMAGE_WIDTH = 600
MAX_IMAGE_BYTES = 5_000_000
ART_DIR = "artist-art"
LINK_TYPES = {  # MusicBrainz url-rel type -> our link kind
    "wikidata": "wikidata", "wikipedia": "wikipedia", "official homepage": "official",
    "discogs": "discogs", "bandcamp": "bandcamp", "allmusic": "allmusic", "last.fm": "lastfm",
    "songkick": "songkick", "soundcloud": "soundcloud", "youtube": "youtube",
}


class FetchError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _norm(s: str) -> str:
    return re.sub(r"[^\w]+", " ", (s or "").casefold()).strip()


def art_path(data_dir: Path, artist_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", artist_id)
    return Path(data_dir) / ART_DIR / f"{safe}.jpg"


# ---------- HTTP ----------


class Http:
    """Small JSON/bytes client. MusicBrainz calls go through the shared 1 req/s limiter."""

    def __init__(self, transport: httpx.BaseTransport | None = None, mb_interval_s: float = musicbrainz.MIN_INTERVAL_S):
        self.transport = transport
        self.mb_interval_s = mb_interval_s

    def mb(self, path: str, params: dict) -> dict:
        try:
            return musicbrainz._get(path, params, self.transport, self.mb_interval_s)
        except musicbrainz.LookupError_ as e:
            raise FetchError(str(e)) from e

    def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        try:
            with httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0), transport=self.transport,
                              follow_redirects=True, headers={"User-Agent": USER_AGENT}) as c:
                return c.get(url, params=params)
        except httpx.HTTPError as e:
            raise FetchError(f"{httpx.URL(url).host} unreachable: {e}") from e

    def json(self, url: str, params: dict | None = None, *, missing_ok: bool = False) -> dict | None:
        r = self._get(url, params)
        if missing_ok and r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise FetchError(f"{httpx.URL(url).host} error {r.status_code}")
        try:
            return r.json()
        except ValueError as e:
            raise FetchError(f"bad response from {httpx.URL(url).host}") from e

    def bytes(self, url: str) -> bytes:
        r = self._get(url)
        if r.status_code >= 400:
            raise FetchError(f"image download failed ({r.status_code})")
        if len(r.content) > MAX_IMAGE_BYTES:
            raise FetchError("image too large")
        return r.content


# ---------- lookups ----------


def resolve_mbid(http: Http, name: str, mbid: str | None) -> str | None:
    """The artist's MBID: from tags when known, else a strict name search (exact normalised name, score 100,
    and unambiguous)."""
    if mbid:
        return mbid
    if not name.strip():
        return None
    data = http.mb("artist", {"query": f'artist:"{name}"', "limit": 5})
    hits = [a for a in data.get("artists") or [] if int(a.get("score") or 0) >= 95
            and (_norm(a.get("name")) == _norm(name) or any(_norm(x.get("name")) == _norm(name)
                                                             for x in a.get("aliases") or []))]
    if not hits:
        return None
    if len(hits) > 1 and int(hits[0].get("score") or 0) == int(hits[1].get("score") or 0):
        return None  # two artists with this name: don't guess
    return hits[0].get("id")


def _year(s: str | None) -> int | None:
    m = re.match(r"^(\d{4})", s or "")
    return int(m.group(1)) if m else None


def _strip_html(s: str | None) -> str | None:
    if not s:
        return None
    t = html.unescape(re.sub(r"<[^>]+>", "", s)).strip()
    return re.sub(r"\s+", " ", t) or None


def parse_mb_artist(a: dict) -> dict:
    genres = [g["name"] for g in sorted(a.get("genres") or [], key=lambda g: -int(g.get("count") or 0))][:8]
    if not genres:
        genres = [t["name"] for t in sorted(a.get("tags") or [], key=lambda t: -int(t.get("count") or 0))
                  if int(t.get("count") or 0) > 0][:5]
    ls = a.get("life-span") or {}
    links: list[dict] = [{"kind": "musicbrainz", "url": f"https://musicbrainz.org/artist/{a['id']}"}]
    wikidata = wikipedia = None
    for rel in a.get("relations") or []:
        kind = LINK_TYPES.get((rel.get("type") or "").casefold())
        url = ((rel.get("url") or {}).get("resource") or "").strip()
        if not kind or not url.startswith(("https://", "http://")) or rel.get("ended"):
            continue
        if kind == "wikidata":
            wikidata = url.rstrip("/").rsplit("/", 1)[-1]
        if kind == "wikipedia":
            wikipedia = url
        if not any(link["kind"] == kind for link in links):
            links.append({"kind": kind, "url": url})
    area = (a.get("area") or {}).get("name")
    begin_area = (a.get("begin-area") or a.get("begin_area") or {}).get("name")
    return {
        "mbid": a.get("id"), "name": a.get("name"), "type": a.get("type"), "disambiguation": a.get("disambiguation") or None,
        "country": a.get("country"), "area": area, "beginArea": begin_area,
        "lifeSpan": {"begin": ls.get("begin"), "end": ls.get("end"), "ended": bool(ls.get("ended"))},
        "yearsActive": {"from": _year(ls.get("begin")), "to": _year(ls.get("end"))},
        "genres": genres, "links": links, "_wikidata": wikidata, "_wikipedia": wikipedia,
    }


def wikidata_entity(http: Http, qid: str) -> tuple[str | None, str | None]:
    """(English Wikipedia title, Commons image file name) from a Wikidata item."""
    if not re.fullmatch(r"Q\d+", qid or ""):
        return None, None
    data = http.json(WIKIDATA, {"action": "wbgetentities", "ids": qid, "props": "sitelinks|claims",
                                "sitefilter": "enwiki", "format": "json"}) or {}
    ent = (data.get("entities") or {}).get(qid) or {}
    title = ((ent.get("sitelinks") or {}).get("enwiki") or {}).get("title")
    image = None
    for claim in (ent.get("claims") or {}).get("P18") or []:
        v = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(v, str) and v:
            image = v
            break
    return title, image


def wikipedia_summary(http: Http, title: str) -> dict | None:
    data = http.json(WIKIPEDIA_SUMMARY + quote(title.replace(" ", "_"), safe=""), missing_ok=True)
    if not data or data.get("type") == "disambiguation" or not data.get("extract"):
        return None
    page = ((data.get("content_urls") or {}).get("desktop") or {}).get("page") \
        or f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
    return {"text": data["extract"], "source": "Wikipedia", "url": page,
            "license": "CC BY-SA 4.0", "licenseUrl": "https://creativecommons.org/licenses/by-sa/4.0/"}


def commons_image(http: Http, filename: str) -> dict | None:
    """Thumbnail URL + licence/attribution of a Commons file."""
    data = http.json(COMMONS, {"action": "query", "titles": f"File:{filename}", "prop": "imageinfo",
                               "iiprop": "url|extmetadata", "iiurlwidth": IMAGE_WIDTH, "format": "json"}) or {}
    for page in ((data.get("query") or {}).get("pages") or {}).values():
        info = (page.get("imageinfo") or [None])[0]
        if not info:
            continue
        meta = info.get("extmetadata") or {}

        def m(k: str) -> str | None:
            return _strip_html((meta.get(k) or {}).get("value"))

        thumb = info.get("thumburl") or info.get("url")
        if not thumb:
            continue
        return {"url": thumb, "filePage": info.get("descriptionurl"), "author": m("Artist"),
                "license": m("LicenseShortName") or m("License"), "licenseUrl": m("LicenseUrl"),
                "credit": m("Credit"), "source": "Wikimedia Commons"}
    return None


def fetch(http: Http, name: str, mbid: str | None) -> tuple[str, dict | None, bytes | None]:
    """('ok' | 'not_found', info, image bytes). Raises FetchError when a source is unreachable."""
    found = resolve_mbid(http, name, mbid)
    if not found:
        return "not_found", None, None
    a = http.mb(f"artist/{found}", {"inc": "url-rels+genres+tags+aliases"})
    info = parse_mb_artist(a)
    title, image_file = None, None
    if info.pop("_wikidata"):
        qid = next(link["url"] for link in info["links"] if link["kind"] == "wikidata").rstrip("/").rsplit("/", 1)[-1]
        title, image_file = wikidata_entity(http, qid)
    wp = info.pop("_wikipedia")
    if not title and wp and "en.wikipedia.org/wiki/" in wp:
        from urllib.parse import unquote

        title = unquote(wp.split("/wiki/", 1)[1]).replace("_", " ")
    if title:
        info["bio"] = wikipedia_summary(http, title)
        url = f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
        info["links"] = [link for link in info["links"] if link["kind"] != "wikipedia"] + [{"kind": "wikipedia", "url": url}]
    else:
        info["bio"] = None
    img_bytes = None
    info["image"] = None
    if image_file:
        img = commons_image(http, image_file)
        if img:
            try:
                img_bytes = http.bytes(img["url"])
                info["image"] = {k: v for k, v in img.items() if k != "url"}
            except FetchError as e:
                log.info("artist image download failed: %s", e)
    return "ok", info, img_bytes


# ---------- cache ----------


def cached(conn: sqlite3.Connection, artist_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM artist_info WHERE artist_id = ?", (artist_id,)).fetchone()


def is_due(row: sqlite3.Row | None, now: datetime | None = None) -> bool:
    return row is None or row["refresh_after"] <= _iso(now or _now())


def _save_image(data_dir: Path, artist_id: str, data: bytes) -> bool:
    from io import BytesIO

    from PIL import Image

    try:
        im = Image.open(BytesIO(data))
        im = im.convert("RGB")
        im.thumbnail((IMAGE_WIDTH, IMAGE_WIDTH * 2))
        p = art_path(data_dir, artist_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        im.save(tmp, "JPEG", quality=85)
        tmp.replace(p)
        return True
    except Exception as e:  # noqa: BLE001 - a bad image just means no photo
        log.info("artist image unusable: %s", e)
        return False


def update(conn: sqlite3.Connection, data_dir: Path, http: Http, artist_id: str) -> sqlite3.Row | None:
    """Look the artist up now and store the result (also on failure, with a short retry time)."""
    a = conn.execute("SELECT id, name, mbid FROM artists WHERE id = ?", (artist_id,)).fetchone()
    if a is None:
        return None
    mbid = a["mbid"] or (artist_id[3:] if artist_id.startswith("mb:") else None)
    now = _now()
    prev = cached(conn, artist_id)
    try:
        status, info, img = fetch(http, a["name"], mbid)
        error = None
    except FetchError as e:
        status, info, img, error = "error", None, None, str(e)
        log.info("artist info for %s failed: %s", a["name"], e)
    if status == "error" and prev is not None and prev["status"] == "ok":
        # keep the old data; just try again tomorrow
        conn.execute("UPDATE artist_info SET refresh_after = ?, error = ? WHERE artist_id = ?",
                     (_iso(now + timedelta(days=REFRESH_DAYS["error"])), error, artist_id))
        return cached(conn, artist_id)
    if info is not None:
        has_img = bool(img) and _save_image(data_dir, artist_id, img)
        if not has_img:
            info["image"] = None
            art_path(data_dir, artist_id).unlink(missing_ok=True)
    conn.execute(
        "INSERT INTO artist_info (artist_id, status, mbid, data, error, fetched_at, refresh_after) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(artist_id) DO UPDATE SET status = excluded.status, "
        "mbid = excluded.mbid, data = excluded.data, error = excluded.error, fetched_at = excluded.fetched_at, "
        "refresh_after = excluded.refresh_after",
        (artist_id, status, info.get("mbid") if info else None, json.dumps(info) if info else None, error,
         _iso(now), _iso(now + timedelta(days=REFRESH_DAYS[status]))))
    return cached(conn, artist_id)


def as_api(row: sqlite3.Row | None, pending: bool = False) -> dict:
    """ArtistInfo for the API."""
    if row is None:
        return {"status": "pending" if pending else "none"}
    data = json.loads(row["data"]) if row["data"] else {}
    out = {"status": "pending" if pending and row["status"] != "ok" else row["status"],
           "fetchedAt": row["fetched_at"], **data}
    out.pop("name", None)
    return out


# ---------- background fetching ----------


class Fetcher:
    """One background thread that works through requested artists (deduplicated), so page loads never wait."""

    def __init__(self, open_conn, data_dir: Path, transport: httpx.BaseTransport | None = None):
        self.open_conn = open_conn
        self.data_dir = Path(data_dir)
        self.http = Http(transport)
        self._queue: list[str] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def request(self, artist_id: str) -> bool:
        """Queue a lookup; True if it is (now) queued or running."""
        with self._lock:
            if artist_id not in self._queue:
                self._queue.append(artist_id)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="artist-info", daemon=True)
                self._thread.start()
        return True

    def pending(self, artist_id: str) -> bool:
        with self._lock:
            return artist_id in self._queue

    def _run(self) -> None:
        conn = self.open_conn()
        try:
            while True:
                with self._lock:
                    if not self._queue:
                        self._thread = None
                        return
                    aid = self._queue[0]
                try:
                    update(conn, self.data_dir, self.http, aid)
                except Exception:  # noqa: BLE001
                    log.exception("artist info lookup failed for %s", aid)
                finally:
                    with self._lock:
                        if self._queue and self._queue[0] == aid:
                            self._queue.pop(0)
        finally:
            conn.close()

    def wait(self, timeout: float = 10.0) -> None:
        """Tests: wait until the queue is empty."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                if not self._queue:
                    return
            time.sleep(0.02)


def refresh_due(conn: sqlite3.Connection, data_dir: Path, http: Http, limit: int = 30) -> dict:
    """Nightly: refresh up to `limit` due artists, most-played first (MusicBrainz' rate limit keeps this slow)."""
    rows = conn.execute(
        "SELECT ar.id FROM artists ar LEFT JOIN artist_info ai ON ai.artist_id = ar.id "
        "WHERE (ai.artist_id IS NULL OR ai.refresh_after <= ?) "
        "AND EXISTS (SELECT 1 FROM tracks t WHERE t.artist_id = ar.id AND t.missing = 0) "
        "ORDER BY (SELECT COALESCE(SUM(ut.play_count), 0) FROM user_tracks ut JOIN tracks t ON t.id = ut.track_id "
        "          WHERE t.artist_id = ar.id) DESC, ar.id LIMIT ?", (_iso(_now()), limit)).fetchall()
    ok = failed = 0
    for r in rows:
        row = update(conn, data_dir, http, r[0])
        if row is not None and row["status"] == "error":
            failed += 1
            if failed >= 3:
                break
        else:
            ok += 1
    return {"looked_up": ok, "failed": failed}
