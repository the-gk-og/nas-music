"""The "On this day" Home shelf: what the user played around today's date in earlier years.

"Around" = within WINDOW_DAYS either side of today's local date (the user's `timezone` setting, else the
server's), in each earlier year that has plays. Imported listening history counts too, which is what makes
the shelf useful on a new server. Per year the most-played tracks come first; years are mixed so one busy year
doesn't fill the shelf."""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homebeat.core import iso
from homebeat.library import queries

WINDOW_DAYS = 3
PER_YEAR = 4      # tracks per earlier year before another round
MAX_ITEMS = 12


def _tz(conn: sqlite3.Connection, uid: str, default: str) -> ZoneInfo:
    name = default
    try:
        r = conn.execute("SELECT value FROM user_settings WHERE user_id = ? AND key = 'timezone'", (uid,)).fetchone()
        v = json.loads(r[0]) if r else None
        if isinstance(v, str) and v:
            name = v
    except (sqlite3.Error, ValueError):
        pass
    for n in (name, default, "UTC"):
        try:
            return ZoneInfo(n)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return ZoneInfo("UTC")


def _same_day(d: date, year: int) -> date:
    try:
        return d.replace(year=year)
    except ValueError:  # 29 February in a year without one
        return date(year, 2, 28)


def _window(d: date, year: int, tz: ZoneInfo) -> tuple[str, str]:
    mid = _same_day(d, year)
    start = datetime.combine(mid - timedelta(days=WINDOW_DAYS), time.min, tzinfo=tz)
    end = datetime.combine(mid + timedelta(days=WINDOW_DAYS + 1), time.min, tzinfo=tz)
    return iso(start), iso(end)


def _ago(n: int) -> str:
    return "1 year ago" if n == 1 else f"{n} years ago"


def items(conn: sqlite3.Connection, uid: str, default_tz: str, now: datetime | None = None) -> list[dict]:
    """Track ShelfItems for the shelf (empty when there is nothing from earlier years)."""
    tz = _tz(conn, uid, default_tz)
    today = (now or datetime.now(UTC)).astimezone(tz).date()
    first = conn.execute("SELECT MIN(at) FROM events WHERE user_id = ? AND type = 'play'", (uid,)).fetchone()[0]
    if not first:
        return []
    first_year = int(first[:4])
    per_year: list[tuple[int, list[tuple[str, int]]]] = []
    for year in range(today.year - 1, first_year - 1, -1):
        lo, hi = _window(today, year, tz)
        rows = conn.execute(
            "SELECT track_id, COUNT(*) n, MAX(at) last FROM events WHERE user_id = ? AND type = 'play' "
            "AND at >= ? AND at < ? GROUP BY track_id ORDER BY n DESC, last DESC LIMIT 40", (uid, lo, hi)).fetchall()
        if rows:
            per_year.append((year, [(r["track_id"], r["n"]) for r in rows]))
    if not per_year:
        return []
    tracks = queries.tracks_by_ids(conn, uid, [t for _, ts in per_year for t, _ in ts])  # present + visible only
    picked: dict[str, tuple[int, int]] = {}            # track -> (year, plays); most recent year wins
    queues = [(year, [t for t in ts if t[0] in tracks]) for year, ts in per_year]
    while len(picked) < MAX_ITEMS and any(q for _, q in queues):
        for year, q in queues:
            taken = 0
            while q and taken < PER_YEAR and len(picked) < MAX_ITEMS:
                tid, n = q.pop(0)
                if tid not in picked:
                    picked[tid] = (year, n)
                    taken += 1
    out = []
    for tid, (year, n) in sorted(picked.items(), key=lambda kv: (-kv[1][0], -kv[1][1])):
        t = tracks[tid]
        plays = "played once" if n == 1 else f"played {n} times"
        out.append({"type": "track", "id": tid, "title": t["title"],
                    "subtitle": f"{t['artist']} · {_ago(today.year - year)}, {plays}",
                    "artUrl": f"art/{t['albumId']}", "track": t})
    return out


def subtitle(conn: sqlite3.Connection, uid: str, default_tz: str, now: datetime | None = None) -> str:
    today = (now or datetime.now(UTC)).astimezone(_tz(conn, uid, default_tz)).date()
    return f"Around {today.day} {today.strftime('%B')} in earlier years"
