"""Automatic tag fixing: Chromaprint fingerprint -> AcoustID -> MusicBrainz release -> per-track suggestions
that an admin reviews. Nothing applies by itself: accepting writes tag overrides through
homebeat.library.tagedit (audited, revertible); the read-only music files are never touched.

The `autotag` job runs three resumable phases, each picking up what is left in the database:
1. fingerprint tracks without a current fingerprint (fpcalc at nice 19, one at a time);
2. look fingerprints up on AcoustID (needs `acoustid_api_key`; ~2.5 req/s);
3. build suggestions album by album: pick the release that best explains the album's tracks, fetch it from
   MusicBrainz (1 req/s, cached in mb_releases), score each track and the album group.
A time budget stops a run cleanly; the next run carries on."""
from __future__ import annotations

import difflib
import json
import logging
import re
import sqlite3
import time
import unicodedata
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx

from homebeat.config import Config
from homebeat.core import now_iso
from homebeat.library import acoustid, art, chromaprint, musicbrainz, overrides, tagedit

log = logging.getLogger(__name__)

TIME_BUDGET_S = 50 * 60
CAA = "https://coverartarchive.org"
MAX_COVER_BYTES = 15 * 1024 * 1024
# Applied on accept unless the admin picks fields. Artist MBIDs change artist ids ('mb:<mbid>'), which splits an
# artist's page until all of their albums carry them, so they are opt-in; genre only fills an empty genre.
DEFAULT_FIELDS = ("title", "artist", "album_artist", "album", "track_no", "disc_no", "year", "mb_track",
                  "mb_releasegroup")
SUGGEST_FIELDS = ("title", "artist", "album_artist", "album", "track_no", "disc_no", "year", "genre",
                  "mb_track", "mb_releasegroup", "mb_artist", "mb_albumartist")


class Stop(Exception):
    """Stop the current phase (budget, network trouble); the next run carries on."""


# ---------- text helpers ----------

_PAREN = re.compile(r"\s*[\(\[][^)\]]*(feat|ft\.|with|remaster|version|edit|mix|mono|stereo|deluxe|edition)"
                    r"[^)\]]*[\)\]]", re.I)
_DASH_SUFFIX = re.compile(r"\s+-\s+.*\b(remaster(ed)?|version|edit|mix|live|mono|stereo)\b.*$", re.I)
_GENERIC_TITLE = re.compile(r"^(track|piste|titel|audio|untitled)?\s*\d*$", re.I)


def norm(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = _PAREN.sub("", s)
    s = _DASH_SUFFIX.sub("", s)
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split())


