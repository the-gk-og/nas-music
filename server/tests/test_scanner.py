import os
import shutil

from tests.conftest import make_flac, png_bytes

from nasmusic.library import art, queries, scanner


def test_tags_ids_and_grouping(cfg, conn):
    res = scanner.scan(cfg, conn)
    assert (res.added, res.errors) == (5, 0)

    rows = {r["title"]: r for r in conn.execute("SELECT * FROM tracks")}
    assert set(rows) == {"Song 1", "Song 2", "Opening", "Closing", "untagged"}
    s1 = rows["Song 1"]
    assert s1["id"] == scanner.sha1("Artist One/First Album/01 Song 1.flac")
    assert (s1["track_no"], s1["year"], s1["genre"]) == (1, 2001, "Rock")
    assert abs(s1["duration_s"] - 2.0) < 0.05
    assert s1["flac_bytes"] == os.path.getsize(cfg.library_root / s1["path"])

    op = rows["Opening"]
    assert (op["track_no"], op["disc_no"], op["mbid"]) == (1, 1, "11111111-1111-1111-1111-111111111111")
    # Grouped by album artist (MBID-based id), track artist kept separately.
    band_id = "mb:22222222-2222-2222-2222-222222222222"
    assert op["album_id"] == rows["Closing"]["album_id"]
    album = conn.execute("SELECT * FROM albums WHERE id = ?", (op["album_id"],)).fetchone()
    assert album["artist_id"] == band_id
    assert album["mbid"] == "33333333-3333-3333-3333-333333333333"
    assert album["year"] == 1999
    assert conn.execute("SELECT name FROM artists WHERE id = ?", (op["artist_id"],)).fetchone()[0] == \
        "The Band feat. X"

    # Untagged: title from file name, album from folder, Unknown Artist.
    un = rows["untagged"]
    al = conn.execute("SELECT title FROM albums WHERE id = ?", (un["album_id"],)).fetchone()
    assert al["title"] == "Loose"

    names = [a["name"] for a in queries.artists(conn, 0, 100)]
    assert names == ["Artist One", "The Band", "Unknown Artist"]  # "The" ignored when sorting
    assert scanner.artist_id("  artist   ONE ", None) == scanner.artist_id("Artist One", None)


def test_ids_are_stable_across_rescans(cfg, conn):
    scanner.scan(cfg, conn)
    before = {r["id"]: (r["album_id"], r["artist_id"]) for r in conn.execute("SELECT * FROM tracks")}
    scanner.scan(cfg, conn, full=True)
    after = {r["id"]: (r["album_id"], r["artist_id"]) for r in conn.execute("SELECT * FROM tracks")}
    assert before == after


def test_cover_art_folder_and_embedded(cfg, conn):
    scanner.scan(cfg, conn)
    albums = {a["title"]: a for a in queries.albums(conn, cfg.data_dir, None, "name", 0, 100)}
    assert albums["First Album"]["hasArt"] and albums["Second"]["hasArt"]
    assert not albums["Loose"]["hasArt"]
    first = conn.execute("SELECT art_path FROM albums WHERE title = 'First Album'").fetchone()[0]
    assert first == "Artist One/First Album/cover.jpg"
    second_id = albums["Second"]["id"]
    assert art.src_path(cfg.data_dir, second_id).exists()  # extracted from embedded picture

    out = art.resized(cfg.data_dir, cfg.library_root, second_id, None, 200)
    from PIL import Image

    with Image.open(out) as im:
        assert im.format == "JPEG" and max(im.size) == 256  # bucketed


