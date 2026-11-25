"""Steerable radio: per-station knobs that change the next picks straight away.

A *station* is what the user would call "the same radio": the smart shuffle on this kind of device, a vibe
with the same chips, or a seed (track / artist / album / playlist). Steering is stored per (user, station)
in `radio_stations.steering`, so it is remembered the next time that station starts. Every steer is also
logged in `radio_feedback`, which the recommender reads as training signal (see recommender.collect_signals).

Actions:
  more_like / less_like   (scope track | artist)  pull toward / push away from that sound or artist
  more_energy / calmer                            shift energy (-3 .. +3)
  more_variety / more_familiar                    one axis (-2 familiar .. +2 varied)
  never_here              (scope track | artist)  never on this station (other stations unaffected)
  remove                  (type, id)              undo one more/less/never entry
  reset                                           clear this station's steering
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np

ACTIONS = ("more_like", "less_like", "more_energy", "calmer", "more_variety", "more_familiar", "never_here",
           "remove", "reset")
MAX_REFS = 50
# recommender weights for logged steering (per track, decayed like events)
FEEDBACK_WEIGHT = {"more_like": 1.0, "less_like": -0.8, "never_here": -1.0}


@dataclass
class Steering:
    energy: int = 0
    variety: int = 0
    more: list[dict] = field(default_factory=list)    # {type: track|artist, id, name}
    less: list[dict] = field(default_factory=list)
    never: list[dict] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {"energy": self.energy, "variety": self.variety, "moreLike": self.more, "lessLike": self.less,
                "never": self.never}

    @classmethod
    def from_json(cls, s: str | None) -> Steering:
        if not s:
            return cls()
        try:
            d = json.loads(s)
        except (TypeError, ValueError):
            return cls()
        if not isinstance(d, dict):
            return cls()

        def refs(k: str) -> list[dict]:
            return [r for r in d.get(k) or [] if isinstance(r, dict) and r.get("type") in ("track", "artist")
                    and isinstance(r.get("id"), str)][:MAX_REFS]
        return cls(energy=int(np.clip(int(d.get("energy") or 0), -3, 3)),
                   variety=int(np.clip(int(d.get("variety") or 0), -2, 2)),
                   more=refs("moreLike"), less=refs("lessLike"), never=refs("never"))

    @property
    def is_neutral(self) -> bool:
        return not (self.energy or self.variety or self.more or self.less or self.never)

    def apply(self, action: str, ref: dict | None = None) -> Steering:
        """Mutates and returns self. `ref` = {type, id, name} for the actions that take one."""
        if action not in ACTIONS:
            raise ValueError(f"unknown steering action {action!r}")
        if action == "reset":
            self.energy = self.variety = 0
            self.more, self.less, self.never = [], [], []
            return self
        if action == "more_energy":
            self.energy = min(self.energy + 1, 3)
            return self
        if action == "calmer":
            self.energy = max(self.energy - 1, -3)
            return self
        if action == "more_variety":
            self.variety = min(self.variety + 1, 2)
            return self
        if action == "more_familiar":
            self.variety = max(self.variety - 1, -2)
            return self
        if not ref or ref.get("type") not in ("track", "artist") or not ref.get("id"):
            raise ValueError(f"{action} needs a track or artist")
        key = (ref["type"], ref["id"])

        def drop(lst: list[dict]) -> list[dict]:
            return [r for r in lst if (r["type"], r["id"]) != key]
        self.more, self.less, self.never = drop(self.more), drop(self.less), drop(self.never)
        entry = {"type": ref["type"], "id": ref["id"], "name": ref.get("name")}
        if action == "more_like":
            self.more = (self.more + [entry])[-MAX_REFS:]
        elif action == "less_like":
            self.less = (self.less + [entry])[-MAX_REFS:]
        elif action == "never_here":
            self.never = (self.never + [entry])[-MAX_REFS:]
        return self   # remove: dropped above

    def summary(self) -> str:
        """Short human text, e.g. 'more energy, more variety, 2 less-like'."""
        parts = []
        if self.energy:
            parts.append(("more energy" if self.energy > 0 else "calmer") + (f" ×{abs(self.energy)}" if abs(self.energy) > 1 else ""))
        if self.variety:
            parts.append(("more variety" if self.variety > 0 else "more familiar") + (f" ×{abs(self.variety)}" if abs(self.variety) > 1 else ""))
        if self.more:
            parts.append(f"more like {_names(self.more)}")
        if self.less:
            parts.append(f"less like {_names(self.less)}")
        if self.never:
            parts.append(f"{len(self.never)} never here")
        return ", ".join(parts)


def _names(refs: list[dict]) -> str:
    names = [r.get("name") or r["type"] for r in refs[-2:]]
    extra = len(refs) - len(names)
    return " & ".join(names) + (f" +{extra}" if extra > 0 else "")


@dataclass
class Effect:
    bias: np.ndarray          # added to every station value
    banned: np.ndarray        # bool: never on this station
    noise: float              # multiplier for the random part (variety)
    gap_extra: int            # extra artist spacing
    energy_shift: float       # moves vibe energy ranges / the moment's energy target
    seed_weight: float        # multiplier for closeness to the seed's sound (variety loosens it)


def _unit(v: np.ndarray) -> np.ndarray:
    return v / max(float(np.linalg.norm(v)), 1e-9)


def effect(st: Steering, lib, sig_weight: np.ndarray, plays: np.ndarray) -> Effect:
    n = len(lib)
    bias = np.zeros(n)
    banned = np.zeros(n, bool)
    if n == 0:
        return Effect(bias, banned, 1.0, 0, 0.0, 1.0)
    artist = np.array(lib.artist, dtype=object)

    def artist_rows(aid: str) -> np.ndarray:
        return np.where(artist == aid)[0]

    if st.energy:
        bias += 0.22 * st.energy * (2 * lib.energy - 1)
    for r in st.more:
        if r["type"] == "track" and r["id"] in lib.index:
            bias += 0.35 * np.clip(lib.emb @ lib.emb[lib.index[r["id"]]], 0, None)
        elif r["type"] == "artist":
            rows = artist_rows(r["id"])
            if len(rows):
                bias[rows] += 0.3
                bias += 0.25 * np.clip(lib.emb @ _unit(lib.emb[rows].mean(axis=0)), 0, None)
    for r in st.less:
        if r["type"] == "track" and r["id"] in lib.index:
            i = lib.index[r["id"]]
            bias -= 0.45 * np.clip(lib.emb @ lib.emb[i], 0, None)
            banned[i] = True
        elif r["type"] == "artist":
            rows = artist_rows(r["id"])
            if len(rows):
                bias[rows] -= 1.5
                bias -= 0.15 * np.clip(lib.emb @ _unit(lib.emb[rows].mean(axis=0)), 0, None)
    for r in st.never:
        if r["type"] == "track" and r["id"] in lib.index:
            banned[lib.index[r["id"]]] = True
        elif r["type"] == "artist":
            banned[artist_rows(r["id"])] = True
    v = st.variety
    noise, gap, seed_w = 1.0, 0, 1.0
    if v > 0:
        noise = 1.0 + 0.7 * v
        gap = v
        seed_w = 1.0 - 0.2 * v
        bias += 0.12 * v * (plays == 0) - 0.1 * v * np.tanh(plays / 5)
    elif v < 0:
        noise = 1.0 / (1 + 0.5 * -v)
        bias += 0.25 * -v * np.tanh(np.clip(sig_weight, 0, None) / 3) - 0.1 * -v * (plays == 0)
    return Effect(bias, banned, noise, gap, 0.12 * st.energy, seed_w)
