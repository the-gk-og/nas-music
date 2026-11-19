"""Shared helpers for the AI engine: settings, time, embeddings, transactions.

Only imports `nasmusic.db`-level concepts (a sqlite3 connection); never server-core modules.
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
import sqlite3
import threading
import unicodedata
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

log = logging.getLogger("nasmusic.ai")

BASIC_VERSION = "basic-1"

# Every tunable lives in the `settings` table (JSON values) under these keys. Missing keys are
# seeded with these defaults the first time the engine runs, so the web UI can show and edit them.
DEFAULT_SETTINGS: dict[str, Any] = {
    # analyser
    "ai.analysis.workers": 2,
    "ai.analysis.max_seconds": 480,          # whole-track analysis: at most this much (centre crop)
    "ai.analysis.excerpts": 3,                # EffNet: analyse N excerpts (20/50/80 %) instead; 0 = whole
    "ai.analysis.excerpt_seconds": 40,
    "ai.analysis.retry_failed_days": 7,
    # recommender
    "ai.rec.learned_min_events": 500,         # feedback events before the LightGBM model kicks in
    "ai.rec.learned_blend": 0.6,              # share of the learned model in the final score
    "ai.rec.taste_clusters": 6,               # max k-means centres for the taste profile
    "ai.rec.half_life_days": 120,             # older feedback counts less
    "ai.rec.recent_penalty_days": 14,         # freshness: penalty fades over this many days
    "ai.rec.never_played_boost": 0.05,
    "ai.rec.skip_early_s": 30,
    "ai.rec.new_user_plays": 20,              # below this many plays a user gets library-wide cold start
    # mixes
    "ai.mix.daily.minutes": 240,
    "ai.mix.morning.minutes": 90,
    "ai.mix.evening.minutes": 90,
    "ai.mix.deep_cuts.minutes": 90,
    "ai.mix.known_pct": 0.6,
    "ai.mix.rare_pct": 0.3,
    "ai.mix.explore_pct": 0.1,
    "ai.mix.rare_max_plays": 2,
    "ai.mix.deep_cuts.max_plays": 1,
    "ai.mix.artist_gap": 5,
    "ai.mix.recent_days": 3,
    "ai.mix.morning.hours": [5, 11],          # local hours [start, end)
    "ai.mix.evening.hours": [17, 24],
    "ai.mix.late_night.hours": [22, 4],
    "ai.mix.context.min_plays": 30,           # a context mix needs this many plays in that context
    "ai.mix.context.minutes": 60,
    "ai.mix.context.max": 3,                  # at most this many context mixes per night
    "ai.mix.keep_days": 60,                   # inactive mixes older than this are deleted
    # discovery
    "ai.discovery.per_week": 10,
    "ai.discovery.max_per_cluster": 2,
    "ai.discovery.seed_artists": 15,
    "ai.discovery.preview_candidates": 25,
    "ai.discovery.qobuz_locale": "gb-en",
}


def now_utc() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None = None) -> str:
    """Canonical timestamp format written by the AI: 'YYYY-MM-DDTHH:MM:SSZ'."""
    return (dt or now_utc()).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace(" ", "T"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def local_tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def new_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------- settings

def ensure_default_settings(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
        [(k, json.dumps(v)) for k, v in DEFAULT_SETTINGS.items()],
    )


STATUS_KEY = "ai.status.model_version"


def write_status(conn: sqlite3.Connection, **fields: Any) -> None:
    """Merge `fields` into the JSON status object at settings[STATUS_KEY] (one short transaction, so
    concurrent analysis and nightly runs don't overwrite each other's fields)."""
    with transaction(conn):
        cur = read_status(conn)
        cur.update(fields)
        cur["updated_at"] = iso()
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (STATUS_KEY, json.dumps(cur)))


def read_status(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (STATUS_KEY,)).fetchone()
    try:
        cur = json.loads(row[0]) if row else {}
    except (TypeError, ValueError):
        return {}
    return cur if isinstance(cur, dict) else {}


@dataclass
class Settings:
    values: dict[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self.values.get(key, DEFAULT_SETTINGS.get(key))

    def num(self, key: str) -> float:
        try:
            return float(self[key])
        except (TypeError, ValueError):
            return float(DEFAULT_SETTINGS[key])

    def int(self, key: str) -> int:
        return round(self.num(key))


def load_settings(conn: sqlite3.Connection, user_overrides: dict[str, Any] | None = None) -> Settings:
    """Global `ai.*` settings, with a user's own `ai.*` keys from `user_settings` layered on top."""
    vals: dict[str, Any] = {}
    for key, value in conn.execute("SELECT key, value FROM settings WHERE key LIKE 'ai.%' AND key NOT LIKE 'ai.status.%'"):
        try:
            vals[key] = json.loads(value)
        except (TypeError, ValueError):
            log.warning("setting %s has invalid JSON %r; using default", key, value)
    for key, value in (user_overrides or {}).items():
        if key.startswith("ai.") and not key.startswith("ai.status.") and key in DEFAULT_SETTINGS:
            vals[key] = value
    return Settings(vals)


