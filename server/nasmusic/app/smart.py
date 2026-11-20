"""Smart playlists (schema v7): rule-based, per-user, auto-updating playlists.

A definition is JSON (api/openapi.yaml SmartPlaylistDefinition):

    {"match": "all" | "any",
     "rules": [ {"field": "playCount", "op": "gte", "value": 5},
                {"match": "any", "rules": [ ...nested group... ]} ],
     "sort": "most_played",
     "limit": {"type": "songs" | "minutes" | "none", "value": 50}}

Every rule compiles to a parameterised SQL condition over tracks `t` (+ the caller's user_tracks `ut`, the
track's explicit verdict, the newest audio features), so evaluation is one query. Family mode is applied
in the same query (nasmusic.db.family). Field names and values are validated against FIELDS; nothing from
the definition is ever pasted into SQL except through parameters.

Results are cached in smart_playlist_tracks. The cache key (`stamp`) covers the definition, the user's
listening (events), their playlists, the library (tracks), explicit verdicts, family mode and today's date
(rules like "in the last 30 days" move every day). Reads recompute a stale cache; the nightly `smart` job
refreshes everyone's. Random order is a stable shuffle seeded by playlist id + date, so the order does not
jump around with every play.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from nasmusic.core import now_iso
from nasmusic.db import family
from nasmusic.library import queries

MAX_PER_USER = 200
MAX_RULES = 60
MAX_DEPTH = 3
MAX_TRACKS = 5000
MOODS = ("happy", "sad", "relaxed", "aggressive", "party", "acoustic")
MOOD_THRESHOLD = 0.5
EXPLICIT = ("explicit", "probable", "clean", "unknown")
THUMBS = ("up", "down", "none")
UNITS = {"days": 1, "weeks": 7, "months": 30, "years": 365}

NUM_OPS = ("eq", "ne", "gt", "gte", "lt", "lte", "between")
TEXT_OPS = ("is", "isNot", "contains", "notContains", "startsWith")
DATE_OPS = ("inLast", "notInLast", "before", "after", "never", "ever")
ENUM_OPS = ("is", "isNot")

# The newest features row of the track (any model version): audio values for mood/energy/... rules.
_FEAT = "(SELECT f.{col} FROM features f WHERE f.track_id = t.id ORDER BY f.analysed_at DESC LIMIT 1)"
_PLAYS = "COALESCE(ut.play_count, 0)"
_SKIPS = "COALESCE(ut.skip_count, 0)"
_THUMB = ("(SELECT e.type FROM events e WHERE e.user_id = ut.user_id AND e.track_id = t.id "
          "AND e.type IN ('thumb_up','thumb_down','thumb_clear') ORDER BY e.at DESC LIMIT 1)")

# field -> (type, label, SQL expression | None for special handling, extra)
FIELDS: dict[str, dict[str, Any]] = {
    "playCount": {"type": "number", "label": "Play count", "sql": _PLAYS},
    "skipCount": {"type": "number", "label": "Skip count", "sql": _SKIPS},
    "skipRate": {"type": "percent", "label": "Skip rate",
                 "sql": f"(CASE WHEN {_PLAYS} + {_SKIPS} = 0 THEN 0.0 ELSE 1.0 * {_SKIPS} / ({_PLAYS} + {_SKIPS}) END)"},
    "lastPlayed": {"type": "date", "label": "Last played", "sql": "ut.last_played"},
    "addedAt": {"type": "date", "label": "Date added", "sql": "t.added_at"},
    "favouritedAt": {"type": "date", "label": "Favourited", "sql": "ut.starred_at"},
    "favourite": {"type": "bool", "label": "Favourite", "sql": "(ut.starred_at IS NOT NULL)"},
    "rating": {"type": "number", "label": "Rating (stars)", "sql": "COALESCE(ut.rating, 0)", "min": 0, "max": 5},
    "thumbs": {"type": "enum", "label": "Thumbs", "options": list(THUMBS)},
    "genre": {"type": "text", "label": "Genre", "sql": "t.genre", "suggest": "genres"},
    "artist": {"type": "text", "label": "Artist", "sql": "ar.name", "suggest": "artists"},
    "album": {"type": "text", "label": "Album", "sql": "al.title"},
    "title": {"type": "text", "label": "Title", "sql": "t.title"},
    "year": {"type": "number", "label": "Year", "sql": "t.year"},
    "decade": {"type": "enum", "label": "Decade", "options": [str(d) for d in range(1920, 2040, 10)]},
    "durationS": {"type": "number", "label": "Duration (seconds)", "sql": "t.duration_s", "unit": "s"},
    "energy": {"type": "percent", "label": "Energy", "sql": _FEAT.format(col="energy")},
    "danceability": {"type": "percent", "label": "Danceability", "sql": _FEAT.format(col="danceability")},
    "tempo": {"type": "number", "label": "Tempo (BPM)", "sql": _FEAT.format(col="bpm"), "unit": "bpm"},
    "mood": {"type": "enum", "label": "Mood", "options": list(MOODS)},
    "explicit": {"type": "enum", "label": "Explicit", "options": list(EXPLICIT)},
    "inPlaylist": {"type": "playlist", "label": "In playlist"},
}
OPS = {"number": NUM_OPS, "percent": NUM_OPS, "text": TEXT_OPS, "date": DATE_OPS, "bool": ENUM_OPS,
       "enum": ENUM_OPS, "playlist": ENUM_OPS}

SORTS = {
    "random": None,
    "most_played": f"{_PLAYS} DESC, ut.last_played DESC",
    "least_played": f"{_PLAYS} ASC, t.added_at DESC",
    "recently_added": "t.added_at DESC, al.title COLLATE NOCASE, COALESCE(t.disc_no, 1), t.track_no",
    "oldest_added": "t.added_at ASC",
    "recently_played": "ut.last_played IS NULL, ut.last_played DESC",
    "least_recently_played": "ut.last_played IS NOT NULL, ut.last_played ASC",
    "recently_favourited": "ut.starred_at IS NULL, ut.starred_at DESC",
    "rating": "COALESCE(ut.rating, 0) DESC, ut.starred_at IS NULL",
    "title": "t.title COLLATE NOCASE",
    "artist": "ar.sort_name COLLATE NOCASE, al.year, al.title COLLATE NOCASE, COALESCE(t.disc_no, 1), t.track_no",
    "album": "al.title COLLATE NOCASE, COALESCE(t.disc_no, 1), t.track_no",
    "year_newest": "t.year IS NULL, t.year DESC",
    "year_oldest": "t.year IS NULL, t.year ASC",
    "duration_longest": "t.duration_s DESC",
    "duration_shortest": "t.duration_s ASC",
    "energy_high": f"{_FEAT.format(col='energy')} IS NULL, {_FEAT.format(col='energy')} DESC",
    "energy_low": f"{_FEAT.format(col='energy')} IS NULL, {_FEAT.format(col='energy')} ASC",
    "tempo_fast": f"{_FEAT.format(col='bpm')} IS NULL, {_FEAT.format(col='bpm')} DESC",
    "tempo_slow": f"{_FEAT.format(col='bpm')} IS NULL, {_FEAT.format(col='bpm')} ASC",
}
SORT_LABELS = {
    "random": "Random", "most_played": "Most played", "least_played": "Least played",
    "recently_added": "Recently added", "oldest_added": "Oldest added", "recently_played": "Recently played",
    "least_recently_played": "Least recently played", "recently_favourited": "Recently favourited",
    "rating": "Highest rated", "title": "Title", "artist": "Artist", "album": "Album",
    "year_newest": "Newest first", "year_oldest": "Oldest first", "duration_longest": "Longest",
    "duration_shortest": "Shortest", "energy_high": "Most energetic", "energy_low": "Calmest",
    "tempo_fast": "Fastest", "tempo_slow": "Slowest",
}

PRESETS: list[dict] = [
    {"id": "loved_not_played", "name": "Loved but not played in a month",
     "description": "Favourites and 4-5 star songs you haven't heard for 30 days.",
     "definition": {"match": "all", "rules": [
         {"match": "any", "rules": [{"field": "favourite", "op": "is", "value": True},
                                    {"field": "rating", "op": "gte", "value": 4}]},
         {"field": "lastPlayed", "op": "notInLast", "value": 1, "unit": "months"}],
         "sort": "random", "limit": {"type": "songs", "value": 100}}},
    {"id": "recently_added", "name": "Recently added",
     "description": "Everything added to the library in the last two weeks.",
     "definition": {"match": "all", "rules": [{"field": "addedAt", "op": "inLast", "value": 2, "unit": "weeks"}],
                    "sort": "recently_added", "limit": {"type": "none"}}},
    {"id": "most_played_year", "name": "Most played this year",
     "description": "Your 50 most played songs of the last 12 months.",
     "definition": {"match": "all", "rules": [{"field": "lastPlayed", "op": "inLast", "value": 1, "unit": "years"},
                                              {"field": "playCount", "op": "gte", "value": 2}],
                    "sort": "most_played", "limit": {"type": "songs", "value": 50}}},
    {"id": "forgotten_favourites", "name": "Forgotten favourites",
     "description": "Songs you used to play a lot but haven't heard for six months.",
     "definition": {"match": "all", "rules": [{"field": "playCount", "op": "gte", "value": 5},
                                              {"field": "lastPlayed", "op": "notInLast", "value": 6, "unit": "months"}],
                    "sort": "random", "limit": {"type": "minutes", "value": 120}}},
    {"id": "high_energy_never_skipped", "name": "High energy, never skipped",
     "description": "Energetic songs you always play to the end.",
     "definition": {"match": "all", "rules": [{"field": "energy", "op": "gte", "value": 0.7},
                                              {"field": "skipCount", "op": "eq", "value": 0},
                                              {"field": "thumbs", "op": "isNot", "value": "down"}],
                    "sort": "energy_high", "limit": {"type": "minutes", "value": 90}}},
]


class Invalid(ValueError):
    pass


class NotFound(Exception):
    pass


# ---------- validation ----------


def _num(v: Any, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise Invalid(f"{what}: a number is required")
    return float(v)


def normalise(defn: Any) -> dict:
    """A validated copy of a definition (defaults filled in). Raises Invalid with a readable message."""
    if not isinstance(defn, dict):
        raise Invalid("definition must be an object")
    count = [0]

    def group(g: Any, depth: int) -> dict:
        if not isinstance(g, dict):
            raise Invalid("a rule group must be an object")
        if depth > MAX_DEPTH:
            raise Invalid(f"groups can be nested at most {MAX_DEPTH} deep")
        match = g.get("match", "all")
        if match not in ("all", "any"):
            raise Invalid("match must be 'all' or 'any'")
        rules = g.get("rules") or []
        if not isinstance(rules, list):
            raise Invalid("rules must be a list")
        out = []
        for r in rules:
            if isinstance(r, dict) and "rules" in r:
                out.append(group(r, depth + 1))
            else:
                count[0] += 1
                out.append(rule(r))
        if count[0] > MAX_RULES:
            raise Invalid(f"at most {MAX_RULES} rules")
        return {"match": match, "rules": out}

    def rule(r: Any) -> dict:
        if not isinstance(r, dict):
            raise Invalid("a rule must be an object")
        field, op, value = r.get("field"), r.get("op"), r.get("value")
        spec = FIELDS.get(field)
        if spec is None:
            raise Invalid(f"unknown field {field!r}")
        typ = spec["type"]
        if op not in OPS[typ]:
            raise Invalid(f"{field}: unknown operator {op!r}")
        out: dict[str, Any] = {"field": field, "op": op}
        label = spec["label"]
        if typ in ("number", "percent"):
            if op == "between":
                if not isinstance(value, list) or len(value) != 2:
                    raise Invalid(f"{label}: 'between' needs two numbers")
                out["value"] = [_num(value[0], label), _num(value[1], label)]
            else:
                out["value"] = _num(value, label)
        elif typ == "text":
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise Invalid(f"{label}: some text is required")
            out["value"] = value.strip()
        elif typ == "date":
            if op in ("inLast", "notInLast"):
                n = _num(value, label)
                if n <= 0:
                    raise Invalid(f"{label}: the number must be positive")
                unit = r.get("unit", "days")
                if unit not in UNITS:
                    raise Invalid(f"{label}: unit must be one of {', '.join(UNITS)}")
                out["value"], out["unit"] = n, unit
            elif op in ("before", "after"):
                try:
                    date.fromisoformat(str(value)[:10])
                except ValueError:
                    raise Invalid(f"{label}: a date (YYYY-MM-DD) is required") from None
                out["value"] = str(value)[:10]
        elif typ == "bool":
            if not isinstance(value, bool):
                raise Invalid(f"{label}: true or false is required")
            out["value"] = value
        elif typ == "enum":
            if str(value) not in spec["options"]:
                raise Invalid(f"{label}: must be one of {', '.join(spec['options'])}")
            out["value"] = str(value)
        elif typ == "playlist":
            if not isinstance(value, str) or not value:
                raise Invalid(f"{label}: a playlist id is required")
            out["value"] = value
        return out

    g = group({"match": defn.get("match", "all"), "rules": defn.get("rules") or []}, 1)
    sort = defn.get("sort") or "random"
    if sort not in SORTS:
        raise Invalid(f"unknown sort {sort!r}")
    lim = defn.get("limit") or {"type": "none"}
    if not isinstance(lim, dict) or lim.get("type", "none") not in ("songs", "minutes", "none"):
        raise Invalid("limit.type must be songs, minutes or none")
    if lim.get("type", "none") == "none":
        lim = {"type": "none"}
    else:
        v = _num(lim.get("value"), "limit")
        if not 1 <= v <= (MAX_TRACKS if lim["type"] == "songs" else 10_000):
            raise Invalid("limit is out of range")
        lim = {"type": lim["type"], "value": int(v)}
    return {**g, "sort": sort, "limit": lim}


# ---------- SQL ----------


def _cutoff(n: float, unit: str, now: datetime) -> str:
    return (now - timedelta(days=n * UNITS[unit])).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _compile_rule(conn: sqlite3.Connection, uid: str, r: dict, now: datetime, self_id: str | None
                  ) -> tuple[str, list]:
    field, op, v = r["field"], r["op"], r.get("value")
    spec = FIELDS[field]
    typ = spec["type"]
    if field == "thumbs":
        cond = {"up": f"{_THUMB} = 'thumb_up'", "down": "COALESCE(ut.blocked, 0) = 1",
                "none": f"(COALESCE(ut.blocked, 0) = 0 AND COALESCE({_THUMB}, '') <> 'thumb_up')"}[v]
        return (cond if op == "is" else f"NOT ({cond})"), []
    if field == "decade":
        d = int(v)
        cond = "(t.year IS NOT NULL AND t.year BETWEEN ? AND ?)"
        return (cond if op == "is" else f"NOT {cond}"), [d, d + 9]
    if field == "mood":
        expr = f"COALESCE(json_extract({_FEAT.format(col='tags')}, ?), 0)"
        cond = f"{expr} >= ?"
        return (cond if op == "is" else f"NOT ({cond})"), [f'$."mood:{v}"', MOOD_THRESHOLD]
    if field == "explicit":
        cond = "COALESCE(fx.status, 'unknown') = ?"
        return (cond if op == "is" else f"NOT ({cond})"), [v]
    if field == "inPlaylist":
        if v == self_id:
            raise Invalid("a smart playlist can't refer to itself")
        sp = conn.execute("SELECT 1 FROM smart_playlists WHERE id = ? AND user_id = ?", (v, uid)).fetchone()
        if sp:
            sub = "SELECT track_id FROM smart_playlist_tracks WHERE smart_playlist_id = ?"
        else:
            from nasmusic.app import playlists

            if not playlists.can_view(conn, uid, v):
                raise Invalid("In playlist: no such playlist")
            sub = "SELECT track_id FROM playlist_tracks WHERE playlist_id = ?"
        return f"t.id {'IN' if op == 'is' else 'NOT IN'} ({sub})", [v]
    expr = spec["sql"]
    if typ in ("number", "percent"):
        if op == "between":
            lo, hi = sorted(v)
            return f"({expr} BETWEEN ? AND ?)", [lo, hi]
        sym = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
        if op == "ne":
            return f"({expr} IS NULL OR {expr} <> ?)", [v]
        return f"({expr} {sym} ?)", [v]
    if typ == "text":
        low = v.casefold()
        if op == "is":
            return f"({expr} = ? COLLATE NOCASE)", [v]
        if op == "isNot":
            return f"({expr} IS NULL OR {expr} <> ? COLLATE NOCASE)", [v]
        pat = "%" + low.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        if op == "startsWith":
            pat = pat[1:]
        if op == "notContains":
            return f"({expr} IS NULL OR lower({expr}) NOT LIKE ? ESCAPE '\\')", [pat]
        return f"(lower({expr}) LIKE ? ESCAPE '\\')", [pat]
    if typ == "date":
        if op == "never":
            return f"({expr} IS NULL)", []
        if op == "ever":
            return f"({expr} IS NOT NULL)", []
        if op == "inLast":
            return f"({expr} >= ?)", [_cutoff(v, r["unit"], now)]
        if op == "notInLast":
            return f"({expr} IS NULL OR {expr} < ?)", [_cutoff(v, r["unit"], now)]
        if op == "before":
            return f"({expr} < ?)", [v]
        return f"({expr} >= ?)", [(date.fromisoformat(v) + timedelta(days=1)).isoformat()]
    if typ == "bool":
        return (f"{expr}" if v == (op == "is") else f"NOT {expr}"), []
    raise Invalid(f"unsupported rule {field}")


def compile_where(conn: sqlite3.Connection, uid: str, defn: dict, now: datetime | None = None,
                  self_id: str | None = None) -> tuple[str, list]:
    """(SQL condition, params) for a normalised definition. An empty group matches everything."""
    now = now or datetime.now(UTC)

    def group(g: dict) -> tuple[str, list]:
        parts, params = [], []
        for r in g["rules"]:
            sql, p = group(r) if "rules" in r else _compile_rule(conn, uid, r, now, self_id)
            parts.append(sql)
            params += p
        if not parts:
            return "1", []
        return "(" + (" AND " if g["match"] == "all" else " OR ").join(parts) + ")", params

    return group(defn)


_BASE = """FROM tracks t JOIN artists ar ON ar.id = t.artist_id JOIN albums al ON al.id = t.album_id
LEFT JOIN user_tracks ut ON ut.track_id = t.id AND ut.user_id = ?
LEFT JOIN track_explicit fx ON fx.track_id = t.id"""


def _shuffle_key(seed: str, tid: str) -> str:
    return hashlib.sha1(f"{seed}:{tid}".encode()).hexdigest()


def evaluate(conn: sqlite3.Connection, uid: str, defn: dict, *, now: datetime | None = None,
             self_id: str | None = None, seed: str | None = None) -> list[tuple[str, float]]:
    """[(track_id, duration_s)] matching a normalised definition, sorted and limited, family-filtered."""
    now = now or datetime.now(UTC)
    where, params = compile_where(conn, uid, defn, now, self_id)
    order = SORTS[defn["sort"]]
    sql = (f"SELECT t.id, t.duration_s {_BASE} WHERE t.missing = 0 AND {where}{family.where(conn, uid)}"
           + (f" ORDER BY {order}, t.id" if order else ""))
    rows = [(r[0], float(r[1] or 0)) for r in conn.execute(sql, (uid, *params))]
    if order is None:
        s = seed or now.date().isoformat()
        rows.sort(key=lambda x: _shuffle_key(s, x[0]))
    lim = defn["limit"]
    if lim["type"] == "songs":
        rows = rows[:lim["value"]]
    elif lim["type"] == "minutes":
        out, total, budget = [], 0.0, lim["value"] * 60
        for tid, dur in rows:
            if total + dur > budget and out:
                break
            out.append((tid, dur))
            total += dur
        rows = out
    return rows[:MAX_TRACKS]


# ---------- cache ----------


def _stamp(conn: sqlite3.Connection, uid: str, row: sqlite3.Row, today: str) -> str:
    """Everything a smart playlist's result depends on. Changes -> recompute."""
    ev = conn.execute("SELECT MAX(received_at), COUNT(*) FROM events WHERE user_id = ?", (uid,)).fetchone()
    tr = conn.execute("SELECT COUNT(*), SUM(missing), MAX(mtime), MAX(added_at) FROM tracks").fetchone()
    fx = conn.execute("SELECT COUNT(*), SUM(status = 'explicit'), SUM(status = 'probable') FROM track_explicit").fetchone()
    ft = conn.execute("SELECT COUNT(*), MAX(analysed_at) FROM features").fetchone()
    pl = conn.execute("SELECT MAX(updated_at), SUM(rev), COUNT(*) FROM playlists WHERE user_id = ? OR id IN "
                      "(SELECT playlist_id FROM playlist_members WHERE user_id = ?)", (uid, uid)).fetchone()
    sp = conn.execute("SELECT MAX(updated_at), SUM(rev) FROM smart_playlists WHERE user_id = ? AND id <> ?",
                      (uid, row["id"])).fetchone()
    ut = conn.execute("SELECT COUNT(*), SUM(rating), COUNT(starred_at), SUM(blocked) FROM user_tracks "
                      "WHERE user_id = ?", (uid,)).fetchone()
    fam = family.policy(conn, uid).hidden
    blob = json.dumps([row["definition"], row["updated_at"], tuple(ev), tuple(tr), tuple(fx), tuple(ft),
                       tuple(pl), tuple(sp), tuple(ut), fam, today], default=str)
    return hashlib.sha1(blob.encode()).hexdigest()


