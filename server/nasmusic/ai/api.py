"""The only entry points server core may call into the AI engine.

Owned by the AI engine. Server core imports these names and nothing else from `nasmusic.ai`.
Signatures are the contract; bodies delegate to the engine modules.

v2 (docs/v2.md §3): analysis is global, everything else is per user.

Error policy: `analyse_pending`, `run_nightly`, `run_nightly_for_user` and `run_weekly_discovery` never
raise (they log and return what they managed). `more_like` raises `KeyError` for an unknown user or an
unknown / not-yet-analysed track (server core maps it to 404). `explain` always answers.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("nasmusic.ai")


@dataclass(frozen=True)
class AIConfig:
    library_root: Path
    data_dir: Path                 # models, caches, snapshots
    model_version: str = "effnet-discogs-1"
    timezone: str = "Europe/London"
    lastfm_api_key: str | None = None


def analyse_pending(conn: sqlite3.Connection, cfg: AIConfig, limit: int | None = None) -> int:
    """Fingerprint tracks with no `features` row for cfg.model_version. Returns count analysed.
    Called after each library scan and nightly. Must be safe to interrupt and resume."""
    from dataclasses import replace

    from . import analyser, models
    from .common import BASIC_VERSION, ensure_default_settings, write_status
    from .extractors import effective_version

    requested = cfg.model_version
    try:
        ensure_default_settings(conn)
        version = effective_version(requested)
        reason = None if version == requested else "Essentia is not importable"
        if version != BASIC_VERSION:
            try:
                models.ensure_models(Path(cfg.data_dir), version)
            except Exception as e:
                log.exception("model files for %s unavailable; analysing with %s for now", version, BASIC_VERSION)
                reason = f"model files unavailable: {e}"[:300]
                version = BASIC_VERSION
        cfg = replace(cfg, model_version=version)
        write_status(conn, requested=requested, analysing=version, fallback_reason=reason)
        return analyser.analyse_pending(conn, cfg, limit)
    except Exception:
        log.exception("analyse_pending failed")
        return 0


def run_nightly(conn: sqlite3.Connection, cfg: AIConfig) -> dict[str, list[str]]:
    """Retrain and rebuild today's mixes for every active (non-disabled) user. Returns {user_id: mix ids}.

    Transitional: on a pre-migration v1 database (no `user_tracks`) this is the single-user v1 job and
    returns a plain list of mix ids, as v1 did."""
    from .store import Store

    try:
        store = Store(conn)
        if store.legacy:
            return run_nightly_for_user(conn, cfg, None)  # type: ignore[return-value]
        users = store.active_users()
    except Exception:
        log.exception("nightly run failed")
        return {}
    return {uid: run_nightly_for_user(conn, cfg, uid) for uid in users}


def run_nightly_for_user(conn: sqlite3.Connection, cfg: AIConfig, user_id: str) -> list[str]:
    """Ingest the user's feedback, retrain their taste model, deactivate their old mixes and write today's.
    Uses only that user's events and user_tracks (plus anonymous popularity while they are new)."""
    from .common import ensure_default_settings, write_status
    from .mixes import build_nightly
    from .recommender import Recommender
    from .store import Store

    try:
        ensure_default_settings(conn)
        store = Store(conn)
        if not store.user_exists(user_id):
            log.warning("nightly: unknown user %s", user_id)
            return []
        rec = Recommender(conn, cfg, user_id, store=store)
        write_status(conn, mixes=rec.version)
        if len(rec.lib):
            rec.train(save=True)
        return build_nightly(rec)
    except Exception:
        log.exception("nightly run failed for user %s", user_id)
        return []


def more_like(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, track_id: str, minutes: int = 40) -> str:
    """Build and store a `more_like` mix for `user_id` seeded by track_id. Returns the mix id.
    Raises KeyError if the user or track is unknown, or the track is not analysed yet."""
    from .mixes import build_more_like
    from .recommender import Recommender
    from .store import Store

    store = Store(conn)
    if store.legacy and not isinstance(track_id, str):  # transitional v1 call: (conn, cfg, track_id, minutes)
        user_id, track_id, minutes = None, user_id, (track_id if track_id is not None else minutes)
    if not store.user_exists(user_id):
        raise KeyError(f"unknown user {user_id}")
    minutes = max(10, min(int(minutes), 240))
    return build_more_like(Recommender(conn, cfg, user_id, store=store), track_id, minutes)


