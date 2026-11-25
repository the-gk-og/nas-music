"""Podcasts: subscriptions, feed polling, server-side episode downloads and directory search.

Feeds are shared (one row per feed URL); subscriptions, speeds and progress are per user. The poll job
(`podcasts`, every `podcast_poll_minutes`) fetches each subscribed feed with ETag / If-Modified-Since,
stores new episodes, then keeps the newest `keep_last` episodes of each show downloaded under
data_dir/podcasts (0 = stream only) within `podcast_storage_gb`, deleting older downloads first."""
from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import shutil
import sqlite3
import time
from pathlib import Path

import httpx

from homebeat.config import Config
from homebeat.core import now_iso
from homebeat.db import family
from homebeat.spoken import feeds, net, trust
from homebeat.spoken import progress as prog

log = logging.getLogger(__name__)
DOWNLOAD_BUDGET_S = 45 * 60
MAX_EPISODE_BYTES = 2 * 1024**3
EXT_FOR_TYPE = {"audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a",
                "audio/aac": ".aac", "audio/ogg": ".ogg", "audio/opus": ".opus", "audio/flac": ".flac",
                "video/mp4": ".mp4", "audio/x-m4b": ".m4b", "audio/wav": ".wav"}


class PodcastError(Exception):
    pass


def podcast_dir(cfg: Config) -> Path:
    return cfg.data_dir / "podcasts"


# ---------- feeds -> database ----------


def _fetch_feed(c: httpx.Client, url: str, etag: str | None = None, modified: str | None = None):
    headers = {"Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"}
    if etag:
        headers["If-None-Match"] = etag
    if modified:
        headers["If-Modified-Since"] = modified
    r = net.get_limited(c, url, net.MAX_FEED_BYTES, headers)
    if r.status_code == 304:
        return None, r
    if r.status_code != 200:
        raise PodcastError(f"feed returned HTTP {r.status_code}")
    return feeds.parse(r.content), r


def store_feed(conn: sqlite3.Connection, feed_url: str, feed: feeds.Feed, etag: str | None = None,
               modified: str | None = None) -> tuple[str, int]:
    """Insert/update the podcast and its episodes. Returns (podcast id, new episode count)."""
    pid = net.short_id(feed_url)
    now = now_iso()
    conn.execute(
        "INSERT INTO podcasts (id, feed_url, title, author, description, link, language, image_url, explicit, etag, "
        "last_modified, last_polled_at, last_error, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?) ON CONFLICT(id) DO UPDATE SET "
        "title = excluded.title, author = excluded.author, description = excluded.description, link = excluded.link, "
        "language = excluded.language, image_url = COALESCE(excluded.image_url, podcasts.image_url), "
        "explicit = excluded.explicit, etag = excluded.etag, last_modified = excluded.last_modified, "
        "last_polled_at = excluded.last_polled_at, last_error = NULL, updated_at = excluded.updated_at",
        (pid, feed_url, feed.title, feed.author, feed.description, feed.link, feed.language, feed.image_url,
         int(feed.explicit), etag, modified, now, now, now))
    new = 0
    for ep in feed.episodes:
        eid = net.short_id(pid, ep.guid)
        chapters = json.dumps(ep.chapters) if ep.chapters else None
        existed = conn.execute("SELECT 1 FROM podcast_episodes WHERE podcast_id = ? AND guid = ?",
                               (pid, ep.guid)).fetchone() is not None
        conn.execute(
            "INSERT INTO podcast_episodes (id, podcast_id, guid, title, description, link, published_at, duration_s, "
            "enclosure_url, enclosure_type, enclosure_bytes, image_url, explicit, season, episode, chapters_url, "
            "chapters) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(podcast_id, guid) DO UPDATE SET title = excluded.title, description = excluded.description, "
            "link = excluded.link, published_at = COALESCE(excluded.published_at, podcast_episodes.published_at), "
            "duration_s = COALESCE(excluded.duration_s, podcast_episodes.duration_s), "
            "enclosure_url = excluded.enclosure_url, enclosure_type = excluded.enclosure_type, "
            "enclosure_bytes = excluded.enclosure_bytes, image_url = excluded.image_url, explicit = excluded.explicit, "
            "season = excluded.season, episode = excluded.episode, chapters_url = excluded.chapters_url, "
            "chapters = COALESCE(excluded.chapters, podcast_episodes.chapters)",
            (eid, pid, ep.guid, ep.title, ep.description, ep.link, ep.published_at, ep.duration_s, ep.enclosure_url,
             ep.enclosure_type, ep.enclosure_bytes, ep.image_url, int(ep.explicit), ep.season, ep.episode,
             ep.chapters_url, chapters))
        new += not existed
    return pid, new