def refresh(conn: sqlite3.Connection, row: sqlite3.Row, force: bool = False) -> bool:
    """Recompute one smart playlist's cache if stale. Returns True if the track list changed."""
    uid, pid = row["user_id"], row["id"]
    now = datetime.now(UTC)
    today = now.date().isoformat()
    stamp = _stamp(conn, uid, row, today)
    if not force and stamp == row["stamp"]:
        return False
    try:
        defn = normalise(json.loads(row["definition"]))
        rows = evaluate(conn, uid, defn, now=now, self_id=pid, seed=f"{pid}:{today}")
    except Invalid:  # e.g. the playlist an inPlaylist rule names was deleted
        rows = []
    ids = [r[0] for r in rows]
    old = [r[0] for r in conn.execute("SELECT track_id FROM smart_playlist_tracks WHERE smart_playlist_id = ? "
                                      "ORDER BY position", (pid,))]
    changed = ids != old
    conn.execute("BEGIN IMMEDIATE")
    try:
        if changed:
            conn.execute("DELETE FROM smart_playlist_tracks WHERE smart_playlist_id = ?", (pid,))
            conn.executemany("INSERT INTO smart_playlist_tracks (smart_playlist_id, position, track_id) "
                             "VALUES (?, ?, ?)", [(pid, i, t) for i, t in enumerate(ids)])
        conn.execute("UPDATE smart_playlists SET refreshed_at = ?, stamp = ?, track_count = ?, duration_s = ?, "
                     "rev = rev + ? WHERE id = ?",
                     (now_iso(), stamp, len(ids), round(sum(r[1] for r in rows), 3), int(changed), pid))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return changed


