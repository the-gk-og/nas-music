"""Fuzzy name matching for spoken names (pure).

Speech-to-text mangles names in a few predictable ways: case and punctuation ("AC/DC" -> "ac dc"),
spelled-out initialisms ("AJR" -> "a j r"), "&" vs "and", leading "the", possessives ("Stacey's" -> "staceys"),
and near-miss spellings. `score` is 1.0 for the same normalised name, high when one name contains the other
as whole words ("bedroom" in "Elijah bedroom display"), and a character-similarity ratio otherwise.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher

from homebeat.voice.parse import clean

THRESHOLD = 0.72

_NUMS = {"1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight",
         "9": "nine", "10": "ten"}


def norm(s: str) -> str:
    t = clean(s).replace("'", "")
    t = re.sub(r"^the ", "", t)
    # "a j r" / "a.j.r." -> "ajr": join runs of single letters
    t = re.sub(r"\b(?:[a-z] ){1,}[a-z]\b", lambda m: m.group(0).replace(" ", ""), t)
    t = " ".join(_NUMS.get(w, w) for w in t.split())
    return t


def _compact(s: str) -> str:
    return s.replace(" ", "")


def score(query: str, name: str) -> float:
    q, n = norm(query), norm(name)
    if not q or not n:
        return 0.0
    if q == n or _compact(q) == _compact(n):
        return 1.0
    ratio = SequenceMatcher(None, q, n).ratio()
    qt, nt = q.split(), n.split()
    best = ratio
    # token-sorted similarity: word order doesn't matter ("room stacey" ~ "stacey room")
    best = max(best, SequenceMatcher(None, " ".join(sorted(qt)), " ".join(sorted(nt))).ratio() - 0.02)
    # whole-word containment either way
    if f" {q} " in f" {n} ":
        best = max(best, 0.8 + 0.18 * len(q) / len(n))
    elif f" {n} " in f" {q} " and len(nt) >= 1:
        best = max(best, 0.7 + 0.2 * len(n) / len(q))
    # every query word is (nearly) a word of the name ("staceys speaker" ~ "staceys room speaker")
    if qt and all(any(w == x or (len(w) > 3 and SequenceMatcher(None, w, x).ratio() >= 0.85) for x in nt) for w in qt):
        best = max(best, 0.6 + 0.35 * len(qt) / max(len(nt), 1))
    return min(best, 0.99)


@dataclass
class Match[T]:
    item: T
    score: float
    label: str


def best[T](query: str, candidates: Iterable[tuple[str, T]], threshold: float = THRESHOLD,
         bonus=None) -> Match[T] | None:
    """The best (label, item) for `query` at or above `threshold`. `bonus(item) -> float` breaks near-ties
    (added before comparing; e.g. prefer an alias over a name, an online device over an offline one)."""
    top: Match[T] | None = None
    top_key = -1.0
    for label, item in candidates:
        s = score(query, label)
        if s < threshold:
            continue
        key = s + (bonus(item) if bonus else 0.0)
        if key > top_key:
            top, top_key = Match(item, s, label), key
    return top


def ranked[T](query: str, candidates: Iterable[tuple[str, T]], threshold: float = THRESHOLD) -> list[Match[T]]:
    out = [Match(item, score(query, label), label) for label, item in candidates]
    return sorted((m for m in out if m.score >= threshold), key=lambda m: -m.score)
