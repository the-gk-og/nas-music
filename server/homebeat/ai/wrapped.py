"""Wrapped: a user's listening year (or the year so far) from their own `events`, plus an "audio aura"
from the shared Essentia features. Server-wide totals for admins.

Everything here is computed on request from SQL (one pass over the year's events, a few thousand rows
for a heavy listener), so no table or nightly job is needed.

Definitions (the tests pin these down):
* listened seconds of an event: `play` → position_s clipped to the track length (whole track when the
  client sent no position); `skip` → position_s clipped (0 without one). Other event types don't count.
* plays = number of `play` events. Minutes = listened seconds / 60 (play + skip).
* local time = the user's `timezone` setting, else the server timezone. Days, hours and weekdays are local.
* streak = consecutive local days with at least one play. `current` counts back from today (or from
  yesterday, so a streak isn't "broken" before you've had a chance to listen today); 0 once the year is over.
* discovery = an artist / track whose first play ever by this user falls inside the period.
"""
from __future__ import annotations

import colorsys
import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from .common import local_tz, parse_ts

TOP_N = 10
MOODS = ("happy", "sad", "relaxed", "aggressive", "party")


# ---------------------------------------------------------------- pure maths

def listened_seconds(etype: str, position_s: float | None, track_duration_s: float | None) -> float:
    dur = float(track_duration_s or 0)
    if etype == "play":
        if position_s is None or position_s <= 0:
            return dur
        return min(float(position_s), dur) if dur > 0 else float(position_s)
    if etype == "skip":
        if position_s is None or position_s <= 0:
            return 0.0
        return min(float(position_s), dur) if dur > 0 else float(position_s)
    return 0.0


def streaks(days: Iterable[date], today: date | None = None) -> dict:
    """{longest, longestFrom, longestTo, current} over a set of local days with plays."""
    ds = sorted(set(days))
    best, best_from, best_to = 0, None, None
    run, run_from, prev = 0, None, None
    for d in ds:
        if prev is not None and d == prev + timedelta(days=1):
            run += 1
        else:
            run, run_from = 1, d
        if run > best:
            best, best_from, best_to = run, run_from, d
        prev = d
    current = 0
    if today is not None and ds:
        have = set(ds)
        start = today if today in have else today - timedelta(days=1)
        d = start
        while d in have:
            current += 1
            d -= timedelta(days=1)
    return {"longest": best, "longestFrom": best_from.isoformat() if best_from else None,
            "longestTo": best_to.isoformat() if best_to else None, "current": current}


def split_genres(genre: str | None) -> list[str]:
    if not genre:
        return []
    parts = [p.strip() for chunk in genre.replace("/", ";").split(";") for p in chunk.split(",")]
    return [p for p in parts if p]


def _hsl_hex(h: float, s: float, l: float) -> str:
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360, l, s)
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


AURAS = {
    # (energy band, mood band) -> (label, description)
    ("high", "bright"): ("Electric", "Loud, upbeat and bright — music that moves."),
    ("high", "dark"): ("Fierce", "Heavy, intense and a little stormy."),
    ("high", "mid"): ("Charged", "High energy without picking a side."),
    ("low", "bright"): ("Sunlit", "Gentle, warm and easy-going."),
    ("low", "dark"): ("Midnight", "Quiet, moody and reflective."),
    ("low", "mid"): ("Drift", "Calm, soft-edged and unhurried."),
    ("mid", "bright"): ("Golden", "Feel-good and steady."),
    ("mid", "dark"): ("Velvet", "Bittersweet, with a slow-burning glow."),
    ("mid", "mid"): ("Balanced", "A bit of everything, in equal measure."),
}