def test_incremental_and_missing(cfg, conn, base_flac):
    scanner.scan(cfg, conn)
    res = scanner.scan(cfg, conn)
    assert (res.added, res.updated, res.unchanged, res.missing) == (0, 0, 5, 0)

    # Retag one file (mtime changes) and add a new one.
    p = cfg.library_root / "Artist One/First Album/02 Song 2.flac"
    make_flac(base_flac, p, title="Song Two", artist="Artist One", albumartist="Artist One",
              album="First Album", tracknumber=2)
    os.utime(p, (1e9, 1e9))
    make_flac(base_flac, cfg.library_root / "Artist One/First Album/03 Song 3.flac", title="Song 3",
              artist="Artist One", albumartist="Artist One", album="First Album", tracknumber=3)
    res = scanner.scan(cfg, conn)
    assert (res.added, res.updated, res.unchanged) == (1, 1, 4)
    assert conn.execute("SELECT title FROM tracks WHERE path LIKE '%02 Song 2.flac'").fetchone()[0] == "Song Two"

    # A starred track that vanishes is kept (with its star) but marked missing and hidden.
    tid = scanner.track_id("The Band/Second/02 Closing.flac")
    conn.execute("UPDATE tracks SET starred_at = '2026-01-01T00:00:00Z' WHERE id = ?", (tid,))
    moved = cfg.library_root.parent / "closing.bak"
    shutil.move(cfg.library_root / "The Band/Second/02 Closing.flac", moved)
    res = scanner.scan(cfg, conn)
    assert res.missing == 1
    row = conn.execute("SELECT missing, starred_at FROM tracks WHERE id = ?", (tid,)).fetchone()
    assert row["missing"] == 1 and row["starred_at"]
    assert queries.track(conn, tid) is None
    assert queries.favourites(conn) == []

    # It comes back.
    shutil.move(moved, cfg.library_root / "The Band/Second/02 Closing.flac")
    res = scanner.scan(cfg, conn)
    assert res.restored == 1 and res.missing == 0
    assert queries.track(conn, tid)["starred"] is True


def test_whole_album_removed_hides_album_and_artist(cfg, conn):
    scanner.scan(cfg, conn)
    shutil.rmtree(cfg.library_root / "Artist One")
    scanner.scan(cfg, conn)
    assert "Artist One" not in [a["name"] for a in queries.artists(conn, 0, 100)]
    assert "First Album" not in [a["title"] for a in queries.albums(conn, cfg.data_dir, None, "name", 0, 100)]


def test_folder_art_added_later_and_embedded_changes(cfg, conn, base_flac):
    scanner.scan(cfg, conn)
    loose = conn.execute("SELECT id FROM albums WHERE title = 'Loose'").fetchone()[0]
    assert art.none_marker(cfg.data_dir, loose).exists()
    from PIL import Image

    Image.new("RGB", (300, 300)).save(cfg.library_root / "Loose" / "folder.png")
    scanner.scan(cfg, conn)
    assert conn.execute("SELECT art_path FROM albums WHERE id = ?", (loose,)).fetchone()[0] == "Loose/folder.png"
    # Retag with an embedded picture and remove the folder image -> embedded art is picked up.
    (cfg.library_root / "Loose" / "folder.png").unlink()
    make_flac(base_flac, cfg.library_root / "Loose" / "untagged.flac", picture=png_bytes((0, 255, 0)))
    scanner.scan(cfg, conn)
    assert conn.execute("SELECT art_path FROM albums WHERE id = ?", (loose,)).fetchone()[0] is None
    assert art.src_path(cfg.data_dir, loose).exists()


def test_corrupt_file_is_reported_not_fatal(cfg, conn):
    (cfg.library_root / "broken.flac").write_bytes(b"not audio at all")
    res = scanner.scan(cfg, conn)
    assert res.errors == 1 and res.added == 5
    assert res.error_paths == ["broken.flac"]


def test_scan_speed_many_files(tmp_path, base_flac):
    """600 files scan quickly, and an unchanged rescan is near-instant (mtime only)."""
    from nasmusic import config
    from nasmusic.core import init_db, open_db

    root = tmp_path / "big"
    src = make_flac(base_flac, tmp_path / "tmpl.flac", title="T", artist="A", album="B")
    for a in range(20):
        for t in range(30):
            d = root / f"Artist {a}" / f"Album {a}"
            d.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, d / f"{t:02d}.flac")
    cfg = config.from_dict({"library_root": str(root), "data_dir": str(tmp_path / "d")})
    init_db(cfg)
    conn = open_db(cfg)
    first = scanner.scan(cfg, conn)
    again = scanner.scan(cfg, conn)
    assert first.added == 600 and again.unchanged == 600
    assert first.duration_s < 15 and again.duration_s < 2
