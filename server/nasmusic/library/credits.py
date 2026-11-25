"""Song credits: who produced, wrote and played on each track, from MusicBrainz plus the file's own
composer/lyricist tags. Cached per track in `credit_lookups` / `track_credits` (schema v12).

Source (free, no key): the MusicBrainz recording (https://musicbrainz.org/doc/MusicBrainz_API) with its artist
relationships (producer, engineer, instrument, vocal, ...), the relationships of the works it records
(composer, lyricist, writer, ...) and those of one release it is on (album-wide producer, mastering, ...).
The recording is the track's MBID tag when it has one, else a strict search (same title and artist, length
within a few seconds, score >= 95, unambiguous). At most 1 request/second through the shared limiter in
nasmusic.library.musicbrainz, with the project's generic User-Agent.

People are keyed `mb:<artist MBID>`; names that only come from tags are keyed `n:<normalised name>`, and a tag
name that matches a MusicBrainz person's name is shown as that person.

Refresh policy: found -> 180 days, not found -> 60 days, error -> 1 day. Lookups for a page that is opened run
in a background thread; the nightly `credits` job works through the rest of the library slowly.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta

import httpx

from nasmusic.library import musicbrainz, queries

log = logging.getLogger(__name__)

REFRESH_DAYS = {"ok": 180, "not_found": 60, "error": 1}
LENGTH_TOLERANCE_S = 5.0
PER_RUN = 300

# Credit groups shown on the page, in order.
GROUPS = [("producers", "Producers"), ("writers", "Songwriters"), ("musicians", "Musicians"),
          ("engineers", "Engineering"), ("other", "Also credited")]

# MusicBrainz artist relationship type -> (group, label). Types not listed (tributes, dedications, ...) are skipped.
ROLES: dict[str, tuple[str, str]] = {
    "producer": ("producers", "Producer"),
    "composer": ("writers", "Composer"),
    "lyricist": ("writers", "Lyricist"),
    "writer": ("writers", "Songwriter"),
    "librettist": ("writers", "Librettist"),
    "arranger": ("other", "Arranger"),
    "orchestrator": ("other", "Orchestrator"),
    "instrument arranger": ("other", "Arranger"),
    "vocal arranger": ("other", "Vocal arranger"),
    "instrument": ("musicians", "Instruments"),
    "vocal": ("musicians", "Vocals"),
    "performer": ("musicians", "Performer"),
    "performing orchestra": ("musicians", "Orchestra"),
    "conductor": ("musicians", "Conductor"),
    "chorus master": ("musicians", "Chorus master"),
    "concertmaster": ("musicians", "Concertmaster"),
    "programming": ("musicians", "Programming"),
    "engineer": ("engineers", "Engineer"),
    "audio": ("engineers", "Audio engineer"),
    "sound": ("engineers", "Sound engineer"),
    "recording": ("engineers", "Recording engineer"),
    "mix": ("engineers", "Mixing"),
    "mastering": ("engineers", "Mastering"),
    "editor": ("engineers", "Editor"),
    "balance": ("engineers", "Balance engineer"),
    "remixer": ("other", "Remixer"),
    "mix-DJ": ("other", "DJ mix"),
}
TAG_ROLES = {"composer": "composer", "lyricist": "lyricist"}
_SPLIT = re.compile(r"\s*(?:;|/|\||,|&| and | with | feat\. | ft\. )\s*", re.I)


class FetchError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def norm(s: str | None) -> str:
    return re.sub(r"[^\w]+", " ", (s or "").casefold()).strip()


def tag_names(value: str | None) -> list[str]:
    """'A; B / C & D' -> ['A', 'B', 'C', 'D'] (composer and lyricist tags often hold several people)."""
    out: list[str] = []
    for part in _SPLIT.split(value or ""):
        p = part.strip()
        if p and norm(p) and p not in out:
            out.append(p)
    return out


def person_id_for_name(name: str) -> str:
    return "n:" + norm(name)


# ---------- MusicBrainz ----------


class Http:
    def __init__(self, transport: httpx.BaseTransport | None = None, mb_interval_s: float = musicbrainz.MIN_INTERVAL_S):
        self.transport = transport
        self.mb_interval_s = mb_interval_s

    def mb(self, path: str, params: dict) -> dict:
        try:
            return musicbrainz._get(path, params, self.transport, self.mb_interval_s)
        except musicbrainz.LookupError_ as e:
            raise FetchError(str(e)) from e


def _q(s: str) -> str:
    return musicbrainz._q(s)


def find_recording(http: Http, title: str, artist: str, duration_s: float | None) -> str | None:
    """A strict search: exact normalised title and artist credit, length within LENGTH_TOLERANCE_S, score >= 95,
    and only one distinct match (several recordings of the same song by the same artist -> don't guess)."""
    if not norm(title) or not norm(artist):
        return None
    data = http.mb("recording", {"query": f"recording:{_q(title)} AND artist:{_q(artist)}", "limit": 10})
    hits: list[str] = []
    for rec in data.get("recordings") or []:
        if int(rec.get("score") or 0) < 95 or norm(rec.get("title")) != norm(title):
            continue
        credit = musicbrainz._credit(rec.get("artist-credit")) or ""
        names = {norm(credit)} | {norm((c.get("artist") or {}).get("name")) for c in rec.get("artist-credit") or []}
        if norm(artist) not in names:
            continue
        length = rec.get("length")
        if duration_s and isinstance(length, (int, float)) and abs(length / 1000 - duration_s) > LENGTH_TOLERANCE_S:
            continue
        if rec.get("id") and rec["id"] not in hits:
            hits.append(rec["id"])
    return hits[0] if len(hits) == 1 else None


def _rels(rels: list | None, scope: str) -> list[dict]:
    out = []
    for rel in rels or []:
        if rel.get("target-type") not in (None, "artist") or not rel.get("artist"):
            continue
        typ = rel.get("type") or ""
        if typ not in ROLES or (rel.get("direction") not in (None, "backward")):
            continue
        a = rel["artist"]
        if not a.get("id"):
            continue
        attrs = [x for x in rel.get("attributes") or [] if x not in ("additional", "guest", "solo")]
        out.append({"mbid": a["id"], "name": (rel.get("target-credit") or a.get("name") or "").strip() or a.get("name"),
                    "role": typ, "detail": ", ".join(attrs), "scope": scope})
    return out


def pick_release(rec: dict, album_title: str | None, release_group: str | None) -> str | None:
    """The release of the track's album: same release group, else same title, else none (we don't guess an
    album-wide credit from a compilation the song also appears on)."""
    releases = rec.get("releases") or []
    if release_group:
        for r in releases:
            if (r.get("release-group") or {}).get("id") == release_group:
                return r.get("id")
    if album_title:
        same = [r for r in releases if norm(r.get("title")) == norm(album_title)]
        same.sort(key=lambda r: (r.get("status") != "Official", r.get("date") or "9999"))
        if same:
            return same[0].get("id")
    return None


def fetch(http: Http, track: sqlite3.Row, *, release_cache: dict[str, list[dict]] | None = None
          ) -> tuple[str, str | None, str | None, list[dict]]:
    """('ok' | 'not_found', recording MBID, release MBID, credits). Raises FetchError when MusicBrainz fails."""
    mbid = track["mbid"] or find_recording(http, track["title"], track["artist"], track["duration_s"])
    if not mbid:
        return "not_found", None, None, []
    try:
        rec = http.mb(f"recording/{mbid}", {"inc": "artist-rels+work-rels+work-level-rels+releases+release-groups"})
    except FetchError as e:
        if "404" in str(e):
            return "not_found", None, None, []
        raise
    credits = _rels(rec.get("relations"), "recording")
    for rel in rec.get("relations") or []:
        if rel.get("target-type") == "work" or rel.get("work"):
            credits += _rels((rel.get("work") or {}).get("relations"), "work")
    release = pick_release(rec, track["album"], track["album_mbid"])
    if release:
        cache = release_cache if release_cache is not None else {}
        if release not in cache:
            rel = http.mb(f"release/{release}", {"inc": "artist-rels"})
            cache[release] = _rels(rel.get("relations"), "release")
        credits += cache[release]
    seen = set()
    out = []
    for c in credits:
        key = (c["mbid"], c["role"], c["detail"], c["scope"])
        if key not in seen:
            seen.add(key)
            out.append(c)
    return "ok", rec.get("id") or mbid, release, out


# ---------- cache ----------

TRACK_ROW = ("SELECT t.id, t.title, t.mbid, t.duration_s, ar.name AS artist, al.title AS album, al.mbid AS album_mbid "
             "FROM tracks t JOIN artists ar ON ar.id = t.artist_id JOIN albums al ON al.id = t.album_id ")


def lookup_row(conn: sqlite3.Connection, track_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM credit_lookups WHERE track_id = ?", (track_id,)).fetchone()


def is_due(row: sqlite3.Row | None, now: datetime | None = None) -> bool:
    return row is None or row["refresh_after"] <= _iso(now or _now())


def _release_from_db(conn: sqlite3.Connection, release: str) -> list[dict] | None:
    """Album-wide credits another track of the same release already fetched."""
    other = conn.execute("SELECT track_id FROM credit_lookups WHERE release_mbid = ? AND status = 'ok' LIMIT 1",
                         (release,)).fetchone()
    if other is None:
        return None
    return [{"mbid": r["person_id"][3:], "name": r["name"], "role": r["role"], "detail": r["detail"],
             "scope": "release"} for r in conn.execute(
        "SELECT * FROM track_credits WHERE track_id = ? AND scope = 'release' ORDER BY position", (other[0],))]


class _DbReleaseCache(dict):
    def __init__(self, conn: sqlite3.Connection):
        super().__init__()
        self.conn = conn

    def __contains__(self, release: object) -> bool:
        if dict.__contains__(self, release):
            return True
        found = _release_from_db(self.conn, str(release))
        if found is not None:
            self[str(release)] = found
            return True
        return False


def update(conn: sqlite3.Connection, http: Http, track_id: str, release_cache: dict | None = None) -> sqlite3.Row | None:
    """Look the track's credits up now and store them (also on failure, with a short retry time)."""
    t = conn.execute(TRACK_ROW + "WHERE t.id = ?", (track_id,)).fetchone()
    if t is None:
        return None
    now = _now()
    prev = lookup_row(conn, track_id)
    cache = release_cache if release_cache is not None else _DbReleaseCache(conn)
    try:
        status, rec, release, credits = fetch(http, t, release_cache=cache)
        error = None
    except FetchError as e:
        status, rec, release, credits, error = "error", None, None, [], str(e)
        log.info("credits for %s failed: %s", track_id, e)
    after = _iso(now + timedelta(days=REFRESH_DAYS[status]))
    conn.execute("BEGIN IMMEDIATE")
    try:
        if status == "error" and prev is not None and prev["status"] == "ok":
            conn.execute("UPDATE credit_lookups SET refresh_after = ?, error = ? WHERE track_id = ?",
                         (after, error, track_id))
        else:
            conn.execute(
                "INSERT INTO credit_lookups (track_id, status, recording_mbid, release_mbid, error, fetched_at, "
                "refresh_after) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(track_id) DO UPDATE SET "
                "status = excluded.status, recording_mbid = excluded.recording_mbid, "
                "release_mbid = excluded.release_mbid, error = excluded.error, fetched_at = excluded.fetched_at, "
                "refresh_after = excluded.refresh_after",
                (track_id, status, rec, release, error, _iso(now), after))
            conn.execute("DELETE FROM track_credits WHERE track_id = ?", (track_id,))
            conn.executemany(
                "INSERT OR IGNORE INTO track_credits (track_id, person_id, name, name_norm, role, detail, scope, "
                "position) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(track_id, "mb:" + c["mbid"], c["name"], norm(c["name"]), c["role"], c["detail"], c["scope"], i)
                 for i, c in enumerate(credits)])
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return lookup_row(conn, track_id)


def refresh_due(conn: sqlite3.Connection, http: Http, limit: int = PER_RUN) -> dict:
    """Nightly: look up to `limit` due tracks, most-played first, album by album (album-wide credits are fetched
    once per release). Stops after 3 failures in a row (MusicBrainz down or rate limiting)."""
    rows = conn.execute(
        "SELECT t.id FROM tracks t LEFT JOIN credit_lookups cl ON cl.track_id = t.id "
        "WHERE t.missing = 0 AND (cl.track_id IS NULL OR cl.refresh_after <= ?) "
        "ORDER BY (SELECT COALESCE(SUM(ut.play_count), 0) FROM user_tracks ut JOIN tracks t2 ON t2.id = ut.track_id "
        "          WHERE t2.album_id = t.album_id) DESC, t.album_id, COALESCE(t.disc_no, 1), t.track_no, t.id "
        "LIMIT ?", (_iso(_now()), limit)).fetchall()
    ok = failed = in_a_row = 0
    cache = _DbReleaseCache(conn)
    for r in rows:
        row = update(conn, http, r[0], cache)
        if row is not None and row["status"] == "error":
            failed += 1
            in_a_row += 1
            if in_a_row >= 3:
                break
        else:
            ok += 1
            in_a_row = 0
    return {"looked_up": ok, "failed": failed}


# ---------- background fetching (pages never wait on the network) ----------


class Fetcher:
    def __init__(self, open_conn, transport: httpx.BaseTransport | None = None):
        self.open_conn = open_conn
        self.http = Http(transport)
        self._queue: list[str] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def request(self, track_id: str) -> None:
        with self._lock:
            if track_id not in self._queue:
                self._queue.append(track_id)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="credits", daemon=True)
                self._thread.start()

    def pending(self, track_id: str) -> bool:
        with self._lock:
            return track_id in self._queue

    def _run(self) -> None:
        conn = self.open_conn()
        try:
            while True:
                with self._lock:
                    if not self._queue:
                        self._thread = None
                        return
                    tid = self._queue[0]
                try:
                    update(conn, self.http, tid)
                except Exception:  # noqa: BLE001
                    log.exception("credits lookup failed for %s", tid)
                finally:
                    with self._lock:
                        if self._queue and self._queue[0] == tid:
                            self._queue.pop(0)
        finally:
            conn.close()

    def wait(self, timeout: float = 10.0) -> None:
        """Tests: wait until the queue is empty."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                if not self._queue:
                    return
            time.sleep(0.02)


# ---------- API shapes ----------


def labels_for(role: str, detail: str) -> list[str]:
    """'instrument' + 'electric guitar, bass' -> ['Electric guitar', 'Bass']; 'producer' + 'co' -> ['Co-producer']."""
    if role in TAG_ROLES:
        return ["Composer" if role == "composer" else "Lyricist"]
    label = ROLES.get(role, ("other", role.title()))[1]
    parts = [d for d in (detail or "").split(", ") if d]
    if role in ("instrument", "vocal", "performer") and parts:
        return [p[:1].upper() + p[1:] for p in parts]
    if role == "producer" and parts:
        prefix = {"co": "Co-", "executive": "Executive ", "assistant": "Assistant ", "associate": "Associate "}
        return ["".join(prefix.get(p, p.title() + " ") for p in parts) + "producer"]
    if parts:
        return [f"{label} ({', '.join(parts)})"]
    return [label]


class TagIndex:
    """Composer/lyricist tag names of the tracks a user can see: normalised name -> {track_id: [roles]}."""

    def __init__(self, conn: sqlite3.Connection, uid: str):
        self.names: dict[str, dict[str, list[str]]] = {}
        self.display: dict[str, str] = {}
        f = queries.fam(conn, uid)
        for r in conn.execute(f"SELECT t.id, t.composer, t.lyricist FROM tracks t WHERE t.missing = 0 "
                              f"AND (t.composer IS NOT NULL OR t.lyricist IS NOT NULL){f}"):
            for col in ("composer", "lyricist"):
                for name in tag_names(r[col]):
                    n = norm(name)
                    self.display.setdefault(n, name)
                    roles = self.names.setdefault(n, {}).setdefault(r["id"], [])
                    if col not in roles:
                        roles.append(col)

    def tracks(self, name_norm: str) -> dict[str, list[str]]:
        return self.names.get(name_norm, {})


def _visible_ids(conn: sqlite3.Connection, uid: str, ids: set[str]) -> set[str]:
    if not ids:
        return set()
    f = queries.fam(conn, uid)
    out: set[str] = set()
    lst = list(ids)
    for i in range(0, len(lst), 500):
        chunk = lst[i:i + 500]
        out |= {r[0] for r in conn.execute(
            f"SELECT t.id FROM tracks t WHERE t.missing = 0 AND t.id IN ({','.join('?' * len(chunk))}){f}", chunk)}
    return out


def _person_tracks(conn: sqlite3.Connection, tags: TagIndex, person_id: str, names: set[str]
                   ) -> dict[str, list[tuple[str, str, str]]]:
    """track id -> [(role, detail, scope)] for everything credited to this person (not yet visibility-filtered)."""
    out: dict[str, list[tuple[str, str, str]]] = {}
    if person_id.startswith("mb:"):
        rows = conn.execute("SELECT track_id, role, detail, scope FROM track_credits WHERE person_id = ?", (person_id,))
    else:
        rows = conn.execute("SELECT track_id, role, detail, scope FROM track_credits WHERE name_norm = ?",
                            (person_id[2:],))
    for r in rows:
        out.setdefault(r[0], []).append((r[1], r[2], r[3]))
    for n in names:
        for tid, roles in tags.tracks(n).items():
            have = {x[0] for x in out.get(tid, [])}
            for role in roles:
                if role not in have and "writer" not in have:
                    out.setdefault(tid, []).append((role, "", "tags"))
    return out


def _artist_for(conn: sqlite3.Connection, mbid: str | None, name: str) -> str | None:
    """The library artist this person is (same MBID, else same name), for a link to their artist page."""
    if mbid:
        r = conn.execute("SELECT id FROM artists WHERE mbid = ? OR id = ? LIMIT 1", (mbid, "mb:" + mbid)).fetchone()
        if r:
            return r[0]
    n = norm(name)
    for r in conn.execute("SELECT id, name FROM artists WHERE name = ? COLLATE NOCASE", (name,)):
        if norm(r["name"]) == n:
            return r["id"]
    return None


def track_credits(conn: sqlite3.Connection, uid: str, track_id: str, *, pending: bool) -> dict | None:
    """TrackCredits for the API, or None when the user can't see the track."""
    t = conn.execute(f"SELECT t.id, t.composer, t.lyricist FROM tracks t WHERE t.id = ? AND t.missing = 0"
                     f"{queries.fam(conn, uid)}", (track_id,)).fetchone()
    if t is None:
        return None
    lk = lookup_row(conn, track_id)
    rows = conn.execute("SELECT * FROM track_credits WHERE track_id = ? ORDER BY position", (track_id,)).fetchall()
    # person -> group -> labels
    people: dict[str, dict] = {}
    for r in rows:
        group = ROLES.get(r["role"], ("other", ""))[0]
        p = people.setdefault(r["person_id"], {"personId": r["person_id"], "name": r["name"], "mbid": r["person_id"][3:],
                                               "groups": {}, "scopes": set()})
        labels = p["groups"].setdefault(group, [])
        for one in labels_for(r["role"], r["detail"]):
            if one not in labels:
                labels.append(one)
        p["scopes"].add((group, r["scope"]))
    mb_by_name = {norm(p["name"]): pid for pid, p in people.items()}
    for col in ("composer", "lyricist"):
        for name in tag_names(t[col]):
            n = norm(name)
            pid = mb_by_name.get(n)
            label = "Composer" if col == "composer" else "Lyricist"
            if pid is not None:
                labels = people[pid]["groups"].setdefault("writers", [])
                if label not in labels and "Songwriter" not in labels:
                    labels.append(label)
                people[pid]["scopes"].add(("writers", "tags"))
                continue
            pid = person_id_for_name(name)
            p = people.setdefault(pid, {"personId": pid, "name": name, "mbid": None, "groups": {}, "scopes": set()})
            labels = p["groups"].setdefault("writers", [])
            if label not in labels:
                labels.append(label)
            p["scopes"].add(("writers", "tags"))
    tags = TagIndex(conn, uid) if people else None
    counts: dict[str, int] = {}
    for pid, p in people.items():
        names = {norm(p["name"])}
        found = _person_tracks(conn, tags, pid, names)
        found.pop(track_id, None)
        counts[pid] = len(_visible_ids(conn, uid, set(found)))
    groups = []
    for gid, glabel in GROUPS:
        members = []
        for pid, p in people.items():
            if gid not in p["groups"]:
                continue
            scopes = {s for g, s in p["scopes"] if g == gid}
            members.append({
                "personId": pid, "name": p["name"], "mbid": p["mbid"], "roles": p["groups"][gid],
                "albumWide": scopes == {"release"}, "fromTags": scopes == {"tags"},
                "otherTracks": counts[pid], "artistId": _artist_for(conn, p["mbid"], p["name"]),
            })
        if members:
            members.sort(key=lambda m: (m["albumWide"], -m["otherTracks"], m["name"].casefold()))
            groups.append({"id": gid, "label": glabel, "people": members})
    if lk is None:
        status = "pending" if pending else "none"
    else:
        status = "pending" if pending and lk["status"] != "ok" else lk["status"]
    rec = lk["recording_mbid"] if lk is not None else None
    return {
        "trackId": track_id, "status": status, "fetchedAt": lk["fetched_at"] if lk is not None else None,
        "recordingMbid": rec,
        "musicbrainzUrl": f"https://musicbrainz.org/recording/{rec}" if rec else None,
        "groups": groups,
    }


