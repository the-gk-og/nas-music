"""Weather for listening context (docs/v2.md §10): Open-Meteo (free, no key, open data) looked up from a
5-character geohash (~5 km cell), hourly data for the last 2 days, cached per cell for an hour."""
from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime

import httpx

log = logging.getLogger(__name__)

URL = "https://api.open-meteo.com/v1/forecast"
CACHE_S = 3600
_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"

# WMO weather interpretation codes (Open-Meteo docs) -> short condition names.
WMO = {0: "clear", 1: "mostly_clear", 2: "partly_cloudy", 3: "overcast", 45: "fog", 48: "fog",
       51: "drizzle", 53: "drizzle", 55: "drizzle", 56: "freezing_drizzle", 57: "freezing_drizzle",
       61: "rain", 63: "rain", 65: "heavy_rain", 66: "freezing_rain", 67: "freezing_rain",
       71: "snow", 73: "snow", 75: "heavy_snow", 77: "snow", 80: "showers", 81: "showers", 82: "heavy_showers",
       85: "snow_showers", 86: "snow_showers", 95: "thunderstorm", 96: "thunderstorm", 99: "thunderstorm"}


def decode_geohash(gh: str) -> tuple[float, float]:
    """Centre (lat, lon) of a geohash cell."""
    lat, lon = [-90.0, 90.0], [-180.0, 180.0]
    even = True
    for ch in gh.lower():
        cd = _BASE32.index(ch)
        for mask in (16, 8, 4, 2, 1):
            rng = lon if even else lat
            mid = (rng[0] + rng[1]) / 2
            if cd & mask:
                rng[0] = mid
            else:
                rng[1] = mid
            even = not even
    return (lat[0] + lat[1]) / 2, (lon[0] + lon[1]) / 2


def valid_geohash(gh: object) -> bool:
    return isinstance(gh, str) and len(gh) == 5 and all(c in _BASE32 for c in gh.lower())


class Weather:
    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.transport = transport
        self._cache: dict[str, tuple[float, dict[str, dict]]] = {}   # geohash -> (fetched, {hour: weather})
        self._lock = threading.Lock()
        self.fetches = 0

    def _hours(self, gh: str) -> dict[str, dict] | None:
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(gh)
            if hit and now - hit[0] < CACHE_S:
                return hit[1]
        lat, lon = decode_geohash(gh)
        params = {"latitude": round(lat, 3), "longitude": round(lon, 3), "hourly": "temperature_2m,weather_code",
                  "past_days": 2, "forecast_days": 1, "timezone": "UTC"}
        try:
            with httpx.Client(timeout=5.0, transport=self.transport,
                              headers={"User-Agent": "nas-music (self-hosted)"}) as c:
                r = c.get(URL, params=params)
                r.raise_for_status()
                h = r.json()["hourly"]
            hours = {}
            for t, temp, code in zip(h["time"], h["temperature_2m"], h["weather_code"], strict=False):
                if temp is None or code is None:
                    continue
                hours[t[:13]] = {"condition": WMO.get(int(code), "unknown"), "tempC": round(float(temp), 1),
                                 "code": int(code)}
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as e:
            log.warning("weather lookup for %s failed: %s", gh, e)
            return None
        self.fetches += 1
        with self._lock:
            self._cache[gh] = (now, hours)
        return hours

    def at(self, gh: str, when: datetime) -> dict | None:
        """Weather in cell `gh` during the UTC hour of `when` (None if unknown, e.g. older than 2 days)."""
        if not valid_geohash(gh):
            return None
        hours = self._hours(gh.lower())
        if not hours:
            return None
        return hours.get(when.astimezone(UTC).strftime("%Y-%m-%dT%H"))
