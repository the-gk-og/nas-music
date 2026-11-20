"""Voice control: intent parsing, fuzzy matching, device targeting, voice tokens and the command endpoint."""
from pathlib import Path

import pytest

from nasmusic import db
from nasmusic.db import migrations
from nasmusic.voice import match, service
from nasmusic.voice.parse import from_slots, parse
from tests.conftest import track_ids, user_client
from tests.test_cast import FakeSpeaker, wait_for

API = "/api/v1"


# ---------- parsing ----------


@pytest.mark.parametrize("text, name, query, kind, artist", [
    ("Play my Pop radio", "play", "pop", "radio", None),
    ("play something chill", "play", "chill", "vibe", None),
    ("Play AJR", "play", "ajr", "any", None),
    ("play the album Neotheater by AJR", "play", "neotheater", "album", "ajr"),
    ("play Bang by AJR", "play", "bang", "song", "ajr"),
    ("play the song Bang", "play", "bang", "song", None),
    ("play my workout playlist", "play", "workout", "playlist", None),
    ("play the playlist called road trip", "play", "road trip", "playlist", None),
    ("play songs by Queen", "play", "queen", "artist", None),
    ("play my daily mix", "play", "daily mix", "mix", None),
    ("play radio for Billie Eilish", "play", "billie eilish", "radio", None),
    ("play some music", "play", "", "moment", None),
    ("play music for studying", "play", "studying", "vibe", None),
    ("hey jarvis please play some jazz", "play", "jazz", "vibe", None),
])
def test_parse_play(text, name, query, kind, artist):
    it = parse(text)
    assert (it.name, it.query, it.kind, it.artist) == (name, query, kind, artist)


@pytest.mark.parametrize("text, name", [
    ("skip", "next"), ("next song", "next"), ("skip this track", "next"),
    ("pause the music", "pause"), ("stop", "pause"), ("pause", "pause"),
    ("resume", "resume"), ("play", "resume"), ("continue playing", "resume"),
    ("go back", "previous"), ("previous track", "previous"),
    ("what's playing?", "now_playing"), ("what song is this", "now_playing"), ("who is this", "now_playing"),
    ("what am I listening to", "now_playing"),
    ("open the pod bay doors", "unknown"), ("", "unknown"),
])
def test_parse_controls(text, name):
    assert parse(text).name == name


def test_parse_volume():
    assert parse("set the volume to 40%").volume == 0.4
    assert parse("volume 35 percent").volume == 0.35
    assert parse("volume 5").volume == 0.5           # out of ten
    assert parse("set volume to forty").volume == 0.4
    assert parse("turn it up").volume_step == 0.1
    assert parse("quieter").volume_step == -0.1


def test_parse_device_phrases():
    it = parse("Play my Pop radio in the bedroom")
    assert it.splits[0] == ("my pop radio", "bedroom")
    it = parse("play something chill in Stacey's room")
    assert it.splits[0] == ("something chill", "stacey's room")
    it = parse("pause the music in the kitchen")
    assert (it.name, it.device) == ("pause", "kitchen")
    it = parse("resume in the kitchen")
    assert (it.name, it.device) == ("resume", "kitchen")
    # Every preposition is a candidate, the last one first (the resolver picks the one naming a device).
    it = parse("play songs in the key of life on the kitchen speaker")
    assert [d for _, d in it.splits] == ["kitchen speaker", "key of life on the kitchen speaker"]


def test_slots_from_home_assistant():
    it = from_slots("NasPlay", {"playlist": "Pop", "device": "Bedroom"})
    assert (it.name, it.kind, it.query, it.device) == ("play", "playlist", "pop", "bedroom")
    it = from_slots("NasPlay", {"query": "something chill"})
    assert (it.kind, it.query) == ("vibe", "chill")
    it = from_slots("NasPlay", {"query": "pop radio in the bedroom"})
    assert it.splits[0] == ("pop radio", "bedroom")
    assert from_slots("HassMediaPause", {}).name == "pause"
    assert from_slots("HassMediaNext", {"area": "x"}).name == "next"
    assert from_slots("HassSetVolume", {"volume_level": 30}).volume == 0.3
    assert from_slots("NasVolumeUp", {}).volume_step == 0.1
    assert from_slots("NasNowPlaying", {}).name == "now_playing"
    assert from_slots("NasPlay", {}).name == "resume"


# ---------- fuzzy matching ----------


