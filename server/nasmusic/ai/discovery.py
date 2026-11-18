"""Weekly album discovery: suggest ~10 albums you don't own. Nothing is ever downloaded except
30-second previews, which are embedded in memory and discarded.

Candidates
* Similar artists of your top artists: ListenBrainz (labs similar-artists), plus Last.fm
  `artist.getSimilar` when `cfg.lastfm_api_key` is set.
* Albums you don't own by artists you play a lot (new releases and discography gaps).
* Albums come from ListenBrainz release-group popularity, falling back to MusicBrainz browse.

Matching: the library is not MusicBrainz-tagged yet, so artists are resolved by normalised name
(MusicBrainz search, exact normalised-name match only), cached in `data_dir/cache/discovery.json`.

Scoring: taste match by artist (how strongly linked to the artists you actually play) plus taste
match by sound (Deezer previews through the same audio model, compared with your taste centres),
minus closeness to artists you answered "Not interested". At most 2 per artist cluster.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import quote, quote_plus

import httpx
import numpy as np

from .common import iso, load_settings, new_id, norm_name, transaction
from .extractors import decode, make_extractor
from .recommender import Recommender

log = logging.getLogger("nasmusic.ai.discovery")

USER_AGENT = "nas-music/0.1 (open-source self-hosted music server)"
MB = "https://musicbrainz.org/ws/2"
LB = "https://api.listenbrainz.org/1"
LB_LABS = "https://labs.api.listenbrainz.org"
LASTFM = "https://ws.audioscrobbler.com/2.0/"
DEEZER = "https://api.deezer.com"
LB_ALGO = "session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30"
BAD_SECONDARY = {"compilation", "live", "remix", "dj-mix", "mixtape/street", "demo", "soundtrack",
                 "interview", "spokenword", "audiobook"}


# ---------------------------------------------------------------- polite HTTP

class Http:
    """httpx wrapper: per-host minimum interval (MusicBrainz 1 req/s), a retry on 503/429, JSON or None."""

    INTERVALS: ClassVar[dict[str, float]] = {"musicbrainz.org": 1.05, "api.listenbrainz.org": 0.3, "labs.api.listenbrainz.org": 0.3,
                 "api.deezer.com": 0.15, "ws.audioscrobbler.com": 0.25}

    def __init__(self, client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.client = client or httpx.Client(timeout=20, follow_redirects=True)
        self.client.headers["User-Agent"] = USER_AGENT
        self.sleep, self.clock = sleep, clock
        self.last: dict[str, float] = {}
        self.calls = 0

    def _wait(self, host: str) -> None:
        gap = self.INTERVALS.get(host, 0.2)
        last = self.last.get(host)
        if last is not None:
            dt = self.clock() - last
            if dt < gap:
                self.sleep(gap - dt)
        self.last[host] = self.clock()

    def get(self, url: str, params: dict | None = None, raw: bool = False) -> Any:
        host = httpx.URL(url).host
        for attempt in range(3):
            self._wait(host)
            self.calls += 1
            try:
                r = self.client.get(url, params=params)
            except httpx.HTTPError as e:
                log.warning("GET %s failed: %s", url, e)
                return None
            if r.status_code in (429, 503) and attempt < 2:
                self.sleep(2.0 * (attempt + 1))
                continue
            if r.status_code != 200:
                log.info("GET %s -> %s", url, r.status_code)
                return None
            if raw:
                return r.content
            try:
                return r.json()
            except ValueError:
                return None
        return None


# ---------------------------------------------------------------- data

@dataclass
class Candidate:
    name: str
    mbid: str | None = None
    links: dict[str, float] = field(default_factory=dict)   # seed artist name -> weighted similarity
    owned: bool = False                                       # artist already in the library
    artist_score: float = 0.0
    penalty: float = 0.0

    @property
    def cluster(self) -> str:
        return max(self.links, key=self.links.get) if self.links else self.name


@dataclass
class Album:
    cand: Candidate
    title: str
    year: int | None
    rg_mbid: str | None
    cover_url: str | None = None
    preview_url: str | None = None
    sound: float | None = None        # percentile of the preview's taste match within the library
    sound_label: str | None = None    # name of the taste cluster the previews sound closest to
    score: float = 0.0


class Discovery:
    def __init__(self, conn: sqlite3.Connection, cfg, http: Http | None = None, rec: Recommender | None = None,
                 embed: Callable[[bytes], np.ndarray | None] | None = None):
        self.conn, self.cfg = conn, cfg
        self.http = http or Http()
        self.s = load_settings(conn)
        self.rec = rec or Recommender(conn, cfg)
        self.model = self.rec.load_or_train()
        self._embed = embed
        self._extractor = None
        self.cache_path = Path(cfg.data_dir) / "cache" / "discovery.json"
        self.cache = self._load_cache()

    # ------------------------------------------------------------ cache

    def _load_cache(self) -> dict[str, Any]:
        try:
            return json.loads(self.cache_path.read_text())
        except (OSError, ValueError):
            return {"artist_mbid": {}}

    def _save_cache(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.cache))
            tmp.replace(self.cache_path)
        except OSError as e:
            log.warning("could not write discovery cache: %s", e)

    # ------------------------------------------------------------ library state

    def taste_artists(self) -> list[tuple[str, str | None, float]]:
        """(name, mbid, weight) of your top artists, by positive feedback and plays."""
        lib, sig = self.rec.lib, self.rec.sig
        w: dict[str, float] = defaultdict(float)
        for i in range(len(lib)):
            w[lib.artist[i]] += max(sig.weight[i], 0) + 0.25 * sig.plays[i]
        if not any(v > 0 for v in w.values()):  # no feedback yet: what you own the most of
            for (aid, cnt) in self.conn.execute(
                    "SELECT artist_id, COUNT(*) FROM tracks WHERE missing = 0 GROUP BY artist_id"):
                w[aid] = cnt * 0.1
        rows = {r[0]: (r[1], r[2]) for r in self.conn.execute("SELECT id, name, mbid FROM artists")}
        top = sorted(((a, v) for a, v in w.items() if v > 0 and a in rows), key=lambda kv: -kv[1])
        top = top[: self.s.int("ai.discovery.seed_artists")]
        mx = top[0][1] if top else 1.0
        return [(rows[a][0], rows[a][1], v / mx) for a, v in top]

    def owned(self) -> tuple[set[str], set[tuple[str, str]], set[str]]:
        artists, albums, mbids = set(), set(), set()
        for name, title, mbid in self.conn.execute(
                "SELECT ar.name, al.title, al.mbid FROM albums al JOIN artists ar ON ar.id = al.artist_id"):
            artists.add(norm_name(name))
            albums.add((norm_name(name), norm_name(title)))
            if mbid:
                mbids.add(mbid)
        for (name,) in self.conn.execute("SELECT name FROM artists"):
            artists.add(norm_name(name))
        return artists, albums, mbids

    def answered(self) -> tuple[set[tuple[str, str]], set[str], list[str]]:
        albums, mbids, not_interested = set(), set(), []
        for artist, album, rg, status in self.conn.execute(
                "SELECT artist, album, release_group_mbid, status FROM suggestions"):
            albums.add((norm_name(artist), norm_name(album)))
            if rg:
                mbids.add(rg)
            if status == "not_interested":
                not_interested.append(artist)
        return albums, mbids, not_interested

    # ------------------------------------------------------------ sources

    def resolve_artist(self, name: str, mbid: str | None = None) -> str | None:
        if mbid:
            return mbid
        key = norm_name(name)
        cache = self.cache.setdefault("artist_mbid", {})
        if key in cache:
            return cache[key] or None
        data = self.http.get(f"{MB}/artist", {"query": f'artist:"{name}"', "fmt": "json", "limit": 5})
        if data is None:
            return None  # network problem: don't cache
        best = None
        for a in data.get("artists", []):
            names = [a.get("name", "")] + [al.get("name", "") for al in a.get("aliases", []) or []]
            if any(norm_name(n) == key for n in names) and (
                    best is None or int(a.get("score", 0)) > int(best.get("score", 0))):
                best = a
        cache[key] = best["id"] if best else ""
        return cache[key] or None

    def similar_listenbrainz(self, mbid: str) -> list[tuple[str, str | None, float]]:
        data = self.http.get(f"{LB_LABS}/similar-artists/json", {"artist_mbids": mbid, "algorithm": LB_ALGO})
        if not isinstance(data, list) or not data:
            return []
        mx = max(float(d.get("score", 0) or 0) for d in data) or 1.0
        return [(d["name"], d.get("artist_mbid"), float(d.get("score", 0)) / mx) for d in data[:40]
                if d.get("name")]

    def similar_lastfm(self, name: str) -> list[tuple[str, str | None, float]]:
        if not self.cfg.lastfm_api_key:
            return []
        data = self.http.get(LASTFM, {"method": "artist.getsimilar", "artist": name, "limit": 40,
                                      "api_key": self.cfg.lastfm_api_key, "format": "json", "autocorrect": 1})
        try:
            arts = data["similarartists"]["artist"]
        except (TypeError, KeyError):
            return []
        return [(a["name"], a.get("mbid") or None, float(a.get("match", 0))) for a in arts if a.get("name")]

    def release_groups(self, mbid: str) -> list[tuple[str, int | None, str]]:
        """(title, year, release group mbid), most popular first."""
        out: list[tuple[str, int | None, str]] = []
        data = self.http.get(f"{LB}/popularity/top-release-groups-for-artist/{mbid}")
        if isinstance(data, list):
            for d in data:
                rg = d.get("release_group") or {}
                if (rg.get("type") or "").lower() not in ("album", ""):
                    continue
                title = rg.get("name") or (d.get("release") or {}).get("name")
                if title and d.get("release_group_mbid"):
                    out.append((title, _year(rg.get("date")), d["release_group_mbid"]))
        if out:
            return out
        data = self.http.get(f"{MB}/release-group", {"artist": mbid, "type": "album", "fmt": "json", "limit": 100})
        rgs = (data or {}).get("release-groups", [])
        rgs = [r for r in rgs if not {s.lower() for s in r.get("secondary-types", [])} & BAD_SECONDARY]
        rgs.sort(key=lambda r: r.get("first-release-date") or "", reverse=True)  # newest first
        return [(r["title"], _year(r.get("first-release-date")), r["id"]) for r in rgs if r.get("title")]

    # ------------------------------------------------------------ sound check

    def embed_preview(self, audio: bytes) -> np.ndarray | None:
        if self._embed is not None:
            return self._embed(audio)
        try:
            if self._extractor is None:
                self._extractor = make_extractor(self.rec.version, Path(self.cfg.data_dir))
            ex = self._extractor
            wav = decode(None, ex.sr, data=audio, max_seconds=35, timeout=60)
            return ex.extract_audio(wav, ex.sr).embedding
        except Exception as e:  # noqa: BLE001
            log.info("preview embedding failed: %s", e)
            return None

    def sound_check(self, alb: Album) -> None:
        q = f'artist:"{alb.cand.name}" album:"{alb.title}"'
        data = self.http.get(f"{DEEZER}/search/album", {"q": q, "limit": 5})
        hits = (data or {}).get("data") or []
        hit = next((h for h in hits if norm_name(h.get("artist", {}).get("name")) == norm_name(alb.cand.name)
                    and norm_name(h.get("title")) == norm_name(alb.title)), None)
        hit = hit or next((h for h in hits if norm_name(h.get("artist", {}).get("name"))
                           == norm_name(alb.cand.name)), None)
        if not hit:
            return
        alb.cover_url = alb.cover_url or hit.get("cover_medium")
        tracks = (self.http.get(f"{DEEZER}/album/{hit['id']}/tracks", {"limit": 50}) or {}).get("data") or []
        tracks = [t for t in tracks if t.get("preview")]
        if not tracks:
            return
        # Deezer preview URLs are signed and expire within minutes; store the API URL that returns a
        # fresh one instead.
        alb.preview_url = f"{DEEZER}/track/{tracks[0]['id']}"
        picks = [tracks[len(tracks) // 3], tracks[0]] if len(tracks) > 2 else tracks[:2]
        embs = []
        for t in picks:
            audio = self.http.get(t["preview"], raw=True)
            if not audio:
                continue
            emb = self.embed_preview(audio)
            del audio  # never stored
            if emb is not None and emb.shape[-1] == self.rec.lib.emb.shape[1]:
                embs.append(self.rec.lib.normalise(emb))
        if embs:
            s = float(np.mean(self.model.sound_score(np.vstack(embs))))
            alb.sound = float(np.mean(self._lib_sound <= s))
            mean = np.mean(embs, axis=0)
            j, sim = self.model.nearest_centroid(mean / max(np.linalg.norm(mean), 1e-9))
            if j >= 0 and sim > 0.3:
                alb.sound_label = self.model.centroid_labels[j]

    # ------------------------------------------------------------ main

    def run(self) -> int:
        per_week = self.s.int("ai.discovery.per_week")
        max_cluster = self.s.int("ai.discovery.max_per_cluster")
        seeds = self.taste_artists()
        if not seeds:
            log.info("discovery: library is empty; nothing to do")
            return 0
        owned_artists, owned_albums, owned_mbids = self.owned()
        ans_albums, ans_mbids, not_interested = self.answered()
        self._lib_sound = (self.model.sound_score(self.rec.lib.emb) if len(self.rec.lib) else np.zeros(1))

        cands: dict[str, Candidate] = {}
        for name, mbid, w in seeds:
            mbid = self.resolve_artist(name, mbid)
            sims = (self.similar_listenbrainz(mbid) if mbid else []) + self.similar_lastfm(name)
            for cname, cmbid, sim in sims:
                key = norm_name(cname)
                if not key or key == norm_name(name):
                    continue
                c = cands.setdefault(key, Candidate(cname, cmbid))
                c.mbid = c.mbid or cmbid
                c.links[name] = max(c.links.get(name, 0.0), w * sim)
            # albums you don't own by an artist you play a lot
            key = norm_name(name)
            c = cands.setdefault(key, Candidate(name, mbid, owned=True))
            c.owned, c.mbid = True, c.mbid or mbid
            c.links[name] = max(c.links.get(name, 0.0), 0.6 * w)

        # "Not interested" memory: those artists and their close neighbours are pushed down
        for ni in dict.fromkeys(not_interested[-10:]):
            key = norm_name(ni)
            if key in cands:
                cands[key].penalty = max(cands[key].penalty, 0.5)
            mbid = self.resolve_artist(ni)
            for cname, _, sim in (self.similar_listenbrainz(mbid) if mbid else [])[:20]:
                c = cands.get(norm_name(cname))
                if c is not None:
                    c.penalty = max(c.penalty, 0.4 * sim)

        for key, c in cands.items():
            if key in owned_artists and not c.owned:
                c.owned = True  # similar artist you already own: only missing albums count
            vals = sorted(c.links.values(), reverse=True)
            c.artist_score = vals[0] + 0.5 * sum(vals[1:4]) if vals else 0.0

        ranked = sorted(cands.values(), key=lambda c: -(c.artist_score - c.penalty))
        albums: list[Album] = []
        looked = 0
        for c in ranked:
            if len(albums) >= per_week * 4 or looked >= per_week * 8:
                break
            looked += 1
            mbid = c.mbid or self.resolve_artist(c.name)
            if not mbid:
                continue
            c.mbid = mbid
            for title, year, rg in self.release_groups(mbid):
                k = (norm_name(c.name), norm_name(title))
                if rg in owned_mbids or rg in ans_mbids or k in owned_albums or k in ans_albums:
                    continue
                albums.append(Album(c, title, year, rg,
                                    cover_url=f"https://coverartarchive.org/release-group/{rg}/front-250"))
                break
        self._save_cache()
        if not albums:
            log.info("discovery: no new candidates")
            return 0

        mx = max(a.cand.artist_score for a in albums) or 1.0
        albums.sort(key=lambda a: -(a.cand.artist_score - a.cand.penalty))
        for a in albums[: self.s.int("ai.discovery.preview_candidates")]:
            try:
                self.sound_check(a)
            except Exception:
                log.exception("sound check failed for %s - %s", a.cand.name, a.title)
        for a in albums:
            art = a.cand.artist_score / mx
            snd = a.sound if a.sound is not None else 0.4  # unknown sound: a little below average
            a.score = 0.55 * art + 0.45 * snd - 0.6 * a.cand.penalty

        picked: list[Album] = []
        per_cluster: dict[str, int] = defaultdict(int)
        for a in sorted(albums, key=lambda a: -a.score):
            if len(picked) >= per_week:
                break
            if per_cluster[a.cand.cluster] >= max_cluster:
                continue
            per_cluster[a.cand.cluster] += 1
            picked.append(a)
        return self.write(picked)

    def write(self, picked: list[Album]) -> int:
        n = 0
        now = iso()
        locale = self.s["ai.discovery.qobuz_locale"] or "gb-en"
        with transaction(self.conn):
            for a in picked:
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO suggestions (id, artist, album, year, artist_mbid, release_group_mbid, "
                    "cover_url, preview_url, buy_links, reason, score, status, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?, 'new', ?)",
                    (new_id(), a.cand.name, a.title, a.year, a.cand.mbid, a.rg_mbid, a.cover_url, a.preview_url,
                     json.dumps(buy_links(a.cand.name, a.title, locale)), reason(a, self.model), round(a.score, 4),
                     now))
                n += cur.rowcount
        log.info("discovery: %d new suggestions (%d HTTP calls)", n, self.http.calls)
        return n


def buy_links(artist: str, album: str, qobuz_locale: str = "gb-en") -> list[dict[str, str]]:
    q = f"{artist} {album}"
    return [
        {"name": "Bandcamp", "url": f"https://bandcamp.com/search?q={quote_plus(q)}&item_type=a"},
        {"name": "Qobuz", "url": f"https://www.qobuz.com/{qobuz_locale}/search/albums/{quote(q, safe='')}"},
    ]


def reason(a: Album, model) -> str:
    links = sorted(a.cand.links.items(), key=lambda kv: -kv[1])
    others = [n for n, _ in links if norm_name(n) != norm_name(a.cand.name)]
    if a.cand.owned and not others:
        when = f"a {a.year} album" if a.year else "an album"
        base = f"{when} by {a.cand.name}, whom you play a lot, that you don't own"
        base = base[0].upper() + base[1:]
    elif len(others) == 1:
        base = f"Linked to {others[0]}, which you play"
    else:
        base = f"Linked to {len(others)} artists you play ({', '.join(others[:2])})"
    if a.sound is not None and a.sound >= 0.6:
        label = a.sound_label
        base += f", and sounds like your {label} favourites" if label else ", and sounds close to your favourites"
    return base


def _year(date: str | None) -> int | None:
    try:
        return int((date or "")[:4])
    except ValueError:
        return None


def run(conn: sqlite3.Connection, cfg, http: Http | None = None, **kw) -> int:
    return Discovery(conn, cfg, http=http, **kw).run()