def refresh_all(conn: sqlite3.Connection) -> dict:
    """Nightly: bring every smart playlist up to date (smart downloads and radio read the cache)."""
    n = changed = 0
    for row in conn.execute("SELECT sp.* FROM smart_playlists sp JOIN users u ON u.id = sp.user_id "
                            "WHERE u.disabled = 0").fetchall():
        n += 1
        changed += refresh(conn, row)
    return {"smartPlaylists": n, "changed": changed}


# ---------- CRUD ----------


def _row(conn: sqlite3.Connection, uid: str, pid: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM smart_playlists WHERE id = ? AND user_id = ?", (pid, uid)).fetchone()
    if row is None:
        raise NotFound("no such smart playlist")
    return row


def _summary(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "name": row["name"], "definition": json.loads(row["definition"]),
            "preset": row["preset"], "createdAt": row["created_at"], "updatedAt": row["updated_at"],
            "refreshedAt": row["refreshed_at"], "trackCount": row["track_count"],
            "durationS": row["duration_s"], "rev": row["rev"]}


def track_ids(conn: sqlite3.Connection, uid: str, pid: str) -> list[str]:
    """The (fresh) cached track ids, in order. NotFound for someone else's / unknown id."""
    row = _row(conn, uid, pid)
    refresh(conn, row)
    return [r[0] for r in conn.execute("SELECT track_id FROM smart_playlist_tracks WHERE smart_playlist_id = ? "
                                       "ORDER BY position", (pid,))]


def list_all(conn: sqlite3.Connection, uid: str) -> list[dict]:
    out = []
    for row in conn.execute("SELECT * FROM smart_playlists WHERE user_id = ? ORDER BY name COLLATE NOCASE, id",
                            (uid,)).fetchall():
        refresh(conn, row)
        out.append(_summary(_row(conn, uid, row["id"])))
    return out


def summary_for(conn: sqlite3.Connection, uid: str, pid: str) -> dict | None:
    try:
        row = _row(conn, uid, pid)
    except NotFound:
        return None
    refresh(conn, row)
    return _summary(_row(conn, uid, pid))


def get(conn: sqlite3.Connection, uid: str, pid: str) -> dict:
    ids = track_ids(conn, uid, pid)
    by_id = queries.tracks_by_ids(conn, uid, ids)
    return {**_summary(_row(conn, uid, pid)), "tracks": [by_id[i] for i in ids if i in by_id]}


def create(conn: sqlite3.Connection, uid: str, name: str, definition: Any, preset: str | None = None) -> dict:
    defn = normalise(definition)
    name = (name or "").strip()
    if not name:
        raise Invalid("a name is required")
    if conn.execute("SELECT COUNT(*) FROM smart_playlists WHERE user_id = ?", (uid,)).fetchone()[0] >= MAX_PER_USER:
        raise Invalid(f"at most {MAX_PER_USER} smart playlists")
    compile_where(conn, uid, defn)  # validates playlist references
    pid = "sp" + uuid.uuid4().hex
    now = now_iso()
    conn.execute("INSERT INTO smart_playlists (id, user_id, name, definition, preset, created_at, updated_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)", (pid, uid, name[:200], json.dumps(defn), preset, now, now))
    return get(conn, uid, pid)


def update(conn: sqlite3.Connection, uid: str, pid: str, name: str | None, definition: Any | None) -> dict:
    row = _row(conn, uid, pid)
    new_name = row["name"] if name is None else name.strip()[:200]
    if not new_name:
        raise Invalid("a name is required")
    defn = json.loads(row["definition"]) if definition is None else normalise(definition)
    compile_where(conn, uid, defn, self_id=pid)
    conn.execute("UPDATE smart_playlists SET name = ?, definition = ?, updated_at = ?, stamp = NULL WHERE id = ?",
                 (new_name, json.dumps(defn), now_iso(), pid))
    return get(conn, uid, pid)


def delete(conn: sqlite3.Connection, uid: str, pid: str) -> None:
    _row(conn, uid, pid)
    conn.execute("DELETE FROM smart_playlists WHERE id = ?", (pid,))
    conn.execute("DELETE FROM pins WHERE user_id = ? AND type = 'smart_playlist' AND item_id = ?", (uid, pid))


def preview(conn: sqlite3.Connection, uid: str, definition: Any, sample: int = 25, self_id: str | None = None
            ) -> dict:
    defn = normalise(definition)
    rows = evaluate(conn, uid, defn, self_id=self_id)
    ids = [r[0] for r in rows[:sample]]
    by_id = queries.tracks_by_ids(conn, uid, ids)
    return {"trackCount": len(rows), "durationS": round(sum(r[1] for r in rows), 3),
            "tracks": [by_id[i] for i in ids if i in by_id]}


def fields() -> dict:
    """The rule editor's catalogue: fields with their type, operators and options; sorts; presets."""
    return {
        "fields": [{"id": k, "label": v["label"], "type": v["type"], "ops": list(OPS[v["type"]]),
                    **({"options": v["options"]} if "options" in v else {}),
                    **({"suggest": v["suggest"]} if "suggest" in v else {}),
                    **({"unit": v["unit"]} if "unit" in v else {})} for k, v in FIELDS.items()],
        "sorts": [{"id": k, "label": SORT_LABELS[k]} for k in SORTS],
        "units": list(UNITS),
        "presets": PRESETS,
    }