def sim(a: str | None, b: str | None) -> float:
    """0..1 similarity of two names after normalisation (case, accents, feat./remaster suffixes)."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def credit(ac: list | None) -> str | None:
    """MusicBrainz/AcoustID artist credit -> display string."""
    if not ac:
        return None
    return "".join(f"{c.get('name') or (c.get('artist') or {}).get('name', '')}{c.get('joinphrase') or ''}"
                   for c in ac) or None


def _credit_id(ac: list | None) -> str | None:
    """The artist MBID, only for single-artist credits (a collaboration has no single artist id)."""
    if not ac or len(ac) != 1:
        return None
    a = ac[0]
    return (a.get("artist") or {}).get("id") or a.get("id")


def _year(date: str | None) -> int | None:
    if date and len(date) >= 4 and date[:4].isdigit():
        return int(date[:4])
    return None


# ---------- scoring ----------

def duration_factor(file_s: float | None, rec_s: float | None) -> float:
    """1.0 within 3 s, falling linearly to 0.5 at 18 s off (unknown duration: 0.9)."""
    if not file_s or not rec_s:
        return 0.9
    d = abs(float(file_s) - float(rec_s))
    if d <= 3:
        return 1.0
    return max(0.5, 1.0 - (d - 3) / 30.0)


def agreement(cur_title: str | None, cur_artist: str | None, title: str | None, artist: str | None,
              path: str = "") -> float | None:
    """How much the current tags agree with the suggestion (None = current tags say nothing useful)."""
    parts = []
    stem = Path(path).stem if path else ""
    if cur_title and not _GENERIC_TITLE.match(cur_title.strip()) and cur_title != stem:
        parts.append(sim(cur_title, title))
    elif cur_title and stem and cur_title == stem:  # title is the file name: compare loosely
        parts.append(max(sim(cur_title, title), 1.0 if norm(title) and norm(title) in norm(stem) else 0.0))
    if cur_artist and norm(cur_artist) not in ("unknown artist", "various artists", ""):
        parts.append(sim(cur_artist, artist))
    return sum(parts) / len(parts) if parts else None


def track_confidence(acoustid_score: float, dur_factor: float, agree: float | None, on_release: bool) -> float:
    """0..1. The AcoustID match score, lowered by a duration mismatch, by disagreement with the current tags
    (only mildly: badly tagged files are why we are here) and when no release could be chosen."""
    f_agree = 0.9 if agree is None else 0.8 + 0.2 * agree
    f_release = 1.0 if on_release else 0.8
    return round(max(0.0, min(1.0, acoustid_score * dur_factor * f_agree * f_release)), 4)


def group_confidence(track_confs: list[float], coverage: float) -> float:
    """Album group: mean track confidence, lowered when the release explains only part of the album."""
    if not track_confs:
        return 0.0
    return round(sum(track_confs) / len(track_confs) * (0.7 + 0.3 * max(0.0, min(1.0, coverage))), 4)


_TYPE_BONUS = {"album": 0.3, "ep": 0.2, "single": 0.1}
_BAD_SECONDARY = {"compilation", "live", "remix", "dj-mix", "mixtape/street", "soundtrack", "demo"}


def release_score(rel: dict, covered: list[float], n_tracks: int, album_tracks: int, cur_album: str | None,
                  cur_album_artist: str | None, single_title: str | None = None) -> float:
    """Rank a candidate release for a set of `n_tracks` tracks. `covered` = AcoustID scores of the tracks it
    contains. Coverage dominates; the current album title, a matching track count and an original studio
    album (not a compilation) break ties."""
    rg = rel.get("releaseGroup") or {}
    s = 3.0 * sum(covered) / max(1, n_tracks)
    if cur_album:
        s += 1.5 * sim(cur_album, rel.get("title"))
    elif single_title and (rg.get("type") or "").lower() == "single":
        s += 0.5 * sim(single_title, rel.get("title"))
    if cur_album_artist:
        s += 0.5 * sim(cur_album_artist, credit(rel.get("artists")))
    s += _TYPE_BONUS.get((rg.get("type") or "").lower(), 0.0)
    if {t.lower() for t in rg.get("secondaryTypes") or []} & _BAD_SECONDARY:
        s -= 0.4
    if rel.get("trackCount") and rel.get("trackCount") == album_tracks:
        s += 0.3
    if rel.get("year"):
        s -= (int(rel["year"]) - 1900) * 1e-5   # earliest release wins a tie
    return s


# ---------- phase 1: fingerprints ----------

def _budget(deadline: float, clock: Callable[[], float]) -> None:
    if clock() >= deadline:
        raise Stop("time budget used")


def fingerprint_pending(conn: sqlite3.Connection, cfg: Config, deadline: float, *, fpcalc: str | None = None,
                        compute: Callable | None = None, clock: Callable[[], float] = time.monotonic) -> dict:
    compute = compute or chromaprint.compute
    rows = conn.execute(
        "SELECT t.id, t.path, t.mtime FROM tracks t LEFT JOIN fingerprints f ON f.track_id = t.id "
        "WHERE t.missing = 0 AND (f.track_id IS NULL OR f.mtime != t.mtime) ORDER BY t.album_id, t.path").fetchall()
    done = failed = 0
    for r in rows:
        _budget(deadline, clock)
        try:
            fp = compute(cfg.library_root / r["path"], fpcalc=fpcalc)
            vals = (fp.duration_s, fp.fingerprint, chromaprint.to_blob(fp.raw), None)
            done += 1
        except chromaprint.FingerprintError as e:
            if "not found" in str(e):
                raise Stop(str(e)) from e
            log.warning("fingerprint failed for %s: %s", r["path"], e)
            vals = (None, None, None, str(e)[:500])
            failed += 1
        conn.execute(
            "INSERT INTO fingerprints (track_id, mtime, duration_s, fingerprint, raw, error, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(track_id) DO UPDATE SET mtime = excluded.mtime, "
            "duration_s = excluded.duration_s, fingerprint = excluded.fingerprint, raw = excluded.raw, "
            "error = excluded.error, computed_at = excluded.computed_at",
            (r["id"], r["mtime"], *vals, now_iso()))
        # The file changed: an old lookup/pending suggestion no longer applies.
        conn.execute("DELETE FROM autotag_suggestions WHERE track_id = ? AND status = 'pending'", (r["id"],))
    return {"fingerprinted": done, "failed": failed, "remaining": 0}


# ---------- phase 2: AcoustID ----------

def lookup_pending(conn: sqlite3.Connection, cfg: Config, deadline: float, *,
                   transport: httpx.BaseTransport | None = None, limiter: acoustid.RateLimiter | None = None,
                   clock: Callable[[], float] = time.monotonic) -> dict:
    if not cfg.acoustid_api_key:
        return {"skipped": "no acoustid_api_key in config.toml"}
    rows = conn.execute(
        "SELECT f.track_id, f.mtime, f.fingerprint, f.duration_s FROM fingerprints f "
        "JOIN tracks t ON t.id = f.track_id LEFT JOIN acoustid_lookups l ON l.track_id = f.track_id "
        "WHERE t.missing = 0 AND f.fingerprint IS NOT NULL AND f.mtime = t.mtime AND "
        "(l.track_id IS NULL OR l.mtime != f.mtime OR (l.status = 'error' AND l.looked_up_at < ?)) "
        "ORDER BY t.album_id, t.path", (_days_ago(1),)).fetchall()
    ok = no_match = errors = 0
    transient_in_a_row = 0
    for r in rows:
        _budget(deadline, clock)
        try:
            res = acoustid.lookup(cfg.acoustid_api_key, r["fingerprint"], r["duration_s"] or 0,
                                  transport=transport, limiter=limiter)
            transient_in_a_row = 0
        except acoustid.AcoustIDError as e:
            if e.fatal:
                raise Stop(str(e)) from e
            if e.transient:
                transient_in_a_row += 1
                if transient_in_a_row >= 3:
                    raise Stop(str(e)) from e
                continue
            errors += 1
            _save_lookup(conn, r, "error", None, None, None, str(e)[:500])
            continue
        if res and res[0]["recordings"]:
            ok += 1
            _save_lookup(conn, r, "ok", res[0]["acoustid"], res[0]["score"], res, None)
        else:
            no_match += 1
            _save_lookup(conn, r, "no_match", res[0]["acoustid"] if res else None,
                         res[0]["score"] if res else None, res, None)
    return {"matched": ok, "noMatch": no_match, "errors": errors}


def _days_ago(days: float) -> str:
    from datetime import UTC, datetime, timedelta

    from homebeat.core import iso

    return iso(datetime.now(UTC) - timedelta(days=days))


def _save_lookup(conn, r, status, aid, score, results, error) -> None:
    conn.execute(
        "INSERT INTO acoustid_lookups (track_id, mtime, status, acoustid, score, results, error, looked_up_at, "
        "suggested_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL) ON CONFLICT(track_id) DO UPDATE SET "
        "mtime = excluded.mtime, status = excluded.status, acoustid = excluded.acoustid, score = excluded.score, "
        "results = excluded.results, error = excluded.error, looked_up_at = excluded.looked_up_at, "
        "suggested_at = NULL",
        (r["track_id"], r["mtime"], status, aid, score,
         json.dumps(results, separators=(",", ":")) if results is not None else None, error, now_iso()))


# ---------- phase 3: suggestions ----------

def mb_release(conn: sqlite3.Connection, mbid: str, transport: httpx.BaseTransport | None = None,
               min_interval_s: float = musicbrainz.MIN_INTERVAL_S) -> dict:
    """MusicBrainz release with tracks, credits, genres and release group; cached in mb_releases."""
    row = conn.execute("SELECT data FROM mb_releases WHERE mbid = ?", (mbid,)).fetchone()
    if row:
        return json.loads(row[0])
    data = musicbrainz._get(f"release/{mbid}", {"inc": "recordings+artist-credits+genres+release-groups"},
                            transport, min_interval_s)
    conn.execute("INSERT OR REPLACE INTO mb_releases (mbid, data, fetched_at) VALUES (?, ?, ?)",
                 (mbid, json.dumps(data, separators=(",", ":")), now_iso()))
    return data


def _current(conn: sqlite3.Connection, where: str, params: tuple) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT t.id, t.path, t.album_id, t.title, t.track_no, t.disc_no, t.year, t.genre, t.album_artist, "
        "t.duration_s, t.mbid, ar.name AS artist, ar.mbid AS artist_mbid, al.title AS album, "
        "al.mbid AS album_mbid, al.art_path, aa.name AS album_artist_name, aa.mbid AS album_artist_mbid "
        "FROM tracks t JOIN artists ar ON ar.id = t.artist_id JOIN albums al ON al.id = t.album_id "
        "JOIN artists aa ON aa.id = al.artist_id WHERE " + where, params).fetchall()


def current_values(r: sqlite3.Row) -> dict:
    """Effective tags of a track, in DB field names (what a suggestion is compared with)."""
    return {"title": r["title"], "artist": r["artist"], "album_artist": r["album_artist"], "album": r["album"],
            "track_no": r["track_no"], "disc_no": r["disc_no"], "year": r["year"], "genre": r["genre"],
            "mb_track": r["mbid"], "mb_releasegroup": r["album_mbid"], "mb_artist": r["artist_mbid"],
            "mb_albumartist": r["album_artist_mbid"]}


def _release_track(mb: dict, rec_ids: set[str], hint: dict | None) -> tuple[dict, dict] | None:
    """(medium, track) of the release whose recording is one of `rec_ids`, else by AcoustID's position hint."""
    for m in mb.get("media") or []:
        for t in m.get("tracks") or []:
            if (t.get("recording") or {}).get("id") in rec_ids:
                return m, t
    if hint and hint.get("medium") and hint.get("track"):
        for m in mb.get("media") or []:
            if m.get("position") == hint["medium"].get("position"):
                for t in m.get("tracks") or []:
                    if t.get("position") == hint["track"].get("position"):
                        return m, t
    return None


