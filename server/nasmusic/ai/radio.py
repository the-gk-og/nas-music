"""Radio (endless per-user queues) and "Because you played" shelves (docs/v3-ux.md §3.2, §5).

Fast path only: no model training per call. A radio call loads the (cached) library, the user's
signals, and the latest taste snapshot; if there is no snapshot yet it builds the cheap cold-start
centres, never the LightGBM model.

A radio session remembers its seed as *anchor tracks* (so the seed's sound is recomputed in
microseconds) and every track it has served (no repeats within a session). Each batch drifts a little
further from the seed toward what the session has recently played, and tracks/artists the user
skipped during the session push similar candidates down.

AI radio (schema v6): two more seed types, both endless and steerable like the others.
* `moment` ("Play something for now", smart shuffle): scored for this moment by `moment.py` (time of day,
  day of week, device, last hour's plays and skips, freshness, rediscoveries); varied like a shuffle.
* `vibe` (describe-a-vibe): text -> constraint chips (`vibe.py`), filtered and scored on the feature space.
Steering (`steering.py`) is per station (`radio_stations`) and applies to every following pick; steers are
logged in `radio_feedback` for the recommender.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import timedelta

import numpy as np

from . import moment, steering, vibe
from .common import iso, local_tz, new_id, parse_ts, transaction
from .recommender import Recommender, build_cold, build_new_user

log = logging.getLogger("nasmusic.ai.radio")

BATCH = 25
SEED_TYPES = ("random", "track", "artist", "album", "playlist", "smart_playlist", "moment", "vibe")
AI_TYPES = ("moment", "vibe")

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
V6_DDL = """
CREATE TABLE IF NOT EXISTS radio_stations (
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    station_key TEXT NOT NULL,
    title       TEXT NOT NULL,
    steering    TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, station_key)
);
CREATE TABLE IF NOT EXISTS radio_feedback (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    station_key TEXT NOT NULL,
    track_id    TEXT NOT NULL,
    action      TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT 'track',
    at          TEXT NOT NULL,
    context     TEXT
);
CREATE INDEX IF NOT EXISTS radio_feedback_user ON radio_feedback(user_id, at);
"""
KEEP_DAYS = 30


def ensure_table(conn: sqlite3.Connection) -> None:
    """The tables belong in server core's schema.sql/migration (v6); created here if they aren't there yet
    (AI tests run on a hand-built schema)."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'radio_sessions'").fetchone():
        conn.executescript(RADIO_DDL)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(radio_sessions)")}
    if "station_key" not in cols:
        conn.execute("ALTER TABLE radio_sessions ADD COLUMN station_key TEXT")
    if "params" not in cols:
        conn.execute("ALTER TABLE radio_sessions ADD COLUMN params TEXT")
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'radio_feedback'").fetchone():
        conn.executescript(V6_DDL)


