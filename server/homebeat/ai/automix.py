"""Automix ordering: reorder upcoming tracks for smooth DJ flow while keeping the list's intent.

Cost of playing b after a = tempo distance (allowing half/double time) + Camelot key distance + energy jump,
plus (for finite lists) distance from an energy arc that builds to ~70 % of the set and eases off.
Intent: no track moves more than `window` places from where the list had it, and the first (now playing)
track stays put. Greedy construction under that window, then pairwise-swap improvement. Tracks without
analysis get neutral costs, so they mostly stay where they were.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

from .djmix import key_distance

MAX_STRETCH = 0.08     # beyond ±8 % the clients won't time-stretch; transition falls back to a crossfade
DEFAULT_WINDOW = 6


@dataclass(frozen=True)
class Item:
    id: str
    bpm: float | None = None
    camelot: str | None = None
    energy: float | None = None
    stable: bool = False


def tempo_match(a: float | None, b: float | None) -> tuple[float, float] | None:
    """(multiplier m in {1, 2, 0.5}, stretch) where playing b at `stretch` makes b*m*stretch == a, choosing the m
    with the smallest stretch. None if either tempo is unknown."""
    if not a or not b or a <= 0 or b <= 0:
        return None
    best = None
    for m in (1.0, 2.0, 0.5):
        s = a / (b * m)
        if best is None or abs(math.log(s)) < abs(math.log(best[1])) - 1e-9:
            best = (m, s)
    return best


def pair_cost(a: Item, b: Item) -> float:
    c = 0.0
    tm = tempo_match(a.bpm, b.bpm)
    if tm is None:
        c += 0.6
    else:
        m, s = tm
        pct = abs(s - 1)
        c += pct / 0.06 if pct <= MAX_STRETCH else 3 + (pct - MAX_STRETCH) * 20
        if m != 1.0:
            c += 0.25
    kd = key_distance(a.camelot, b.camelot)
    c += 0.6 if kd is None else (0.0, 0.3, 0.8)[kd] if kd < 3 else 1.5 + (kd - 3) * 0.3
    if a.energy is not None and b.energy is not None:
        c += abs(a.energy - b.energy) * 2
    return c


def arc_target(p: float, lo: float, hi: float) -> float:
    """Energy arc for a finite set at relative position p (0..1): builds to the peak at 70 %, eases to 60 %."""
    x = p / 0.7 if p <= 0.7 else 1 - 0.4 * (p - 0.7) / 0.3
    x = max(0.0, min(1.0, x))
    return lo + (hi - lo) * (0.3 + 0.7 * x)


def order(items: list[Item], *, window: int = DEFAULT_WINDOW, keep_first: bool = True, arc: bool = True,
          passes: int = 4) -> list[Item]:
    n = len(items)
    if n <= 2:
        return list(items)
    window = max(1, int(window))
    energies = [it.energy for it in items if it.energy is not None]
    lo, hi = (min(energies), max(energies)) if len(energies) >= 3 else (0.0, 0.0)
    use_arc = arc and hi - lo > 0.1

    def step_cost(prev: Item | None, it: Item, pos: int) -> float:
        c = pair_cost(prev, it) if prev is not None else 0.0
        if use_arc and it.energy is not None:
            c += abs(it.energy - arc_target(pos / (n - 1), lo, hi)) * 1.0
        return c

    placed: list[int] = []
    left = set(range(n))
    if keep_first:
        placed.append(0)
        left.discard(0)
    while left:
        j = len(placed)
        overdue = [i for i in left if i <= j - window]
        if overdue:
            pick = min(overdue)
        else:
            cands = [i for i in left if i <= j + window]
            prev = items[placed[-1]] if placed else None
            pick = min(cands, key=lambda i: (step_cost(prev, items[i], j), i))
        placed.append(pick)
        left.discard(pick)

    def total(seq: list[int]) -> float:
        return sum(step_cost(items[seq[k - 1]] if k else None, items[seq[k]], k) for k in range(n))

    def ok(seq: list[int], k: int) -> bool:
        return abs(seq[k] - k) <= window

    best = total(placed)
    start = 1 if keep_first else 0
    for _ in range(passes):
        improved = False
        for x in range(start, n - 1):
            for y in range(x + 1, min(n, x + 2 * window + 1)):
                placed[x], placed[y] = placed[y], placed[x]
                if ok(placed, x) and ok(placed, y):
                    t = total(placed)
                    if t < best - 1e-9:
                        best, improved = t, True
                        continue
                placed[x], placed[y] = placed[y], placed[x]
        if not improved:
            break
    return [items[i] for i in placed]


def transition_info(a: Item, b: Item) -> dict:
    tm = tempo_match(a.bpm, b.bpm)
    kd = key_distance(a.camelot, b.camelot)
    beatmatch = bool(tm and a.stable and b.stable and abs(tm[1] - 1) <= MAX_STRETCH)
    return {"fromId": a.id, "toId": b.id, "tempoRatio": round(tm[1], 4) if tm else None,
            "tempoMultiplier": tm[0] if tm else None, "keyDistance": kd, "beatmatch": beatmatch}


def load_items(conn: sqlite3.Connection, track_ids: list[str]) -> list[Item]:
    """Items for these ids (in order, duplicates kept). Energy: the DJ analysis, else the AI features."""
    uniq = list(dict.fromkeys(track_ids))
    rows: dict[str, sqlite3.Row] = {}
    for s in range(0, len(uniq), 500):
        chunk = uniq[s:s + 500]
        q = ",".join("?" * len(chunk))
        for r in conn.execute(
                f"SELECT t.id, d.bpm, d.camelot, d.grid_stable, d.status, "
                f"COALESCE(d.energy, (SELECT f.energy FROM features f WHERE f.track_id = t.id "
                f" ORDER BY f.analysed_at DESC LIMIT 1)) AS energy "
                f"FROM tracks t LEFT JOIN track_dj d ON d.track_id = t.id AND d.status = 'done' "
                f"WHERE t.id IN ({q})", chunk):
            rows[r["id"]] = r
    out = []
    for tid in track_ids:
        r = rows.get(tid)
        if r is None:
            out.append(Item(tid))
        else:
            out.append(Item(tid, r["bpm"], r["camelot"], r["energy"], bool(r["grid_stable"])))
    return out


def order_ids(conn: sqlite3.Connection, track_ids: list[str], *, window: int = DEFAULT_WINDOW,
              keep_first: bool = True, arc: bool = True) -> tuple[list[str], list[dict]]:
    items = order(load_items(conn, track_ids), window=window, keep_first=keep_first, arc=arc)
    return [it.id for it in items], [transition_info(a, b) for a, b in zip(items, items[1:])]