def aura(values: dict[str, float | None]) -> dict | None:
    """Label + colours from minutes-weighted averages: energy, danceability (0..1) and the mood
    probabilities happy/sad/relaxed/aggressive/party (None when the features have no mood heads)."""
    energy = values.get("energy")
    if energy is None:
        return None
    happy, sad = values.get("happy"), values.get("sad")
    valence = (happy - sad) if happy is not None and sad is not None else None
    dance = values.get("danceability")
    e_band = "high" if energy >= 0.58 else "low" if energy <= 0.42 else "mid"
    if valence is None:  # basic features (no mood heads): lean on danceability for the mood axis
        v_band = "bright" if (dance or 0) >= 0.6 else "mid"
    else:
        v_band = "bright" if valence >= 0.12 else "dark" if valence <= -0.12 else "mid"
    label, desc = AURAS[(e_band, v_band)]
    v = 0.0 if valence is None else max(-1.0, min(1.0, valence * 2))
    hue1 = 230 - (v + 1) / 2 * 190              # sad → blue (230), happy → amber (40)
    hue2 = hue1 + 35 + 45 * (dance or 0.5)       # danceable pulls the second colour towards magenta
    sat = 0.45 + 0.45 * energy
    light = 0.62 - 0.18 * energy
    moods = sorted(((m, values[m]) for m in MOODS if values.get(m) is not None), key=lambda kv: -kv[1])
    return {"label": label, "description": desc,
            "energy": round(energy, 3), "danceability": None if dance is None else round(dance, 3),
            "valence": None if valence is None else round(valence, 3),
            "bpm": None if values.get("bpm") is None else round(values["bpm"]),
            "moods": [{"name": m, "score": round(s, 3)} for m, s in moods],
            "colors": [_hsl_hex(hue1, sat, light), _hsl_hex(hue2, sat, light - 0.08)]}


# ---------------------------------------------------------------- data access

@dataclass
class Period:
    year: int
    start: datetime      # UTC
    end: datetime        # UTC, exclusive
    tz: str
    final: bool          # the year is over
    today: date | None   # local today when inside the year


def period(year: int, tz_name: str, now: datetime | None = None) -> Period:
    tz = local_tz(tz_name)
    now = now or datetime.now(UTC)
    start = datetime(year, 1, 1, tzinfo=tz).astimezone(UTC)
    end = datetime(year + 1, 1, 1, tzinfo=tz).astimezone(UTC)
    local_today = now.astimezone(tz).date()
    final = now >= end
    return Period(year, start, min(end, now) if not final else end, tz_name, final,
                  local_today if local_today.year == year else None)


def user_timezone(conn: sqlite3.Connection, user_id: str, default: str) -> str:
    try:
        r = conn.execute("SELECT value FROM user_settings WHERE user_id = ? AND key = 'timezone'",
                         (user_id,)).fetchone()
        tz = json.loads(r[0]) if r else None
    except (sqlite3.Error, ValueError):
        tz = None
    return tz if isinstance(tz, str) and tz else default


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _events(conn: sqlite3.Connection, user_id: str | None, p: Period) -> list[tuple]:
    """(user_id, track_id, type, at datetime, seconds, artist_id, album_id, genre) for the period."""
    # String bounds a day wider than needed (timestamps may carry offsets or fractions); exact below.
    lo, hi = _iso(p.start - timedelta(days=1)), _iso(p.end + timedelta(days=1))
    where = "e.at >= ? AND e.at < ? AND e.type IN ('play', 'skip')"
    args: list = [lo, hi]
    if user_id is not None:
        where += " AND e.user_id = ?"
        args.append(user_id)
    out = []
    for r in conn.execute(
        "SELECT e.user_id, e.track_id, e.type, e.at, e.position_s, t.duration_s, t.artist_id, t.album_id, t.genre "
        f"FROM events e JOIN tracks t ON t.id = e.track_id WHERE {where}", args
    ):
        at = parse_ts(r[3])
        if at is None or not (p.start <= at < p.end):
            continue
        out.append((r[0], r[1], r[2], at, listened_seconds(r[2], r[4], r[5]), r[6], r[7], r[8]))
    return out


