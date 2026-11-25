from mutagen.flac import FLAC

from homebeat.library import lyrics
from tests.conftest import make_flac, track_ids

LRC = """[ar:Someone]
[ti:Song]
[offset:+200]
[00:12.00]First line
[00:15.50][01:02.30]Chorus line
[00:20.5]
[00:24.123]<00:24.12>Word <00:25.00>timed
"""


def test_parse_synced_lrc():
    synced, lines = lyrics.parse(LRC)
    assert synced
    # offset +200 means shown 200 ms earlier; repeated stamps expand; sorted by time
    assert [(ln["timeMs"], ln["text"]) for ln in lines] == [
        (11800, "First line"),
        (15300, "Chorus line"),
        (20300, ""),
        (23923, "Word timed"),
        (62100, "Chorus line"),
    ]


def test_parse_plain_keeps_section_labels_and_stanza_breaks():
    synced, lines = lyrics.parse("\n[Chorus: Singer]\nLa la\n\nVerse two\n\n")
    assert not synced
    assert [ln["text"] for ln in lines] == ["[Chorus: Singer]", "La la", "", "Verse two"]
    assert all(ln["timeMs"] is None for ln in lines)


def test_parse_empty():
    assert lyrics.parse("") is None
    assert lyrics.parse("  \n \n") is None
    assert lyrics.parse("[ar:x]\n[ti:y]\n") is None


def test_read_prefers_synced_embedded(tmp_path, base_flac):
    f = make_flac(base_flac, tmp_path / "a.flac", title="A",
                  unsyncedlyrics="plain words", lyrics="[00:01.00]timed words")
    got = lyrics.read(f)
    assert got.synced and got.source == "embedded"
    assert got.lines == [{"timeMs": 1000, "text": "timed words"}]


def test_read_sidecar_lrc_beats_unsynced_embedded(tmp_path, base_flac):
    f = make_flac(base_flac, tmp_path / "b.flac", title="B", unsyncedlyrics="plain words")
    (tmp_path / "b.lrc").write_text("[00:02.00]from sidecar\n", encoding="utf-8")
    got = lyrics.read(f)
    assert got.synced and got.source == "lrc_file"


def test_read_plain_embedded_and_none(tmp_path, base_flac):
    f = make_flac(base_flac, tmp_path / "c.flac", title="C", unsyncedlyrics="one\ntwo")
    got = lyrics.read(f)
    assert not got.synced and got.as_api("x")["text"] == "one\ntwo"
    g = make_flac(base_flac, tmp_path / "d.flac", title="D")
    assert lyrics.read(g) is None


def test_api_lyrics(client, conn, cfg):
    ids = track_ids(conn)
    path = conn.execute("SELECT path FROM tracks WHERE id = ?", (ids["Song 1"],)).fetchone()[0]
    f = FLAC(cfg.library_root / path)
    f["LYRICS"] = "[00:00.50]hello\n[00:03.00]world"
    f.save()

    r = client.get(f"/api/v1/tracks/{ids['Song 1']}/lyrics")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["trackId"] == ids["Song 1"] and body["synced"] and body["source"] == "embedded"
    assert body["lines"] == [{"timeMs": 500, "text": "hello"}, {"timeMs": 3000, "text": "world"}]
    assert body["text"] == "hello\nworld"

    assert client.get(f"/api/v1/tracks/{ids['Song 2']}/lyrics").status_code == 404   # no lyrics
    assert client.get("/api/v1/tracks/nope/lyrics").status_code == 404               # unknown track


def test_api_lyrics_requires_auth(anon, conn):
    ids = track_ids(conn)
    assert anon.get(f"/api/v1/tracks/{ids['Song 1']}/lyrics").status_code == 401


# ---------- 0.8.1: enhanced LRC word timings ----------

def test_enhanced_lrc_keeps_word_timings():
    from homebeat.library import lyrics as L

    synced, lines = L.parse("[00:12.00]<00:12.00>Hello <00:12.50>big <00:13.00>world <00:13.80>\n[00:15.00]plain line")
    assert synced
    assert lines[0]["text"] == "Hello big world"
    assert lines[0]["words"] == [
        {"timeMs": 12000, "text": "Hello "}, {"timeMs": 12500, "text": "big "}, {"timeMs": 13000, "text": "world"},
    ]
    assert "".join(w["text"] for w in lines[0]["words"]) == lines[0]["text"]
    assert "words" not in lines[1]


def test_enhanced_lrc_leading_text_offset_and_repeats():
    from homebeat.library import lyrics as L

    _, lines = L.parse("[offset:+500]\n[00:10.00][00:30.00]Oh <00:11.00>yeah")
    assert [ln["timeMs"] for ln in lines] == [9500, 29500]
    assert lines[0]["words"] == [{"timeMs": 9500, "text": "Oh "}, {"timeMs": 10500, "text": "yeah"}]
    assert lines[1]["words"] == [{"timeMs": 29500, "text": "Oh "}, {"timeMs": 30500, "text": "yeah"}]


def test_word_stamps_never_go_backwards():
    from homebeat.library import lyrics as L

    _, lines = L.parse("[00:20.00]<00:19.00>a <00:21.00>b")
    assert [w["timeMs"] for w in lines[0]["words"]] == [20000, 21000]