def poll_one(conn: sqlite3.Connection, pid: str, transport=None, force: bool = False) -> dict:
    row = conn.execute("SELECT * FROM podcasts WHERE id = ?", (pid,)).fetchone()
    if row is None:
        raise KeyError(pid)
    try:
        with trust.for_podcast(conn, pid), net.client(transport) as c:
            feed, r = _fetch_feed(c, row["feed_url"], None if force else row["etag"],
                                  None if force else row["last_modified"])
    except (httpx.HTTPError, PodcastError, feeds.FeedError, net.BadUrl) as e:
        conn.execute("UPDATE podcasts SET last_polled_at = ?, last_error = ? WHERE id = ?",
                     (now_iso(), str(e)[:500], pid))
        return {"id": pid, "ok": False, "error": str(e)}
    if feed is None:
        conn.execute("UPDATE podcasts SET last_polled_at = ?, last_error = NULL WHERE id = ?", (now_iso(), pid))
        return {"id": pid, "ok": True, "new": 0, "notModified": True}
    _, new = store_feed(conn, row["feed_url"], feed, r.headers.get("etag"), r.headers.get("last-modified"))
    return {"id": pid, "ok": True, "new": new}


def subscribe(conn: sqlite3.Connection, uid: str, feed_url: str, transport=None) -> str:
    """Subscribe `uid` to a feed (fetched now if the server does not know it yet). Returns the podcast id.
    Only admins may add feeds on the home network (or join one an admin already subscribed to)."""
    known = conn.execute("SELECT id FROM podcasts WHERE feed_url = ?", ((feed_url or "").strip(),)).fetchone()
    ok = trust.is_admin(conn, uid) or (known is not None and trust.podcast_ok(conn, known["id"]))
    with net.local_allowed(ok):
        return _subscribe(conn, uid, feed_url, transport)


def _subscribe(conn: sqlite3.Connection, uid: str, feed_url: str, transport=None) -> str:
    feed_url = net.check_url(feed_url)
    row = conn.execute("SELECT id FROM podcasts WHERE feed_url = ?", (feed_url,)).fetchone()
    if row is None:
        try:
            with net.client(transport) as c:
                feed, r = _fetch_feed(c, feed_url)
        except httpx.HTTPError as e:
            raise PodcastError(f"could not fetch the feed: {e}") from e
        except feeds.FeedError as e:
            raise PodcastError(f"not a podcast feed: {e}") from e
        if feed.new_feed_url and feed.new_feed_url != feed_url:
            try:
                return _subscribe(conn, uid, feed.new_feed_url, transport)
            except (PodcastError, net.BadUrl):
                pass
        pid, _ = store_feed(conn, feed_url, feed, r.headers.get("etag"), r.headers.get("last-modified"))
    else:
        pid = row["id"]
    conn.execute("INSERT OR IGNORE INTO podcast_subscriptions (user_id, podcast_id, subscribed_at) VALUES (?, ?, ?)",
                 (uid, pid, now_iso()))
    return pid


def unsubscribe(conn: sqlite3.Connection, cfg: Config, uid: str, pid: str) -> bool:
    n = conn.execute("DELETE FROM podcast_subscriptions WHERE user_id = ? AND podcast_id = ?", (uid, pid)).rowcount
    if n and not conn.execute("SELECT 1 FROM podcast_subscriptions WHERE podcast_id = ?", (pid,)).fetchone():
        forget(conn, cfg, pid)
    return bool(n)


def forget(conn: sqlite3.Connection, cfg: Config, pid: str) -> None:
    """Nobody subscribes any more: drop the feed, its episodes and downloads, and its users' positions."""
    ids = [r[0] for r in conn.execute("SELECT id FROM podcast_episodes WHERE podcast_id = ?", (pid,))]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        conn.execute(f"DELETE FROM spoken_progress WHERE kind = 'episode' AND item_id IN ({','.join('?' * len(chunk))})",
                     chunk)
    conn.execute("DELETE FROM podcasts WHERE id = ?", (pid,))
    shutil.rmtree(podcast_dir(cfg) / pid, ignore_errors=True)