@pytest.mark.parametrize("spoken, name", [
    ("ajr", "AJR"), ("a j r", "AJR"), ("ac dc", "AC/DC"), ("beatles", "The Beatles"),
    ("neo theater", "Neotheater"), ("simon and garfunkel", "Simon & Garfunkel"),
    ("staceys room", "Stacey's room speaker"), ("bedroom", "Elijah bedroom display"),
    ("guns n roses", "Guns N' Roses"), ("blink 182", "blink-182"),
])
def test_fuzzy_matches(spoken, name):
    assert match.score(spoken, name) >= match.THRESHOLD


@pytest.mark.parametrize("spoken, name", [
    ("kitchen", "Elijah bedroom display"), ("jazz", "Pop"), ("queen", "Queens of the Stone Age Live Album"),
])
def test_fuzzy_rejects(spoken, name):
    assert match.score(spoken, name) < match.THRESHOLD


def test_best_prefers_exact_over_partial():
    cands = [("Pop Punk", "pp"), ("Pop", "p"), ("K-Pop", "k")]
    assert match.best("pop", cands).item == "p"


# ---------- device targeting (pure) ----------

DEVICES = [
    {"id": "spk:chromecast:abc", "name": "Elijah bedroom display", "kind": "speaker", "castType": "chromecast",
     "online": True, "canPlay": True, "aliases": [], "isDefault": False},
    {"id": "spk:homeassistant:media_player.elijah_bedroom_display", "name": "Elijah bedroom display",
     "kind": "speaker", "castType": "homeassistant", "online": True, "canPlay": True, "aliases": [],
     "isDefault": False},
    {"id": "spk:chromecast:def", "name": "Stacey's room speaker", "kind": "speaker", "castType": "chromecast",
     "online": True, "canPlay": True, "aliases": ["Stacey's room"], "isDefault": False},
    {"id": "phone1", "name": "Pixel 8", "kind": "android", "online": True, "canPlay": True, "aliases": [],
     "isDefault": True},
    {"id": "spk:dlna:k", "name": "Living Room TV", "kind": "speaker", "castType": "dlna", "online": True,
     "canPlay": True, "aliases": ["lounge"], "isDefault": False},
]


def test_find_device_by_name_alias_and_generic():
    assert service.find_device("bedroom", DEVICES)["id"] == "spk:chromecast:abc"   # native beats the HA twin
    assert service.find_device("stacey's room", DEVICES)["id"] == "spk:chromecast:def"
    assert service.find_device("staceys speaker", DEVICES)["id"] == "spk:chromecast:def"
    assert service.find_device("lounge", DEVICES)["id"] == "spk:dlna:k"
    assert service.find_device("my phone", DEVICES)["id"] == "phone1"
    assert service.find_device("garage", DEVICES) is None


def test_area_device():
    # HA passes the satellite's area and that area's media players.
    d = service.area_device(DEVICES, "Bedroom", ["media_player.elijah_bedroom_display"])
    assert d["id"] == "spk:chromecast:abc"
    assert service.area_device(DEVICES, "Living room", [])["id"] == "spk:dlna:k"
    assert service.area_device(DEVICES, "Lounge", None)["id"] == "spk:dlna:k"
    assert service.area_device(DEVICES, "Garage", []) is None
    assert service.default_device(DEVICES)["id"] == "phone1"


def test_pick_split_keeps_library_names_whole():
    it = parse("play songs in the key of life on the kitchen speaker")
    devs = DEVICES + [{"id": "k", "name": "Kitchen speaker", "kind": "speaker", "online": True, "canPlay": True,
                       "aliases": []}]
    service.pick_split(it, devs)
    assert it.device == "kitchen speaker" and it.query == "songs in the key of life"
    it = parse("Play my Pop radio in the bedroom")
    service.pick_split(it, DEVICES)
    assert (it.device, it.query, it.kind) == ("bedroom", "pop", "radio")


# ---------- tokens + settings API ----------


@pytest.fixture
def voice(client):
    r = client.post(f"{API}/me/voice/tokens", json={"name": "HA kitchen"})
    assert r.status_code == 201
    tok = r.json()["token"]
    assert tok.startswith("nmv_")
    from fastapi.testclient import TestClient

    with TestClient(client.app) as v:
        v.headers["Authorization"] = f"Bearer {tok}"
        v.token_id = r.json()["id"]
        yield v