def explain(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, track_id: str | None = None
            ) -> tuple[str, list[str]]:
    """Return (reason text, up to 3 similar track ids) for this user.

    `track_id` only has a default for the transitional v1 call `explain(conn, cfg, track_id)` on a
    pre-migration database; v2 callers always pass both."""
    from .store import Store

    store = Store(conn)
    if track_id is None:
        user_id, track_id = None, user_id
    where, wvals = store.where_owner(user_id, "m")
    row = conn.execute(
        "SELECT reason FROM mix_tracks mt JOIN mixes m ON m.id = mt.mix_id "
        f"WHERE mt.track_id = ?{where} ORDER BY m.created_at DESC, m.rowid DESC LIMIT 1",
        (track_id, *wvals),
    ).fetchone()
    reason = row[0] if row else None
    similar: list[str] = []
    try:
        from .mixes import MixBuilder
        from .recommender import Recommender

        rec = Recommender(conn, cfg, user_id, store=store)
        i = rec.lib.index.get(track_id)
        if i is not None:
            similar = [rec.lib.ids[j] for j in rec.similar(i, 3)]
            if reason is None:
                rec.load_or_train()
                liked = rec.sig.weight[i] >= 0.75 or rec.sig.starred[i] or rec.sig.rating[i] >= 4
                reason = MixBuilder(rec).reason(i, "known" if liked else "rare")
    except Exception:
        log.exception("explain failed for %s", track_id)
    return (reason or "Picked from your library."), similar


def run_weekly_discovery(conn: sqlite3.Connection, cfg: AIConfig) -> dict[str, int]:
    """Refresh each active user's `suggestions`. Returns {user_id: number of new suggestions}.

    Transitional: on a pre-migration v1 database returns a plain int, as v1 did."""
    from . import discovery
    from .common import ensure_default_settings
    from .store import Store

    try:
        ensure_default_settings(conn)
        store = Store(conn)
        if store.legacy:
            return discovery.run(conn, cfg)  # type: ignore[return-value]
        users = store.active_users()
        http = discovery.Http()  # one client for all users, so rate limits and caches are shared
    except Exception:
        log.exception("weekly discovery failed")
        return 0 if Store(conn).legacy else {}  # type: ignore[return-value]
    out: dict[str, int] = {}
    for uid in users:
        try:
            out[uid] = discovery.run(conn, cfg, http=http, user_id=uid)
        except Exception:
            log.exception("weekly discovery failed for user %s", uid)
            out[uid] = 0
    return out


# ---------------------------------------------------------------- v3: radio and shelves (docs/v3-ux.md §3.2, §5)

def radio_start(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, seed: dict, context: dict | None = None,
                device_kind: str | None = None) -> tuple[str, list[str]]:
    """Start a radio for the user. `seed` = {"type": "random"|"track"|"artist"|"album"|"playlist", "id"?}, or
    (AI radio, v6) {"type": "moment"} (smart shuffle for this moment) or {"type": "vibe", "text"?, "constraints"?}
    (describe-a-vibe; constraints = chips from `radio_parse_vibe`, possibly edited; text is parsed when absent).
    `context` (moment): optional EventContext-like hints {localHour, weekday, deviceKind, output, motion, workout};
    `device_kind` = the registered device's kind. Returns (radio_id, ~25 track ids, ready to queue). The title
    ("<seed> radio", "Smart shuffle") is in radio_sessions.title. Raises KeyError for an unknown user / seed or
    nothing analysed / matching, ValueError for a malformed seed. No model training."""
    from . import radio
    from .store import Store

    if not Store(conn).user_exists(user_id):
        raise KeyError(f"unknown user {user_id}")
    return radio.start(conn, cfg, user_id, seed or {"type": "random"}, context=context, device_kind=device_kind)