# ---------- server downloads ----------


def _ext(url: str, ctype: str | None) -> str:
    if ctype:
        e = EXT_FOR_TYPE.get(ctype.split(";")[0].strip().lower())
        if e:
            return e
    path = httpx.URL(url).path.lower()
    for e in feeds.AUDIO_EXT:
        if path.endswith(e):
            return e
    return mimetypes.guess_extension(ctype or "") or ".mp3"


def _download(cfg: Config, c: httpx.Client, ep: sqlite3.Row) -> tuple[str, int]:
    url = net.check_url(ep["enclosure_url"])
    folder = podcast_dir(cfg) / ep["podcast_id"]
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f"{ep['id']}.part"
    with c.stream("GET", url, timeout=httpx.Timeout(60.0, connect=10.0)) as r:
        if r.status_code != 200:
            raise PodcastError(f"HTTP {r.status_code}")
        name = f"{ep['id']}{_ext(str(r.url), r.headers.get('content-type') or ep['enclosure_type'])}"
        size = 0
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                size += len(chunk)
                if size > MAX_EPISODE_BYTES:
                    raise PodcastError("episode larger than 2 GB")
                f.write(chunk)
    tmp.replace(folder / name)
    return f"{ep['podcast_id']}/{name}", size


def _delete_local(conn: sqlite3.Connection, cfg: Config, ep_id: str, rel: str | None) -> None:
    if rel:
        (podcast_dir(cfg) / rel).unlink(missing_ok=True)
    conn.execute("UPDATE podcast_episodes SET local_path = NULL, local_bytes = NULL, downloaded_at = NULL "
                 "WHERE id = ?", (ep_id,))


def keep_last(cfg: Config, row) -> int:
    return cfg.podcast_keep_last if row["keep_last"] is None else int(row["keep_last"])


def sync_downloads(conn: sqlite3.Connection, cfg: Config, transport=None,
                   budget_s: float = DOWNLOAD_BUDGET_S) -> dict:
    """Make the server's downloads match each show's keep-last-N within the storage cap."""
    t0 = time.monotonic()
    cap = int(cfg.podcast_storage_gb * 1024**3)
    downloaded = deleted = failed = 0
    want: list[sqlite3.Row] = []
    for p in conn.execute("SELECT p.* FROM podcasts p WHERE EXISTS (SELECT 1 FROM podcast_subscriptions s "
                          "WHERE s.podcast_id = p.id) ORDER BY p.title").fetchall():
        n = keep_last(cfg, p)
        eps = conn.execute("SELECT * FROM podcast_episodes WHERE podcast_id = ? "
                           "ORDER BY COALESCE(published_at, '') DESC, rowid DESC", (p["id"],)).fetchall()
        for i, ep in enumerate(eps):
            if i < n:
                want.append(ep)
            elif ep["local_path"]:
                _delete_local(conn, cfg, ep["id"], ep["local_path"])
                deleted += 1
    # Orphan files (feed forgotten, or an interrupted download) go too.
    root = podcast_dir(cfg)
    if root.is_dir():
        known = {r[0] for r in conn.execute("SELECT local_path FROM podcast_episodes WHERE local_path IS NOT NULL")}
        for f in root.glob("*/*"):
            rel = f"{f.parent.name}/{f.name}"
            if rel not in known and (f.suffix != ".part" or f.stat().st_mtime < time.time() - 3600):
                f.unlink(missing_ok=True)
    used = conn.execute("SELECT COALESCE(SUM(local_bytes), 0) FROM podcast_episodes").fetchone()[0]
    want.sort(key=lambda e: e["published_at"] or "", reverse=True)  # newest first gets the space
    with net.client(transport) as c:
        for ep in want:
            if ep["local_path"] or time.monotonic() - t0 > budget_s:
                continue
            est = ep["enclosure_bytes"] or 60 * 1024 * 1024
            if used + est > cap:
                # make room by dropping the oldest downloads that are older than this episode
                victims = conn.execute(
                    "SELECT id, local_path, local_bytes FROM podcast_episodes WHERE local_path IS NOT NULL "
                    "AND COALESCE(published_at, '') < ? ORDER BY COALESCE(published_at, '')",
                    (ep["published_at"] or "",)).fetchall()
                for v in victims:
                    if used + est <= cap:
                        break
                    _delete_local(conn, cfg, v["id"], v["local_path"])
                    used -= v["local_bytes"] or 0
                    deleted += 1
                if used + est > cap:
                    continue
            try:
                with trust.for_podcast(conn, ep["podcast_id"]):
                    rel, size = _download(cfg, c, ep)
            except (httpx.HTTPError, PodcastError, net.BadUrl, OSError) as e:
                failed += 1
                conn.execute("UPDATE podcast_episodes SET download_error = ? WHERE id = ?", (str(e)[:300], ep["id"]))
                continue
            conn.execute("UPDATE podcast_episodes SET local_path = ?, local_bytes = ?, downloaded_at = ?, "
                         "download_error = NULL WHERE id = ?", (rel, size, now_iso(), ep["id"]))
            used += size
            downloaded += 1
    return {"downloaded": downloaded, "deleted": deleted, "failed": failed, "bytes": used}


