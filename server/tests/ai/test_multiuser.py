"""v2: everything about listening is per user (docs/v2.md §3)."""
from __future__ import annotations

import inspect
from datetime import timedelta

import numpy as np
import pytest

from nasmusic import db
from nasmusic.ai import api
from nasmusic.ai.recommender import Recommender
from tests.ai.conftest import NOW, USER, build_library, mix_rows
from tests.ai.schema_v2 import add_user

A, B, C = USER, "bob", "carol"


def test_api_signatures_match_spec():
    sig = {name: list(inspect.signature(getattr(api, name)).parameters) for name in
           ("analyse_pending", "run_nightly", "run_nightly_for_user", "more_like", "explain",
            "run_weekly_discovery")}
    assert sig == {
        "analyse_pending": ["conn", "cfg", "limit"],
        "run_nightly": ["conn", "cfg"],
        "run_nightly_for_user": ["conn", "cfg", "user_id"],
        "more_like": ["conn", "cfg", "user_id", "track_id", "minutes"],
        "explain": ["conn", "cfg", "user_id", "track_id"],
        "run_weekly_discovery": ["conn", "cfg"],
    }


def _taste(synth, user, liked_cluster, n_days=40):
    rng = np.random.default_rng(hash(user) % 1000)
    for t in synth.by_cluster(liked_cluster)[:30]:
        for _ in range(2):
            synth.play(t, NOW - timedelta(days=int(rng.integers(5, n_days)), hours=int(rng.integers(0, 24))),
                       user=user)
    for t in synth.by_cluster((liked_cluster + 3) % 6)[:15]:
        synth.skip(t, NOW - timedelta(days=8), user=user)


def test_run_nightly_covers_active_users_only(conn, cfg, synth):
    add_user(conn, B)
    add_user(conn, "zed", disabled=True)
    _taste(synth, A, 0)
    _taste(synth, B, 2)
    out = api.run_nightly(conn, cfg)
    assert set(out) == {A, B}
    owners = dict(conn.execute("SELECT id, user_id FROM mixes"))
    for uid, ids in out.items():
        assert len(ids) == 4 and all(owners[m] == uid for m in ids)
    snaps = {r[0] for r in conn.execute("SELECT user_id FROM taste_snapshots")}
    assert snaps == {A, B}
    # each user's mixes lean to their own taste
    cluster = {t["id"]: t["cluster"] for t in synth.tracks}
    for uid, liked in ((A, 0), (B, 2)):
        daily = next(m for m in out[uid] if conn.execute("SELECT kind FROM mixes WHERE id=?", (m,)).fetchone()[0]
                     == "daily")
        tracks = [r[1] for r in mix_rows(conn, daily)]
        share = np.mean([cluster[t] == liked for t in tracks])
        assert share >= 0.4, (uid, share)
    # a second night deactivates only that user's mixes
    ids_a = api.run_nightly_for_user(conn, cfg, A)
    active = dict(conn.execute("SELECT id, user_id FROM mixes WHERE active = 1"))
    assert {m for m, u in active.items() if u == A} == set(ids_a)
    assert {m for m, u in active.items() if u == B} == set(out[B])
    assert api.run_nightly_for_user(conn, cfg, "nobody") == []


def test_established_user_is_not_influenced_by_others(conn, cfg, synth):
    add_user(conn, B)
    _taste(synth, B, 2)
    rec = Recommender(conn, cfg, B)
    assert not rec.is_new_user
    before = rec.scores()
    rec.train(save=True)
    # another user does lots of things, including training and saving their own snapshot
    _taste(synth, A, 4)
    for t in synth.by_cluster(2)[:20]:
        synth.event(t["id"], "thumb_down", NOW - timedelta(days=1))
        synth.user_track(t["id"], starred_at=None, blocked=1)
    Recommender(conn, cfg, A).train(save=True)
    after_rec = Recommender(conn, cfg, B)
    assert after_rec.load_or_train().notes["plays"] == rec.model.notes["plays"]   # B's own snapshot
    assert np.allclose(Recommender(conn, cfg, B).scores(), before)


def _scores_for_new_user(tmp_path, cfg, scenario):
    conn = db.connect(tmp_path / f"{scenario}.db")
    from tests.ai.schema_v2 import init_v2
    init_v2(conn)
    for u in (A, C):
        add_user(conn, u)
    synth = build_library(conn, cfg)
    pop_tracks = synth.by_cluster(1)[:10]
    if scenario in ("one", "two"):
        for k, t in enumerate(pop_tracks):  # same per-track play counts in both scenarios
            for j in range(3):
                when = NOW - timedelta(days=(3 + k + j) if scenario == "one" else (40 - k - j), hours=j)
                synth.play(t, when, user=A)
    if scenario == "two":  # ...but different everything else
        for t in synth.by_cluster(5)[:20]:
            synth.skip(t, NOW - timedelta(days=2), user=A)
            synth.event(t["id"], "thumb_down", NOW - timedelta(days=2), user=A)
            synth.user_track(t["id"], user=A, blocked=1, starred_at="2026-01-01T00:00:00Z", rating=1)
    if scenario == "other":
        for t in synth.by_cluster(4)[:10]:
            for j in range(3):
                synth.play(t, NOW - timedelta(days=3 + j), user=A)
    rec = Recommender(conn, cfg, C)
    assert rec.is_new_user
    s = rec.scores()
    conn.close()
    return s, synth


