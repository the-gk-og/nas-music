"""Voice text -> intent. Pure (no database): English sentences as Home Assistant's speech-to-text writes them.

`parse(text)` returns an Intent. Where a sentence might end in a room / speaker ("... in the bedroom",
"... on Stacey's room speaker"), the Intent carries every possible split (`splits`, last preposition first);
the resolver keeps the first split whose device phrase matches a real device, else the whole phrase is the
query ("play songs in the key of life").
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

INTENTS = ("play", "pause", "resume", "next", "previous", "volume", "now_playing", "unknown")
KINDS = ("any", "playlist", "album", "artist", "song", "radio", "mix", "vibe", "moment")

_NUM_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "twenty five": 25,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "seventy five": 75, "eighty": 80,
    "ninety": 90, "hundred": 100, "a hundred": 100, "one hundred": 100, "half": 50, "max": 100, "maximum": 100,
    "full": 100,
}


@dataclass
class Intent:
    name: str                                 # one of INTENTS
    query: str = ""                           # play: what to play (without the device phrase)
    kind: str = "any"                         # play: one of KINDS
    artist: str | None = None                 # play: "X by <artist>"
    device: str | None = None                 # an explicitly named device/room (set by the resolver or slots)
    volume: float | None = None               # volume: absolute 0..1
    volume_step: float | None = None          # volume: relative (+0.1 / -0.1)
    splits: list[tuple[str, str]] = field(default_factory=list)  # (rest of sentence, device phrase)
    text: str = ""


def clean(text: str) -> str:
    """Lowercase, accents folded, curly quotes straightened, punctuation (except apostrophes) dropped."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("’", "'").replace("‘", "'").replace("&", " and ").lower()
    t = re.sub(r"(\d+)\s*%", r"\1 percent", t)
    t = re.sub(r"[^\w\s']", " ", t)
    return re.sub(r"\s+", " ", t).strip()


_POLITE = re.compile(r"^(?:(?:hey|ok|okay) \w+ )?(?:please |can you |could you |would you |will you |i want to |"
                     r"i'd like to |i would like to |let's |lets |go ahead and |now )*")
_TRAIL = re.compile(r"(?: please| for me| now| thanks| thank you)+$")


def _strip_polite(t: str) -> str:
    t = _POLITE.sub("", t)
    return _TRAIL.sub("", t).strip()


# A device phrase follows one of these. "to" only after "play ... to" style is rare; keep on/in/to/through/at.
_PREP = re.compile(r"\b(?:on|in|to|through|at|via)\b(?: (?:the|my))? ")


def device_splits(t: str) -> list[tuple[str, str]]:
    """Every (before, device phrase) split at a preposition, last one first. `before` may be ''."""
    out = []
    for m in _PREP.finditer(t):
        before, after = t[:m.start()].strip(), t[m.end():].strip()
        if after:
            out.append((before, after))
    out.reverse()
    return out


_MUSIC = r"(?:the )?(?:music|song|track|tune|audio|playback|player|it|this|that)"
_NOW_PLAYING = re.compile(
    r"^(?:what(?:'s| is| song is| track is| song's)? (?:this|that|playing|on)(?: song| track)?(?: right now| now)?"
    r"|what(?:'s| is) (?:currently |now )?playing|what (?:song|track|music) is (?:this|that|playing|on)"
    r"|what am i listening to|who(?:'s| is) (?:this|singing|playing)(?: song)?|name (?:this|that) (?:song|track)"
    r"|what(?:'s| is) the (?:name of (?:this|the) )?(?:song|track)(?: called)?)\b")


def _volume(t: str) -> Intent | None:
    if re.search(r"\b(?:louder|turn (?:it |the volume |the music )?up|volume up|raise the volume|"
                 r"increase the volume|crank it)\b", t):
        return Intent("volume", volume_step=0.1)
    if re.search(r"\b(?:quieter|softer|turn (?:it |the volume |the music )?down|volume down|lower the volume|"
                 r"decrease the volume)\b", t):
        return Intent("volume", volume_step=-0.1)
    m = re.search(r"\bvolume\b(?: (?:to|at))?(?: level)? (.+?)(?: percent)?$", t) or \
        re.search(r"\b(?:set|turn|change|put) (?:the )?volume (?:to |at )?(.+?)(?: percent)?$", t)
    if m:
        raw = m.group(1).strip()
        if raw.isdigit():
            v = int(raw)
        elif raw in _NUM_WORDS:
            v = _NUM_WORDS[raw]
        else:
            return None
        if v <= 10 and "percent" not in t:   # "volume 7" = 7 of 10
            v *= 10
        return Intent("volume", volume=max(0.0, min(1.0, v / 100)))
    return None


