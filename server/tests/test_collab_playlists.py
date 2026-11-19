"""Collaborative playlists (schema v5): roles, entry-based conflict-safe ordering, activity, live notify."""
import threading

from nasmusic.app import playlists as pl
from tests.conftest import track_ids, user_client


def _ids(conn):
    t = track_ids(conn)
    return [t["Song 1"], t["Song 2"], t["Opening"], t["Closing"]]


def _titles(p):
    return [t["title"] for t in p["tracks"]]


class FakeHub:
    def __init__(self):
        self.sent = []

    def notify_playlist(self, uids, pid, rev, deleted=False):
        self.sent.append((set(uids), pid, rev, deleted))

    def notify_queue(self, *a):
        pass

    def disconnect_device(self, *a):
        pass


def test_roles_and_permissions(client, bob, conn, app):
    hub = FakeHub()
    app.state.ctx.hub = hub
    s1, s2, op, cl = _ids(conn)
    p = client.post("/api/v1/playlists", json={"name": "Road trip", "trackIds": [s1]}).json()
    pid = p["id"]
    assert p["role"] == "owner" and p["entries"][0]["addedBy"] == "alice"
    # not shared yet: bob can't see it
    assert bob.get(f"/api/v1/playlists/{pid}").status_code == 404
    assert pid not in [x["id"] for x in bob.get("/api/v1/playlists").json()]
    # invite bob as viewer
    users = client.get("/api/v1/users").json()
    assert {"id": bob.user_id, "username": "bob"} in users
    m = client.post(f"/api/v1/playlists/{pid}/members", json={"username": "bob", "role": "viewer"})
    assert m.status_code == 200 and [x["role"] for x in m.json()] == ["owner", "viewer"]
    assert hub.sent[-1][0] == {client.user_id, bob.user_id}
    got = bob.get(f"/api/v1/playlists/{pid}").json()
    assert got["role"] == "viewer" and got["owner"] == "alice" and got["collaborative"]
    assert bob.post(f"/api/v1/playlists/{pid}/tracks", json={"trackIds": [s2]}).status_code == 403
    # promote to editor: bob adds, his name is on the entry
    assert client.patch(f"/api/v1/playlists/{pid}/members/{bob.user_id}", json={"role": "editor"}).status_code == 200
    r = bob.post(f"/api/v1/playlists/{pid}/tracks", json={"trackIds": [s2]})
    assert r.status_code == 200 and [e["addedBy"] for e in r.json()["entries"]] == ["alice", "bob"]
    # editors can't rename, delete, or manage members
    assert bob.patch(f"/api/v1/playlists/{pid}", json={"name": "Mine"}).status_code == 403
    assert bob.delete(f"/api/v1/playlists/{pid}").status_code == 403
    assert bob.post(f"/api/v1/playlists/{pid}/members", json={"username": "alice"}).status_code == 403
    # activity
    acts = [a["action"] for a in bob.get(f"/api/v1/playlists/{pid}/activity").json()]
    assert acts == ["add", "member_role", "member_add", "create"]
    # owner removes bob -> bob is told it's gone for him
    assert client.delete(f"/api/v1/playlists/{pid}/members/{bob.user_id}").status_code == 204
    assert hub.sent[-1] == ({bob.user_id}, pid, hub.sent[-1][2], True)
    assert bob.get(f"/api/v1/playlists/{pid}").status_code == 404
    # bob's entry stays, attributed to him
    assert client.get(f"/api/v1/playlists/{pid}").json()["entries"][1]["addedBy"] == "bob"


def test_member_can_leave_owner_cannot(client, bob, conn):
    pid = client.post("/api/v1/playlists", json={"name": "P"}).json()["id"]
    client.post(f"/api/v1/playlists/{pid}/members", json={"userId": bob.user_id, "role": "editor"})
    assert client.delete(f"/api/v1/playlists/{pid}/members/{client.user_id}").status_code == 422
    assert bob.delete(f"/api/v1/playlists/{pid}/members/{bob.user_id}").status_code == 204
    assert bob.get(f"/api/v1/playlists/{pid}").status_code == 404
    assert client.post(f"/api/v1/playlists/{pid}/members", json={"username": "nobody"}).status_code == 404


