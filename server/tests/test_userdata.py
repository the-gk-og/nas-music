"""docs/v2.md §10: synced settings, event context filtered by the switches, server-side weather from
geohash5 (Open-Meteo, mocked), and the user's data summary / export / erase."""
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from homebeat.app.weather import Weather, decode_geohash
from tests.conftest import track_ids

API = "/api/v1"
NOW = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)


class OpenMeteo:
    def __init__(self):
        self.calls = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        hours = [NOW - timedelta(hours=h) for h in range(48, -24, -1)]
        return httpx.Response(200, json={"hourly": {
            "time": [h.strftime("%Y-%m-%dT%H:00") for h in hours],
            "temperature_2m": [12.5 for _ in hours],
            "weather_code": [61 for _ in hours]}})


@pytest.fixture
def meteo(app):
    m = OpenMeteo()
    app.state.ctx.weather = Weather(httpx.MockTransport(m))
    return m


def ev(track, ctx, at=None, typ="play"):
    return {"id": str(uuid.uuid4()), "type": typ, "trackId": track, "at": (at or NOW).isoformat(), "context": ctx}


FULL = {"output": "bluetooth:Car kit", "network": "wifi", "batteryPct": 80, "motion": "walking", "place": "gym",
        "geohash5": "gcpvj", "searchQuery": "conver", "localHour": 9, "seeks": [{"fromMs": 1, "toMs": 2}],
        "heartRate": 120, "unknownField": "dropped"}


def test_geohash_decode():
    lat, lon = decode_geohash("gcpvj")  # London
    assert 51.4 < lat < 51.6 and -0.2 < lon < 0.0


def test_settings_sync(client):
    assert client.get(f"{API}/me/settings").json() == {"settings": {}}
    r = client.put(f"{API}/me/settings", json={"settings": {"context.output": True, "offline.favourites": False,
                                                             "eq": {"bands": [1, 2]}}})
    assert r.json()["settings"] == {"context.output": True, "offline.favourites": False, "eq": {"bands": [1, 2]}}
    r = client.put(f"{API}/me/settings", json={"settings": {"eq": None, "context.motion": True}})
    assert r.json()["settings"] == {"context.output": True, "offline.favourites": False, "context.motion": True}
    assert client.put(f"{API}/me/settings", json={"settings": {"x": "y" * 30_000}}).status_code == 422
    assert client.get(f"{API}/me/settings").json()["settings"]["context.motion"] is True


def test_context_is_dropped_when_switches_are_off(client, conn, meteo):
    t = track_ids(conn)["Song 1"]
    assert client.post(f"{API}/events", json={"events": [ev(t, FULL)]}).json()["accepted"] == 1
    assert conn.execute("SELECT context FROM events").fetchone()[0] is None
    assert meteo.calls == []  # weather is off too: no lookup at all


def test_context_kept_per_switch_with_weather(client, conn, meteo):
    t = track_ids(conn)["Song 1"]
    client.put(f"{API}/me/settings", json={"settings": {
        "context.output": True, "context.motion": True, "context.weather": True, "context.search": True,
        f"device.{client.device_id}.context.motion": False}})  # per-device override wins
    client.post(f"{API}/events", json={"events": [ev(t, FULL), ev(t, FULL, NOW - timedelta(hours=1))]})
    rows = [json.loads(r[0]) for r in conn.execute("SELECT context FROM events ORDER BY at")]
    for ctx in rows:
        assert set(ctx) == {"output", "searchQuery", "weather"}  # no motion (device off), no geohash (location off)
        assert ctx["weather"] == {"condition": "rain", "tempC": 12.5, "code": 61}
    assert len(meteo.calls) == 1  # cached per cell for an hour
    q = dict(meteo.calls[0].url.params)
    assert q["past_days"] == "2" and abs(float(q["latitude"]) - 51.5) < 0.1
    # Too old for the hourly window: no weather, event still stored.
    client.post(f"{API}/events", json={"events": [ev(t, FULL, NOW - timedelta(days=5))]})
    old = json.loads(conn.execute("SELECT context FROM events ORDER BY at LIMIT 1").fetchone()[0])
    assert "weather" not in old


