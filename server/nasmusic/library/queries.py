"""Read queries returning API-shaped dicts (see components/schemas in api/openapi.yaml).

Track dicts are per user (`starred` / `rating` are the caller's): every TRACK_SELECT query takes the
user id as its FIRST parameter. Missing tracks, and albums/artists with no present tracks, are hidden
from browsing (playlists and queues still list them, flagged `missing`).

Family mode (schema v6, nasmusic.db.family): every query that takes the user id hides the tracks that user's
policy hides (and albums/artists/genres/folders left with none). Queues and playlists keep such tracks but
flag them `familyHidden` (tracks_by_ids(..., hidden="flag")), so positions stay stable and clients skip them."""
from __future__ import annotations

import difflib
import posixpath
import re
import sqlite3
import time
import unicodedata
from pathlib import Path

from nasmusic.db import family
from nasmusic.library import art, loudness

TRACK_COLS = """t.id, t.title, t.artist_id, ar.name AS artist, t.album_id, al.title AS album, t.track_no, t.disc_no,
       t.year, t.genre, t.duration_s, t.flac_bytes, t.missing, ut.starred_at, ut.rating,
       t.rg_track_gain, t.rg_track_peak, t.rg_album_gain, t.rg_album_peak, fx.status AS explicit_status,
       dj.bpm AS dj_bpm, dj.beat_s AS dj_beat_s, dj.downbeat_s AS dj_downbeat_s, dj.beats_per_bar AS dj_beats_per_bar,
       dj.grid_stable AS dj_grid_stable, dj.key AS dj_key, dj.camelot AS dj_camelot, dj.mix_in_s AS dj_mix_in_s,
       dj.mix_out_s AS dj_mix_out_s, dj.intro_end_s AS dj_intro_end_s, dj.outro_start_s AS dj_outro_start_s,
       dj.last_beat_s AS dj_last_beat_s, dj.energy AS dj_energy"""
TRACK_FROM = """FROM tracks t JOIN artists ar ON ar.id = t.artist_id JOIN albums al ON al.id = t.album_id
LEFT JOIN user_tracks ut ON ut.track_id = t.id AND ut.user_id = ?
LEFT JOIN track_explicit fx ON fx.track_id = t.id
LEFT JOIN track_dj dj ON dj.track_id = t.id AND dj.status = 'done'"""
TRACK_SELECT = f"SELECT {TRACK_COLS}\n{TRACK_FROM}\n"


def album_select(fam: str = "") -> str:
    """Albums with present tracks; `fam` is a family.where() fragment on alias `t` (counts, duration and
    the explicit marker then only cover the tracks the user may see, and empty albums disappear)."""
    return f"""
SELECT al.id, al.title, al.artist_id, ar.name AS artist, al.year, al.mbid, al.art_path, al.added_at,
       s.n AS track_count, s.dur AS duration_s, s.explicit AS has_explicit
FROM albums al JOIN artists ar ON ar.id = al.artist_id
JOIN (SELECT t.album_id, COUNT(*) AS n, SUM(t.duration_s) AS dur,
             MAX(EXISTS (SELECT 1 FROM track_explicit ax WHERE ax.track_id = t.id AND ax.status = 'explicit')) AS explicit
      FROM tracks t WHERE t.missing = 0{fam} GROUP BY t.album_id) s
  ON s.album_id = al.id
"""


def artist_select(fam: str = "") -> str:
    return f"""
SELECT ar.id, ar.name, ar.mbid, ar.sort_name, COUNT(DISTINCT al.id) AS album_count
FROM artists ar JOIN albums al ON al.artist_id = ar.id
WHERE EXISTS (SELECT 1 FROM tracks t WHERE t.album_id = al.id AND t.missing = 0{fam})
"""


ALBUM_SELECT = album_select()
ARTIST_SELECT = artist_select()


def fam(conn: sqlite3.Connection, uid: str | None, alias: str = "t") -> str:
    """The family-mode WHERE fragment for this user ("" when nothing is hidden)."""
    return family.where(conn, uid, alias)

