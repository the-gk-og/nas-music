"""Discovery phase 2 maths: Wrapped stats, Blend match / track picking, Release Radar parsing + job."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from nasmusic import db
from nasmusic.ai import api, blend, release_radar, wrapped
from nasmusic.ai.discovery import Http
from tests.ai.conftest import build_library
from tests.ai.schema_v2 import add_user


@pytest.fixture
def conn5(tmp_path):
    c = db.connect(tmp_path / "v5.db")
    db.init(c, backup=False)
    for u in ("u1", "u2", "u3"):
        add_user(c, u)
    yield c
    c.close()


@pytest.fixture
def lib5(conn5, cfg):
    return build_library(conn5, cfg, n_clusters=4, artists_per_cluster=4, tracks_per_artist=4)


def at(y, m, d, h=12, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC)


# ---------------------------------------------------------------- Wrapped: pure parts

def test_listened_seconds():
    assert wrapped.listened_seconds("play", None, 200) == 200          # no position: whole track
    assert wrapped.listened_seconds("play", 150, 200) == 150
    assert wrapped.listened_seconds("play", 999, 200) == 200          # clipped to the track
    assert wrapped.listened_seconds("skip", 30, 200) == 30
    assert wrapped.listened_seconds("skip", None, 200) == 0
    assert wrapped.listened_seconds("star", 30, 200) == 0


def test_streaks():
    d = date(2026, 3, 1)
    days = [d, d + timedelta(1), d + timedelta(2), d + timedelta(5), d + timedelta(6)]
    s = wrapped.streaks(days, today=d + timedelta(7))           # today not yet played: count from yesterday
    assert s == {"longest": 3, "longestFrom": "2026-03-01", "longestTo": "2026-03-03", "current": 2}
    assert wrapped.streaks(days, today=d + timedelta(9))["current"] == 0
    assert wrapped.streaks(days, today=None)["current"] == 0      # finished year
    assert wrapped.streaks([], today=d) == {"longest": 0, "longestFrom": None, "longestTo": None, "current": 0}
    # ties keep the first longest run
    assert wrapped.streaks([d, d + timedelta(1), d + timedelta(3), d + timedelta(4)])["longestFrom"] == "2026-03-01"


def test_split_genres():
    assert wrapped.split_genres("Rock; Pop/Indie, Folk") == ["Rock", "Pop", "Indie", "Folk"]
    assert wrapped.split_genres(None) == []


def test_aura_quadrants_and_colours():
    hi_happy = wrapped.aura({"energy": 0.8, "happy": 0.7, "sad": 0.1, "danceability": 0.8})
    lo_sad = wrapped.aura({"energy": 0.3, "happy": 0.1, "sad": 0.6, "danceability": 0.2})
    assert hi_happy["label"] == "Electric" and lo_sad["label"] == "Midnight"
    assert wrapped.aura({"energy": 0.5, "happy": 0.3, "sad": 0.3})["label"] == "Balanced"
    assert wrapped.aura({"energy": 0.3, "danceability": 0.7})["label"] == "Sunlit"   # no mood heads
    assert wrapped.aura({}) is None
    for a in (hi_happy, lo_sad):
        assert len(a["colors"]) == 2 and all(c.startswith("#") and len(c) == 7 for c in a["colors"])
    assert hi_happy["colors"] != lo_sad["colors"]
    assert hi_happy["moods"][0]["name"] == "happy"


# ---------------------------------------------------------------- Wrapped: from events

def test_user_wrapped_stats(conn5, lib5):
    lib5.user = "u1"
    t = lib5.tracks
    # last year: t0 once (discovery check), this year below
    lib5.event(t[0]["id"], "play", at(2025, 6, 1), t[0]["duration"], t[0]["duration"])
    lib5.event(t[0]["id"], "play", at(2026, 1, 1, 0, 30), None, None)          # 00:30 UTC = 00:30 London (GMT)
    lib5.event(t[0]["id"], "play", at(2026, 1, 2, 8), t[0]["duration"], t[0]["duration"])
    lib5.event(t[1]["id"], "play", at(2026, 1, 3, 20), 60, t[1]["duration"])   # partial play: 60 s
    lib5.event(t[20]["id"], "skip", at(2026, 1, 3, 21), 30, t[20]["duration"])
    lib5.event(t[0]["id"], "play", at(2026, 7, 1, 11), None, None)            # BST: 12:00 local
    lib5.event(t[0]["id"], "star", at(2026, 7, 1, 12))                         # not counted
    lib5.event(t[2]["id"], "play", at(2027, 1, 1, 12), None, None, user=None)  # next year: excluded
    w = wrapped.user_wrapped(conn5, "u1", 2026, "Europe/London", now=at(2026, 11, 19))
    d0, d1 = t[0]["duration"], t[1]["duration"]
    expect_s = d0 * 3 + 60 + 30
    assert w["totals"]["plays"] == 4 and w["totals"]["skips"] == 1
    assert w["totals"]["minutes"] == round(expect_s / 60)
    assert w["totals"]["tracks"] == 2 and w["totals"]["days"] == 4
    assert w["topTracks"][0] == {"id": t[0]["id"], "plays": 3, "minutes": round(d0 * 3 / 60, 1)}
    assert w["topArtists"][0]["id"] == t[0]["artist_id"]
    assert abs(sum(w["byHour"]) - expect_s / 60) < 0.5
    assert w["byHour"][0] == round(d0 / 60, 1) and w["byHour"][12] == round(d0 / 60, 1)   # BST shift
    assert w["byMonth"][6] == round(d0 / 60, 1)
    assert w["byWeekday"][date(2026, 1, 1).weekday()] == round(d0 / 60, 1)
    assert w["streaks"]["longest"] == 3 and w["streaks"]["current"] == 0
    assert w["firstPlay"]["trackId"] == t[0]["id"]
    # t0's artist was played in 2025, so only t1's artist is new (t20's was only skipped)
    assert w["discoveries"]["artists"] == (0 if t[1]["artist_id"] == t[0]["artist_id"] else 1)
    assert w["discoveries"]["tracks"] == 1
    assert w["aura"] is not None and w["aura"]["label"]
    assert w["final"] is False and w["year"] == 2026
    assert w["topGenres"][0]["share"] <= 1
    assert w["topDay"]["minutes"] >= 1


def test_user_wrapped_is_per_user_and_empty_safe(conn5, lib5):
    lib5.event(lib5.tracks[0]["id"], "play", at(2026, 2, 1), None, None, user="u2")
    w = wrapped.user_wrapped(conn5, "u1", 2026, "UTC", now=at(2026, 3, 1))
    assert w["totals"]["plays"] == 0 and w["topTracks"] == [] and w["aura"] is None
    assert w["peakHour"] is None and w["topDay"] is None
    s = wrapped.server_wrapped(conn5, 2026, "UTC", now=at(2026, 3, 1))
    assert s["totals"]["plays"] == 1 and s["listeners"] == 1 and "streaks" not in s
    assert wrapped.years(conn5, "u2") == [2026] and wrapped.years(conn5, "u1") == []


def test_wrapped_final_year(conn5, lib5):
    lib5.event(lib5.tracks[0]["id"], "play", at(2025, 12, 31, 23), None, None)
    w = wrapped.user_wrapped(conn5, "u1", 2025, "UTC", now=at(2026, 5, 1))
    assert w["final"] is True and w["streaks"]["current"] == 0 and w["totals"]["plays"] == 1


# ---------------------------------------------------------------- Blend maths

def P(uid, tracks, artists=None, genres=None, blocked=()):
    return blend.Profile(uid, tracks=tracks, artists=artists or {}, genres=genres or {}, blocked=set(blocked))


def test_track_weights_and_cosine():
    w = blend.track_weights({"a": 10, "b": 1, "c": 0, "d": -2})
    assert w["a"] == 1.0 and 0 < w["b"] < 1 and "c" not in w and "d" not in w
    assert blend.track_weights({}) == {}
    assert blend.cosine({"x": 1}, {"x": 4}) == pytest.approx(1.0)
    assert blend.cosine({"x": 1}, {"y": 1}) == 0
    assert blend.cosine({}, {"y": 1}) is None


def test_match_pct_bounds_and_order():
    same = P("a", {}, {"ar1": 3, "ar2": 1}, {"rock": 1})
    twin = P("b", {}, {"ar1": 3, "ar2": 1}, {"rock": 1})
    near = P("c", {}, {"ar1": 1, "ar3": 3}, {"rock": 1, "jazz": 1})
    far = P("d", {}, {"ar9": 1}, {"techno": 1})
    assert blend.match_pct(blend.match_score(same, twin)) == 100
    assert blend.match_pct(blend.match_score(same, far)) == 0
    mid = blend.match_pct(blend.match_score(same, near))
    assert 0 < mid < 100
    assert blend.match_score(same, P("e", {})) is None and blend.match_pct(None) is None
    g = blend.group_match([same, twin, far])
    assert [p["pct"] for p in g["pairs"]] == [100, 0, 0] and g["pct"] == 33


def test_pick_tracks_fair_shared_first_attributed_and_blocked():
    artist_of = {f"a{i}": f"A{i}" for i in range(20)} | {f"b{i}": f"B{i}" for i in range(20)} | {"s": "S", "x": "X"}
    alex = P("alex", {**{f"a{i}": 1 - i / 40 for i in range(20)}, "s": 0.9, "x": 1.0},
             {f"A{i}": 1 for i in range(20)} | {"S": 0.9, "X": 1})
    sam = P("sam", {**{f"b{i}": 1 - i / 40 for i in range(20)}, "s": 0.8}, {f"B{i}": 1 for i in range(20)} | {"S": 0.8},
            blocked={"x"})
    items = blend.pick_tracks([alex, sam], artist_of, size=10)
    ids = [x["track_id"] for x in items]
    assert len(items) == 10 and len(set(ids)) == 10
    assert "x" not in ids                                   # sam's thumbs-down keeps it out for everyone
    shared = next(x for x in items if x["track_id"] == "s")
    assert shared["reason"] == "shared" and shared["for_users"] == ["alex", "sam"]
    mains = [x["for_users"][0] for x in items if x["reason"] != "shared"]
    assert abs(mains.count("alex") - mains.count("sam")) <= 1   # equal share
    assert all(x["for_users"][0] == "alex" for x in items if x["track_id"].startswith("a"))
    assert items == blend.pick_tracks([alex, sam], artist_of, size=10)   # deterministic


def test_pick_tracks_artist_cap_and_spread():
    artist_of = {f"t{i}": "SAME" for i in range(10)} | {f"o{i}": f"O{i}" for i in range(10)}
    a = P("a", {f"t{i}": 1 - i / 20 for i in range(10)}, {"SAME": 10})
    b = P("b", {f"o{i}": 1 - i / 20 for i in range(10)}, {f"O{i}": 1 for i in range(10)})
    items = blend.pick_tracks([a, b], artist_of, size=12)
    arts = [artist_of[x["track_id"]] for x in items]
    assert arts.count("SAME") == blend.MAX_PER_ARTIST
    assert all(arts[i] != arts[i + 1] for i in range(len(arts) - 1))


def test_pick_tracks_needs_two():
    assert blend.pick_tracks([P("a", {"t": 1})], {"t": "A"}) == []


def test_build_blend_end_to_end(conn5, lib5, cfg):
    now = datetime.now(UTC)
    for k, t in enumerate(lib5.by_cluster(0)[:8]):
        for j in range(3):
            lib5.play(t, now - timedelta(days=j + k), user="u1")
    for k, t in enumerate(lib5.by_cluster(1)[:8]):
        lib5.play(t, now - timedelta(days=k), user="u2")
    shared = lib5.by_cluster(2)[0]
    lib5.play(shared, now, user="u1")
    lib5.play(shared, now, user="u2")
    lib5.user_track(lib5.by_cluster(0)[0]["id"], user="u2", blocked=1)
    conn5.execute("INSERT INTO blends (id, name, created_by, created_at, updated_at) VALUES ('b1','B','u1','x','x')")
    for u, st in (("u1", "joined"), ("u2", "joined"), ("u3", "invited")):
        conn5.execute("INSERT INTO blend_members (blend_id, user_id, status, invited_at, joined_at) VALUES "
                      "('b1', ?, ?, 'x', ?)", (u, st, "x" if st == "joined" else None))
    n = api.build_blend(conn5, cfg, "b1")
    rows = conn5.execute("SELECT track_id, for_users, reason FROM blend_tracks WHERE blend_id='b1' ORDER BY position"
                         ).fetchall()
    assert n == len(rows) > 0
    users = {u for r in rows for u in json.loads(r[1])}
    assert users <= {"u1", "u2"}                                     # the invitee's taste is never used
    assert lib5.by_cluster(0)[0]["id"] not in [r[0] for r in rows]   # blocked by u2
    assert any(r[2] == "shared" for r in rows)
    m = json.loads(conn5.execute("SELECT match FROM blends WHERE id='b1'").fetchone()[0])
    assert 0 <= m["pct"] <= 100 and len(m["pairs"]) == 1
    # u2 leaves -> blend empties
    conn5.execute("UPDATE blend_members SET status='left' WHERE user_id='u2'")
    assert api.build_blend(conn5, cfg, "b1") == 0
    assert conn5.execute("SELECT match FROM blends WHERE id='b1'").fetchone()[0] is None
    with pytest.raises(KeyError):
        api.build_blend(conn5, cfg, "nope")


# ---------------------------------------------------------------- Release Radar

RG = {
    "count": 3,
    "release-groups": [
        {"id": "rg1", "title": "New LP", "primary-type": "Album", "first-release-date": "2026-11-13",
         "artist-credit": [{"name": "Artist 0-0", "artist": {"id": "mb-a00", "name": "Artist 0-0"}}]},
        {"id": "rg2", "title": "Live at X", "primary-type": "Album", "secondary-types": ["Live"],
         "first-release-date": "2026-11-01", "artist-credit": [{"name": "Artist 0-0", "artist": {"id": "mb-a00"}}]},
        {"id": "rg3", "title": "Duet", "primary-type": "Single", "first-release-date": "2026-12-05",
         "artist-credit": [{"name": "Artist 1-0", "joinphrase": " & ", "artist": {"id": "mb-a10"}},
                           {"name": "Someone", "artist": {"id": "mb-zz"}}]},
    ],
}


def test_parse_and_query():
    rgs = release_radar.parse_release_groups(RG)
    assert [r["mbid"] for r in rgs] == ["rg1", "rg3"]                      # live album filtered out
    assert rgs[1]["artist"] == "Artist 1-0 & Someone" and rgs[1]["artist_mbids"] == ["mb-a10", "mb-zz"]
    q = release_radar.build_query(["a", "b"], date(2026, 9, 1), date(2026, 12, 1))
    assert q.startswith("(arid:a OR arid:b) AND firstreleasedate:[2026-09-01 TO 2026-12-01]")
    assert "primarytype:ep" in q


def test_release_radar_job(conn5, lib5, cfg):
    now = datetime(2026, 11, 19, tzinfo=UTC)
    a00 = lib5.by_cluster(0)[0]
    a10 = lib5.by_cluster(1)[0]
    conn5.execute("UPDATE artists SET mbid = 'mb-a00' WHERE id = ?", (a00["artist_id"],))
    for k in range(5):
        lib5.play(a00, now - timedelta(days=k), user="u1")
    lib5.play(a10, now, user="u2")
    conn5.execute("UPDATE artists SET mbid = 'mb-a10' WHERE id = ?", (a10["artist_id"],))
    calls = []

    def handler(req: httpx.Request):
        calls.append(req.url)
        assert "nas-music" in req.headers["user-agent"] and "@" not in req.headers["user-agent"]
        return httpx.Response(200, json=RG)

    sleeps = []
    http = Http(httpx.Client(transport=httpx.MockTransport(handler)), sleep=sleeps.append)
    out = release_radar.run(conn5, cfg, http=http, now=now)
    assert out == {"u1": 1, "u2": 1, "u3": 0}
    assert len(calls) == 1                                   # both users' artists in one batched search
    q = calls[0].params["query"]
    assert "arid:mb-a00" in q and "arid:mb-a10" in q and "firstreleasedate:[2026-09-24 TO 2026-12-19]" in q
    r1 = release_radar.for_user(conn5, "u1", now=now)
    assert [r["mbid"] for r in r1["releases"]] == ["rg1"] and r1["releases"][0]["artistId"] == a00["artist_id"]
    r2 = release_radar.for_user(conn5, "u2", now=now)
    assert r2["releases"][0]["upcoming"] is True             # 2026-12-05 is after "today"
    # network down: last week's results stay
    down = Http(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))), sleep=sleeps.append)
    assert release_radar.run(conn5, cfg, http=down, now=now) == {}
    assert len(release_radar.for_user(conn5, "u1", now=now)["releases"]) == 1


def test_release_radar_rate_limit():
    t = [0.0]
    slept = []
    http = Http(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))),
                sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)), clock=lambda: t[0])
    for _ in range(3):
        http.get("https://musicbrainz.org/ws/2/release-group", {"query": "x"})
    assert len(slept) == 2 and all(s >= 1.0 for s in slept)


def test_recently_added_in_library(conn5, lib5):
    now = datetime(2026, 11, 19, tzinfo=UTC)
    t = lib5.by_cluster(3)[0]
    lib5.play(t, now - timedelta(days=1), user="u1")
    alb = conn5.execute("SELECT album_id FROM tracks WHERE id = ?", (t["id"],)).fetchone()[0]
    assert release_radar.for_user(conn5, "u1", now=now)["recentlyAdded"] == []   # added 100 days ago
    conn5.execute("UPDATE tracks SET added_at = ? WHERE album_id = ?", ("2026-11-10T00:00:00Z", alb))
    got = release_radar.for_user(conn5, "u1", now=now)["recentlyAdded"]
    assert [g["albumId"] for g in got] == [alb]
