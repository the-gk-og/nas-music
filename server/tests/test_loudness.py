"""ReplayGain / volume levelling: tags, ebur128 measurement, album gain, API, schema v5 migration."""
import math
from pathlib import Path

import pytest
from mutagen.flac import FLAC
from tests.conftest import make_flac, track_ids

from nasmusic import db
from nasmusic.db import migrations
from nasmusic.library import loudness, scanner, tags

SUMMARY = """
[Parsed_ebur128_0 @ 0x1] Summary:

  Integrated loudness:
    I:         -21.8 LUFS
    Threshold: -31.8 LUFS

  Loudness range:
    LRA:        20.0 LU

  Sample peak:
    Peak:      -18.1 dBFS
"""


def test_parse_ebur128():
    m = loudness.parse_ebur128(SUMMARY)
    assert m.lufs == -21.8
    assert abs(m.peak - 10 ** (-18.1 / 20)) < 1e-5
    assert m.gain_db == 3.8
    assert loudness.parse_ebur128("garbage") is None
    silent = loudness.parse_ebur128(SUMMARY.replace("-21.8 LUFS", "-inf LUFS", 1).replace("-18.1 dBFS", "-inf dBFS"))
    assert silent.lufs == -70.0 and silent.peak == 0.0
    assert silent.gain_db == loudness.MAX_GAIN_DB  # capped, silence isn't boosted by +52 dB


def test_album_values_energy_mean():
    # Two equal-length tracks at the same loudness -> album loudness is that loudness.
    g, p = loudness.album_values([(-10.0, 100, 0.9), (-10.0, 100, 0.5)])
    assert g == -8.0 and p == 0.9
    # Energy mean sits closer to the louder track than the arithmetic mean.
    g, _ = loudness.album_values([(-6.0, 100, None), (-16.0, 100, None)])
    lufs = -18 - g
    assert -9.0 < lufs < -8.0
    assert loudness.album_values([]) is None


def test_tag_reading_vorbis_and_r128(tmp_path, base_flac):
    p = make_flac(base_flac, tmp_path / "a.flac", title="A", replaygain_track_gain="-7.53 dB",
                  replaygain_track_peak="0.988", REPLAYGAIN_ALBUM_GAIN="-8,10 dB", replaygain_album_peak="1.02")
    t, _ = tags.read(p)
    assert (t.rg_track_gain, t.rg_track_peak, t.rg_album_gain, t.rg_album_peak) == (-7.53, 0.988, -8.10, 1.02)
    # Opus-style R128 gains (Q7.8 relative to -23 LUFS) when no REPLAYGAIN_* tags.
    p2 = make_flac(base_flac, tmp_path / "b.flac", title="B", r128_track_gain="-1792")
    t2, _ = tags.read(p2)
    assert t2.rg_track_gain == -7.0 + 5.0 and t2.rg_album_gain is None
    # Junk is ignored.
    p3 = make_flac(base_flac, tmp_path / "c.flac", title="C", replaygain_track_gain="loud")
    assert tags.read(p3)[0].rg_track_gain is None


