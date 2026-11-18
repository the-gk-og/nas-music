"""Listening context (docs/v2.md §10): optional features, context mixes, extra listening signals."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from nasmusic.ai import api
from nasmusic.ai import context as cx
from nasmusic.ai.common import Settings, ensure_default_settings
from nasmusic.ai.recommender import Recommender
from tests.ai.conftest import NOW, USER, mix_rows

LOCAL = datetime(2026, 11, 18, 14, 30, tzinfo=UTC)


def test_parse_context_fields_and_missing():
    c = cx.parse('{"output": "bluetooth:Sony WH-1000", "motion": "Running", "place": "gym", '
                 '"weather": {"condition": "Rain", "tempC": 7.5}, "localHour": 6, "weekday": 2, '
                 '"seeks": [{"from": 10, "to": 120}, {"fromMs": 5000, "toMs": 2000}], '
                 '"volumeChanges": [{"from": 0.3, "to": 0.6}, -0.1], "repeatOfPrevious": true}', LOCAL)
    assert (c.output, c.output_name, c.motion, c.place) == ("bluetooth", "Sony WH-1000", "running", "gym")
    assert (c.weather, c.temp_c, c.hour, c.weekday, c.repeat) == ("rain", 7.5, 6, 2, True)
    assert c.seeks == [(10.0, 120.0), (5.0, 2.0)] and np.isclose(sum(c.volume_deltas), 0.2)
    empty = cx.parse(None, LOCAL)
    assert (empty.hour, empty.weekday, empty.output, empty.place) == (14, LOCAL.weekday(), None, None)
    assert cx.parse("not json", LOCAL).output is None
    # a switched-off signal is ignored even if old events still carry it
    off = cx.parse({"place": "gym", "output": "car"}, LOCAL, cx.disabled_fields({"context.location": False}))
    assert off.place is None and off.output == "car"


def test_listening_detail_weights():
    base = cx.Context(hour=12, weekday=1)
    assert cx.detail_weight(cx.Context(12, 1, repeat=True), 200) == 1.5                    # strong like
    assert cx.detail_weight(cx.Context(12, 1, seeks=[(10, 60), (70, 120), (130, 180)]), 200) == -0.5
    assert cx.detail_weight(cx.Context(12, 1, seeks=[(100, 40)]), 200) == 0.3              # re-listen
    assert cx.detail_weight(cx.Context(12, 1, volume_deltas=[0.2]), 200) == 0.2
    assert cx.detail_weight(cx.Context(12, 1, volume_deltas=[-0.5]), 200) == -0.2
    assert cx.detail_weight(base, 200) == 0.0


def test_context_keys_and_titles():
    s = Settings({})
    assert "workout" in cx.context_keys(cx.Context(18, 1, motion="running"), s)
    assert "workout" in cx.context_keys(cx.Context(18, 1, place="City Gym"), s)
    assert "drive" in cx.context_keys(cx.Context(8, 1, output="car"), s)
    k = cx.context_keys(cx.Context(23, 1, place="home"), s)
    assert {"late_night", "place:home", "evening"} <= k
    assert "late_night" not in cx.context_keys(cx.Context(15, 1, place="home"), s)
    assert "place:place-3" not in cx.context_keys(cx.Context(12, 1, place="place-3"), s)  # unnamed cluster
    assert [cx.mix_title(x) for x in ("workout", "drive", "late_night", "place:work")] == \
        ["Workout", "Drive", "Late night", "At work"]


def test_repeat_and_seek_signals_change_weights(conn, cfg, synth):
    a, b, c = synth.tracks[:3]
    synth.play(a, NOW - timedelta(days=2))
    synth.play(a, NOW - timedelta(days=2, minutes=-5), context={"repeatOfPrevious": True})
    synth.play(b, NOW - timedelta(days=2), context={"seeks": [{"from": 5, "to": 60}, {"from": 70, "to": 140},
                                                             {"from": 150, "to": 200}]})
    synth.play(c, NOW - timedelta(days=2))
    rec = Recommender(conn, cfg, USER)
    w = {t["id"]: rec.sig.weight[rec.lib.index[t["id"]]] for t in (a, b, c)}
    assert w[a["id"]] > 2 * w[c["id"]] + 1        # two plays + a repeat (strong like)
    assert w[b["id"]] < w[c["id"]]                # seeking through = mild dislike


def _gym_history(synth, plays: int, place: str = "gym", cluster: int = 2):
    tracks = synth.by_cluster(cluster)
    for k in range(plays):
        t = tracks[k % len(tracks)]
        synth.play(t, NOW - timedelta(days=4 + k % 20, hours=k % 5), context={"place": place, "motion": "running"})


def test_context_mix_needs_30_plays_in_that_context(conn, cfg, synth):
    for t in synth.by_cluster(0)[:25]:  # an established user with everyday listening
        synth.play(t, NOW - timedelta(days=10))
    _gym_history(synth, 29)
    ids = api.run_nightly_for_user(conn, cfg, USER)
    assert [r[0] for r in conn.execute("SELECT kind FROM mixes WHERE active=1 AND kind='context'")] == []
    assert len(ids) == 4
    _gym_history(synth, 2)
    ids = api.run_nightly_for_user(conn, cfg, USER)
    ctx = conn.execute("SELECT id, title FROM mixes WHERE active=1 AND kind='context'").fetchall()
    assert [t for _, t in ctx] == ["Workout"] and len(ids) == 5
    rows = mix_rows(conn, ctx[0][0])
    cluster = {t["id"]: t["cluster"] for t in synth.tracks}
    assert np.mean([cluster[r[1]] == 2 for r in rows]) >= 0.4      # what you play at the gym
    assert any(r[3] == "You often play this when working out" for r in rows)
    minutes = sum(r[4] for r in rows) / 60
    assert 50 <= minutes <= 66


def test_named_places_drive_and_late_night(conn, cfg, synth):
    ensure_default_settings(conn)
    conn.execute("UPDATE settings SET value = '5' WHERE key = 'ai.mix.context.max'")
    for k, t in enumerate(synth.by_cluster(1)[:32]):
        synth.play(t, NOW - timedelta(days=3, minutes=k), context={"output": "car"})
    for k, t in enumerate(synth.by_cluster(3)[:32]):
        synth.play(t, NOW - timedelta(days=4, minutes=k), context={"place": "work"})
    for k, t in enumerate(synth.by_cluster(4)[:32]):
        synth.play(t, NOW - timedelta(days=5, minutes=k), context={"place": "home", "localHour": 23})
    api.run_nightly_for_user(conn, cfg, USER)
    titles = {r[0] for r in conn.execute("SELECT title FROM mixes WHERE active=1 AND kind='context'")}
    assert titles == {"Drive", "At work", "Late night", "At home"}
    # turning the location switch off removes place-based mixes
    conn.execute("INSERT INTO user_settings (user_id, key, value) VALUES (?, 'context.location', 'false')", (USER,))
    api.run_nightly_for_user(conn, cfg, USER)
    titles = {r[0] for r in conn.execute("SELECT title FROM mixes WHERE active=1 AND kind='context'")}
    assert titles == {"Drive"}


def test_context_mix_skipped_if_schema_lacks_kind(conn, cfg, synth, caplog):
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE mixes2 AS SELECT * FROM mixes WHERE 0;
        DROP TABLE mixes;
        CREATE TABLE mixes (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN
            ('daily','morning','evening','deep_cuts','more_like')), title TEXT NOT NULL, for_date TEXT NOT NULL,
            created_at TEXT NOT NULL, seed_track TEXT, active INTEGER NOT NULL DEFAULT 1);
        DROP TABLE mixes2;
        PRAGMA foreign_keys = ON;
    """)
    for t in synth.by_cluster(0)[:25]:
        synth.play(t, NOW - timedelta(days=10))
    _gym_history(synth, 35)
    ids = api.run_nightly_for_user(conn, cfg, USER)
    assert len(ids) == 4
    assert any("allow 'context'" in r.getMessage() for r in caplog.records)


