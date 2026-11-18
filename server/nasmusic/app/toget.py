"""Per-user "To get" list (docs/v3-ux.md §4): albums and songs to acquire.

Items are de-duplicated per user by (type, normalised "artist|title"). Statuses: want / got / dismissed.
After each library scan `match_owned` marks wanted items `got` when the library now has them (MBID, else
normalised names). Suggestions answered "want" are copied in (`from_suggestion`)."""
from __future__ import annotations

import sqlite3
import uuid

from nasmusic import catalog
from nasmusic.core import now_iso


class NotFound(Exception):
    pass


def to_dict(conn: sqlite3.Connection, r: sqlite3.Row) -> dict:
    cat = None
    if r["catalog_id"]:
        cat = conn.execute("SELECT cover_url, preview_ref FROM catalog_items WHERE id = ?",
                           (r["catalog_id"],)).fetchone()
    return {
        "id": r["id"], "type": r["type"], "title": r["title"], "artist": r["artist"], "year": r["year"],
        "mbid": r["mbid"], "catalogId": r["catalog_id"],
        "artUrl": f"catalog/{r['catalog_id']}/cover" if cat and cat["cover_url"] else None,
        "hasPreview": bool(cat and cat["preview_ref"]), "note": r["note"], "status": r["status"],
        "source": r["source"], "suggestionId": r["suggestion_id"], "ownedId": r["owned_id"],
        "createdAt": r["created_at"], "updatedAt": r["updated_at"], "gotAt": r["got_at"],
        "links": catalog.buy_links(r["artist"], r["title"]),
    }


def list_items(conn: sqlite3.Connection, uid: str, status: str = "want") -> list[dict]:
    q = "SELECT * FROM toget WHERE user_id = ?"
    params: list = [uid]
    if status != "all":
        q += " AND status = ?"
        params.append(status)
    return [to_dict(conn, r) for r in conn.execute(q + " ORDER BY created_at DESC, id", params).fetchall()]


def get(conn: sqlite3.Connection, uid: str, item_id: str) -> dict:
    r = conn.execute("SELECT * FROM toget WHERE id = ? AND user_id = ?", (item_id, uid)).fetchone()
    if r is None:
        raise NotFound("no such item")
    return to_dict(conn, r)


def add(conn: sqlite3.Connection, uid: str, *, type: str, title: str, artist: str, year: int | None = None,
        mbid: str | None = None, catalog_id: str | None = None, note: str | None = None, source: str = "manual",
        suggestion_id: str | None = None, index: catalog.LibraryIndex | None = None) -> dict:
    """Add (or return the existing) item. A dismissed duplicate comes back as `want`. Owned -> `got`."""
    key = catalog.match_key(artist, title)
    now = now_iso()
    existing = conn.execute("SELECT * FROM toget WHERE user_id = ? AND type = ? AND match_key = ?",
                            (uid, type, key)).fetchone()
    if existing:
        conn.execute(
            "UPDATE toget SET status = CASE WHEN status = 'dismissed' THEN 'want' ELSE status END, "
            "note = COALESCE(?, note), mbid = COALESCE(mbid, ?), catalog_id = COALESCE(catalog_id, ?), "
            "updated_at = ? WHERE id = ?", (note, mbid, catalog_id, now, existing["id"]))
        return get(conn, uid, existing["id"])
    index = index or catalog.LibraryIndex(conn)
    owned = index.owned({"type": type, "title": title, "artist": artist, "mbid": mbid})
    item_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO toget (id, user_id, type, title, artist, year, mbid, catalog_id, note, status, source, "
        "suggestion_id, match_key, owned_id, created_at, updated_at, got_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (item_id, uid, type, title.strip()[:300], artist.strip()[:300], year, mbid, catalog_id, note,
         "got" if owned else "want", source, suggestion_id, key, owned, now, now, now if owned else None))
    return get(conn, uid, item_id)


def patch(conn: sqlite3.Connection, uid: str, item_id: str, status: str | None = None,
          note: str | None = None, clear_note: bool = False) -> dict:
    get(conn, uid, item_id)
    now = now_iso()
    if status is not None:
        conn.execute("UPDATE toget SET status = ?, updated_at = ?, got_at = CASE WHEN ? = 'got' "
                     "THEN COALESCE(got_at, ?) ELSE NULL END WHERE id = ?", (status, now, status, now, item_id))
    if note is not None or clear_note:
        conn.execute("UPDATE toget SET note = ?, updated_at = ? WHERE id = ?", (note, now, item_id))
    return get(conn, uid, item_id)


def delete(conn: sqlite3.Connection, uid: str, item_id: str) -> None:
    if conn.execute("DELETE FROM toget WHERE id = ? AND user_id = ?", (item_id, uid)).rowcount != 1:
        raise NotFound("no such item")


def status_for(conn: sqlite3.Connection, uid: str) -> dict[tuple[str, str], dict]:
    """(type, match_key) -> {id, status}, for marking catalogue search results."""
    return {(r["type"], r["match_key"]): {"id": r["id"], "status": r["status"]}
            for r in conn.execute("SELECT id, type, match_key, status FROM toget WHERE user_id = ?", (uid,))}


def from_suggestion(conn: sqlite3.Connection, uid: str, suggestion_id: str) -> dict | None:
    """Copy a suggestion the user answered "want" into the list (its cover/preview travel along)."""
    s = conn.execute("SELECT * FROM suggestions WHERE id = ? AND user_id = ?", (suggestion_id, uid)).fetchone()
    if s is None:
        return None
    cid = f"mb:rg:{s['release_group_mbid']}" if s["release_group_mbid"] else f"sg:{s['id']}"
    catalog.store(conn, [{"id": cid, "type": "album", "title": s["album"], "artist": s["artist"], "year": s["year"],
                          "mbid": s["release_group_mbid"], "cover_url": s["cover_url"],
                          "preview_ref": s["preview_url"]}])
    return add(conn, uid, type="album", title=s["album"], artist=s["artist"], year=s["year"],
               mbid=s["release_group_mbid"], catalog_id=cid, source="suggestion", suggestion_id=s["id"])


def match_owned(conn: sqlite3.Connection) -> int:
    """Mark every user's wanted items `got` when the library has them now. Returns how many changed."""
    wanted = conn.execute("SELECT id, type, title, artist, mbid FROM toget WHERE status = 'want'").fetchall()
    if not wanted:
        return 0
    index = catalog.LibraryIndex(conn)
    now = now_iso()
    changed = 0
    for r in wanted:
        owned = index.owned({"type": r["type"], "title": r["title"], "artist": r["artist"], "mbid": r["mbid"]})
        if owned:
            conn.execute("UPDATE toget SET status = 'got', owned_id = ?, got_at = ?, updated_at = ? WHERE id = ?",
                         (owned, now, now, r["id"]))
            changed += 1
    return changed