def test_new_user_only_sees_the_anonymous_popularity_prior(tmp_path, cfg):
    one, synth = _scores_for_new_user(tmp_path, cfg, "one")
    two, _ = _scores_for_new_user(tmp_path, cfg, "two")
    other, _ = _scores_for_new_user(tmp_path, cfg, "other")
    nobody, _ = _scores_for_new_user(tmp_path, cfg, "nobody")
    # identical per-track play counts -> identical scores, whatever else the other user did
    assert np.allclose(one, two)
    # the prior is actually used: different popularity changes the scores, and popular tracks rise
    assert not np.allclose(one, other) and not np.allclose(one, nobody)
    lib_ids = [t["id"] for t in synth.tracks]
    popular = [lib_ids.index(t["id"]) for t in synth.by_cluster(1)[:10]]
    rest = [i for i in range(len(lib_ids)) if synth.tracks[i]["cluster"] == 1 and i not in popular]
    assert one[popular].mean() > one[rest].mean()


def test_new_user_cold_start_then_own_centres(conn, cfg, synth):
    add_user(conn, C)
    for t in synth.by_cluster(1)[:8]:  # someone else's plays: anonymous popularity
        for d in range(4):
            synth.play(t, NOW - timedelta(days=5 + d))
    ids = api.run_nightly_for_user(conn, cfg, C)
    assert len(ids) == 4
    rec = Recommender(conn, cfg, C)
    assert rec.is_new_user and rec.load_or_train().mode == "new_user"
    daily = ids[0]
    rows = mix_rows(conn, daily)
    cluster = {t["id"]: t["cluster"] for t in synth.tracks}
    # sound diversity: a brand-new user's Daily Mix spans the library
    assert len({cluster[r[1]] for r in rows}) >= 5
    assert any(r[3] == "Popular with listeners on this server" for r in rows)
    # ~20 plays later the user's own centres take over
    for k, t in enumerate(synth.by_cluster(3)[:20]):
        synth.play(t, NOW - timedelta(days=10, minutes=k), user=C)
    rec = Recommender(conn, cfg, C)
    assert not rec.is_new_user
    m = rec.train(save=True)
    assert m.mode == "cold" and m.notes["plays"] == 20
    assert not rec.popularity.any()  # no cross-user data once established


def test_more_like_and_explain_are_per_user(conn, cfg, synth):
    add_user(conn, B)
    seed = synth.by_cluster(2)[0]["id"]
    a_mix = api.more_like(conn, cfg, A, seed)
    b_mixes = [api.more_like(conn, cfg, B, synth.by_cluster(c)[0]["id"]) for c in range(4)]
    owner = dict(conn.execute("SELECT id, user_id FROM mixes"))
    assert owner[a_mix] == A and all(owner[m] == B for m in b_mixes)
    active = {r[0] for r in conn.execute("SELECT id FROM mixes WHERE active = 1")}
    assert a_mix in active                 # B's more_like cap doesn't touch A's mixes
    assert active & set(b_mixes) == set(b_mixes[-3:])
    # explain reads only the asking user's mixes
    track = mix_rows(conn, a_mix)[2][1]
    reason_a, _ = api.explain(conn, cfg, A, track)
    assert reason_a.startswith("Sounds like")
    with pytest.raises(KeyError):
        api.more_like(conn, cfg, "nobody", seed)


def test_transitional_v1_database(tmp_path, cfg, monkeypatch):
    """Until server core's migration lands, a v1 database works single-user with the v1 call shapes."""
    from pathlib import Path
    conn = db.connect(tmp_path / "v1.db")
    conn.executescript((Path(__file__).parent / "schema_v1.sql").read_text())
    synth = build_library(conn, cfg)
    t0 = synth.by_cluster(0)[0]
    conn.execute("UPDATE tracks SET starred_at = '2026-01-01T00:00:00Z' WHERE id = ?", (t0["id"],))
    for k, t in enumerate(synth.by_cluster(0)[:25]):
        conn.execute("INSERT INTO events (id, type, track_id, at, position_s, duration_s, received_at) "
                     "VALUES (?, 'play', ?, ?, ?, ?, ?)", (f"e{k}", t["id"], f"2026-10-0{1 + k % 9}T10:00:00Z",
                                                          t["duration"], t["duration"], "2026-10-10T00:00:00Z"))
    ids = api.run_nightly(conn, cfg)
    assert isinstance(ids, list) and len(ids) == 4
    mid = api.more_like(conn, cfg, synth.by_cluster(3)[0]["id"], 30)       # v1 shape
    assert conn.execute("SELECT kind FROM mixes WHERE id = ?", (mid,)).fetchone()[0] == "more_like"
    reason, similar = api.explain(conn, cfg, mix_rows(conn, mid)[1][1])     # v1 shape
    assert reason.startswith("Sounds like") and len(similar) == 3
    from nasmusic.ai import discovery
    monkeypatch.setattr(discovery, "run", lambda *a, **k: 3)
    assert api.run_weekly_discovery(conn, cfg) == 3
