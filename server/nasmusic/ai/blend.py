"""Blend: one auto-updating playlist mixing the tastes of two or more users who all said yes.

Server core owns membership and consent (`blends`, `blend_members`); this module only reads the members
whose status is 'joined' and writes `blend_tracks` plus `blends.match` / `blends.built_at`.

Inputs per member (their own data only; never listening context): play / skip events of the last year
with a 90-day half-life, favourites, ratings and thumbs-down (blocked tracks never enter the blend, for
anyone). Maths, all deterministic for a given day:

* track weight w_u(t) in 0..1: log1p(decayed completed plays + 3·favourite + 0.5·(rating − 3)), scaled
  so the user's top track is 1.
* taste match between two users = 0.45·artist + 0.25·genre + 0.30·sound (weights renormalised over the
  parts both users have data for), where artist / genre are cosine similarities of √-weighted artist and
  genre vectors, and sound is the cosine of their weighted mean audio embeddings (clipped at 0).
  Shown as pct = round(100 · score^0.6). Group match = mean of the pairwise matches.
* each member gets an equal share of the slots. Tracks two or more members like (w ≥ 0.2) go first
  ("shared"); then round-robin over members, each picking their next track by w_u(t) + 0.6·mean affinity
  of the others (their own w, else half their artist weight, else 0.4·sound closeness), with a little
  per-day noise so the blend changes as it is refreshed. At most 3 tracks per artist, never the same artist
  twice in a row.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np

from .common import active_version, iso, load_library, parse_ts, transaction
from .extractors import effective_version

log = logging.getLogger("nasmusic.ai.blend")

HALF_LIFE_DAYS = 90
LOOKBACK_DAYS = 365
SHARED_MIN = 0.2
OTHERS_WEIGHT = 0.6
MAX_PER_ARTIST = 3
DEFAULT_SIZE = 50


@dataclass
class Profile:
    user_id: str
    tracks: dict[str, float] = field(default_factory=dict)     # track id -> w in 0..1
    artists: dict[str, float] = field(default_factory=dict)    # artist id -> summed w
    genres: dict[str, float] = field(default_factory=dict)
    blocked: set[str] = field(default_factory=set)
    centroid: np.ndarray | None = None                         # weighted mean embedding (L2-normalised)


# ---------------------------------------------------------------- pure maths

def track_weights(raw: dict[str, float]) -> dict[str, float]:
    """Raw scores -> 0..1 (log-scaled, top track = 1). Non-positive scores are dropped."""
    logs = {t: math.log1p(v) for t, v in raw.items() if v > 0}
    mx = max(logs.values(), default=0.0)
    return {t: v / mx for t, v in logs.items()} if mx > 0 else {}


def cosine(a: dict[str, float], b: dict[str, float]) -> float | None:
    """Cosine of √-weighted sparse vectors; None when either is empty."""
    if not a or not b:
        return None
    sa = {k: math.sqrt(max(v, 0)) for k, v in a.items()}
    sb = {k: math.sqrt(max(v, 0)) for k, v in b.items()}
    na = math.sqrt(sum(v * v for v in sa.values()))
    nb = math.sqrt(sum(v * v for v in sb.values()))
    if na == 0 or nb == 0:
        return None
    return sum(v * sb.get(k, 0.0) for k, v in sa.items()) / (na * nb)


def match_score(a: Profile, b: Profile) -> float | None:
    """0..1 taste match, or None when either user has no listening data."""
    parts = []
    art = cosine(a.artists, b.artists)
    if art is not None:
        parts.append((0.45, art))
    gen = cosine(a.genres, b.genres)
    if gen is not None:
        parts.append((0.25, gen))
    if a.centroid is not None and b.centroid is not None:
        parts.append((0.30, max(0.0, float(np.dot(a.centroid, b.centroid)))))
    if not parts:
        return None
    total = sum(w for w, _ in parts)
    return max(0.0, min(1.0, sum(w * v for w, v in parts) / total))


def match_pct(score: float | None) -> int | None:
    return None if score is None else round(100 * score ** 0.6)


def group_match(profiles: list[Profile]) -> dict:
    pairs = []
    for i in range(len(profiles)):
        for j in range(i + 1, len(profiles)):
            pct = match_pct(match_score(profiles[i], profiles[j]))
            pairs.append({"a": profiles[i].user_id, "b": profiles[j].user_id, "pct": pct})
    known = [p["pct"] for p in pairs if p["pct"] is not None]
    return {"pct": round(sum(known) / len(known)) if known else None, "pairs": pairs}


def _noise(seed: str, tid: str) -> float:
    h = hashlib.blake2b(f"{seed}|{tid}".encode(), digest_size=4).digest()
    return int.from_bytes(h, "big") / 2**32


def pick_tracks(profiles: list[Profile], artist_of: dict[str, str], size: int = DEFAULT_SIZE,
                sound: dict[str, dict[str, float]] | None = None, seed: str = "") -> list[dict]:
    """[{track_id, for_users, reason, score}] in play order. `artist_of` covers every playable track
    (missing tracks are simply absent). `sound[user][track]` = 0..1 closeness to that user's taste."""
    sound = sound or {}
    members = [p.user_id for p in profiles]
    if len(profiles) < 2:
        return []
    blocked = set().union(*(p.blocked for p in profiles))
    amax = {p.user_id: max(p.artists.values(), default=0.0) or 1.0 for p in profiles}

    def affinity(p: Profile, tid: str) -> float:
        own = p.tracks.get(tid, 0.0)
        if own > 0:
            return own
        art = 0.5 * p.artists.get(artist_of.get(tid, ""), 0.0) / amax[p.user_id]
        return max(art, 0.4 * sound.get(p.user_id, {}).get(tid, 0.0))

    usable = lambda t: t in artist_of and t not in blocked
    candidates = {t for p in profiles for t in p.tracks if usable(t)}
    jitter = {t: 0.05 * _noise(seed, t) for t in candidates} if seed else dict.fromkeys(candidates, 0.0)

    picked: list[dict] = []
    used: set[str] = set()
    per_artist: dict[str, int] = defaultdict(int)

    def take(tid: str, for_users: list[str], reason: str, score: float) -> None:
        picked.append({"track_id": tid, "for_users": for_users, "reason": reason, "score": round(score, 4)})
        used.add(tid)
        per_artist[artist_of[tid]] += 1

    # 1. shared favourites (up to 30 % of the blend)
    shared = []
    for t in candidates:
        fans = sorted((p for p in profiles if p.tracks.get(t, 0) >= SHARED_MIN), key=lambda p: (-p.tracks[t], p.user_id))
        if len(fans) >= 2:
            shared.append((sum(p.tracks[t] for p in fans) / len(profiles) + jitter[t], t, [p.user_id for p in fans]))
    for score, t, fans in sorted(shared, key=lambda x: (-x[0], x[1]))[: max(1, int(size * 0.3))]:
        if per_artist[artist_of[t]] < MAX_PER_ARTIST:
            take(t, fans, "shared", score)

    # 2. round robin, each member's next best track for the group
    ranked: dict[str, list[tuple[float, str]]] = {}
    for p in profiles:
        others = [o for o in profiles if o.user_id != p.user_id]
        lst = []
        for t, w in p.tracks.items():
            if not usable(t):
                continue
            s = w + OTHERS_WEIGHT * sum(affinity(o, t) for o in others) / len(others) + jitter[t]
            lst.append((s, t))
        ranked[p.user_id] = sorted(lst, key=lambda x: (-x[0], x[1]))
    cursor = dict.fromkeys(members, 0)
    while len(picked) < size:
        progressed = False
        for p in sorted(profiles, key=lambda p: (sum(1 for x in picked if x["for_users"][0] == p.user_id), p.user_id)):
            if len(picked) >= size:
                break
            lst, i = ranked[p.user_id], cursor[p.user_id]
            while i < len(lst) and (lst[i][1] in used or per_artist[artist_of[lst[i][1]]] >= MAX_PER_ARTIST):
                i += 1
            cursor[p.user_id] = i + 1
            if i < len(lst):
                s, t = lst[i]
                take(t, [p.user_id], "top" if p.tracks[t] >= 0.5 else "match", s)
                progressed = True
        if not progressed:
            break
    return spread_artists(picked, artist_of)


