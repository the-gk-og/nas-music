"""AI radio (schema v6): smart shuffle / moment scoring, describe-a-vibe parsing, steering, variety."""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest

from nasmusic.ai import api, steering, vibe
from nasmusic.ai.recommender import Recommender
from tests.ai.conftest import NOW, USER


def _by(synth, key):
    return {t["id"]: t[key] for t in synth.tracks}


def _energy(conn, ids):
    rows = dict(conn.execute("SELECT track_id, energy FROM features").fetchall())
    return float(np.mean([rows[i] for i in ids]))


def kinds(chips):
    return {c["kind"]: c for c in chips}


# ---------------------------------------------------------------- vibe parsing (pure)

def test_parse_chill_rainy_evening():
    chips = vibe.parse("chill rainy evening")
    k = kinds(chips)
    assert k["energy"]["max"] <= 0.45 and "min" not in k["energy"]
    assert any(c["kind"] == "mood" and c["value"] == "chill" and c["weight"] > 0 for c in chips)
    assert all(c["label"] and c["id"] for c in chips)


def test_parse_gym_no_slow_songs():
    k = kinds(vibe.parse("gym, no slow songs"))
    assert k["energy"]["min"] >= 0.65 and "max" not in k["energy"]
    assert k["tempo"]["min"] == 100
    assert k["energy"]["label"] == "High energy"


def test_parse_focus_no_lyrics():
    chips = vibe.parse("focus, no lyrics")
    k = kinds(chips)
    assert k["vocals"]["value"] == "instrumental" and k["vocals"]["label"] == "No vocals"
    assert k["energy"]["max"] <= 0.7
    assert any(c["kind"] == "mood" and c["value"] == "focus" for c in chips)


def test_parse_2000s_throwbacks():
    k = kinds(vibe.parse("2000s throwbacks"))
    assert (k["year"]["min"], k["year"]["max"]) == (2000, 2009) and k["year"]["label"] == "2000s"
    assert k["familiarity"]["value"] == "familiar"


@pytest.mark.parametrize("text,lo,hi", [("80s", 1980, 1989), ("'90s pop", 1990, 1999), ("before 1995", None, 1994),
                                        ("since 2015", 2015, None), ("1995-2001", 1995, 2001), ("1999", 1999, 1999)])
def test_parse_years(text, lo, hi):
    y = kinds(vibe.parse(text))["year"]
    assert y.get("min") == lo and y.get("max") == hi


def test_parse_negation_and_genres():
    chips = vibe.parse("upbeat rock but no metal, nothing sad")
    by = {c["id"]: c for c in chips}
    assert "genre:rock" in by and "genre:metal:x" in by and by["genre:metal:x"]["label"] == "No Metal"
    assert any(c["kind"] == "mood" and c["value"] == "sad" and c["weight"] < 0 and c["label"] == "Not melancholy"
               for c in chips)
    assert kinds(chips)["energy"]["min"] >= 0.5


def test_parse_bpm_and_unknown_words():
    k = kinds(vibe.parse("120-130 bpm zxqv"))
    assert (k["tempo"]["min"], k["tempo"]["max"]) == (120, 130)
    assert len(vibe.parse("zxqv blorp")) == 0


def test_parse_library_vocab():
    voc = vibe.Vocab({"artist 2-3": ("a23", "Artist 2-3")}, {"hardbop"})
    chips = vibe.parse("like Artist 2-3, hard bop", voc)
    assert any(c["kind"] == "like" and c["value"] == "a23" and c["label"] == "Like Artist 2-3" for c in chips)
    assert any(c["kind"] == "genre" and c["value"] == "hard bop" for c in chips)


def test_sanitize_recomputes_and_drops_junk():
    chips = vibe.sanitize([{"kind": "energy", "max": 0.4, "label": "evil"}, {"kind": "bogus"}, "x",
                           {"kind": "mood", "value": "notamood"}, {"kind": "vocals", "value": "instrumental"},
                           {"kind": "energy", "max": 0.4}])
    assert [c["kind"] for c in chips] == ["energy", "vocals"]
    assert chips[0]["label"] == "Low energy"
    with pytest.raises(ValueError):
        vibe.sanitize("nope")


def test_station_key_ignores_order():
    a = vibe.parse("chill jazz")
    assert vibe.station_key(a) == vibe.station_key(list(reversed(a)))


# ---------------------------------------------------------------- variety helpers

def _spacing_ok(synth, ids, gap):
    art = _by(synth, "artist_id")
    for k in range(1, len(ids)):
        if art[ids[k]] in {art[x] for x in ids[max(0, k - gap):k]}:
            return False
    return True