def test_entry_operations_are_conflict_safe(client, bob, conn):
    s1, s2, op, cl = _ids(conn)
    p = client.post("/api/v1/playlists", json={"name": "P", "trackIds": [s1, s2, op]}).json()
    pid = p["id"]
    client.post(f"/api/v1/playlists/{pid}/members", json={"username": "bob", "role": "editor"})
    e1, e2, e3 = [e["id"] for e in p["entries"]]
    # both clients have the same (stale) view. Alice moves Opening to the top; Bob inserts after Song 1.
    client.post(f"/api/v1/playlists/{pid}/entries/{e3}/move", json={"afterEntryId": None})
    r = bob.post(f"/api/v1/playlists/{pid}/tracks", json={"trackIds": [cl], "afterEntryId": e1})
    # Bob's intent (Closing right after Song 1) holds despite Alice's move
    assert _titles(r.json()) == ["Opening", "Song 1", "Closing", "Song 2"]
    # both remove Song 2 at once: idempotent
    assert client.delete(f"/api/v1/playlists/{pid}/entries/{e2}").status_code == 200
    assert bob.delete(f"/api/v1/playlists/{pid}/entries/{e2}").status_code == 200
    # moving after an entry that someone removed -> 409 (client refetches)
    assert bob.post(f"/api/v1/playlists/{pid}/entries/{e1}/move", json={"afterEntryId": e2}).status_code == 409
    # insert at top with ""
    r = client.post(f"/api/v1/playlists/{pid}/tracks", json={"trackIds": [s2], "afterEntryId": ""})
    assert _titles(r.json())[0] == "Song 2"
    # duplicates are separate entries
    r = client.post(f"/api/v1/playlists/{pid}/tracks", json={"trackIds": [s2]})
    assert _titles(r.json()).count("Song 2") == 2


def test_legacy_index_endpoints_still_work(client, conn):
    s1, s2, op, cl = _ids(conn)
    pid = client.post("/api/v1/playlists", json={"name": "P", "trackIds": [s1, s2, op]}).json()["id"]
    r = client.post(f"/api/v1/playlists/{pid}/move", json={"from": 0, "to": 2})
    assert _titles(r.json()) == ["Song 2", "Opening", "Song 1"]
    r = client.post(f"/api/v1/playlists/{pid}/tracks", json={"trackIds": [cl], "position": 1})
    assert _titles(r.json()) == ["Song 2", "Closing", "Opening", "Song 1"]
    r = client.delete(f"/api/v1/playlists/{pid}/tracks/0")
    assert _titles(r.json()) == ["Closing", "Opening", "Song 1"]
    assert client.delete(f"/api/v1/playlists/{pid}/tracks/9").status_code == 422


def test_many_inserts_at_same_spot_renormalise(client, conn):
    s1, s2, op, cl = _ids(conn)
    p = client.post("/api/v1/playlists", json={"name": "P", "trackIds": [s1, s2]}).json()
    first = p["entries"][0]["id"]
    for _ in range(80):  # halves the gap each time: forces a renormalisation
        p = client.post(f"/api/v1/playlists/{p['id']}/tracks", json={"trackIds": [op], "afterEntryId": first}).json()
    t = _titles(p)
    assert t[0] == "Song 1" and t[-1] == "Song 2" and len(t) == 82


def test_concurrent_edits_lose_nothing(app, conn, client):
    s1, s2, op, cl = _ids(conn)
    pid = client.post("/api/v1/playlists", json={"name": "P", "trackIds": [s1]}).json()["id"]
    editors = []
    for name in ("carol", "dave", "erin"):
        c = user_client(app, conn, name, admin=False)
        c.__enter__()
        client.post(f"/api/v1/playlists/{pid}/members", json={"username": name, "role": "editor"})
        editors.append(c)
    errors = []

    def work(c):
        for _ in range(10):
            r = c.post(f"/api/v1/playlists/{pid}/tracks", json={"trackIds": [op], "afterEntryId": None})
            if r.status_code != 200:
                errors.append(r.text)

    threads = [threading.Thread(target=work, args=(c,)) for c in editors]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for c in editors:
        c.__exit__(None, None, None)
    assert not errors
    p = client.get(f"/api/v1/playlists/{pid}").json()
    assert len(p["entries"]) == 31 and p["rev"] >= 30
    assert {e["addedBy"] for e in p["entries"]} == {"alice", "carol", "dave", "erin"}
    pos = [r[0] for r in conn.execute("SELECT position FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
                                      (pid,))]
    assert len(set(pos)) == len(pos)


def test_shared_playlist_pin_and_search(client, bob, conn):
    pid = client.post("/api/v1/playlists", json={"name": "Shared Faves"}).json()["id"]
    client.post(f"/api/v1/playlists/{pid}/members", json={"username": "bob", "role": "viewer"})
    assert bob.post("/api/v1/pins", json={"type": "playlist", "id": pid}).status_code in (200, 201, 204)
    assert any(p["id"] == pid for p in bob.get("/api/v1/search", params={"q": "Shared"}).json()["playlists"])


def test_remove_missing_tracks_cleans_entries(client, conn):
    s1, s2, *_ = _ids(conn)
    pid = client.post("/api/v1/playlists", json={"name": "P", "trackIds": [s1, s2]}).json()["id"]
    conn.execute("UPDATE tracks SET missing = 1 WHERE id = ?", (s1,))
    from nasmusic.app import health

    assert health.remove_missing(conn, [s1]) == 1
    assert [e["trackId"] for e in client.get(f"/api/v1/playlists/{pid}").json()["entries"]] == [s2]


def test_between():
    assert pl._between(None, None, 2) == [1.0, 2.0]
    assert pl._between(1.0, None, 2) == [2.0, 3.0]
    assert pl._between(None, 1.0, 2) == [-1.0, 0.0]
    assert pl._between(1.0, 2.0, 1) == [1.5]
    assert pl._between(1.0, 1.0 + 1e-12, 1) is None