_SIMPLE = (
    ("pause", re.compile(rf"^(?:pause|stop|halt|hold)(?: {_MUSIC}| playing| playback)?$|^(?:pause|stop) (?:the )?"
                         rf"(?:music|song|playback)\b")),
    ("resume", re.compile(rf"^(?:resume|unpause|continue|keep playing|start playing again|play again|"
                          rf"carry on)(?: {_MUSIC}| playing| playback)?$|^(?:play|resume)$|^(?:resume|unpause) ")),
    ("next", re.compile(rf"^(?:skip|next|play the next|go to the next|play next|skip ahead)(?: {_MUSIC}| one| song| "
                        rf"track)?$|^skip (?:this|the) (?:song|track|one)\b|^next (?:song|track)\b|^skip\b")),
    ("previous", re.compile(rf"^(?:previous|prev|go back|back|play the previous|play the last|last|restart|"
                            rf"play that again|replay)(?: {_MUSIC}| one| song| track)?$|^(?:previous|last) "
                            rf"(?:song|track)\b|^go back\b")),
)


def _simple(t: str) -> Intent | None:
    for name, rx in _SIMPLE:
        if rx.search(t):
            return Intent(name)
    return None


_PLAY = re.compile(r"^(?:play|put on|start|listen to|shuffle|queue up|i want to hear|i wanna hear|"
                   r"let me hear|throw on|spin)\b ?")


def parse(text: str) -> Intent:
    t = _strip_polite(clean(text))
    if not t:
        return Intent("unknown", text=text)
    if _NOW_PLAYING.search(t):
        return Intent("now_playing", text=text)
    # A trailing device phrase for control intents: "pause the music in the kitchen", "skip on bedroom".
    for cand in device_splits(t) + [(t, None)]:
        body, dev = cand
        if not body:
            continue
        it = _volume(body) or _simple(body)
        if it is not None:
            it.device, it.text = dev, text
            if dev is not None:
                it.splits = [(body, dev)]
            return it
    m = _PLAY.match(t)
    if m is None:
        return Intent("unknown", text=text)
    rest = t[m.end():].strip()
    if not rest:
        return Intent("resume", text=text)
    it = Intent("play", text=text)
    it.splits = [(b, d) for b, d in device_splits(rest) if b]
    q = play_query(rest)
    it.query, it.kind, it.artist = q
    return it


_ARTICLES = re.compile(r"^(?:(?:my|the|a|an|some|me|us|our|your) )+")


def play_query(rest: str) -> tuple[str, str, str | None]:
    """'pop radio' -> ('pop', 'radio', None); 'the album x by y' -> ('x', 'album', 'y'); 'something chill' ->
    ('chill', 'vibe', None)."""
    r = rest.strip()
    r = re.sub(r"^(?:me|us) ", "", r)
    if re.fullmatch(r"(?:(?:some|any|a little|a bit of) )?(?:music|songs|tunes|anything|something|whatever|"
                    r"some music|something good|something for (?:now|me)|something (?:i|i'd|you think i'd) like|"
                    r"music for (?:now|me))", r):
        return "", "moment", None
    m = re.fullmatch(r"(?:a |an |the |my )?(?:radio|station|radio station) (?:for|of|based on|like|from) (.+)", r)
    if m:
        return _strip_kind_words(m.group(1)), "radio", None
    m = re.fullmatch(r"(.+?) (?:radio|station|radio station)", r)
    if m:
        return _strip_kind_words(_ARTICLES.sub("", m.group(1))), "radio", None
    m = re.fullmatch(r"(?:(?:all |some )?(?:music|songs|tracks|stuff|something|anything) )?by (?:the artist )?(.+)", r) \
        or re.fullmatch(r"(?:the )?artist (.+)", r)
    if m:
        return m.group(1), "artist", None
    m = re.fullmatch(r"(?:my |the |our )?playlist(?: called| named)? (.+)", r) or \
        re.fullmatch(r"(?:my |the |our )?(.+?) playlist", r)
    if m:
        return m.group(1), "playlist", None
    m = re.fullmatch(r"(?:the )?album(?: called| named)? (.+)", r) or re.fullmatch(r"(?:the )?(.+?) album", r)
    if m:
        q, artist = _by(m.group(1))
        return q, "album", artist
    m = re.fullmatch(r"(?:the )?(?:song|track|tune|single)(?: called| named)? (.+)", r)
    if m:
        q, artist = _by(m.group(1))
        return q, "song", artist
    m = re.fullmatch(r"(?:my |the |our |today's )?(.+? mix)(?: of the day)?", r)
    if m:
        return m.group(1), "mix", None
    m = re.fullmatch(r"(?:(?:some|something|anything) )(?:(?:a )?(?:bit |little )?(?:more )?)?(.+?)(?: music| songs| tunes| stuff)?", r)
    if m and m.group(1) not in ("music", "songs"):
        return m.group(1), "vibe", None
    m = re.fullmatch(r"(?:music|songs|tunes|something) (?:for|to) (.+)", r)
    if m:
        return m.group(1), "vibe", None
    q, artist = _by(_ARTICLES.sub("", r) if not r.startswith("the ") else r)
    if artist:
        return q, "song", artist
    return r, "any", None


