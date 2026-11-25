"""Server-side scrobbling to ListenBrainz and Last.fm (v7).

Every client (phone, web, desktop, watch, the phone driving a cast speaker) already posts listening events to
POST /events, so the server scrobbles from those: one play counts once, whichever device played it.

Rules (both services use Last.fm's): the track is at least 30 s long, and it was played for at least half its
length or 4 minutes. A `play` event qualifies (clients send it at >= 80 %); a `skip` qualifies when its
positionS already meets the rule (e.g. skipping a 20-minute track after 6 minutes). The scrobble's timestamp
is when the playthrough started (event time minus position).

Delivery: qualifying events go to the `scrobble_queue` outbox (key = user, service, event id: idempotent),
and a flush sends due rows in batches of 50 with exponential backoff on network/server errors (1 min doubling
to 6 h, then given up after MAX_ATTEMPTS). Auth failures pause the account (`needs_reauth`) without losing
rows. Dedupe: a second event for the same track whose start is within the same playthrough is skipped.
"Now playing" is sent when the user's Connect state starts playing a new track (best effort, never queued).

Opt-in: nothing is scrobbled unless the user connected and enabled a service. A device can opt out with the
user setting `device.<deviceId>.scrobble` = false. Erasing all listening data clears the outbox.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from homebeat.core import iso, now_iso

log = logging.getLogger(__name__)

SERVICES = ("listenbrainz", "lastfm")
MIN_TRACK_S = 30.0
ENOUGH_S = 240.0
BATCH = 50
MAX_ATTEMPTS = 15
MAX_BACKOFF_S = 6 * 3600
LASTFM_MAX_AGE_S = 14 * 24 * 3600     # Last.fm drops scrobbles older than two weeks
LB_API = "https://api.listenbrainz.org"
LASTFM_API = "https://ws.audioscrobbler.com/2.0/"
LASTFM_AUTH = "https://www.last.fm/api/auth/"
CLIENT = "Homebeat"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
# Last.fm error codes: 9 invalid session, 4/14/15 token problems, 10/26 bad API key -> reconnect / admin.
# 11 offline, 16 temporary, 29 rate limit -> retry later.
LASTFM_AUTH_ERRORS = {4, 9, 10, 14, 15, 26}
LASTFM_RETRY_ERRORS = {8, 11, 16, 29}


class ScrobbleError(Exception):
    """kind: 'auth' (token rejected), 'retry' (try again later), 'bad' (this payload will never work)."""

    def __init__(self, kind: str, message: str, code: int | None = None):
        super().__init__(message)
        self.kind, self.code = kind, code


# ---------- rules ----------


def qualifies(event_type: str, position_s: float | None, track_duration_s: float | None) -> bool:
    dur = float(track_duration_s or 0)
    if dur < MIN_TRACK_S:
        return False
    if event_type == "play":
        return True
    if event_type == "skip" and position_s is not None:
        return float(position_s) >= min(dur / 2, ENOUGH_S)
    return False


def started_at(at_iso: str, event_type: str, position_s: float | None, track_duration_s: float | None) -> int:
    """Unix seconds the playthrough started: event time minus how far into the track it was."""
    at = datetime.fromisoformat(at_iso)
    pos = position_s
    if pos is None:
        pos = float(track_duration_s or 0) * (0.8 if event_type == "play" else 0)
    return int(at.timestamp() - max(0.0, min(float(pos), float(track_duration_s or pos))))


def backoff_s(attempts: int) -> int:
    return int(min(MAX_BACKOFF_S, 60 * 2 ** max(0, attempts - 1)))


# ---------- accounts ----------


def account(conn: sqlite3.Connection, uid: str, service: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM scrobble_accounts WHERE user_id = ? AND service = ?", (uid, service)).fetchone()


def _upsert(conn: sqlite3.Connection, uid: str, service: str, **cols) -> None:
    conn.execute("INSERT OR IGNORE INTO scrobble_accounts (user_id, service) VALUES (?, ?)", (uid, service))
    if cols:
        sets = ", ".join(f"{k} = ?" for k in cols)
        conn.execute(f"UPDATE scrobble_accounts SET {sets} WHERE user_id = ? AND service = ?",
                     (*cols.values(), uid, service))


def connect_account(conn: sqlite3.Connection, uid: str, service: str, token: str, username: str | None) -> None:
    _upsert(conn, uid, service, token=token, username=username, enabled=1, needs_reauth=0, pending_token=None,
            pending_at=None, connected_at=now_iso(), last_error=None, last_error_at=None)


def disconnect(conn: sqlite3.Connection, uid: str, service: str) -> None:
    conn.execute("DELETE FROM scrobble_queue WHERE user_id = ? AND service = ?", (uid, service))
    conn.execute("DELETE FROM scrobble_accounts WHERE user_id = ? AND service = ?", (uid, service))


def lastfm_available(cfg) -> bool:
    return bool(cfg.lastfm_api_key and cfg.lastfm_api_secret)


def status(conn: sqlite3.Connection, cfg, uid: str) -> dict:
    out = []
    for s in SERVICES:
        a = account(conn, uid, s)
        counts = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM scrobble_queue WHERE user_id = ? AND service = ? GROUP BY status",
            (uid, s))}
        connected = bool(a and a["token"])
        out.append({
            "service": s, "available": True if s == "listenbrainz" else lastfm_available(cfg),
            "connected": connected, "enabled": bool(a and a["enabled"] and connected),
            "username": a["username"] if a else None, "nowPlaying": bool(a["now_playing"]) if a else True,
            "needsReauth": bool(a and a["needs_reauth"]), "authPending": bool(a and a["pending_token"]),
            "connectedAt": a["connected_at"] if a else None, "lastScrobbleAt": a["last_scrobble_at"] if a else None,
            "lastError": a["last_error"] if a else None, "lastErrorAt": a["last_error_at"] if a else None,
            "pending": counts.get("pending", 0), "failed": counts.get("failed", 0), "sent": counts.get("sent", 0),
            "ignored": counts.get("ignored", 0),
        })
    return {"services": out}


# ---------- enqueue (from POST /events) ----------


def _device_opted_out(conn: sqlite3.Connection, uid: str, device_id: str | None) -> bool:
    if not device_id:
        return False
    r = conn.execute("SELECT value FROM user_settings WHERE user_id = ? AND key = ?",
                     (uid, f"device.{device_id}.scrobble")).fetchone()
    return bool(r) and r[0].strip() == "false"


def enqueue(conn: sqlite3.Connection, uid: str, event_ids: list[str], now: datetime | None = None) -> int:
    """Queue qualifying events (by id; already-queued ones are ignored) for each enabled service. Returns rows
    added. Call after the events are stored."""
    services = [r["service"] for r in conn.execute(
        "SELECT service FROM scrobble_accounts WHERE user_id = ? AND enabled = 1 AND token IS NOT NULL", (uid,))]
    if not services or not event_ids:
        return 0
    now = now or datetime.now(UTC)
    added = 0
    rows = conn.execute(
        f"SELECT e.id, e.type, e.at, e.position_s, e.device_id, e.track_id, t.duration_s FROM events e "
        f"JOIN tracks t ON t.id = e.track_id WHERE e.user_id = ? AND e.imported IS NULL "
        f"AND e.type IN ('play','skip') AND e.id IN ({','.join('?' * len(event_ids))}) ORDER BY e.at",
        (uid, *event_ids)).fetchall()
    for r in rows:
        if not qualifies(r["type"], r["position_s"], r["duration_s"]) or _device_opted_out(conn, uid, r["device_id"]):
            continue
        ts = started_at(r["at"], r["type"], r["position_s"], r["duration_s"])
        window = max(MIN_TRACK_S, float(r["duration_s"]) / 2)
        for s in services:
            dup = conn.execute(
                "SELECT 1 FROM scrobble_queue WHERE user_id = ? AND service = ? AND track_id = ? "
                "AND listened_at > ? AND listened_at < ? AND event_id != ?",
                (uid, s, r["track_id"], ts - window, ts + window, r["id"])).fetchone()
            if dup:
                continue
            too_old = s == "lastfm" and now.timestamp() - ts > LASTFM_MAX_AGE_S
            cur = conn.execute(
                "INSERT OR IGNORE INTO scrobble_queue (user_id, service, event_id, track_id, listened_at, status, "
                "next_attempt_at, last_error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (uid, s, r["id"], r["track_id"], ts, "ignored" if too_old else "pending", iso(now),
                 "older than 14 days (Last.fm ignores those)" if too_old else None, iso(now)))
            added += cur.rowcount if not too_old else 0
    return added


# ---------- track metadata ----------


def track_meta(conn: sqlite3.Connection, track_ids: list[str]) -> dict[str, dict]:
    if not track_ids:
        return {}
    rows = conn.execute(
        "SELECT t.id, t.title, t.duration_s, t.mbid, t.track_no, t.album_artist, ar.name AS artist, "
        "al.title AS album, al.mbid AS album_mbid FROM tracks t JOIN artists ar ON ar.id = t.artist_id "
        f"JOIN albums al ON al.id = t.album_id WHERE t.id IN ({','.join('?' * len(track_ids))})", track_ids)
    return {r["id"]: dict(r) for r in rows}


# ---------- ListenBrainz ----------


def _lb_listen(m: dict, listened_at: int | None, version: str) -> dict:
    info: dict[str, Any] = {"submission_client": CLIENT, "submission_client_version": version,
                            "media_player": CLIENT, "duration_ms": int(float(m["duration_s"] or 0) * 1000)}
    if m.get("mbid"):
        info["recording_mbid"] = m["mbid"]
    if m.get("track_no"):
        info["tracknumber"] = m["track_no"]
    meta = {"artist_name": m["artist"], "track_name": m["title"], "additional_info": info}
    if m.get("album"):
        meta["release_name"] = m["album"]
    out: dict[str, Any] = {"track_metadata": meta}
    if listened_at is not None:
        out["listened_at"] = listened_at
    return out


def _lb_check(r: httpx.Response) -> dict:
    if r.status_code == 401:
        raise ScrobbleError("auth", "ListenBrainz rejected the user token", 401)
    if r.status_code == 429 or r.status_code >= 500:
        raise ScrobbleError("retry", f"ListenBrainz HTTP {r.status_code}", r.status_code)
    if r.status_code >= 400:
        try:
            msg = r.json().get("error") or r.text
        except ValueError:
            msg = r.text
        raise ScrobbleError("bad", f"ListenBrainz HTTP {r.status_code}: {str(msg)[:200]}", r.status_code)
    try:
        return r.json()
    except ValueError:
        return {}


def lb_validate(client: httpx.Client, token: str) -> str:
    """The user name for a ListenBrainz user token; ScrobbleError('auth') when invalid."""
    try:
        r = client.get(f"{LB_API}/1/validate-token", headers={"Authorization": f"Token {token}"})
    except httpx.HTTPError as e:
        raise ScrobbleError("retry", f"ListenBrainz unreachable: {e}") from e
    data = _lb_check(r)
    if not data.get("valid"):
        raise ScrobbleError("auth", data.get("message") or "invalid ListenBrainz token")
    return str(data.get("user_name") or "")


def lb_submit(client: httpx.Client, token: str, listen_type: str, payload: list[dict]) -> None:
    try:
        r = client.post(f"{LB_API}/1/submit-listens", headers={"Authorization": f"Token {token}"},
                        json={"listen_type": listen_type, "payload": payload})
    except httpx.HTTPError as e:
        raise ScrobbleError("retry", f"ListenBrainz unreachable: {e}") from e
    _lb_check(r)


# ---------- Last.fm ----------


def lastfm_sign(params: dict[str, str], secret: str) -> str:
    raw = "".join(f"{k}{params[k]}" for k in sorted(params) if k not in ("format", "callback")) + secret
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def lastfm_call(client: httpx.Client, cfg, method: str, params: dict[str, Any], post: bool = True) -> dict:
    p = {k: str(v) for k, v in params.items() if v is not None}
    p.update(method=method, api_key=cfg.lastfm_api_key)
    p["api_sig"] = lastfm_sign(p, cfg.lastfm_api_secret)
    p["format"] = "json"
    try:
        r = client.post(LASTFM_API, data=p) if post else client.get(LASTFM_API, params=p)
    except httpx.HTTPError as e:
        raise ScrobbleError("retry", f"Last.fm unreachable: {e}") from e
    try:
        data = r.json()
    except ValueError:
        data = {}
    if isinstance(data, dict) and "error" in data:
        code = int(data.get("error") or 0)
        msg = f"Last.fm error {code}: {data.get('message', '')}"
        if code in LASTFM_AUTH_ERRORS:
            raise ScrobbleError("auth", msg, code)
        if code in LASTFM_RETRY_ERRORS:
            raise ScrobbleError("retry", msg, code)
        raise ScrobbleError("bad", msg, code)
    if r.status_code >= 500 or r.status_code == 429:
        raise ScrobbleError("retry", f"Last.fm HTTP {r.status_code}", r.status_code)
    if r.status_code >= 400:
        raise ScrobbleError("bad", f"Last.fm HTTP {r.status_code}", r.status_code)
    return data


def lastfm_start(client: httpx.Client, cfg) -> tuple[str, str]:
    """Desktop-style web auth: a request token and the last.fm page where the user approves it."""
    data = lastfm_call(client, cfg, "auth.getToken", {}, post=False)
    token = data.get("token")
    if not token:
        raise ScrobbleError("retry", "Last.fm returned no token")
    return token, f"{LASTFM_AUTH}?api_key={cfg.lastfm_api_key}&token={token}"


def lastfm_session(client: httpx.Client, cfg, token: str) -> tuple[str, str]:
    """(session key, user name) once the user approved `token`; ScrobbleError code 14 = not approved yet."""
    data = lastfm_call(client, cfg, "auth.getSession", {"token": token}, post=False)
    s = data.get("session") or {}
    if not s.get("key"):
        raise ScrobbleError("retry", "Last.fm returned no session")
    return s["key"], s.get("name") or ""


def _lastfm_track_params(m: dict, i: int | None = None) -> dict[str, Any]:
    sfx = f"[{i}]" if i is not None else ""
    p = {f"artist{sfx}": m["artist"], f"track{sfx}": m["title"], f"album{sfx}": m.get("album"),
         f"duration{sfx}": int(float(m["duration_s"] or 0)) or None, f"mbid{sfx}": m.get("mbid"),
         f"trackNumber{sfx}": m.get("track_no")}
    if m.get("album_artist") and m["album_artist"] != m["artist"]:
        p[f"albumArtist{sfx}"] = m["album_artist"]
    return p


def lastfm_scrobble(client: httpx.Client, cfg, sk: str, items: list[tuple[dict, int]]) -> list[str | None]:
    """Scrobble up to 50 (meta, timestamp). Returns, per item, None if accepted or the ignore reason."""
    params: dict[str, Any] = {"sk": sk}
    for i, (m, ts) in enumerate(items):
        params.update(_lastfm_track_params(m, i))
        params[f"timestamp[{i}]"] = ts
    data = lastfm_call(client, cfg, "track.scrobble", params)
    sc = (data.get("scrobbles") or {}).get("scrobble")
    if isinstance(sc, dict):
        sc = [sc]
    out: list[str | None] = []
    for i in range(len(items)):
        msg = None
        if isinstance(sc, list) and i < len(sc):
            ig = (sc[i] or {}).get("ignoredMessage") or {}
            if str(ig.get("code", "0")) not in ("0", ""):
                msg = f"ignored by Last.fm ({ig.get('code')}): {ig.get('#text', '')}".strip()
        out.append(msg)
    return out


# ---------- flush ----------


def _client(transport) -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, transport=transport,
                        headers={"User-Agent": f"{CLIENT}/1 (self-hosted music server)"})


def _set_error(conn, uid, service, msg, reauth=False) -> None:
    _upsert(conn, uid, service, last_error=msg[:500], last_error_at=now_iso(), **({"needs_reauth": 1} if reauth else {}))


def _retry_rows(conn, keys, uid, service, msg, now: datetime) -> None:
    for ev in keys:
        r = conn.execute("SELECT attempts FROM scrobble_queue WHERE user_id = ? AND service = ? AND event_id = ?",
                         (uid, service, ev)).fetchone()
        n = (r[0] if r else 0) + 1
        st = "failed" if n >= MAX_ATTEMPTS else "pending"
        conn.execute("UPDATE scrobble_queue SET attempts = ?, status = ?, last_error = ?, next_attempt_at = ? "
                     "WHERE user_id = ? AND service = ? AND event_id = ?",
                     (n, st, msg[:500], iso(now + timedelta(seconds=backoff_s(n))), uid, service, ev))


def _mark(conn, uid, service, ev, status, msg=None) -> None:
    conn.execute("UPDATE scrobble_queue SET status = ?, last_error = ?, attempts = attempts + 1, sent_at = ? "
                 "WHERE user_id = ? AND service = ? AND event_id = ?",
                 (status, msg, now_iso() if status == "sent" else None, uid, service, ev))


def _send_batch(client, cfg, acct, rows, meta, version) -> list[tuple[str, str, str | None]]:
    """Returns [(event_id, status, message)]. Raises ScrobbleError for batch-level failures."""
    service = acct["service"]
    if service == "listenbrainz":
        payload = [_lb_listen(meta[r["track_id"]], r["listened_at"], version) for r in rows]
        try:
            lb_submit(client, acct["token"], "import" if len(payload) > 1 else "single", payload)
            return [(r["event_id"], "sent", None) for r in rows]
        except ScrobbleError as e:
            if e.kind != "bad" or len(rows) == 1:
                raise
        out = []   # a 400 for the batch: find the bad listen(s) one by one
        for r, p in zip(rows, payload):
            try:
                lb_submit(client, acct["token"], "single", [p])
                out.append((r["event_id"], "sent", None))
            except ScrobbleError as e:
                if e.kind != "bad":
                    raise
                out.append((r["event_id"], "failed", str(e)))
        return out
    res = lastfm_scrobble(client, cfg, acct["token"], [(meta[r["track_id"]], r["listened_at"]) for r in rows])
    return [(r["event_id"], "ignored" if msg else "sent", msg) for r, msg in zip(rows, res)]


def flush(conn: sqlite3.Connection, cfg, transport=None, now: datetime | None = None, version: str = "0",
          uid: str | None = None) -> dict:
    """Send every due queued scrobble (optionally for one user). Returns counts."""
    now = now or datetime.now(UTC)
    counts = {"sent": 0, "ignored": 0, "failed": 0, "retry": 0, "paused": 0}
    accts = conn.execute(
        "SELECT * FROM scrobble_accounts WHERE enabled = 1 AND token IS NOT NULL AND needs_reauth = 0"
        + (" AND user_id = ?" if uid else ""), (uid,) if uid else ()).fetchall()
    if not accts:
        return counts
    with _client(transport) as client:
        for a in accts:
            if a["service"] == "lastfm" and not lastfm_available(cfg):
                continue
            while True:
                rows = conn.execute(
                    "SELECT * FROM scrobble_queue WHERE user_id = ? AND service = ? AND status = 'pending' "
                    "AND next_attempt_at <= ? ORDER BY listened_at LIMIT ?",
                    (a["user_id"], a["service"], iso(now), BATCH)).fetchall()
                if not rows:
                    break
                meta = track_meta(conn, list({r["track_id"] for r in rows}))
                rows = [r for r in rows if r["track_id"] in meta]
                try:
                    results = _send_batch(client, cfg, a, rows, meta, version)
                except ScrobbleError as e:
                    if e.kind == "auth":
                        _set_error(conn, a["user_id"], a["service"], str(e), reauth=True)
                        counts["paused"] += len(rows)
                    elif e.kind == "bad":
                        for r in rows:
                            _mark(conn, a["user_id"], a["service"], r["event_id"], "failed", str(e)[:500])
                        _set_error(conn, a["user_id"], a["service"], str(e))
                        counts["failed"] += len(rows)
                        continue
                    else:
                        _retry_rows(conn, [r["event_id"] for r in rows], a["user_id"], a["service"], str(e), now)
                        _set_error(conn, a["user_id"], a["service"], str(e))
                        counts["retry"] += len(rows)
                    break
                ok = False
                for ev, st, msg in results:
                    _mark(conn, a["user_id"], a["service"], ev, st, msg)
                    counts[st] += 1
                    ok = ok or st == "sent"
                if ok:
                    _upsert(conn, a["user_id"], a["service"], last_scrobble_at=iso(now), last_error=None,
                            last_error_at=None)
                if len(rows) < BATCH:
                    break
    return counts


def retry_failed(conn: sqlite3.Connection, uid: str) -> int:
    return conn.execute("UPDATE scrobble_queue SET status = 'pending', attempts = 0, next_attempt_at = ? "
                        "WHERE user_id = ? AND status = 'failed'", (now_iso(), uid)).rowcount


def now_playing(conn: sqlite3.Connection, cfg, uid: str, track_id: str, transport=None, version: str = "0",
                device_id: str | None = None) -> int:
    """Best-effort "now playing" to each enabled service with now_playing on. Returns how many accepted."""
    accts = conn.execute("SELECT * FROM scrobble_accounts WHERE user_id = ? AND enabled = 1 AND now_playing = 1 "
                         "AND token IS NOT NULL AND needs_reauth = 0", (uid,)).fetchall()
    if not accts or _device_opted_out(conn, uid, device_id):
        return 0
    m = track_meta(conn, [track_id]).get(track_id)
    if not m:
        return 0
    ok = 0
    with _client(transport) as client:
        for a in accts:
            try:
                if a["service"] == "listenbrainz":
                    lb_submit(client, a["token"], "playing_now", [_lb_listen(m, None, version)])
                elif lastfm_available(cfg):
                    lastfm_call(client, cfg, "track.updateNowPlaying", {"sk": a["token"], **_lastfm_track_params(m)})
                else:
                    continue
                ok += 1
            except ScrobbleError as e:
                if e.kind == "auth":
                    _set_error(conn, uid, a["service"], str(e), reauth=True)
                log.info("now playing to %s failed: %s", a["service"], e)
    return ok


# ---------- background runner ----------


class Scrobbler:
    """Per-process: flushes in a background thread after new events, and sends "now playing" when a user's
    Connect state starts a new track. `auto` False (tests) makes kick()/on_hub() do nothing."""

    def __init__(self, cfg, transport=None, version: str = "0"):
        self.cfg, self.transport, self.version = cfg, transport, version
        self.auto = True
        self._lock = threading.Lock()
        self._again = False
        self._playing: dict[str, tuple[str, float]] = {}   # uid -> (track id, monotonic time sent)

    def _open(self):
        from homebeat.core import open_db

        return open_db(self.cfg)

    def flush_now(self) -> dict | None:
        if not self._lock.acquire(blocking=False):
            self._again = True
            return None
        try:
            while True:
                self._again = False
                conn = self._open()
                try:
                    res = flush(conn, self.cfg, self.transport, version=self.version)
                finally:
                    conn.close()
                if not self._again:
                    return res
        except Exception:
            log.exception("scrobble flush failed")
            return None
        finally:
            self._lock.release()

    def kick(self) -> None:
        if self.auto:
            threading.Thread(target=self.flush_now, name="scrobble-flush", daemon=True).start()

    def on_hub(self, uid: str, kind: str, payload) -> None:
        """Connect listener (runs on the event loop: must not block)."""
        if not self.auto or kind != "state" or not isinstance(payload, dict):
            return
        tid = payload.get("trackId")
        if not payload.get("playing") or not tid:
            return
        last = self._playing.get(uid)
        if last and last[0] == tid and time.monotonic() - last[1] < 600:
            return
        self._playing[uid] = (tid, time.monotonic())
        threading.Thread(target=self._now_playing, args=(uid, tid, payload.get("activeDeviceId")),
                         name="scrobble-np", daemon=True).start()

    def _now_playing(self, uid: str, tid: str, device_id: str | None) -> None:
        conn = self._open()
        try:
            now_playing(conn, self.cfg, uid, tid, self.transport, self.version, device_id)
        except Exception:
            log.exception("now playing failed")
        finally:
            conn.close()


# ---------- ListenBrainz history import (recommender signals) ----------

IMPORT_PAGE = 1000
IMPORT_MAX = 100_000


def import_listenbrainz(conn: sqlite3.Connection, uid: str, transport=None, max_listens: int = IMPORT_MAX,
                        progress=None) -> dict:
    """Copy the user's ListenBrainz listens that match library tracks into `events` as imported plays
    (imported = 'listenbrainz'; idempotent ids; listens the server already has from a client are skipped)."""
    a = account(conn, uid, "listenbrainz")
    if not a or not a["token"] or not a["username"]:
        raise ScrobbleError("auth", "connect ListenBrainz first")
    from homebeat.app import imports

    matcher = imports.Matcher(conn)
    res = {"scanned": 0, "matched": 0, "imported": 0, "alreadyKnown": 0, "unmatched": 0}
    max_ts = None
    with _client(transport) as client:
        while res["scanned"] < max_listens:
            params: dict[str, Any] = {"count": IMPORT_PAGE}
            if max_ts is not None:
                params["max_ts"] = max_ts
            try:
                r = client.get(f"{LB_API}/1/user/{a['username']}/listens", params=params,
                               headers={"Authorization": f"Token {a['token']}"})
            except httpx.HTTPError as e:
                raise ScrobbleError("retry", f"ListenBrainz unreachable: {e}") from e
            listens = ((_lb_check(r).get("payload") or {}).get("listens")) or []
            if not listens:
                break
            imports.store_listens(conn, uid, "listenbrainz", (_lb_row(li) for li in listens), matcher, res)
            if progress:
                progress(res)
            oldest = min(int(li.get("listened_at") or 0) for li in listens)
            if max_ts is not None and oldest >= max_ts:
                break
            max_ts = oldest
            if len(listens) < IMPORT_PAGE:
                break
    return res


def _lb_row(li: dict) -> dict:
    tm = li.get("track_metadata") or {}
    info = tm.get("additional_info") or {}
    mapping = tm.get("mbid_mapping") or {}
    return {"ts": int(li.get("listened_at") or 0), "artist": tm.get("artist_name"), "title": tm.get("track_name"),
            "mbid": mapping.get("recording_mbid") or info.get("recording_mbid")}


def remove_imported(conn: sqlite3.Connection, uid: str, source: str = "listenbrainz") -> int:
    """Delete the user's imported events from `source` and recompute play counts (see app.imports)."""
    from homebeat.app import imports

    return imports.remove_imported(conn, uid, source)


def imported_count(conn: sqlite3.Connection, uid: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM events WHERE user_id = ? AND imported IS NOT NULL", (uid,)).fetchone()[0]
