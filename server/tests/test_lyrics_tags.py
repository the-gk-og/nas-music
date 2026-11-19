"""Every place downloaders/taggers put lyrics (docs: GET /tracks/{id}/lyrics). One fixture per location."""
import shutil
import subprocess
from pathlib import Path

import pytest
from mutagen.apev2 import APEv2
from mutagen.flac import FLAC
from mutagen.id3 import COMM, ID3, SYLT, TXXX, USLT
from mutagen.mp4 import MP4, MP4FreeForm
from mutagen.oggopus import OggOpus

from nasmusic.library import lyrics

# The owner's real files (SpotiFLAC-Mobile) look like this.
SPOTIFLAC = """[ti:Clarity]
[ar:Zedd, Foxes]
[al:Clarity]
[by:SpotiFLAC-Mobile (source: LRCLIB (cached fallback))]
[00:12.34]High dive into frozen waves
[00:16.80]Where the past comes back to life
[00:21.05]
[00:24.50]Fight fear for the selfish pain"""
LRC = "[00:01.00]one\n[00:02.50]two\n[00:04.00]three"
PLAIN = "first line\nsecond line"


def _gen(tmp_path_factory, ext: str, codec: list[str]) -> Path:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("lyr") / f"base{ext}"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1",
                    *codec, str(out)], check=True)
    return out


@pytest.fixture(scope="module")
def mp3(tmp_path_factory):
    return _gen(tmp_path_factory, ".mp3", ["-c:a", "libmp3lame", "-b:a", "64k"])


@pytest.fixture(scope="module")
def m4a(tmp_path_factory):
    return _gen(tmp_path_factory, ".m4a", ["-c:a", "aac", "-b:a", "64k"])


@pytest.fixture(scope="module")
def opus(tmp_path_factory):
    return _gen(tmp_path_factory, ".opus", ["-c:a", "libopus", "-b:a", "32k"])