@dataclass
class Session:
    id: str
    seed_type: str
    seed_id: str | None
    title: str
    anchors: list[str]
    served: list[str]
    created_at: str
    station_key: str = ""
    params: dict | None = None


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
    if kind == "smart_playlist":
        # v7: the server keeps the evaluated list in smart_playlist_tracks (refreshed before radio starts).
        row = rec.conn.execute("SELECT name FROM smart_playlists WHERE id = ? AND user_id = ?",
                               (sid, rec.user_id)).fetchone()
        if row is None:
            raise KeyError(f"smart playlist {sid} not found")
        tids = [r[0] for r in rec.conn.execute(
            "SELECT track_id FROM smart_playlist_tracks WHERE smart_playlist_id = ? ORDER BY position", (sid,))]
        idx = [lib.index[t] for t in tids if t in lib.index]
        if not idx:
            raise KeyError(f"smart playlist {sid} has no analysed tracks")
        return f"{row[0]} radio", idx
    # random: one of the user's taste centres (weighted by how much they like it), varied each time
    m = _fast_model(rec)
    allowed = ~rec.sig.excluded
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
          skipped: list[int], seed_artists: set[str], first: int | None = None, taste: float = 0.2,
          eff: steering.Effect | None = None, history: list[int] | None = None) -> list[int]:
    """`taste` = how much the user's overall taste counts against closeness to the seed's sound
    (random radio leans on taste more; a track/artist/album/playlist radio stays near its seed).
    `eff` = this station's steering; `history` = the session's last served tracks (artist spacing
    carries across batches)."""
    lib = rec.lib
    sims = lib.emb @ vec
    z = _taste_z(rec)
    sw = eff.seed_weight if eff else 1.0
    val = (0.95 - taste) * sw * sims + taste * np.tanh(z / 2) + rng.gumbel(size=len(lib)) * 0.03 * (
        eff.noise if eff else 1.0)
    if eff is not None:
        val += eff.bias
        val[eff.banned] = -np.inf
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
    hist = list(history or [])[-6:]
    gap = 3 + (eff.gap_extra if eff else 0)
    if first is not None and first not in exclude:
        out.append(first)
        pool = [i for i in pool if i != first]
    while pool and len(out) < n:
        recent = {lib.artist[j] for j in (hist + out)[-gap:]}
        cands = [i for i in pool if lib.artist[i] not in recent] or pool
        if out or hist:
            prev = lib.emb[(hist + out)[-1]]
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


# ---------------------------------------------------------------- variety picker (moment / vibe)

def _pick_varied(lib, val: np.ndarray, exclude: set[int], n: int, rng: np.random.Generator,
                 history: list[int], gap: int = 4, noise: float = 0.08, per_artist: int = 2,
                 discovery: np.ndarray | None = None, discovery_every: int = 0) -> list[int]:
    """Shuffle-like: sample from the best candidates (softmax over the top few, so it varies but stays good),
    never the same artist within `gap` tracks (counting the session's previous batch) or the same album
    back to back, at most `per_artist` per batch, and every `discovery_every`-th slot a rediscovery."""
    val = val + rng.gumbel(size=len(val)) * noise
    if exclude:
        val[list(exclude)] = -np.inf
    order = [int(i) for i in np.argsort(-val) if np.isfinite(val[i])]
    pool = order[:max(8 * n, 200)]
    disc = [i for i in order if discovery is not None and discovery[i]][:max(4 * n, 60)] if discovery_every else []
    hist = list(history)[-max(gap, 1):]
    out: list[int] = []
    per: dict[str, int] = {}
    used: set[int] = set()

    def ok(i: int, relax: int) -> bool:
        if i in used:
            return False
        recent = hist + out
        g = max(gap - relax, 1)
        if lib.artist[i] in {lib.artist[j] for j in recent[-g:]}:
            return False
        if relax < 2 and recent and lib.album[i] == lib.album[recent[-1]]:
            return False
        return relax >= 1 or per.get(lib.artist[i], 0) < per_artist

    while len(out) < n:
        slot_disc = discovery_every and len(out) % discovery_every == discovery_every - 1
        pick = None
        for relax in range(4):
            source = disc if slot_disc and relax == 0 else pool
            cands = [i for i in source if ok(i, relax)][:6]
            if cands:
                v = np.array([val[i] for i in cands])
                p = np.exp((v - v.max()) / 0.08)
                pick = int(rng.choice(cands, p=p / p.sum()))
                break
        if pick is None:
            break
        out.append(pick)
        used.add(pick)
        per[lib.artist[pick]] = per.get(lib.artist[pick], 0) + 1
    return out


# ---------------------------------------------------------------- stations and steering

def _seed_station_key(seed: dict) -> str:
    kind = seed.get("type", "random")
    return "random" if kind == "random" else f"{kind}:{seed.get('id')}"


def _load_steering(conn: sqlite3.Connection, user_id: str | None, key: str) -> steering.Steering:
    if not key:
        return steering.Steering()
    row = conn.execute("SELECT steering FROM radio_stations WHERE user_id IS ? AND station_key = ?",
                       (user_id, key)).fetchone()
    return steering.Steering.from_json(row[0] if row else None)


