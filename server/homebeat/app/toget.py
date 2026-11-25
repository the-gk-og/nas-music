"""Per-user "To get" list (docs/v3-ux.md §4): albums and songs to acquire.

Items are de-duplicated per user by (type, normalised "artist|title"). Statuses: want / got / dismissed.
After each library scan `match_owned` marks wanted items `got` when the library now has them (MBID, else
normalised names). Suggestions answered "want" are copied in (`from_suggestion`)."""
from __future__ import annotations

import sqlite3
import uuid

from homebeat import catalog
from homebeat.core import now_iso


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
        "adminAdded": admin_mark(conn, r["id"]),
    }


def admin_mark(conn: sqlite3.Connection, item_id: str) -> dict | None:
    """The admin's check-off (toget_admin), as the user sees it: {at, note} or None."""
    a = conn.execute("SELECT note, done_at FROM toget_admin WHERE toget_id = ?", (item_id,)).fetchone()
    return {"at": a["done_at"], "note": a["note"]} if a else None


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


# ---------- admin: every user's list, with check-off (toget_admin, schema v8) ----------

def _admin_dict(conn: sqlite3.Connection, r: sqlite3.Row) -> dict:
    d = to_dict(conn, r)
    done = r["done_at"] is not None
    d.update({
        "userId": r["user_id"], "username": r["username"],
        "done": done,
        "doneAt": r["done_at"], "doneNote": r["done_note"], "doneBy": r["done_by_name"], "doneAuto": bool(r["done_auto"]),
        # found in the library (scan/upload matched it, or the user marked it got) but not checked off yet
        "suggestDone": not done and (r["owned_id"] is not None or r["status"] == "got"),
    })
    return d


_ADMIN_SELECT = (
    "SELECT t.*, u.username, a.done_at, a.note AS done_note, a.auto AS done_auto, "
    "b.username AS done_by_name FROM toget t JOIN users u ON u.id = t.user_id "
    "LEFT JOIN toget_admin a ON a.toget_id = t.id LEFT JOIN users b ON b.id = a.done_by")


def admin_list(conn: sqlite3.Connection, *, user_id: str | None = None, type: str | None = None,
               status: str = "active", done: bool | None = None, q: str | None = None) -> list[dict]:
    """Every user's items. status: want/got/dismissed/all, or "active" (= not dismissed)."""
    where, params = [], []
    if user_id:
        where.append("t.user_id = ?")
        params.append(user_id)
    if type:
        where.append("t.type = ?")
        params.append(type)
    if status == "active":
        where.append("t.status != 'dismissed'")
    elif status != "all":
        where.append("t.status = ?")
        params.append(status)
    if done is not None:
        where.append("a.toget_id IS NOT NULL" if done else "a.toget_id IS NULL")
    if q and q.strip():
        like = "%" + q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(lower(t.title) LIKE ? ESCAPE '\\' OR lower(t.artist) LIKE ? ESCAPE '\\' "
                     "OR lower(COALESCE(t.note, '')) LIKE ? ESCAPE '\\' OR lower(u.username) LIKE ? ESCAPE '\\')")
        params += [like] * 4
    sql = _ADMIN_SELECT + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY t.created_at DESC, t.id"
    return [_admin_dict(conn, r) for r in conn.execute(sql, params).fetchall()]


def admin_get(conn: sqlite3.Connection, item_id: str) -> dict:
    r = conn.execute(_ADMIN_SELECT + " WHERE t.id = ?", (item_id,)).fetchone()
    if r is None:
        raise NotFound("no such item")
    return _admin_dict(conn, r)


def admin_check(conn: sqlite3.Connection, admin_id: str, item_id: str, note: str | None = None,
                auto: bool = False) -> dict:
    """Check an item off ("added"). Checking again updates the note (and keeps the original time)."""
    admin_get(conn, item_id)
    note = (note or "").strip()[:500] or None
    conn.execute(
        "INSERT INTO toget_admin (toget_id, note, done_by, done_at, auto) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (toget_id) DO UPDATE SET note = excluded.note, done_by = excluded.done_by, auto = excluded.auto",
        (item_id, note, admin_id, now_iso(), 1 if auto else 0))
    return admin_get(conn, item_id)


def admin_uncheck(conn: sqlite3.Connection, item_id: str) -> dict:
    admin_get(conn, item_id)
    conn.execute("DELETE FROM toget_admin WHERE toget_id = ?", (item_id,))
    return admin_get(conn, item_id)


def admin_check_found(conn: sqlite3.Connection, admin_id: str, ids: list[str] | None = None) -> int:
    """Check off every suggested item (found in the library, not checked yet), or just `ids` among them."""
    match_owned(conn)  # pick up anything a scan has not matched yet
    found = [d for d in admin_list(conn, done=False, status="all") if d["suggestDone"]]
    if ids is not None:
        wanted = set(ids)
        found = [d for d in found if d["id"] in wanted]
    for d in found:
        admin_check(conn, admin_id, d["id"], "Found in the library", auto=True)
    return len(found)


def admin_summary(conn: sqlite3.Connection) -> list[dict]:
    """Per user: open (not checked, not dismissed) / done / suggested counts."""
    rows = conn.execute(
        "SELECT u.id, u.username, "
        "SUM(CASE WHEN a.toget_id IS NULL AND t.status != 'dismissed' THEN 1 ELSE 0 END) AS open, "
        "SUM(CASE WHEN a.toget_id IS NOT NULL THEN 1 ELSE 0 END) AS done, "
        "SUM(CASE WHEN a.toget_id IS NULL AND (t.owned_id IS NOT NULL OR t.status = 'got') THEN 1 ELSE 0 END) "
        "AS found, COUNT(*) AS total "
        "FROM toget t JOIN users u ON u.id = t.user_id LEFT JOIN toget_admin a ON a.toget_id = t.id "
        "GROUP BY u.id ORDER BY lower(u.username)").fetchall()
    return [{"userId": r["id"], "username": r["username"], "open": r["open"],
             "done": r["done"], "found": r["found"], "total": r["total"]} for r in rows]