def copy(src: Path, dest_dir: Path, name: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    p = dest_dir / name
    shutil.copyfile(src, p)
    return p


# ---------- FLAC / Vorbis ----------


def test_flac_spotiflac_lyrics_and_unsyncedlyrics(base_flac, tmp_path):
    p = copy(base_flac, tmp_path, "clarity.flac")
    f = FLAC(p)
    f["LYRICS"] = SPOTIFLAC
    f["UNSYNCEDLYRICS"] = SPOTIFLAC
    f.save()
    got = lyrics.read(p)
    assert got.source == "embedded" and got.synced is True
    assert [ln["text"] for ln in got.lines][:2] == ["High dive into frozen waves", "Where the past comes back to life"]
    assert got.lines[0]["timeMs"] == 12340
    assert not any("SpotiFLAC" in ln["text"] or ln["text"].startswith("[") for ln in got.lines)


@pytest.mark.parametrize("key", ["Lyrics", "lyrics", "UnSyncedLyrics", "SYNCEDLYRICS", "LYRICS-ENG", "lyric"])
def test_vorbis_keys_any_casing(base_flac, tmp_path, key):
    p = copy(base_flac, tmp_path, "x.flac")
    f = FLAC(p)
    f[key] = LRC
    f.save()
    got = lyrics.read(p)
    assert got and got.synced and len(got.lines) == 3


def test_ogg_opus(opus, tmp_path):
    p = copy(opus, tmp_path, "x.opus")
    f = OggOpus(p)
    f["LYRICS"] = PLAIN
    f.save()
    got = lyrics.read(p)
    assert got.synced is False and [ln["text"] for ln in got.lines] == ["first line", "second line"]


# ---------- MP3 / ID3 ----------


def _id3(p: Path, *frames) -> None:
    tags = ID3()
    for fr in frames:
        tags.add(fr)
    tags.save(p)


@pytest.mark.parametrize("frame", [
    USLT(encoding=3, lang="eng", desc="", text=LRC),
    USLT(encoding=3, lang="XXX", desc="Lyrics", text=LRC),
    USLT(encoding=1, lang="deu", desc="from spotdl", text=LRC),
    TXXX(encoding=3, desc="LYRICS", text=[LRC]),
    TXXX(encoding=3, desc="UNSYNCEDLYRICS", text=[LRC]),
    TXXX(encoding=3, desc="SyncedLyrics", text=[LRC]),
    COMM(encoding=3, lang="eng", desc="", text=[LRC]),
    SYLT(encoding=3, lang="eng", format=2, type=1, desc="", text=[("one", 1000), ("two", 2500), ("three", 4000)]),
], ids=["uslt-eng", "uslt-xxx-desc", "uslt-utf16", "txxx-lyrics", "txxx-unsynced", "txxx-synced", "comm-lrc", "sylt"])
def test_mp3_frames(mp3, tmp_path, frame):
    p = copy(mp3, tmp_path, "x.mp3")
    _id3(p, frame)
    got = lyrics.read(p)
    assert got is not None and got.synced is True
    assert [ln["timeMs"] for ln in got.lines] == [1000, 2500, 4000]
    assert [ln["text"] for ln in got.lines] == ["one", "two", "three"]


def test_mp3_plain_comment_is_not_lyrics(mp3, tmp_path):
    p = copy(mp3, tmp_path, "x.mp3")
    _id3(p, COMM(encoding=3, lang="eng", desc="", text=["Ripped by me"]))
    assert lyrics.read(p) is None


def test_mp3_synced_preferred_over_plain_uslt(mp3, tmp_path):
    p = copy(mp3, tmp_path, "x.mp3")
    _id3(p, USLT(encoding=3, lang="eng", desc="", text=PLAIN), TXXX(encoding=3, desc="SYNCEDLYRICS", text=[LRC]))
    assert lyrics.read(p).synced is True


def test_mp3_apev2_lyrics(mp3, tmp_path):
    p = copy(mp3, tmp_path, "x.mp3")
    ape = APEv2()
    ape["Lyrics"] = LRC
    ape.save(p)
    got = lyrics.read(p)
    assert got and got.synced and len(got.lines) == 3


# ---------- MP4 ----------


def test_m4a_lyr_atom(m4a, tmp_path):
    p = copy(m4a, tmp_path, "x.m4a")
    f = MP4(p)
    f["\xa9lyr"] = [SPOTIFLAC]
    f.save()
    got = lyrics.read(p)
    assert got.synced and got.lines[0]["text"] == "High dive into frozen waves"


def test_m4a_freeform_lyrics(m4a, tmp_path):
    p = copy(m4a, tmp_path, "x.m4a")
    f = MP4(p)
    f["----:com.apple.iTunes:LYRICS"] = [MP4FreeForm(LRC.encode())]
    f.save()
    got = lyrics.read(p)
    assert got and got.synced and len(got.lines) == 3


# ---------- sidecars ----------


@pytest.mark.parametrize("name", ["Song.lrc", "Song.LRC", "song.Lrc", "SONG.lrc"])
def test_lrc_sidecar_any_case(base_flac, tmp_path, name):
    p = copy(base_flac, tmp_path, "Song.flac")
    (tmp_path / name).write_text(LRC)
    got = lyrics.read(p)
    assert got.source == "lrc_file" and got.synced


def test_txt_sidecar_and_lrc_preferred(base_flac, tmp_path):
    p = copy(base_flac, tmp_path, "Song.flac")
    (tmp_path / "Song.TXT").write_text(PLAIN)
    got = lyrics.read(p)
    assert got.source == "lrc_file" and not got.synced and got.lines[0]["text"] == "first line"
    (tmp_path / "song.lrc").write_text(LRC)
    assert lyrics.read(p).synced is True


def test_sidecar_utf16_and_cp1252(base_flac, tmp_path):
    p = copy(base_flac, tmp_path, "S.flac")
    (tmp_path / "S.lrc").write_bytes("[00:01.00]café\n[00:02.00]naïve".encode("utf-16"))
    assert lyrics.read(p).lines[0]["text"] == "café"
    (tmp_path / "S.lrc").unlink()
    (tmp_path / "S.lrc").write_bytes("[00:01.00]déjà vu".encode("cp1252"))
    assert lyrics.read(p).lines[0]["text"] == "déjà vu"


def test_embedded_beats_sidecar_when_both_synced(base_flac, tmp_path):
    p = copy(base_flac, tmp_path, "S.flac")
    f = FLAC(p)
    f["LYRICS"] = LRC
    f.save()
    (tmp_path / "S.lrc").write_text("[00:00.50]other")
    assert lyrics.read(p).source == "embedded"
    assert set(lyrics.read_all(p)) == {"embedded", "lrc_file"}
