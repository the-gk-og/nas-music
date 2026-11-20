"""Per-user taste model and track scoring.

Everything here is computed for **one user** from that user's `events` and `user_tracks` only
(`store.Store`). The single cross-user input is an anonymous per-track play count summed over other
users, used as a cold-start prior for new users.

Signals:

| signal                         | weight                                                  |
| ------------------------------ | ------------------------------------------------------- |
| full play (>= 80 % listened)   | +1.0  (mild like); 50-80 % = +0.5; shorter = +0.1       |
| skip after 30 s                | -0.6  (mild dislike)                                    |
| skip before 30 s               | -1.5  (strong)                                          |
| repeated skip (30 days)        | each further skip x1.5, x2.0 ... (capped at -3 / event) |
| repeat (context.repeatOfPrevious) | +1.5 (strong like)                                   |
| seeking through (context.seeks)   | -0.5 (mild dislike); seeking back to re-hear +0.3    |
| volume (context.volumeChanges)    | turned up +0.2; turned well down -0.2                |
| thumbs-up (latest thumb event) | +2.0                                                    |
| star (user_tracks.starred_at)  | +3.0  (strong like)                                     |
| rating r (user_tracks.rating)  | (r - 3) x 1.0                                           |
| thumbs-down (latest thumb)     | excluded from every mix                                 |

Thumbs: per track the latest thumb_up / thumb_down / thumb_clear event wins (thumb_clear cancels the
earlier thumb). Tracks with no thumb events fall back to `user_tracks.blocked`.

Scoring:
* New user (< `ai.rec.new_user_plays` plays): library-wide prior = popularity across other users
  (anonymous counts), recently added, and sound diversity (popularity-weighted k-means centres over
  the whole library, so mixes cover the library's range of sounds).
* Afterwards, cold start: weighted spherical k-means over the user's positively weighted tracks,
  negatives from disliked ones.
* Learned (>= `ai.rec.learned_min_events` of the user's feedback events, validation AUC >= 0.55):
  LightGBM play-through classifier on embedding PCA, tags, energy/BPM, star/rating, hour, weekday,
  days since last play, play/skip counts, and optional context (output, motion, place, network,
  weather, temperature; missing = unknown).
* Freshness: recently played tracks are pushed down; never-played tracks get a small boost.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from . import context as cx
from .common import (
    Library,
    Settings,
    active_version,
    iso,
    load_library,
    load_settings,
    local_tz,
    new_id,
    now_utc,
    parse_ts,
    top_tag,
    transaction,
)
from .extractors import effective_version
from .store import Store

log = logging.getLogger("nasmusic.ai.recommender")

FEEDBACK_TYPES = ("play", "skip", "star", "unstar", "rate", "thumb_up", "thumb_down", "thumb_clear")
THUMB_TYPES = ("thumb_up", "thumb_down", "thumb_clear")
N_PCA = 16
N_TAGS = 24
N_VOCAB = 16
MIN_AUC = 0.55
RADIO_FEEDBACK_WEIGHT = {"more_like": 1.0, "less_like": -0.8, "never_here": -1.0}  # = steering.FEEDBACK_WEIGHT


# ---------------------------------------------------------------- signals

@dataclass
class Signals:
    weight: np.ndarray            # per library track, decayed
    plays: np.ndarray             # full-ish plays (>= 50 %)
    skips: np.ndarray
    last_played_days: np.ndarray  # inf when never played
    blocked: np.ndarray           # bool
    starred: np.ndarray           # bool
    rating: np.ndarray            # 0 when unrated
    ctx_weight: dict[str, np.ndarray]   # context key ('morning', 'workout', 'place:work' ...) -> weight
    ctx_plays: dict[str, int]           # context key -> number of plays in it
    n_feedback: int
    n_plays: int                  # all of the user's plays (any length), for the new-user threshold
    events: list[tuple]           # (datetime, Context, lib index, type, label or None)
    hidden: np.ndarray | None = None   # bool: hidden from this user by family mode (nasmusic.db.family)

    @property
    def excluded(self) -> np.ndarray:
        """Never pick these: thumbs-down OR hidden by family mode. Training still uses `blocked` only (a
        family-mode track isn't a dislike)."""
        if self.hidden is None:
            return self.blocked
        return self.blocked | self.hidden


def play_completion(position_s: float | None, duration_s: float | None) -> float:
    if position_s is None or not duration_s:
        return 1.0
    return float(np.clip(position_s / duration_s, 0, 1))


def event_weight(etype: str, position_s: float | None, duration_s: float | None, prior_skips: int,
                 skip_early_s: float = 30.0) -> float:
    if etype == "play":
        c = play_completion(position_s, duration_s)
        return 1.0 if c >= 0.8 else 0.5 if c >= 0.5 else 0.1
    if etype == "skip":
        base = -1.5 if (position_s is not None and position_s < skip_early_s) else -0.6
        return max(base * (1 + 0.5 * prior_skips), -3.0)
    if etype == "thumb_up":
        return 2.0
    return 0.0


def collect_signals(store: Store, user_id: str | None, lib: Library, settings: Settings, tz_name: str,
                    now: datetime | None = None, disabled: set[str] = frozenset()) -> Signals:
    now = now or now_utc()
    tz = local_tz(tz_name)
    n = len(lib)
    weight, plays, skips = np.zeros(n), np.zeros(n), np.zeros(n)
    last = np.full(n, np.inf)
    blocked, starred, rating = np.zeros(n, bool), np.zeros(n, bool), np.zeros(n)
    for tid, ut in store.user_tracks(user_id).items():
        i = lib.index.get(tid)
        if i is None:
            continue
        blocked[i], starred[i], rating[i] = ut.blocked, ut.starred, ut.rating
        lp = parse_ts(ut.last_played)
        if lp is not None:
            last[i] = max((now - lp).total_seconds() / 86400, 0.0)

    ctx_weight: dict[str, np.ndarray] = defaultdict(lambda: np.zeros(n))
    ctx_plays: dict[str, int] = defaultdict(int)
    half_life = max(settings.num("ai.rec.half_life_days"), 1.0)
    early = settings.num("ai.rec.skip_early_s")
    recent_skips: dict[int, list[datetime]] = defaultdict(list)
    thumbs: dict[int, str] = {}
    events: list[tuple] = []
    n_feedback = n_plays = 0
    prev: tuple[int, datetime] | None = None

    for track_id, etype, at, pos, dur, raw_ctx in store.events(user_id):
        if etype in FEEDBACK_TYPES:
            n_feedback += 1
        ts = parse_ts(at)
        if ts is None:
            continue
        if etype == "play":
            n_plays += 1
        i = lib.index.get(track_id)
        if i is None:
            continue
        ctx = cx.parse(raw_ctx, ts.astimezone(tz), disabled)
        if etype == "play" and prev is not None and prev[0] == i and (ts - prev[1]).total_seconds() < 1800 \
                and "repeatOfPrevious" not in disabled:
            ctx.repeat = True  # same track played again straight away
        age = max((now - ts).total_seconds() / 86400, 0.0)
        decay = 0.5 ** (age / half_life)
        prior = sum(1 for t in recent_skips[i] if (ts - t).days < 30)
        d = dur or lib.duration[i]
        w = 0.0
        if etype not in THUMB_TYPES:
            w = event_weight(etype, pos, d, prior, early)
            if etype in ("play", "skip"):
                w += cx.detail_weight(ctx, d)
            w *= decay
        weight[i] += w
        keys = cx.context_keys(ctx, settings)
        for key in keys:
            ctx_weight[key][i] += w
        label = None
        if etype == "play":
            c = play_completion(pos, d)
            if c >= 0.5:
                plays[i] += 1
            label = 1 if (c >= 0.5 or ctx.repeat) else 0
            for key in keys:
                ctx_plays[key] += 1
            prev = (i, ts)
        elif etype == "skip":
            skips[i] += 1
            recent_skips[i].append(ts)
            label = 0
            prev = (i, ts)
        elif etype in THUMB_TYPES:
            thumbs[i] = etype  # events are in time order: the latest one wins
        if etype in ("play", "skip"):
            last[i] = min(last[i], age)
        events.append((ts, ctx, i, etype, label))

    # AI radio steering (more / less like this, never on this station): explicit feedback on the track.
    # Counted in the taste weight and, as labelled rows, in the learned model's training data.
    steered = False
    for track_id, action, _scope, at, raw_ctx in store.radio_feedback(user_id):
        i = lib.index.get(track_id)
        ts = parse_ts(at)
        w0 = RADIO_FEEDBACK_WEIGHT.get(action)
        if i is None or ts is None or w0 is None:
            continue
        n_feedback += 1
        age = max((now - ts).total_seconds() / 86400, 0.0)
        weight[i] += w0 * 0.5 ** (age / half_life)
        ctx = cx.parse(raw_ctx, ts.astimezone(tz), disabled)
        events.append((ts, ctx, i, f"radio_{action}", 1 if w0 > 0 else 0))
        steered = True
    if steered:
        events.sort(key=lambda e: e[0])

    for i, t in thumbs.items():
        blocked[i] = t == "thumb_down"  # overrides user_tracks.blocked, which may lag behind the events
        if t == "thumb_up":
            weight[i] += event_weight("thumb_up", None, None, 0)
    weight += np.where(starred, 3.0, 0.0)
    weight += np.where(rating > 0, rating - 3.0, 0.0)
    return Signals(weight=weight, plays=plays, skips=skips, last_played_days=last, blocked=blocked,
                   starred=starred, rating=rating, ctx_weight=dict(ctx_weight), ctx_plays=dict(ctx_plays),
                   n_feedback=n_feedback, n_plays=n_plays, events=events)


# ---------------------------------------------------------------- k-means

def spherical_kmeans(x: np.ndarray, w: np.ndarray, k: int, iters: int = 25, seed: int = 0) -> tuple:
    """Weighted k-means on unit vectors (cosine). Returns (centres (k,d), mass (k,), labels (n,))."""
    rng = np.random.default_rng(seed)
    n = x.shape[0]
    k = max(1, min(k, n))
    p = w / w.sum()
    centres = [x[rng.choice(n, p=p)]]
    for _ in range(1, k):  # k-means++ seeding
        d = 1 - np.max(x @ np.array(centres).T, axis=1)
        prob = np.clip(d, 0, None) * w
        if prob.sum() <= 0:
            break
        centres.append(x[rng.choice(n, p=prob / prob.sum())])
    c = np.array(centres)
    labels = np.zeros(n, dtype=int)
    for _ in range(iters):
        labels = np.argmax(x @ c.T, axis=1)
        new = np.zeros_like(c)
        for j in range(c.shape[0]):
            m = labels == j
            if m.any():
                v = (x[m] * w[m, None]).sum(axis=0)
                new[j] = v / max(np.linalg.norm(v), 1e-9)
            else:
                new[j] = c[j]
        if np.allclose(new, c):
            break
        c = new
    mass = np.array([w[labels == j].sum() for j in range(c.shape[0])])
    return c, mass, labels


# ---------------------------------------------------------------- learned model helpers

def _auc(y: np.ndarray, p: np.ndarray) -> float:
    pos, neg = y == 1, y == 0
    if not pos.any() or not neg.any():
        return 0.5
    ranks = np.argsort(np.argsort(p)) + 1
    return float((ranks[pos].sum() - pos.sum() * (pos.sum() + 1) / 2) / (pos.sum() * neg.sum()))


@dataclass
class FeatureSpace:
    pca_mean: np.ndarray
    pca_comp: np.ndarray       # (N_PCA, d)
    tag_keys: list[str]
    vocab: dict[str, list[str]] = field(default_factory=dict)  # context field -> known values

    @classmethod
    def fit(cls, lib: Library, events: list[tuple] = ()) -> FeatureSpace:
        x = lib.emb
        mean = x.mean(axis=0)
        k = min(N_PCA, x.shape[0], x.shape[1])
        _, _, vt = np.linalg.svd(x - mean, full_matrices=False)
        counts: dict[str, float] = defaultdict(float)
        for t in lib.tags:
            for key, v in t.items():
                counts[key] += v
        tag_keys = [k_ for k_, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:N_TAGS]]
        vocab: dict[str, list[str]] = {}
        for f in cx.CATEGORICAL:
            c: dict[str, int] = defaultdict(int)
            for e in events:
                v = getattr(e[1], f)
                if v:
                    c[v] += 1
            vocab[f] = [v for v, _ in sorted(c.items(), key=lambda kv: -kv[1])[:N_VOCAB]]
        return cls(mean.astype(np.float32), vt[:k].astype(np.float32), tag_keys, vocab)

    def static(self, lib: Library, sig: Signals) -> np.ndarray:
        """Per-track columns that do not depend on the moment: PCA, tags, energy, bpm, dance, star, rating."""
        pca = (lib.emb - self.pca_mean) @ self.pca_comp.T
        if pca.shape[1] < N_PCA:
            pca = np.pad(pca, ((0, 0), (0, N_PCA - pca.shape[1])))
        tags = np.array([[t.get(k, 0.0) for k in self.tag_keys] + [0.0] * (N_TAGS - len(self.tag_keys))
                         for t in lib.tags]).reshape(len(lib), N_TAGS)
        other = np.column_stack([lib.energy, lib.bpm / 200.0, lib.dance, sig.starred.astype(float), sig.rating])
        return np.hstack([pca, tags, other]).astype(np.float32)

    def code(self, f: str, value: str | None) -> float:
        vals = self.vocab.get(f, [])
        return float(vals.index(value)) if value in vals else np.nan

    def context_columns(self, ctxs: list[dict[str, Any]]) -> np.ndarray:
        """Categorical codes (NaN = unknown) for output, motion, place, network, weather + temperature."""
        rows = [[self.code(f, c.get(f)) for f in cx.CATEGORICAL]
                + [c["temp_c"] if c.get("temp_c") is not None else np.nan] for c in ctxs]
        return np.array(rows, dtype=float).reshape(len(ctxs), len(cx.CATEGORICAL) + 1)