def test_token_lifecycle_and_scope(client, voice, conn):
    toks = client.get(f"{API}/me/voice/tokens").json()["tokens"]
    assert [t["name"] for t in toks] == ["HA kitchen"] and "token" not in toks[0]
    r = voice.post(f"{API}/voice/command", json={"text": "what's playing"})
    assert r.status_code == 200 and r.json()["speech"] == "Nothing is playing right now."
    # A voice token is not a device token, and a device token is not a voice token.
    assert voice.get(f"{API}/me").status_code == 401
    assert voice.get(f"{API}/playlists").status_code == 401
    assert client.post(f"{API}/voice/command", json={"text": "skip"}).status_code == 401
    assert client.get(f"{API}/me/voice/tokens").json()["tokens"][0]["lastUsed"] is not None
    # Revoked: dead at once. Only the owner can revoke.
    bob = user_client(client.app, conn, "bob", admin=False)
    with bob:
        assert bob.delete(f"{API}/me/voice/tokens/{voice.token_id}").status_code == 404
    assert client.delete(f"{API}/me/voice/tokens/{voice.token_id}").status_code == 204
    assert voice.post(f"{API}/voice/command", json={"text": "skip"}).status_code == 401
    assert client.get(f"{API}/me/voice/tokens").json()["tokens"] == []


def test_device_aliases(client):
    r = client.put(f"{API}/me/voice/devices/spk:homeassistant:media_player.kitchen",
                   json={"aliases": ["Kitchen", " kitchen ", "Cooking"], "isDefault": True})
    assert r.json() == {"id": "spk:homeassistant:media_player.kitchen", "aliases": ["Kitchen", "Cooking"],
                        "isDefault": True}
    devs = client.get(f"{API}/me/voice/devices").json()["devices"]
    k = next(d for d in devs if d["id"] == "spk:homeassistant:media_player.kitchen")
    assert k["aliases"] == ["Kitchen", "Cooking"] and k["isDefault"] and k["online"] is False
    # The user's own signed-in device is listed too (offline: no socket in this test).
    assert any(d["id"] == client.device_id for d in devs)
    client.put(f"{API}/me/voice/devices/{client.device_id}", json={"isDefault": True})
    devs = client.get(f"{API}/me/voice/devices").json()["devices"]
    assert [d["id"] for d in devs if d["isDefault"]] == [client.device_id]


def test_offline_default_device(client, voice):
    client.put(f"{API}/me/voice/devices/{client.device_id}", json={"isDefault": True})

    # Offline default device: says so instead of playing nowhere.
    r = voice.post(f"{API}/voice/command", json={"text": "play the band", "dryRun": True}).json()
    assert r["ok"] is False and r["speech"].endswith("is offline.")


# ---------- end to end through Connect onto a (fake) speaker ----------


@pytest.fixture
def speakers(app):
    bedroom = FakeSpeaker("bed", "Elijah bedroom display")
    stacey = FakeSpeaker("stacey", "Stacey's room speaker")
    reg = app.state.ctx.hub.speakers
    reg.poll_s = 0.05

    async def disc(cfg):
        return [bedroom, stacey]

    reg.discoverers = [disc]
    return bedroom, stacey