def spread_artists(items: list[dict], artist_of: dict[str, str]) -> list[dict]:
    """Reorder so the same artist never plays twice in a row where avoidable; otherwise keep the order."""
    pending = list(items)
    out: list[dict] = []
    while pending:
        last = artist_of.get(out[-1]["track_id"]) if out else None
        j = next((k for k, x in enumerate(pending) if artist_of.get(x["track_id"]) != last), 0)
        out.append(pending.pop(j))
    return out


# ---------------------------------------------------------------- data

def load_profile(conn: sqlite3.Connection, user_id: str, now: datetime | None = None) -> Profile:
    now = now or datetime.now(UTC)
    raw: dict[str, float] = defaultdict(float)
    since = iso(datetime.fromtimestamp(now.timestamp() - LOOKBACK_DAYS * 86400, UTC))
    for tid, etype, at, pos, dur in conn.execute(
        "SELECT e.track_id, e.type, e.at, e.position_s, t.duration_s FROM events e JOIN tracks t ON t.id = e.track_id "
        "WHERE e.user_id = ? AND e.type IN ('play', 'skip') AND e.at >= ?", (user_id, since)
    ):
        ts = parse_ts(at)
        age = (now - ts).total_seconds() / 86400 if ts else LOOKBACK_DAYS
        decay = 0.5 ** (max(age, 0) / HALF_LIFE_DAYS)
        if etype == "play":
            comp = 1.0 if not pos or not dur else max(0.3, min(1.0, pos / dur))
            raw[tid] += decay * comp
        else:
            raw[tid] -= 0.3 * decay
    blocked = set()
    for tid, starred, rating, blk in conn.execute(
            "SELECT track_id, starred_at, rating, blocked FROM user_tracks WHERE user_id = ?", (user_id,)):
        if blk:
            blocked.add(tid)
            continue
        raw[tid] += 3.0 * (starred is not None) + (0.5 * (rating - 3) if rating else 0.0)
    for t in blocked:
        raw.pop(t, None)
    p = Profile(user_id, tracks=track_weights(raw), blocked=blocked)
    if p.tracks:
        ids = list(p.tracks)
        meta = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for r in conn.execute(f"SELECT id, artist_id, genre FROM tracks WHERE id IN ({','.join('?' * len(chunk))})",
                                  chunk):
                meta[r[0]] = (r[1], r[2])
        artists: dict[str, float] = defaultdict(float)
        genres: dict[str, float] = defaultdict(float)
        from .wrapped import split_genres

        for t, w in p.tracks.items():
            a, g = meta.get(t, (None, None))
            if a:
                artists[a] += w
            gs = split_genres(g)
            for x in gs:
                genres[x.casefold()] += w / len(gs)
        p.artists, p.genres = dict(artists), dict(genres)
    return p


