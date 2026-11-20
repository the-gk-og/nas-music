"""Mix builder: Daily, Morning, Evening, Deep Cuts (nightly) and More-like (on demand).

Rules for every mix: no artist twice within `ai.mix.artist_gap` tracks, nothing played in the last
`ai.mix.recent_days` days, no blocked (thumbs-down) tracks. Order is a nearest-neighbour walk in
embedding space that follows a smooth energy curve. Every track gets a short reason.
"""
from __future__ import annotations

import logging
import math
import sqlite3
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from . import context as cx
from .common import iso, local_tz, new_id, top_tag, transaction
from .recommender import Recommender
from .store import Store

log = logging.getLogger("nasmusic.ai.mixes")

NIGHTLY_KINDS = ("daily", "morning", "evening", "deep_cuts")
MORE_LIKE_MAX_ACTIVE = 3


@dataclass(frozen=True)
class MixSpec:
    kind: str
    title: str
    minutes_key: str
    hour: int
    tod: str | None
    curve: str


SPECS = {
    "daily": MixSpec("daily", "Daily Mix", "ai.mix.daily.minutes", 14, None, "arc"),
    "morning": MixSpec("morning", "Morning Mix", "ai.mix.morning.minutes", 8, "morning", "rise"),
    "evening": MixSpec("evening", "Evening Mix", "ai.mix.evening.minutes", 20, "evening", "fall"),
    "deep_cuts": MixSpec("deep_cuts", "Deep Cuts", "ai.mix.deep_cuts.minutes", 14, None, "arc"),
}


CONTEXT_CURVES = {"workout": "high", "drive": "arc", "late_night": "fall"}
CONTEXT_HOURS = {"workout": 18, "drive": 17, "late_night": 23}


def context_spec(key: str) -> MixSpec:
    """A context mix ('workout', 'drive', 'late_night', 'place:<name>'), stored with kind='context'."""
    return MixSpec("context", cx.mix_title(key), "ai.mix.context.minutes", CONTEXT_HOURS.get(key, 14), key,
                   CONTEXT_CURVES.get(key, "arc"))


def _short(s: str, n: int = 40) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def energy_target(curve: str, t: float, start: float = 0.5) -> float:
    """Target energy percentile (within the mix) at position t in [0, 1]."""
    if curve == "rise":
        return 0.15 + 0.75 * t
    if curve == "fall":
        return 0.85 - 0.75 * t
    if curve == "high":  # workout: start lively, stay high
        return 0.55 + 0.35 * min(t / 0.2, 1.0)
    if curve == "arc":  # warm up, peak around 60 %, ease off
        return 0.25 + 0.6 * math.sin(math.pi * min(t / 1.2, 1.0) ** 0.9)
    return start  # 'flat': stay near the seed


