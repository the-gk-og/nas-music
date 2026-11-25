"""Radio DJ voice: intro scripts, the Piper engine's cache/download, and /dj/* + /stream/dj/*."""
import io
import json
import math
import shutil
import struct
import wave

import httpx
import pytest

from homebeat.djvoice import script, tts


def fake_wav(text: str, seconds: float = 0.6) -> bytes:
    sr = 22050
    n = int(sr * seconds)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i * 0.05))) for i in range(n)))
    return buf.getvalue()


# ---------------------------------------------------------------- script

def test_speakable_cleans_titles():
    assert script.speakable("Paranoid Android (2017 Remaster)") == "Paranoid Android"
    assert script.speakable("Song - 2011 Remastered") == "Song"
    assert script.speakable("Get Lucky (feat. Pharrell Williams)") == "Get Lucky featuring Pharrell Williams"
    assert script.speakable("Simon & Garfunkel") == "Simon and Garfunkel"
    assert script.speakable("()") == "()"  # never empty


def test_bio_sentences_drop_brackets_and_long_ones():
    bio = ("Radiohead (/ˈreɪdioʊhɛd/) are an English rock band formed in Abingdon, Oxfordshire, in 1985. "
           "The band consists of Thom Yorke, brothers Jonny Greenwood and Colin Greenwood, Ed O'Brien and Philip "
           "Selway, who have played together since they met at school and have released many records over decades "
           "of touring around the world.")
    s = script.bio_sentences(bio)
    assert s == ["Radiohead are an English rock band formed in Abingdon, Oxfordshire, in 1985."]
    assert script.bio_sentences(None) == []


def test_facts_from_info():
    data = {"type": "Group", "beginArea": "Abingdon", "yearsActive": {"from": 1985}, "genres": ["rock", "alternative"],
            "bio": {"text": "Radiohead are an English rock band formed in Abingdon in 1985."}}
    f = script.facts_from_info("Radiohead", data)
    assert f.sentences[0].startswith("Radiohead are an English rock band")
    assert "Radiohead got together in Abingdon back in 1985." in f.sentences
    assert "Expect some rock and alternative." in f.sentences
    assert script.facts_from_info("X", None).sentences == []
    solo = script.facts_from_info("Kate Bush", {"type": "Person", "area": "United Kingdom"})
    assert solo.sentences == ["Kate Bush is from United Kingdom."]


def test_write_is_deterministic_and_varies():
    song = script.Song("Karma Police", "Radiohead", "OK Computer", 1997, "a1")
    prev = script.Song("Hyperballad", "Björk", "Post", 1995, "a2")
    facts = script.ArtistFacts(["Radiohead are an English rock band."])
    a = script.write(song, prev, station="Radiohead radio", position=3, facts=facts, seed=42)
    b = script.write(song, prev, station="Radiohead radio", position=3, facts=facts, seed=42)
    assert a == b and "Karma Police" in a and len(a) <= script.MAX_TEXT_CHARS
    texts = {script.write(song, prev, station=None, position=3, facts=facts, seed=s) for s in range(40)}
    assert len(texts) > 5
    assert any("Hyperballad" in t for t in texts)          # back-announces sometimes
    assert any("English rock band" in t for t in texts)    # uses the fact sometimes
    opener = script.write(song, None, station="Your Indie radio", position=0, facts=facts, seed=1)
    assert "Your Indie radio" in opener
    same = script.write(song, script.Song("Creep", "Radiohead", artist_id="a1"), station=None, position=4,
                        facts=facts, seed=7)
    assert "Radiohead" in same and "Karma Police" in same


def test_clock_and_cadence():
    assert script.clock(16, 3) == "just after four"
    assert script.clock(9, 30) == "half past nine"
    assert script.clock(23, 55) == "coming up to midnight"
    assert script.clock(11, 52) == "coming up to noon"
    assert script.clock(14, 20) == "20 minutes past two"
    assert [p for p in range(8) if script.speaks_at(p, 3)] == [0, 3, 6]
    assert all(script.speaks_at(p, 1) for p in range(5))


# ---------------------------------------------------------------- engine

@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg")
def test_engine_caches_clips(cfg):
    calls = []

    def synth(text):
        calls.append(text)
        return fake_wav(text)

    eng = tts.Engine(cfg, synth=synth)
    assert eng.status()["state"] == "ready"
    cid, ms = eng.speak("Hello there.")
    assert 600 <= ms < 1000 and tts.CLIP_ID_RE.match(cid)
    p = eng.clip_path(cid)
    assert p is not None and p.read_bytes()[:4] == b"OggS"
    assert eng.speak("Hello there.") == (cid, ms) and calls == ["Hello there."]   # cached
    assert eng.clip_path("../../etc/passwd") is None and eng.clip_path("0" * 32) is None
    eng.speak("Another one.")
    assert eng.prune(max_files=1) == 1 and len(list(eng.clips_dir.glob("*.ogg"))) == 1