def run(conn: sqlite3.Connection, cfg: Config, transport=None) -> dict:
    """The `podcasts` job: poll every subscribed feed, then sync server downloads."""
    polled = new = errors = 0
    for (pid,) in conn.execute("SELECT DISTINCT podcast_id FROM podcast_subscriptions").fetchall():
        res = poll_one(conn, pid, transport)
        polled += 1
        new += res.get("new", 0)
        errors += not res["ok"]
    out = {"polled": polled, "newEpisodes": new, "errors": errors}
    out.update(sync_downloads(conn, cfg, transport))
    return out


# ---------- chapters ----------


def chapters(conn: sqlite3.Connection, cfg: Config, ep: sqlite3.Row, transport=None) -> list[dict]:
    """Feed chapters, else the Podcasting 2.0 JSON file (fetched once and stored), else the downloaded file's
    embedded chapters (ffprobe)."""
    if ep["chapters"]:
        try:
            return json.loads(ep["chapters"])
        except ValueError:
            pass
    out: list[dict] | None = None
    if ep["chapters_url"]:
        try:
            with trust.for_podcast(conn, ep["podcast_id"]), net.client(transport) as c:
                r = net.get_limited(c, ep["chapters_url"], 2 * 1024 * 1024)
            if r.status_code == 200:
                out = feeds.parse_json_chapters(r.content)
        except (httpx.HTTPError, feeds.FeedError, net.BadUrl) as e:
            log.info("chapters for %s: %s", ep["id"], e)
            return []
    if out is None and ep["local_path"]:
        from homebeat.spoken.audiobooks import probe_chapters

        out = [{"start": s, "title": t} for s, t in probe_chapters(podcast_dir(cfg) / ep["local_path"])]
    if out is not None:
        conn.execute("UPDATE podcast_episodes SET chapters = ? WHERE id = ?", (json.dumps(out), ep["id"]))
    return out or []


# ---------- API shapes ----------


def show_api(p, sub=None, unplayed: int | None = None, cfg: Config | None = None) -> dict:
    out = {
        "id": p["id"], "feedUrl": p["feed_url"], "title": p["title"], "author": p["author"],
        "description": p["description"], "summary": feeds.plain_text(p["description"]), "link": p["link"],
        "language": p["language"], "explicit": bool(p["explicit"]), "hasImage": bool(p["image_url"]),
        "lastPolledAt": p["last_polled_at"], "lastError": p["last_error"],
        "serverKeepLast": keep_last(cfg, p) if cfg else p["keep_last"],
    }
    if sub is not None:
        out.update(subscribed=True, speed=sub["speed"], skipSilence=bool(sub["skip_silence"]),
                   autoDownload=bool(sub["auto_download"]), subscribedAt=sub["subscribed_at"])
    else:
        out.update(subscribed=False, speed=1.0, skipSilence=False, autoDownload=False, subscribedAt=None)
    if unplayed is not None:
        out["unplayed"] = unplayed
    return out


def episode_api(e, prog_row: dict | None = None, show: dict | None = None, full: bool = False) -> dict:
    out = {
        "id": e["id"], "podcastId": e["podcast_id"], "title": e["title"],
        "summary": feeds.plain_text(e["description"]), "publishedAt": e["published_at"],
        "durationS": e["duration_s"], "explicit": bool(e["explicit"]), "season": e["season"],
        "episode": e["episode"], "contentType": e["enclosure_type"], "sizeBytes": e["local_bytes"] or e["enclosure_bytes"],
        "onServer": bool(e["local_path"]), "hasChapters": bool(e["chapters"] or e["chapters_url"]),
        "link": e["link"],
        "progress": prog_row,
    }
    if full:
        out["description"] = e["description"]
    if show is not None:
        out["podcastTitle"] = show.get("title")
    return out


