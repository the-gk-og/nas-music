"""AcoustID web service client (https://acoustid.org/webservice): fingerprint -> MusicBrainz recordings.

Free for non-commercial use with an application API key (register at https://acoustid.org/new-application,
then put it in config.toml as `acoustid_api_key`). The service allows 3 requests per second; we stay well
under that. We send the project's generic User-Agent (no personal contact address)."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

import httpx

from nasmusic.library.musicbrainz import USER_AGENT

URL = "https://api.acoustid.org/v2/lookup"
MIN_INTERVAL_S = 0.4          # <= 2.5 requests/s (the limit is 3/s)
META = "recordings releasegroups releases tracks"
MIN_RESULT_SCORE = 0.4
MAX_RESULTS = 5
MAX_RECORDINGS = 10
MAX_RELEASES = 40


class RateLimiter:
    """Spaces calls at least `interval_s` apart (process-wide per instance). Clock/sleep injectable for tests."""

    def __init__(self, interval_s: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.interval_s = interval_s
        self.clock, self.sleep = clock, sleep
        self._lock = threading.Lock()
        self._last: float | None = None

    def __enter__(self):
        self._lock.acquire()
        if self._last is not None:
            wait = self._last + self.interval_s - self.clock()
            if wait > 0:
                self.sleep(wait)
        return self

    def __exit__(self, *exc):
        self._last = self.clock()
        self._lock.release()
        return False


LIMITER = RateLimiter(MIN_INTERVAL_S)


class AcoustIDError(Exception):
    """A failed lookup. `fatal` = retrying won't help this run (bad API key); `transient` = network/rate limit."""

    def __init__(self, msg: str, fatal: bool = False, transient: bool = False):
        super().__init__(msg)
        self.fatal, self.transient = fatal, transient


def lookup(api_key: str, fingerprint: str, duration_s: int, *, transport: httpx.BaseTransport | None = None,
           limiter: RateLimiter | None = None) -> list[dict]:
    """Normalised candidates, best first: [{acoustid, score, recordings: [{id, title, durationS, artists:
    [{id, name}], releases: [{id, title, year, country, trackCount, mediumCount, releaseGroup: {id, title, type,
    secondaryTypes}, artists, medium: {position, format, trackCount}, track: {position, title, artists}}]}]}]."""
    lim = limiter or LIMITER
    with lim:
        try:
            with httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0), transport=transport,
                              headers={"User-Agent": USER_AGENT, "Accept": "application/json"}) as c:
                r = c.post(URL, data={"client": api_key, "format": "json", "meta": META,
                                      "duration": str(int(duration_s)), "fingerprint": fingerprint})
        except httpx.HTTPError as e:
            raise AcoustIDError(f"AcoustID unreachable: {e}", transient=True) from e
    try:
        data = r.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise AcoustIDError(f"AcoustID error {r.status_code}", transient=r.status_code >= 500)
    if data.get("status") != "ok":
        err = data.get("error") or {}
        code, msg = err.get("code"), err.get("message") or f"HTTP {r.status_code}"
        if code in (4, 5):  # invalid API key / invalid user API key
            raise AcoustIDError(f"AcoustID rejected the API key: {msg}", fatal=True)
        if code == 14 or r.status_code in (429, 503):  # too many requests
            raise AcoustIDError(f"AcoustID rate limit: {msg}", transient=True)
        raise AcoustIDError(f"AcoustID error: {msg}", transient=r.status_code >= 500)
    return normalise(data.get("results") or [])


def _artists(a) -> list[dict]:
    return [{"id": x.get("id"), "name": x.get("name"), "joinphrase": x.get("joinphrase", "")}
            for x in a or [] if isinstance(x, dict)]


def _release(rel: dict, rg: dict | None, rec_id: str) -> dict:
    date = rel.get("date") or {}
    medium = track = None
    for m in rel.get("mediums") or []:
        for t in m.get("tracks") or []:
            # With meta=tracks AcoustID lists only the track(s) of this recording on the medium.
            track = {"position": t.get("position"), "title": t.get("title"), "artists": _artists(t.get("artists"))}
            medium = {"position": m.get("position"), "format": m.get("format"), "trackCount": m.get("track_count")}
            break
        if track:
            break
    g = rg or rel.get("releasegroup") or {}
    return {"id": rel.get("id"), "title": rel.get("title") or g.get("title"),
            "year": date.get("year") if isinstance(date, dict) else None, "country": rel.get("country"),
            "trackCount": rel.get("track_count"), "mediumCount": rel.get("medium_count"),
            "releaseGroup": {"id": g.get("id"), "title": g.get("title"), "type": g.get("type"),
                             "secondaryTypes": list(g.get("secondarytypes") or [])},
            "artists": _artists(rel.get("artists") or g.get("artists")), "medium": medium, "track": track}


def normalise(results: list) -> list[dict]:
    out = []
    for res in sorted((r for r in results if isinstance(r, dict)), key=lambda r: -(r.get("score") or 0)):
        score = float(res.get("score") or 0)
        if score < MIN_RESULT_SCORE:
            continue
        recs = []
        for rec in (res.get("recordings") or [])[:MAX_RECORDINGS]:
            if not rec.get("id"):
                continue
            releases = []
            for rg in rec.get("releasegroups") or []:
                for rel in rg.get("releases") or []:
                    releases.append(_release(rel, rg, rec["id"]))
            for rel in rec.get("releases") or []:
                releases.append(_release(rel, None, rec["id"]))
            recs.append({"id": rec["id"], "title": rec.get("title"),
                         "durationS": rec.get("duration"), "artists": _artists(rec.get("artists")),
                         "releases": releases[:MAX_RELEASES]})
        out.append({"acoustid": res.get("id"), "score": score, "recordings": recs})
        if len(out) >= MAX_RESULTS:
            break
    return out