def _taste(synth):
    for t in synth.by_cluster(0)[:20] + synth.by_cluster(3)[:20]:
        for d in (12, 25):
            synth.play(t, NOW - timedelta(days=d))


# ---------------------------------------------------------------- vibe radio

def test_vibe_radio_respects_constraints(conn, cfg, synth):
    _taste(synth)
    rid, ids = api.radio_start(conn, cfg, USER, {"type": "vibe", "text": "chill, low energy"})
    assert len(ids) == 25 and len(set(ids)) == 25
    lib_energy = np.array([r[0] for r in conn.execute("SELECT energy FROM features")])
    assert _energy(conn, ids) < np.mean(lib_energy) - 0.1
    st = api.radio_station(conn, cfg, USER, rid)
    assert st["kind"] == "vibe" and st["constraints"] and st["explanation"].startswith("Vibe:")
    assert conn.execute("SELECT title FROM radio_sessions WHERE id = ?", (rid,)).fetchone()[0] == "Chill, low energy radio"


def test_vibe_radio_edited_chips_and_genre(conn, cfg, synth):
    chips = api.radio_parse_vibe(conn, cfg, USER, "jazz")["constraints"]
    assert any(c["kind"] == "genre" for c in chips)
    rid, ids = api.radio_start(conn, cfg, USER, {"type": "vibe", "constraints": chips})
    cl = _by(synth, "cluster")
    assert np.mean([cl[i] == 1 for i in ids]) >= 0.9          # GENRES[1] is Jazz
    # years: albums are 2000 + artist index
    rid, ids = api.radio_start(conn, cfg, USER, {"type": "vibe", "text": "2000-2002"})
    years = dict(conn.execute("SELECT t.id, al.year FROM tracks t JOIN albums al ON al.id = t.album_id").fetchall())
    assert all(2000 <= years[i] <= 2002 for i in ids)


def test_vibe_radio_relaxes_when_nothing_matches(conn, cfg, synth):
    rid, ids = api.radio_start(conn, cfg, USER, {"type": "vibe", "text": "1950s 200 bpm"})
    assert len(ids) == 25
    assert "loosened" in api.radio_station(conn, cfg, USER, rid)["explanation"]


def test_vibe_needs_something(conn, cfg, synth):
    with pytest.raises(ValueError):
        api.radio_start(conn, cfg, USER, {"type": "vibe", "text": ""})


# ---------------------------------------------------------------- moment / smart shuffle

def _routine(synth):
    """Friday evenings: cluster 1. Weekday mornings: cluster 4. Both weeks back."""
    fri = {"localHour": 19, "weekday": 5}
    tue = {"localHour": 8, "weekday": 2}
    for d in range(3, 40, 7):
        for t in synth.by_cluster(1)[:24]:
            synth.play(t, NOW - timedelta(days=d), context=fri)
        for t in synth.by_cluster(4)[:24]:
            synth.play(t, NOW - timedelta(days=d + 1), context=tue)


def test_moment_follows_time_of_day(conn, cfg, synth):
    _routine(synth)
    cl = _by(synth, "cluster")
    rid, ids = api.radio_start(conn, cfg, USER, {"type": "moment"}, context={"localHour": 19, "weekday": 5})
    assert np.mean([cl[i] == 1 for i in ids]) > np.mean([cl[i] == 4 for i in ids])
    assert np.mean([cl[i] == 1 for i in ids]) >= 0.4
    st = api.radio_station(conn, cfg, USER, rid)
    assert st["kind"] == "moment" and "Friday evening" in st["explanation"]
    assert "usually go for" in st["explanation"]
    rid, ids = api.radio_start(conn, cfg, USER, {"type": "moment"}, context={"localHour": 8, "weekday": 2})
    assert np.mean([cl[i] == 4 for i in ids]) > np.mean([cl[i] == 1 for i in ids])
    assert "Tuesday morning" in api.radio_station(conn, cfg, USER, rid)["explanation"]


def test_moment_momentum_and_recent_skips(conn, cfg, synth):
    _taste(synth)
    cl = _by(synth, "cluster")
    for k, t in enumerate(synth.by_cluster(2)[:6]):
        synth.play(t, NOW - timedelta(minutes=50 - 5 * k))
    for t in synth.by_cluster(0)[:4]:
        synth.skip(t, NOW - timedelta(minutes=10))
    rid, ids = api.radio_start(conn, cfg, USER, {"type": "moment"})
    share2 = np.mean([cl[i] == 2 for i in ids])
    assert share2 >= 0.4
    assert np.mean([cl[i] == 0 for i in ids]) < share2
    exp = api.radio_station(conn, cfg, USER, rid)["explanation"]
    assert "you've been playing" in exp and "skipped" in exp
    played = {t["id"] for t in synth.by_cluster(2)[:6]}
    assert not played & set(ids)          # just heard: not replayed


