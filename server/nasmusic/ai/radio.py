"""Radio (endless per-user queues) and "Because you played" shelves (docs/v3-ux.md §3.2, §5).

Fast path only: no model training per call. A radio call loads the (cached) library, the user's
signals, and the latest taste snapshot; if there is no snapshot yet it builds the cheap cold-start
centres, never the LightGBM model.

A radio session remembers its seed as *anchor tracks* (so the seed's sound is recomputed in
microseconds) and every track it has served (no repeats within a session). Each batch drifts a little
further from the seed toward what the session has recently played, and tracks/artists the user
skipped during the session push similar candidates down.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import timedelta

import numpy as np

from .common import iso, local_tz, new_id, parse_ts, transaction
from .recommender import Recommender, build_cold, build_new_user

log = logging.getLogger("nasmusic.ai.radio")

BATCH = 25
SEED_TYPES = ("random", "track", "artist", "album", "playlist")

RADIO_DDL = """
CREATE TABLE IF NOT EXISTS radio_sessions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    seed_type   TEXT NOT NULL,          -- random | track | artist | album | playlist
    seed_id     TEXT,                   -- NULL for random
    title       TEXT NOT NULL,          -- "<seed> radio", for the queue name
    anchors     TEXT NOT NULL,          -- JSON [track id]: the seed's sound
    served      TEXT NOT NULL,          -- JSON [track id]: everything handed out, in order
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS radio_sessions_user ON radio_sessions(user_id, updated_at);
"""
KEEP_DAYS = 30


def ensure_table(conn: sqlite3.Connection) -> None:
    """The table belongs in server core's schema.sql/migration; created here if it isn't there yet."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'radio_sessions'").fetchone():
        conn.executescript(RADIO_DDL)


@dataclass
class Session:
    id: str
    seed_type: str
    seed_id: str | None
    title: str
    anchors: list[str]
    served: list[str]
    created_at: str


# ---------------------------------------------------------------- shared helpers