def _features(conn: sqlite3.Connection, track_ids: list[str]) -> dict[str, tuple]:
    """track_id -> (energy, danceability, bpm, tags dict) from the newest features row of each track."""
    out: dict[str, tuple] = {}
    ids = list(track_ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in conn.execute(
            "SELECT track_id, energy, danceability, bpm, tags, analysed_at FROM features "
            f"WHERE track_id IN ({','.join('?' * len(chunk))}) ORDER BY analysed_at", chunk
        ):
            try:
                tags = json.loads(r[4]) if r[4] else {}
            except ValueError:
                tags = {}
            out[r[0]] = (r[1], r[2], r[3], tags if isinstance(tags, dict) else {})
    return out


def _top(d: dict[str, list[float]], n: int = TOP_N) -> list[dict]:
    """d: id -> [plays, seconds]. By plays, then minutes, then id."""
    rows = sorted(d.items(), key=lambda kv: (-kv[1][0], -kv[1][1], kv[0]))[:n]
    return [{"id": k, "plays": int(v[0]), "minutes": round(v[1] / 60, 1)} for k, v in rows]


def _top_by_minutes(d: dict[str, list[float]], n: int = TOP_N) -> list[dict]:
    rows = sorted(d.items(), key=lambda kv: (-kv[1][1], -kv[1][0], kv[0]))[:n]
    return [{"id": k, "plays": int(v[0]), "minutes": round(v[1] / 60, 1)} for k, v in rows]


def _aggregate(conn: sqlite3.Connection, evs: list[tuple], p: Period) -> dict:
    tz = local_tz(p.tz)
    tracks: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    artists: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    albums: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    genres: dict[str, float] = defaultdict(float)
    by_hour, by_weekday, by_month = [0.0] * 24, [0.0] * 7, [0.0] * 12
    by_day: dict[date, float] = defaultdict(float)
    play_days: set[date] = set()
    plays = skips = 0
    seconds = 0.0
    first = None
    for _uid, tid, etype, at, secs, aid, alid, genre in evs:
        local = at.astimezone(tz)
        is_play = etype == "play"
        plays += is_play
        skips += not is_play
        seconds += secs
        for d in (tracks[tid], artists[aid], albums[alid]):
            d[0] += is_play
            d[1] += secs
        gs = split_genres(genre)
        for g in gs:
            genres[g] += secs / len(gs)
        by_hour[local.hour] += secs
        by_weekday[local.weekday()] += secs
        by_month[local.month - 1] += secs
        by_day[local.date()] += secs
        if is_play:
            play_days.add(local.date())
            if first is None or at < first[1]:
                first = (tid, at)
    total_g = sum(genres.values()) or 1.0
    top_genres = sorted(genres.items(), key=lambda kv: (-kv[1], kv[0].casefold()))[:8]
    top_day = max(by_day.items(), key=lambda kv: (kv[1], kv[0])) if by_day else None
    mins = lambda xs: [round(x / 60, 1) for x in xs]
    return {
        "totals": {"minutes": round(seconds / 60), "plays": plays, "skips": skips,
                   "tracks": sum(1 for v in tracks.values() if v[0] > 0),
                   "artists": sum(1 for v in artists.values() if v[0] > 0),
                   "albums": sum(1 for v in albums.values() if v[0] > 0), "days": len(play_days)},
        "topTracks": _top(tracks), "topArtists": _top_by_minutes(artists), "topAlbums": _top_by_minutes(albums),
        "topGenres": [{"name": g, "minutes": round(s / 60, 1), "share": round(s / total_g, 3)} for g, s in top_genres],
        "byHour": mins(by_hour), "byWeekday": mins(by_weekday), "byMonth": mins(by_month),
        "peakHour": max(range(24), key=lambda h: (by_hour[h], -h)) if seconds else None,
        "peakWeekday": max(range(7), key=lambda d: (by_weekday[d], -d)) if seconds else None,
        "topDay": {"date": top_day[0].isoformat(), "minutes": round(top_day[1] / 60)} if top_day else None,
        "streaks": streaks(play_days, None if p.final else p.today),
        "firstPlay": {"trackId": first[0], "at": _iso(first[1])} if first else None,
        "_seconds": {"tracks": tracks, "artists": artists},
    }


def _aura_values(conn: sqlite3.Connection, track_seconds: dict[str, list[float]]) -> dict[str, float | None]:
    feats = _features(conn, [t for t, v in track_seconds.items() if v[1] > 0])
    acc: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])  # key -> [weighted sum, weight]
    for tid, (energy, dance, bpm, tags) in feats.items():
        w = track_seconds[tid][1]
        if w <= 0:
            continue
        for key, val in (("energy", energy), ("danceability", dance), ("bpm", bpm),
                         *((m, tags.get(f"mood:{m}")) for m in MOODS)):
            if val is not None:
                acc[key][0] += float(val) * w
                acc[key][1] += w
    return {k: (s / w if w else None) for k, (s, w) in acc.items()}


