"""Listening context (`events.context`, docs/v2.md §10): parsing, context mixes, extra signals.

Every field is optional; missing means unknown. A field whose collection switch the user has
turned off (`user_settings[<switch key>] == false`) is ignored even if old events still carry it.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Switch keys in `user_settings` -> the context fields they cover. The spec names the switches but
# not their keys; these are the AI engine's assumption (see README, "Spec gaps").
SWITCHES: dict[str, tuple[str, ...]] = {
    "context.listening_details": ("seeks", "volumeChanges", "repeatOfPrevious", "addedFrom", "sessionId",
                                  "sessionPosition"),
    "context.audio_output": ("output",),
    "context.device_network": ("deviceKind", "network", "charging", "batteryPct", "screenOn"),
    "context.movement": ("motion", "cadenceSpm"),
    "context.location": ("place", "geohash5"),
    "context.weather": ("weather",),
    "context.time": ("localHour", "weekday", "holiday"),
}

CATEGORICAL = ("output", "motion", "place", "network", "weather")
_AUTO_PLACE = re.compile(r"^place-\d+$")
_WORKOUT_PLACE = re.compile(r"gym|fitness|workout|sport|pool|climb", re.IGNORECASE)


@dataclass
class Context:
    hour: int
    weekday: int
    output: str | None = None        # speaker / wired / bluetooth / cast / car
    output_name: str | None = None   # "bluetooth:<device name>" -> device name
    motion: str | None = None        # still / walking / running / vehicle
    place: str | None = None         # user-named cluster label
    network: str | None = None
    weather: str | None = None       # condition
    temp_c: float | None = None
    repeat: bool = False
    seeks: list[tuple[float, float]] = field(default_factory=list)   # (from_s, to_s)
    volume_deltas: list[float] = field(default_factory=list)


def disabled_fields(user_settings: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for key, fields_ in SWITCHES.items():
        if user_settings.get(key) is False:
            out.update(fields_)
    return out


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _seconds(d: dict, key: str) -> float | None:
    for k, scale in ((key, 1.0), (key + "S", 1.0), (key + "Ms", 0.001), (key + "_s", 1.0)):
        if k in d:
            v = _num(d[k])
            if v is not None:
                return v * scale
    return None


def parse(raw: str | dict | None, local: datetime, disabled: set[str] = frozenset()) -> Context:
    """Parse one event's context. `local` is the event time in the user's timezone (fallback for
    localHour / weekday)."""
    d: dict[str, Any] = {}
    if isinstance(raw, dict):
        d = raw
    elif raw:
        try:
            v = json.loads(raw)
            d = v if isinstance(v, dict) else {}
        except (TypeError, ValueError):
            d = {}
    d = {k: v for k, v in d.items() if k not in disabled}

    hour = _num(d.get("localHour"))
    wd = _num(d.get("weekday"))
    ctx = Context(hour=int(hour) % 24 if hour is not None else local.hour,
                  weekday=int(wd) % 7 if wd is not None else local.weekday())
    out = d.get("output")
    if isinstance(out, str) and out:
        kind, _, name = out.partition(":")
        ctx.output = kind.strip().lower()
        ctx.output_name = name.strip() or None
    for key in ("motion", "network"):
        if isinstance(d.get(key), str) and d[key]:
            setattr(ctx, key, d[key].strip().lower())
    if isinstance(d.get("place"), str) and d["place"].strip():
        ctx.place = d["place"].strip()
    w = d.get("weather")
    if isinstance(w, dict):
        cond = w.get("condition")
        ctx.weather = cond.strip().lower() if isinstance(cond, str) and cond else None
        ctx.temp_c = _num(w.get("tempC", w.get("temp", w.get("temperature"))))
    elif isinstance(w, str) and w:
        ctx.weather = w.strip().lower()
    ctx.repeat = d.get("repeatOfPrevious") is True
    for s in d.get("seeks") or []:
        if isinstance(s, dict):
            a, b = _seconds(s, "from"), _seconds(s, "to")
            if a is not None and b is not None:
                ctx.seeks.append((a, b))
    for v in d.get("volumeChanges") or []:
        if isinstance(v, dict):
            delta = _num(v.get("delta"))
            if delta is None:
                a, b = _num(v.get("from")), _num(v.get("to"))
                delta = b - a if a is not None and b is not None else None
            if delta is not None:
                ctx.volume_deltas.append(delta)
        elif _num(v) is not None:
            ctx.volume_deltas.append(float(v))
    return ctx


# ---------------------------------------------------------------- listening-detail signals

def detail_weight(ctx: Context, duration_s: float) -> float:
    """Extra like/dislike from listening details: repeat = strong like, seeking through = mild dislike,
    seeking back = mild like, turning it up / right down = small like / dislike."""
    w = 0.0
    if ctx.repeat:
        w += 1.5
    if ctx.seeks and duration_s > 0:
        forward = sum(b - a for a, b in ctx.seeks if b > a)
        back = any(b < a - 3 for a, b in ctx.seeks)
        n_fwd = sum(1 for a, b in ctx.seeks if b > a + 3)
        if forward > 0.3 * duration_s or n_fwd >= 3:
            w -= 0.5
        elif back:
            w += 0.3
    if ctx.volume_deltas:
        net = sum(ctx.volume_deltas)
        if net >= 0.1:
            w += 0.2
        elif net <= -0.3:
            w -= 0.2
    return w


# ---------------------------------------------------------------- context keys and mixes

def in_window(hour: int, window) -> bool:
    lo, hi = int(window[0]), int(window[1])
    return lo <= hour < hi if lo <= hi else (hour >= lo or hour < hi)


def context_keys(ctx: Context, settings) -> set[str]:
    """Which named contexts an event belongs to. 'morning'/'evening' feed the time-of-day mixes; the
    rest are candidate context mixes."""
    keys = set()
    if in_window(ctx.hour, settings["ai.mix.morning.hours"]):
        keys.add("morning")
    if in_window(ctx.hour, settings["ai.mix.evening.hours"]):
        keys.add("evening")
    if ctx.motion == "running" or (ctx.place and _WORKOUT_PLACE.search(ctx.place)):
        keys.add("workout")
    if ctx.output == "car":
        keys.add("drive")
    if ctx.place and ctx.place.lower() == "home" and in_window(ctx.hour, settings["ai.mix.late_night.hours"]):
        keys.add("late_night")
    if ctx.place and not _AUTO_PLACE.match(ctx.place) and not _WORKOUT_PLACE.search(ctx.place):
        keys.add(f"place:{ctx.place}")
    return keys


def mix_title(key: str) -> str:
    if key == "workout":
        return "Workout"
    if key == "drive":
        return "Drive"
    if key == "late_night":
        return "Late night"
    if key.startswith("place:"):
        return f"At {key[6:]}"
    return key.replace("_", " ").capitalize()


def reason_phrase(key: str) -> str:
    if key in ("morning", "evening"):
        return f"You often play this in the {key}"
    if key == "workout":
        return "You often play this when working out"
    if key == "drive":
        return "You often play this in the car"
    if key == "late_night":
        return "A late-night favourite at home"
    if key.startswith("place:"):
        return f"You often play this at {key[6:]}"
    return "Fits this moment"


def target(key: str) -> dict[str, Any]:
    """The context the learned model scores a context mix in."""
    return {
        "workout": {"motion": "running"},
        "drive": {"output": "car", "motion": "vehicle"},
        "late_night": {"place": "home", "hour": 23},
    }.get(key, {"place": key[6:]} if key.startswith("place:") else {})
