"""Describe-a-vibe: free text -> constraints on the existing feature space (no language model).

"chill rainy evening", "gym, no slow songs", "focus, no lyrics", "2000s throwbacks" are mapped with a hand-made
lexicon (phrases first, longest first, with simple negation: "no", "not", "without", "less", ...) onto the
features every analysed track already has: energy (library percentile), BPM, danceability, the Essentia mood
/ theme / voice / genre tags, the track year, and the user's own history (familiar vs. rediscover). Words the
lexicon doesn't know are matched against the library's own vocabulary: artist names ("like Radiohead") and
genre tags. Runs in microseconds on the i5; nothing to download.

The result is a flat list of *constraints* ("chips"): `{id, kind, label, ...}`. Clients only need `label` (to
draw a chip) and the list itself (remove a chip = drop it, then start the radio with the edited list); the
server interprets `kind`:

  energy       {min?, max?}   0..1, percentile of the library's energy
  tempo        {min?, max?}   BPM
  danceability {min?, max?}   0..1
  year         {min?, max?}
  vocals       {value: instrumental | vocal}
  mood         {value: <canonical mood>, weight: +1 | -1}
  genre        {value: <genre words>, exclude?: bool}
  familiarity  {value: familiar | discover}
  like         {value: <artist id>}      sounds like this artist from the library
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

import numpy as np

KINDS = ("energy", "tempo", "danceability", "year", "vocals", "mood", "genre", "familiarity", "like")
RANGE_KINDS = ("energy", "tempo", "danceability", "year")
MAX_TEXT = 200

# canonical mood -> (label, tags with weights, energy prior (min, max) or None)
MOODS: dict[str, tuple[str, dict[str, float], tuple[float | None, float | None] | None]] = {
    "chill": ("Chill", {"mood:relaxed": 1.0, "theme:calm": 0.8, "theme:relaxing": 0.8, "theme:soft": 0.5,
                         "mood:aggressive": -0.6}, None),
    "happy": ("Happy", {"mood:happy": 1.0, "theme:happy": 0.8, "theme:positive": 0.6, "theme:fun": 0.5,
                        "theme:uplifting": 0.5, "mood:sad": -0.6}, None),
    "sad": ("Melancholy", {"mood:sad": 1.0, "theme:sad": 0.8, "theme:melancholic": 0.8, "theme:emotional": 0.4,
                           "mood:happy": -0.5}, None),
    "aggressive": ("Intense", {"mood:aggressive": 1.0, "theme:heavy": 0.7, "theme:powerful": 0.6,
                               "mood:relaxed": -0.5}, None),
    "party": ("Party", {"mood:party": 1.0, "theme:party": 0.8, "dance:danceable": 0.6, "theme:groovy": 0.4},
              None),
    "romantic": ("Romantic", {"theme:romantic": 1.0, "theme:love": 0.8, "theme:sexy": 0.5, "mood:relaxed": 0.2},
                 None),
    "dark": ("Dark", {"theme:dark": 1.0, "theme:deep": 0.4, "mood:happy": -0.4}, None),
    "dreamy": ("Dreamy", {"theme:dream": 1.0, "theme:space": 0.6, "theme:soundscape": 0.5, "theme:meditative": 0.4},
               None),
    "epic": ("Epic", {"theme:epic": 1.0, "theme:film": 0.6, "theme:dramatic": 0.6, "theme:trailer": 0.5}, None),
    "acoustic": ("Acoustic", {"mood:acoustic": 1.0}, None),
    "groovy": ("Groovy", {"theme:groovy": 1.0, "dance:danceable": 0.6, "theme:cool": 0.3}, None),
    "uplifting": ("Uplifting", {"theme:uplifting": 1.0, "theme:hopeful": 0.6, "theme:inspiring": 0.6,
                                "theme:motivational": 0.5, "mood:happy": 0.4}, None),
    "focus": ("Focus", {"theme:background": 0.8, "theme:meditative": 0.5, "voice:instrumental": 0.5,
                        "theme:calm": 0.3, "mood:party": -0.4}, None),
    "energetic": ("Energetic", {"theme:energetic": 1.0, "theme:upbeat": 0.7, "theme:sport": 0.5,
                                "theme:fast": 0.4, "mood:relaxed": -0.4}, None),
}

# phrase -> list of constraint fragments. A fragment is (kind, params). Longest phrases match first.
_E_LOW = ("energy", {"max": 0.45})
_E_CALM = ("energy", {"max": 0.35})
_E_HIGH = ("energy", {"min": 0.65})
_E_UP = ("energy", {"min": 0.5})
_E_MID = ("energy", {"min": 0.2, "max": 0.7})


def _mood(v: str, w: float = 1.0) -> tuple[str, dict]:
    return ("mood", {"value": v, "weight": w})


def _genre(v: str) -> tuple[str, dict]:
    return ("genre", {"value": v})


LEXICON: dict[str, list[tuple[str, dict]]] = {
    # energy / tempo
    "no slow songs": [_E_UP, ("tempo", {"min": 100})], "no slow ones": [_E_UP, ("tempo", {"min": 100})],
    "nothing slow": [_E_UP, ("tempo", {"min": 100})], "no slow": [_E_UP, ("tempo", {"min": 100})],
    "no ballads": [_E_UP], "not too loud": [("energy", {"max": 0.65})], "nothing too loud": [("energy", {"max": 0.65})],
    "not too slow": [("energy", {"min": 0.35})], "no fast": [("energy", {"max": 0.6})],
    "high energy": [_E_HIGH, _mood("energetic")], "low energy": [_E_LOW], "low key": [_E_LOW, _mood("chill")],
    "low-key": [_E_LOW, _mood("chill")], "upbeat": [_E_UP, _mood("energetic")], "energetic": [_E_HIGH, _mood("energetic")],
    "energy": [_E_UP], "pumped": [_E_HIGH], "pump up": [_E_HIGH, _mood("energetic")], "hype": [_E_HIGH, _mood("energetic")],
    "banger": [_E_HIGH, _mood("party")], "bangers": [_E_HIGH, _mood("party")], "loud": [_E_HIGH],
    "fast": [("tempo", {"min": 120}), _E_UP], "slow": [("tempo", {"max": 100}), _E_LOW],
    "calm": [_E_CALM, _mood("chill")], "chill": [_E_LOW, _mood("chill")], "chilled": [_E_LOW, _mood("chill")],
    "chillout": [_E_LOW, _mood("chill")], "chill out": [_E_LOW, _mood("chill")], "relax": [_E_LOW, _mood("chill")],
    "relaxing": [_E_LOW, _mood("chill")], "relaxed": [_E_LOW, _mood("chill")], "unwind": [_E_LOW, _mood("chill")],
    "wind down": [_E_CALM, _mood("chill")], "mellow": [_E_LOW, _mood("chill")], "soft": [_E_LOW],
    "gentle": [_E_CALM], "quiet": [_E_CALM], "peaceful": [_E_CALM, _mood("chill")], "lazy": [_E_LOW, _mood("chill")],
    "cozy": [_E_LOW, _mood("chill")], "cosy": [_E_LOW, _mood("chill")], "sleep": [_E_CALM, _mood("chill")],
    "sleepy": [_E_CALM, _mood("chill")], "bedtime": [_E_CALM, _mood("chill")], "rainy": [_E_LOW, _mood("chill")],
    "rain": [_E_LOW, _mood("chill")], "sunday morning": [_E_LOW, _mood("chill")],
    "late night": [("energy", {"max": 0.6})], "midnight": [("energy", {"max": 0.6})],
    "evening": [("energy", {"max": 0.7})], "night": [("energy", {"max": 0.7})],
    "morning": [_E_MID, _mood("uplifting")], "wake up": [("energy", {"min": 0.35}), _mood("uplifting")],
    "sunrise": [_E_MID, _mood("uplifting")], "dinner": [_E_LOW, _mood("chill")],
    "background": [_E_MID, _mood("focus")],
    # activities
    "gym": [_E_HIGH, _mood("energetic")], "workout": [_E_HIGH, _mood("energetic")], "work out": [_E_HIGH, _mood("energetic")],
    "training": [_E_HIGH, _mood("energetic")], "cardio": [_E_HIGH, ("tempo", {"min": 120})],
    "running": [_E_HIGH, ("tempo", {"min": 120})], "run": [_E_HIGH, ("tempo", {"min": 120})],
    "lifting": [_E_HIGH, _mood("aggressive")], "focus": [_E_MID, _mood("focus")], "study": [_E_MID, _mood("focus")],
    "studying": [_E_MID, _mood("focus")], "concentrate": [_E_MID, _mood("focus")], "coding": [_E_MID, _mood("focus")],
    "deep work": [_E_MID, _mood("focus")], "reading": [_E_LOW, _mood("focus")], "work": [_E_MID, _mood("focus")],
    "road trip": [("energy", {"min": 0.4}), ("familiarity", {"value": "familiar"})],
    "driving": [("energy", {"min": 0.4})], "drive": [("energy", {"min": 0.4})],
    "party": [_E_UP, ("danceability", {"min": 0.55}), _mood("party")], "dance": [("danceability", {"min": 0.6})],
    "dancing": [("danceability", {"min": 0.6})], "danceable": [("danceability", {"min": 0.6})],
    "club": [_E_HIGH, ("danceability", {"min": 0.6}), _mood("party")], "cleaning": [_E_UP, _mood("happy")],
    "cooking": [_E_MID, _mood("happy")], "date night": [_E_LOW, _mood("romantic")],
    # vocals
    "no lyrics": [("vocals", {"value": "instrumental"})], "no vocals": [("vocals", {"value": "instrumental"})],
    "without lyrics": [("vocals", {"value": "instrumental"})], "without vocals": [("vocals", {"value": "instrumental"})],
    "no singing": [("vocals", {"value": "instrumental"})], "instrumental": [("vocals", {"value": "instrumental"})],
    "instrumentals": [("vocals", {"value": "instrumental"})], "wordless": [("vocals", {"value": "instrumental"})],
    "with vocals": [("vocals", {"value": "vocal"})], "with lyrics": [("vocals", {"value": "vocal"})],
    "sing along": [("vocals", {"value": "vocal"}), ("familiarity", {"value": "familiar"})],
    "singalong": [("vocals", {"value": "vocal"}), ("familiarity", {"value": "familiar"})],
    "karaoke": [("vocals", {"value": "vocal"}), ("familiarity", {"value": "familiar"})],
    # moods
    "feel good": [_mood("happy")], "feel-good": [_mood("happy")], "happy": [_mood("happy")], "cheerful": [_mood("happy")],
    "joyful": [_mood("happy")], "sunny": [_mood("happy")], "summer": [_mood("happy")], "summery": [_mood("happy")],
    "positive": [_mood("happy")], "fun": [_mood("happy")], "uplifting": [_mood("uplifting")],
    "inspiring": [_mood("uplifting")], "motivation": [_mood("uplifting"), _E_UP], "motivational": [_mood("uplifting"), _E_UP],
    "sad": [_mood("sad")], "melancholy": [_mood("sad")], "melancholic": [_mood("sad")], "heartbreak": [_mood("sad")],
    "breakup": [_mood("sad")], "crying": [_mood("sad")], "moody": [_mood("sad")], "emotional": [_mood("sad")],
    "angry": [_mood("aggressive")], "aggressive": [_mood("aggressive")], "rage": [_mood("aggressive"), _E_HIGH],
    "intense": [_mood("aggressive")], "heavy": [_mood("aggressive")], "romantic": [_mood("romantic")],
"sexy": [_mood("romantic")], "dark": [_mood("dark")], "spooky": [_mood("dark")],
    "halloween": [_mood("dark")], "eerie": [_mood("dark")], "dreamy": [_mood("dreamy")], "ethereal": [_mood("dreamy")],
    "atmospheric": [_mood("dreamy")], "spacey": [_mood("dreamy")], "epic": [_mood("epic")], "cinematic": [_mood("epic")],
    "acoustic": [_mood("acoustic")], "unplugged": [_mood("acoustic")], "stripped back": [_mood("acoustic")],
    "groovy": [_mood("groovy"), ("danceability", {"min": 0.5})], "funky": [_mood("groovy"), ("danceability", {"min": 0.5})],
    # familiarity
    "throwback": [("familiarity", {"value": "familiar"})], "throwbacks": [("familiarity", {"value": "familiar"})],
    "nostalgia": [("familiarity", {"value": "familiar"})], "nostalgic": [("familiarity", {"value": "familiar"})],
    "favourites": [("familiarity", {"value": "familiar"})], "favorites": [("familiarity", {"value": "familiar"})],
    "old favourites": [("familiarity", {"value": "familiar"})], "old favorites": [("familiarity", {"value": "familiar"})],
    "classics": [("familiarity", {"value": "familiar"})], "familiar": [("familiarity", {"value": "familiar"})],
    "oldies": [("familiarity", {"value": "familiar"}), ("year", {"max": 1979})],
    "discover": [("familiarity", {"value": "discover"})], "discovery": [("familiarity", {"value": "discover"})],
    "something new": [("familiarity", {"value": "discover"})], "new to me": [("familiarity", {"value": "discover"})],
    "deep cuts": [("familiarity", {"value": "discover"})], "rarely played": [("familiarity", {"value": "discover"})],
    "forgotten": [("familiarity", {"value": "discover"})], "unheard": [("familiarity", {"value": "discover"})],
    "rediscover": [("familiarity", {"value": "discover"})],
    # eras without digits
    "noughties": [("year", {"min": 2000, "max": 2009})], "y2k": [("year", {"min": 1998, "max": 2004})],
    # genres (also matched against the library's own tags, see `vocab`)
    "rock": [_genre("rock")], "indie": [_genre("indie")], "pop": [_genre("pop")], "jazz": [_genre("jazz")],
    "hip hop": [_genre("hip hop")], "hip-hop": [_genre("hip hop")], "hiphop": [_genre("hip hop")], "rap": [_genre("hip hop")],
    "electronic": [_genre("electronic")], "electronica": [_genre("electronic")], "edm": [_genre("electronic")],
    "techno": [_genre("techno")], "house": [_genre("house")], "trance": [_genre("trance")], "dubstep": [_genre("dubstep")],
    "drum and bass": [_genre("drum n bass")], "drum n bass": [_genre("drum n bass")], "dnb": [_genre("drum n bass")],
    "classical": [_genre("classical")], "folk": [_genre("folk")], "metal": [_genre("metal")], "punk": [_genre("punk")],
    "r&b": [_genre("rnb")], "rnb": [_genre("rnb")], "soul": [_genre("soul")], "funk": [_genre("funk")],
    "blues": [_genre("blues")], "country": [_genre("country")], "reggae": [_genre("reggae")],
    "ambient": [_genre("ambient"), _E_LOW], "lofi": [_genre("lo-fi"), _E_LOW], "lo-fi": [_genre("lo-fi"), _E_LOW],
    "disco": [_genre("disco"), ("danceability", {"min": 0.55})], "grunge": [_genre("grunge")], "emo": [_genre("emo")],
    "shoegaze": [_genre("shoegaze")], "synthwave": [_genre("synthwave")], "synth": [_genre("synth")],
    "latin": [_genre("latin")], "kpop": [_genre("k-pop")], "k-pop": [_genre("k-pop")], "soundtrack": [_genre("soundtrack"), _mood("epic")],
    "gospel": [_genre("gospel")], "trip hop": [_genre("trip hop")], "post-rock": [_genre("post rock")],
    "post rock": [_genre("post rock")], "hardcore": [_genre("hardcore")], "garage": [_genre("garage")],
    "acid jazz": [_genre("acid jazz")], "bossa nova": [_genre("bossa nova"), _mood("chill")],
}

NEGATIONS = {"no", "not", "without", "nothing", "less", "avoid", "except", "never", "zero", "minus", "skip"}
STOPWORDS = {"a", "an", "the", "and", "or", "with", "for", "to", "of", "some", "music", "songs", "song", "tracks",
             "track", "me", "my", "i", "im", "want", "play", "please", "vibe", "vibes", "vibey", "radio", "mix",
             "stuff", "like", "kind", "sort", "in", "on", "at", "while", "but", "only", "just", "really",
             "very", "bit", "little", "something", "anything", "mood", "feel", "feeling", "time", "day", "ones",
             "too", "much", "more", "that", "this", "is", "are", "be", "from", "era", "style", "sounds", "sound",
             "similar", "type", "good", "great", "best", "all", "any", "lots", "lot", "plenty", "&"}

_DECADE = re.compile(r"(?<![\d])'?((?:19|20)?\d0)'?s\b")
_YEAR_RANGE = re.compile(r"\b((?:19|20)\d\d)\s*(?:-|–|to|until|till)\s*((?:19|20)\d\d)\b")
_YEAR_BOUND = re.compile(r"\b(before|after|since|from|pre|post|until|till)\s*-?\s*((?:19|20)\d\d)\b")
_YEAR = re.compile(r"\b((?:19|20)\d\d)\b")
_BPM = re.compile(r"\b(\d{2,3})\s*(?:-|–|to)?\s*(\d{2,3})?\s*bpm\b")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("’", "'").replace("‘", "'")
    s = re.sub(r"[^\w\s&'\-–]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


@dataclass
class Vocab:
    """What the library can be matched against: artist names and genre tag words."""
    artists: dict[str, tuple[str, str]]      # normalised name -> (artist id, display name)
    genres: set[str]                          # squashed genre words seen in tags / genre column

    @classmethod
    def empty(cls) -> Vocab:
        return cls({}, set())


def vocab_from_library(lib) -> Vocab:
    artists: dict[str, tuple[str, str]] = {}
    for aid, name in zip(lib.artist, lib.artist_name, strict=False):
        n = _norm(name)
        if n and len(n) >= 3 and n not in artists:
            artists[n] = (aid, name)
    genres: set[str] = set()
    for tags, g in zip(lib.tags, lib.genre, strict=False):
        for k in tags:
            if k.startswith("genre:"):
                for part in re.split(r"---|/|,", k[6:]):
                    if part.strip():
                        genres.add(_squash(part))
        if g:
            for part in re.split(r"[;/,]", g):
                if part.strip():
                    genres.add(_squash(part))
    return Vocab(artists, genres)


# ---------------------------------------------------------------- parsing

def _negated(tokens_before: list[str]) -> bool:
    return any(t in NEGATIONS for t in tokens_before[-2:])


def _range_frag(kind: str, lo: float | None, hi: float | None) -> tuple[str, dict]:
    d: dict[str, float] = {}
    if lo is not None:
        d["min"] = lo
    if hi is not None:
        d["max"] = hi
    return kind, d


def _invert(kind: str, params: dict) -> tuple[str, dict] | None:
    """'no X' for a lexicon entry: moods flip weight, genres exclude, vocals flip, energy ranges flip."""
    if kind == "mood":
        return kind, {**params, "weight": -abs(params.get("weight", 1.0))}
    if kind == "genre":
        return kind, {**params, "exclude": True}
    if kind == "vocals":
        return kind, {"value": "vocal" if params["value"] == "instrumental" else "instrumental"}
    if kind == "familiarity":
        return kind, {"value": "discover" if params["value"] == "familiar" else "familiar"}
    if kind in ("energy", "tempo", "danceability"):
        if "max" in params and "min" not in params:     # "no chill" -> not low energy
            return kind, {"min": params["max"]}
        if "min" in params and "max" not in params:     # "no fast" -> not high
            return kind, {"max": params["min"]}
    return None


def parse(text: str, vocab: Vocab | None = None) -> list[dict[str, Any]]:
    """Text -> merged constraint list (chips). Pure; `vocab` adds artist and library-genre matches."""
    vocab = vocab or Vocab.empty()
    s = " " + _norm(text[:MAX_TEXT]) + " "
    frags: list[tuple[int, str, dict]] = []   # (position, kind, params) in text order

    def take(m: re.Match, repl: str = " ") -> None:
        nonlocal s
        s = s[:m.start()] + repl * (m.end() - m.start()) + s[m.end():]

    # numbers first: BPM, year ranges, bounds, decades, single years
    for m in list(_BPM.finditer(s)):
        a, b = int(m.group(1)), m.group(2)
        kind, rp = _range_frag("tempo", a, int(b)) if b else _range_frag("tempo", a - 8, a + 8)
        frags.append((m.start(), kind, rp))
        take(m)
    for m in list(_YEAR_RANGE.finditer(s)):
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        frags.append((m.start(), *_range_frag("year", a, b)))
        take(m)
    for m in list(_YEAR_BOUND.finditer(s)):
        w, y = m.group(1), int(m.group(2))
        lo, hi = (None, y - 1) if w in ("before", "pre", "until", "till") else (y if w in ("since", "from") else y + 1, None)
        frags.append((m.start(), *_range_frag("year", lo, hi)))
        take(m)
    for m in list(_DECADE.finditer(s)):
        d = m.group(1)
        y = int(d) if len(d) == 4 else (1900 + int(d) if int(d) >= 30 else 2000 + int(d))
        frags.append((m.start(), *_range_frag("year", y, y + 9)))
        take(m)
    for m in list(_YEAR.finditer(s)):
        y = int(m.group(1))
        frags.append((m.start(), *_range_frag("year", y, y)))
        take(m)

    # lexicon phrases, longest first
    for phrase in sorted(LEXICON, key=lambda p: (-len(p), p)):
        pat = re.compile(r"(?<![\w-])" + re.escape(phrase) + r"(?![\w-])")
        for m in list(pat.finditer(s)):
            before = s[:m.start()].split()
            neg = _negated(before) and phrase.split()[0] not in NEGATIONS
            for kind, params in LEXICON[phrase]:
                f = _invert(kind, params) if neg else (kind, dict(params))
                if f is not None:
                    frags.append((m.start(), f[0], f[1]))
            # blank the phrase (and its negation word) so it isn't matched again
            start = m.start()
            if neg:
                for t in reversed(before[-2:]):
                    if t in NEGATIONS:
                        start = s.rfind(t, 0, start)
                        break
            s = s[:start] + " " * (m.end() - start) + s[m.end():]

    # leftovers: artists ("like X", "X vibes") and genre words known to this library
    rest = s.split()
    i = 0
    while i < len(rest):
        matched = False
        for n in (4, 3, 2, 1):
            if i + n > len(rest):
                continue
            words = rest[i:i + n]
            phrase = " ".join(words)
            if phrase in STOPWORDS or phrase in NEGATIONS:
                continue
            neg = _negated(rest[:i])
            pos = s.find(phrase)
            if phrase in vocab.artists:
                aid, name = vocab.artists[phrase]
                if not neg:
                    frags.append((pos, "like", {"value": aid, "name": name}))
                matched = True
            elif _squash(phrase) in vocab.genres and len(_squash(phrase)) >= 3:
                frags.append((pos, "genre", {"value": phrase, "exclude": neg} if neg else {"value": phrase}))
                matched = True
            if matched:
                i += n
                break
        if not matched:
            i += 1
    frags.sort(key=lambda f: f[0])
    return merge([(k, p) for _, k, p in frags])


def merge(frags: list[tuple[str, dict]]) -> list[dict[str, Any]]:
    """Fragments (in text order) -> one chip per range kind (intersected; a contradiction keeps the later
    words), one per mood / genre / like value, the last vocals / familiarity wins."""
    ranges: dict[str, dict[str, float]] = {}
    singles: dict[str, dict] = {}
    multi: dict[tuple[str, str], dict] = {}
    for kind, p in frags:
        if kind in RANGE_KINDS:
            cur = ranges.setdefault(kind, {})
            new = dict(cur)
            if "min" in p:
                new["min"] = max(new.get("min", -1e9), p["min"])
            if "max" in p:
                new["max"] = min(new.get("max", 1e9), p["max"])
            if new.get("min", -1e9) > new.get("max", 1e9):   # contradiction: later words win
                new = {k: v for k, v in p.items() if k in ("min", "max")}
            ranges[kind] = new
        elif kind in ("vocals", "familiarity"):
            singles[kind] = {"value": p["value"]}
        elif kind in ("mood", "genre", "like"):
            multi[(kind, str(p["value"]))] = dict(p)
    # an "energy min" from a mood that clashes with an explicit chill range is resolved by merge order above
    out: list[dict[str, Any]] = []
    for kind in RANGE_KINDS:
        if ranges.get(kind):
            out.append(make(kind, **ranges[kind]))
    for kind in ("vocals", "familiarity"):
        if kind in singles:
            out.append(make(kind, **singles[kind]))
    for (kind, _v), p in multi.items():
        out.append(make(kind, **p))
    return out


# ---------------------------------------------------------------- chips

def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def label(kind: str, c: dict) -> str:
    lo, hi = c.get("min"), c.get("max")
    if kind == "energy":
        if hi is not None and lo is None:
            return "Calm" if hi <= 0.35 else "Low energy" if hi <= 0.5 else "Not too loud" if hi <= 0.7 else "Not full-on"
        if lo is not None and hi is None:
            return "High energy" if lo >= 0.65 else "Upbeat" if lo >= 0.45 else "Not too slow"
        return f"Energy {_pct(lo or 0)}–{_pct(hi if hi is not None else 1)}"
    if kind == "tempo":
        if lo is not None and hi is not None:
            return f"{round(lo)}–{round(hi)} BPM"
        return f"≥ {round(lo)} BPM" if lo is not None else f"≤ {round(hi)} BPM"
    if kind == "danceability":
        return "Danceable" if lo is not None and hi is None else "Not too dancey" if hi is not None and lo is None \
            else "Danceability " + f"{_pct(lo or 0)}–{_pct(hi if hi is not None else 1)}"
    if kind == "year":
        if lo is not None and hi is not None:
            if lo == hi:
                return str(int(lo))
            if hi - lo == 9 and int(lo) % 10 == 0:
                return f"{int(lo)}s"
            return f"{int(lo)}–{int(hi)}"
        return f"From {int(lo)}" if lo is not None else f"Before {int(hi) + 1}"
    if kind == "vocals":
        return "No vocals" if c["value"] == "instrumental" else "With vocals"
    if kind == "familiarity":
        return "Familiar favourites" if c["value"] == "familiar" else "Rediscover & new"
    if kind == "mood":
        name = MOODS.get(c["value"], (c["value"].capitalize(),))[0]
        return name if c.get("weight", 1) > 0 else f"Not {name.lower()}"
    if kind == "genre":
        g = c["value"].replace("rnb", "R&B").replace("drum n bass", "drum & bass")
        g = g if g.isupper() or "&" in g else g.title() if len(g) > 3 else g.upper()
        return f"No {g}" if c.get("exclude") else g
    if kind == "like":
        return f"Like {c.get('name') or 'this artist'}"
    return kind


def make(kind: str, **params: Any) -> dict[str, Any]:
    """A chip: {id, kind, label, ...params}. The id is stable for the same meaning (station identity)."""
    c: dict[str, Any] = {"kind": kind}
    for k in ("min", "max"):
        if params.get(k) is not None:
            v = float(params[k])
            c[k] = int(v) if kind in ("year", "tempo") else round(v, 3)
    for k in ("value", "name"):
        if params.get(k) is not None:
            c[k] = str(params[k])
    if kind == "mood":
        c["weight"] = 1.0 if float(params.get("weight", 1.0)) >= 0 else -1.0
    if kind == "genre" and params.get("exclude"):
        c["exclude"] = True
    if kind in RANGE_KINDS:
        cid = f"{kind}:{c.get('min', '')}-{c.get('max', '')}"
    elif kind == "mood":
        cid = f"mood:{c['value']}:{'+' if c['weight'] > 0 else '-'}"
    elif kind == "genre":
        cid = f"genre:{_squash(c['value'])}{':x' if c.get('exclude') else ''}"
    else:
        cid = f"{kind}:{c.get('value', '')}"
    c["id"] = cid
    c["label"] = label(kind, c)
    return c


def sanitize(chips: list[Any]) -> list[dict[str, Any]]:
    """Validate client-edited chips (they come back from the app). Unknown kinds / junk are dropped; labels and
    ids are recomputed so a client can't smuggle in anything but the meaning. ValueError if it isn't a list."""
    if not isinstance(chips, list):
        raise ValueError("constraints must be a list")
    out, seen = [], set()
    for c in chips[:30]:
        if not isinstance(c, dict) or c.get("kind") not in KINDS:
            continue
        kind = c["kind"]
        try:
            if kind in RANGE_KINDS:
                lo, hi = c.get("min"), c.get("max")
                lo = None if lo is None else float(lo)
                hi = None if hi is None else float(hi)
                if lo is None and hi is None:
                    continue
                chip = make(kind, min=lo, max=hi)
            elif kind == "vocals":
                if c.get("value") not in ("instrumental", "vocal"):
                    continue
                chip = make(kind, value=c["value"])
            elif kind == "familiarity":
                if c.get("value") not in ("familiar", "discover"):
                    continue
                chip = make(kind, value=c["value"])
            elif kind == "mood":
                if c.get("value") not in MOODS:
                    continue
                chip = make(kind, value=c["value"], weight=float(c.get("weight", 1)))
            elif kind == "genre":
                v = str(c.get("value") or "").strip().lower()[:40]
                if not v:
                    continue
                chip = make(kind, value=v, exclude=bool(c.get("exclude")))
            else:  # like
                v = str(c.get("value") or "")[:80]
                if not v:
                    continue
                chip = make(kind, value=v, name=str(c.get("name") or "")[:80] or None)
        except (TypeError, ValueError):
            continue
        if chip["id"] not in seen:
            seen.add(chip["id"])
            out.append(chip)
    return out


def describe(chips: list[dict]) -> str:
    return ", ".join(c["label"] for c in chips) or "Anything"


def station_key(chips: list[dict]) -> str:
    return "vibe:" + "|".join(sorted(c["id"] for c in chips))


# ---------------------------------------------------------------- scoring

def _tag(lib, name: str) -> np.ndarray:
    return np.array([t.get(name, 0.0) for t in lib.tags], dtype=float)


def _genre_match(lib, value: str) -> np.ndarray:
    v = _squash(value)
    alts = {v, {"hiphop": "rap", "rnb": "rhythmblues", "drumnbass": "drumbass", "lofi": "lofi"}.get(v, v)}
    out = np.zeros(len(lib))
    for i, (tags, g) in enumerate(zip(lib.tags, lib.genre, strict=False)):
        best = 0.0
        for k, p in tags.items():
            if k.startswith("genre:") and any(a in _squash(k[6:]) for a in alts):
                best = max(best, min(1.0, 0.4 + p))
        if g and any(a in _squash(g) for a in alts):
            best = max(best, 0.9)
        out[i] = best
    return out


@dataclass
class VibeScore:
    soft: np.ndarray          # added to the station value
    mask: np.ndarray          # bool: allowed
    relaxed: list[str]        # chip labels that had to be loosened for a small library
    unsupported: list[str]    # chip labels the analysis can't judge (no such tags in this library)


def score(lib, chips: list[dict], years: np.ndarray, sig_weight: np.ndarray, plays: np.ndarray,
          min_pool: int = 60, energy_shift: float = 0.0, available: np.ndarray | None = None) -> VibeScore:
    """Hard filters (ranges, vocals, years, excluded genres; included genres when enough tracks match) and soft
    scores (moods, genres, familiarity, artist likeness). If the filters leave fewer than `min_pool` tracks that
    are still `available` (not yet served on this station, not banned) they are widened step by step and reported
    in `relaxed`, so a narrow vibe keeps going once its exact matches are used up. `energy_shift` (steering) moves
    energy ranges."""
    n = len(lib)
    soft = np.zeros(n)
    relaxed: list[str] = []
    unsupported: list[str] = []
    has = {k.split(":")[0] for t in lib.tags for k in t}
    has_tag = {k for t in lib.tags for k in t}
    filters: list[tuple[int, str, np.ndarray, Any]] = []   # (relax order, label, mask, widen fn or None)

    def rng(values: np.ndarray, lo, hi, pad: float, unknown_ok: np.ndarray | None = None):
        def m(widen: float) -> np.ndarray:
            ok = np.ones(n, bool)
            if lo is not None:
                ok &= values >= lo - widen * pad
            if hi is not None:
                ok &= values <= hi + widen * pad
            if unknown_ok is not None:
                ok |= unknown_ok
            return ok
        return m

    for c in chips:
        kind, lo, hi = c["kind"], c.get("min"), c.get("max")
        if kind == "energy":
            lo2 = None if lo is None else float(np.clip(lo + energy_shift, 0, 0.95))
            hi2 = None if hi is None else float(np.clip(hi + energy_shift, 0.05, 1))
            filters.append((1, c["label"], None, rng(lib.energy, lo2, hi2, 0.12)))
            centre = ((lo2 if lo2 is not None else 0) + (hi2 if hi2 is not None else 1)) / 2
            soft -= 0.25 * np.abs(lib.energy - centre)
        elif kind == "tempo":
            filters.append((0, c["label"], None, rng(lib.bpm, lo, hi, 12, lib.bpm <= 0)))
        elif kind == "danceability":
            filters.append((0, c["label"], None, rng(lib.dance, lo, hi, 0.12)))
        elif kind == "year":
            filters.append((2, c["label"], None, rng(years, lo, hi, 3, years <= 0)))
            soft -= np.where(years <= 0, 0.3, 0.0)   # unknown year: allowed, but last
        elif kind == "vocals":
            if "voice:instrumental" not in has_tag:
                unsupported.append(c["label"])
                continue
            inst = _tag(lib, "voice:instrumental")
            want = c["value"] == "instrumental"
            filters.append((3, c["label"], None,
                            (lambda w, inst=inst, want=want: (inst >= 0.5 - 0.15 * w) if want else (inst <= 0.5 + 0.15 * w))))
            soft += 0.4 * (inst if want else 1 - inst)
        elif kind == "mood":
            spec = MOODS.get(c["value"])
            if spec is None:
                continue
            tags = {t: w for t, w in spec[1].items() if t in has_tag}
            if not tags:
                if not ({"mood", "theme"} & has):
                    unsupported.append(c["label"])
                continue
            s = sum(w * _tag(lib, t) for t, w in tags.items()) / max(sum(abs(w) for w in tags.values()), 1e-9)
            soft += 0.9 * c.get("weight", 1.0) * s
        elif kind == "genre":
            g = _genre_match(lib, c["value"])
            if c.get("exclude"):
                filters.append((9, c["label"], g < 0.3, None))
            else:
                soft += 0.6 * g
                if (g > 0).sum() >= 10:
                    filters.append((4, c["label"], g > 0, None))
        elif kind == "familiarity":
            known = np.tanh(np.clip(sig_weight, 0, None) / 3)
            if c["value"] == "familiar":
                soft += 0.5 * known + 0.15 * np.tanh(plays / 3)
            else:
                soft += np.where(plays == 0, 0.35, 0.0) - 0.3 * np.tanh(plays / 4)
        elif kind == "like":
            idx = [i for i, a in enumerate(lib.artist) if a == c["value"]]
            if idx:
                centre = lib.emb[idx].mean(axis=0)
                centre /= max(float(np.linalg.norm(centre)), 1e-9)
                soft += 0.6 * np.clip(lib.emb @ centre, 0, None)
    # apply filters, loosening in relax order (0 first) until the pool is big enough
    widen = {i: 0.0 for i in range(len(filters))}
    dropped: set[int] = set()

    def current() -> np.ndarray:
        m = np.ones(n, bool)
        for k, (_o, _lab, mask, fn) in enumerate(filters):
            if k in dropped:
                continue
            m &= mask if fn is None else fn(widen[k])
        return m

    avail = np.ones(n, bool) if available is None else available

    def count(m: np.ndarray) -> int:
        return int((m & avail).sum())

    mask = current()
    need = min(min_pool, int(avail.sum()))
    order = sorted(range(len(filters)), key=lambda k: filters[k][0])
    for k in order:
        if count(mask) >= need:
            break
        if filters[k][0] == 9:      # an explicit "no X" is never relaxed
            continue
        if filters[k][3] is not None:
            for w in (1.0, 2.0):
                widen[k] = w
                mask = current()
                if count(mask) >= need:
                    break
        if count(mask) < need:
            dropped.add(k)
            mask = current()
        relaxed.append(filters[k][1])
    return VibeScore(soft=soft, mask=mask, relaxed=relaxed, unsupported=unsupported)


def track_years(conn, lib) -> np.ndarray:
    """tracks.year, else the album's year; 0 when unknown. Aligned with the library."""
    rows = conn.execute("SELECT t.id, COALESCE(t.year, al.year) FROM tracks t "
                        "LEFT JOIN albums al ON al.id = t.album_id WHERE t.missing = 0").fetchall()
    by = {r[0]: r[1] for r in rows}
    out = np.zeros(len(lib))
    for i, tid in enumerate(lib.ids):
        y = by.get(tid)
        try:
            out[i] = int(y) if y else 0
        except (TypeError, ValueError):
            out[i] = 0
    return out
