"""v3 radio and "Because you played" shelves (docs/v3-ux.md §3.2, §5)."""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest

from homebeat.ai import api
from homebeat.ai.common import iso
from homebeat.ai.recommender import Recommender
from tests.ai.conftest import NOW, USER, build_library
from tests.ai.schema_v2 import add_user


def _cluster(synth):
    return {t["id"]: t["cluster"] for t in synth.tracks}


def _artist(synth):
    return {t["id"]: t["artist_id"] for t in synth.tracks}


def _taste(synth, user=USER):
    for t in synth.by_cluster(0)[:20] + synth.by_cluster(3)[:20]:
        for d in (12, 25):
            synth.play(t, NOW - timedelta(days=d), user=user)
    for t in synth.by_cluster(5)[:10]:
        synth.skip(t, NOW - timedelta(days=9), user=user)


def _check_queue(synth, ids, n=25):
    assert len(ids) == n and len(set(ids)) == n
    art = _artist(synth)
    for k in range(len(ids)):
        assert art[ids[k]] not in {art[x] for x in ids[max(0, k - 3):k]} or k == 0, "artist within 3"


def test_track_radio(conn, cfg, synth):
    _taste(synth)
    seed = synth.by_cluster(2)[3]
    rid, ids = api.radio_start(conn, cfg, USER, {"type": "track", "id": seed["id"]})
    _check_queue(synth, ids)
    assert ids[0] == seed["id"]
    cl = _cluster(synth)
    assert np.mean([cl[i] == 2 for i in ids]) >= 0.7          # sounds like the seed
    title = conn.execute("SELECT title, user_id FROM radio_sessions WHERE id = ?", (rid,)).fetchone()
    assert tuple(title) == ("Song 2-0-3 radio", USER)


def test_artist_album_playlist_seeds(conn, cfg, synth):
    t = synth.by_cluster(4)[0]
    _rid, ids = api.radio_start(conn, cfg, USER, {"type": "artist", "id": t["artist_id"]})
    cl, art = _cluster(synth), _artist(synth)
    assert np.mean([cl[i] == 4 for i in ids]) >= 0.7
    assert 1 <= sum(art[i] == t["artist_id"] for i in ids) <= 6
    album = conn.execute("SELECT album_id FROM tracks WHERE id = ?", (t["id"],)).fetchone()[0]
    _, ids = api.radio_start(conn, cfg, USER, {"type": "album", "id": album})
    assert np.mean([cl[i] == 4 for i in ids]) >= 0.7
    assert conn.execute("SELECT title FROM radio_sessions ORDER BY rowid DESC LIMIT 1").fetchone()[0] == \
        "Album 4-0 radio"
    # playlist: mixed seed -> both clusters show up
    conn.execute("INSERT INTO playlists (id, user_id, name, created_at, updated_at) VALUES ('p1', ?, 'Mine', ?, ?)",
                 (USER, iso(), iso()))
    for k, tr in enumerate(synth.by_cluster(1)[:4] + synth.by_cluster(5)[:4]):
        conn.execute("INSERT INTO playlist_tracks (id, playlist_id, position, track_id, added_at) "
                         "VALUES (?, 'p1', ?, ?, ?)", (f"e{k}", k, tr["id"], iso()))
    _, ids = api.radio_start(conn, cfg, USER, {"type": "playlist", "id": "p1"})
    assert {cl[i] for i in ids} & {1, 5}
    # someone else's playlist is not yours to seed from
    add_user(conn, "bob")
    with pytest.raises(KeyError):
        api.radio_start(conn, cfg, "bob", {"type": "playlist", "id": "p1"})


def test_random_radio_follows_taste_and_varies(conn, cfg, synth):
    _taste(synth)
    Recommender(conn, cfg, USER).train(save=True)
    cl = _cluster(synth)
    runs = [api.radio_start(conn, cfg, USER, {"type": "random"})[1] for _ in range(6)]
    for ids in runs:
        _check_queue(synth, ids)
        assert np.mean([cl[i] in (0, 3) for i in ids]) >= 0.6, "seeded from the user's taste centres"
        assert not any(cl[i] == 5 for i in ids[:10])
    assert len({tuple(r) for r in runs}) == 6                    # different every time
    titles = {r[0] for r in conn.execute("SELECT title FROM radio_sessions")}
    assert all(t.endswith(" radio") for t in titles)


