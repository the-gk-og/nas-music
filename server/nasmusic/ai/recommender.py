"""Taste model and track scoring.

Signals (from `events` plus the current `tracks` state):

| signal                         | weight                                                  |
| ------------------------------ | ------------------------------------------------------- |
| full play (>= 80 % listened)   | +1.0  (mild like); 50-80 % = +0.5; shorter = +0.1       |
| skip after 30 s                | -0.6  (mild dislike)                                    |
| skip before 30 s               | -1.5  (strong)                                          |
| repeated skip (30 days)        | each further skip x1.5, x2.0 ... (capped at -3 / event) |
| thumbs-up (latest thumb event) | +2.0                                                    |
| star (tracks.starred_at)       | +3.0  (strong like)                                     |
| rating r (tracks.rating)       | (r - 3) x 1.0, i.e. 1 star = -2 ... 5 stars = +2        |
| thumbs-down (latest thumb)     | excluded from every mix                                 |

Thumbs: per track the latest thumb_up / thumb_down / thumb_clear event wins (thumb_clear cancels the
earlier thumb). Tracks with no thumb events fall back to `tracks.blocked`.

Event weights decay with a half-life (`ai.rec.half_life_days`).

Scoring:
* Cold start: taste = weighted spherical k-means on the embeddings of positively weighted tracks
  (several centres, so liking metal and jazz doesn't average into mush); negatives = centres of
  disliked tracks. sound = max cos(track, taste centre) - 0.5 * max(0, max cos(track, negative)).
* Learned (>= `ai.rec.learned_min_events` feedback events and a validation AUC >= 0.55): a LightGBM
  "will you play this through?" classifier on embedding PCA, tags, energy/BPM, hour of day, weekday,
  days since last play and play/skip counts, retrained nightly and saved in `taste_snapshots`.
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

log = logging.getLogger("nasmusic.ai.recommender")

FEEDBACK_TYPES = ("play", "skip", "star", "unstar", "rate", "thumb_up", "thumb_down", "thumb_clear")
THUMB_TYPES = ("thumb_up", "thumb_down", "thumb_clear")
N_PCA = 16
N_TAGS = 24
MIN_AUC = 0.55


# ---------------------------------------------------------------- signals

@dataclass
class Signals:
    weight: np.ndarray            # per library track, decayed
    plays: np.ndarray             # full-ish plays (>= 50 %)
    skips: np.ndarray
    last_played_days: np.ndarray  # inf when never played
    blocked: np.ndarray           # bool
    tod_weight: dict[str, np.ndarray]  # 'morning' / 'evening' -> weight from events in that window
    n_feedback: int
    events: list[tuple]           # (datetime, local hour, weekday, lib index, type, label or None)


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


def _in_window(hour: int, window) -> bool:
    lo, hi = int(window[0]), int(window[1])
    return lo <= hour < hi if lo <= hi else (hour >= lo or hour < hi)


def collect_signals(conn: sqlite3.Connection, lib: Library, settings: Settings, tz_name: str,
                    now: datetime | None = None) -> Signals:
    now = now or now_utc()
    tz = local_tz(tz_name)
    n = len(lib)
    weight = np.zeros(n)
    plays = np.zeros(n)
    skips = np.zeros(n)
    last = np.full(n, np.inf)
    blocked = lib.blocked.copy()
    tod = {"morning": np.zeros(n), "evening": np.zeros(n)}
    windows = {"morning": settings["ai.mix.morning.hours"], "evening": settings["ai.mix.evening.hours"]}
    half_life = max(settings.num("ai.rec.half_life_days"), 1.0)
    early = settings.num("ai.rec.skip_early_s")
    recent_skips: dict[int, list[datetime]] = defaultdict(list)
    thumbs: dict[int, str] = {}
    events: list[tuple] = []
    n_feedback = 0

    rows = conn.execute(
        "SELECT track_id, type, at, position_s, duration_s FROM events ORDER BY at, received_at"
    ).fetchall()
    for track_id, etype, at, pos, dur in rows:
        if etype in FEEDBACK_TYPES:
            n_feedback += 1
        i = lib.index.get(track_id)
        ts = parse_ts(at)
        if i is None or ts is None:
            continue
        local = ts.astimezone(tz)
        age = max((now - ts).total_seconds() / 86400, 0.0)
        decay = 0.5 ** (age / half_life)
        prior = sum(1 for t in recent_skips[i] if (ts - t).days < 30)
        w = 0.0 if etype in THUMB_TYPES else event_weight(etype, pos, dur or lib.duration[i], prior, early) * decay
        weight[i] += w
        for name, win in windows.items():
            if _in_window(local.hour, win):
                tod[name][i] += w
        label = None
        if etype == "play":
            c = play_completion(pos, dur or lib.duration[i])
            if c >= 0.5:
                plays[i] += 1
            label = 1 if c >= 0.5 else 0
        elif etype == "skip":
            skips[i] += 1
            recent_skips[i].append(ts)
            label = 0
        elif etype in THUMB_TYPES:
            thumbs[i] = etype  # events are in time order: the latest one wins
        if etype in ("play", "skip"):
            last[i] = min(last[i], age)
        events.append((ts, local.hour, local.weekday(), i, etype, label))

    for i, t in thumbs.items():
        blocked[i] = t == "thumb_down"  # overrides tracks.blocked, which may lag behind the events
        if t == "thumb_up":
            weight[i] += event_weight("thumb_up", None, None, 0)
    weight += np.where(lib.starred, 3.0, 0.0)
    weight += np.where(lib.rating > 0, lib.rating - 3.0, 0.0)
    return Signals(weight=weight, plays=plays, skips=skips, last_played_days=last, blocked=blocked,
                   tod_weight=tod, n_feedback=n_feedback, events=events)


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

    @classmethod
    def fit(cls, lib: Library) -> FeatureSpace:
        x = lib.emb
        mean = x.mean(axis=0)
        k = min(N_PCA, x.shape[0], x.shape[1])
        _, _, vt = np.linalg.svd(x - mean, full_matrices=False)
        counts: dict[str, float] = defaultdict(float)
        for t in lib.tags:
            for key, v in t.items():
                counts[key] += v
        tag_keys = [k_ for k_, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:N_TAGS]]
        return cls(mean.astype(np.float32), vt[:k].astype(np.float32), tag_keys)

    def static(self, lib: Library) -> np.ndarray:
        """Per-track columns that do not depend on time: PCA, tags, energy, bpm, dance, star, rating."""
        pca = (lib.emb - self.pca_mean) @ self.pca_comp.T
        if pca.shape[1] < N_PCA:
            pca = np.pad(pca, ((0, 0), (0, N_PCA - pca.shape[1])))
        tags = np.array([[t.get(k, 0.0) for k in self.tag_keys] + [0.0] * (N_TAGS - len(self.tag_keys))
                         for t in lib.tags]).reshape(len(lib), N_TAGS)
        other = np.column_stack([lib.energy, lib.bpm / 200.0, lib.dance, lib.starred.astype(float),
                                 lib.rating])
        return np.hstack([pca, tags, other]).astype(np.float32)


def dynamic_columns(hour: np.ndarray, weekday: np.ndarray, days_since: np.ndarray, plays: np.ndarray,
                    skips: np.ndarray) -> np.ndarray:
    ang = 2 * np.pi * hour / 24
    ds = np.where(np.isfinite(days_since), np.minimum(days_since, 365), -1.0)
    return np.column_stack([np.sin(ang), np.cos(ang), hour, weekday, ds, np.log1p(plays), np.log1p(skips)])


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
        return "learned" if self.booster is not None else "cold"

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
                "tag_keys": self.space.tag_keys},
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
                                 list(s["tag_keys"]))
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
            members = pos[lab == j]
            labels.append(_cluster_label(lib, members))
    negatives = np.zeros((0, d), np.float32)
    if neg.size:
        w = np.abs(np.where(sig.blocked[neg], 3.0, sig.weight[neg]))
        negatives, _, _ = spherical_kmeans(lib.emb[neg], w, min(4, max(1, neg.size // 6)))
    return TasteModel(model_version=lib.model_version, centroids=centroids.astype(np.float32), centroid_mass=mass,
                      centroid_labels=labels, negatives=negatives.astype(np.float32),
                      notes={"mode": "cold", "positives": int(pos.size), "negatives": int(neg.size),
                             "feedback_events": sig.n_feedback})


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
    artists = defaultdict(int)
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
    rows = [e for e in sig.events if e[5] is not None]
    y = np.array([e[5] for e in rows])
    if len(rows) < 100 or y.sum() < 20 or (1 - y).sum() < 20:
        model.notes["learned"] = "not enough play/skip events of both kinds"
        return model
    import lightgbm as lgb

    space = FeatureSpace.fit(lib)
    static = space.static(lib)
    # replay events to get the state *before* each event (no peeking at the future)
    n = len(lib)
    plays, skips, last = np.zeros(n), np.zeros(n), np.full(n, None, dtype=object)
    idx, hours, wds, ds, pl, sk = [], [], [], [], [], []
    for ts, hour, wd, i, etype, label in sig.events:
        if label is not None:
            idx.append(i)
            hours.append(hour)
            wds.append(wd)
            ds.append((ts - last[i]).total_seconds() / 86400 if last[i] is not None else np.inf)
            pl.append(plays[i])
            sk.append(skips[i])
        if etype == "play":
            plays[i] += label or 0
            last[i] = ts
        elif etype == "skip":
            skips[i] += 1
            last[i] = ts
    X = np.hstack([static[idx], dynamic_columns(np.array(hours, float), np.array(wds, float),
                                                np.array(ds, float), np.array(pl), np.array(sk))])
    params = {"objective": "binary", "learning_rate": 0.05, "num_leaves": 15, "min_data_in_leaf": 10,
              "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0,
              "verbose": -1, "num_threads": 2, "seed": 7}
    cut = int(len(y) * 0.8)
    auc = 0.5
    if 0 < cut < len(y) and len(set(y[:cut])) == 2 and len(set(y[cut:])) == 2:
        val = lgb.train(params, lgb.Dataset(X[:cut], y[:cut]), num_boost_round=150)
        auc = _auc(y[cut:], val.predict(X[cut:]))
    model.notes.update({"training_rows": len(y), "validation_auc": round(auc, 3)})
    if auc < MIN_AUC:
        model.notes["learned"] = f"validation AUC {auc:.2f} < {MIN_AUC}; staying on cold start"
        return model
    model.booster = lgb.train(params, lgb.Dataset(X, y), num_boost_round=150)
    model.space = space
    model.notes["mode"] = "learned"
    model.notes["learned"] = "active"
    return model


# ---------------------------------------------------------------- engine facade

class Recommender:
    """Everything the mix builder needs for one run: library, signals, taste model, scores."""

    def __init__(self, conn: sqlite3.Connection, cfg, now: datetime | None = None):
        self.conn = conn
        self.cfg = cfg
        self.now = now or now_utc()
        self.settings = load_settings(conn)
        self.version = active_version(conn, effective_version(cfg.model_version))
        self.lib = load_library(conn, self.version)
        self.sig = collect_signals(conn, self.lib, self.settings, cfg.timezone, self.now)
        self.model: TasteModel | None = None
        self._static: np.ndarray | None = None

    # --- model lifecycle
    def train(self, save: bool = True) -> TasteModel:
        model = build_cold(self.lib, self.sig, self.settings)
        if len(self.lib) >= 20:
            try:
                model = train_learned(self.lib, self.sig, model, self.settings)
            except Exception as e:
                log.exception("learned model training failed; using cold start")
                model.notes["learned"] = f"training failed: {e}"
        self.model = model
        if save:
            self.save_snapshot()
        log.info("taste model: %s", json.dumps(model.notes))
        return model

    def save_snapshot(self) -> str:
        assert self.model is not None
        sid = new_id()
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO taste_snapshots (id, created_at, model_version, payload, notes) VALUES (?,?,?,?,?)",
                (sid, iso(self.now), self.version, self.model.to_payload(), json.dumps(self.model.notes)))
            # keep the last 30 snapshots per version
            self.conn.execute(
                "DELETE FROM taste_snapshots WHERE model_version = ? AND id NOT IN (SELECT id FROM "
                "taste_snapshots WHERE model_version = ? ORDER BY created_at DESC, rowid DESC LIMIT 30)",
                (self.version, self.version))
        return sid

    def load_or_train(self) -> TasteModel:
        if self.model is not None:
            return self.model
        row = self.conn.execute(
            "SELECT payload FROM taste_snapshots WHERE model_version = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (self.version,)).fetchone()
        if row is not None and len(self.lib):
            try:
                m = TasteModel.from_payload(row[0])
                if m.centroids.shape[1] in (0, self.lib.emb.shape[1]):
                    self.model = m
                    return m
            except Exception:
                log.exception("could not load taste snapshot; rebuilding")
        return self.train(save=False)

    # --- scoring
    def learned_prob(self, hour: float, weekday: float, idx: np.ndarray | None = None) -> np.ndarray | None:
        m = self.load_or_train()
        if m.booster is None or m.space is None or not len(self.lib):
            return None
        if self._static is None:
            self._static = m.space.static(self.lib)
        idx = np.arange(len(self.lib)) if idx is None else idx
        k = len(idx)
        dyn = dynamic_columns(np.full(k, hour, float), np.full(k, weekday, float),
                              self.sig.last_played_days[idx], self.sig.plays[idx], self.sig.skips[idx])
        try:
            return m.booster.predict(np.hstack([self._static[idx], dyn]))
        except Exception:
            log.exception("learned model prediction failed; using cold-start scores")
            return None

    def scores(self, hour: float = 14, weekday: float | None = None, tod: str | None = None) -> np.ndarray:
        """Final score per library track (higher = better). Blocked tracks get -inf."""
        n = len(self.lib)
        if n == 0:
            return np.zeros(0)
        m = self.load_or_train()
        s = self.settings
        if weekday is None:
            weekday = self.now.astimezone(local_tz(self.cfg.timezone)).weekday()
        sound = m.sound_score(self.lib.emb)
        known = np.tanh(self.sig.weight / 3.0)
        base = 0.6 * sound + 0.4 * known
        if tod and tod in self.sig.tod_weight:
            base = base + 0.3 * np.tanh(self.sig.tod_weight[tod] / 2.0)
        p = self.learned_prob(hour, weekday)
        if p is not None:
            blend = float(np.clip(s.num("ai.rec.learned_blend"), 0, 1))
            base = blend * (2 * p - 1) + (1 - blend) * base
        days = self.sig.last_played_days
        window = max(s.num("ai.rec.recent_penalty_days"), 1.0)
        fresh = np.where(np.isfinite(days), -0.3 * np.clip(1 - days / window, 0, 1),
                         s.num("ai.rec.never_played_boost"))
        out = base + fresh
        return np.where(self.sig.blocked, -np.inf, out)

    def similar(self, i: int, k: int = 3, exclude_same_album: bool = True) -> list[int]:
        if not len(self.lib):
            return []
        sims = self.lib.emb @ self.lib.emb[i]
        sims[i] = -np.inf
        if exclude_same_album:
            album = self.lib.album[i]
            sims[[j for j, a in enumerate(self.lib.album) if a == album]] = -np.inf
        sims[self.sig.blocked] = -np.inf
        order = np.argsort(-sims)
        return [int(j) for j in order[:k] if np.isfinite(sims[j])]


def latest_notes(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT notes, created_at FROM taste_snapshots ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()
    if not row:
        return {}
    try:
        return json.loads(row[0] or "{}") | {"created_at": row[1]}
    except ValueError:
        return {}
