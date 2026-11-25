"""Release Radar: new releases (albums, EPs, singles) by the artists each user actually listens to.

Weekly job (`run`): take every active user's followed artists (plays in the last year with a half-life,
plus favourites), resolve their MusicBrainz ids (tag MBID, else an exact-name search, cached), and ask
the MusicBrainz *release-group search* for releases first released in the window
[today − window_days, today + lookahead_days], batching 20 artists per query
`(arid:A OR arid:B …) AND firstreleasedate:[from TO to]`. One shared HTTP client for all users keeps to
MusicBrainz's 1 request/second, with a generic User-Agent (no personal contact details). Results are
cached per user in `release_radar`; nothing in the library is touched.

Read side (`for_user`) is cheap SQL: the cached releases, plus albums by followed artists that were
recently added to the library. Server core decides ownership (its LibraryIndex) when rendering.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .common import iso, load_settings, norm_name, parse_ts, transaction
from .discovery import BAD_SECONDARY, MB, Http

log = logging.getLogger("homebeat.ai.release_radar")

DEFAULTS = {"window_days": 56, "lookahead_days": 30, "artists_per_user": 40, "max_per_user": 60, "batch": 20}
TYPES = ("album", "ep", "single")
HALF_LIFE_DAYS = 120


def _setting(conn: sqlite3.Connection, key: str) -> int:
    v = load_settings(conn).int(f"ai.release_radar.{key}")
    return v if v > 0 else DEFAULTS[key]


# ---------------------------------------------------------------- followed artists

def followed_artists(conn: sqlite3.Connection, user_id: str, limit: int, now: datetime | None = None
                     ) -> list[dict]:
    """[{id, name, mbid, weight 0..1}] — the user's most listened (decayed) and favourited artists."""
    now = now or datetime.now(UTC)
    w: dict[str, float] = defaultdict(float)
    since = iso(now - timedelta(days=365))
    for aid, at in conn.execute(
        "SELECT t.artist_id, e.at FROM events e JOIN tracks t ON t.id = e.track_id "
        "WHERE e.user_id = ? AND e.type = 'play' AND e.at >= ?", (user_id, since)
    ):
        ts = parse_ts(at)
        age = (now - ts).total_seconds() / 86400 if ts else 365
        w[aid] += 0.5 ** (max(age, 0) / HALF_LIFE_DAYS)
    for aid, n in conn.execute(
        "SELECT t.artist_id, COUNT(*) FROM user_tracks ut JOIN tracks t ON t.id = ut.track_id "
        "WHERE ut.user_id = ? AND ut.starred_at IS NOT NULL AND ut.blocked = 0 GROUP BY t.artist_id", (user_id,)
    ):
        w[aid] += 2.0 * n
    if not w:
        return []
    top = sorted(w.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    mx = top[0][1] or 1.0
    names = {}
    ids = [a for a, _ in top]
    for r in conn.execute(f"SELECT id, name, mbid FROM artists WHERE id IN ({','.join('?' * len(ids))})", ids):
        names[r[0]] = (r[1], r[2])
    return [{"id": a, "name": names[a][0], "mbid": names[a][1], "weight": round(v / mx, 4)}
            for a, v in top if a in names and names[a][0]]


# ---------------------------------------------------------------- MusicBrainz

class Resolver:
    """Artist name -> MBID via MusicBrainz search (exact normalised name), cached on disk."""

    def __init__(self, http: Http, cache_path: Path):
        self.http, self.path = http, cache_path
        try:
            self.cache: dict[str, Any] = json.loads(cache_path.read_text())
        except (OSError, ValueError):
            self.cache = {}
        self.cache.setdefault("artist_mbid", {})
        try:  # seed from the discovery cache so artists resolved there cost nothing
            disc = json.loads((cache_path.parent / "discovery.json").read_text()).get("artist_mbid", {})
            for k, v in disc.items():
                self.cache["artist_mbid"].setdefault(k, v)
        except (OSError, ValueError, AttributeError):
            pass

    def resolve(self, name: str, mbid: str | None) -> str | None:
        if mbid:
            return mbid.removeprefix("mb:")
        key = norm_name(name)
        cache = self.cache["artist_mbid"]
        if key in cache:
            return cache[key] or None
        data = self.http.get(f"{MB}/artist", {"query": f'artist:"{name}"', "fmt": "json", "limit": 5})
        if data is None:
            return None  # network trouble: try again next week
        best = None
        for a in data.get("artists", []):
            names = [a.get("name", "")] + [al.get("name", "") for al in a.get("aliases", []) or []]
            if any(norm_name(n) == key for n in names) and (
                    best is None or int(a.get("score", 0)) > int(best.get("score", 0))):
                best = a
        cache[key] = best["id"] if best else ""
        return cache[key] or None

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.cache))
            tmp.replace(self.path)
        except OSError as e:
            log.warning("could not write release radar cache: %s", e)


