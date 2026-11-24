"""v8: resume where you left off, podcasts in Connect, and Continue listening."""
import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from nasmusic.cast import Media, SpokenMedia
from nasmusic.cast.session import CastHooks, SpeakerSession
from tests.conftest import track_ids
from tests.test_cast import FakeSpeaker
from tests.test_connect import URL, hello, token, until
from tests.test_spoken import (  # noqa: F401 - fixtures
    FEED_URL, RSS, alice, books_root, sapp, sconn, up,
)

API = "/api/v1"


@pytest.fixture
def tc(app):
    app.state.ctx.hub.persist_every_s = 0.05
    with TestClient(app) as c:
        yield c


# ---------- Connect: resume a queue where it stopped ----------


def test_play_queue_without_index_resumes_where_it_stopped(tc, conn):
    ids = list(track_ids(conn).values())
    _, tok = token(conn)
    _, wtok = token(conn, kind="watch", name="watch")
    h = {"Authorization": f"Bearer {tok}"}
    q = tc.post(f"{API}/queues", json={"name": "Album", "trackIds": ids[:4]}, headers=h).json()
    tc.patch(f"{API}/queues/{q['id']}", json={"currentIndex": 2, "positionMs": 61_000}, headers=h)
    with tc.websocket_connect(URL, headers=h) as phone, \
         tc.websocket_connect(URL, headers={"Authorization": f"Bearer {wtok}"}) as watch:
        hello(phone)
        hello(watch, "Watch", "watch", can_play=False)
        # No active device: the phone plays it itself; the takeover starts at the saved track AND position.
        phone.send_json({"type": "command", "action": "playQueue", "args": {"queueId": q["id"]}})
        st = until(phone, "takeover")["state"]
        assert (st["index"], st["trackId"], st["positionMs"], st["media"]) == (2, ids[2], 61_000, None)
        # A remote asking for the same queue without an index: the command carries the resume point.
        watch.send_json({"type": "command", "action": "playQueue", "args": {"queueId": q["id"]}})
        cmd = until(phone, "command")
        assert cmd["args"] == {"queueId": q["id"], "index": 2, "positionMs": 61_000}
        # An explicit index still starts that track from the top.
        watch.send_json({"type": "command", "action": "playQueue", "args": {"queueId": q["id"], "index": 0}})
        assert until(phone, "command")["args"] == {"queueId": q["id"], "index": 0}


def test_state_persists_queue_position_and_updated_at(tc, conn):
    ids = list(track_ids(conn).values())
    _, tok = token(conn)
    h = {"Authorization": f"Bearer {tok}"}
    q = tc.post(f"{API}/queues", json={"name": "Q", "trackIds": ids[:3]}, headers=h).json()
    conn.execute("UPDATE queues SET updated_at = '2020-01-01T00:00:00Z' WHERE id = ?", (q["id"],))
    with tc.websocket_connect(URL, headers=h) as ws:
        hello(ws)
        ws.send_json({"type": "transfer", "play": False})
        until(ws, "takeover")
        ws.send_json({"type": "state", "state": {"queueId": q["id"], "index": 1, "trackId": ids[1],
                                                 "positionMs": 42_000, "playing": False}})
        until(ws, "state")
    from tests.test_cast import wait_for

    def row():
        return conn.execute("SELECT current_index, position_ms, updated_at FROM queues WHERE id = ?",
                            (q["id"],)).fetchone()

    wait_for(lambda: (row()[0], row()[1]) == (1, 42_000))
    assert row()[2] > "2026"


# ---------- Connect: podcasts / audiobooks / stations ----------


def test_play_spoken_and_media_state(sapp, sconn, alice, up):  # noqa: F811
    up.routes[FEED_URL] = httpx.Response(200, content=RSS)
    alice.post(f"{API}/podcasts", json={"feedUrl": FEED_URL})
    eid = sconn.execute("SELECT id FROM podcast_episodes WHERE guid = 'ep-2'").fetchone()[0]
    sapp.state.ctx.hub.persist_every_s = 0.05
    h = {"Authorization": f"Bearer {alice.token}"}
    from nasmusic import auth

    _, wtok = auth.create_device(sconn, alice.user_id, "watch", "watch")
    with TestClient(sapp) as tc, tc.websocket_connect(URL, headers=h) as phone, \
         tc.websocket_connect(URL, headers={"Authorization": f"Bearer {wtok}"}) as watch:
        hello(phone)
        hello(watch, "Watch", "watch", can_play=False)
        watch.send_json({"type": "command", "action": "playSpoken", "args": {"kind": "nope", "id": "x"}})
        assert "playSpoken" in until(watch, "error")["detail"]
        phone.send_json({"type": "command", "action": "playSpoken",
                         "args": {"kind": "episode", "id": eid, "positionMs": 90_000}})
        st = until(phone, "takeover")["state"]
        assert st["media"] == {"kind": "episode", "id": eid} and st["queueId"] is None and st["positionMs"] == 90_000
        # The player reports the episode; remotes see it and the server keeps the resume point.
        phone.send_json({"type": "state", "state": {"media": {"kind": "episode", "id": eid, "junk": 1},
                                                    "queueId": None, "trackId": None, "positionMs": 125_000,
                                                    "playing": False}})
        got = until(watch, "state")["state"]
        while got["positionMs"] != 125_000:
            got = until(watch, "state")["state"]
        assert got["media"] == {"kind": "episode", "id": eid}
        # The hub broadcasts the state first and saves it afterwards in a worker thread: wait for the save.
        deadline = time.monotonic() + 5
        pr = alice.get(f"{API}/podcasts/episodes/{eid}").json()["progress"]
        while pr["positionS"] != 125.0 and time.monotonic() < deadline:
            time.sleep(0.05)
            pr = alice.get(f"{API}/podcasts/episodes/{eid}").json()["progress"]
        assert pr["positionS"] == 125.0
        # An old client that reports a track (no `media` field) is playing music again.
        phone.send_json({"type": "state", "state": {"queueId": None, "trackId": "t", "positionMs": 0}})
        got = until(watch, "state")["state"]
        assert got["media"] is None