def visible(conn: sqlite3.Connection, uid: str) -> str:
    """SQL fragment (alias p = podcasts, e = podcast_episodes optional) for family mode: explicit shows and
    episodes are hidden while it is on."""
    return " AND p.explicit = 0" if family.policy(conn, uid).enabled else ""


def subscriptions(conn: sqlite3.Connection, cfg: Config, uid: str) -> list[dict]:
    fam = family.policy(conn, uid).enabled
    rows = conn.execute(
        "SELECT p.*, s.speed, s.skip_silence, s.auto_download, s.subscribed_at, "
        " (SELECT MAX(published_at) FROM podcast_episodes e WHERE e.podcast_id = p.id) AS latest, "
        " (SELECT COUNT(*) FROM podcast_episodes e WHERE e.podcast_id = p.id" + (" AND e.explicit = 0" if fam else "") +
        "  AND NOT EXISTS (SELECT 1 FROM spoken_progress sp WHERE sp.user_id = s.user_id AND sp.kind = 'episode' "
        "  AND sp.item_id = e.id AND sp.played = 1)) AS unplayed "
        "FROM podcast_subscriptions s JOIN podcasts p ON p.id = s.podcast_id WHERE s.user_id = ?"
        + visible(conn, uid) + " ORDER BY latest DESC", (uid,)).fetchall()
    out = []
    for r in rows:
        d = show_api(r, r, r["unplayed"], cfg)
        d["latestAt"] = r["latest"]
        out.append(d)
    return out


def episodes(conn: sqlite3.Connection, uid: str, pid: str | None, filt: str = "all", offset: int = 0,
             limit: int = 100) -> list[dict]:
    """Episodes of one show (pid) or of every subscribed show (pid None: the "new episodes" list)."""
    fam = family.policy(conn, uid).enabled
    where = ["1"]
    args: list = [uid]
    if pid:
        where.append("e.podcast_id = ?")
        args.append(pid)
    else:
        where.append("e.podcast_id IN (SELECT podcast_id FROM podcast_subscriptions WHERE user_id = ?)")
        args.append(uid)
    if fam:
        where.append("e.explicit = 0 AND p.explicit = 0")
    if filt == "unplayed":
        where.append("COALESCE(sp.played, 0) = 0")
    elif filt == "in_progress":
        where.append("COALESCE(sp.played, 0) = 0 AND COALESCE(sp.position_s, 0) > 0")
    elif filt == "downloaded":
        where.append("e.local_path IS NOT NULL")
    rows = conn.execute(
        "SELECT e.*, p.title AS ptitle, sp.kind, sp.item_id, sp.position_s, sp.duration_s AS sp_dur, sp.played, "
        "sp.speed, sp.updated_at, sp.device_id FROM podcast_episodes e JOIN podcasts p ON p.id = e.podcast_id "
        "LEFT JOIN spoken_progress sp ON sp.user_id = ? AND sp.kind = 'episode' AND sp.item_id = e.id "
        f"WHERE {' AND '.join(where)} ORDER BY COALESCE(e.published_at, '') DESC, e.rowid DESC LIMIT ? OFFSET ?",
        (*args, limit, offset)).fetchall()
    out = []
    for r in rows:
        pr = None
        if r["item_id"]:
            pr = {"kind": "episode", "id": r["id"], "positionS": r["position_s"], "durationS": r["sp_dur"],
                  "played": bool(r["played"]), "speed": r["speed"], "updatedAt": r["updated_at"],
                  "deviceId": r["device_id"]}
        out.append(episode_api(r, pr, {"title": r["ptitle"]}))
    return out


def episode_row(conn: sqlite3.Connection, uid: str, eid: str):
    """The episode if the user may see it (family mode), with the show's title/explicit."""
    r = conn.execute("SELECT e.*, p.title AS ptitle, p.explicit AS pexplicit FROM podcast_episodes e "
                     "JOIN podcasts p ON p.id = e.podcast_id WHERE e.id = ?", (eid,)).fetchone()
    if r is None:
        return None
    if family.policy(conn, uid).enabled and (r["explicit"] or r["pexplicit"]):
        return None
    return r