def test_moment_device_station_and_context(conn, cfg, synth):
    _taste(synth)
    rid, _ = api.radio_start(conn, cfg, USER, {"type": "moment"}, context={"output": "car"}, device_kind="android")
    st = api.radio_station(conn, cfg, USER, rid)
    assert st["stationKey"] == "moment:car" and st["context"]["deviceKind"] == "car"
    assert "in the car" in st["explanation"]
    rid, _ = api.radio_start(conn, cfg, USER, {"type": "moment"}, device_kind="desktop")
    assert api.radio_station(conn, cfg, USER, rid)["stationKey"] == "moment:desktop"


def test_moment_variety_across_batches(conn, cfg, synth):
    _taste(synth)
    rid, ids = api.radio_start(conn, cfg, USER, {"type": "moment"})
    for _ in range(4):
        ids += api.radio_more(conn, cfg, USER, rid, ids, 25)
    assert len(ids) == 125 and len(set(ids)) == 125          # no repeats in the session
    assert _spacing_ok(synth, ids, 4)                          # no artist within 4, across batch edges
    # it's a shuffle: two starts differ
    _, a = api.radio_start(conn, cfg, USER, {"type": "moment"})
    _, b = api.radio_start(conn, cfg, USER, {"type": "moment"})
    assert a != b


def test_moment_includes_rediscoveries(conn, cfg, synth):
    _taste(synth)
    played = {t["id"] for t in synth.by_cluster(0)[:20] + synth.by_cluster(3)[:20]}
    _, ids = api.radio_start(conn, cfg, USER, {"type": "moment"})
    assert any(i not in played for i in ids)


# ---------------------------------------------------------------- steering

def test_steer_energy_changes_next_fetch_and_is_remembered(conn, cfg, synth):
    _taste(synth)
    rid, ids = api.radio_start(conn, cfg, USER, {"type": "moment"})
    base = api.radio_more(conn, cfg, USER, rid, ids, 25)
    for _ in range(3):
        st = api.radio_steer(conn, cfg, USER, rid, "more_energy")
    assert st["steering"]["energy"] == 3 and "more energy" in st["steeringSummary"]
    up = api.radio_more(conn, cfg, USER, rid, ids + base, 25)
    assert _energy(conn, up) > _energy(conn, base) + 0.05
    # a new session of the same station starts steered
    rid2, ids2 = api.radio_start(conn, cfg, USER, {"type": "moment"})
    assert api.radio_station(conn, cfg, USER, rid2)["steering"]["energy"] == 3
    assert "Steered" in api.radio_station(conn, cfg, USER, rid2)["explanation"]
    for _ in range(6):
        api.radio_steer(conn, cfg, USER, rid2, "calmer")
    down = api.radio_more(conn, cfg, USER, rid2, ids2, 25)
    assert _energy(conn, down) < _energy(conn, up) - 0.1
    assert api.radio_steer(conn, cfg, USER, rid2, "reset")["steering"]["energy"] == 0


def test_steer_less_like_artist_and_never_here(conn, cfg, synth):
    _taste(synth)
    seed = synth.by_cluster(2)[0]
    rid, ids = api.radio_start(conn, cfg, USER, {"type": "track", "id": seed["id"]})
    art = _by(synth, "artist_id")
    victim = ids[1]
    st = api.radio_steer(conn, cfg, USER, rid, "less_like", victim, "artist")
    assert st["steering"]["lessLike"][0]["type"] == "artist"
    never = ids[2]
    api.radio_steer(conn, cfg, USER, rid, "never_here", never, "track")
    more = []
    for _ in range(3):
        more += api.radio_more(conn, cfg, USER, rid, ids + more, 25)
    assert art[victim] not in {art[i] for i in more}
    assert never not in more
    # other stations are unaffected by "never here"
    rid2, _ = api.radio_start(conn, cfg, USER, {"type": "artist", "id": art[never]})
    assert api.radio_station(conn, cfg, USER, rid2)["steering"]["never"] == []
    # undo
    st = api.radio_steer(conn, cfg, USER, rid, "remove", scope="track", ref_id=never)
    assert st["steering"]["never"] == []