def build_query(mbids: list[str], start: date, end: date) -> str:
    arids = " OR ".join(f"arid:{m}" for m in mbids)
    types = " OR ".join(f"primarytype:{t}" for t in TYPES)
    return f"({arids}) AND firstreleasedate:[{start.isoformat()} TO {end.isoformat()}] AND ({types})"


def parse_release_groups(data: Any) -> list[dict]:
    """MusicBrainz release-group search JSON -> [{mbid, title, type, date, artist, artist_mbids}]."""
    out = []
    for rg in (data or {}).get("release-groups", []) or []:
        secondary = {s.lower() for s in rg.get("secondary-types", []) or []}
        ptype = (rg.get("primary-type") or "").strip()
        if secondary & BAD_SECONDARY or ptype.lower() not in TYPES or not rg.get("id") or not rg.get("title"):
            continue
        credits = rg.get("artist-credit") or []
        artist = "".join((c.get("name") or (c.get("artist") or {}).get("name") or "") + (c.get("joinphrase") or "")
                         for c in credits).strip()
        out.append({"mbid": rg["id"], "title": rg["title"], "type": ptype, "date": rg.get("first-release-date") or None,
                    "artist": artist or "Unknown artist",
                    "artist_mbids": [(c.get("artist") or {}).get("id") for c in credits if (c.get("artist") or {}).get("id")]})
    return out


def search_releases(http: Http, mbids: list[str], start: date, end: date, batch: int) -> list[dict] | None:
    """Releases in the window, or None when MusicBrainz couldn't be reached at all."""
    found: dict[str, dict] = {}
    answered = 0
    for i in range(0, len(mbids), batch):
        q = build_query(mbids[i:i + batch], start, end)
        offset = 0
        for _page in range(3):  # at most 300 releases per batch of artists
            data = http.get(f"{MB}/release-group", {"query": q, "fmt": "json", "limit": 100, "offset": offset})
            if data is None:
                break
            answered += 1
            for rg in parse_release_groups(data):
                found.setdefault(rg["mbid"], rg)
            total = int(data.get("count") or 0)
            offset += 100
            if offset >= total:
                break
    return list(found.values()) if answered else None


# ---------------------------------------------------------------- job

def assign(followed: list[dict], mbid_of: dict[str, str | None], releases: list[dict], max_items: int
           ) -> list[dict]:
    """The releases credited to this user's followed artists, best artist first, newest first."""
    by_mbid = {mbid_of[a["id"]]: a for a in followed if mbid_of.get(a["id"])}
    out = []
    for rg in releases:
        hits = [by_mbid[m] for m in rg["artist_mbids"] if m in by_mbid]
        if not hits:
            continue
        a = max(hits, key=lambda x: x["weight"])
        out.append({**rg, "artist_id": a["id"], "artist_mbid": next(m for m in rg["artist_mbids"] if m in by_mbid),
                    "score": a["weight"]})
    out.sort(key=lambda r: (r["date"] or "", r["score"]), reverse=True)
    return out[:max_items]


