"""Search by lyrics (schema v9: lyrics_index + lyrics_fts) and the On this day Home shelf."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mutagen.flac import FLAC

from homebeat import db
from homebeat.core import iso
from homebeat.db import migrations
from homebeat.library import lyrics_index, overrides
from tests.conftest import track_ids

V8_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v8.sql").read_text()


def _set_lyrics(cfg, conn, title: str, text: str) -> str:
    tid = track_ids(conn)[title]
    path = conn.execute("SELECT path FROM tracks WHERE id = ?", (tid,)).fetchone()[0]
    f = FLAC(cfg.library_root / path)
    f["LYRICS"] = text
    f.save()
    return tid


def _bump_mtime(conn, tid):
    conn.execute("UPDATE tracks SET mtime = mtime + 1 WHERE id = ?", (tid,))


# ---------- migration ----------

def test_v8_to_v9(tmp_path):
    path = tmp_path / "homebeat.db"
    conn = db.connect(path)
    conn.executescript(V8_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (8)")
    conn.close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION
    assert {"track_id", "source", "mtime", "indexed_at"} <= migrations._columns(conn, "lyrics_index")
    conn.execute("INSERT INTO lyrics_fts (track_id, body) VALUES ('t', 'hello')")
    assert conn.execute("SELECT track_id FROM lyrics_fts WHERE lyrics_fts MATCH 'hello'").fetchone()[0] == "t"
    assert len(list(tmp_path.glob("homebeat.db.v8-backup-*"))) == 1
    assert not migrations.migrate_v8_to_v9(conn, db.schema_sql())


# ---------- index ----------

def test_refresh_indexes_file_lyrics_and_skips_unchanged(cfg, scanned):
    conn = scanned
    tid = _set_lyrics(cfg, conn, "Song 1", "[00:01.00]Hello darkness my old friend\n[00:04.00]I've come to talk")
    _bump_mtime(conn, tid)
    out = lyrics_index.refresh(conn, cfg.library_root)
    assert out["complete"] and out["withLyrics"] == 1
    assert out["indexed"] == conn.execute("SELECT COUNT(*) FROM tracks WHERE missing = 0").fetchone()[0]
    body = conn.execute("SELECT body FROM lyrics_fts WHERE track_id = ?", (tid,)).fetchone()[0]
    assert "[00:" not in body and "Hello darkness" in body
    assert lyrics_index.refresh(conn, cfg.library_root)["indexed"] == 0       # nothing changed
    assert lyrics_index.refresh(conn, cfg.library_root, full=True)["indexed"] > 0


def test_admin_edit_and_lrclib_cache_are_indexed(cfg, scanned):
    conn = scanned
    ids = track_ids(conn)
    lyrics_index.refresh(conn, cfg.library_root)
    later = iso(datetime.now(UTC) + timedelta(seconds=5))
    conn.execute("INSERT INTO lyrics_cache (track_id, status, synced, raw, fetched_at) VALUES (?, 'found', 0, ?, ?)",
                 (ids["Song 2"], "Purple rain falling", later))
    conn.execute("INSERT INTO tag_overrides (track_id, field, value, updated_at) VALUES (?, 'lyrics', ?, ?)",
                 (ids["Opening"], '"Admin words here"', later))
    assert lyrics_index.refresh(conn, cfg.library_root)["indexed"] == 2
    src = dict(conn.execute("SELECT track_id, source FROM lyrics_index").fetchall())
    assert src[ids["Song 2"]] == "lrclib" and src[ids["Opening"]] == "library"
    assert overrides.lyrics_override(conn, ids["Opening"]) == "Admin words here"
    # reverting the admin edit puts the track back to "no lyrics"
    conn.execute("DELETE FROM tag_overrides WHERE track_id = ?", (ids["Opening"],))
    lyrics_index.refresh(conn, cfg.library_root)
    assert conn.execute("SELECT source FROM lyrics_index WHERE track_id = ?", (ids["Opening"],)).fetchone()[0] == "none"
    assert not conn.execute("SELECT 1 FROM lyrics_fts WHERE track_id = ?", (ids["Opening"],)).fetchone()


def test_missing_tracks_leave_the_index(cfg, scanned):
    conn = scanned
    tid = _set_lyrics(cfg, conn, "Song 1", "gone tomorrow")
    _bump_mtime(conn, tid)
    lyrics_index.refresh(conn, cfg.library_root)
    conn.execute("UPDATE tracks SET missing = 1 WHERE id = ?", (tid,))
    assert lyrics_index.refresh(conn, cfg.library_root)["removed"] == 1
    assert not conn.execute("SELECT 1 FROM lyrics_fts WHERE track_id = ?", (tid,)).fetchone()


def test_matching_line_and_query():
    text = "First line here\nCafé society never sleeps\nAnother line"
    assert lyrics_index.matching_line(text, "cafe SOCIETY") == "Café society never sleeps"
    assert lyrics_index.fts_query('he said "no"') == '"he" "said" "no"'
    assert lyrics_index.fts_query("never sle") == '"never" "sle"*'
    assert lyrics_index.fts_query("!!!") is None


# ---------- API ----------

def test_search_by_lyrics(client, cfg, conn):
    tid = _set_lyrics(cfg, conn, "Song 1", "[00:01.00]We were dancing in the moonlight\n[00:05.00]Nothing else")
    _bump_mtime(conn, tid)
    lyrics_index.refresh(conn, cfg.library_root)
    r = client.get("/api/v1/search", params={"q": "dancing moonl", "scope": "lyrics"})
    assert r.status_code == 200
    body = r.json()
    assert [m["track"]["id"] for m in body["lyrics"]] == [tid]
    assert body["lyrics"][0]["line"] == "We were dancing in the moonlight"
    assert body["tracks"] == [] and body["catalog"] == []
    # an ordinary library search carries a few lyric matches too
    r = client.get("/api/v1/search", params={"q": "moonlight", "scope": "library"}).json()
    assert [m["track"]["id"] for m in r["lyrics"]] == [tid]
    assert client.get("/api/v1/search", params={"q": "zebra", "scope": "lyrics"}).json()["lyrics"] == []
    assert client.get("/api/v1/search", params={"q": '"(*', "scope": "lyrics"}).status_code == 200


def test_scan_job_builds_the_index(client, app, cfg, conn):
    tid = _set_lyrics(cfg, conn, "Song 2", "Sweet caroline")
    _bump_mtime(conn, tid)
    res = app.state.ctx.jobs._scan(conn)
    assert res["lyricsIndex"]["withLyrics"] == 1
    hits = client.get("/api/v1/search", params={"q": "caroline", "scope": "lyrics"}).json()["lyrics"]
    assert [h["track"]["title"] for h in hits] == ["Song 2"]


# ---------- On this day ----------

def _play(conn, uid, tid, at: datetime, n: int = 1):
    for i in range(n):
        conn.execute("INSERT INTO events (id, user_id, type, track_id, at, received_at) VALUES (?, ?, 'play', ?, ?, ?)",
                     (f"e-{tid}-{at.isoformat()}-{i}", uid, tid, iso(at + timedelta(minutes=i)), iso(at)))


def test_on_this_day_shelf(client, conn):
    from homebeat.app import onthisday

    ids = track_ids(conn)
    now = datetime.now(UTC)
    uid = client.user_id
    _play(conn, uid, ids["Song 1"], now.replace(year=now.year - 1) - timedelta(days=1), n=3)
    _play(conn, uid, ids["Closing"], now.replace(year=now.year - 2), n=1)
    _play(conn, uid, ids["Opening"], now.replace(year=now.year - 1) - timedelta(days=40))   # outside the window
    _play(conn, uid, ids["Song 2"], now - timedelta(days=1))                                # this year
    shelves = {s["kind"]: s for s in client.get("/api/v1/home").json()["shelves"]}
    s = shelves["on_this_day"]
    assert [i["id"] for i in s["items"]] == [ids["Song 1"], ids["Closing"]]
    assert s["items"][0]["subtitle"].endswith("1 year ago, played 3 times")
    assert "2 years ago, played once" in s["items"][1]["subtitle"]
    assert s["items"][0]["track"]["id"] == ids["Song 1"]
    # hidden like any Home shelf
    assert client.put("/api/v1/me/settings", json={"settings": {"home.hidden": ["on_this_day"]}}).status_code == 200
    assert "on_this_day" not in {x["kind"] for x in client.get("/api/v1/home").json()["shelves"]}
    # 29 February falls back to the 28th in other years
    leap = datetime(2028, 2, 29, 12, tzinfo=UTC)
    _play(conn, uid, ids["Song 2"], datetime(2027, 2, 28, 12, tzinfo=UTC))
    assert ids["Song 2"] in [i["id"] for i in onthisday.items(conn, uid, "UTC", now=leap)]


def test_on_this_day_empty_without_history(client):
    kinds = {s["kind"] for s in client.get("/api/v1/home").json()["shelves"]}
    assert "on_this_day" not in kinds
