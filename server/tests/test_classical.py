"""Classical music (schema v10): work/movement from tags or titles, composers and works API, backfill."""
import pytest

from homebeat.library import classical, scanner
from tests.conftest import make_flac


@pytest.mark.parametrize("title,composer,tags,want", [
    ("Symphony No. 5 in C minor, Op. 67: I. Allegro con brio", "Beethoven", {},
     ("Symphony No. 5 in C minor, Op. 67", "Allegro con brio", 1)),
    ("Piano Sonata No. 14: III. Presto agitato", "Beethoven", {}, ("Piano Sonata No. 14", "Presto agitato", 3)),
    ("Goldberg Variations: 12. Variatio 11", "Bach", {}, ("Goldberg Variations", "Variatio 11", 12)),
    # no composer: a pop title with a colon is left alone
    ("Interlude: I. Love You", None, {}, ("", None, None)),
    ("Just a song", "Someone", {}, ("", None, None)),
    # WORK tag wins; the title repeating the work is trimmed and the number read
    ("Symphony No. 9: IV. Presto", "Beethoven", {"work": "Symphony No. 9 in D minor"},
     ("Symphony No. 9 in D minor", "Presto", 4)),
    ("Symphony No. 9 in D minor: II. Molto vivace", None, {"work": "Symphony No. 9 in D minor"},
     ("Symphony No. 9 in D minor", "Molto vivace", 2)),
    ("whatever", None, {"work": "Requiem", "movement": "Dies irae", "movement_no": 2}, ("Requiem", "Dies irae", 2)),
])
def test_parse(title, composer, tags, want):
    m = classical.parse(title, composer, tags.get("work"), tags.get("movement"), tags.get("movement_no"))
    assert (m.work, m.movement, m.movement_no) == want


def test_roman():
    assert [classical.roman(s) for s in ("I", "IV", "IX", "XIV", "XL", "Q", "")] == [1, 4, 9, 14, 40, None, None]


def test_work_id_round_trip():
    wid = classical.work_id("album1", "Requiem")
    assert classical.split_work_id(wid) == ("album1", wid[-12:])
    assert classical.split_work_id("nonsense") is None


def _classical_album(cfg, base_flac):
    d = cfg.library_root / "Karajan" / "Beethoven 5 and 6"
    for i, mv in enumerate(["Allegro con brio", "Andante con moto"], 1):
        make_flac(base_flac, d / f"0{i}.flac", title=f"Symphony No. 5 in C minor, Op. 67: {'I' * i}. {mv}",
                  artist="Berliner Philharmoniker", albumartist="Herbert von Karajan", album="Beethoven 5 and 6",
                  composer="Ludwig van Beethoven", tracknumber=i)
    make_flac(base_flac, d / "03.flac", title="Erwachen heiterer Empfindungen", artist="Berliner Philharmoniker",
              albumartist="Herbert von Karajan", album="Beethoven 5 and 6", composer="Ludwig van Beethoven",
              work="Symphony No. 6 in F major, Op. 68 \"Pastoral\"", movementname="Erwachen heiterer Empfindungen",
              movement=1, tracknumber=3)
    return scanner.scan(cfg, _conn(cfg))


def _conn(cfg):
    from homebeat.core import open_db
    return open_db(cfg)


def test_scan_works_and_api(cfg, base_flac, client, conn):
    _classical_album(cfg, base_flac)
    comps = client.get("/api/v1/library/composers").json()
    assert comps == [{"name": "Ludwig van Beethoven", "workCount": 2, "albumCount": 1, "trackCount": 3}]
    works = client.get("/api/v1/library/works", params={"composer": "ludwig van beethoven"}).json()
    assert [(w["title"], w["trackCount"], w["artist"]) for w in works] == [
        ("Symphony No. 5 in C minor, Op. 67", 2, "Herbert von Karajan"),
        ("Symphony No. 6 in F major, Op. 68 \"Pastoral\"", 1, "Herbert von Karajan")]
    w = client.get(f"/api/v1/library/works/{works[0]['id']}").json()
    assert [(t["movementNo"], t["movement"]) for t in w["tracks"]] == [(1, "Allegro con brio"), (2, "Andante con moto")]
    assert w["tracks"][0]["composer"] == "Ludwig van Beethoven" and w["tracks"][0]["workId"] == works[0]["id"]
    # album order
    album_id = w["albumId"]
    assert [x["title"] for x in client.get("/api/v1/library/works", params={"albumId": album_id}).json()] == \
        [works[0]["title"], works[1]["title"]]
    album = client.get(f"/api/v1/library/albums/{album_id}").json()
    assert [t.get("work") for t in album["tracks"]] == [works[0]["title"]] * 2 + [works[1]["title"]]
    # non-classical tracks carry no work fields
    other = client.get("/api/v1/library/tracks").json()
    assert all("work" not in t for t in other if t["album"] != "Beethoven 5 and 6")
    assert client.get("/api/v1/library/works/nope").status_code == 404
    assert client.get(f"/api/v1/library/works/{album_id}.000000000000").status_code == 404


def test_backfill_reads_tracks_from_before_v9(cfg, base_flac, client, conn):
    _classical_album(cfg, base_flac)
    conn.execute("UPDATE tracks SET work = NULL, movement = NULL, movement_no = NULL")
    n = classical.backfill(cfg, conn)
    assert n == conn.execute("SELECT COUNT(*) FROM tracks WHERE missing != 1").fetchone()[0]
    rows = dict(conn.execute("SELECT title, work FROM tracks").fetchall())
    assert rows["Erwachen heiterer Empfindungen"] == "Symphony No. 6 in F major, Op. 68 \"Pastoral\""
    assert rows["Song 1"] == ""
    assert classical.backfill(cfg, conn) == 0


def test_classical_setting_is_validated(client):
    r = client.put("/api/v1/me/settings", json={"settings": {"library.classical": True}})
    assert r.json()["settings"]["library.classical"] is True
    assert client.put("/api/v1/me/settings", json={"settings": {"library.classical": "yes"}}).status_code == 422