def _top_genre(mb: dict) -> str | None:
    gs = [g for g in (mb.get("genres") or (mb.get("release-group") or {}).get("genres") or [])
          if (g.get("count") or 0) > 0 and g.get("name")]
    if not gs:
        return None
    g = max(gs, key=lambda g: g.get("count") or 0)["name"]
    return " ".join(w if w.isupper() else w[:1].upper() + w[1:] for w in g.replace("-", "‐").split()).replace("‐", "-")


def suggestion_from_release(mb: dict, medium: dict, track: dict) -> dict:
    rec = track.get("recording") or {}
    rg = mb.get("release-group") or {}
    t_credit = track.get("artist-credit") or rec.get("artist-credit") or mb.get("artist-credit")
    num = track.get("number")
    return {
        "title": track.get("title") or rec.get("title"),
        "artist": credit(t_credit),
        "album_artist": credit(mb.get("artist-credit")),
        "album": mb.get("title"),
        "track_no": int(num) if isinstance(num, str) and num.isdigit() else track.get("position"),
        "disc_no": medium.get("position"),
        "year": _year(rg.get("first-release-date")) or _year(mb.get("date")),
        "genre": _top_genre(mb),
        "mb_track": rec.get("id"),
        "mb_releasegroup": rg.get("id"),
        "mb_artist": _credit_id(t_credit),
        "mb_albumartist": _credit_id(mb.get("artist-credit")),
    }