def run(conn: sqlite3.Connection, cfg, http: Http | None = None, now: datetime | None = None,
        users: list[str] | None = None) -> dict[str, int]:
    """Refresh every active user's radar. Returns {user_id: releases}. Network failures keep the old cache."""
    now = now or datetime.now(UTC)
    http = http or Http()
    window, ahead = _setting(conn, "window_days"), _setting(conn, "lookahead_days")
    per_user, max_items, batch = _setting(conn, "artists_per_user"), _setting(conn, "max_per_user"), _setting(conn, "batch")
    if users is None:
        users = [r[0] for r in conn.execute("SELECT id FROM users WHERE disabled = 0 ORDER BY created_at, id")]
    followed = {u: followed_artists(conn, u, per_user, now) for u in users}
    resolver = Resolver(http, Path(cfg.data_dir) / "cache" / "release_radar.json")
    mbid_of: dict[str, str | None] = {}
    for arts in followed.values():
        for a in arts:
            if a["id"] not in mbid_of:
                mbid_of[a["id"]] = resolver.resolve(a["name"], a["mbid"])
    resolver.save()
    wanted = sorted({m for m in mbid_of.values() if m})
    today = now.date()
    releases = search_releases(http, wanted, today - timedelta(days=window), today + timedelta(days=ahead), batch) \
        if wanted else []
    if releases is None:
        log.warning("release radar: MusicBrainz unreachable; keeping last week's results")
        return {}
    out = {}
    stamp = iso(now)
    with transaction(conn):
        for u in users:
            items = assign(followed[u], mbid_of, releases, max_items)
            conn.execute("DELETE FROM release_radar WHERE user_id = ?", (u,))
            conn.executemany(
                "INSERT OR IGNORE INTO release_radar (user_id, release_group_mbid, title, artist, artist_mbid, artist_id, "
                "primary_type, release_date, cover_url, score, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(u, r["mbid"], r["title"], r["artist"], r["artist_mbid"], r["artist_id"], r["type"], r["date"],
                  f"https://coverartarchive.org/release-group/{r['mbid']}/front-250", r["score"], stamp) for r in items])
            out[u] = len(items)
    log.info("release radar: %d artists, %d releases, %d HTTP calls", len(wanted), len(releases), http.calls)
    return out


# ---------------------------------------------------------------- read

def for_user(conn: sqlite3.Connection, user_id: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    window, ahead = _setting(conn, "window_days"), _setting(conn, "lookahead_days")
    today = now.date().isoformat()
    rows = conn.execute(
        "SELECT * FROM release_radar WHERE user_id = ? ORDER BY COALESCE(release_date, '') DESC, score DESC",
        (user_id,)).fetchall()
    releases = [{"mbid": r["release_group_mbid"], "title": r["title"], "artist": r["artist"],
                 "artistMbid": r["artist_mbid"], "artistId": r["artist_id"], "type": r["primary_type"],
                 "date": r["release_date"], "coverUrl": r["cover_url"], "score": r["score"],
                 "upcoming": bool(r["release_date"] and len(r["release_date"]) >= 10 and r["release_date"] > today)}
                for r in rows]
    updated = max((r["created_at"] for r in rows), default=None)
    followed = followed_artists(conn, user_id, _setting(conn, "artists_per_user"), now)
    added = []
    if followed:
        ids = [a["id"] for a in followed]
        since = iso(now - timedelta(days=window))
        added = [{"albumId": r[0], "artistId": r[1], "addedAt": r[2]} for r in conn.execute(
            f"SELECT al.id, al.artist_id, MAX(t.added_at) FROM albums al JOIN tracks t ON t.album_id = al.id "
            f"WHERE t.missing = 0 AND (al.artist_id IN ({','.join('?' * len(ids))}) OR t.artist_id IN "
            f"({','.join('?' * len(ids))})) GROUP BY al.id HAVING MIN(t.added_at) >= ? ORDER BY MAX(t.added_at) DESC "
            "LIMIT 30", (*ids, *ids, since))]
    return {"updatedAt": updated, "windowDays": window, "lookaheadDays": ahead, "releases": releases,
            "recentlyAdded": added}