N_STATIC = N_PCA + N_TAGS + 5
N_DYNAMIC = 7
CAT_IDX = list(range(N_STATIC + N_DYNAMIC, N_STATIC + N_DYNAMIC + len(cx.CATEGORICAL)))


def dynamic_columns(hour: np.ndarray, weekday: np.ndarray, days_since: np.ndarray, plays: np.ndarray,
                    skips: np.ndarray) -> np.ndarray:
    ang = 2 * np.pi * hour / 24
    ds = np.where(np.isfinite(days_since), np.minimum(days_since, 365), -1.0)
    return np.column_stack([np.sin(ang), np.cos(ang), hour, weekday, ds, np.log1p(plays), np.log1p(skips)])


def _ctx_dict(c: cx.Context) -> dict[str, Any]:
    return {"output": c.output, "motion": c.motion, "place": c.place, "network": c.network,
            "weather": c.weather, "temp_c": c.temp_c}


# ---------------------------------------------------------------- taste model

@dataclass
class TasteModel:
    model_version: str
    centroids: np.ndarray            # (k, d), may be (0, d)
    centroid_mass: np.ndarray
    centroid_labels: list[str]
    negatives: np.ndarray            # (m, d)
    space: FeatureSpace | None = None
    booster: Any = None              # lightgbm.Booster
    notes: dict[str, Any] = field(default_factory=dict)

    @property
    def mode(self) -> str:
        if self.booster is not None:
            return "learned"
        return "new_user" if self.notes.get("mode") == "new_user" else "cold"

    def sound_score(self, emb: np.ndarray) -> np.ndarray:
        emb = np.atleast_2d(emb)
        if self.centroids.shape[0] == 0:
            pos = np.zeros(emb.shape[0])
        else:
            pos = np.max(emb @ self.centroids.T, axis=1)
        if self.negatives.shape[0]:
            pos = pos - 0.5 * np.clip(np.max(emb @ self.negatives.T, axis=1), 0, None)
        return pos

    def nearest_centroid(self, emb: np.ndarray) -> tuple[int, float]:
        if self.centroids.shape[0] == 0:
            return -1, 0.0
        sims = self.centroids @ emb
        j = int(np.argmax(sims))
        return j, float(sims[j])

    # --- persistence
    def to_payload(self) -> bytes:
        d = {
            "model_version": self.model_version,
            "centroids": self.centroids.tolist(), "centroid_mass": self.centroid_mass.tolist(),
            "centroid_labels": self.centroid_labels, "negatives": self.negatives.tolist(),
            "space": None if self.space is None else {
                "pca_mean": self.space.pca_mean.tolist(), "pca_comp": self.space.pca_comp.tolist(),
                "tag_keys": self.space.tag_keys, "vocab": self.space.vocab},
            "booster": None if self.booster is None else self.booster.model_to_string(),
            "notes": self.notes,
        }
        return zlib.compress(json.dumps(d).encode())

    @classmethod
    def from_payload(cls, blob: bytes) -> TasteModel:
        d = json.loads(zlib.decompress(blob))
        dims = len(d["centroids"][0]) if d["centroids"] else (len(d["negatives"][0]) if d["negatives"] else 0)
        space = None
        if d.get("space"):
            s = d["space"]
            space = FeatureSpace(np.array(s["pca_mean"], np.float32), np.array(s["pca_comp"], np.float32),
                                 list(s["tag_keys"]), dict(s.get("vocab") or {}))
        booster = None
        if d.get("booster"):
            import lightgbm as lgb
            booster = lgb.Booster(model_str=d["booster"])
        return cls(model_version=d["model_version"],
                   centroids=np.array(d["centroids"], np.float32).reshape(-1, dims),
                   centroid_mass=np.array(d["centroid_mass"]), centroid_labels=list(d["centroid_labels"]),
                   negatives=np.array(d["negatives"], np.float32).reshape(-1, dims),
                   space=space, booster=booster, notes=d.get("notes", {}))


