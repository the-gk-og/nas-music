"""API for discovery phase 2: /release-radar, /wrapped, /admin/wrapped, /blends (+ their For You shelves)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from tests.conftest import track_ids, user_client

API = "/api/v1"


def _play(c, tid, at, pos=None):
    r = c.post(f"{API}/events", json={"events": [{"id": str(uuid.uuid4()), "type": "play", "trackId": tid, "at": at,
                                                     "positionS": pos}]})
    assert r.status_code == 200, r.text


def _year():
    return datetime.now(UTC).year


# ---------- Wrapped ----------


def test_wrapped(client, bob, conn):
    ids = track_ids(conn)
    y = _year()
    _play(client, ids["Song 1"], f"{y}-01-05T10:00:00Z")
    _play(client, ids["Song 1"], f"{y}-01-06T10:00:00Z")
    _play(client, ids["Song 2"], f"{y}-01-06T11:00:00Z", pos=1.0)
    _play(bob, ids["Opening"], f"{y}-01-06T11:00:00Z")
    w = client.get(f"{API}/wrapped").json()
    assert w["year"] == y and w["totals"]["plays"] == 3 and w["totals"]["days"] == 2
    assert w["topTracks"][0]["track"]["id"] == ids["Song 1"] and w["topTracks"][0]["plays"] == 2
    assert w["topArtists"][0]["artist"]["name"] and "artUrl" in w["topArtists"][0]
    assert w["topAlbums"][0]["album"]["id"]
    assert w["firstPlay"]["track"]["id"] == ids["Song 1"]
    assert len(w["byHour"]) == 24 and len(w["byWeekday"]) == 7 and len(w["byMonth"]) == 12
    assert w["streaks"]["longest"] == 2
    assert client.get(f"{API}/wrapped", params={"year": 1999}).status_code == 422
    assert client.get(f"{API}/wrapped", params={"year": 2001}).json()["totals"]["plays"] == 0
    assert y in client.get(f"{API}/wrapped/years").json()
    # bob only sees his own year
    assert bob.get(f"{API}/wrapped").json()["totals"]["plays"] == 1
    # admin: server-wide aggregates, nobody's individual history
    s = client.get(f"{API}/admin/wrapped").json()
    assert s["totals"]["plays"] == 4 and s["listeners"] == 2 and s["users"] >= 2
    assert "firstPlay" not in s and "streaks" not in s
    assert bob.get(f"{API}/admin/wrapped").status_code == 403
    shelf = {x["kind"]: x for x in client.get(f"{API}/foryou").json()["shelves"]}["wrapped"]
    assert shelf["items"][0]["type"] == "wrapped" and shelf["items"][0]["wrapped"] == {"year": y, "final": False}


# ---------- Blends ----------


def test_blend_consent_flow(app, client, bob, conn):
    ids = track_ids(conn)
    y = _year()
    for t in ("Song 1", "Song 2"):
        _play(client, ids[t], f"{y}-02-01T10:00:00Z")
    for t in ("Song 2", "Opening"):
        _play(bob, ids[t], f"{y}-02-01T10:00:00Z")
    assert [c["username"] for c in client.get(f"{API}/blends/candidates").json()] == ["bob"]
    # unknown / self invites
    assert client.post(f"{API}/blends", json={"usernames": ["nobody"]}).status_code == 404
    assert client.post(f"{API}/blends", json={"usernames": ["alice"]}).status_code == 422
    r = client.post(f"{API}/blends", json={"usernames": ["Bob"]})
    assert r.status_code == 201, r.text
    b = r.json()
    bid = b["id"]
    assert b["name"] == "alice + bob" and b["myStatus"] == "joined" and b["trackCount"] == 0
    assert {m["username"]: m["status"] for m in b["members"]} == {"alice": "joined", "bob": "invited"}
    # bob's listening is not used before he says yes; he sees the invite but no tracks
    inv = bob.get(f"{API}/blends/{bid}").json()
    assert inv["myStatus"] == "invited" and inv["invitedBy"] == "alice" and inv["tracks"] == []
    assert bob.get(f"{API}/foryou").json()["shelves"]  # invite shows on For You
    sh = {x["kind"]: x for x in bob.get(f"{API}/foryou").json()["shelves"]}["blends"]
    assert sh["items"][0]["blend"]["status"] == "invited"
    assert bob.patch(f"{API}/blends/{bid}", json={"name": "x"}).status_code == 404   # only members rename
    # accept -> built right away, tracks attributed to members
    a = bob.post(f"{API}/blends/{bid}/accept").json()
    assert a["myStatus"] == "joined" and a["trackCount"] > 0 and a["matchPct"] is not None
    assert all(set(t["forUsers"]) <= {client.user_id, bob.user_id} for t in a["tracks"])
    assert {t["track"]["id"] for t in a["tracks"]} <= {ids["Song 1"], ids["Song 2"], ids["Opening"]}
    assert a["pairs"][0]["aName"] in ("alice", "bob")
    assert bob.post(f"{API}/blends/{bid}/accept").status_code == 404
    assert client.patch(f"{API}/blends/{bid}", json={"name": "Our mix"}).json()["name"] == "Our mix"
    # a third user can't see it
    with user_client(app, conn, "carol", admin=False) as carol:
        assert carol.get(f"{API}/blends/{bid}").status_code == 404
        # carol opts out of invites
        carol.put(f"{API}/me/settings", json={"settings": {"blends.allowInvites": False}})
        assert client.post(f"{API}/blends/{bid}/invite", json={"usernames": ["carol"]}).status_code == 403
        assert "carol" not in [c["username"] for c in client.get(f"{API}/blends/candidates").json()]
    # bob leaves -> his tracks and attribution go immediately
    assert bob.post(f"{API}/blends/{bid}/leave").status_code == 204
    after = client.get(f"{API}/blends/{bid}").json()
    assert after["trackCount"] == 0 and after["matchPct"] is None
    assert [m["username"] for m in after["members"]] == ["alice"]
    assert bob.get(f"{API}/blends/{bid}").status_code == 404
    # the last member leaving deletes it
    assert client.post(f"{API}/blends/{bid}/leave").status_code == 204
    assert client.get(f"{API}/blends").json() == []


def test_blend_decline_and_reinvite(client, bob):
    bid = client.post(f"{API}/blends", json={"usernames": ["bob"], "name": "Mix"}).json()["id"]
    assert bob.post(f"{API}/blends/{bid}/decline").status_code == 204
    assert bob.get(f"{API}/blends").json() == []
    assert client.get(f"{API}/blends/{bid}").json()["members"] == [
        {"userId": client.user_id, "username": "alice", "status": "joined",
         "joinedAt": client.get(f"{API}/blends/{bid}").json()["members"][0]["joinedAt"]}]
    # asking again needs a fresh yes
    r = client.post(f"{API}/blends/{bid}/invite", json={"usernames": ["bob"]}).json()
    assert {m["username"]: m["status"] for m in r["members"]}["bob"] == "invited"
    assert bob.get(f"{API}/blends").json()[0]["myStatus"] == "invited"


def test_blend_shelf_offers_start(client, bob):
    sh = {x["kind"]: x for x in client.get(f"{API}/foryou").json()["shelves"]}["blends"]
    assert [i["id"] for i in sh["items"]] == ["new"] and sh["items"][0]["blend"]["status"] == "new"


# ---------- Release Radar ----------


def test_release_radar(client, conn):
    ids = track_ids(conn)
    uid = client.user_id
    y = _year()
    _play(client, ids["Song 1"], f"{y}-11-01T10:00:00Z")
    row = conn.execute("SELECT ar.name, al.title, al.id FROM tracks t JOIN albums al ON al.id = t.album_id "
                       "JOIN artists ar ON ar.id = t.artist_id WHERE t.id = ?", (ids["Song 1"],)).fetchone()
    artist, owned_title, owned_id = row
    conn.executemany(
        "INSERT INTO release_radar (user_id, release_group_mbid, title, artist, primary_type, release_date, cover_url, "
        "score, created_at) VALUES (?, ?, ?, ?, 'Album', ?, ?, 1, '2026-11-13T04:00:00Z')",
        [(uid, "rg-new", "Brand New", artist, "2026-11-13", "https://coverartarchive.org/release-group/rg-new/front-250"),
         (uid, "rg-own", owned_title, artist, "2026-11-06", None)])
    r = client.get(f"{API}/release-radar").json()
    assert r["updatedAt"] == "2026-11-13T04:00:00Z"
    assert [i["id"] for i in r["notOwned"]] == ["mb:rg:rg-new"]
    new = r["notOwned"][0]
    assert new["type"] == "catalog" and new["catalog"]["artUrl"] == "catalog/mb:rg:rg-new/cover"
    assert new["release"] == {"date": "2026-11-13", "type": "Album", "upcoming": False, "artist": artist}
    assert r["inLibrary"][0]["type"] == "album" and r["inLibrary"][0]["id"] == owned_id
    # add to To get through the existing endpoint
    tg = client.post(f"{API}/toget", json={"catalogId": "mb:rg:rg-new"}).json()
    assert tg["title"] == "Brand New" and tg["mbid"] == "rg-new"
    assert client.get(f"{API}/release-radar").json()["notOwned"][0]["catalog"]["toGet"]["id"] == tg["id"]
    shelves = [x for x in client.get(f"{API}/foryou").json()["shelves"] if x["kind"] == "release_radar"]
    assert [s["id"] for s in shelves] == ["release_radar", "release_radar:library"]


def test_release_radar_job_registered(client):
    names = [j["name"] for j in client.get(f"{API}/admin/jobs").json()]
    assert "release_radar" in names and "blends" in names