def test_steer_more_like_pulls_toward_that_sound(conn, cfg, synth):
    _taste(synth)
    cl = _by(synth, "cluster")
    rid, ids = api.radio_start(conn, cfg, USER, {"type": "random"})
    target = synth.by_cluster(5)[0]["id"]
    before = api.radio_more(conn, cfg, USER, rid, ids, 25)
    api.radio_steer(conn, cfg, USER, rid, "more_like", target, "track")
    api.radio_steer(conn, cfg, USER, rid, "more_like", synth.by_cluster(5)[9]["id"], "track")
    after = api.radio_more(conn, cfg, USER, rid, ids + before, 25)
    assert np.mean([cl[i] == 5 for i in after]) > np.mean([cl[i] == 5 for i in before])


def test_steer_variety_axis(conn, cfg, synth):
    _taste(synth)
    rid, ids = api.radio_start(conn, cfg, USER, {"type": "moment"})
    api.radio_steer(conn, cfg, USER, rid, "more_familiar")
    api.radio_steer(conn, cfg, USER, rid, "more_familiar")
    fam = api.radio_more(conn, cfg, USER, rid, ids, 25)
    played = {t["id"] for t in synth.by_cluster(0)[:20] + synth.by_cluster(3)[:20]}
    st = api.radio_steer(conn, cfg, USER, rid, "reset")
    assert st["steering"]["variety"] == 0
    for _ in range(2):
        api.radio_steer(conn, cfg, USER, rid, "more_variety")
    var = api.radio_more(conn, cfg, USER, rid, ids + fam, 25)
    assert np.mean([i in played for i in fam]) > np.mean([i in played for i in var])
    assert _spacing_ok(synth, var, 5)       # more variety widens artist spacing


def test_steer_errors(conn, cfg, synth):
    rid, ids = api.radio_start(conn, cfg, USER, {"type": "moment"})
    with pytest.raises(ValueError):
        api.radio_steer(conn, cfg, USER, rid, "explode")
    with pytest.raises(ValueError):
        api.radio_steer(conn, cfg, USER, rid, "more_like")
    with pytest.raises(KeyError):
        api.radio_steer(conn, cfg, USER, "nope", "calmer")


def test_steering_feeds_the_recommender(conn, cfg, synth):
    rid, ids = api.radio_start(conn, cfg, USER, {"type": "moment"})
    api.radio_steer(conn, cfg, USER, rid, "more_like", ids[0], "track")
    api.radio_steer(conn, cfg, USER, rid, "less_like", ids[1], "track")
    rec = Recommender(conn, cfg, USER)
    i0, i1 = rec.lib.index[ids[0]], rec.lib.index[ids[1]]
    assert rec.sig.weight[i0] > 0.5 and rec.sig.weight[i1] < -0.5
    labels = {(e[2], e[3]): e[4] for e in rec.sig.events}
    assert labels[(i0, "radio_more_like")] == 1 and labels[(i1, "radio_less_like")] == 0


def test_refill_replaces_upcoming(conn, cfg, synth):
    _taste(synth)
    rid, ids = api.radio_start(conn, cfg, USER, {"type": "moment"})
    keep, drop = ids[:2], ids[2:]
    for _ in range(3):
        api.radio_steer(conn, cfg, USER, rid, "calmer")
    fresh = api.radio_refill(conn, cfg, USER, rid, drop, keep, 23)
    assert len(fresh) == 23 and not set(fresh) & set(keep)
    assert _energy(conn, fresh) < _energy(conn, drop)


def test_steering_unit():
    st = steering.Steering().apply("more_energy").apply("more_energy").apply("more_energy").apply("more_energy")
    assert st.energy == 3
    st.apply("less_like", {"type": "artist", "id": "a", "name": "A"}).apply("more_like", {"type": "artist", "id": "a"})
    assert st.less == [] and st.more[0]["id"] == "a"
    assert steering.Steering.from_json(st.to_json()).as_dict() == st.as_dict()
    assert steering.Steering.from_json("garbage").is_neutral


def test_vocab_from_real_library(conn, cfg, synth):
    out = api.radio_parse_vibe(conn, cfg, USER, "something like Artist 2-3")
    assert any(c["kind"] == "like" and c["name"] == "Artist 2-3" for c in out["constraints"])
    rid, ids = api.radio_start(conn, cfg, USER, {"type": "vibe", "constraints": out["constraints"]})
    cl = _by(synth, "cluster")
    assert np.mean([cl[i] == 2 for i in ids]) >= 0.6
