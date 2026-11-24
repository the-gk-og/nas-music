"""What the radio DJ says before a song: a short, plain-text intro built from what the server already knows
(track/album tags, the queue's name, and the artist-page cache in `artist_info`: Wikipedia bio, origin, years
active, genres). No network and no AI model: templates picked with a seed, so the same spot in the same queue
always gets the same words (and so the same cached clip), while different spots vary.

Everything here is pure; `facts_for` reads the DB, `write` builds the text."""
from __future__ import annotations

import hashlib
import json
import random
import re
import sqlite3
from dataclasses import dataclass, field

MAX_FACT_WORDS = 28
MAX_TEXT_CHARS = 420


@dataclass
class Song:
    title: str
    artist: str
    album: str | None = None
    year: int | None = None
    artist_id: str | None = None


@dataclass
class ArtistFacts:
    """Short spoken facts about an artist, best first."""
    sentences: list[str] = field(default_factory=list)


# ---------- cleaning text for speech ----------

_SUFFIX = re.compile(r"\s*[\(\[][^\)\]]*(remaster|remix(ed)? \d{4}|version|edit|mono|stereo|deluxe|bonus|"
                     r"live at|demo|single|explicit|clean)[^\)\]]*[\)\]]\s*$", re.I)
_DASH_SUFFIX = re.compile(r"\s+-\s+(\d{4}\s+)?(remaster(ed)?|single version|radio edit|mono|stereo)( \d{4})?( version)?\s*$",
                          re.I)
_FEAT = re.compile(r"\s*[\(\[]?\b(feat\.?|ft\.?|featuring)\s+([^\)\]]+)[\)\]]?", re.I)


def speakable(s: str) -> str:
    """A title or name as it should be read out: no "(2011 Remaster)", "feat." spelt out, symbols named."""
    t = (s or "").strip()
    for _ in range(2):
        t = _SUFFIX.sub("", t)
        t = _DASH_SUFFIX.sub("", t)
    t = _FEAT.sub(lambda m: f" featuring {m.group(2).strip()}", t)
    t = t.replace("&", " and ").replace("+", " and ").replace("@", " at ")
    t = re.sub(r"[_*#~^|<>{}\[\]\\/]", " ", t)
    return re.sub(r"\s+", " ", t).strip(" -–—:;,") or (s or "").strip()


_PARENS = re.compile(r"\s*\([^()]*\)")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'“])")


def bio_sentences(text: str | None) -> list[str]:
    """Wikipedia's summary split into short sentences, without pronunciations/dates in brackets."""
    if not text:
        return []
    t = text
    for _ in range(3):  # nested brackets
        t = _PARENS.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    out = []
    for s in _SENTENCE.split(t):
        s = s.strip()
        if not s or len(s.split()) > MAX_FACT_WORDS or len(s.split()) < 4:
            continue
        out.append(s if s[-1] in ".!?" else s + ".")
    return out


# ---------- facts from the artist-page cache ----------


def facts_from_info(name: str, data: dict | None) -> ArtistFacts:
    if not data:
        return ArtistFacts()
    sentences: list[str] = []
    bio = (data.get("bio") or {}).get("text") if isinstance(data.get("bio"), dict) else None
    bio_s = bio_sentences(bio)
    # The first sentence ("X is an English rock band formed in Abingdon in 1985.") is the best one; later ones
    # are often about line-up changes, so take at most one more.
    sentences += bio_s[:1]
    group = (data.get("type") or "").lower() in ("group", "orchestra", "choir")
    origin = data.get("beginArea") or data.get("area")
    since = (data.get("yearsActive") or {}).get("from")
    if origin and since and group:
        sentences.append(f"{name} got together in {origin} back in {since}.")
    elif origin:
        sentences.append(f"{name} {'are' if group else 'is'} from {origin}.")
    elif since:
        sentences.append(f"{name} {'have' if group else 'has'} been making music since {since}.")
    genres = [g for g in (data.get("genres") or []) if isinstance(g, str)][:2]
    if genres:
        sentences.append(f"Expect some {' and '.join(genres)}.")
    sentences += bio_s[1:2]
    return ArtistFacts(sentences=[speakable_sentence(s) for s in sentences])


def speakable_sentence(s: str) -> str:
    s = s.replace("&", " and ")
    return re.sub(r"\s+", " ", s).strip()


