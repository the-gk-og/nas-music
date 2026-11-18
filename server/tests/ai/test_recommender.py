from __future__ import annotations

import json
from datetime import timedelta

import numpy as np

from nasmusic.ai.common import ensure_default_settings
from nasmusic.ai.recommender import Recommender, TasteModel, event_weight
from tests.ai.conftest import NOW, USER


def test_event_weights():
    assert event_weight("play", 200, 200, 0) == 1.0          # full play: mild like
    assert event_weight("play", 120, 200, 0) == 0.5
    assert event_weight("skip", 60, 200, 0) == -0.6           # mild dislike
    assert event_weight("skip", 10, 200, 0) == -1.5           # early skip: strong
    assert event_weight("skip", 60, 200, 2) < event_weight("skip", 60, 200, 1) < -0.6  # repeated
    assert event_weight("skip", 5, 200, 10) == -3.0           # capped
    assert event_weight("thumb_up", None, None, 0) == 2.0


def test_star_rating_and_thumbs_down(conn, cfg, synth):
    t_star, t_rate, t_down = synth.tracks[0], synth.tracks[1], synth.tracks[2]
    synth.user_track(t_star["id"], starred_at="2026-01-01T00:00:00Z")
    synth.user_track(t_rate["id"], rating=1)
    synth.event(t_down["id"], "thumb_down", NOW - timedelta(days=1))
    rec = Recommender(conn, cfg, USER)
    i = rec.lib.index
    assert rec.sig.weight[i[t_star["id"]]] == 3.0
    assert rec.sig.weight[i[t_rate["id"]]] == -2.0
    assert rec.sig.blocked[i[t_down["id"]]]
    assert rec.scores()[i[t_down["id"]]] == -np.inf


def test_cold_start_multi_centroid(conn, cfg, synth):
    # likes in two very different clusters, early skips in a third
    for t in synth.by_cluster(0)[:12] + synth.by_cluster(3)[:12]:
        for d in range(3):
            synth.play(t, NOW - timedelta(days=20 + d))
    for t in synth.by_cluster(5)[:10]:
        synth.skip(t, NOW - timedelta(days=20))
    rec = Recommender(conn, cfg, USER)
    model = rec.train(save=True)
    assert model.mode == "cold"
    assert model.centroids.shape[0] >= 2
    assert model.negatives.shape[0] >= 1
    s = model.sound_score(rec.lib.emb)
    idx = lambda c: [rec.lib.index[t["id"]] for t in synth.by_cluster(c)[12:]]
    # both liked clusters beat the neutral one, which beats the disliked one
    assert s[idx(0)].mean() > s[idx(1)].mean() + 0.2
    assert s[idx(3)].mean() > s[idx(1)].mean() + 0.2
    assert s[idx(1)].mean() > s[idx(5)].mean()
    # snapshot round trip
    row = conn.execute("SELECT payload, notes FROM taste_snapshots").fetchone()
    m2 = TasteModel.from_payload(row[0])
    assert np.allclose(m2.sound_score(rec.lib.emb), s, atol=1e-5)
    assert json.loads(row[1])["mode"] == "cold"


def test_freshness(conn, cfg, synth):
    a, b = synth.by_cluster(2)[0], synth.by_cluster(2)[1]
    synth.play(a, NOW - timedelta(hours=5))
    rec = Recommender(conn, cfg, USER)
    rec.train(save=False)
    s = rec.scores()
    ia, ib = rec.lib.index[a["id"]], rec.lib.index[b["id"]]
    # a full play is a mild like, but having just been played outweighs it
    assert s[ia] < s[ib]


def _learnable_history(synth, n: int, rng: np.random.Generator):
    """Clusters 0-2 get played through, 3-5 get skipped; more so in the evening."""
    for k in range(n):
        t = synth.tracks[int(rng.integers(len(synth.tracks)))]
        at = NOW - timedelta(days=60) + timedelta(minutes=137 * k)
        liked = t["cluster"] < 3
        if rng.random() < (0.85 if liked else 0.15):
            synth.play(t, at)
        else:
            synth.skip(t, at, pos=float(rng.uniform(3, 60)))


def test_cold_start_to_learned_switch(conn, cfg, synth):
    ensure_default_settings(conn)
    rng = np.random.default_rng(5)
    _learnable_history(synth, 300, rng)
    rec = Recommender(conn, cfg, USER)
    m = rec.train(save=True)
    assert rec.sig.n_feedback == 300
    assert m.mode == "cold" and "waiting for feedback" in m.notes["learned"]

    _learnable_history(synth, 400, rng)  # now 700 >= 500
    rec = Recommender(conn, cfg, USER)
    m = rec.train(save=True)
    assert m.mode == "learned", m.notes
    assert m.notes["validation_auc"] >= 0.7
    p = rec.learned_prob(hour=20, weekday=2)
    liked = [rec.lib.index[t["id"]] for t in synth.tracks if t["cluster"] < 3]
    disliked = [rec.lib.index[t["id"]] for t in synth.tracks if t["cluster"] >= 3]
    assert p[liked].mean() > p[disliked].mean() + 0.3
    # learned model survives a snapshot round trip and is used by a fresh Recommender
    notes = [json.loads(r[0])["mode"] for r in conn.execute("SELECT notes FROM taste_snapshots ORDER BY created_at, rowid")]
    assert notes[-1] == "learned"
    rec2 = Recommender(conn, cfg, USER)
    assert rec2.load_or_train().mode == "learned"
    assert np.allclose(rec2.learned_prob(20, 2), p)


def test_threshold_is_a_setting(conn, cfg, synth):
    ensure_default_settings(conn)
    conn.execute("UPDATE settings SET value = '100' WHERE key = 'ai.rec.learned_min_events'")
    _learnable_history(synth, 250, np.random.default_rng(9))
    m = Recommender(conn, cfg, USER).train(save=False)
    assert m.mode == "learned"


def test_latest_thumb_wins_and_clear_cancels(conn, cfg, synth):
    t = synth.tracks
    day = timedelta(days=1)
    # down then clear: unblocked, even if tracks.blocked lags behind the events
    synth.event(t[0]["id"], "thumb_down", NOW - 3 * day)
    synth.event(t[0]["id"], "thumb_clear", NOW - 2 * day)
    synth.user_track(t[0]["id"], blocked=1)
    # up then down: blocked
    synth.event(t[1]["id"], "thumb_up", NOW - 3 * day)
    synth.event(t[1]["id"], "thumb_down", NOW - 2 * day)
    # down then up: liked, not blocked
    synth.event(t[2]["id"], "thumb_down", NOW - 3 * day)
    synth.event(t[2]["id"], "thumb_up", NOW - 2 * day)
    # up then clear: neutral
    synth.event(t[3]["id"], "thumb_up", NOW - 3 * day)
    synth.event(t[3]["id"], "thumb_clear", NOW - 2 * day)
    # no thumb events: tracks.blocked still counts
    synth.user_track(t[4]["id"], blocked=1)
    rec = Recommender(conn, cfg, USER)
    i = [rec.lib.index[x["id"]] for x in t[:5]]
    assert list(rec.sig.blocked[i]) == [False, True, False, False, True]
    assert list(rec.sig.weight[i]) == [0.0, 0.0, 2.0, 0.0, 0.0]
    assert rec.sig.n_feedback == 8