def _save_station(conn: sqlite3.Connection, user_id: str | None, key: str, title: str,
                  st: steering.Steering | None = None) -> None:
    now = iso()
    if st is None:
        conn.execute("INSERT INTO radio_stations (user_id, station_key, title, steering, created_at, updated_at) "
                     "VALUES (?,?,?,?,?,?) ON CONFLICT(user_id, station_key) DO UPDATE SET title = excluded.title, "
                     "updated_at = excluded.updated_at", (user_id, key, title, "{}", now, now))
    else:
        conn.execute("INSERT INTO radio_stations (user_id, station_key, title, steering, created_at, updated_at) "
                     "VALUES (?,?,?,?,?,?) ON CONFLICT(user_id, station_key) DO UPDATE SET "
                     "steering = excluded.steering, updated_at = excluded.updated_at",
                     (user_id, key, title, st.to_json(), now, now))


def _effect(rec: Recommender, st: steering.Steering) -> steering.Effect:
    return steering.effect(st, rec.lib, rec.sig.weight, rec.sig.plays)


# ---------------------------------------------------------------- persistence

def _load(conn: sqlite3.Connection, rec: Recommender, radio_id: str) -> Session:
    where, wvals = rec.store.where_owner(rec.user_id)
    row = conn.execute(f"SELECT id, seed_type, seed_id, title, anchors, served, created_at, station_key, params "
                       f"FROM radio_sessions WHERE id = ?{where}", (radio_id, *wvals)).fetchone()
    if row is None:
        raise KeyError(f"radio {radio_id} not found")
    try:
        params = json.loads(row[8]) if row[8] else {}
    except (TypeError, ValueError):
        params = {}
    return Session(row[0], row[1], row[2], row[3], json.loads(row[4]), json.loads(row[5]), row[6],
                   row[7] or _seed_station_key({"type": row[1], "id": row[2]}), params if isinstance(params, dict) else {})


def _save_served(conn: sqlite3.Connection, radio_id: str, served: list[str], params: dict | None = None) -> None:
    with transaction(conn):
        if params is None:
            conn.execute("UPDATE radio_sessions SET served = ?, updated_at = ? WHERE id = ?",
                         (json.dumps(served), iso(), radio_id))
        else:
            conn.execute("UPDATE radio_sessions SET served = ?, params = ?, updated_at = ? WHERE id = ?",
                         (json.dumps(served), json.dumps(params), iso(), radio_id))


# ---------------------------------------------------------------- moment / vibe picking

def _ai_pick(rec: Recommender, kind: str, params: dict, st: steering.Steering, exclude: set[int],
             history: list[int], n: int, rng: np.random.Generator) -> tuple[list[int], dict]:
    """Picks for a moment or vibe station, and the (updated) params with the explanation."""
    lib = rec.lib
    eff = _effect(rec, st)
    if kind == "moment":
        m = moment.MomentContext(**{k: params["context"][k] for k in ("hour", "weekday", "device", "motion", "workout")})
        ms = moment.score(rec, m, eff.energy_shift)
        val = ms.value + eff.bias
        never = eff.banned | rec.sig.excluded   # thumbs-down and family-mode-hidden tracks
        val[never] = -np.inf
        picked = _pick_varied(lib, val, exclude, n, rng, history, gap=4 + eff.gap_extra, noise=0.08 * eff.noise,
                              per_artist=2, discovery=ms.discovery & ~never,
                              discovery_every=max(3, 6 - 2 * max(st.variety, 0)) if st.variety >= 0 else 0)
        params = {**params, "explanation": ms.explanation}
    else:
        chips = params.get("constraints") or []
        years = vibe.track_years(rec.conn, lib)
        z = _taste_z(rec)
        avail = np.isfinite(z) & ~eff.banned
        if exclude:
            avail[list(exclude)] = False
        vs = vibe.score(lib, chips, years, rec.sig.weight, rec.sig.plays, min_pool=max(2 * n, 40),
                        energy_shift=eff.energy_shift, available=avail)
        val = vs.soft + 0.35 * np.tanh(z / 2) + eff.bias
        val[~vs.mask | ~np.isfinite(z) | eff.banned] = -np.inf
        # coherence: lean toward the sound of the best matches, so a vibe stays one vibe
        fin = np.where(np.isfinite(val))[0]
        if len(fin) >= 5:
            top = fin[np.argsort(-val[fin])[:30]]
            val = val + 0.2 * eff.seed_weight * (lib.emb @ _unit(lib.emb[top].mean(axis=0)))
        picked = _pick_varied(lib, val, exclude, n, rng, history, gap=3 + eff.gap_extra, noise=0.06 * eff.noise,
                              per_artist=3)
        bits = [vibe.describe(chips)]
        if vs.relaxed:
            bits.append("loosened " + ", ".join(vs.relaxed) + " to keep it going")
        if vs.unsupported:
            bits.append("can't judge " + ", ".join(vs.unsupported) + " without full analysis")
        params = {**params, "explanation": "Vibe: " + "; ".join(bits) + "."}
    if not st.is_neutral:
        params["explanation"] = params["explanation"].rstrip(".") + f". Steered: {st.summary()}."
    return picked, params


