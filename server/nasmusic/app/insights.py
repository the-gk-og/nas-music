"""Release Radar and Wrapped for the API: the AI's ids and numbers turned into Track / Album / Artist /
CatalogItem objects, plus their For You shelves."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from nasmusic import catalog
from nasmusic.app import ai_adapter, toget
from nasmusic.config import Config
from nasmusic.core import ai_config
from nasmusic.library import queries

LIBRARY_LIMIT = 30


class NotFound(Exception):
    pass


# ---------- Release Radar ----------


def _release(r: dict) -> dict:
    return {"date": r.get("date"), "type": r.get("type"), "upcoming": bool(r.get("upcoming")), "artist": r.get("artist")}


def release_radar(conn: sqlite3.Connection, cfg: Config, uid: str) -> dict:
    """{updatedAt, windowDays, inLibrary: ShelfItem[], notOwned: ShelfItem[]}."""
    from nasmusic.app.discover import _album_rows, album_item

    try:
        rr = ai_adapter.release_radar(conn, ai_config(cfg), uid)
    except NotImplementedError:
        rr = {"updatedAt": None, "windowDays": 56, "releases": [], "recentlyAdded": []}
    index = catalog.LibraryIndex(conn)
    tg = toget.status_for(conn, uid)
    owned: list[tuple[str, dict]] = []      # (album id, release)
    not_owned: list[dict] = []
    for r in rr.get("releases", []):
        oid = index.owned({"type": "album", "title": r["title"], "artist": r["artist"], "mbid": r["mbid"]})
        if oid:
            owned.append((oid, r))
        else:
            not_owned.append(r)
    # Not-owned releases become catalogue items, so cover, To get and the catalogue sheet just work.
    cat_ids = {f"mb:rg:{r['mbid']}": r for r in not_owned}
    have = set()
    if cat_ids:
        ids = list(cat_ids)
        have = {x[0] for x in conn.execute(f"SELECT id FROM catalog_items WHERE id IN ({','.join('?' * len(ids))})", ids)}
    fresh = [{"id": cid, "type": "album", "title": r["title"], "artist": r["artist"], "year": _year(r.get("date")),
              "mbid": r["mbid"], "cover_url": r.get("coverUrl"), "preview_ref": None}
             for cid, r in cat_ids.items() if cid not in have]
    if fresh:
        catalog.store(conn, fresh)
    new_items = []
    for cid, r in cat_ids.items():
        row = {"id": cid, "type": "album", "title": r["title"], "artist": r["artist"], "year": _year(r.get("date")),
               "mbid": r["mbid"], "cover_url": r.get("coverUrl")}
        c = catalog.api_item(row, None, tg.get(("album", catalog.match_key(r["artist"], r["title"]))))
        new_items.append({"type": "catalog", "id": cid, "title": r["title"], "subtitle": r["artist"],
                          "artUrl": c["artUrl"], "catalog": c, "release": _release(r)})
    rows = _album_rows(conn, [a for a, _ in owned] + [x["albumId"] for x in rr.get("recentlyAdded", [])])
    lib_items, seen = [], set()
    for aid, r in owned:
        if aid in rows and aid not in seen:
            seen.add(aid)
            lib_items.append({**album_item(cfg.data_dir, rows[aid]), "release": _release(r)})
    for x in rr.get("recentlyAdded", []):
        aid = x["albumId"]
        if aid in rows and aid not in seen:
            seen.add(aid)
            row = rows[aid]
            lib_items.append({**album_item(cfg.data_dir, row), "addedAt": x.get("addedAt"),
                              "release": {"date": str(row["year"]) if row["year"] else None, "type": None,
                                          "upcoming": False, "artist": row["artist"]}})
    return {"updatedAt": rr.get("updatedAt"), "windowDays": rr.get("windowDays"),
            "inLibrary": lib_items[:LIBRARY_LIMIT], "notOwned": new_items}


def _year(d: str | None) -> int | None:
    return int(d[:4]) if d and d[:4].isdigit() else None


def release_radar_shelves(conn: sqlite3.Connection, cfg: Config, uid: str) -> list[dict]:
    from nasmusic.app.discover import shelf

    rr = release_radar(conn, cfg, uid)
    return [shelf("release_radar", "release_radar", "Release Radar", rr["notOwned"][:20],
                  subtitle="New from artists you play"),
            shelf("release_radar:library", "release_radar", "New in your library", rr["inLibrary"][:20],
                  subtitle="Recently added by artists you play")]


# ---------- Wrapped ----------


def _artist_entry(conn: sqlite3.Connection, data_dir, x: dict) -> dict | None:
    from nasmusic.app.discover import _artist_item

    item = _artist_item(conn, data_dir, x["id"])
    if item is None:
        r = conn.execute("SELECT id, name, mbid FROM artists WHERE id = ?", (x["id"],)).fetchone()
        if r is None:
            return None
        item = {"artist": {"id": r["id"], "name": r["name"], "albumCount": 0, "mbid": r["mbid"]}, "artUrl": None}
    return {"artist": item["artist"], "artUrl": item["artUrl"], "plays": x["plays"], "minutes": x["minutes"]}


def wrapped(conn: sqlite3.Connection, cfg: Config, uid: str, year: int) -> dict:
    now_year = datetime.now(UTC).year
    if not 2000 <= year <= now_year + 1:
        raise NotFound("no such year")
    w = ai_adapter.wrapped(conn, ai_config(cfg), uid, year)
    return _resolve(conn, cfg, uid, w)


def _resolve(conn: sqlite3.Connection, cfg: Config, uid: str, w: dict) -> dict:
    from nasmusic.app.discover import _album_rows

    data_dir = cfg.data_dir
    tids = [x["id"] for x in w.get("topTracks", [])]
    if w.get("firstPlay"):
        tids.append(w["firstPlay"]["trackId"])
    tracks = queries.tracks_by_ids(conn, uid, tids, include_missing=True)
    w["topTracks"] = [{"track": tracks[x["id"]], "plays": x["plays"], "minutes": x["minutes"]}
                      for x in w.get("topTracks", []) if x["id"] in tracks]
    albums = _album_rows(conn, [x["id"] for x in w.get("topAlbums", [])])
    w["topAlbums"] = [{"album": queries.album_dict(albums[x["id"]], data_dir), "plays": x["plays"],
                       "minutes": x["minutes"]} for x in w.get("topAlbums", []) if x["id"] in albums]
    w["topArtists"] = [e for e in (_artist_entry(conn, data_dir, x) for x in w.get("topArtists", [])) if e]
    if "discoveries" in w:
        d = w["discoveries"]
        d["topArtists"] = [e for e in (_artist_entry(conn, data_dir, x) for x in d.get("topArtists", [])) if e]
    if w.get("firstPlay"):
        fp = w["firstPlay"]
        w["firstPlay"] = {"track": tracks[fp["trackId"]], "at": fp["at"]} if fp["trackId"] in tracks else None
    return w


def wrapped_server(conn: sqlite3.Connection, cfg: Config, uid: str, year: int) -> dict:
    w = ai_adapter.wrapped_server(conn, ai_config(cfg), year)
    w = _resolve(conn, cfg, uid, w)
    w["users"] = conn.execute("SELECT COUNT(*) FROM users WHERE disabled = 0").fetchone()[0]
    return w


def wrapped_years(conn: sqlite3.Connection, cfg: Config, uid: str | None) -> list[int]:
    years = set(ai_adapter.wrapped_years(conn, ai_config(cfg), uid))
    years.add(datetime.now(UTC).year)
    return sorted(years, reverse=True)


def wrapped_shelf(conn: sqlite3.Connection, cfg: Config, uid: str, now: datetime | None = None) -> list[dict]:
    """"Your <year> so far" (when you've played something this year) and, in January–March, last year's."""
    from nasmusic.app.discover import shelf

    now = now or datetime.now(UTC)
    items = []
    for year, final in ((now.year, False), (now.year - 1, True)):
        if final and now.month > 3:
            continue
        lo, hi = f"{year}-01-01", f"{year + 1}-01-01"
        n = conn.execute("SELECT COUNT(*) FROM events WHERE user_id = ? AND type = 'play' AND at >= ? AND at < ?",
                         (uid, lo, hi)).fetchone()[0]
        if n == 0:
            continue
        items.append({"type": "wrapped", "id": str(year),
                      "title": f"Your {year} Wrapped" if final else f"Your {year} so far",
                      "subtitle": f"{n} play{'s' if n != 1 else ''}", "artUrl": None,
                      "wrapped": {"year": year, "final": final}})
    return [shelf("wrapped", "wrapped", "Wrapped", items, subtitle="Your year in music")]