# ---------------------------------------------------------------- transactions

@contextlib.contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT, or join an outer transaction if one is open."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


# ---------------------------------------------------------------- embeddings

def to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype="<f4").tobytes()


def from_blob(blob: bytes, dims: int | None = None) -> np.ndarray:
    v = np.frombuffer(blob, dtype="<f4")
    if dims is not None and v.size != dims:
        raise ValueError(f"embedding has {v.size} values, expected {dims}")
    return v


def active_version(conn: sqlite3.Connection, preferred: str) -> str:
    """The feature version the recommender should read.

    The preferred version is used once it covers most of the library. While a model upgrade is
    re-analysing overnight, the version with the best coverage keeps the mixes working.
    """
    rows = conn.execute(
        "SELECT f.model_version, COUNT(*) FROM features f JOIN tracks t ON t.id = f.track_id "
        "WHERE t.missing = 0 GROUP BY f.model_version"
    ).fetchall()
    counts = {r[0]: r[1] for r in rows}
    if not counts:
        return preferred
    total = conn.execute("SELECT COUNT(*) FROM tracks WHERE missing = 0").fetchone()[0] or 1
    if counts.get(preferred, 0) >= 0.8 * total:
        return preferred
    best = max(counts, key=lambda v: counts[v])
    return best if counts[best] > counts.get(preferred, 0) else preferred


@dataclass
class Library:
    """Analysed, present tracks with normalised embeddings and metadata, aligned by row index."""
    ids: list[str]
    index: dict[str, int]
    emb: np.ndarray            # (n, d) centred, scaled, L2-normalised
    raw_mean: np.ndarray       # normalisation stats, to embed outside tracks (previews) identically
    raw_std: np.ndarray
    energy: np.ndarray         # 0..1 percentile rank within the library
    energy_raw: np.ndarray
    bpm: np.ndarray
    dance: np.ndarray
    tags: list[dict[str, float]]
    artist: list[str]
    artist_name: list[str]
    album: list[str]
    title: list[str]
    duration: np.ndarray
    added_days: np.ndarray     # days since the track was added to the library
    genre: list[str | None]
    model_version: str

    def __len__(self) -> int:
        return len(self.ids)

    def normalise(self, raw: np.ndarray) -> np.ndarray:
        x = (np.asarray(raw, dtype=np.float32) - self.raw_mean) / self.raw_std
        n = np.linalg.norm(x, axis=-1, keepdims=True)
        return x / np.maximum(n, 1e-9)


_LIB_CACHE: dict[tuple, Library] = {}
_LIB_LOCK = threading.Lock()