# ---------------------------------------------------------------- public

def _resolve_ai_seed(rec: Recommender, seed: dict, context: dict | None, device_kind: str | None
                     ) -> tuple[str, str, dict]:
    """(title, station key, params) for a moment or vibe seed."""
    if seed["type"] == "moment":
        m = moment.resolve(rec.now, rec.tz, context, device_kind)
        return "Smart shuffle", m.key, {"context": {"hour": m.hour, "weekday": m.weekday, "device": m.device,
                                                     "motion": m.motion, "workout": m.workout}}
    text = str(seed.get("text") or "").strip()[:vibe.MAX_TEXT]
    if seed.get("constraints") is not None:
        chips = vibe.sanitize(seed["constraints"])
    else:
        chips = vibe.parse(text, vibe.vocab_from_library(rec.lib))
    if not chips and not text:
        raise ValueError("describe a vibe (text or constraints)")
    name = text[:1].upper() + text[1:] if text else vibe.describe(chips)
    return f"{name[:60]} radio", vibe.station_key(chips), {"text": text, "constraints": chips}


def start(conn: sqlite3.Connection, cfg, user_id: str | None, seed: dict, n: int = BATCH,
          context: dict | None = None, device_kind: str | None = None) -> tuple[str, list[str]]:
    ensure_table(conn)
    rec = Recommender(conn, cfg, user_id)
    if not len(rec.lib):
        raise KeyError("no analysed tracks yet")
    _fast_model(rec)
    rng = np.random.default_rng()  # unseeded: a random radio differs every time
    kind = seed.get("type", "random")
    if kind not in SEED_TYPES:
        raise ValueError(f"unknown seed type {kind!r}")
    params: dict | None = None
    if kind in AI_TYPES:
        title, key, params = _resolve_ai_seed(rec, seed, context, device_kind)
        st = _load_steering(conn, user_id, key)
        picked, params = _ai_pick(rec, kind, params, st, set(), [], n, rng)
        anchors = picked[:5]
    else:
        title, anchors = _resolve_seed(rec, seed, rng)
        key = _seed_station_key(seed)
        st = _load_steering(conn, user_id, key)
        vec = _unit(rec.lib.emb[anchors].mean(axis=0))
        seed_artists = {rec.lib.artist[i] for i in anchors} if kind != "random" else set()
        first = anchors[0] if kind == "track" and not rec.sig.excluded[anchors[0]] else None
        eff = _effect(rec, st)
        if first is not None and eff.banned[first]:
            first = None
        taste = 0.3 if kind == "random" else 0.2
        picked = _pick(rec, vec, set(), n, rng, [], seed_artists, first, taste, eff)
        if not st.is_neutral:
            params = {"explanation": f"Steered: {st.summary()}."}
    if not picked:
        raise KeyError("nothing matches this radio")
    ids = [rec.lib.ids[i] for i in picked]
    rid = new_id()
    cols, marks, vals = rec.store.owner(user_id)
    now = iso()
    with transaction(conn):
        conn.execute("DELETE FROM radio_sessions WHERE updated_at < ?",
                     (iso(rec.now - timedelta(days=KEEP_DAYS)),))
        conn.execute(
            f"INSERT INTO radio_sessions (id, seed_type, seed_id, title, anchors, served, created_at, updated_at, "
            f"station_key, params{cols}) VALUES (?,?,?,?,?,?,?,?,?,?{marks})",
            (rid, kind, seed.get("id") if kind not in AI_TYPES else None, title,
             json.dumps([rec.lib.ids[i] for i in anchors]), json.dumps(ids), now, now, key,
             json.dumps(params) if params is not None else None, *vals))
        _save_station(conn, user_id, key, title)
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
    st = _load_steering(conn, user_id, sess.station_key)
    exclude = {lib.index[t] for t in set(sess.served) | set(exclude_ids or []) if t in lib.index}
    history = [lib.index[t] for t in sess.served[-8:] if t in lib.index]
    rng = np.random.default_rng()
    n = max(int(n), 0)
    params = None
    if sess.seed_type in AI_TYPES:
        picked, params = _ai_pick(rec, sess.seed_type, sess.params or {}, st, exclude, history, n, rng)
    else:
        anchors = [lib.index[t] for t in sess.anchors if t in lib.index]
        seed_vec = _unit(lib.emb[anchors].mean(axis=0)) if anchors else _unit(lib.emb.mean(axis=0))
        skipped, liked = _session_feedback(rec, sess)
        # drift: each batch moves a little further toward what this session recently played (or served)
        batch_no = max(len(sess.served) // BATCH, 1)
        alpha = min(0.15 * batch_no, 0.5)
        recent = liked[-10:] or [lib.index[t] for t in sess.served[-10:] if t in lib.index]
        recent = [i for i in recent if i not in set(skipped)]
        vec = _unit((1 - alpha) * seed_vec + alpha * _unit(lib.emb[recent].mean(axis=0))) if recent else seed_vec
        seed_artists = {lib.artist[i] for i in anchors} if sess.seed_type != "random" else set()
        taste = 0.3 if sess.seed_type == "random" else 0.2
        picked = _pick(rec, vec, exclude, n, rng, skipped, seed_artists, taste=taste, eff=_effect(rec, st),
                       history=history)
        params = {k: v for k, v in (sess.params or {}).items() if k != "explanation"}
        if not st.is_neutral:
            params["explanation"] = f"Steered: {st.summary()}."
    ids = [lib.ids[i] for i in picked]
    _save_served(conn, radio_id, sess.served + ids, params)
    return ids


def refill(conn: sqlite3.Connection, cfg, user_id: str | None, radio_id: str, drop_ids: list[str],
           keep_ids: list[str], n: int = BATCH) -> list[str]:
    """Replace a radio's upcoming tracks after steering: `drop_ids` (queued, not played) go back into the pool,
    then `n` fresh picks that avoid `keep_ids` (what stays in the queue)."""
    ensure_table(conn)
    rec = Recommender(conn, cfg, user_id)
    sess = _load(conn, rec, radio_id)
    drop = set(drop_ids or []) - set(keep_ids or [])
    served = [t for t in sess.served if t not in drop]
    _save_served(conn, radio_id, served)
    return more(conn, cfg, user_id, radio_id, list(keep_ids or []), n)


def steer(conn: sqlite3.Connection, cfg, user_id: str | None, radio_id: str, action: str,
          track_id: str | None = None, scope: str = "track", ref_id: str | None = None,
          context: dict | None = None) -> dict:
    """Apply one steering action to the radio's station and log it for the recommender. Returns station()."""
    ensure_table(conn)
    from .store import Store

    store = Store(conn)
    where, wvals = store.where_owner(user_id)
    row = conn.execute(f"SELECT seed_type, seed_id, station_key, title FROM radio_sessions WHERE id = ?{where}",
                       (radio_id, *wvals)).fetchone()
    if row is None:
        raise KeyError(f"radio {radio_id} not found")
    key = row[2] or _seed_station_key({"type": row[0], "id": row[1]})
    if action not in steering.ACTIONS:
        raise ValueError(f"unknown steering action {action!r}")
    if scope not in ("track", "artist"):
        raise ValueError("scope must be track or artist")
    ref = None
    if action in ("more_like", "less_like", "never_here"):
        if not track_id:
            raise ValueError(f"{action} needs trackId")
        t = conn.execute("SELECT t.id, t.title, t.artist_id, a.name FROM tracks t LEFT JOIN artists a "
                         "ON a.id = t.artist_id WHERE t.id = ?", (track_id,)).fetchone()
        if t is None:
            raise KeyError(f"track {track_id} not found")
        ref = ({"type": "track", "id": t[0], "name": t[1]} if scope == "track"
               else {"type": "artist", "id": t[2], "name": t[3]})
    elif action == "remove":
        if not ref_id:
            raise ValueError("remove needs the id of the entry")
        ref = {"type": scope, "id": ref_id}
    st = _load_steering(conn, user_id, key).apply(action, ref)
    now = iso()
    with transaction(conn):
        _save_station(conn, user_id, key, row[3], st)
        if action in steering.FEEDBACK_WEIGHT and track_id:
            cols, marks, vals = store.owner(user_id)
            conn.execute(
                f"INSERT INTO radio_feedback (id, station_key, track_id, action, scope, at, context{cols}) "
                f"VALUES (?,?,?,?,?,?,?{marks})",
                (new_id(), key, track_id, action, scope, now, json.dumps(context) if context else None, *vals))
    return station(conn, cfg, user_id, radio_id)


def station(conn: sqlite3.Connection, cfg, user_id: str | None, radio_id: str) -> dict:
    """{radioId, kind, title, stationKey, explanation, text, constraints, context, steering, steeringSummary}."""
    ensure_table(conn)
    from .store import Store

    store = Store(conn)
    where, wvals = store.where_owner(user_id)
    row = conn.execute(f"SELECT seed_type, seed_id, station_key, title, params FROM radio_sessions WHERE id = ?{where}",
                       (radio_id, *wvals)).fetchone()
    if row is None:
        raise KeyError(f"radio {radio_id} not found")
    key = row[2] or _seed_station_key({"type": row[0], "id": row[1]})
    try:
        params = json.loads(row[4]) if row[4] else {}
    except (TypeError, ValueError):
        params = {}
    st = _load_steering(conn, user_id, key)
    ctx = params.get("context")
    return {"radioId": radio_id, "kind": row[0], "seedId": row[1], "title": row[3], "stationKey": key,
            "explanation": params.get("explanation"), "text": params.get("text"),
            "constraints": params.get("constraints"),
            "context": moment.MomentContext(**ctx).as_dict() if isinstance(ctx, dict) else None,
            "steering": st.as_dict(), "steeringSummary": st.summary() or None}


def parse_vibe(conn: sqlite3.Connection, cfg, user_id: str | None, text: str) -> dict:
    """{text, constraints, title} for the vibe box; nothing is stored."""
    from .common import active_version, load_library
    from .extractors import effective_version

    text = (text or "").strip()[:vibe.MAX_TEXT]
    try:
        lib = load_library(conn, active_version(conn, effective_version(cfg.model_version)))
        voc = vibe.vocab_from_library(lib)
    except Exception:
        log.exception("vibe vocabulary unavailable; parsing with the lexicon only")
        voc = None
    chips = vibe.parse(text, voc)
    return {"text": text, "constraints": chips, "title": (text[:1].upper() + text[1:]) if text else vibe.describe(chips)}


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
        if etype != "play" or sig.excluded[i] or i in seeds or lib.artist[i] in seen_artists:
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
        sims[sig.excluded] = -np.inf
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