class MixBuilder:
    def __init__(self, rec: Recommender, today: str | None = None):
        self.rec = rec
        self.lib = rec.lib
        self.sig = rec.sig
        self.s = rec.settings
        tz = local_tz(rec.cfg.timezone)
        self.local_now = rec.now.astimezone(tz)
        self.today = today or self.local_now.date().isoformat()
        # stable across processes (str hash() is salted): same user + day -> same mixes
        self.rng = np.random.default_rng(zlib.crc32(f"{self.today}:{rec.user_id}".encode()))
        self.gap = max(self.s.int("ai.mix.artist_gap"), 1)
        recent = self.s.num("ai.mix.recent_days")
        self.eligible = (~self.sig.excluded) & ~(self.sig.last_played_days < recent)
        liked = np.where(self.sig.weight >= 1.0)[0]
        self.liked = liked

    # ------------------------------------------------------------ selection

    def _per_artist_cap(self, target_tracks: int) -> int:
        return max(2, target_tracks // 12)

    def _take(self, ranked: np.ndarray, seconds: float, chosen: list[int], counts: dict[str, int],
              cap: int) -> float:
        got = 0.0
        taken = set(chosen)
        for i in ranked:
            if got >= seconds - 30:
                break
            i = int(i)
            a = self.lib.artist[i]
            d = max(self.lib.duration[i], 30.0)
            if i in taken or counts.get(a, 0) >= cap or got + d > seconds + 60:
                continue
            chosen.append(i)
            taken.add(i)
            counts[a] = counts.get(a, 0) + 1
            got += d
        return got

    def _noisy_rank(self, idx: np.ndarray, score: np.ndarray, noise: float = 0.05) -> np.ndarray:
        if idx.size == 0:
            return idx
        jitter = self.rng.gumbel(size=idx.size) * noise
        return idx[np.argsort(-(score[idx] + jitter))]

    def _diverse_rank(self, idx: np.ndarray, score: np.ndarray, noise: float = 0.05) -> np.ndarray:
        """New users: alternate between the library-wide taste centres, so a cold-start mix samples the
        whole range of sounds instead of the single most popular corner."""
        ranked = self._noisy_rank(idx, score, noise)
        m = self.rec.load_or_train()
        if ranked.size == 0 or m.centroids.shape[0] < 2:
            return ranked
        groups = np.argmax(self.lib.emb[ranked] @ m.centroids.T, axis=1)
        queues = [list(ranked[groups == g]) for g in range(m.centroids.shape[0])]
        out = []
        while any(queues):
            for q in queues:
                if q:
                    out.append(q.pop(0))
        return np.array(out, dtype=ranked.dtype)

    def select(self, spec: MixSpec, used: set[int]) -> tuple[list[int], dict[int, str]]:
        seconds = self.s.num(spec.minutes_key) * 60
        avg = float(np.median(self.lib.duration)) if len(self.lib) else 240.0
        cap = self._per_artist_cap(int(seconds / max(avg, 60)))
        score = self.rec.scores(hour=spec.hour, weekday=self.local_now.weekday(), tod=spec.tod)
        sound = self.rec.load_or_train().sound_score(self.lib.emb)
        pool = self.eligible & np.isfinite(score)
        unused = pool.copy()
        if used:
            unused[list(used)] = False
        dur = self.lib.duration
        if dur[unused].sum() >= seconds:  # small libraries may have to reuse tracks across mixes
            pool = unused
        plays = self.sig.plays
        chosen: list[int] = []
        bucket: dict[int, str] = {}
        counts: dict[str, int] = {}

        def take(mask: np.ndarray, rank_by: np.ndarray, until: float, name: str, noise: float = 0.05):
            """Add tracks from `mask` until the mix holds `until` seconds in total."""
            before = len(chosen)
            secs = until - float(sum(max(dur[i], 30.0) for i in chosen))
            if secs <= 30:
                return 0.0
            rank = self._diverse_rank if self.rec.is_new_user else self._noisy_rank
            got = self._take(rank(np.where(mask)[0], rank_by, noise), secs, chosen, counts, cap)
            for i in chosen[before:]:
                bucket[i] = name
            return got

        if spec.kind == "deep_cuts":
            mask = pool & (plays <= self.s.num("ai.mix.deep_cuts.max_plays")) & (self.sig.weight < 1.0)
            take(mask, sound, seconds, "deep")
        else:
            known = pool & ((self.sig.weight >= 0.75) | self.sig.starred | (self.sig.rating >= 4))
            if spec.kind == "context":  # what you actually play in this context counts as "known"
                known &= self.sig.ctx_weight.get(spec.tod, np.zeros(len(self.lib))) > 0.25
            rare = pool & ~known & (plays <= self.s.num("ai.mix.rare_max_plays"))
            kp, rp = self.s.num("ai.mix.known_pct"), self.s.num("ai.mix.rare_pct")
            ep = self.s.num("ai.mix.explore_pct")
            total = max(kp + rp + ep, 1e-9)
            take(known, score, seconds * kp / total, "known")
            take(rare, score, seconds * (kp + rp) / total, "rare")
            # exploration: middle-of-the-road sound match, ranked with lots of noise
            rest = pool.copy()
            rest[chosen] = False
            if rest.any():
                lo, hi = np.percentile(sound[rest], [20, 75])
                explore = rest & (sound >= lo) & (sound <= hi)
                take(explore, -np.abs(sound - (lo + hi) / 2), seconds, "explore", noise=0.5)
        # backfill (e.g. cold start: no known likes yet)
        rest = pool.copy()
        rest[chosen] = False
        fill_name = "rare" if spec.kind != "deep_cuts" else "deep"
        take(rest, score, seconds, fill_name)
        # spares for top_up(), in case ordering has to drop tracks to keep the artist rule
        rest[chosen] = False
        if spec.kind == "deep_cuts":
            rest &= plays <= self.s.num("ai.mix.deep_cuts.max_plays")
        self._spares = (list(self._noisy_rank(np.where(rest)[0], score)), fill_name, seconds, cap)
        return chosen, bucket

    def top_up(self, ordered: list[int], bucket: dict[int, str]) -> list[int]:
        """Refill a mix that ordering left short, appending spares that keep the artist rule."""
        spares, name, seconds, cap = self._spares
        dur = self.lib.duration
        total = float(sum(max(dur[i], 30.0) for i in ordered))
        counts: dict[str, int] = {}
        for i in ordered:
            counts[self.lib.artist[i]] = counts.get(self.lib.artist[i], 0) + 1
        taken = set(ordered)
        for i in spares:
            if total >= seconds - 30:
                break
            a = self.lib.artist[i]
            d = max(dur[i], 30.0)
            if i in taken or counts.get(a, 0) >= cap or total + d > seconds + 60:
                continue
            if a in {self.lib.artist[j] for j in ordered[-self.gap:]}:
                continue
            ordered.append(i)
            taken.add(i)
            bucket.setdefault(i, name)
            counts[a] = counts.get(a, 0) + 1
            total += d
        return ordered

    # ------------------------------------------------------------ ordering

    def order(self, idx: list[int], curve: str, seed: int | None = None) -> list[int]:
        """Nearest-neighbour walk with a smooth energy curve; enforces the artist gap.
        Tracks that cannot be placed without breaking the artist rule are dropped."""
        if not idx:
            return []
        remaining = list(dict.fromkeys(idx))
        e = self.lib.energy
        emb = self.lib.emb
        sel_e = np.sort(e[remaining])
        n = len(remaining)

        def target(pos: int) -> float:
            t = pos / max(n - 1, 1)
            q = energy_target(curve, t, start=float(e[seed]) if seed is not None else 0.5)
            return float(np.quantile(sel_e, np.clip(q, 0, 1)))

        if seed is not None and seed in remaining:
            first = seed
        else:
            t0 = target(0)
            first = min(remaining, key=lambda i: abs(e[i] - t0))
        out = [first]
        remaining.remove(first)
        while remaining:
            prev = out[-1]
            recent_artists = {self.lib.artist[j] for j in out[-(self.gap):]}
            allowed = [i for i in remaining if self.lib.artist[i] not in recent_artists]
            if not allowed:
                break
            cand = np.array(allowed)
            tgt = target(len(out))
            # prefer artists with more tracks still to place, so none are stranded at the end
            left = np.array([sum(self.lib.artist[j] == self.lib.artist[i] for j in remaining) for i in allowed])
            cost = (1 - emb[cand] @ emb[prev]) + 1.5 * np.abs(e[cand] - tgt) + 0.5 * np.abs(e[cand] - e[prev]) \
                - 0.25 * (left - 1)
            nxt = int(cand[int(np.argmin(cost))])
            out.append(nxt)
            remaining.remove(nxt)
        if remaining:
            log.debug("dropped %d tracks that would break the artist rule", len(remaining))
        return out

    # ------------------------------------------------------------ reasons

    def _nearest_liked(self, i: int) -> int | None:
        if self.liked.size == 0:
            return None
        cands = self.liked[[self.lib.album[j] != self.lib.album[i] for j in self.liked]]
        if cands.size == 0:
            return None
        sims = self.lib.emb[cands] @ self.lib.emb[i]
        j = int(np.argmax(sims))
        return int(cands[j]) if sims[j] > 0.2 else None

    def reason(self, i: int, bucket: str, spec: MixSpec | None = None, seed: int | None = None) -> str:
        lib, sig = self.lib, self.sig
        plays = int(sig.plays[i])
        days = sig.last_played_days[i]
        if bucket == "seed":
            return "The track you started from"
        if bucket == "more_like" and seed is not None:
            return f"Sounds like {_short(lib.title[seed])} by {_short(lib.artist_name[seed], 30)}"
        if bucket == "known":
            if spec and spec.tod and sig.ctx_weight.get(spec.tod, np.zeros(len(lib)))[i] > 0.5:
                return cx.reason_phrase(spec.tod)
            if sig.starred[i]:
                if np.isfinite(days) and days >= 14:
                    return f"A favourite you haven't heard in {int(days // 7)} weeks"
                return "One of your favourites"
            if sig.rating[i] >= 4:
                return f"You rated this {int(sig.rating[i])} stars"
            if plays >= 2:
                return f"You've played this through {plays} times"
            if plays == 1:
                return "You played this all the way through"
            return "You liked this recently"
        near = self._nearest_liked(i)
        like = (f"sounds like {_short(lib.title[near])} by {_short(lib.artist_name[near], 30)}"
                if near is not None else None)
        if bucket == "deep":
            head = "Never played" if plays == 0 else f"Played only {plays} time{'s' if plays > 1 else ''}"
            return f"{head}; {like}" if like else f"{head}, and close to your taste"
        if bucket == "explore":
            label = top_tag(lib.tags[i]) or lib.genre[i]
            if label:
                return f"Something different: {_short(label, 30)} you haven't explored much"
            return "A wildcard from your library you haven't played much"
        # rare
        head = "Not played yet" if plays == 0 else "Rarely played"
        if like:
            return f"{head}, but {like}"
        model = self.rec.load_or_train()
        if self.rec.is_new_user:  # library-wide cold start: say which library-wide signal picked it
            if self.rec.popularity[i] >= 3:
                return "Popular with listeners on this server"
            if lib.added_days[i] <= 30:
                return "Recently added to the library"
            j, _ = model.nearest_centroid(lib.emb[i])
            label = model.centroid_labels[j] if j >= 0 else None
            return f"A taste of the library's {_short(label, 30)}" if label else "A taste of the library"
        j, sim = model.nearest_centroid(lib.emb[i])
        if j >= 0 and sim > 0.2:
            return f"{head}, close to your {_short(model.centroid_labels[j], 30)} favourites"
        return f"{head}, picked for you"

    # ------------------------------------------------------------ writing

    def write(self, kind: str, title: str, ordered: list[int], bucket: dict[int, str], score: np.ndarray,
              spec: MixSpec | None = None, seed: int | None = None) -> str:
        mid = new_id()
        # everything is computed before the write transaction, which then only holds the lock for inserts
        rows = [(mid, pos, self.lib.ids[i], float(score[i]) if np.isfinite(score[i]) else None,
                 self.reason(i, bucket.get(i, "rare"), spec, seed)) for pos, i in enumerate(ordered)]
        store, uid = self.rec.store, self.rec.user_id
        cols, marks, vals = store.owner(uid)
        with transaction(self.rec.conn) as c:
            if kind == "more_like":
                retire_more_like(c, self.rec.now, keep_active=MORE_LIKE_MAX_ACTIVE - 1, store=store, user_id=uid)
            c.execute(f"INSERT INTO mixes (id, kind, title, for_date, created_at, seed_track, active{cols}) "
                      f"VALUES (?,?,?,?,?,?,1{marks})",
                      (mid, kind, title, self.today, iso(self.rec.now),
                       self.lib.ids[seed] if seed is not None else None, *vals))
            c.executemany("INSERT INTO mix_tracks (mix_id, position, track_id, score, reason) VALUES (?,?,?,?,?)",
                          rows)
        return mid


def retire_more_like(conn: sqlite3.Connection, now: datetime, keep_active: int = MORE_LIKE_MAX_ACTIVE,
                     store: Store | None = None, user_id: str | None = None) -> None:
    """On-demand mixes go into the phone's sync manifest while active, so they must not pile up:
    deactivate the user's ones older than 24 h and all but the newest `keep_active`."""
    where, w = (store or Store(conn)).where_owner(user_id)
    conn.execute(f"UPDATE mixes SET active = 0 WHERE active = 1 AND kind = 'more_like'{where} AND created_at < ?",
                 (*w, iso(now - timedelta(days=1))))
    conn.execute(
        f"UPDATE mixes SET active = 0 WHERE active = 1 AND kind = 'more_like'{where} AND id NOT IN ("
        f"SELECT id FROM mixes WHERE active = 1 AND kind = 'more_like'{where} "
        "ORDER BY created_at DESC, rowid DESC LIMIT ?)", (*w, *w, max(keep_active, 0)))


def deactivate_old(conn: sqlite3.Connection, now: datetime, keep_days: float, store: Store | None = None,
                   user_id: str | None = None) -> None:
    """Nightly, per user: yesterday's mixes (nightly and context) and every on-demand mix go inactive."""
    where, w = (store or Store(conn)).where_owner(user_id)
    with transaction(conn):
        conn.execute(f"UPDATE mixes SET active = 0 WHERE active = 1{where}", w)
        conn.execute(f"DELETE FROM mixes WHERE active = 0{where} AND created_at < ?",
                     (*w, iso(now - timedelta(days=keep_days))))


def context_mix_keys(rec: Recommender) -> list[str]:
    """Contexts with enough plays for their own mix, most played first."""
    need = rec.settings.num("ai.mix.context.min_plays")
    keys = [k for k, n in rec.sig.ctx_plays.items() if k not in ("morning", "evening") and n >= need]
    keys.sort(key=lambda k: (-rec.sig.ctx_plays[k], k))
    return keys[: max(rec.settings.int("ai.mix.context.max"), 0)]


def build_nightly(rec: Recommender) -> list[str]:
    deactivate_old(rec.conn, rec.now, rec.settings.num("ai.mix.keep_days"), rec.store, rec.user_id)
    if len(rec.lib) == 0:
        log.info("no analysed tracks yet; no mixes built")
        return []
    b = MixBuilder(rec)
    used: set[int] = set()
    ids = []
    specs = [SPECS[k] for k in NIGHTLY_KINDS] + [context_spec(k) for k in context_mix_keys(rec)]
    for spec in specs:
        try:
            # context mixes may share tracks with the time-of-day mixes: they're for a different moment
            chosen, bucket = b.select(spec, set() if spec.kind == "context" else used)
            ordered = b.top_up(b.order(chosen, spec.curve), bucket)
            if not ordered:
                log.info("%s: nothing eligible", spec.title)
                continue
            score = rec.scores(hour=spec.hour, tod=spec.tod)
            try:
                ids.append(b.write(spec.kind, spec.title, ordered, bucket, score, spec))
            except sqlite3.IntegrityError as e:
                if spec.kind != "context":
                    raise
                log.warning("cannot store context mix %r (does mixes.kind allow 'context'?): %s", spec.title, e)
                continue
            if spec.kind != "context":
                used.update(ordered)
            log.info("%s: %d tracks, %.0f min", spec.title, len(ordered), rec.lib.duration[ordered].sum() / 60)
        except Exception:
            log.exception("building %s mix failed", spec.title)
    return ids


def build_more_like(rec: Recommender, track_id: str, minutes: int) -> str:
    lib = rec.lib
    if track_id not in lib.index:
        raise KeyError(f"track {track_id} has not been analysed yet")
    seed = lib.index[track_id]
    b = MixBuilder(rec)
    sims = lib.emb @ lib.emb[seed]
    score = rec.scores(hour=b.local_now.hour)
    fin = np.isfinite(score)
    z = np.zeros_like(score)
    if fin.any():
        z[fin] = (score[fin] - score[fin].mean()) / (score[fin].std() + 1e-9)
    combined = np.where(fin, 0.75 * sims + 0.25 * np.tanh(z / 2), -np.inf)
    pool = b.eligible & fin
    pool[seed] = False
    seconds = minutes * 60
    cap = b._per_artist_cap(int(seconds / max(float(np.median(lib.duration)), 60)))
    chosen = [seed]
    counts = {lib.artist[seed]: 1}
    b._take(np.where(pool)[0][np.argsort(-combined[pool])], seconds - lib.duration[seed], chosen, counts, cap)
    ordered = b.order(chosen, "flat", seed=seed)
    bucket = {i: "more_like" for i in ordered}
    bucket[seed] = "seed"
    return b.write("more_like", f"More like {_short(lib.title[seed])}", ordered, bucket, combined, seed=seed)