def test_scan_reads_tags_and_job_measures_the_rest(cfg, conn, library, base_flac):
    make_flac(base_flac, library / "Tagged" / "01 t.flac", title="Tagged", album="Tagged", artist="X",
              replaygain_track_gain="-5.00 dB", replaygain_track_peak="0.5")
    scanner.scan(cfg, conn)
    rows = {r["title"]: r for r in conn.execute("SELECT * FROM tracks")}
    assert rows["Tagged"]["rg_source"] == "tags" and rows["Tagged"]["rg_track_gain"] == -5.0
    assert rows["Tagged"]["loudness_lufs"] == -13.0
    assert rows["Song 1"]["rg_source"] is None

    res = loudness.run(conn, cfg.library_root)
    assert res["measured"] == 5 and res["failed"] == 0 and res["remaining"] == 0
    s1 = conn.execute("SELECT * FROM tracks WHERE title = 'Song 1'").fetchone()
    assert s1["rg_source"] == "ebur128"
    # Base file: 440 Hz sine from ffmpeg's lavfi : about -21 LUFS, sample peak ~0.09.
    assert -24 < s1["loudness_lufs"] < -18
    assert math.isclose(s1["rg_track_gain"], -18 - s1["loudness_lufs"], abs_tol=0.01)
    assert 0.05 < s1["rg_track_peak"] < 0.2
    # Album gain: both songs of First Album are identical -> album gain == track gain.
    assert s1["rg_album_gain"] == s1["rg_track_gain"]
    # Tag-only album (no album gain tag) gets one derived from its track gain.
    tagged = conn.execute("SELECT * FROM tracks WHERE title = 'Tagged'").fetchone()
    assert tagged["rg_album_gain"] == -5.0 and tagged["rg_album_peak"] == 0.5
    # Nothing left to do on the next run.
    assert loudness.run(conn, cfg.library_root)["measured"] == 0

    # A changed file is re-levelled: tags removed -> measured again.
    f = FLAC(library / "Tagged" / "01 t.flac")
    del f["replaygain_track_gain"]
    f.save()
    import os
    os.utime(library / "Tagged" / "01 t.flac", (1, 1))
    scanner.scan(cfg, conn)
    assert conn.execute("SELECT rg_source FROM tracks WHERE title = 'Tagged'").fetchone()[0] is None
    assert loudness.run(conn, cfg.library_root)["measured"] == 1


def test_unreadable_file_marked_failed(cfg, conn, library):
    scanner.scan(cfg, conn)
    loudness.run(conn, cfg.library_root, ffmpeg="false")  # every measurement fails
    assert {r[0] for r in conn.execute("SELECT rg_source FROM tracks")} == {"failed"}
    assert loudness.run(conn, cfg.library_root)["pending"] == 0


def test_run_respects_limit(cfg, conn):
    scanner.scan(cfg, conn)
    res = loudness.run(conn, cfg.library_root, limit=2)
    assert res["measured"] == 2 and res["remaining"] == 3


def test_api_exposes_replay_gain(client, conn, cfg):
    ids = track_ids(conn)
    t = client.get(f"/api/v1/library/tracks/{ids['Song 1']}").json()
    assert "replayGain" not in t
    loudness.run(conn, cfg.library_root)
    t = client.get(f"/api/v1/library/tracks/{ids['Song 1']}").json()
    rg = t["replayGain"]
    assert set(rg) == {"trackGain", "trackPeak", "albumGain", "albumPeak"}
    assert rg["albumGain"] == rg["trackGain"]
    album = client.get(f"/api/v1/library/albums/{t['albumId']}").json()
    assert all("replayGain" in x for x in album["tracks"])


def test_loudness_job_registered(cfg):
    from nasmusic.app.scheduler import JOB_NAMES, Jobs

    assert "loudness" in JOB_NAMES
    rec = Jobs(cfg).run("loudness")
    assert rec["ok"], rec


V3_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v3.sql").read_text()


def test_v4_to_v5(tmp_path):
    from tests.test_migration_v4 import make_v3

    path = tmp_path / "nasmusic.db"
    conn = make_v3(path)
    migrations.migrate_v3_to_v4(conn, db.schema_sql(), make_backup=False)
    assert migrations.current_version(conn) == 4
    conn.close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tracks)")}
    assert {c for c, _ in migrations.V5_TRACK_COLUMNS} <= cols
    assert conn.execute("SELECT title, rg_source FROM tracks").fetchone()[:] == ("T", None)
    assert len(list(tmp_path.glob("nasmusic.db.v4-backup-*"))) == 1
    db.init(conn)  # idempotent
    assert len(list(tmp_path.glob("*-backup-*"))) == 1


@pytest.mark.parametrize("raw", ["+3.2 dB", "3.2", "3.20 dB "])
def test_float_parsing(raw):
    assert tags._float(raw) == 3.2
