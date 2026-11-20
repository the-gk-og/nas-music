"""Smart shuffle ("Play something for now"): an endless station chosen for this moment.

Scores every library track for *now* from:
  * the user's taste in this context (the recommender's score with hour / weekday and a context key: the
    learned LightGBM model when there is one, plus the context weights for car / workout / morning / evening);
  * a time profile: what the user played around this hour on this kind of day (weekday vs weekend);
  * momentum: what was played through in the last hour (stay in the groove) and what was skipped (move off it);
  * the device: car leans familiar and a bit livelier, late night calmer, a running workout high energy;
  * freshness: nothing heard in the last few hours, little from the last two days, some rediscoveries
    (the picker reserves slots for never / rarely played tracks close to the user's taste).

It returns per-track values plus a one-line explanation ("Because it's Friday evening and you've been
playing upbeat indie rock."). Cheap: a few vector ops over the cached library; no training.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from . import context as cx
from .common import local_tz, top_tag

DEVICES = ("phone", "car", "speaker", "desktop", "watch", "web", "tv")
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


@dataclass
class MomentContext:
    hour: int
    weekday: int                 # 0 = Monday
    device: str = "phone"
    motion: str | None = None
    workout: str | None = None

    @property
    def key(self) -> str:
        return f"moment:{self.device}"

    def as_dict(self) -> dict[str, Any]:
        return {"localHour": self.hour, "weekday": self.weekday + 1, "deviceKind": self.device,
                "motion": self.motion, "workout": self.workout}


def resolve(now: datetime, tz: str, raw: dict | None, device_kind: str | None = None) -> MomentContext:
    """The moment from the client's hint (all optional) and the server's clock in the user's timezone.
    `device_kind` is the registered device kind (android / web / desktop / watch / other)."""
    raw = raw if isinstance(raw, dict) else {}
    local = now.astimezone(local_tz(tz))
    hour, wd = local.hour, local.weekday()
    try:
        if raw.get("localHour") is not None:
            hour = int(raw["localHour"]) % 24
        if raw.get("weekday") is not None:
            wd = (int(raw["weekday"]) - 1) % 7      # ISO 1 = Monday, as in EventContext
    except (TypeError, ValueError):
        pass
    dev = str(raw.get("deviceKind") or "").lower()
    out = str(raw.get("output") or "").split(":")[0].lower()
    if out == "car" or dev == "car":
        dev = "car"
    elif out in ("cast", "speaker") or dev in ("speaker", "cast", "tv"):
        dev = "speaker" if dev != "tv" else "tv"
    elif dev not in DEVICES:
        dev = {"android": "phone", "watch": "watch", "desktop": "desktop", "web": "web"}.get(
            (device_kind or "").lower(), "phone")
    motion = raw.get("motion") if isinstance(raw.get("motion"), str) else None
    workout = raw.get("workout") if isinstance(raw.get("workout"), str) else None
    return MomentContext(hour, wd, dev, motion.lower() if motion else None, workout.lower() if workout else None)


def daypart(hour: int) -> str:
    return "morning" if 5 <= hour < 12 else "afternoon" if 12 <= hour < 17 else "evening" if 17 <= hour < 22 \
        else "night"


def when_phrase(m: MomentContext) -> str:
    return f"{DAYS[m.weekday]} {daypart(m.hour)}"


def where_phrase(m: MomentContext) -> str:
    if m.motion == "running" or m.workout:
        return " and you're working out"
    return {"car": " and you're in the car", "speaker": " on the speaker", "tv": " on the TV",
            "desktop": " at your desk", "watch": " on your watch"}.get(m.device, "")


def energy_word(e: float) -> str:
    return "calm" if e < 0.3 else "mellow" if e < 0.5 else "upbeat" if e < 0.72 else "high-energy"


def style_phrase(lib, idx: list[int]) -> str:
    """'upbeat indie rock' for a set of library rows."""
    if not idx:
        return "music"
    e = float(np.mean(lib.energy[idx]))
    genres = Counter()
    for i in idx:
        g = top_tag(lib.tags[i]) or (lib.genre[i] or "").split(";")[0].strip() or None
        if g:
            genres[g] += 1
    g = ""
    if genres:
        name, cnt = genres.most_common(1)[0]
        if cnt >= max(2, 0.3 * len(idx)):
            g = name if (name.isupper() or len(name) <= 3) else name.lower()
    return f"{energy_word(e)} {g}".strip() if g else f"{energy_word(e)} music"


def _unit(v: np.ndarray) -> np.ndarray:
    return v / max(float(np.linalg.norm(v)), 1e-9)


def _hour_dist(a: int, b: int) -> int:
    d = abs(a - b) % 24
    return min(d, 24 - d)


@dataclass
class MomentScore:
    value: np.ndarray            # per track; -inf = not now
    explanation: str
    discovery: np.ndarray        # bool: candidates for rediscovery slots
    target_energy: float | None


def tod_key(rec, m: MomentContext) -> str | None:
    if m.motion == "running" or m.workout:
        return "workout"
    if m.device == "car":
        return "drive"
    s = rec.settings
    if cx.in_window(m.hour, s["ai.mix.late_night.hours"]) and "late_night" in rec.sig.ctx_weight:
        return "late_night"
    if cx.in_window(m.hour, s["ai.mix.morning.hours"]):
        return "morning"
    if cx.in_window(m.hour, s["ai.mix.evening.hours"]):
        return "evening"
    return None


def score(rec, m: MomentContext, energy_shift: float = 0.0) -> MomentScore:
    lib, sig = rec.lib, rec.sig
    n = len(lib)
    key = tod_key(rec, m)
    s = rec.scores(hour=m.hour, weekday=m.weekday, tod=key if key in sig.ctx_weight else None)
    fin = np.isfinite(s)
    z = np.full(n, -np.inf)
    if fin.any():
        z[fin] = (s[fin] - s[fin].mean()) / (s[fin].std() + 1e-9)
    val = 0.5 * np.tanh(z / 2)

    now = rec.now
    weekend = m.weekday >= 5
    # time profile: played-through tracks around this hour on this kind of day, recent ones count more
    prof_idx, prof_w = [], []
    hour_ago = now - timedelta(hours=1)
    mom, skipped, skipped_day = [], [], []
    for ts, ctx, i, etype, label in sig.events:
        if label == 1 and _hour_dist(ctx.hour, m.hour) <= 2 and (ctx.weekday >= 5) == weekend:
            age = max((now - ts).total_seconds() / 86400, 0.0)
            prof_idx.append(i)
            prof_w.append(0.5 ** (age / 60))
        if ts >= hour_ago:
            if etype == "play" and label == 1:
                mom.append(i)
            elif etype == "skip":
                skipped.append(i)
        elif etype == "skip" and ts >= now - timedelta(hours=24):
            skipped_day.append(i)
    targets: list[tuple[float, float]] = []     # (energy, weight)
    if len(prof_idx) >= 5:
        w = np.array(prof_w)
        centre = _unit((lib.emb[prof_idx] * w[:, None]).sum(axis=0))
        val += 0.25 * np.clip(lib.emb @ centre, -0.2, None)
        targets.append((float(np.average(lib.energy[prof_idx], weights=w)), 1.0))
    if mom:
        recent = mom[-8:]
        val += 0.3 * np.clip(lib.emb @ _unit(lib.emb[recent].mean(axis=0)), -0.2, None)
        targets.append((float(np.mean(lib.energy[recent])), 1.5))
    if skipped or skipped_day:
        neg = skipped + skipped_day[-10:]
        weight = 0.35 if skipped else 0.15
        val -= weight * np.clip(lib.emb @ _unit(lib.emb[neg].mean(axis=0)), 0, None)
        counts = Counter(lib.artist[i] for i in skipped)
        if counts:
            val -= np.array([0.25 * min(counts.get(a, 0), 2) for a in lib.artist])
    # device / moment priors on energy
    prior = None
    if m.motion == "running" or m.workout:
        prior = 0.8
    elif m.device == "car":
        prior = 0.6
    elif m.hour >= 23 or m.hour < 5:
        prior = 0.3
    elif 5 <= m.hour < 8:
        prior = 0.45
    if prior is not None:
        targets.append((prior, 0.7))
    target = None
    if targets:
        target = float(np.clip(sum(e * w for e, w in targets) / sum(w for _, w in targets) + energy_shift, 0, 1))
        val -= 0.3 * np.abs(lib.energy - target)
    elif energy_shift:
        val += 0.6 * energy_shift * (2 * lib.energy - 1)
    if m.device == "car":    # sing-along territory: lean on what's known
        val += 0.1 * np.tanh(np.clip(sig.weight, 0, None) / 3)
    # freshness: a shuffle shouldn't replay what you just heard
    days = sig.last_played_days
    val = np.where(days < 4 / 24, -np.inf, val)
    val -= np.where(days < 2, 0.35, 0.0)
    val[~np.isfinite(z)] = -np.inf
    sound = rec.model.sound_score(lib.emb) if rec.model is not None else np.zeros(n)
    discovery = ((sig.plays == 0) | (days > 120)) & (sound >= np.quantile(sound, 0.6) if n else True) \
        & np.isfinite(val) & (sig.skips < 2)

    when, where = when_phrase(m), where_phrase(m)
    if len(mom) >= 2:
        text = f"Because it's {when}{where} and you've been playing {style_phrase(lib, mom[-8:])}."
    elif len(prof_idx) >= 5:
        top = [i for i, _ in sorted(zip(prof_idx, prof_w, strict=False), key=lambda p: -p[1])[:30]]
        text = f"Because it's {when}{where}: you usually go for {style_phrase(lib, top)} around now."
    else:
        text = f"Because it's {when}{where}: a shuffle of your favourites with a few rediscoveries."
    if len(skipped) >= 2:
        text += " Steering clear of what you just skipped."
    return MomentScore(val, text, discovery, target)
