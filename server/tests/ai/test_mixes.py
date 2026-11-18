from __future__ import annotations

from datetime import timedelta
from itertools import pairwise

import numpy as np
import pytest

from nasmusic.ai import api
from nasmusic.ai.common import ensure_default_settings
from nasmusic.ai.mixes import MixBuilder, energy_target
from nasmusic.ai.recommender import Recommender
from tests.ai.conftest import NOW, USER, build_library, mix_rows


def _history(synth):
    rng = np.random.default_rng(3)
    for t in synth.by_cluster(0) + synth.by_cluster(1)[:20]:
        for d in rng.integers(5, 60, size=2):
            synth.play(t, NOW - timedelta(days=int(d), hours=int(rng.integers(0, 24))))
    for t in synth.by_cluster(4)[:15]:
        synth.skip(t, NOW - timedelta(days=10))
    recent = synth.by_cluster(0)[:10] + synth.by_cluster(2)[:5]
    for t in recent:
        synth.play(t, NOW - timedelta(days=1))
    blocked = synth.by_cluster(0)[10:14]
    for t in blocked[:2]:
        synth.user_track(t["id"], blocked=1)
    for t in blocked[2:]:
        synth.event(t["id"], "thumb_down", NOW - timedelta(days=30))
    starred = synth.by_cluster(1)[:6]
    for t in starred:
        synth.user_track(t["id"], starred_at="2026-01-01T00:00:00Z")
    return {t["id"] for t in recent}, {t["id"] for t in blocked}


def _check_rules(conn, mix_id, recent, blocked, gap=5):
    rows = mix_rows(conn, mix_id)
    assert rows, "mix is empty"
    artists = [r[2] for r in rows]
    for k in range(len(artists)):
        assert artists[k] not in artists[max(0, k - gap):k], f"artist repeated within {gap} at {k}"
    ids = [r[1] for r in rows]
    assert len(ids) == len(set(ids))
    assert not set(ids) & recent, "track played in the last 3 days"
    assert not set(ids) & blocked, "blocked track"
    assert all(r[3] and len(r[3]) <= 100 for r in rows), "every track needs a short reason"
    return rows


def test_nightly_mixes_follow_rules(conn, cfg, synth):
    recent, blocked = _history(synth)
    ids = api.run_nightly_for_user(conn, cfg, USER)
    kinds = dict(conn.execute("SELECT id, kind FROM mixes WHERE active = 1").fetchall())
    assert sorted(kinds[i] for i in ids) == ["daily", "deep_cuts", "evening", "morning"]
    targets = {"daily": 240, "morning": 90, "evening": 90, "deep_cuts": 90}
    for mid in ids:
        rows = _check_rules(conn, mid, recent, blocked)
        minutes = sum(r[4] for r in rows) / 60
        assert targets[kinds[mid]] * 0.8 <= minutes <= targets[kinds[mid]] + 6, (kinds[mid], minutes)
    # daily: roughly 60 % known likes, the rest rarely played / exploration
    daily = next(i for i in ids if kinds[i] == "daily")
    reasons = [r[3] for r in mix_rows(conn, daily)]
    known = sum(r.startswith(("One of your favourites", "You've played", "You liked", "You rated", "You played",
                              "A favourite", "You often")) for r in reasons)
    assert 0.4 <= known / len(reasons) <= 0.75
    # deep cuts: low play count
    deep = next(i for i in ids if kinds[i] == "deep_cuts")
    plays = dict(conn.execute("SELECT track_id, COUNT(*) FROM events WHERE type='play' GROUP BY track_id"))
    assert all(plays.get(r[1], 0) <= 1 for r in mix_rows(conn, deep))
    # mixes don't overlap
    all_tracks = [r[1] for i in ids for r in mix_rows(conn, i)]
    assert len(all_tracks) == len(set(all_tracks))
    # settings were seeded with defaults
    assert conn.execute("SELECT value FROM settings WHERE key = 'ai.mix.daily.minutes'").fetchone()[0] == "240"
    # a second run deactivates yesterday's mixes
    ids2 = api.run_nightly_for_user(conn, cfg, USER)
    active = {r[0] for r in conn.execute("SELECT id FROM mixes WHERE active = 1")}
    assert active == set(ids2) and not active & set(ids)


def test_settings_change_mix_length(conn, cfg, synth):
    ensure_default_settings(conn)
    conn.execute("UPDATE settings SET value = '30' WHERE key = 'ai.mix.daily.minutes'")
    conn.execute("UPDATE settings SET value = '2' WHERE key = 'ai.mix.artist_gap'")
    ids = api.run_nightly_for_user(conn, cfg, USER)
    daily = conn.execute("SELECT id FROM mixes WHERE kind='daily' AND active=1").fetchone()[0]
    assert daily in ids
    minutes = sum(r[4] for r in mix_rows(conn, daily)) / 60
    assert 24 <= minutes <= 36


