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


# ---------- v3 (docs/v3-ux.md §5): radio + "because you played". Until the engine has them: stubs. ----------


def _call(name: str, *args, **kwargs):
    fn = getattr(ai, name, None)
    if fn is None:
        raise NotImplementedError(f"AI engine has no {name} yet")
    return fn(*args, **kwargs)


def _track_ids(res) -> list[str]:
    if isinstance(res, dict):
        res = res.get("trackIds") or res.get("tracks") or []
    return [t for t in (res or []) if isinstance(t, str)]


def radio_start(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, seed: dict, n: int = 25) -> list[str]:
    """Track ids for a new radio. Raises NotImplementedError until the engine has radio_start, KeyError
    for an unknown/unanalysed seed."""
    return _track_ids(_call("radio_start", conn, cfg, user_id, seed, n))


def radio_more(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, seed: dict, exclude: list[str],
               n: int = 25, queue_id: str | None = None) -> list[str]:
    fn = getattr(ai, "radio_more", None)
    if fn is None:
        raise NotImplementedError("AI engine has no radio_more yet")
    params = inspect.signature(fn).parameters
    kwargs = {k: v for k, v in (("queue_id", queue_id), ("seed", seed), ("exclude", exclude), ("n", n))
              if k in params}
    return _track_ids(fn(conn, cfg, user_id, **kwargs))


def because_you_played(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, limit: int = 3) -> list[dict]:
    """[{seed: {type, id, title}, trackIds: [...], albumIds?: [...]}]; [] while the engine lacks it."""
    fn = getattr(ai, "because_you_played", None)
    if fn is None:
        return []
    res = fn(conn, cfg, user_id, limit)
    return [r for r in (res or []) if isinstance(r, dict) and isinstance(r.get("seed"), dict)][:limit]