def test_learned_model_uses_context(conn, cfg, synth):
    """Cluster 0 is played through in the car and skipped elsewhere; cluster 1 the other way round."""
    rng = np.random.default_rng(4)
    for k in range(2100):
        t = synth.tracks[int(rng.integers(len(synth.tracks)))]
        if t["cluster"] not in (0, 1):
            continue
        in_car = rng.random() < 0.5
        ctx = {"output": "car"} if in_car else {"output": "wired"}
        good = (t["cluster"] == 0) == in_car
        at = NOW - timedelta(days=60) + timedelta(minutes=32 * k)
        if rng.random() < (0.9 if good else 0.1):
            synth.play(t, at, context=ctx)
        else:
            synth.skip(t, at, pos=8, context=ctx)
    rec = Recommender(conn, cfg, USER)
    m = rec.train(save=False)
    assert m.mode == "learned", m.notes
    assert m.notes["rows_with_context"] > 300
    idx0 = np.array([rec.lib.index[t["id"]] for t in synth.by_cluster(0)])
    idx1 = np.array([rec.lib.index[t["id"]] for t in synth.by_cluster(1)])
    car = rec.learned_prob(17, 2, context={"output": "car"})
    wired = rec.learned_prob(17, 2, context={"output": "wired"})
    unknown = rec.learned_prob(17, 2, context={})
    assert car[idx0].mean() > car[idx1].mean() + 0.2
    assert wired[idx1].mean() > wired[idx0].mean() + 0.2
    assert np.all(np.isfinite(unknown))  # missing context = unknown, still scores
