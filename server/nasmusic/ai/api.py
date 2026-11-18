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