def _by(q: str) -> tuple[str, str | None]:
    m = re.fullmatch(r"(.+?) by (.+)", q)
    return (m.group(1), m.group(2)) if m else (q, None)


def _strip_kind_words(q: str) -> str:
    return re.sub(r"^(?:the )?(?:artist|album|song|playlist) ", "", q).strip()


# Slot names from Home Assistant intents (intent_script / custom sentences) -> our kinds.
SLOT_KINDS = {"playlist": "playlist", "album": "album", "artist": "artist", "song": "song", "track": "song",
              "radio": "radio", "station": "radio", "mix": "mix", "vibe": "vibe", "genre": "radio",
              "moment": "moment", "any": "any", "media": "any", "query": "any", "name": "any"}


def from_slots(intent: str, slots: dict) -> Intent:
    """An Intent from Home Assistant's recognised intent + slots (no text parsing beyond the play query).
    intent: NasPlay | NasPause | NasResume | NasNext | NasPrevious | NasVolume | NasNowPlaying, or the HA
    built-ins HassMediaPause / HassMediaUnpause / HassMediaNext / HassMediaPrevious / HassSetVolume."""
    key = re.sub(r"^(?:nas|nasmusic|hass(?:media)?)", "", (intent or "").lower().replace("_", ""))
    slots = {str(k).lower(): v for k, v in (slots or {}).items() if v not in (None, "")}
    device = slots.get("device") or slots.get("room") or slots.get("speaker") or slots.get("name_device")
    device = clean(str(device)) if device else None
    if key in ("play", "playmedia", "search", "searchandplay", "mediasearchandplay"):
        kind = SLOT_KINDS.get(str(slots.get("kind") or slots.get("media_class") or "").lower(), None)
        query = None
        for k in ("playlist", "album", "artist", "song", "track", "radio", "station", "mix", "vibe", "genre"):
            if slots.get(k):
                query, kind = clean(str(slots[k])), kind or SLOT_KINDS[k]
                break
        if query is None:
            raw = clean(str(slots.get("query") or slots.get("search_query") or slots.get("media") or
                            slots.get("name") or ""))
            if not raw:
                return Intent("resume", device=device, text=str(slots))
            q, k2, artist = play_query(raw)
            it = Intent("play", query=q, kind=kind or k2, artist=artist, device=device, text=raw)
            if device is None:
                it.splits = [(b, d) for b, d in device_splits(raw) if b]
            return it
        artist = clean(str(slots["artist"])) if kind in ("song", "album") and slots.get("artist") else None
        return Intent("play", query=query, kind=kind or "any", artist=artist, device=device, text=str(slots))
    simple = {"pause": "pause", "stop": "pause", "resume": "resume", "unpause": "resume", "next": "next",
              "skip": "next", "previous": "previous", "prev": "previous", "nowplaying": "now_playing",
              "whatsplaying": "now_playing"}
    if key in simple:
        return Intent(simple[key], device=device)
    if key in ("volume", "setvolume", "volumeup", "volumedown"):
        it = Intent("volume", device=device)
        if key == "volumeup":
            it.volume_step = 0.1
        elif key == "volumedown":
            it.volume_step = -0.1
        else:
            lvl = slots.get("volume_level", slots.get("volume", slots.get("level")))
            try:
                v = float(str(lvl).rstrip("%"))
            except (TypeError, ValueError):
                return Intent("unknown", device=device)
            it.volume = max(0.0, min(1.0, v / 100 if v > 1 else v))
        return it
    return Intent("unknown", device=device)