def test_weather_failure_is_harmless(client, conn, app):
    def down(request):
        raise httpx.ConnectError("offline")

    app.state.ctx.weather = Weather(httpx.MockTransport(down))
    client.put(f"{API}/me/settings", json={"settings": {"context.weather": True, "context.location": True}})
    t = track_ids(conn)["Song 1"]
    assert client.post(f"{API}/events", json={"events": [ev(t, FULL)]}).json()["accepted"] == 1
    ctx = json.loads(conn.execute("SELECT context FROM events").fetchone()[0])
    assert ctx == {"place": "gym", "geohash5": "gcpvj"}


def test_data_summary_export_and_erase(client, conn, meteo):
    ids = track_ids(conn)
    client.put(f"{API}/me/settings", json={"settings": {"context.output": True, "context.location": True}})
    client.put(f"{API}/favourites/{ids['Song 2']}")
    client.post(f"{API}/events", json={"events": [
        ev(ids["Song 1"], FULL, NOW - timedelta(hours=2)), ev(ids["Song 1"], {"output": "wired"}),
        ev(ids["Opening"], None, typ="skip")]})
    client.post(f"{API}/playlists", json={"name": "P", "trackIds": [ids["Song 1"]]})
    s = client.get(f"{API}/me/data").json()
    assert s["events"]["count"] == 3 and s["events"]["withContext"] == 2
    assert s["signals"]["output"]["count"] == 2 and s["signals"]["output"]["enabled"] is True
    assert s["signals"]["location"]["count"] == 1 and s["signals"]["motion"] == {
        "enabled": False, "fields": ["motion", "cadenceSpm"], "count": 0, "first": None, "last": None}

    r = client.get(f"{API}/me/data/export")
    assert "attachment" in r.headers["content-disposition"]
    doc = r.json()
    assert doc["user"]["username"] == "alice" and len(doc["events"]) == 3
    assert doc["events"][0]["context"]["place"] == "gym"
    assert doc["playlists"][0]["name"] == "P" and doc["settings"]["context.output"] is True
    assert {t["trackId"] for t in doc["tracks"]} >= {ids["Song 1"], ids["Song 2"]}

    r = client.delete(f"{API}/me/data", params={"signal": "location"}).json()
    assert r == {"eventsUpdated": 1, "eventsDeleted": 0}
    assert client.get(f"{API}/me/data").json()["signals"]["location"]["count"] == 0
    assert client.get(f"{API}/me/data").json()["signals"]["output"]["count"] == 2

    assert client.delete(f"{API}/me/data", params={"signal": "all"}).status_code == 400
    r = client.delete(f"{API}/me/data", params={"signal": "all", "confirm": "true"}).json()
    assert r["eventsDeleted"] == 3
    assert client.get(f"{API}/me/data").json()["events"]["count"] == 0
    assert client.get(f"{API}/lists/most_played").json() == []
    assert [t["id"] for t in client.get(f"{API}/favourites").json()] == [ids["Song 2"]]  # kept
    assert len(client.get(f"{API}/playlists").json()) == 1  # kept
    assert client.delete(f"{API}/me/data", params={"signal": "nope"}).status_code == 422


def test_admin_cannot_read_other_users_context(client, bob, conn, meteo):
    bob.put(f"{API}/me/settings", json={"settings": {"context.location": True}})
    bob.post(f"{API}/events", json={"events": [ev(track_ids(conn)["Song 1"], FULL)]})
    assert client.get(f"{API}/me/data").json()["events"]["count"] == 0
    assert client.get(f"{API}/me/data/export").json()["events"] == []
    users = client.get(f"{API}/admin/users").json()
    assert all(set(u) == {"id", "username", "isAdmin", "createdAt", "lastLogin", "disabled", "deviceCount",
                          "lastActivity"}
               for u in users)