def _fast_model(rec: Recommender):
    """Latest snapshot, or cheap cold-start centres. Never trains LightGBM."""
    if rec.model is not None:
        return rec.model
    where, wvals = rec.store.where_owner(rec.user_id)
    row = rec.conn.execute(
        f"SELECT payload FROM taste_snapshots WHERE model_version = ?{where} "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1", (rec.version, *wvals)).fetchone()
    if row is not None:
        from .recommender import TasteModel
        try:
            m = TasteModel.from_payload(row[0])
            if m.centroids.shape[1] in (0, rec.lib.emb.shape[1]):
                rec.model = m
                return m
        except Exception:
            log.exception("could not load taste snapshot; using cold-start centres")
    rec.model = (build_new_user(rec.lib, rec.sig, rec.popularity, rec.settings) if rec.is_new_user
                 else build_cold(rec.lib, rec.sig, rec.settings))
    return rec.model


def _unit(v: np.ndarray) -> np.ndarray:
    return v / max(float(np.linalg.norm(v)), 1e-9)


def _taste_z(rec: Recommender) -> np.ndarray:
    local = rec.now.astimezone(local_tz(rec.tz))
    s = rec.scores(hour=local.hour, weekday=local.weekday())
    fin = np.isfinite(s)
    z = np.full(len(s), -np.inf)
    if fin.any():
        z[fin] = (s[fin] - s[fin].mean()) / (s[fin].std() + 1e-9)
    return z


# ---------------------------------------------------------------- seeds

def _resolve_seed(rec: Recommender, seed: dict, rng: np.random.Generator) -> tuple[str, list[int]]:
    """(title, anchor library indices) for a seed. KeyError if it names nothing analysed."""
    lib = rec.lib
    kind = (seed or {}).get("type", "random")
    sid = (seed or {}).get("id")
    if kind not in SEED_TYPES:
        raise ValueError(f"unknown seed type {kind!r}")
    if kind != "random" and not sid:
        raise ValueError(f"seed type {kind!r} needs an id")
    if kind == "track":
        if sid not in lib.index:
            raise KeyError(f"track {sid} unknown or not analysed yet")
        i = lib.index[sid]
        return f"{lib.title[i]} radio", [i]
    if kind == "artist":
        idx = [i for i, a in enumerate(lib.artist) if a == sid]
        if not idx:
            raise KeyError(f"artist {sid} has no analysed tracks")
        return f"{lib.artist_name[idx[0]]} radio", idx
    if kind == "album":
        idx = [i for i, a in enumerate(lib.album) if a == sid]
        if not idx:
            raise KeyError(f"album {sid} has no analysed tracks")
        row = rec.conn.execute("SELECT title FROM albums WHERE id = ?", (sid,)).fetchone()
        return f"{row[0] if row else lib.title[idx[0]]} radio", idx
    if kind == "playlist":
        where, wvals = rec.store.where_owner(rec.user_id)
        row = rec.conn.execute(f"SELECT name FROM playlists WHERE id = ?{where}", (sid, *wvals)).fetchone()
        if row is None:
            raise KeyError(f"playlist {sid} not found")
        tids = [r[0] for r in rec.conn.execute(
            "SELECT track_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position", (sid,))]
        idx = [lib.index[t] for t in tids if t in lib.index]
        if not idx:
            raise KeyError(f"playlist {sid} has no analysed tracks")
        return f"{row[0]} radio", idx
    # random: one of the user's taste centres (weighted by how much they like it), varied each time
    m = _fast_model(rec)
    allowed = ~rec.sig.blocked
    if m.centroids.shape[0]:
        w = np.clip(m.centroid_mass, 1e-6, None)
        j = int(rng.choice(len(w), p=w / w.sum()))
        centre = _unit(m.centroids[j] + rng.normal(scale=0.15, size=m.centroids.shape[1]) / np.sqrt(
            m.centroids.shape[1]))
        near = np.argsort(-(lib.emb @ centre))
        near = [int(i) for i in near if allowed[i]][:40]
        label = m.centroid_labels[j] if j < len(m.centroid_labels) else None
    else:
        near = [int(i) for i in np.where(allowed)[0]]
        label = None
    if not near:
        raise KeyError("no analysed tracks")
    anchors = [int(i) for i in rng.choice(near, size=min(5, len(near)), replace=False)]
    return (f"Your {label} radio" if label else "Random radio"), anchors


# ---------------------------------------------------------------- picking

def _pick(rec: Recommender, vec: np.ndarray, exclude: set[int], n: int, rng: np.random.Generator,
          skipped: list[int], seed_artists: set[str], first: int | None = None, taste: float = 0.2) -> list[int]:
    """`taste` = how much the user's overall taste counts against closeness to the seed's sound
    (random radio leans on taste more; a track/artist/album/playlist radio stays near its seed)."""
    lib = rec.lib
    sims = lib.emb @ vec
    z = _taste_z(rec)
    val = (0.95 - taste) * sims + taste * np.tanh(z / 2) + rng.gumbel(size=len(lib)) * 0.03
    if skipped:  # this session's skips: steer away from that sound and those artists
        neg = _unit(lib.emb[skipped].mean(axis=0))
        val -= 0.35 * np.clip(lib.emb @ neg, 0, None)
        counts: dict[str, int] = {}
        for i in skipped:
            counts[lib.artist[i]] = counts.get(lib.artist[i], 0) + 1
        val -= np.array([0.3 * min(counts.get(a, 0), 2) for a in lib.artist])
    val[~np.isfinite(z)] = -np.inf          # blocked
    if exclude:
        val[list(exclude)] = -np.inf
    order = [int(i) for i in np.argsort(-val) if np.isfinite(val[i])]
    # candidate pool with per-artist caps (the seed's own artist may appear more often)
    pool, per = [], {}
    for i in order:
        a = lib.artist[i]
        cap = 6 if a in seed_artists else 2
        if per.get(a, 0) >= cap:
            continue
        per[a] = per.get(a, 0) + 1
        pool.append(i)
        if len(pool) >= 3 * n:
            break
    # smooth walk: next = best of (value + closeness to the previous track), no artist within 3 tracks
    out: list[int] = []
    if first is not None and first not in exclude:
        out.append(first)
        pool = [i for i in pool if i != first]
    while pool and len(out) < n:
        recent = {lib.artist[j] for j in out[-3:]}
        cands = [i for i in pool if lib.artist[i] not in recent] or pool
        if out:
            prev = lib.emb[out[-1]]
            best = max(cands, key=lambda i: val[i] + 0.3 * float(lib.emb[i] @ prev))
        else:
            best = cands[0]
        out.append(best)
        pool.remove(best)
    return out


def _session_feedback(rec: Recommender, sess: Session) -> tuple[list[int], list[int]]:
    """(skipped, played-through) library indices among this session's tracks since it started."""
    since = parse_ts(sess.created_at)
    served = set(sess.served)
    skipped, liked = [], []
    for ts, _ctx, i, etype, _label in rec.sig.events:
        if since is not None and ts < since - timedelta(minutes=1):
            continue
        tid = rec.lib.ids[i]
        if tid not in served:
            continue
        if etype == "skip":
            skipped.append(i)
        elif etype == "play":
            liked.append(i)
    return skipped, liked


# ---------------------------------------------------------------- persistence

def _load(conn: sqlite3.Connection, rec: Recommender, radio_id: str) -> Session:
    where, wvals = rec.store.where_owner(rec.user_id)
    row = conn.execute(f"SELECT id, seed_type, seed_id, title, anchors, served, created_at FROM radio_sessions "
                       f"WHERE id = ?{where}", (radio_id, *wvals)).fetchone()
    if row is None:
        raise KeyError(f"radio {radio_id} not found")
    return Session(row[0], row[1], row[2], row[3], json.loads(row[4]), json.loads(row[5]), row[6])


def _save_served(conn: sqlite3.Connection, radio_id: str, served: list[str]) -> None:
    with transaction(conn):
        conn.execute("UPDATE radio_sessions SET served = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(served), iso(), radio_id))


# ---------------------------------------------------------------- public

def start(conn: sqlite3.Connection, cfg, user_id: str | None, seed: dict, n: int = BATCH) -> tuple[str, list[str]]:
    ensure_table(conn)
    rec = Recommender(conn, cfg, user_id)
    if not len(rec.lib):
        raise KeyError("no analysed tracks yet")
    _fast_model(rec)
    rng = np.random.default_rng()  # unseeded: a random radio differs every time
    title, anchors = _resolve_seed(rec, seed, rng)
    vec = _unit(rec.lib.emb[anchors].mean(axis=0))
    seed_artists = {rec.lib.artist[i] for i in anchors} if seed.get("type", "random") != "random" else set()
    first = anchors[0] if seed.get("type") == "track" and not rec.sig.blocked[anchors[0]] else None
    taste = 0.3 if seed.get("type", "random") == "random" else 0.2
    picked = _pick(rec, vec, set(), n, rng, [], seed_artists, first, taste)
    ids = [rec.lib.ids[i] for i in picked]
    rid = new_id()
    cols, marks, vals = rec.store.owner(user_id)
    now = iso()
    with transaction(conn):
        conn.execute("DELETE FROM radio_sessions WHERE updated_at < ?",
                     (iso(rec.now - timedelta(days=KEEP_DAYS)),))
        conn.execute(
            f"INSERT INTO radio_sessions (id, seed_type, seed_id, title, anchors, served, created_at, updated_at{cols}) "
            f"VALUES (?,?,?,?,?,?,?,?{marks})",
            (rid, seed.get("type", "random"), seed.get("id"), title,
             json.dumps([rec.lib.ids[i] for i in anchors]), json.dumps(ids), now, now, *vals))
    return rid, ids


def more(conn: sqlite3.Connection, cfg, user_id: str | None, radio_id: str, exclude_ids: list[str],
         n: int = BATCH) -> list[str]:
    ensure_table(conn)
    rec = Recommender(conn, cfg, user_id)
    sess = _load(conn, rec, radio_id)
    if not len(rec.lib):
        return []
    _fast_model(rec)
    lib = rec.lib
    anchors = [lib.index[t] for t in sess.anchors if t in lib.index]
    seed_vec = _unit(lib.emb[anchors].mean(axis=0)) if anchors else _unit(lib.emb.mean(axis=0))
    skipped, liked = _session_feedback(rec, sess)
    # drift: each batch moves a little further toward what this session recently played (or served)
    batch_no = max(len(sess.served) // BATCH, 1)
    alpha = min(0.15 * batch_no, 0.5)
    recent = liked[-10:] or [lib.index[t] for t in sess.served[-10:] if t in lib.index]
    recent = [i for i in recent if i not in set(skipped)]
    vec = _unit((1 - alpha) * seed_vec + alpha * _unit(lib.emb[recent].mean(axis=0))) if recent else seed_vec
    exclude = {lib.index[t] for t in set(sess.served) | set(exclude_ids or []) if t in lib.index}
    seed_artists = {lib.artist[i] for i in anchors} if sess.seed_type != "random" else set()
    rng = np.random.default_rng()
    taste = 0.3 if sess.seed_type == "random" else 0.2
    picked = _pick(rec, vec, exclude, max(int(n), 0), rng, skipped, seed_artists, taste=taste)
    ids = [lib.ids[i] for i in picked]
    _save_served(conn, radio_id, sess.served + ids)
    return ids


def because_you_played(conn: sqlite3.Connection, cfg, user_id: str | None, limit_shelves: int = 3,
                       per_shelf: int = 15, days: float = 30) -> list[dict]:
    """Shelves of similar library tracks for the user's recent favourite plays (own data only)."""
    rec = Recommender(conn, cfg, user_id)
    lib, sig = rec.lib, rec.sig
    if not len(lib) or limit_shelves <= 0:
        return []
    since = rec.now - timedelta(days=days)
    seeds: list[int] = []
    seen_artists: set[str] = set()
    for ts, _ctx, i, etype, _label in reversed(sig.events):  # most recent first
        if ts < since:
            break
        if etype != "play" or sig.blocked[i] or i in seeds or lib.artist[i] in seen_artists:
            continue
        favourite = sig.starred[i] or sig.rating[i] >= 4 or sig.weight[i] >= 1.5
        if not favourite:
            continue
        seeds.append(i)
        seen_artists.add(lib.artist[i])
        if len(seeds) >= limit_shelves:
            break
    shelves, used = [], set(seeds)
    for s in seeds:
        sims = lib.emb @ lib.emb[s]
        sims[sig.blocked] = -np.inf
        sims[[j for j, a in enumerate(lib.album) if a == lib.album[s]]] = -np.inf
        tracks, per = [], {}
        for j in np.argsort(-sims):
            j = int(j)
            if not np.isfinite(sims[j]) or len(tracks) >= per_shelf:
                break
            a = lib.artist[j]
            if j in used or per.get(a, 0) >= 2:
                continue
            per[a] = per.get(a, 0) + 1
            tracks.append(j)
            used.add(j)
        if tracks:
            shelves.append({"seed_track_id": lib.ids[s], "title": f"Because you played {lib.title[s]}",
                            "track_ids": [lib.ids[j] for j in tracks]})
    return shelves