def facts_for(conn: sqlite3.Connection, artist_id: str | None, name: str) -> ArtistFacts:
    if not artist_id:
        return ArtistFacts()
    row = conn.execute("SELECT status, data FROM artist_info WHERE artist_id = ?", (artist_id,)).fetchone()
    if row is None or row["status"] != "ok" or not row["data"]:
        return ArtistFacts()
    try:
        data = json.loads(row["data"])
    except ValueError:
        return ArtistFacts()
    return facts_from_info(name, data if isinstance(data, dict) else None)


# ---------- the intro ----------

OPENERS = [
    "Hi there, you're listening to {station}.",
    "Welcome to {station}.",
    "This is {station}. Let's get going.",
]
BACK_ANNOUNCE = [
    "That was {title} by {artist}.",
    "You just heard {artist} with {title}.",
    "{title}, there, from {artist}.",
]
UP_NEXT = [
    "Up next, it's {artist} with {title}.",
    "Here's {title} by {artist}.",
    "Next up: {title}, from {artist}.",
    "Coming up, {artist}, and {title}.",
]
UP_NEXT_ALBUM = [
    "Here's {title}, from the {year}album {album}.",
    "Next, {artist}, with {title}, off {album}.",
]
SAME_ARTIST = [
    "Staying with {artist} for one more: {title}.",
    "More from {artist} now. This is {title}.",
]
TIME = [
    "It's {time}.",
    "The time is {time}.",
]


def clock(hour: int, minute: int) -> str:
    """'just after four', 'half past nine', 'coming up to midnight'."""
    names = ["midnight", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
             "noon"]

    def name(h: int) -> str:
        h %= 24
        return names[h] if h <= 12 else names[h - 12]

    if minute < 8:
        return f"just after {name(hour)}"
    if 25 <= minute <= 35:
        return f"half past {name(hour)}"
    if minute >= 50:
        return f"coming up to {name(hour + 1)}"
    return f"{minute} minutes past {name(hour)}" if minute < 25 else f"{60 - minute} minutes to {name(hour + 1)}"


def seed_of(*parts: object) -> int:
    return int.from_bytes(hashlib.sha1("\x1f".join(str(p) for p in parts).encode()).digest()[:8], "big")


def write(song: Song, prev: Song | None, *, station: str | None, position: int, facts: ArtistFacts,
          seed: int, local_time: tuple[int, int] | None = None) -> str:
    """The intro for `song` (at queue `position`), after `prev`. Short: two to four sentences."""
    rnd = random.Random(seed)
    title, artist = speakable(song.title), speakable(song.artist)
    parts: list[str] = []
    if position == 0 and station:
        parts.append(rnd.choice(OPENERS).format(station=speakable(station)))
    elif prev and rnd.random() < 0.6:
        parts.append(rnd.choice(BACK_ANNOUNCE).format(title=speakable(prev.title), artist=speakable(prev.artist)))
    if local_time and rnd.random() < 0.2:
        parts.append(rnd.choice(TIME).format(time=clock(*local_time)))
    same = prev is not None and prev.artist_id and prev.artist_id == song.artist_id
    if same:
        parts.append(rnd.choice(SAME_ARTIST).format(title=title, artist=artist))
    else:
        if facts.sentences and rnd.random() < 0.75:
            # Mostly the best fact, sometimes another, so a much-played artist doesn't always get the same line.
            i = 0 if rnd.random() < 0.6 or len(facts.sentences) == 1 else rnd.randrange(1, len(facts.sentences))
            parts.append(facts.sentences[i])
        album = speakable(song.album) if song.album else None
        if album and album.casefold() != title.casefold() and rnd.random() < 0.35:
            year = f"{song.year} " if song.year and rnd.random() < 0.7 else ""
            parts.append(rnd.choice(UP_NEXT_ALBUM).format(title=title, artist=artist, album=album, year=year))
        else:
            parts.append(rnd.choice(UP_NEXT).format(title=title, artist=artist))
    text = " ".join(p.strip() for p in parts if p.strip())
    if len(text) > MAX_TEXT_CHARS:  # an unusually long fact: drop it rather than cut a sentence
        text = " ".join(p for p in parts if p not in facts.sentences)[:MAX_TEXT_CHARS]
    return text


def speaks_at(position: int, every: int) -> bool:
    """Whether the DJ talks before the song at this queue position: always before the first, then every
    `every` songs (1 = before every song)."""
    every = max(1, every)
    return position == 0 or position % every == 0
