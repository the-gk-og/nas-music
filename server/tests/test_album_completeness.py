"""Album completeness ("9 of 12") from MusicBrainz, and adding the gaps to To-get."""
from __future__ import annotations

import httpx
import pytest

from homebeat.library import completeness, musicbrainz

RG_SECOND = "33333333-3333-3333-3333-333333333333"   # "Second" is tagged with this release group (conftest)
RG_FIRST = "44444444-4444-4444-4444-444444444444"
REC_OPENING = "11111111-1111-1111-1111-111111111111"


def _rel(rid, title, tracks, status="Official", fmt="CD", extra_media=()):
    return {"id": rid, "title": title, "status": status, "media": [
        {"position": 1, "format": fmt, "tracks": [
            {"position": i + 1, "title": t, "length": 200000, "recording": {"id": f"rec-{rid}-{i + 1}"}}
            for i, t in enumerate(tracks)]},
        *extra_media]}


@pytest.fixture(autouse=True)
def fast_mb(monkeypatch):
    monkeypatch.setattr(musicbrainz.time, "sleep", lambda s: None)
    monkeypatch.setattr(musicbrainz, "_last", 0.0)


@pytest.fixture
def mb(app):
    calls: list[str] = []
    second = _rel("rel-std", "Second", ["Opening", "Middle", "Closing"])
    second["media"][0]["tracks"][0]["recording"]["id"] = REC_OPENING
    deluxe = _rel("rel-dlx", "Second", ["Opening", "Middle", "Closing", "Bonus 1", "Bonus 2"])
    dvd = {"position": 2, "format": "DVD-Video", "tracks": [{"position": 1, "title": "Live video", "recording": {}}]}
    deluxe["media"].append(dvd)
    first = _rel("rel-first", "First Album", ["Song 1", "Song 2"])

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path + "?" + request.url.query.decode())
        p = request.url.params
        if request.url.path.endswith("/release-group"):
            assert "First Album" in p["query"]
            return httpx.Response(200, json={"release-groups": [
                {"id": RG_FIRST, "title": "First Album", "score": 100}]})
        if request.url.path.endswith("/release"):
            rg = p["release-group"]
            rels = {RG_SECOND: [deluxe, second], RG_FIRST: [first]}[rg]
            return httpx.Response(200, json={"releases": rels})
        return httpx.Response(404)

    app.state.ctx.http_transport = httpx.MockTransport(handler)
    return calls


def _album_id(client, title):
    return next(a["id"] for a in client.get("/api/v1/library/albums").json() if a["title"] == title)


def test_incomplete_album_picks_best_release_and_lists_gaps(client, mb):
    aid = _album_id(client, "Second")
    r = client.get(f"/api/v1/library/albums/{aid}/completeness")
    assert r.status_code == 200
    body = r.json()
    # the standard edition (3 tracks) beats the deluxe (5 + a DVD) for a 2-track library album
    assert body["status"] == "incomplete" and body["have"] == 2 and body["total"] == 3
    assert body["releaseMbid"] == "rel-std" and body["releaseGroupMbid"] == RG_SECOND
    assert [m["title"] for m in body["missing"]] == ["Middle"]
    assert body["missing"][0]["toget"] is None and body["missing"][0]["position"] == 2
    # cached: a second look asks MusicBrainz nothing
    n = len(mb)
    assert client.get(f"/api/v1/library/albums/{aid}/completeness").json()["total"] == 3
    assert len(mb) == n


def test_untagged_album_found_by_search(client, mb):
    aid = _album_id(client, "First Album")
    body = client.get(f"/api/v1/library/albums/{aid}/completeness").json()
    assert body["status"] == "complete" and body["have"] == body["total"] == 2 and body["missing"] == []
    assert body["releaseGroupMbid"] == RG_FIRST


def test_add_missing_to_toget(client, mb):
    aid = _album_id(client, "Second")
    r = client.post(f"/api/v1/library/albums/{aid}/completeness/toget", json={})
    assert r.status_code == 200
    m = r.json()["missing"][0]
    assert m["toget"]["status"] == "want"
    items = client.get("/api/v1/toget").json()
    assert [(i["type"], i["title"], i["artist"]) for i in items] == [("track", "Middle", "The Band")]
    assert items[0]["note"] == "Missing from Second (track 2)"
    # again: no duplicate
    client.post(f"/api/v1/library/albums/{aid}/completeness/toget", json={"recordingMbids": [m["recordingMbid"]]})
    assert len(client.get("/api/v1/toget").json()) == 1


def test_musicbrainz_down_is_503_and_not_cached(client, app):
    def down(request):
        raise httpx.ConnectError("offline")

    app.state.ctx.http_transport = httpx.MockTransport(down)
    aid = _album_id(client, "Second")
    assert client.get(f"/api/v1/library/albums/{aid}/completeness").status_code == 503


def test_turned_off(client, app):
    app.state.ctx.cfg.album_completeness_enabled = False
    aid = _album_id(client, "Second")
    assert client.get(f"/api/v1/library/albums/{aid}/completeness").status_code == 409


def test_match_falls_back_to_position():
    rel = _rel("r", "X", ["Intro", "Song (Radio Edit)", "Outro"])
    tracks = [{"id": "a", "title": "Intro", "trackNo": 1}, {"id": "b", "title": "Totally Mistagged", "trackNo": 2}]
    slots, matched = completeness.match(completeness._slim(rel), tracks)
    assert matched == 2 and [s["have"] for s in slots] == ["a", "b", None]