def _cover_url(mb: dict) -> str | None:
    caa = mb.get("cover-art-archive") or {}
    return f"{CAA}/release/{mb['id']}/front-500" if caa.get("front") and mb.get("id") else None


def build_album(conn: sqlite3.Connection, album_id: str, *, transport: httpx.BaseTransport | None = None,
                min_interval_s: float = musicbrainz.MIN_INTERVAL_S) -> int:
    """(Re)build pending suggestions for one album. Decided (accepted/rejected) tracks are left alone.
    Returns the number of suggestions written. Raises Stop on MusicBrainz trouble (nothing written)."""
    rows = _current(conn, "t.album_id = ? AND t.missing = 0", (album_id,))
    if not rows:
        return 0
    decided = {r[0] for r in conn.execute(
        "SELECT s.track_id FROM autotag_suggestions s JOIN tracks t ON t.id = s.track_id "
        "WHERE t.album_id = ? AND s.status != 'pending'", (album_id,))}
    looks = {r["track_id"]: json.loads(r["results"]) for r in conn.execute(
        "SELECT l.track_id, l.results FROM acoustid_lookups l JOIN tracks t ON t.id = l.track_id "
        "WHERE t.album_id = ? AND l.status = 'ok'", (album_id,))}
    cand = {r["id"]: looks[r["id"]] for r in rows if r["id"] in looks and r["id"] not in decided}
    by_id = {r["id"]: r for r in rows}
    album_tracks = len(rows)
    cur_album = rows[0]["album"] if rows[0]["album"] and norm(rows[0]["album"]) != "unknown album" else None
    cur_aa = rows[0]["album_artist_name"]

    # Per track: recording id -> (acoustid score, recording); release id -> (release, score of that track).
    recs: dict[str, dict[str, tuple[float, dict]]] = {}
    rels: dict[str, dict[str, tuple[dict, float, str]]] = {}
    for tid, results in cand.items():
        recs[tid], rels[tid] = {}, {}
        for res in results:
            for rec in res["recordings"]:
                if rec["id"] not in recs[tid] or recs[tid][rec["id"]][0] < res["score"]:
                    recs[tid][rec["id"]] = (res["score"], rec)
                for rel in rec["releases"]:
                    if rel.get("id") and (rel["id"] not in rels[tid] or rels[tid][rel["id"]][1] < res["score"]):
                        rels[tid][rel["id"]] = (rel, res["score"], rec["id"])

    now = now_iso()
    written = 0
    plan: list[tuple[str | None, list[str]]] = []   # (release id, track ids)
    left = [t for t in cand if recs.get(t)]
    while left:
        scores: dict[str, float] = {}
        info: dict[str, dict] = {}
        for tid in left:
            for rid, (rel, _s, _rec) in rels[tid].items():
                info.setdefault(rid, rel)
        for rid, rel in info.items():
            covered = [rels[t][rid][1] for t in left if rid in rels[t]]
            single = by_id[left[0]]["title"] if len(left) == 1 else None
            scores[rid] = release_score(rel, covered, len(left), album_tracks, cur_album, cur_aa, single)
        if not scores:
            plan.append((None, left))
            break
        best = max(scores, key=lambda k: (scores[k], k))
        on = [t for t in left if best in rels[t]]
        plan.append((best, on))
        left = [t for t in left if t not in on]

    out: list[tuple] = []
    for rid, tids in plan:
        mb = None
        if rid:
            try:
                mb = mb_release(conn, rid, transport, min_interval_s)
            except musicbrainz.LookupError_ as e:
                if "error 404" not in str(e):
                    raise Stop(str(e)) from e
                mb = None   # release merged/deleted on MusicBrainz: fall back to recording-only suggestions
        group_id = f"{album_id}~{rid or 'none'}"
        confs, rows_out = [], []
        for tid in tids:
            r = by_id[tid]
            best_rec = max(recs[tid].values(), key=lambda x: x[0])
            on_release = False
            if mb is not None:
                hint_rel = rels[tid][rid][0]
                hit = _release_track(mb, set(recs[tid]), hint_rel)
            else:
                hit = None
            if hit:
                medium, track = hit
                rec_id = (track.get("recording") or {}).get("id")
                a_score = recs[tid].get(rec_id, (rels[tid][rid][1], None))[0]
                rec_len = (track.get("length") or (track.get("recording") or {}).get("length") or 0) / 1000 or None
                sug = suggestion_from_release(mb, medium, track)
                on_release = True
            else:  # recording only: title/artist/recording id, no album fields
                a_score, rec = best_rec
                rec_len = rec.get("durationS")
                sug = {"title": rec.get("title"), "artist": credit(rec.get("artists")),
                       "mb_track": rec.get("id"), "mb_artist": _credit_id(rec.get("artists"))}
            sug = {k: v for k, v in sug.items() if v is not None and k in SUGGEST_FIELDS}
            df = duration_factor(r["duration_s"], rec_len)
            agree = agreement(r["title"], r["artist"], sug.get("title"), sug.get("artist"), r["path"])
            conf = track_confidence(a_score, df, agree, on_release)
            confs.append(conf)
            detail = {"acoustidScore": a_score, "durationDiffS": round(abs((r["duration_s"] or 0) - rec_len), 1)
                      if rec_len and r["duration_s"] else None, "agreement": None if agree is None else round(agree, 3),
                      "onRelease": on_release}
            if mb is not None:
                detail.update(releaseTitle=mb.get("title"), releaseArtist=credit(mb.get("artist-credit")),
                              releaseYear=_year(mb.get("date")), country=mb.get("country"),
                              trackCount=sum(len(m.get("tracks") or []) for m in mb.get("media") or []),
                              format=", ".join(sorted({m.get("format") for m in mb.get("media") or []
                                                       if m.get("format")})) or None,
                              releaseGroupType=(mb.get("release-group") or {}).get("primary-type"))
            rows_out.append((tid, rid if on_release else None, sug.get("mb_track"), conf, sug,
                             _cover_url(mb) if (mb is not None and on_release) else None, detail))
        gconf = group_confidence(confs, len(tids) / max(1, album_tracks))
        for tid, rel_id, rec_id, conf, sug, cover, detail in rows_out:
            out.append((tid, group_id, rel_id, rec_id, conf, gconf, json.dumps(sug, ensure_ascii=False), cover,
                        json.dumps(detail), now))
    conn.execute("BEGIN IMMEDIATE")
    try:
        ph = ",".join("?" * len(cand)) if cand else "''"
        conn.execute(f"DELETE FROM autotag_suggestions WHERE status = 'pending' AND track_id IN ({ph})", list(cand))
        for o in out:
            conn.execute(
                "INSERT INTO autotag_suggestions (track_id, group_id, release_mbid, recording_mbid, confidence, "
                "group_confidence, suggested, cover_url, detail, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?) ON CONFLICT(track_id) DO NOTHING", o)
            written += 1
        conn.execute("UPDATE acoustid_lookups SET suggested_at = ? WHERE track_id IN "
                     "(SELECT id FROM tracks WHERE album_id = ?)", (now, album_id))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return written


