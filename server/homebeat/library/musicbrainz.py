"""MusicBrainz lookups that suggest tags for the admin tag editor.

Free web service (https://musicbrainz.org/doc/MusicBrainz_API): at most 1 request per second per client and
a meaningful User-Agent. We send the project's generic User-Agent (no personal contact address) and space
requests by >= 1.1 s process-wide. Nothing is cached in the database; suggestions are only shown, the admin
decides what to apply."""
from __future__ import annotations

import threading
import time

import httpx

BASE = "https://musicbrainz.org/ws/2"
USER_AGENT = "Homebeat/1 (open-source self-hosted music server)"
MIN_INTERVAL_S = 1.1

_lock = threading.Lock()
_last = 0.0


class LookupError_(Exception):
    pass


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _get(path: str, params: dict, transport: httpx.BaseTransport | None, min_interval_s: float) -> dict:
    global _last
    with _lock:
        wait = _last + min_interval_s - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            with httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0), transport=transport,
                              headers={"User-Agent": USER_AGENT, "Accept": "application/json"}) as c:
                r = c.get(f"{BASE}/{path}", params={**params, "fmt": "json"})
        except httpx.HTTPError as e:
            raise LookupError_(f"MusicBrainz unreachable: {e}") from e
        finally:
            _last = time.monotonic()
    if r.status_code == 503:
        raise LookupError_("MusicBrainz is rate limiting; try again in a moment")
    if r.status_code >= 400:
        raise LookupError_(f"MusicBrainz error {r.status_code}")
    try:
        return r.json()
    except ValueError as e:
        raise LookupError_("bad response from MusicBrainz") from e


def _credit(ac: list | None) -> str | None:
    if not ac:
        return None
    return "".join(f"{c.get('name') or (c.get('artist') or {}).get('name', '')}{c.get('joinphrase', '')}"
                   for c in ac) or None


def _year(date: str | None) -> int | None:
    try:
        return int((date or "")[:4]) if date and len(date) >= 4 else None
    except ValueError:
        return None


def search_recordings(title: str, artist: str | None, album: str | None = None, *,
                      transport: httpx.BaseTransport | None = None, min_interval_s: float = MIN_INTERVAL_S,
                      limit: int = 10) -> list[dict]:
    """Candidate tags for one track: one entry per (recording, release)."""
    parts = [f"recording:{_q(title)}"]
    if artist:
        parts.append(f"artist:{_q(artist)}")
    if album:
        parts.append(f"release:{_q(album)}")
    data = _get("recording", {"query": " AND ".join(parts), "limit": limit}, transport, min_interval_s)
    out: list[dict] = []
    for rec in data.get("recordings") or []:
        rec_artist = _credit(rec.get("artist-credit"))
        for rel in (rec.get("releases") or [])[:3]:
            media = (rel.get("media") or [{}])[0]
            trk = (media.get("track") or [{}])[0]
            num = trk.get("number")
            out.append({
                "score": rec.get("score"), "recordingMbid": rec.get("id"), "releaseMbid": rel.get("id"),
                "title": rec.get("title"), "artist": rec_artist,
                "album": rel.get("title"), "albumArtist": _credit(rel.get("artist-credit")) or rec_artist,
                "year": _year(rel.get("date")),
                "trackNo": int(num) if isinstance(num, str) and num.isdigit() else None,
                "discNo": media.get("position"),
                "genre": None,
            })
        if not rec.get("releases"):
            out.append({"score": rec.get("score"), "recordingMbid": rec.get("id"), "releaseMbid": None,
                        "title": rec.get("title"), "artist": rec_artist, "album": None, "albumArtist": None,
                        "year": _year(rec.get("first-release-date")), "trackNo": None, "discNo": None,
                        "genre": None})
    return out[:limit * 2]


def search_releases(album: str, artist: str | None, *, transport: httpx.BaseTransport | None = None,
                    min_interval_s: float = MIN_INTERVAL_S, limit: int = 8) -> list[dict]:
    parts = [f"release:{_q(album)}"]
    if artist:
        parts.append(f"artist:{_q(artist)}")
    data = _get("release", {"query": " AND ".join(parts), "limit": limit}, transport, min_interval_s)
    return [{"score": r.get("score"), "releaseMbid": r.get("id"), "album": r.get("title"),
             "albumArtist": _credit(r.get("artist-credit")), "year": _year(r.get("date")),
             "country": r.get("country"), "trackCount": r.get("track-count"),
             "format": ", ".join(sorted({m.get("format") for m in r.get("media") or [] if m.get("format")})) or None}
            for r in data.get("releases") or []]


def release(mbid: str, *, transport: httpx.BaseTransport | None = None,
            min_interval_s: float = MIN_INTERVAL_S) -> dict:
    """A release with its track list (for applying album-wide suggestions)."""
    r = _get(f"release/{mbid}", {"inc": "recordings+artist-credits+genres+release-groups"}, transport,
             min_interval_s)
    genres = sorted(r.get("genres") or (r.get("release-group") or {}).get("genres") or [],
                    key=lambda g: -(g.get("count") or 0))
    tracks = []
    for m in r.get("media") or []:
        for t in m.get("tracks") or []:
            num = t.get("number")
            tracks.append({"discNo": m.get("position"),
                           "trackNo": int(num) if isinstance(num, str) and num.isdigit() else t.get("position"),
                           "title": t.get("title") or (t.get("recording") or {}).get("title"),
                           "artist": _credit(t.get("artist-credit")) or _credit(r.get("artist-credit")),
                           "recordingMbid": (t.get("recording") or {}).get("id")})
    return {"releaseMbid": r.get("id"), "album": r.get("title"), "albumArtist": _credit(r.get("artist-credit")),
            "year": _year(r.get("date")), "genre": genres[0]["name"].title() if genres else None,
            "discCount": len(r.get("media") or []), "tracks": tracks}