def load_library(conn: sqlite3.Connection, model_version: str) -> Library:
    """The analysed library, cached in-process between calls (radio, explain, more-like are called often;
    loading 5,000 embeddings from SQLite each time is the slow part on the i5). Any new analysis, scan
    change or new database file invalidates it. Callers must treat the result as read-only."""
    db_file = next((r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main"), "")
    f = conn.execute("SELECT COUNT(*), MAX(analysed_at) FROM features WHERE model_version = ?",
                     (model_version,)).fetchone()
    t = conn.execute("SELECT COUNT(*), SUM(missing), MAX(mtime), MAX(added_at) FROM tracks").fetchone()
    key = (db_file, model_version, *f, *t)
    if not db_file:  # in-memory database: no stable identity
        return _load_library(conn, model_version)
    with _LIB_LOCK:
        hit = _LIB_CACHE.get(key)
    if hit is not None:
        return hit
    lib = _load_library(conn, model_version)
    with _LIB_LOCK:
        for k in [k for k in _LIB_CACHE if k[:2] == key[:2]]:
            del _LIB_CACHE[k]
        _LIB_CACHE[key] = lib
    return lib


def _load_library(conn: sqlite3.Connection, model_version: str) -> Library:
    rows = conn.execute(
        """
        SELECT t.id, t.artist_id, COALESCE(a.name, ''), t.album_id, t.title, t.duration_s, t.added_at,
               NULL, NULL, t.genre, f.embedding, f.dims, f.energy, f.bpm, f.danceability, f.tags
        FROM tracks t
        JOIN features f ON f.track_id = t.id AND f.model_version = ?
        LEFT JOIN artists a ON a.id = t.artist_id
        WHERE t.missing = 0
        ORDER BY t.id
        """,
        (model_version,),
    ).fetchall()
    good = []
    sizes: dict[int, int] = {}
    for r in rows:
        sizes[r[11]] = sizes.get(r[11], 0) + 1
    dims = max(sizes, key=lambda d: sizes[d]) if sizes else None  # majority wins if a version is mixed
    for r in rows:
        if r[11] != dims:
            continue
        try:
            v = from_blob(r[10], r[11])
        except ValueError:
            continue
        if not np.all(np.isfinite(v)):
            continue
        good.append((r, v))
    n = len(good)
    d = dims or 1
    raw = np.zeros((n, d), dtype=np.float32)
    for i, (_, v) in enumerate(good):
        raw[i] = v
    mean = raw.mean(axis=0) if n else np.zeros(d, np.float32)
    std = raw.std(axis=0) if n > 1 else np.ones(d, np.float32)
    std = np.where(std < 1e-6, 1.0, std).astype(np.float32)
    lib = Library(
        ids=[r[0] for r, _ in good], index={}, emb=raw, raw_mean=mean.astype(np.float32), raw_std=std,
        energy=np.zeros(n), energy_raw=np.array([r[12] if r[12] is not None else 0.5 for r, _ in good], dtype=float),
        bpm=np.array([r[13] or 0.0 for r, _ in good], dtype=float),
        dance=np.array([r[14] if r[14] is not None else 0.5 for r, _ in good], dtype=float),
        tags=[_json_dict(r[15]) for r, _ in good],
        artist=[r[1] for r, _ in good], artist_name=[r[2] for r, _ in good], album=[r[3] for r, _ in good],
        title=[r[4] for r, _ in good], duration=np.array([r[5] or 0.0 for r, _ in good], dtype=float),
        added_days=np.array([days_ago(parse_ts(r[6]), now_utc()) or 0.0 for r, _ in good], dtype=float),
        genre=[r[9] for r, _ in good],
        model_version=model_version,
    )
    lib.index = {tid: i for i, tid in enumerate(lib.ids)}
    if n:
        lib.emb = lib.normalise(raw)
        order = np.argsort(np.argsort(lib.energy_raw, kind="stable"), kind="stable")
        lib.energy = order / max(n - 1, 1)
    return lib


def _json_dict(s: str | None) -> dict[str, float]:
    if not s:
        return {}
    try:
        d = json.loads(s)
        return {str(k): float(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except (TypeError, ValueError):
        return {}


def top_tag(tags: dict[str, float], prefix: str = "genre:") -> str | None:
    cands = [(v, k) for k, v in tags.items() if k.startswith(prefix)]
    if not cands:
        return None
    label = max(cands)[1][len(prefix):]
    return label.split("---")[-1]  # Discogs styles look like "Rock---Indie Rock"


# ---------------------------------------------------------------- names

_PAREN = re.compile(r"[\(\[][^\)\]]*(edition|remaster|deluxe|version|expanded|anniversary|bonus|mono|stereo)"
                    r"[^\)\]]*[\)\]]", re.IGNORECASE)


def norm_name(s: str | None) -> str:
    """Normalise an artist or album name for fuzzy equality (no MBIDs yet)."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = _PAREN.sub(" ", s)
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.removeprefix("the ")
    return s


def days_ago(ts: datetime | None, now: datetime) -> float | None:
    return None if ts is None else max((now - ts) / timedelta(days=1), 0.0)