def suggest_pending(conn: sqlite3.Connection, deadline: float, *, transport: httpx.BaseTransport | None = None,
                    min_interval_s: float = musicbrainz.MIN_INTERVAL_S,
                    clock: Callable[[], float] = time.monotonic) -> dict:
    albums = [r[0] for r in conn.execute(
        "SELECT DISTINCT t.album_id FROM acoustid_lookups l JOIN tracks t ON t.id = l.track_id "
        "WHERE t.missing = 0 AND l.status = 'ok' AND l.suggested_at IS NULL ORDER BY t.album_id")]
    n_albums = n = 0
    for al in albums:
        _budget(deadline, clock)
        n += build_album(conn, al, transport=transport, min_interval_s=min_interval_s)
        n_albums += 1
    return {"albums": n_albums, "suggestions": n}


def run(conn: sqlite3.Connection, cfg: Config, *, time_budget_s: float = TIME_BUDGET_S,
        transport: httpx.BaseTransport | None = None, fpcalc: str | None = None, compute: Callable | None = None,
        limiter: acoustid.RateLimiter | None = None, mb_interval_s: float = musicbrainz.MIN_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic) -> dict:
    """The `autotag` job. Each phase stops cleanly on the time budget or network trouble."""
    deadline = clock() + time_budget_s
    out: dict = {}
    phases = (
        ("fingerprints", lambda: fingerprint_pending(conn, cfg, deadline, fpcalc=fpcalc, compute=compute, clock=clock)),
        ("acoustid", lambda: lookup_pending(conn, cfg, deadline, transport=transport, limiter=limiter, clock=clock)),
        ("suggestions", lambda: suggest_pending(conn, deadline, transport=transport, min_interval_s=mb_interval_s,
                                                clock=clock)),
    )
    for name, fn in phases:
        try:
            out[name] = fn()
        except Stop as e:
            out[name] = {"stopped": str(e)}
            if str(e) == "time budget used":
                break
    out["status"] = status(conn, cfg)
    return out