def person(conn: sqlite3.Connection, uid: str, person_id: str) -> dict | None:
    """CreditPerson: everything in the user's library this person is credited on, or None when nothing is."""
    if not re.fullmatch(r"mb:[0-9a-fA-F-]{36}|n:[\w ]+", person_id or ""):
        return None
    tags = TagIndex(conn, uid)
    if person_id.startswith("mb:"):
        r = conn.execute("SELECT name FROM track_credits WHERE person_id = ? GROUP BY name ORDER BY COUNT(*) DESC "
                         "LIMIT 1", (person_id,)).fetchone()
        if r is None:
            return None
        name, mbid = r[0], person_id[3:]
        names = {norm(x[0]) for x in conn.execute("SELECT DISTINCT name FROM track_credits WHERE person_id = ?",
                                                    (person_id,))}
    else:
        n = person_id[2:]
        r = conn.execute("SELECT name FROM track_credits WHERE name_norm = ? LIMIT 1", (n,)).fetchone()
        name = tags.display.get(n) or (r[0] if r else None)
        if name is None:
            return None
        mbid, names = None, {n}
    found = _person_tracks(conn, tags, person_id, names)
    visible = _visible_ids(conn, uid, set(found))
    by_id = queries.tracks_by_ids(conn, uid, list(visible))
    tracks = []
    role_count: dict[str, int] = {}
    for tid, credits in found.items():
        tr = by_id.get(tid)
        if tr is None:
            continue
        labels: list[str] = []
        for role, detail, _scope in credits:
            for lab in labels_for(role, detail):
                if lab not in labels:
                    labels.append(lab)
        for lab in labels:
            role_count[lab] = role_count.get(lab, 0) + 1
        tracks.append({**tr, "credits": labels})
    if not tracks:
        return None
    tracks.sort(key=lambda x: (x["artist"].casefold(), x["album"].casefold(), x.get("discNo") or 1,
                               x.get("trackNo") or 0, x["title"].casefold()))
    return {
        "id": person_id, "name": name, "mbid": mbid,
        "musicbrainzUrl": f"https://musicbrainz.org/artist/{mbid}" if mbid else None,
        "artistId": _artist_for(conn, mbid, name),
        "roles": [k for k, _ in sorted(role_count.items(), key=lambda kv: (-kv[1], kv[0]))],
        "trackCount": len(tracks), "tracks": tracks,
    }
