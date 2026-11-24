"""Admin want-list view (GET/PUT/DELETE /admin/wants*, schema v8 toget_admin): every user's To get items,
check-off with a note, un-check, "found in library" suggestions, and what the user sees of it."""
from nasmusic import db
from nasmusic.db import migrations
from tests.conftest import make_flac

API = "/api/v1"


def _add(c, title, artist="Someone Else", type="album", note=None):
    r = c.post(f"{API}/toget", json={"type": type, "title": title, "artist": artist, "note": note})
    assert r.status_code == 200, r.text
    return r.json()


def test_admin_only(client, bob):
    item = _add(bob, "Bob Record")
    assert bob.get(f"{API}/admin/wants").status_code == 403
    assert bob.put(f"{API}/admin/wants/{item['id']}/done", json={}).status_code == 403
    assert bob.delete(f"{API}/admin/wants/{item['id']}/done").status_code == 403
    assert bob.post(f"{API}/admin/wants/check-found", json={}).status_code == 403
    assert client.get(f"{API}/admin/wants").status_code == 200


def test_list_filter_and_search(client, bob):
    a = _add(client, "Alice LP", "Band A")
    b = _add(bob, "Bob Record", "Band B", note="the 2019 reissue")
    t = _add(bob, "Bob Song", "Band B", type="track")
    gone = _add(bob, "Dismissed Thing", "Band C")
    bob.patch(f"{API}/toget/{gone['id']}", json={"status": "dismissed"})

    res = client.get(f"{API}/admin/wants").json()
    ids = [x["id"] for x in res["items"]]
    assert set(ids) == {a["id"], b["id"], t["id"]}  # default "active" hides dismissed
    row = next(x for x in res["items"] if x["id"] == b["id"])
    assert (row["username"], row["userId"], row["done"], row["note"]) == ("bob", bob.user_id, False, "the 2019 reissue")
    assert row["links"] and "createdAt" in row
    users = {u["username"]: u for u in res["users"]}
    assert users["bob"]["open"] == 2 and users["bob"]["total"] == 3 and users["alice"]["open"] == 1

    def ids_for(**params):
        return {x["id"] for x in client.get(f"{API}/admin/wants", params=params).json()["items"]}

    assert ids_for(userId=bob.user_id) == {b["id"], t["id"]}
    assert ids_for(type="track") == {t["id"]}
    assert ids_for(q="reissue") == {b["id"]}
    assert ids_for(q="BOB") == {b["id"], t["id"]}  # username / title, case-insensitive
    assert ids_for(q="100%") == set()
    assert ids_for(status="dismissed") == {gone["id"]}
    assert len(ids_for(status="all")) == 4


def test_check_uncheck_and_user_view(client, bob):
    b = _add(bob, "Bob Record")
    r = client.put(f"{API}/admin/wants/{b['id']}/done", json={"note": "  ripped from CD  "})
    assert r.status_code == 200
    d = r.json()
    assert d["done"] and d["doneNote"] == "ripped from CD" and d["doneBy"] == "alice" and d["doneAt"]
    first_at = d["doneAt"]
    # checking again just updates the note
    d = client.put(f"{API}/admin/wants/{b['id']}/done", json={"note": "flac"}).json()
    assert d["doneNote"] == "flac" and d["doneAt"] == first_at
    # the user sees the admin's mark; their own status is untouched
    mine = bob.get(f"{API}/toget").json()[0]
    assert mine["status"] == "want" and mine["adminAdded"] == {"at": first_at, "note": "flac"}
    # hide done
    assert client.get(f"{API}/admin/wants", params={"done": "false"}).json()["items"] == []
    assert [x["id"] for x in client.get(f"{API}/admin/wants", params={"done": "true"}).json()["items"]] == [b["id"]]

    d = client.delete(f"{API}/admin/wants/{b['id']}/done").json()
    assert not d["done"] and d["doneNote"] is None
    assert bob.get(f"{API}/toget").json()[0]["adminAdded"] is None
    assert client.put(f"{API}/admin/wants/nope/done", json={}).status_code == 404
    assert client.delete(f"{API}/admin/wants/nope/done").status_code == 404
    # check-off goes with the item
    client.put(f"{API}/admin/wants/{b['id']}/done")
    bob.delete(f"{API}/toget/{b['id']}")
    assert client.get(f"{API}/admin/wants").json()["items"] == []


def test_found_in_library_is_suggested(client, bob, conn, app, base_flac, cfg):
    owned = _add(bob, "First Album", "Artist One")  # already in the test library
    want = _add(bob, "New Record")
    other = _add(client, "Still Missing")
    items = {x["id"]: x for x in client.get(f"{API}/admin/wants").json()["items"]}
    assert items[owned["id"]]["suggestDone"] and not items[want["id"]]["suggestDone"]

    make_flac(base_flac, cfg.library_root / "Someone Else" / "New Record" / "01.flac", title="x",
              artist="Someone Else", albumartist="Someone Else", album="New Record")
    assert app.state.ctx.jobs.run("scan")["ok"]
    res = client.get(f"{API}/admin/wants").json()
    items = {x["id"]: x for x in res["items"]}
    assert items[want["id"]]["suggestDone"] and not items[other["id"]]["suggestDone"]
    assert {u["username"]: u["found"] for u in res["users"]}["bob"] == 2

    # check just one of them, then the rest
    assert client.post(f"{API}/admin/wants/check-found", json={"ids": [want["id"]]}).json() == {"checked": 1}
    assert client.post(f"{API}/admin/wants/check-found").json() == {"checked": 1}
    items = {x["id"]: x for x in client.get(f"{API}/admin/wants").json()["items"]}
    assert items[owned["id"]]["done"] and items[owned["id"]]["doneAuto"] and not items[owned["id"]]["suggestDone"]
    assert not items[other["id"]]["done"]
    assert client.post(f"{API}/admin/wants/check-found").json() == {"checked": 0}


def test_v7_to_v8(tmp_path):
    path = tmp_path / "x.db"
    conn = db.connect(path)
    db.init(conn)
    conn.execute("DROP TABLE toget_admin")
    conn.execute("UPDATE schema_version SET version = 7")
    conn.close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION >= 8
    assert {"toget_id", "note", "done_by", "done_at", "auto"} <= migrations._columns(conn, "toget_admin")
    assert len(list(tmp_path.glob("x.db.v7-backup-*"))) == 1
    assert not migrations.migrate_v7_to_v8(conn, db.schema_sql())
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