ALBUM_ORDER = "ORDER BY ar.sort_name COLLATE NOCASE, al.title COLLATE NOCASE, COALESCE(t.disc_no, 1), " \
              "t.track_no IS NULL, t.track_no, t.path"


def track_dict(r: sqlite3.Row) -> dict:
    d = {
        "id": r["id"], "title": r["title"], "artistId": r["artist_id"], "artist": r["artist"],
        "albumId": r["album_id"], "album": r["album"], "trackNo": r["track_no"], "discNo": r["disc_no"],
        "year": r["year"], "genre": r["genre"], "durationS": r["duration_s"], "flacBytes": r["flac_bytes"],
        "starred": r["starred_at"] is not None, "rating": r["rating"],
        "explicit": (r["explicit_status"] if "explicit_status" in r.keys() else None) or "unknown",
    }
    if r["missing"]:
        d["missing"] = True
    rg = loudness.replay_gain(r)
    if rg is not None:
        d["replayGain"] = rg
    mix = track_mix(r)
    if mix is not None:
        d["mix"] = mix
    return d


_MIX_FIELDS = (("bpm", "bpm"), ("beatS", "beat_s"), ("downbeatS", "downbeat_s"), ("beatsPerBar", "beats_per_bar"),
               ("key", "key"), ("camelot", "camelot"), ("mixInS", "mix_in_s"), ("mixOutS", "mix_out_s"),
               ("introEndS", "intro_end_s"), ("outroStartS", "outro_start_s"), ("lastBeatS", "last_beat_s"),
               ("energy", "energy"))


def track_mix(r: sqlite3.Row) -> dict | None:
    """v7 Track.mix (openapi TrackMix) from the dj_* columns of TRACK_SELECT (track_dj, written by the AI's
    automix job). None until the track is analysed, or for queries that don't select them."""
    keys = r.keys()
    if "dj_mix_out_s" not in keys or (r["dj_bpm"] is None and r["dj_mix_out_s"] is None):
        return None
    out = {k: r["dj_" + c] for k, c in _MIX_FIELDS if r["dj_" + c] is not None}
    out["gridStable"] = bool(r["dj_grid_stable"])
    return out


def album_dict(r: sqlite3.Row, data_dir: Path) -> dict:
    return {
        "id": r["id"], "title": r["title"], "artistId": r["artist_id"], "artist": r["artist"],
        "year": r["year"], "trackCount": r["track_count"], "durationS": r["duration_s"] or 0.0,
        "hasArt": art.has_art(data_dir, r["art_path"], r["id"]), "mbid": r["mbid"],
        "explicit": bool(r["has_explicit"]) if "has_explicit" in r.keys() else False,
    }


def artist_dict(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "name": r["name"], "albumCount": r["album_count"], "mbid": r["mbid"]}


def artists(conn: sqlite3.Connection, offset: int, limit: int, uid: str | None = None) -> list[dict]:
    rows = conn.execute(
        artist_select(fam(conn, uid)) + " GROUP BY ar.id ORDER BY ar.sort_name COLLATE NOCASE, ar.id LIMIT ? OFFSET ?",
        (limit, offset),
    )
    return [artist_dict(r) for r in rows]


def artist(conn: sqlite3.Connection, artist_id: str, data_dir: Path, uid: str | None = None) -> dict | None:
    r = conn.execute(artist_select(fam(conn, uid)) + " AND ar.id = ? GROUP BY ar.id", (artist_id,)).fetchone()
    if r is None:
        return None
    d = artist_dict(r)
    d["albums"] = albums(conn, data_dir, artist_id=artist_id, sort="year", offset=0, limit=10_000, uid=uid)
    return d


_ALBUM_SORT = {
    "name": "al.title COLLATE NOCASE, al.id",
    "year": "al.year IS NULL, al.year, al.title COLLATE NOCASE, al.id",
    "added": "al.added_at DESC, al.title COLLATE NOCASE, al.id",
}