def test_engine_status_without_piper(cfg, monkeypatch):
    monkeypatch.setattr(tts, "piper_installed", lambda: False)
    eng = tts.Engine(cfg)
    assert eng.status()["state"] == "unavailable"
    assert eng.speak("hi") is None
    cfg.dj_voice_enabled = False
    assert eng.status()["state"] == "disabled"


def test_voice_download(cfg):
    seen = []

    def handler(req: httpx.Request):
        seen.append(str(req.url))
        if req.url.path.endswith(".onnx.json"):
            return httpx.Response(200, json={"audio": {"sample_rate": 22050}})
        return httpx.Response(200, content=b"onnx-bytes")

    eng = tts.Engine(cfg, transport=httpx.MockTransport(handler))
    eng.download()
    assert seen[0].startswith("https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/cori/medium/"
                              "en_GB-cori-medium.onnx")
    assert eng.model_path().read_bytes() == b"onnx-bytes"
    assert json.loads((eng.model_path().parent / "en_GB-cori-medium.onnx.json").read_text())["audio"]
    cfg.dj_voice = "not a voice"
    with pytest.raises(ValueError):
        eng.download()
    assert tts.voice_parts("en_US-ljspeech-high") == {"lang": "en_US", "family": "en", "name": "ljspeech",
                                                       "quality": "high"}


# ---------------------------------------------------------------- API

@pytest.fixture
def dj(app):
    eng = tts.Engine(app.state.ctx.cfg, synth=fake_wav)
    app.state.ctx.dj_voice = eng
    return eng


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg")
def test_intro_api(client, scanned, dj):
    from tests.conftest import track_ids

    ids = list(track_ids(scanned).values())
    st = client.get("/api/v1/dj/status").json()
    assert st["state"] == "ready" and st["enabled"] is False and st["every"] == 3
    body = {"trackId": ids[1], "previousTrackId": ids[0], "position": 3}
    assert client.post("/api/v1/dj/intro", json=body).status_code == 204          # not switched on
    assert client.put("/api/v1/me/settings", json={"settings": {"dj.every": 0}}).status_code == 422
    assert client.put("/api/v1/me/settings", json={"settings": {"dj.voice": "yes"}}).status_code == 422
    client.put("/api/v1/me/settings", json={"settings": {"dj.voice": True, "dj.every": 3}})
    r = client.post("/api/v1/dj/intro", json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["url"] == f"stream/dj/{d['clipId']}" and d["durationMs"] > 0 and d["text"]
    assert client.post("/api/v1/dj/intro", json=body).json() == d                  # same spot, same clip
    assert client.post("/api/v1/dj/intro", json={**body, "position": 4}).status_code == 204   # every 3rd
    assert client.post("/api/v1/dj/intro", json={**body, "position": 4, "force": True}).status_code == 200
    a = client.get(f"/api/v1/{d['url']}")
    assert a.status_code == 200 and a.headers["content-type"] == "audio/ogg" and a.content[:4] == b"OggS"
    assert client.get("/api/v1/stream/dj/" + "f" * 32).status_code == 404
    assert client.post("/api/v1/dj/intro", json={**body, "trackId": "nope"}).status_code == 404
    # A radio queue gives the opener its station name.
    q = client.post("/api/v1/queues", json={"name": "Song 1 radio", "trackIds": ids[:3],
                                            "source": {"type": "radio"}})
    assert q.status_code in (200, 201), q.text
    first = client.post("/api/v1/dj/intro", json={"trackId": ids[0], "queueId": q.json()["id"], "position": 0})
    assert "Song 1 radio" in first.json()["text"]


def test_intro_api_voice_not_ready(client, scanned, app, monkeypatch):
    from tests.conftest import track_ids

    monkeypatch.setattr(tts, "piper_installed", lambda: False)
    client.put("/api/v1/me/settings", json={"settings": {"dj.voice": True}})
    tid = next(iter(track_ids(scanned).values()))
    r = client.post("/api/v1/dj/intro", json={"trackId": tid, "position": 0})
    assert r.status_code == 503
    assert client.get("/api/v1/dj/status").json()["state"] == "unavailable"


def test_clip_needs_auth(anon):
    assert anon.get("/api/v1/stream/dj/" + "a" * 32).status_code == 401
