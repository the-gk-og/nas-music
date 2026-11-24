"""Playlist folders (schema v9): per-user trees, filing own and shared playlists, moves, deletes."""
from tests.conftest import track_ids


def _names(client):
    return [(f["name"], f["parentId"]) for f in client.get("/api/v1/playlist-folders").json()]


def test_folders_tree_and_filing(client, bob, conn):
    s1 = track_ids(conn)["Song 1"]
    road = client.post("/api/v1/playlist-folders", json={"name": "Road"}).json()
    work = client.post("/api/v1/playlist-folders", json={"name": "Work"}).json()
    sub = client.post("/api/v1/playlist-folders", json={"name": "Focus", "parentId": work["id"]}).json()
    assert sub["parentId"] == work["id"] and sub["playlistCount"] == 0
    # depth first: a parent before its children
    assert _names(client) == [("Road", None), ("Work", None), ("Focus", work["id"])]
    # create straight into a folder; file another one later
    p1 = client.post("/api/v1/playlists", json={"name": "Drive", "trackIds": [s1], "folderId": road["id"]}).json()
    assert p1["folderId"] == road["id"]
    p2 = client.post("/api/v1/playlists", json={"name": "Deep", "trackIds": []}).json()
    assert p2["folderId"] is None
    r = client.put(f"/api/v1/playlists/{p2['id']}/folder", json={"folderId": sub["id"]})
    assert r.status_code == 200 and r.json()["folderId"] == sub["id"]
    by_id = {p["id"]: p for p in client.get("/api/v1/playlists").json()}
    assert by_id[p1["id"]]["folderId"] == road["id"] and by_id[p2["id"]]["folderId"] == sub["id"]
    assert {f["name"]: f["playlistCount"] for f in client.get("/api/v1/playlist-folders").json()}["Focus"] == 1

    # folders are private: bob sees none and can't use alice's
    assert bob.get("/api/v1/playlist-folders").json() == []
    assert bob.patch(f"/api/v1/playlist-folders/{road['id']}", json={"name": "x"}).status_code == 404
    assert bob.post("/api/v1/playlists", json={"name": "B", "folderId": road["id"]}).status_code == 422
    # ... nor file a playlist he can't see
    assert bob.put(f"/api/v1/playlists/{p1['id']}/folder", json={"folderId": None}).status_code == 404

    # shared playlist: bob files it in his own folder; alice's filing is unchanged
    client.post(f"/api/v1/playlists/{p1['id']}/members", json={"username": "bob", "role": "viewer"})
    mine = bob.post("/api/v1/playlist-folders", json={"name": "From Alice"}).json()
    assert bob.put(f"/api/v1/playlists/{p1['id']}/folder", json={"folderId": mine["id"]}).json()["folderId"] == mine["id"]
    assert bob.put(f"/api/v1/playlists/{p1['id']}/folder", json={"folderId": road["id"]}).status_code == 422
    assert client.get(f"/api/v1/playlists/{p1['id']}").json()["folderId"] == road["id"]
    # leaving drops bob's filing
    bob.delete(f"/api/v1/playlists/{p1['id']}/members/{bob.user_id}")
    assert conn.execute("SELECT COUNT(*) FROM playlist_folder_items WHERE user_id = ?", (bob.user_id,)).fetchone()[0] == 0


def test_move_reorder_and_delete(client, conn):
    a = client.post("/api/v1/playlist-folders", json={"name": "A"}).json()
    b = client.post("/api/v1/playlist-folders", json={"name": "B"}).json()
    c = client.post("/api/v1/playlist-folders", json={"name": "C", "parentId": a["id"]}).json()
    # reorder: B first
    assert client.patch(f"/api/v1/playlist-folders/{b['id']}", json={"index": 0}).status_code == 200
    assert [n for n, _ in _names(client)] == ["B", "A", "C"]
    # no cycles: A can't go into its own child
    assert client.patch(f"/api/v1/playlist-folders/{a['id']}", json={"parentId": c["id"]}).status_code == 422
    assert client.patch(f"/api/v1/playlist-folders/{a['id']}", json={"parentId": a["id"]}).status_code == 422
    # move A (with C) into B, rename it
    r = client.patch(f"/api/v1/playlist-folders/{a['id']}", json={"parentId": b["id"], "name": "A2"})
    assert r.status_code == 200 and r.json()["parentId"] == b["id"] and r.json()["name"] == "A2"
    # renaming alone keeps the parent
    assert client.patch(f"/api/v1/playlist-folders/{a['id']}", json={"name": "A3"}).json()["parentId"] == b["id"]
    # back to the top level
    assert client.patch(f"/api/v1/playlist-folders/{a['id']}", json={"parentId": None}).json()["parentId"] is None
    client.patch(f"/api/v1/playlist-folders/{a['id']}", json={"parentId": b["id"]})
    p = client.post("/api/v1/playlists", json={"name": "P", "folderId": a["id"]}).json()
    # deleting A moves C and P up into B; the playlist survives
    assert client.delete(f"/api/v1/playlist-folders/{a['id']}").status_code == 204
    assert _names(client) == [("B", None), ("C", b["id"])]
    assert client.get(f"/api/v1/playlists/{p['id']}").json()["folderId"] == b["id"]
    # deleting a top-level folder puts its playlists at the top level
    client.delete(f"/api/v1/playlist-folders/{b['id']}")
    assert client.get(f"/api/v1/playlists/{p['id']}").json()["folderId"] is None
    assert _names(client) == [("C", None)]
    assert client.delete(f"/api/v1/playlist-folders/{b['id']}").status_code == 404


def test_depth_limit(client):
    parent = None
    for i in range(8):
        parent = client.post("/api/v1/playlist-folders", json={"name": f"L{i}", "parentId": parent}).json()["id"]
    assert client.post("/api/v1/playlist-folders", json={"name": "too deep", "parentId": parent}).status_code == 422
    assert client.post("/api/v1/playlist-folders", json={"name": " "}).status_code == 422


def test_deleting_playlist_unfiles_it(client, conn):
    f = client.post("/api/v1/playlist-folders", json={"name": "F"}).json()
    p = client.post("/api/v1/playlists", json={"name": "P", "folderId": f["id"]}).json()
    client.delete(f"/api/v1/playlists/{p['id']}")
    assert client.get("/api/v1/playlist-folders").json()[0]["playlistCount"] == 0