def albums(conn: sqlite3.Connection, data_dir: Path, artist_id: str | None, sort: str, offset: int,
           limit: int, uid: str | None = None) -> list[dict]:
    where, params = "", []
    if artist_id:
        where, params = " WHERE al.artist_id = ?", [artist_id]
    rows = conn.execute(
        album_select(fam(conn, uid)) + where + f" ORDER BY {_ALBUM_SORT.get(sort, _ALBUM_SORT['name'])} LIMIT ? OFFSET ?",
        (*params, limit, offset),
    )
    return [album_dict(r, data_dir) for r in rows]


def album(conn: sqlite3.Connection, uid: str, album_id: str, data_dir: Path) -> dict | None:
    f = fam(conn, uid)
    r = conn.execute(album_select(f) + " WHERE al.id = ?", (album_id,)).fetchone()
    if r is None:
        return None
    d = album_dict(r, data_dir)
    rows = conn.execute(
        TRACK_SELECT + f" WHERE t.album_id = ? AND t.missing = 0{f} "
        "ORDER BY COALESCE(t.disc_no, 1), t.track_no IS NULL, t.track_no, t.path",
        (uid, album_id),
    )
    d["tracks"] = [track_dict(t) for t in rows]
    return d


def album_art_info(conn: sqlite3.Connection, album_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT id, art_path FROM albums WHERE id = ?", (album_id,)).fetchone()


def track(conn: sqlite3.Connection, uid: str, track_id: str) -> dict | None:
    r = conn.execute(TRACK_SELECT + f" WHERE t.id = ? AND t.missing = 0{fam(conn, uid)}", (uid, track_id)).fetchone()
    return track_dict(r) if r else None


def tracks_by_ids(conn: sqlite3.Connection, uid: str, ids: list[str], include_missing: bool = False,
                  hidden: str = "drop") -> dict[str, dict]:
    """Tracks by id for this user. Family mode: `hidden="drop"` (default; mixes, Home, blends, AI results)
    leaves out tracks the user may not see; "flag" (queues, playlists) keeps them with familyHidden=true."""
    out: dict[str, dict] = {}
    uniq = list(dict.fromkeys(ids))
    p = family.policy(conn, uid)
    for i in range(0, len(uniq), 500):
        chunk = uniq[i:i + 500]
        q = TRACK_SELECT + f" WHERE t.id IN ({','.join('?' * len(chunk))})"
        if not include_missing:
            q += " AND t.missing = 0"
        if p.private_others:  # other people's "only me" uploads are never shown, not even flagged
            q += " AND " + family.private_condition(p, "t")
        for r in conn.execute(q, (uid, *chunk)):
            d = track_dict(r)
            if p.hides(d["explicit"]):
                if hidden == "drop":
                    continue
                d["familyHidden"] = True
            out[r["id"]] = d
    return out


def track_file(conn: sqlite3.Connection, track_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, path, mtime, flac_bytes, duration_s, codec, bitrate, sample_rate, bits_per_sample, channels "
        "FROM tracks "
        "WHERE id = ? AND missing = 0", (track_id,)
    ).fetchone()


def track_info(conn: sqlite3.Connection, uid: str, track_id: str) -> dict | None:
    r = conn.execute(
        f"SELECT {TRACK_COLS}, t.path, t.folder, t.album_artist, t.composer, t.lyricist, t.comment, t.codec, "
        "t.bitrate, t.sample_rate, t.bits_per_sample, t.channels, t.added_at, "
        "COALESCE(ut.play_count, 0) AS play_count, COALESCE(ut.skip_count, 0) AS skip_count, ut.last_played "
        f"{TRACK_FROM} WHERE t.id = ? AND t.missing = 0{fam(conn, uid)}", (uid, track_id),
    ).fetchone()
    if r is None:
        return None
    return {
        **track_dict(r), "fileName": posixpath.basename(r["path"]), "folder": r["folder"],
        "albumArtist": r["album_artist"], "composer": r["composer"], "lyricist": r["lyricist"],
        "comment": r["comment"], "codec": r["codec"], "bitrateKbps": r["bitrate"], "sampleRate": r["sample_rate"],
        "bitsPerSample": r["bits_per_sample"], "channels": r["channels"], "fileBytes": r["flac_bytes"],
        "addedAt": r["added_at"], "playCount": r["play_count"], "skipCount": r["skip_count"],
        "lastPlayed": r["last_played"],
    }


_TRACK_SORT = {
    "title": "t.title COLLATE NOCASE, ar.sort_name COLLATE NOCASE, t.id",
    "added": "t.added_at DESC, al.title COLLATE NOCASE, COALESCE(t.disc_no, 1), t.track_no, t.id",
    "year": "t.year IS NULL, t.year DESC, al.title COLLATE NOCASE, COALESCE(t.disc_no, 1), t.track_no, t.id",
}


def all_tracks(conn: sqlite3.Connection, uid: str, sort: str, offset: int, limit: int) -> list[dict]:
    rows = conn.execute(
        TRACK_SELECT + f" WHERE t.missing = 0{fam(conn, uid)} ORDER BY {_TRACK_SORT[sort]} LIMIT ? OFFSET ?",
        (uid, limit, offset))
    return [track_dict(r) for r in rows]


# ---------- smart lists ----------

LIST_KINDS = ("favourites", "recently_added", "recently_played", "most_played", "not_played")
_LISTS = {
    "favourites": ("ut.starred_at IS NOT NULL", "ut.starred_at DESC, t.id"),
    "recently_added": ("1", "t.added_at DESC, al.title COLLATE NOCASE, COALESCE(t.disc_no, 1), t.track_no, t.id"),
    "recently_played": ("ut.last_played IS NOT NULL", "ut.last_played DESC, t.id"),
    "most_played": ("ut.play_count > 0", "ut.play_count DESC, ut.last_played DESC, t.id"),
    "not_played": ("COALESCE(ut.play_count, 0) = 0",
                   "ar.sort_name COLLATE NOCASE, al.title COLLATE NOCASE, COALESCE(t.disc_no, 1), t.track_no, t.id"),
}


def smart_list(conn: sqlite3.Connection, uid: str, kind: str, offset: int = 0, limit: int = 10_000) -> list[dict]:
    where, order = _LISTS[kind]
    rows = conn.execute(TRACK_SELECT + f" WHERE t.missing = 0 AND {where}{fam(conn, uid)} ORDER BY {order} LIMIT ? OFFSET ?",
                        (uid, limit, offset))
    return [track_dict(r) for r in rows]


def favourites(conn: sqlite3.Connection, uid: str) -> list[dict]:
    return smart_list(conn, uid, "favourites")


# ---------- folders + genres ----------


def folders(conn: sqlite3.Connection, uid: str, path: str) -> dict | None:
    path = path.strip("/")
    ff = fam(conn, uid)
    counts = conn.execute(f"SELECT t.folder, COUNT(*) AS n FROM tracks t WHERE t.missing = 0{ff} GROUP BY t.folder").fetchall()
    prefix = f"{path}/" if path else ""
    children: dict[str, int] = {}
    exists = path == ""
    for r in counts:
        f = r["folder"]
        if f == path:
            exists = True
            continue
        if f.startswith(prefix):
            exists = True
            name = f[len(prefix):].split("/", 1)[0]
            children[name] = children.get(name, 0) + r["n"]
    if not exists:
        return None
    rows = conn.execute(TRACK_SELECT + f" WHERE t.folder = ? AND t.missing = 0{ff} ORDER BY t.path", (uid, path))
    parent = None if path == "" else posixpath.dirname(path)
    return {
        "path": path, "parent": parent,
        "folders": [{"name": n, "path": f"{prefix}{n}", "trackCount": c}
                    for n, c in sorted(children.items(), key=lambda kv: kv[0].casefold())],
        "tracks": [track_dict(r) for r in rows],
    }


def genres(conn: sqlite3.Connection, uid: str | None = None) -> list[dict]:
    rows = conn.execute(
        "SELECT MIN(t.genre) AS name, COUNT(*) AS n, COUNT(DISTINCT t.album_id) AS albums FROM tracks t "
        f"WHERE t.missing = 0 AND t.genre IS NOT NULL AND t.genre != ''{fam(conn, uid)} GROUP BY t.genre COLLATE NOCASE "
        "ORDER BY t.genre COLLATE NOCASE")
    return [{"name": r["name"], "trackCount": r["n"], "albumCount": r["albums"]} for r in rows]


def genre_tracks(conn: sqlite3.Connection, uid: str, genre: str, offset: int, limit: int) -> list[dict]:
    rows = conn.execute(
        TRACK_SELECT + f" WHERE t.missing = 0 AND t.genre = ? COLLATE NOCASE{fam(conn, uid)} {ALBUM_ORDER} LIMIT ? OFFSET ?",
        (uid, genre, limit, offset))
    return [track_dict(r) for r in rows]


# ---------- search + stats ----------


def _like(q: str) -> str:
    esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def _fold(s: str) -> str:
    """Lower-case and strip accents, so "Beyonce" finds "Beyoncé"."""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).casefold()