def _media(tid: str) -> Media:
    return Media(track_id=tid, title=tid, artist="", album="", duration_s=100, url_for=lambda f: "http://x/" + tid)


def test_speaker_plays_a_book_across_parts():
    parts = [(0.0, 100.0), (100.0, 250.0), (250.0, None)]
    calls: list = []

    def spoken(uid, kind, ident, pos):
        calls.append((kind, ident, pos))
        i = max(j for j, (s, _) in enumerate(parts) if s <= pos)
        return SpokenMedia(_media(f"book:{ident}:{i}"), start_s=parts[i][0], end_s=parts[i][1])

    reports: list[dict] = []

    async def report(uid, did, partial):
        reports.append(partial)
        return True

    async def noop(*a):
        return None

    hooks = CastHooks(media=lambda t: None, queue_track=lambda *a: None, queue_len=lambda *a: 0, report=report,
                      lost=noop, error=noop, spoken=spoken)

    async def run():
        spk = FakeSpeaker()
        s = SpeakerSession(spk, hooks, poll_s=10)
        await s.takeover("u", {"media": {"kind": "book", "id": "b1"}, "positionMs": 120_000, "playing": True})
        assert spk.calls[-1] == ("load", "book:b1:1", 20.0, True)
        assert reports[-1]["media"] == {"kind": "book", "id": "b1"} and reports[-1]["positionMs"] == 120_000
        # A seek inside this part seeks the speaker; one into another part loads that part.
        await s.command("u", "seek", {"positionMs": 150_000})
        assert spk.calls[-1] == ("seek", 50.0)
        await s.command("u", "seek", {"positionMs": 260_000})
        assert spk.calls[-1] == ("load", "book:b1:2", 10.0, True)
        # The speaker's own position is part-relative; reports are book-wide.
        spk.pos = 30.0
        await s._tick()
        assert s.position_s == 280.0
        # The end of a part goes on to the next part.
        await s.command("u", "seek", {"positionMs": 10_000})
        spk.state = "finished"
        await s._tick()
        assert spk.calls[-1] == ("load", "book:b1:1", 0.0, True)
        # Back to music: playQueue leaves the book.
        await s.command("u", "playQueue", {"queueId": "q", "index": 0})
        assert s.spoken is None

    asyncio.run(run())


# ---------- Continue listening ----------


def test_continue_mixes_queues_and_spoken_and_dismiss(sapp, sconn, alice, up):  # noqa: F811
    up.routes[FEED_URL] = httpx.Response(200, content=RSS)
    alice.post(f"{API}/podcasts", json={"feedUrl": FEED_URL})
    eid = sconn.execute("SELECT id FROM podcast_episodes WHERE guid = 'ep-2'").fetchone()[0]
    from nasmusic.library import scanner

    scanner.scan(sapp.state.ctx.cfg, sconn)
    ids = list(track_ids(sconn).values())
    q = alice.post(f"{API}/queues", json={"name": "Album: First", "trackIds": ids[:3]}).json()
    alice.patch(f"{API}/queues/{q['id']}", json={"currentIndex": 1, "positionMs": 5000})
    sconn.execute("UPDATE queues SET updated_at = '2026-11-20T09:00:00Z' WHERE id = ?", (q["id"],))
    alice.put(f"{API}/spoken/progress", json={"items": [
        {"kind": "episode", "id": eid, "positionS": 600, "durationS": 3723, "updatedAt": "2026-11-20T10:00:00Z"}]})
    items = alice.get(f"{API}/continue").json()["items"]
    assert [(i["type"], i["id"]) for i in items] == [("episode", eid), ("queue", q["id"])]
    ep = items[0]
    assert ep["progress"]["positionMs"] == 600_000 and ep["progress"]["fraction"] == pytest.approx(600 / 3723, 1e-3)
    assert ep["episode"]["podcastTitle"] and ep["artUrl"].startswith("art/podcast/")
    assert items[1]["progress"]["index"] == 1 and items[1]["progress"]["positionMs"] == 5000
    # Home's shelf is the same list.
    home = {s["kind"]: s for s in alice.get(f"{API}/home").json()["shelves"]}
    assert [i["id"] for i in home["continue_listening"]["items"]] == [eid, q["id"]]
    # Dismiss: gone until it's played again.
    assert alice.delete(f"{API}/continue/episode/{eid}").status_code == 204
    assert [i["id"] for i in alice.get(f"{API}/continue").json()["items"]] == [q["id"]]
    alice.put(f"{API}/spoken/progress", json={"items": [{"kind": "episode", "id": eid, "positionS": 700}]})
    assert [i["id"] for i in alice.get(f"{API}/continue").json()["items"]] == [eid, q["id"]]
    assert alice.delete(f"{API}/continue/queue/{q['id']}").status_code == 204
    assert [i["id"] for i in alice.get(f"{API}/continue").json()["items"]] == [eid]
    assert alice.delete(f"{API}/continue/nope/x").status_code == 422