def _discoveries(conn: sqlite3.Connection, user_id: str, p: Period, agg: dict) -> dict:
    """Artists / tracks first played (ever, by this user) inside the period."""
    start = _iso(p.start)
    before_artists = {r[0] for r in conn.execute(
        "SELECT DISTINCT t.artist_id FROM events e JOIN tracks t ON t.id = e.track_id "
        "WHERE e.user_id = ? AND e.type = 'play' AND e.at < ?", (user_id, start))}
    before_tracks = {r[0] for r in conn.execute(
        "SELECT DISTINCT track_id FROM events WHERE user_id = ? AND type = 'play' AND at < ?", (user_id, start))}
    artists = {a: v for a, v in agg["_seconds"]["artists"].items() if v[0] > 0 and a not in before_artists}
    tracks = [t for t, v in agg["_seconds"]["tracks"].items() if v[0] > 0 and t not in before_tracks]
    new_secs = sum(v[1] for v in artists.values())
    all_secs = sum(v[1] for v in agg["_seconds"]["artists"].values()) or 1.0
    return {"artists": len(artists), "tracks": len(tracks), "share": round(new_secs / all_secs, 3),
            "topArtists": _top_by_minutes(artists, 5)}


def user_wrapped(conn: sqlite3.Connection, user_id: str, year: int, tz_name: str,
                 now: datetime | None = None) -> dict:
    """The user's Wrapped for `year` (so far, while it's running). Ids only; server core resolves them."""
    p = period(year, tz_name, now)
    evs = _events(conn, user_id, p)
    agg = _aggregate(conn, evs, p)
    agg["discoveries"] = _discoveries(conn, user_id, p, agg)
    agg["aura"] = aura(_aura_values(conn, agg["_seconds"]["tracks"]))
    del agg["_seconds"]
    agg.update(year=year, final=p.final, timezone=tz_name, **{"from": _iso(p.start), "to": _iso(p.end)})
    return agg


def server_wrapped(conn: sqlite3.Connection, year: int, tz_name: str, now: datetime | None = None) -> dict:
    """Server-wide totals for admins: aggregates only (no per-user breakdown of what anyone played)."""
    p = period(year, tz_name, now)
    evs = _events(conn, None, p)
    agg = _aggregate(conn, evs, p)
    agg["aura"] = aura(_aura_values(conn, agg["_seconds"]["tracks"]))
    del agg["_seconds"], agg["firstPlay"], agg["streaks"]
    agg["listeners"] = len({e[0] for e in evs if e[2] == "play"})
    agg.update(year=year, final=p.final, timezone=tz_name, **{"from": _iso(p.start), "to": _iso(p.end)})
    return agg


def years(conn: sqlite3.Connection, user_id: str | None) -> list[int]:
    """Years (UTC, close enough for a picker) that have plays, newest first."""
    q = "SELECT DISTINCT substr(at, 1, 4) FROM events WHERE type = 'play'"
    args: tuple = ()
    if user_id is not None:
        q += " AND user_id = ?"
        args = (user_id,)
    return sorted((int(r[0]) for r in conn.execute(q, args) if r[0] and r[0].isdigit()), reverse=True)