_WORD = re.compile(r"\w+", re.UNICODE)
# Per database file: (library stamp, {folded word: {spellings as tagged}}).
_vocab_cache: dict[str, tuple[tuple, dict[str, set[str]]]] = {}


def _vocab(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """Every word in artist names, album titles and track titles, rebuilt only when the library changes."""
    db = next((r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main"), "") or f"mem:{id(conn)}"
    stamp = tuple(conn.execute(
        "SELECT (SELECT COUNT(*) FROM tracks WHERE missing = 0), (SELECT COUNT(*) FROM artists), "
        "(SELECT COUNT(*) FROM albums), (SELECT COALESCE(SUM(mtime), 0) FROM tracks)").fetchone())
    stamp = (*stamp, int(time.monotonic() // 300))  # and at least every five minutes (tag edits keep counts)
    hit = _vocab_cache.get(db)
    if hit and hit[0] == stamp:
        return hit[1]
    vocab: dict[str, set[str]] = {}
    for sql in ("SELECT name FROM artists", "SELECT title FROM albums", "SELECT title FROM tracks WHERE missing = 0"):
        for (text,) in conn.execute(sql):
            for w in _WORD.findall(text or ""):
                if len(w) >= 2:
                    vocab.setdefault(_fold(w), set()).add(w)
    _vocab_cache[db] = (stamp, vocab)
    return vocab


def _fuzzy_words(vocab: dict[str, set[str]], word: str) -> list[str]:
    """Library spellings a (possibly misspelt or accent-less) query word could mean."""
    w = _fold(word)
    out: set[str] = set()
    if len(w) >= 2:
        for k, forms in vocab.items():
            if w in k:
                out |= forms
                if len(out) >= 12:
                    break
    if len(w) >= 3:
        # Typos: difflib narrows to similar words quickly, then allow one slip (a wrong, missing, extra or
        # swapped letter) per word, two in words of eight letters or more.
        edits = 1 if len(w) < 8 else 2
        keys = [k for k in vocab if abs(len(k) - len(w)) <= edits]
        for k in difflib.get_close_matches(w, keys, n=12, cutoff=0.6):
            if _edit_distance(w, k) <= edits:
                out |= vocab[k]
    return sorted(out)[:16]


def _edit_distance(a: str, b: str) -> int:
    """Optimal string alignment distance (Levenshtein plus adjacent swaps)."""
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = a[i - 1] != b[j - 1]
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[-1]


def fuzzy_terms(conn: sqlite3.Connection, q: str) -> list[list[str]] | None:
    """For each query word, the library words it probably means; None when some word matches nothing."""
    vocab = _vocab(conn)
    terms = []
    for w in [w for w in q.split() if w][:8]:
        alts = _fuzzy_words(vocab, w)
        if not alts:
            return None
        terms.append(alts)
    return terms or None


def search(conn: sqlite3.Connection, uid: str, q: str, limit: int, data_dir: Path) -> dict:
    """Case-insensitive substring search on each word (all words must match). When that finds nothing at all,
    retry typo- and accent-tolerantly: each word may instead match a close spelling from the library's own
    vocabulary ("beatels" finds "Beatles", "bjork" finds "Björk")."""
    words = [w for w in q.split() if w][:8]
    if not words:
        return {"artists": [], "albums": [], "tracks": []}
    res = _search_terms(conn, uid, [[w] for w in words], limit, data_dir)
    if not any(res.values()):
        terms = fuzzy_terms(conn, q)
        if terms:
            res = _search_terms(conn, uid, terms, limit, data_dir)
    return res


def _search_terms(conn: sqlite3.Connection, uid: str, terms: list[list[str]], limit: int, data_dir: Path) -> dict:
    """Every term must match one of the columns; a term matches when any of its alternatives is a substring."""

    def cond(cols: list[str]) -> tuple[str, list[str]]:
        parts, params = [], []
        for alts in terms:
            parts.append("(" + " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for a in alts for c in cols) + ")")
            for a in alts:
                params.extend([_like(a)] * len(cols))
        return " AND ".join(parts), params

    f = fam(conn, uid)
    c, p = cond(["ar.name"])
    ar_rows = conn.execute(
        artist_select(f) + f" AND {c} GROUP BY ar.id ORDER BY ar.sort_name COLLATE NOCASE LIMIT ?", (*p, limit)
    )
    c, p = cond(["al.title", "ar.name"])
    al_rows = conn.execute(album_select(f) + f" WHERE {c} ORDER BY al.title COLLATE NOCASE LIMIT ?", (*p, limit))
    c, p = cond(["t.title", "ar.name", "al.title"])
    tr_rows = conn.execute(
        TRACK_SELECT + f" WHERE t.missing = 0{f} AND {c} ORDER BY t.title COLLATE NOCASE LIMIT ?", (uid, *p, limit)
    )
    return {
        "artists": [artist_dict(r) for r in ar_rows],
        "albums": [album_dict(r, data_dir) for r in al_rows],
        "tracks": [track_dict(r) for r in tr_rows],
    }


def stats(conn: sqlite3.Connection, uid: str | None = None) -> dict:
    r = conn.execute(
        "SELECT COUNT(*) AS tracks, COALESCE(SUM(duration_s), 0) AS dur, COALESCE(SUM(flac_bytes), 0) AS bytes "
        "FROM tracks WHERE missing = 0"
    ).fetchone()
    missing = conn.execute("SELECT COUNT(*) FROM tracks WHERE missing = 1").fetchone()[0]
    albums_n = conn.execute("SELECT COUNT(DISTINCT album_id) FROM tracks WHERE missing = 0").fetchone()[0]
    artists_n = conn.execute(
        "SELECT COUNT(DISTINCT al.artist_id) FROM albums al WHERE EXISTS "
        "(SELECT 1 FROM tracks t WHERE t.album_id = al.id AND t.missing = 0)"
    ).fetchone()[0]
    starred = blocked = 0
    if uid:
        u = conn.execute("SELECT SUM(starred_at IS NOT NULL), SUM(blocked) FROM user_tracks WHERE user_id = ?",
                         (uid,)).fetchone()
        starred, blocked = u[0] or 0, u[1] or 0
    users_n = conn.execute("SELECT COUNT(*) FROM users WHERE disabled = 0").fetchone()[0]
    return {
        "tracks": r["tracks"], "albums": albums_n, "artists": artists_n, "missing": missing,
        "hours": round(r["dur"] / 3600, 1), "gb": round(r["bytes"] / 1e9, 1),
        "starred": starred, "blocked": blocked, "users": users_n,
    }