# ---------- review ----------

def status(conn: sqlite3.Connection, cfg: Config) -> dict:
    one = lambda q, *p: conn.execute(q, p).fetchone()[0]  # noqa: E731
    by = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM autotag_suggestions s JOIN tracks t "
                                           "ON t.id = s.track_id WHERE t.missing = 0 GROUP BY status")}
    lk = {r[0]: r[1] for r in conn.execute("SELECT l.status, COUNT(*) FROM acoustid_lookups l JOIN tracks t "
                                           "ON t.id = l.track_id WHERE t.missing = 0 GROUP BY l.status")}
    return {
        "apiKeyConfigured": bool(cfg.acoustid_api_key),
        "fpcalcAvailable": chromaprint.fpcalc_path() is not None,
        "tracks": one("SELECT COUNT(*) FROM tracks WHERE missing = 0"),
        "fingerprinted": one("SELECT COUNT(*) FROM fingerprints f JOIN tracks t ON t.id = f.track_id "
                             "WHERE t.missing = 0 AND f.fingerprint IS NOT NULL AND f.mtime = t.mtime"),
        "fingerprintFailed": one("SELECT COUNT(*) FROM fingerprints f JOIN tracks t ON t.id = f.track_id "
                                 "WHERE t.missing = 0 AND f.fingerprint IS NULL"),
        "matched": lk.get("ok", 0), "noMatch": lk.get("no_match", 0), "lookupErrors": lk.get("error", 0),
        "pending": by.get("pending", 0), "accepted": by.get("accepted", 0), "rejected": by.get("rejected", 0),
    }


def _api(d: dict) -> dict:
    return {overrides.API_NAMES[k]: v for k, v in d.items() if k in overrides.API_NAMES}


def changed_fields(cur: dict, sug: dict) -> list[str]:
    return [f for f in SUGGEST_FIELDS if f in sug and sug[f] != cur.get(f)]


def default_fields(cur: dict, sug: dict) -> list[str]:
    """Fields an accept applies when the admin doesn't choose: the core tags + recording/release-group ids;
    genre only where the track has none."""
    fs = [f for f in changed_fields(cur, sug) if f in DEFAULT_FIELDS]
    if "genre" in sug and not cur.get("genre"):
        fs.append("genre")
    return fs


