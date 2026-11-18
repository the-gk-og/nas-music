"""Thin adapter over nasmusic.ai.api while the AI engine moves to the v2 (per-user) signatures of
docs/v2.md §3. It detects the installed signatures, so server core works before and after that merge.
Once the v2 engine is merged everywhere this module can shrink to direct calls."""
from __future__ import annotations

import inspect
import sqlite3

from nasmusic.ai import api as ai
from nasmusic.ai.api import AIConfig


def _takes_user(fn) -> bool:
    try:
        return "user_id" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def is_v2() -> bool:
    return hasattr(ai, "run_nightly_for_user") or _takes_user(ai.more_like)


def analyse_pending(conn: sqlite3.Connection, cfg: AIConfig) -> int:
    return ai.analyse_pending(conn, cfg)


def run_nightly(conn: sqlite3.Connection, cfg: AIConfig) -> dict[str, list[str]]:
    """{user_id: new mix ids}. A v1 engine's flat list is reported under '*'."""
    res = ai.run_nightly(conn, cfg)
    return dict(res) if isinstance(res, dict) else {"*": list(res or [])}


def run_nightly_for_user(conn: sqlite3.Connection, cfg: AIConfig, user_id: str) -> list[str]:
    if hasattr(ai, "run_nightly_for_user"):
        return list(ai.run_nightly_for_user(conn, cfg, user_id))
    return run_nightly(conn, cfg).get(user_id, [])


def more_like(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, track_id: str, minutes: int = 40) -> str:
    if _takes_user(ai.more_like):
        return ai.more_like(conn, cfg, user_id, track_id, minutes)
    return ai.more_like(conn, cfg, track_id, minutes)


def explain(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, track_id: str) -> tuple[str, list[str]]:
    if _takes_user(ai.explain):
        return ai.explain(conn, cfg, user_id, track_id)
    return ai.explain(conn, cfg, track_id)


def run_weekly_discovery(conn: sqlite3.Connection, cfg: AIConfig) -> dict[str, int]:
    res = ai.run_weekly_discovery(conn, cfg)
    return dict(res) if isinstance(res, dict) else {"*": int(res or 0)}
