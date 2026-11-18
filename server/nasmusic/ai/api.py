"""The only entry points server core may call into the AI engine.

Owned by the AI engine. Server core imports these names and nothing else from `nasmusic.ai`.
The stub bodies below are deliberately simple so server core can be built and tested before the
real engine lands; the AI engine replaces them, keeping the signatures.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


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
    return 0


def run_nightly(conn: sqlite3.Connection, cfg: AIConfig) -> list[str]:
    """Ingest feedback, retrain, deactivate yesterday's mixes, write today's mixes. Returns new mix ids."""
    return []


def more_like(conn: sqlite3.Connection, cfg: AIConfig, track_id: str, minutes: int = 40) -> str:
    """Build and store a `more_like` mix seeded by track_id. Returns the mix id."""
    raise NotImplementedError("AI engine not installed yet")


def explain(conn: sqlite3.Connection, cfg: AIConfig, track_id: str) -> tuple[str, list[str]]:
    """Return (reason text, up to 3 similar track ids)."""
    row = conn.execute(
        "SELECT reason FROM mix_tracks mt JOIN mixes m ON m.id = mt.mix_id "
        "WHERE mt.track_id = ? ORDER BY m.created_at DESC LIMIT 1",
        (track_id,),
    ).fetchone()
    return (row[0] if row else "Picked from your library."), []


def run_weekly_discovery(conn: sqlite3.Connection, cfg: AIConfig) -> int:
    """Refresh the `suggestions` table. Returns number of new suggestions."""
    return 0