def mark_all_played(conn: sqlite3.Connection, uid: str, device_id: str | None, pid: str) -> int:
    n = 0
    for (eid,) in conn.execute("SELECT id FROM podcast_episodes WHERE podcast_id = ?", (pid,)).fetchall():
        applied, _ = prog.put(conn, uid, device_id, "episode", eid, played=True)
        n += applied
    return n


# ---------- directory search ----------


def search_itunes(term: str, limit: int = 25, transport=None) -> list[dict]:
    """iTunes Search API (free, no key) - used for discovery only: we subscribe to the feed URL it returns."""
    with net.client(transport, timeout=httpx.Timeout(10.0, connect=5.0)) as c:
        r = c.get("https://itunes.apple.com/search",
                  params={"media": "podcast", "entity": "podcast", "term": term, "limit": limit})
        r.raise_for_status()
        data = r.json()
    out = []
    for it in data.get("results") or []:
        if not it.get("feedUrl"):
            continue
        out.append({
            "title": it.get("collectionName") or it.get("trackName") or "",
            "author": it.get("artistName"),
            "feedUrl": it["feedUrl"],
            "imageUrl": it.get("artworkUrl600") or it.get("artworkUrl100"),
            "explicit": (it.get("collectionExplicitness") or it.get("trackExplicitness")) == "explicit",
            "genre": it.get("primaryGenreName"),
            "episodeCount": it.get("trackCount"),
            "source": "itunes",
        })
    return out


def search_podcastindex(term: str, key: str, secret: str, limit: int = 25, transport=None,
                        now: int | None = None) -> list[dict]:
    """Podcast Index (free key + secret from api.podcastindex.org; config podcastindex_api_key/secret)."""
    ts = str(now or int(time.time()))
    auth = hashlib.sha1((key + secret + ts).encode()).hexdigest()
    headers = {"X-Auth-Key": key, "X-Auth-Date": ts, "Authorization": auth}
    with net.client(transport, timeout=httpx.Timeout(10.0, connect=5.0)) as c:
        r = c.get("https://api.podcastindex.org/api/1.0/search/byterm", params={"q": term, "max": limit},
                  headers=headers)
        r.raise_for_status()
        data = r.json()
    out = []
    for it in data.get("feeds") or []:
        if not it.get("url"):
            continue
        out.append({
            "title": it.get("title") or "",
            "author": it.get("author") or it.get("ownerName"),
            "feedUrl": it["url"],
            "imageUrl": it.get("artwork") or it.get("image"),
            "explicit": bool(it.get("explicit")),
            "genre": ", ".join((it.get("categories") or {}).values()) or None
            if isinstance(it.get("categories"), dict) else None,
            "episodeCount": it.get("episodeCount"),
            "source": "podcastindex",
        })
    return out


def search(conn: sqlite3.Connection, cfg: Config, uid: str, term: str, limit: int = 25, transport=None) -> dict:
    results: list[dict] = []
    errors = []
    if cfg.podcastindex_api_key and cfg.podcastindex_api_secret:
        try:
            results += search_podcastindex(term, cfg.podcastindex_api_key, cfg.podcastindex_api_secret, limit,
                                           transport)
        except (httpx.HTTPError, ValueError) as e:
            errors.append(f"podcastindex: {e}")
    if cfg.podcast_itunes_search:
        try:
            results += search_itunes(term, limit, transport)
        except (httpx.HTTPError, ValueError) as e:
            errors.append(f"itunes: {e}")
    seen: set[str] = set()
    fam = family.policy(conn, uid).enabled
    subs = {r[0] for r in conn.execute("SELECT p.feed_url FROM podcast_subscriptions s JOIN podcasts p "
                                       "ON p.id = s.podcast_id WHERE s.user_id = ?", (uid,))}
    out = []
    for r in results:
        k = r["feedUrl"].rstrip("/").lower()
        if k in seen or (fam and r["explicit"]):
            continue
        seen.add(k)
        r["subscribed"] = r["feedUrl"] in subs
        r["id"] = net.short_id(r["feedUrl"])
        out.append(r)
    return {"results": out[:limit], "errors": errors}


def image_key(pid: str) -> str:
    return f"podcast_{pid}"

