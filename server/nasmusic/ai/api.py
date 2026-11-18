"""The only entry points server core may call into the AI engine.

Owned by the AI engine. Server core imports these names and nothing else from `nasmusic.ai`.
Signatures are the contract; bodies delegate to the engine modules.

Error policy: `analyse_pending`, `run_nightly` and `run_weekly_discovery` never raise (they log and
return what they managed). `more_like` raises `KeyError` for an unknown / not-yet-analysed track
(server core maps it to 404). `explain` always answers.
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


def run_nightly(conn: sqlite3.Connection, cfg: AIConfig) -> list[str]:
    """Ingest feedback, retrain, deactivate yesterday's mixes, write today's mixes. Returns new mix ids."""
    from .common import ensure_default_settings, write_status
    from .mixes import build_nightly
    from .recommender import Recommender

    try:
        ensure_default_settings(conn)
        rec = Recommender(conn, cfg)
        write_status(conn, mixes=rec.version)
        if len(rec.lib):
            rec.train(save=True)
        return build_nightly(rec)
    except Exception:
        log.exception("nightly run failed")
        return []


def more_like(conn: sqlite3.Connection, cfg: AIConfig, track_id: str, minutes: int = 40) -> str:
    """Build and store a `more_like` mix seeded by track_id. Returns the mix id."""
    from .mixes import build_more_like
    from .recommender import Recommender

    minutes = max(10, min(int(minutes), 240))
    return build_more_like(Recommender(conn, cfg), track_id, minutes)


def explain(conn: sqlite3.Connection, cfg: AIConfig, track_id: str) -> tuple[str, list[str]]:
    """Return (reason text, up to 3 similar track ids)."""
    row = conn.execute(
        "SELECT reason FROM mix_tracks mt JOIN mixes m ON m.id = mt.mix_id "
        "WHERE mt.track_id = ? ORDER BY m.created_at DESC LIMIT 1",
        (track_id,),
    ).fetchone()
    reason = row[0] if row else None
    similar: list[str] = []
    try:
        from .mixes import MixBuilder
        from .recommender import Recommender

        rec = Recommender(conn, cfg)
        i = rec.lib.index.get(track_id)
        if i is not None:
            similar = [rec.lib.ids[j] for j in rec.similar(i, 3)]
            if reason is None:
                rec.load_or_train()
                liked = rec.sig.weight[i] >= 0.75 or rec.lib.starred[i] or rec.lib.rating[i] >= 4
                reason = MixBuilder(rec).reason(i, "known" if liked else "rare")
    except Exception:
        log.exception("explain failed for %s", track_id)
    return (reason or "Picked from your library."), similar


def run_weekly_discovery(conn: sqlite3.Connection, cfg: AIConfig) -> int:
    """Refresh the `suggestions` table. Returns number of new suggestions."""
    from . import discovery
    from .common import ensure_default_settings

    try:
        ensure_default_settings(conn)
        return discovery.run(conn, cfg)
    except Exception:
        log.exception("weekly discovery failed")
        return 0