def test_order_is_smooth_and_follows_energy(conn, cfg, synth):
    rec = Recommender(conn, cfg, USER)
    b = MixBuilder(rec)
    rng = np.random.default_rng(0)
    idx = list(rng.choice(len(rec.lib), 40, replace=False))
    ordered = b.order(idx, "rise")
    e = rec.lib.energy[ordered]
    # rising curve: first third calmer than last third
    assert e[: len(e) // 3].mean() < e[-len(e) // 3:].mean()
    # nearest-neighbour walk: consecutive tracks closer than random pairs
    emb = rec.lib.emb
    walk = np.mean([emb[a] @ emb[b_] for a, b_ in pairwise(ordered)])
    rand = np.mean([emb[a] @ emb[b_] for a, b_ in pairwise(idx)])
    assert walk > rand
    assert energy_target("fall", 0) > energy_target("fall", 1)


def test_artist_rule_drops_unplaceable_tracks(conn, cfg):
    lib = build_library(conn, cfg, n_clusters=1, artists_per_cluster=2, tracks_per_artist=10)
    rec = Recommender(conn, cfg, USER)
    ordered = MixBuilder(rec).order(list(range(len(rec.lib))), "arc")
    artists = [rec.lib.artist[i] for i in ordered]
    for k in range(len(artists)):
        assert artists[k] not in artists[max(0, k - 5):k]
    assert len(ordered) <= 2 and lib


def test_more_like_and_explain(conn, cfg, synth):
    seed = synth.by_cluster(2)[5]
    mid = api.more_like(conn, cfg, USER, seed["id"], minutes=40)
    m = conn.execute("SELECT kind, seed_track, title, active FROM mixes WHERE id = ?", (mid,)).fetchone()
    assert m[0] == "more_like" and m[1] == seed["id"] and m[3] == 1 and m[2].startswith("More like")
    rows = _check_rules(conn, mid, set(), set())
    assert rows[0][1] == seed["id"]
    cluster = {t["id"]: t["cluster"] for t in synth.tracks}
    same = sum(cluster[r[1]] == 2 for r in rows) / len(rows)
    assert same >= 0.8
    assert 30 <= sum(r[4] for r in rows) / 60 <= 46
    reason, similar = api.explain(conn, cfg, USER, rows[3][1])
    assert reason.startswith("Sounds like")
    assert len(similar) == 3 and all(cluster[s] == 2 for s in similar)
    # a track in no mix still gets an explanation
    other = synth.by_cluster(5)[0]["id"]
    reason, similar = api.explain(conn, cfg, USER, other)
    assert reason and len(similar) == 3
    with pytest.raises(KeyError):
        api.more_like(conn, cfg, USER, "nope")


def test_empty_library_and_no_events(conn, cfg):
    assert api.run_nightly_for_user(conn, cfg, USER) == []
    assert api.analyse_pending(conn, cfg) == 0
    reason, similar = api.explain(conn, cfg, USER, "missing")
    assert reason == "Picked from your library." and similar == []


def test_library_without_events_still_gets_mixes(conn, cfg):
    build_library(conn, cfg, n_clusters=3, artists_per_cluster=6, tracks_per_artist=6)
    ids = api.run_nightly_for_user(conn, cfg, USER)
    assert len(ids) == 4
    for mid in ids:
        _check_rules(conn, mid, set(), set())


def test_nightly_never_raises(conn, cfg, synth, monkeypatch):
    from nasmusic.ai import recommender

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(recommender.Recommender, "train", boom)
    assert api.run_nightly_for_user(conn, cfg, USER) == []


def test_more_like_mixes_do_not_pile_up(conn, cfg, synth):
    from nasmusic.ai.common import iso
    seeds = [synth.by_cluster(c)[0]["id"] for c in range(5)]
    old = api.more_like(conn, cfg, USER, seeds[0])
    conn.execute("UPDATE mixes SET created_at = ? WHERE id = ?", (iso(NOW - timedelta(hours=25)), old))
    ids = [api.more_like(conn, cfg, USER, s) for s in seeds[1:]]
    active = [r[0] for r in conn.execute(
        "SELECT id FROM mixes WHERE kind = 'more_like' AND active = 1 ORDER BY created_at, rowid")]
    assert old not in active                       # older than 24 h
    assert active == ids[-3:]                      # at most 3, newest kept
    # the nightly run retires every on-demand mix
    api.run_nightly_for_user(conn, cfg, USER)
    assert conn.execute("SELECT COUNT(*) FROM mixes WHERE kind = 'more_like' AND active = 1").fetchone()[0] == 0