def groups(conn: sqlite3.Connection, cfg: Config, status_: str = "pending", min_confidence: float = 0.0,
           q: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    """Suggestions grouped by album group, best first: {total, groups: [...]}."""
    params: list = [status_, min_confidence]
    where = "s.status = ? AND s.group_confidence >= ? AND t.missing = 0"
    if q:
        where += " AND (al.title LIKE ? OR ar.name LIKE ? OR t.title LIKE ? OR s.suggested LIKE ?)"
        params += [f"%{q}%"] * 4
    base = (f"FROM autotag_suggestions s JOIN tracks t ON t.id = s.track_id JOIN albums al ON al.id = t.album_id "
            f"JOIN artists ar ON ar.id = al.artist_id WHERE {where}")
    total = conn.execute(f"SELECT COUNT(DISTINCT s.group_id) {base}", params).fetchone()[0]
    gids = [r[0] for r in conn.execute(
        f"SELECT s.group_id, MAX(s.group_confidence) AS gc {base} GROUP BY s.group_id ORDER BY gc DESC, s.group_id "
        f"LIMIT ? OFFSET ?", (*params, limit, offset))]
    return {"total": total, "groups": [_group(conn, cfg, g, status_) for g in gids]}


def _group(conn: sqlite3.Connection, cfg: Config, gid: str, status_: str) -> dict:
    srows = conn.execute("SELECT * FROM autotag_suggestions WHERE group_id = ? AND status = ?",
                         (gid, status_)).fetchall()
    ids = [s["track_id"] for s in srows]
    cur = {r["id"]: r for r in _current(conn, f"t.id IN ({','.join('?' * len(ids))}) AND t.missing = 0", tuple(ids))}
    tracks = []
    first = None
    for s in srows:
        r = cur.get(s["track_id"])
        if r is None:
            continue
        first = first or r
        sug = json.loads(s["suggested"])
        cv = current_values(r)
        tracks.append({
            "trackId": s["track_id"], "path": r["path"], "status": s["status"], "confidence": s["confidence"],
            "current": _api(cv), "suggested": _api(sug), "changes": [overrides.API_NAMES[f] for f in changed_fields(cv, sug)],
            "defaultFields": [overrides.API_NAMES[f] for f in default_fields(cv, sug)],
            "recordingMbid": s["recording_mbid"], "releaseMbid": s["release_mbid"],
            "durationS": r["duration_s"], "detail": json.loads(s["detail"] or "{}"),
            "decidedAt": s["decided_at"], "batchId": s["batch_id"],
            "_order": (sug.get("disc_no") or r["disc_no"] or 0, sug.get("track_no") or r["track_no"] or 0, r["path"]),
        })
    tracks.sort(key=lambda t: t.pop("_order"))
    s0 = srows[0] if srows else None
    detail = json.loads(s0["detail"] or "{}") if s0 else {}
    album_id = gid.split("~", 1)[0]
    return {
        "groupId": gid, "albumId": album_id,
        "currentAlbum": first["album"] if first else None,
        "currentAlbumArtist": first["album_artist_name"] if first else None,
        "albumHasArt": bool(first and art.has_art(cfg.data_dir, first["art_path"], first["album_id"])),
        "confidence": s0["group_confidence"] if s0 else 0.0,
        "release": None if not (s0 and s0["release_mbid"]) else {
            "mbid": s0["release_mbid"], "title": detail.get("releaseTitle"), "artist": detail.get("releaseArtist"),
            "year": detail.get("releaseYear"), "country": detail.get("country"), "format": detail.get("format"),
            "trackCount": detail.get("trackCount"), "type": detail.get("releaseGroupType"),
            "hasCover": bool(s0["cover_url"])},
        "albumTrackCount": conn.execute("SELECT COUNT(*) FROM tracks WHERE album_id = ? AND missing = 0",
                                        (album_id,)).fetchone()[0],
        "tracks": tracks,
    }


def _select(conn: sqlite3.Connection, status_: str, track_ids: list[str] | None, group_ids: list[str] | None,
            min_confidence: float | None) -> list[sqlite3.Row]:
    where, params = ["s.status = ?", "t.missing = 0"], [status_]
    ors = []
    if track_ids:
        ors.append(f"s.track_id IN ({','.join('?' * len(track_ids))})")
        params += track_ids
    if group_ids:
        ors.append(f"s.group_id IN ({','.join('?' * len(group_ids))})")
        params += group_ids
    if ors:
        where.append("(" + " OR ".join(ors) + ")")
    if min_confidence is not None:
        where.append("s.confidence >= ?")
        params.append(min_confidence)
    if not ors and min_confidence is None:
        return []
    return conn.execute(f"SELECT s.* FROM autotag_suggestions s JOIN tracks t ON t.id = s.track_id "
                        f"WHERE {' AND '.join(where)}", params).fetchall()


def fetch_cover(cfg: Config, release_mbid: str, transport: httpx.BaseTransport | None = None) -> bytes | None:
    """Front cover of a release from the Cover Art Archive (cached under data_dir/art/caa/). None = no cover."""
    if not re.fullmatch(r"[0-9a-f-]{36}", release_mbid):
        return None
    d = cfg.data_dir / "art" / "caa"
    f, none = d / f"{release_mbid}.jpg", d / f"{release_mbid}.none"
    if f.exists():
        return f.read_bytes()
    if none.exists() and none.stat().st_mtime > time.time() - 7 * 86400:
        return None
    try:
        with httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0), transport=transport, follow_redirects=True,
                          headers={"User-Agent": musicbrainz.USER_AGENT}) as c:
            r = c.get(f"{CAA}/release/{release_mbid}/front-500")
    except httpx.HTTPError as e:
        raise CoverError(f"Cover Art Archive unreachable: {e}") from e
    d.mkdir(parents=True, exist_ok=True)
    if r.status_code == 404:
        none.touch()
        return None
    if r.status_code >= 400 or not r.content or len(r.content) > MAX_COVER_BYTES:
        raise CoverError(f"Cover Art Archive error {r.status_code}")
    tmp = f.with_suffix(".part")
    tmp.write_bytes(r.content)
    tmp.replace(f)
    return r.content


class CoverError(Exception):
    pass