def radio_steer(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, radio_id: str, action: str,
                track_id: str | None = None, scope: str = "track", ref_id: str | None = None,
                context: dict | None = None) -> dict:
    """Steer a radio's station (v6): action in more_like | less_like | never_here (need track_id; scope track |
    artist), more_energy | calmer | more_variety | more_familiar, remove (scope + ref_id: undo one entry), reset.
    Remembered per station and applied to every following radio_more / radio_refill. more/less/never are logged
    as recommender feedback. Returns radio_station(). KeyError: unknown radio / track; ValueError: bad action."""
    from . import radio

    return radio.steer(conn, cfg, user_id, radio_id, action, track_id, scope, ref_id, context)


def radio_refill(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, radio_id: str, drop_ids: list[str],
                 keep_ids: list[str], n: int = 25) -> list[str]:
    """After steering: put the queue's unplayed `drop_ids` back and return `n` fresh picks (avoiding `keep_ids`),
    so a steer is heard on the next track rather than after the queued batch. KeyError: unknown radio."""
    from . import radio

    return radio.refill(conn, cfg, user_id, radio_id, drop_ids, keep_ids, max(1, min(int(n), 100)))


def radio_station(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, radio_id: str) -> dict:
    """{radioId, kind, seedId, title, stationKey, explanation, text, constraints, context, steering
    {energy, variety, moreLike[], lessLike[], never[]}, steeringSummary}. KeyError: unknown / not yours."""
    from . import radio

    return radio.station(conn, cfg, user_id, radio_id)


def radio_parse_vibe(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, text: str) -> dict:
    """Describe-a-vibe text -> {text, title, constraints: [{id, kind, label, min?, max?, value?, weight?,
    exclude?, name?}]}. Lexicon + library vocabulary (artists, genre tags); CPU-cheap, stores nothing."""
    from . import radio

    return radio.parse_vibe(conn, cfg, user_id, text)


def radio_more(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, radio_id: str, exclude_ids: list[str],
               n: int = 25) -> list[str]:
    """Next `n` tracks for an endless radio: no repeats within the session, drifting gradually from the seed,
    steering away from what the user skipped during the session (reported as normal events).
    Raises KeyError if the radio doesn't exist or belongs to another user. May return fewer (or none) once
    a small library is exhausted."""
    from . import radio

    return radio.more(conn, cfg, user_id, radio_id, exclude_ids, max(1, min(int(n), 100)))


def because_you_played(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, limit_shelves: int = 3
                       ) -> list[dict]:
    """Up to `limit_shelves` shelves [{seed_track_id, title, track_ids}] of similar library tracks for the
    user's recent favourite plays. Only that user's own history. Never raises (returns [])."""
    from . import radio

    try:
        return radio.because_you_played(conn, cfg, user_id, max(0, min(int(limit_shelves), 10)))
    except Exception:
        log.exception("because_you_played failed for user %s", user_id)
        return []


# ---------------------------------------------------------------- discovery phase 2: Release Radar, Wrapped, Blend

def run_weekly_release_radar(conn: sqlite3.Connection, cfg: AIConfig) -> dict[str, int]:
    """Refresh every active user's Release Radar from MusicBrainz (≤ 1 request/s, batched by artist).
    Returns {user_id: releases}. Never raises; on network failure the previous week's cache is kept."""
    from . import release_radar
    from .common import ensure_default_settings

    try:
        ensure_default_settings(conn)
        return release_radar.run(conn, cfg)
    except Exception:
        log.exception("release radar failed")
        return {}


def release_radar(conn: sqlite3.Connection, cfg: AIConfig, user_id: str) -> dict:
    """{updatedAt, windowDays, lookaheadDays, releases: [{mbid, title, artist, artistMbid, artistId, type, date,
    coverUrl, score, upcoming}], recentlyAdded: [{albumId, artistId, addedAt}]}. Cheap (SQL only).
    Ownership of `releases` is for the caller to decide."""
    from . import release_radar as rr

    return rr.for_user(conn, user_id)


def wrapped(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, year: int) -> dict:
    """The user's listening year (so far, for the current year) from their own events. Track / artist / album
    entries are ids with plays and minutes; see nasmusic/ai/wrapped.py for the definitions."""
    from . import wrapped as w

    return w.user_wrapped(conn, user_id, year, w.user_timezone(conn, user_id, cfg.timezone))