def test_voice_plays_controls_and_reports(client, voice, conn, speakers):
    bedroom, stacey = speakers
    ids = track_ids(conn)
    client.post(f"{API}/playlists", json={"name": "Pop", "trackIds": [ids["Song 2"], ids["Opening"]]})
    client.put(f"{API}/me/voice/devices/spk:fake:stacey", json={"aliases": ["Stacey's room"]})

    r = voice.post(f"{API}/voice/command", json={"text": "play the Pop playlist in the bedroom"}).json()
    assert r["ok"], r
    assert r["speech"] == "Playing your playlist Pop on Elijah bedroom display."
    assert r["device"]["id"] == "spk:fake:bed" and r["item"]["kind"] == "playlist"
    wait_for(lambda: bedroom.calls and bedroom.calls[0][:2] == ("load", ids["Song 2"]))

    wait_for(lambda: voice.post(f"{API}/voice/command", json={"text": "what's playing"}).json()["speech"] ==
             "This is Song 2 by Artist One on Elijah bedroom display.")
    r = voice.post(f"{API}/voice/command", json={"text": "skip"}).json()
    assert r["ok"] and r["speech"] == "Skipping."
    wait_for(lambda: any(c[:2] == ("load", ids["Opening"]) for c in bedroom.calls))
    r = voice.post(f"{API}/voice/command", json={"text": "set the volume to 30 percent"}).json()
    assert r["speech"] == "Volume 30 percent."
    wait_for(lambda: ("volume", 0.3) in bedroom.calls)
    assert voice.post(f"{API}/voice/command", json={"intent": "HassMediaPause"}).json()["speech"] == "Paused."
    wait_for(lambda: bedroom.calls[-1] == ("pause",))
    # Pausing a speaker that isn't playing for you.
    r = voice.post(f"{API}/voice/command", json={"text": "pause in Stacey's room"}).json()
    assert r["ok"] is False and r["speech"] == "Nothing is playing on Stacey's room speaker."

    # An artist, by the alias of another speaker; the bedroom stops being the active device.
    r = voice.post(f"{API}/voice/command", json={"text": "play the band in stacey's room"}).json()
    assert r["ok"] and r["speech"] == "Playing The Band on Stacey's room speaker.", r
    wait_for(lambda: stacey.calls and stacey.calls[0][0] == "load")
    assert stacey.calls[0][1] in (ids["Opening"], ids["Closing"])

    # No room named: HA's satellite area picks the speaker.
    r = voice.post(f"{API}/voice/command", json={"text": "play the album First Album", "area": "Bedroom"}).json()
    assert r["ok"] and r["device"]["id"] == "spk:fake:bed" and r["speech"] == \
        "Playing First Album by Artist One on Elijah bedroom display."
    r = voice.post(f"{API}/voice/command", json={"text": "play nothing like this in the garage"}).json()
    assert r["ok"] is False and r["speech"] == "I couldn't find a speaker called garage."
    r = voice.post(f"{API}/voice/command", json={"text": "play the song Zebra Crossing in the bedroom"}).json()
    assert r["ok"] is False and r["speech"] == "Sorry, I couldn't find zebra crossing in your library."


def test_voice_respects_family_mode(client, voice, conn, speakers):
    ids = track_ids(conn)
    conn.execute("INSERT INTO track_explicit (track_id, status, source, updated_at) VALUES (?, 'explicit', 'admin', 'x')",
                 (ids["Song 1"],))
    conn.execute("INSERT INTO track_explicit (track_id, status, source, updated_at) VALUES (?, 'explicit', 'admin', 'x')",
                 (ids["Song 2"],))
    assert client.put(f"{API}/me/family", json={"enabled": True}).status_code == 200
    r = voice.post(f"{API}/voice/command", json={"text": "play the song Song 1 in the bedroom"}).json()
    assert r["ok"] is False and "couldn't find" in r["speech"]
    # The whole album is explicit: nothing left to play.
    r = voice.post(f"{API}/voice/command", json={"text": "play the album First Album in the bedroom"}).json()
    assert r["ok"] is False and "couldn't find" in r["speech"]
    # A playlist with one hidden track plays without it.
    client.post(f"{API}/playlists", json={"name": "Mixed", "trackIds": [ids["Song 1"], ids["Closing"]]})
    r = voice.post(f"{API}/voice/command", json={"text": "play my mixed playlist in the bedroom"}).json()
    assert r["ok"], r
    q = client.get(f"{API}/queues/{r['queueId']}").json()
    assert [t["id"] for t in q["tracks"]] == [ids["Closing"]]


def test_command_validation(voice):
    assert voice.post(f"{API}/voice/command", json={}).status_code == 400
    assert voice.post(f"{API}/voice/command", json={"text": "x" * 501}).status_code == 422
    r = voice.post(f"{API}/voice/command", json={"text": "open the pod bay doors"}).json()
    assert r == {"ok": False, "intent": "unknown", "speech": "Sorry, I didn't understand that."}
    r = voice.post(f"{API}/voice/command", json={"text": "play the band"}).json()
    assert r["ok"] is False and r["speech"].startswith("Where should I play it?")


# ---------- migration ----------

V6_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v6.sql").read_text()


def test_v6_to_v7(tmp_path):
    path = tmp_path / "nasmusic.db"
    conn = db.connect(path)
    conn.executescript(V6_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (6)")
    conn.execute("INSERT INTO users (id, username, password_hash, created_at) VALUES ('u1', 'owner', 'h', 'x')")
    conn.close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION == 7
    for t in ("voice_tokens", "voice_devices"):
        assert migrations._table_exists(conn, t)
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'voice_tokens_user'").fetchone()
    assert len(list(tmp_path.glob("nasmusic.db.v6-backup-*"))) == 1
    assert not migrations.migrate_v6_to_v7(conn, db.schema_sql())
    db.init(conn)
    assert len(list(tmp_path.glob("*-backup-*"))) == 1