def accept(cfg: Config, conn: sqlite3.Connection, user_id: str, *, track_ids: list[str] | None = None,
           group_ids: list[str] | None = None, min_confidence: float | None = None,
           edits: dict[str, dict] | None = None, fields: list[str] | None = None, cover: bool | None = None,
           transport: httpx.BaseTransport | None = None) -> dict:
    """Accept pending suggestions: write their values as tag overrides (one audit batch) and mark them accepted.
    `edits` = {track_id: {db_field: value}} replaces suggested values (and those fields are applied).
    `fields` = DB fields to apply (default: per track, `default_fields`). `cover`: also set the Cover Art Archive
    front cover as the album cover (None = only for albums that have no art)."""
    sel = _select(conn, "pending", track_ids, group_ids, min_confidence)
    if not sel:
        return {"accepted": 0, "batchId": None, "changed": 0, "coverErrors": []}
    edits = edits or {}
    rows = {r["id"]: r for r in _current(conn, f"t.id IN ({','.join('?' * len(sel))})",
                                         tuple(s["track_id"] for s in sel))}
    changes: dict[str, dict] = {}
    for s in sel:
        r = rows[s["track_id"]]
        cur = current_values(r)
        sug = {**json.loads(s["suggested"]), **{k: overrides.clean_value(k, v)
                                                for k, v in edits.get(s["track_id"], {}).items()}}
        want = list(fields) if fields is not None else default_fields(cur, sug)
        want += [f for f in edits.get(s["track_id"], {}) if f not in want]
        changes[s["track_id"]] = {f: sug[f] for f in want if f in sug and sug[f] != cur.get(f)}
    cover_errors: list[str] = []
    by_release: dict[str, list[str]] = {}
    for s in sel:
        if s["cover_url"] and s["release_mbid"]:
            r = rows[s["track_id"]]
            if cover or (cover is None and not art.has_art(cfg.data_dir, r["art_path"], r["album_id"])):
                by_release.setdefault(s["release_mbid"], []).append(s["track_id"])
    for rid, tids in by_release.items():
        try:
            data = fetch_cover(cfg, rid, transport)
            if data:
                sha = art.store_upload(cfg.data_dir, data)
                for t in tids:
                    changes[t]["cover"] = sha
        except (CoverError, ValueError) as e:
            cover_errors.append(f"{rid}: {e}")
    batch = uuid.uuid4().hex
    now = now_iso()
    conn.execute("BEGIN IMMEDIATE")
    try:
        todo = {t: c for t, c in changes.items() if c}
        res = tagedit.edit_each(cfg, conn, user_id, todo, batch=batch, in_transaction=True) if todo else {"changed": 0}
        for s in sel:
            conn.execute("UPDATE autotag_suggestions SET status = 'accepted', decided_at = ?, decided_by = ?, "
                         "batch_id = ? WHERE track_id = ?", (now, user_id, batch, s["track_id"]))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"accepted": len(sel), "batchId": batch, "changed": res["changed"], "coverErrors": cover_errors}


def reject(conn: sqlite3.Connection, user_id: str, *, track_ids: list[str] | None = None,
           group_ids: list[str] | None = None) -> dict:
    sel = _select(conn, "pending", track_ids, group_ids, None)
    now = now_iso()
    for s in sel:
        conn.execute("UPDATE autotag_suggestions SET status = 'rejected', decided_at = ?, decided_by = ? "
                     "WHERE track_id = ?", (now, user_id, s["track_id"]))
    return {"rejected": len(sel)}


def reopen(cfg: Config, conn: sqlite3.Connection, user_id: str, track_ids: list[str]) -> dict:
    """Undo a decision: a rejected suggestion goes back to pending; an accepted one has the overrides its accept
    wrote reverted (only fields still holding the accepted value) and goes back to pending."""
    rows = conn.execute(f"SELECT * FROM autotag_suggestions WHERE status != 'pending' AND track_id IN "
                        f"({','.join('?' * len(track_ids))})", track_ids).fetchall() if track_ids else []
    reverted = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for s in rows:
            if s["status"] == "accepted" and s["batch_id"]:
                ov = overrides.load(conn, s["track_id"])
                fields = [e["field"] for e in conn.execute(
                    "SELECT field, new_value FROM tag_edits WHERE batch_id = ? AND track_id = ? AND action = 'set'",
                    (s["batch_id"], s["track_id"]))
                    if e["field"] in ov and json.dumps(ov[e["field"]], ensure_ascii=False) == e["new_value"]]
                if fields:
                    reverted += tagedit.edit_each(cfg, conn, user_id, {s["track_id"]: {}}, revert=fields,
                                                  in_transaction=True)["changed"]
            conn.execute("UPDATE autotag_suggestions SET status = 'pending', decided_at = NULL, decided_by = NULL, "
                         "batch_id = NULL WHERE track_id = ?", (s["track_id"],))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"reopened": len(rows), "reverted": reverted}


def rematch(conn: sqlite3.Connection, track_ids: list[str]) -> dict:
    """Forget pending suggestions and lookups of these tracks; the next run looks them up again."""
    if not track_ids:
        return {"queued": 0}
    ph = ",".join("?" * len(track_ids))
    conn.execute(f"DELETE FROM autotag_suggestions WHERE status = 'pending' AND track_id IN ({ph})", track_ids)
    n = conn.execute(f"DELETE FROM acoustid_lookups WHERE track_id IN ({ph})", track_ids).rowcount
    return {"queued": n}