def wrapped_server(conn: sqlite3.Connection, cfg: AIConfig, year: int) -> dict:
    """Server-wide aggregates for admins (no per-user breakdown)."""
    from . import wrapped as w

    return w.server_wrapped(conn, year, cfg.timezone)


def wrapped_years(conn: sqlite3.Connection, cfg: AIConfig, user_id: str | None) -> list[int]:
    """Years with plays (all users when user_id is None), newest first."""
    from . import wrapped as w

    return w.years(conn, user_id)


def build_blend(conn: sqlite3.Connection, cfg: AIConfig, blend_id: str) -> int:
    """Rebuild one blend's tracks and taste match from its joined members (fewer than 2 empties it).
    Returns the number of tracks. Raises KeyError for an unknown blend. Fast enough to call on join/leave."""
    from . import blend
    from .common import load_settings

    return blend.build(conn, cfg, blend_id, size=max(10, min(load_settings(conn).int("ai.blend.size"), 200)))


def run_blends(conn: sqlite3.Connection, cfg: AIConfig) -> dict[str, int]:
    """Nightly refresh of every blend. Never raises. Returns {blend_id: tracks}."""
    from . import blend
    from .common import load_settings

    try:
        return blend.build_all(conn, cfg, max(10, min(load_settings(conn).int("ai.blend.size"), 200)))
    except Exception:
        log.exception("blend refresh failed")
        return {}


def likely_next(conn: sqlite3.Connection, cfg: AIConfig, user_id: str) -> dict[str, float]:
    """How likely the user is to want each library track soon, for smart downloads: {track_id: p} with p in
    (0, 1], the percentile rank of the recommender's score (1 = most likely). Blocked tracks are left out.
    Uses the latest taste snapshot (trains in memory, without saving, if there is none). Never raises;
    returns {} for an unknown user or an empty / unanalysed library."""
    import numpy as np

    from .recommender import Recommender
    from .store import Store

    try:
        store = Store(conn)
        if not store.user_exists(user_id):
            return {}
        rec = Recommender(conn, cfg, user_id, store=store)
        if not len(rec.lib):
            return {}
        s = rec.scores()
        ok = np.isfinite(s)
        n = int(ok.sum())
        if n == 0:
            return {}
        idx = np.flatnonzero(ok)
        order = idx[np.argsort(-s[idx], kind="stable")]
        return {rec.lib.ids[int(j)]: round(1.0 - rank / n, 4) for rank, j in enumerate(order)}
    except Exception:
        log.exception("likely_next failed for user %s", user_id)
        return {}


def similar_artists(conn: sqlite3.Connection, cfg: AIConfig, artist_id: str, limit: int = 12
                    ) -> list[tuple[str, float]]:
    """(v7 artist pages) Library artists that sound most like `artist_id`: [(artist_id, similarity 0..1)],
    best first, from the mean audio embedding of each artist's analysed tracks. Shared (not per user): the
    server filters the result for the user (family mode). Never raises; [] when the artist isn't analysed."""
    import numpy as np

    from .common import active_version, load_library
    from .extractors import effective_version

    try:
        lib = load_library(conn, active_version(conn, effective_version(cfg.model_version)))
        if not len(lib):
            return []
        artists = np.array(lib.artist)
        mine = artists == artist_id
        if not mine.any():
            return []
        uniq, inv = np.unique(artists, return_inverse=True)
        sums = np.zeros((len(uniq), lib.emb.shape[1]), dtype=np.float64)
        np.add.at(sums, inv, lib.emb)
        counts = np.bincount(inv, minlength=len(uniq)).astype(np.float64)
        cent = sums / counts[:, None]
        cent /= np.maximum(np.linalg.norm(cent, axis=1, keepdims=True), 1e-9)
        me = int(np.flatnonzero(uniq == artist_id)[0])
        sim = cent @ cent[me]
        sim[me] = -np.inf
        order = np.argsort(-sim, kind="stable")[:limit]
        return [(str(uniq[j]), round(float((sim[j] + 1) / 2), 4)) for j in order if np.isfinite(sim[j])]
    except Exception:
        log.exception("similar_artists failed for %s", artist_id)
        return []
