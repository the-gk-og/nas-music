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


def _ids(res) -> list[str]:
    return [t for t in (res or []) if isinstance(t, str)]


def radio_start(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, seed: dict, context: dict | None = None,
                device_kind: str | None = None) -> tuple[str, list[str]]:
    """(radio_id, ~25 track ids). The radio's display name is radio_sessions.title. Raises NotImplementedError
    without the v3 engine, KeyError for unknown/unanalysed seeds or users, ValueError for a malformed seed."""
    clean = {k: v for k, v in seed.items() if k in ("type", "id", "text", "constraints")}
    if seed.get("type") in ("moment", "vibe"):
        radio_id, ids = _call("radio_start", conn, cfg, user_id, clean, context=context, device_kind=device_kind)
    else:
        radio_id, ids = _call("radio_start", conn, cfg, user_id, {k: v for k, v in clean.items() if k in ("type", "id")})
    return radio_id, _ids(ids)


def radio_steer(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, radio_id: str, action: str,
                track_id: str | None, scope: str, ref_id: str | None, context: dict | None) -> dict:
    return dict(_call("radio_steer", conn, cfg, user_id, radio_id, action, track_id, scope, ref_id, context))


def radio_refill(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, radio_id: str, drop: list[str],
                 keep: list[str], n: int) -> list[str]:
    return _ids(_call("radio_refill", conn, cfg, user_id, radio_id, drop, keep, n))


def radio_station(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, radio_id: str) -> dict:
    return dict(_call("radio_station", conn, cfg, user_id, radio_id))


def radio_parse_vibe(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, text: str) -> dict:
    return dict(_call("radio_parse_vibe", conn, cfg, user_id, text))


def radio_more(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, radio_id: str, exclude: list[str],
               n: int = 25) -> list[str]:
    """More ids for radio `radio_id` (the AI's id, not the queue's); [] once the library runs out."""
    return _ids(_call("radio_more", conn, cfg, user_id, radio_id, exclude, n))


def because_you_played(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, limit: int = 3) -> list[dict]:
    """[{seed_track_id, title, track_ids}]; [] while the engine lacks it (it never raises)."""
    fn = getattr(ai, "because_you_played", None)
    if fn is None:
        return []
    res = fn(conn, cfg, user_id, limit)
    return [r for r in (res or []) if isinstance(r, dict) and isinstance(r.get("track_ids"), list)][:limit]


# ---------- discovery phase 2: Release Radar, Wrapped, Blend ----------


def run_weekly_release_radar(conn: sqlite3.Connection, cfg: AIConfig) -> dict[str, int]:
    return dict(_call("run_weekly_release_radar", conn, cfg) or {})


def release_radar(conn: sqlite3.Connection, cfg: AIConfig, user_id: str) -> dict:
    return _call("release_radar", conn, cfg, user_id)


def wrapped(conn: sqlite3.Connection, cfg: AIConfig, user_id: str, year: int) -> dict:
    return _call("wrapped", conn, cfg, user_id, year)


def wrapped_server(conn: sqlite3.Connection, cfg: AIConfig, year: int) -> dict:
    return _call("wrapped_server", conn, cfg, year)


def wrapped_years(conn: sqlite3.Connection, cfg: AIConfig, user_id: str | None) -> list[int]:
    return list(_call("wrapped_years", conn, cfg, user_id) or [])


def build_blend(conn: sqlite3.Connection, cfg: AIConfig, blend_id: str) -> int:
    return int(_call("build_blend", conn, cfg, blend_id) or 0)


def run_blends(conn: sqlite3.Connection, cfg: AIConfig) -> dict[str, int]:
    return dict(_call("run_blends", conn, cfg) or {})


# ---------- smart downloads ----------


def likely_next(conn: sqlite3.Connection, cfg: AIConfig, user_id: str) -> dict[str, float]:
    """{track_id: likelihood 0..1} for smart downloads; {} while the engine lacks it (never raises)."""
    fn = getattr(ai, "likely_next", None)
    if fn is None:
        return {}
    try:
        res = fn(conn, cfg, user_id)
    except Exception:
        return {}
    return {k: float(v) for k, v in (res or {}).items() if isinstance(k, str)}


# ---------- artist pages (v7) ----------


def similar_artists(conn: sqlite3.Connection, cfg: AIConfig, artist_id: str, limit: int = 12) -> list[tuple[str, float]]:
    """[(artist_id, similarity)] from the AI's embeddings; [] when the engine lacks it (never raises)."""
    fn = getattr(ai, "similar_artists", None)
    if fn is None:
        return []
    try:
        return [(str(a), float(s)) for a, s in fn(conn, cfg, artist_id, limit) or []]
    except Exception:
        return []