def test_radio_more_is_endless_without_repeats_and_drifts(conn, cfg, synth):
    _taste(synth)
    seed = synth.by_cluster(2)[0]
    rid, first = api.radio_start(conn, cfg, USER, {"type": "track", "id": seed["id"]})
    batches = [first]
    for _ in range(5):
        batches.append(api.radio_more(conn, cfg, USER, rid, exclude_ids=[]))
    served = [t for b in batches for t in b]
    assert len(served) == 150 and len(set(served)) == 150      # no repeats within the session
    rec = Recommender(conn, cfg, USER)
    s = rec.lib.emb[rec.lib.index[seed["id"]]]
    sim = [np.mean([rec.lib.emb[rec.lib.index[t]] @ s for t in b]) for b in batches]
    assert sim[0] > sim[-1]                                      # drifts gradually from the seed
    assert sim[1] > sim[-1] - 0.05
    # client-side exclusions are honoured too
    extra = [t["id"] for t in synth.by_cluster(2)[40:48]]
    nxt = api.radio_more(conn, cfg, USER, rid, exclude_ids=extra, n=10)
    assert len(nxt) == 10 and not set(nxt) & set(extra) and not set(nxt) & set(served)


def test_radio_more_respects_session_skips(conn, cfg, synth):
    seed = synth.by_cluster(2)[0]
    rid, ids = api.radio_start(conn, cfg, USER, {"type": "track", "id": seed["id"]})
    art = _artist(synth)
    # during the session the user skips everything by two artists the radio played
    victims = [a for a in dict.fromkeys(art[i] for i in ids[1:]) if a != seed["artist_id"]][:2]
    for k, tid in enumerate(i for i in ids if art[i] in victims):
        synth.event(tid, "skip", NOW + timedelta(minutes=k + 1), position_s=5,
                    duration_s=200)
    nxt = api.radio_more(conn, cfg, USER, rid, exclude_ids=[])
    assert not any(art[i] in victims for i in nxt)


def test_radio_errors_and_ownership(conn, cfg, synth):
    add_user(conn, "bob")
    rid, _ = api.radio_start(conn, cfg, USER, {"type": "random"})
    with pytest.raises(KeyError):
        api.radio_more(conn, cfg, "bob", rid, [])              # not bob's radio
    with pytest.raises(KeyError):
        api.radio_more(conn, cfg, USER, "nope", [])
    with pytest.raises(KeyError):
        api.radio_start(conn, cfg, "nobody", {"type": "random"})
    with pytest.raises(KeyError):
        api.radio_start(conn, cfg, USER, {"type": "track", "id": "unanalysed"})
    with pytest.raises(ValueError):
        api.radio_start(conn, cfg, USER, {"type": "genre", "id": "x"})
    with pytest.raises(ValueError):
        api.radio_start(conn, cfg, USER, {"type": "artist"})


def test_radio_never_trains_and_small_library_runs_out(conn, cfg, monkeypatch):
    synth = build_library(conn, cfg, n_clusters=2, artists_per_cluster=5, tracks_per_artist=4)  # 40 tracks
    for t in synth.tracks[:30]:
        synth.play(t, NOW - timedelta(days=3))
    monkeypatch.setattr(Recommender, "train", lambda *a, **k: (_ for _ in ()).throw(AssertionError("trained")))
    rid, ids = api.radio_start(conn, cfg, USER, {"type": "random"})
    got = list(ids)
    for _ in range(4):
        got += api.radio_more(conn, cfg, USER, rid, [])
    assert len(got) == len(set(got)) <= 40
    assert api.radio_more(conn, cfg, USER, rid, []) == [] or len(set(got)) < 40


def test_because_you_played(conn, cfg, synth):
    add_user(conn, "bob")
    cl, art = _cluster(synth), _artist(synth)
    fav = [synth.by_cluster(1)[0], synth.by_cluster(4)[0], synth.by_cluster(2)[0], synth.by_cluster(3)[8]]
    for k, t in enumerate(fav):
        synth.user_track(t["id"], starred_at="2026-01-01T00:00:00Z")
        synth.play(t, NOW - timedelta(days=2, hours=k))         # fav[0] is the most recent
    old = synth.by_cluster(5)[0]
    synth.user_track(old["id"], starred_at="2026-01-01T00:00:00Z")
    synth.play(old, NOW - timedelta(days=60))                   # too long ago
    meh = synth.by_cluster(0)[0]
    synth.play(meh, NOW - timedelta(hours=1))                   # recent but not a favourite
    shelves = api.because_you_played(conn, cfg, USER)
    assert [s["seed_track_id"] for s in shelves] == [t["id"] for t in fav[:3]]
    all_tracks = [t for s in shelves for t in s["track_ids"]]
    assert len(all_tracks) == len(set(all_tracks))
    for s, t in zip(shelves, fav, strict=False):
        assert s["title"] == f"Because you played Song {t['cluster']}-0-0"
        assert 10 <= len(s["track_ids"]) <= 15
        assert np.mean([cl[x] == t["cluster"] for x in s["track_ids"]]) >= 0.8
        assert t["id"] not in s["track_ids"]
        assert max(sum(art[x] == a for x in s["track_ids"]) for a in set(art.values())) <= 2
    assert len(api.because_you_played(conn, cfg, USER, limit_shelves=1)) == 1
    # only the user's own history: bob has none, so no shelves for bob
    assert api.because_you_played(conn, cfg, "bob") == []
