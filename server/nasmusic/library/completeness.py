"""Album completeness: "9 of 12" on the album page, and which tracks are missing.

The album's MusicBrainz release group is the album's own `mbid` tag, else a release-group search by album
artist + title (accepted only at score >= MIN_SEARCH_SCORE). Of the group's releases we pick the one the
library matches best (most tracks matched, then official, then the fewest tracks), so a 12-track album isn't
reported as "12 of 18" against a deluxe edition. Video media (DVD, Blu-ray, ...) are left out.

Library tracks are matched to the release's tracks by recording MBID, then normalised title, then the same
disc/position for whatever is left. Only the album artist, title and MBIDs go out, with the generic
User-Agent and the process-wide 1 request/s MusicBrainz limit (library/musicbrainz.py).

Lookups are cached in `mb_releases` under synthetic keys (no schema change): "rg:<release-group mbid>" holds
the browse result, "rgsearch:<artist|title>" the search's answer ({"id": mbid or null}).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import httpx

from nasmusic import catalog
from nasmusic.core import now_iso
from nasmusic.library import musicbrainz

CACHE_DAYS = 30            # a release group's track lists rarely change
NEGATIVE_CACHE_DAYS = 7    # "not found on MusicBrainz" is asked again after a week
MIN_SEARCH_SCORE = 95
MAX_RELEASES = 25
VIDEO_FORMATS = ("dvd", "blu-ray", "vhs", "video", "hd-dvd", "umd")


class Unavailable(Exception):
    """MusicBrainz could not be asked (offline, rate-limited); nothing was cached."""


def _fresh(fetched_at: str, days: float) -> bool:
    try:
        t = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return datetime.now(UTC) - t < timedelta(days=days)


def _cached(conn: sqlite3.Connection, key: str, days: float) -> dict | None:
    row = conn.execute("SELECT data, fetched_at FROM mb_releases WHERE mbid = ?", (key,)).fetchone()
    if row and _fresh(row["fetched_at"], days):
        return json.loads(row["data"])
    return None


def _store(conn: sqlite3.Connection, key: str, data: dict) -> None:
    conn.execute("INSERT OR REPLACE INTO mb_releases (mbid, data, fetched_at) VALUES (?, ?, ?)",
                 (key, json.dumps(data, separators=(",", ":")), now_iso()))


def _ask(path: str, params: dict, transport, min_interval_s: float) -> dict:
    try:
        return musicbrainz._get(path, params, transport, min_interval_s)
    except musicbrainz.LookupError_ as e:
        raise Unavailable(str(e)) from e


def find_release_group(conn: sqlite3.Connection, artist: str, title: str, *, transport=None,
                       min_interval_s: float = musicbrainz.MIN_INTERVAL_S) -> str | None:
    key = "rgsearch:" + catalog.match_key(artist, title)
    hit = _cached(conn, key, CACHE_DAYS)
    if hit is None:
        q = f"releasegroup:{musicbrainz._q(title)} AND artist:{musicbrainz._q(artist)}"
        data = _ask("release-group", {"query": q, "limit": 5}, transport, min_interval_s)
        found = None
        for rg in data.get("release-groups") or []:
            if int(rg.get("score") or 0) >= MIN_SEARCH_SCORE and catalog.norm(rg.get("title")) == catalog.norm(title):
                found = rg.get("id")
                break
        hit = {"id": found}
        _store(conn, key, hit)
    elif hit.get("id") is None:
        # a cached miss expires sooner than a hit
        row = conn.execute("SELECT fetched_at FROM mb_releases WHERE mbid = ?", (key,)).fetchone()
        if not _fresh(row["fetched_at"], NEGATIVE_CACHE_DAYS):
            conn.execute("DELETE FROM mb_releases WHERE mbid = ?", (key,))
            return find_release_group(conn, artist, title, transport=transport, min_interval_s=min_interval_s)
    return hit.get("id")


def releases(conn: sqlite3.Connection, rg_mbid: str, *, transport=None,
             min_interval_s: float = musicbrainz.MIN_INTERVAL_S) -> list[dict]:
    key = f"rg:{rg_mbid}"
    data = _cached(conn, key, CACHE_DAYS)
    if data is None:
        data = _ask("release", {"release-group": rg_mbid, "inc": "recordings+media", "limit": MAX_RELEASES},
                    transport, min_interval_s)
        data = {"releases": [_slim(r) for r in data.get("releases") or []]}
        _store(conn, key, data)
    return data["releases"]


def _slim(r: dict) -> dict:
    """Just what matching needs (the browse result is large)."""
    media = []
    for m in r.get("media") or []:
        if any(v in (m.get("format") or "").lower() for v in VIDEO_FORMATS):
            continue
        media.append({"position": m.get("position") or len(media) + 1, "tracks": [
            {"position": t.get("position"), "title": t.get("title") or (t.get("recording") or {}).get("title") or "",
             "lengthMs": t.get("length") or (t.get("recording") or {}).get("length"),
             "recording": (t.get("recording") or {}).get("id")}
            for t in m.get("tracks") or []]})
    return {"id": r.get("id"), "title": r.get("title"), "status": r.get("status"), "date": r.get("date"),
            "disambiguation": r.get("disambiguation") or None, "media": media}


def match(release: dict, tracks: list[dict]) -> tuple[list[dict], int]:
    """(release tracks each with `have`: library track id or None, number of library tracks matched)."""
    out = [{"disc": m["position"], "position": t["position"], "title": t["title"], "lengthMs": t["lengthMs"],
            "recording": t["recording"], "have": None} for m in release["media"] for t in m["tracks"]]
    free = list(tracks)

    def take(pred) -> None:
        for slot in out:
            if slot["have"]:
                continue
            for lt in free:
                if pred(slot, lt):
                    slot["have"] = lt["id"]
                    free.remove(lt)
                    break

    take(lambda s, lt: bool(s["recording"] and lt.get("mbid") and s["recording"].lower() == lt["mbid"].lower()))
    take(lambda s, lt: catalog.norm(s["title"]) != "" and catalog.norm(s["title"]) == catalog.norm(lt["title"]))
    take(lambda s, lt: lt.get("trackNo") == s["position"] and (lt.get("discNo") or 1) == s["disc"])
    return out, len(tracks) - len(free)


def pick(rels: list[dict], tracks: list[dict]) -> tuple[dict, list[dict]] | None:
    best = None
    for r in rels:
        n = sum(len(m["tracks"]) for m in r["media"])
        if n == 0:
            continue
        slots, matched = match(r, tracks)
        rank = (matched, r.get("status") == "Official", -abs(n - len(tracks)), -n)
        if best is None or rank > best[0]:
            best = (rank, r, slots)
    return (best[1], best[2]) if best else None


def album_completeness(conn: sqlite3.Connection, album: dict, *, transport: httpx.BaseTransport | None = None,
                       min_interval_s: float = musicbrainz.MIN_INTERVAL_S) -> dict:
    """`album` is queries.album()'s dict (with its visible tracks). Raises Unavailable when MusicBrainz can't
    be reached and nothing is cached."""
    tracks = [dict(t) for t in album.get("tracks") or []]
    if tracks:
        ids = [t["id"] for t in tracks]
        mb = dict(conn.execute(f"SELECT id, mbid FROM tracks WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall())
        for t in tracks:
            t["mbid"] = mb.get(t["id"])
    base = {"albumId": album["id"], "have": len(tracks), "total": None, "status": "unknown",
            "releaseGroupMbid": None, "releaseMbid": None, "releaseTitle": None, "discCount": None, "missing": []}
    rg = album.get("mbid")
    if not rg:
        rg = find_release_group(conn, album["artist"], album["title"], transport=transport,
                                min_interval_s=min_interval_s)
    if not rg:
        return base
    chosen = pick(releases(conn, rg, transport=transport, min_interval_s=min_interval_s), tracks)
    base["releaseGroupMbid"] = rg
    if chosen is None:
        return base
    rel, slots = chosen
    have = sum(1 for s in slots if s["have"])
    if have == 0:  # nothing lines up: probably the wrong release group; say nothing rather than "0 of 14"
        return base
    missing = [{"disc": s["disc"], "position": s["position"], "title": s["title"],
                "durationS": round(s["lengthMs"] / 1000) if s["lengthMs"] else None,
                "recordingMbid": s["recording"]} for s in slots if not s["have"]]
    title = rel["title"] + (f" ({rel['disambiguation']})" if rel.get("disambiguation") else "")
    base.update(have=have, total=len(slots), status="complete" if not missing else "incomplete",
                releaseMbid=rel["id"], releaseTitle=title, discCount=len(rel["media"]), missing=missing)
    return base