def build_cold(lib: Library, sig: Signals, settings: Settings) -> TasteModel:
    d = lib.emb.shape[1] if len(lib) else 0
    pos = np.where((sig.weight > 0.25) & ~sig.blocked)[0]
    neg = np.where((sig.weight < -0.5) | sig.blocked)[0]
    k_max = max(1, settings.int("ai.rec.taste_clusters"))
    centroids = np.zeros((0, d), np.float32)
    mass = np.zeros(0)
    labels: list[str] = []
    if pos.size:
        k = min(k_max, max(1, pos.size // 6))
        centroids, mass, lab = spherical_kmeans(lib.emb[pos], sig.weight[pos], k)
        for j in range(centroids.shape[0]):
            labels.append(_cluster_label(lib, pos[lab == j]))
    negatives = np.zeros((0, d), np.float32)
    if neg.size:
        w = np.abs(np.where(sig.blocked[neg], 3.0, sig.weight[neg]))
        negatives, _, _ = spherical_kmeans(lib.emb[neg], w, min(4, max(1, neg.size // 6)))
    return TasteModel(model_version=lib.model_version, centroids=centroids.astype(np.float32), centroid_mass=mass,
                      centroid_labels=labels, negatives=negatives.astype(np.float32),
                      notes={"mode": "cold", "positives": int(pos.size), "negatives": int(neg.size),
                             "feedback_events": sig.n_feedback, "plays": sig.n_plays})


def build_new_user(lib: Library, sig: Signals, popularity: np.ndarray, settings: Settings) -> TasteModel:
    """Library-wide prior: centres spread over the whole library, weighted by anonymous popularity."""
    d = lib.emb.shape[1] if len(lib) else 0
    m = build_cold(lib, sig, settings)  # keeps the user's own negatives (skips, thumbs-down)
    if len(lib):
        w = 1.0 + np.log1p(popularity)
        k = min(8, max(1, len(lib) // 25))
        centres, mass, lab = spherical_kmeans(lib.emb, w, k, seed=1)
        m.centroids = centres.astype(np.float32)
        m.centroid_mass = mass
        m.centroid_labels = [_cluster_label(lib, np.where(lab == j)[0]) for j in range(centres.shape[0])]
    else:
        m.centroids = np.zeros((0, d), np.float32)
    m.notes.update({"mode": "new_user", "new_user_plays": settings.int("ai.rec.new_user_plays")})
    return m


def _cluster_label(lib: Library, members: np.ndarray) -> str:
    agg: dict[str, float] = defaultdict(float)
    for i in members:
        for k, v in lib.tags[i].items():
            if k.startswith("genre:"):
                agg[k] += v
        if lib.genre[i]:
            agg["genre:" + lib.genre[i]] += 0.5
    label = top_tag(agg) if agg else None
    if label:
        return label
    artists: dict[str, int] = defaultdict(int)
    for i in members:
        artists[lib.artist_name[i]] += 1
    return max(artists, key=artists.get) if artists else "favourites"


def train_learned(lib: Library, sig: Signals, model: TasteModel, settings: Settings) -> TasteModel:
    """Add a LightGBM play-through classifier to `model` when there is enough feedback."""
    min_events = settings.int("ai.rec.learned_min_events")
    model.notes["learned_min_events"] = min_events
    if sig.n_feedback < min_events:
        model.notes["learned"] = f"waiting for feedback ({sig.n_feedback}/{min_events} events)"
        return model
    rows = [e for e in sig.events if e[4] is not None]
    y = np.array([e[4] for e in rows])
    if len(rows) < 100 or y.sum() < 20 or (1 - y).sum() < 20:
        model.notes["learned"] = "not enough play/skip events of both kinds"
        return model
    import lightgbm as lgb

    space = FeatureSpace.fit(lib, sig.events)
    static = space.static(lib, sig)
    # replay events to get the state *before* each event (no peeking at the future)
    n = len(lib)
    plays, skips, last = np.zeros(n), np.zeros(n), np.full(n, None, dtype=object)
    idx, hours, wds, ds, pl, sk, ctxs = [], [], [], [], [], [], []
    for ts, ctx, i, etype, label in sig.events:
        if label is not None:
            idx.append(i)
            hours.append(ctx.hour)
            wds.append(ctx.weekday)
            ds.append((ts - last[i]).total_seconds() / 86400 if last[i] is not None else np.inf)
            pl.append(plays[i])
            sk.append(skips[i])
            ctxs.append(_ctx_dict(ctx))
        if etype == "play":
            plays[i] += label or 0
            last[i] = ts
        elif etype == "skip":
            skips[i] += 1
            last[i] = ts
    X = np.hstack([static[idx],
                   dynamic_columns(np.array(hours, float), np.array(wds, float), np.array(ds, float),
                                   np.array(pl), np.array(sk)),
                   space.context_columns(ctxs)])
    params = {"objective": "binary", "learning_rate": 0.05, "num_leaves": 15, "min_data_in_leaf": 10,
              "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0,
              "verbose": -1, "num_threads": 2, "seed": 7, "cat_smooth": 10}

    def fit(Xs, ys):
        return lgb.train(params, lgb.Dataset(Xs, ys, categorical_feature=CAT_IDX, free_raw_data=False),
                         num_boost_round=150)
    cut = int(len(y) * 0.8)
    auc = 0.5
    if 0 < cut < len(y) and len(set(y[:cut])) == 2 and len(set(y[cut:])) == 2:
        auc = _auc(y[cut:], fit(X[:cut], y[:cut]).predict(X[cut:]))
    n_ctx = sum(1 for c in ctxs if any(v is not None for v in c.values()))
    model.notes.update({"training_rows": len(y), "validation_auc": round(auc, 3),
                        "rows_with_context": n_ctx})
    if auc < MIN_AUC:
        model.notes["learned"] = f"validation AUC {auc:.2f} < {MIN_AUC}; staying on cold start"
        return model
    model.booster = fit(X, y)
    model.space = space
    model.notes["mode"] = "learned"
    model.notes["learned"] = "active"
    return model


# ---------------------------------------------------------------- engine facade


def family_mask(conn: sqlite3.Connection, user_id: str | None, lib: Library) -> np.ndarray | None:
    """Bool mask over the library of tracks this user's family mode hides (None = nothing hidden)."""
    from nasmusic.db import family

    hidden = family.all_hidden(conn, user_id)
    if not hidden or not len(lib):
        return None
    return np.array([t in hidden for t in lib.ids], dtype=bool)


class Recommender:
    """Everything the mix builder needs for one user and one run."""

    def __init__(self, conn: sqlite3.Connection, cfg, user_id: str | None = None, now: datetime | None = None,
                 store: Store | None = None):
        self.conn = conn
        self.cfg = cfg
        self.user_id = user_id
        self.store = store or Store(conn)
        self.now = now or now_utc()
        user_settings = self.store.user_settings(user_id)
        self.settings = load_settings(conn, user_settings)
        tz = user_settings.get("timezone")
        self.tz = tz if isinstance(tz, str) and tz else cfg.timezone
        self.version = active_version(conn, effective_version(cfg.model_version))
        self.lib = load_library(conn, self.version)
        self.disabled = cx.disabled_fields(user_settings)
        self.sig = collect_signals(self.store, user_id, self.lib, self.settings, self.tz, self.now, self.disabled)
        self.sig.hidden = family_mask(conn, user_id, self.lib)
        self.is_new_user = self.sig.n_plays < self.settings.int("ai.rec.new_user_plays")
        self.popularity = np.zeros(len(self.lib))
        if self.is_new_user and len(self.lib):
            pop = self.store.popularity(exclude_user=user_id)
            self.popularity = np.array([pop.get(t, 0) for t in self.lib.ids], dtype=float)
        self.model: TasteModel | None = None
        self._static: np.ndarray | None = None

    # --- model lifecycle
    def train(self, save: bool = True) -> TasteModel:
        if self.is_new_user:
            model = build_new_user(self.lib, self.sig, self.popularity, self.settings)
        else:
            model = build_cold(self.lib, self.sig, self.settings)
            if len(self.lib) >= 20:
                try:
                    model = train_learned(self.lib, self.sig, model, self.settings)
                except Exception as e:
                    log.exception("learned model training failed; using cold start")
                    model.notes["learned"] = f"training failed: {e}"
        self.model = model
        self._static = None
        if save:
            self.save_snapshot()
        log.info("taste model (user %s): %s", self.user_id, json.dumps(model.notes))
        return model

    def save_snapshot(self) -> str:
        assert self.model is not None
        sid = new_id()
        cols, marks, vals = self.store.owner(self.user_id)
        where, wvals = self.store.where_owner(self.user_id)
        with transaction(self.conn):
            self.conn.execute(
                f"INSERT INTO taste_snapshots (id, created_at, model_version, payload, notes{cols}) "
                f"VALUES (?,?,?,?,?{marks})",
                (sid, iso(self.now), self.version, self.model.to_payload(), json.dumps(self.model.notes), *vals))
            # keep the last 30 snapshots per user and version
            self.conn.execute(
                f"DELETE FROM taste_snapshots WHERE model_version = ?{where} AND id NOT IN (SELECT id FROM "
                f"taste_snapshots WHERE model_version = ?{where} ORDER BY created_at DESC, rowid DESC LIMIT 30)",
                (self.version, *wvals, self.version, *wvals))
        return sid

    def load_or_train(self) -> TasteModel:
        if self.model is not None:
            return self.model
        where, wvals = self.store.where_owner(self.user_id)
        row = self.conn.execute(
            f"SELECT payload FROM taste_snapshots WHERE model_version = ?{where} "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1", (self.version, *wvals)).fetchone()
        if row is not None and len(self.lib):
            try:
                m = TasteModel.from_payload(row[0])
                stale = (m.mode == "new_user") != self.is_new_user
                if m.centroids.shape[1] in (0, self.lib.emb.shape[1]) and not stale:
                    self.model = m
                    return m
            except Exception:
                log.exception("could not load taste snapshot; rebuilding")
        return self.train(save=False)

    # --- scoring
    def learned_prob(self, hour: float, weekday: float, idx: np.ndarray | None = None,
                     context: dict[str, Any] | None = None) -> np.ndarray | None:
        m = self.load_or_train()
        if m.booster is None or m.space is None or not len(self.lib):
            return None
        if self._static is None:
            self._static = m.space.static(self.lib, self.sig)
        idx = np.arange(len(self.lib)) if idx is None else idx
        k = len(idx)
        dyn = dynamic_columns(np.full(k, hour, float), np.full(k, weekday, float),
                              self.sig.last_played_days[idx], self.sig.plays[idx], self.sig.skips[idx])
        ctx = np.repeat(m.space.context_columns([context or {}]), k, axis=0)
        try:
            return m.booster.predict(np.hstack([self._static[idx], dyn, ctx]))
        except Exception:
            log.exception("learned model prediction failed; using cold-start scores")
            return None

    def scores(self, hour: float = 14, weekday: float | None = None, tod: str | None = None) -> np.ndarray:
        """Final score per library track (higher = better). Blocked tracks get -inf.
        `tod` is a context key ('morning', 'workout', 'place:work' ...) to favour."""
        n = len(self.lib)
        if n == 0:
            return np.zeros(0)
        m = self.load_or_train()
        s = self.settings
        if weekday is None:
            weekday = self.now.astimezone(local_tz(self.tz)).weekday()
        sound = m.sound_score(self.lib.emb)
        known = np.tanh(self.sig.weight / 3.0)
        if self.is_new_user:
            pop = self.popularity
            pop_pct = np.log1p(pop) / np.log1p(pop.max()) if pop.any() else np.zeros(n)  # 0 = never played
            recent = np.exp(-self.lib.added_days / 60.0)
            base = 0.35 * sound + 0.3 * pop_pct + 0.15 * recent + 0.2 * known
        else:
            base = 0.6 * sound + 0.4 * known
        if tod and tod in self.sig.ctx_weight:
            base = base + 0.3 * np.tanh(self.sig.ctx_weight[tod] / 2.0)
        context = dict(cx.target(tod)) if tod else {}
        hour = context.pop("hour", hour)
        p = self.learned_prob(hour, weekday, context=context)
        if p is not None:
            blend = float(np.clip(s.num("ai.rec.learned_blend"), 0, 1))
            base = blend * (2 * p - 1) + (1 - blend) * base
        days = self.sig.last_played_days
        window = max(s.num("ai.rec.recent_penalty_days"), 1.0)
        fresh = np.where(np.isfinite(days), -0.3 * np.clip(1 - days / window, 0, 1),
                         s.num("ai.rec.never_played_boost"))
        out = base + fresh
        return np.where(self.sig.excluded, -np.inf, out)

    def similar(self, i: int, k: int = 3, exclude_same_album: bool = True) -> list[int]:
        if not len(self.lib):
            return []
        sims = self.lib.emb @ self.lib.emb[i]
        sims[i] = -np.inf
        if exclude_same_album:
            album = self.lib.album[i]
            sims[[j for j, a in enumerate(self.lib.album) if a == album]] = -np.inf
        sims[self.sig.excluded] = -np.inf
        order = np.argsort(-sims)
        return [int(j) for j in order[:k] if np.isfinite(sims[j])]