def _attach_sound(conn: sqlite3.Connection, cfg, profiles: list[Profile]) -> dict[str, dict[str, float]]:
    """Centroids from the analysed library, and each member's closeness to the other members' tracks."""
    try:
        version = active_version(conn, effective_version(cfg.model_version))
        lib = load_library(conn, version)
    except Exception:
        log.exception("blend: library embeddings unavailable")
        return {}
    if not len(lib):
        return {}
    for p in profiles:
        idx = [(lib.index[t], w) for t, w in p.tracks.items() if t in lib.index]
        if not idx:
            continue
        rows, ws = zip(*idx, strict=True)
        c = (lib.emb[list(rows)] * np.asarray(ws, dtype=np.float32)[:, None]).sum(axis=0)
        n = float(np.linalg.norm(c))
        p.centroid = c / n if n > 0 else None
    pool = sorted({t for p in profiles for t in p.tracks if t in lib.index})
    out: dict[str, dict[str, float]] = {}
    if pool:
        m = lib.emb[[lib.index[t] for t in pool]]
        for p in profiles:
            if p.centroid is not None:
                sims = np.clip(m @ p.centroid, 0, 1)
                out[p.user_id] = {t: float(s) for t, s in zip(pool, sims, strict=True)}
    return out


def joined_members(conn: sqlite3.Connection, blend_id: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT m.user_id FROM blend_members m JOIN users u ON u.id = m.user_id "
        "WHERE m.blend_id = ? AND m.status = 'joined' AND u.disabled = 0 ORDER BY m.joined_at, m.user_id",
        (blend_id,))]


def build(conn: sqlite3.Connection, cfg, blend_id: str, size: int = DEFAULT_SIZE, now: datetime | None = None) -> int:
    """Rebuild one blend's tracks and match. Fewer than two joined members empties it. Returns track count."""
    now = now or datetime.now(UTC)
    if conn.execute("SELECT 1 FROM blends WHERE id = ?", (blend_id,)).fetchone() is None:
        raise KeyError(f"unknown blend {blend_id}")
    members = joined_members(conn, blend_id)
    items: list[dict] = []
    match = None
    if len(members) >= 2:
        profiles = [load_profile(conn, u, now) for u in members]
        sound = _attach_sound(conn, cfg, profiles)
        match = group_match(profiles)
        pool = {t for p in profiles for t in p.tracks}
        artist_of = {}
        ids = list(pool)
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for r in conn.execute(f"SELECT id, artist_id FROM tracks WHERE missing = 0 AND id IN "
                                  f"({','.join('?' * len(chunk))})", chunk):
                artist_of[r[0]] = r[1]
        items = pick_tracks(profiles, artist_of, size, sound, seed=f"{blend_id}|{now.date().isoformat()}")
    with transaction(conn):
        conn.execute("DELETE FROM blend_tracks WHERE blend_id = ?", (blend_id,))
        conn.executemany(
            "INSERT INTO blend_tracks (blend_id, position, track_id, for_users, reason, score) VALUES (?, ?, ?, ?, ?, ?)",
            [(blend_id, n, x["track_id"], json.dumps(x["for_users"]), x["reason"], x["score"])
             for n, x in enumerate(items)])
        conn.execute("UPDATE blends SET match = ?, built_at = ? WHERE id = ?",
                     (json.dumps(match) if match else None, iso(now), blend_id))
    return len(items)


def build_all(conn: sqlite3.Connection, cfg, size: int = DEFAULT_SIZE) -> dict[str, int]:
    out = {}
    for (bid,) in conn.execute("SELECT id FROM blends ORDER BY created_at").fetchall():
        try:
            out[bid] = build(conn, cfg, bid, size)
        except Exception:
            log.exception("blend %s failed", bid)
    return out
