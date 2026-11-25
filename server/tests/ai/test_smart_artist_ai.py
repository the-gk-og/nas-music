"""v7 AI additions: similar artists (embedding centroids) and smart-playlist radio seeds."""
import numpy as np
import pytest

from homebeat.ai import api
from homebeat.ai.common import iso
from tests.ai.conftest import USER
from tests.ai.schema_v2 import add_user


def test_similar_artists_come_from_the_same_cluster(conn, cfg, synth):
    t = synth.by_cluster(2)[0]
    res = api.similar_artists(conn, cfg, t["artist_id"], limit=5)
    assert len(res) == 5
    cluster_of = {x["artist_id"]: x["cluster"] for x in synth.tracks}
    assert all(cluster_of[a] == 2 for a, _ in res)
    assert t["artist_id"] not in [a for a, _ in res]
    scores = [s for _, s in res]
    assert scores == sorted(scores, reverse=True) and all(0 <= s <= 1 for s in scores)
    assert api.similar_artists(conn, cfg, "nobody") == []


def test_smart_playlist_seed(conn, cfg, synth):
    conn.execute("INSERT INTO smart_playlists (id, user_id, name, definition, created_at, updated_at) "
                 "VALUES ('sp1', ?, 'Smart', '{}', ?, ?)", (USER, iso(), iso()))
    for k, tr in enumerate(synth.by_cluster(3)[:6]):
        conn.execute("INSERT INTO smart_playlist_tracks VALUES ('sp1', ?, ?)", (k, tr["id"]))
    _rid, ids = api.radio_start(conn, cfg, USER, {"type": "smart_playlist", "id": "sp1"})
    cl = {x["id"]: x["cluster"] for x in synth.tracks}
    assert np.mean([cl[i] == 3 for i in ids]) >= 0.7
    assert conn.execute("SELECT title FROM radio_sessions ORDER BY rowid DESC LIMIT 1").fetchone()[0] == "Smart radio"
    add_user(conn, "bob")
    with pytest.raises(KeyError):
        api.radio_start(conn, cfg, "bob", {"type": "smart_playlist", "id": "sp1"})
