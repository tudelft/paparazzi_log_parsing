"""On-demand historical weather; never called while parsing logs."""
import json
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


class WeatherError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


_lock = threading.Lock()
_last_request = None
FIELDS = {
    "temperature_2m": "temperature_c",
    "relative_humidity_2m": "humidity_percent",
    "precipitation": "precipitation_mm",
    "cloud_cover": "cloud_cover_percent",
    "pressure_msl": "pressure_hpa",
    "wind_speed_10m": "wind_speed_ms",
    "wind_direction_10m": "wind_from_deg",
    "wind_gusts_10m": "wind_gusts_ms",
}


def location_hour(entry):
    lat, lon = entry.get("takeoff_lat"), entry.get("takeoff_lon")
    if (not isinstance(lat, (int, float)) or not isinstance(lon, (int, float))
            or not math.isfinite(lat) or not math.isfinite(lon)
            or not -90 <= lat <= 90 or not -180 <= lon <= 180):
        raise WeatherError("Weather needs valid takeoff coordinates.", 400)
    try:
        instant = datetime.fromisoformat(entry["takeoff_utc"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise WeatherError("Weather needs a valid takeoff date.", 400) from exc
    if not entry.get("date_ok") or instant.tzinfo is None or instant.year < 1940:
        raise WeatherError("Correct the flight date before fetching weather (1940 onwards).", 400)
    instant = instant.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    if instant > datetime.now(timezone.utc) - timedelta(days=6):
        raise WeatherError("Historical weather is available for flights at least six days old.", 400)
    return round(lat, 2), round(lon, 2), instant.strftime("%Y-%m-%dT%H:00")


def lookup(con, entry, fetch=False):
    lat, lon, hour = location_hour(entry)
    key = f"{lat:.2f},{lon:.2f}/{hour}"

    def cached():
        row = con.execute("SELECT payload FROM weather_cache WHERE cache_key=?", (key,)).fetchone()
        return json.loads(row["payload"]) if row else None

    result = cached()
    if result is not None or not fetch:
        return result
    if not _lock.acquire(blocking=False):
        raise WeatherError("Another weather request is running. Please try again shortly.", 429)
    try:
        result = cached()
        if result is not None:
            return result
        global _last_request
        if _last_request is not None and time.monotonic() - _last_request < 10:
            raise WeatherError("Weather requests are limited to one per 10 seconds. Please try again shortly.", 429)
        _last_request = time.monotonic()
        params = urlencode({
            "latitude": lat, "longitude": lon,
            "start_date": hour[:10], "end_date": hour[:10],
            "hourly": ",".join(FIELDS), "timezone": "UTC", "wind_speed_unit": "ms",
        })
        try:
            with urlopen("https://archive-api.open-meteo.com/v1/archive?" + params, timeout=15) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise WeatherError("Weather service returned an oversized response.")
            data = json.loads(raw)
        except HTTPError as exc:
            raise WeatherError(f"Weather service returned HTTP {exc.code}. Please try again later.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise WeatherError("Could not reach the weather service. Please try again later.") from exc
        except (ValueError, UnicodeError) as exc:
            raise WeatherError("Weather service returned invalid JSON.") from exc
        try:
            hourly = data["hourly"]
            idx = hourly["time"].index(hour)
            values = {label: hourly[field][idx] for field, label in FIELDS.items()}
            if any(v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))
                                     or not math.isfinite(v)) for v in values.values()):
                raise ValueError("Invalid weather values")
            if all(v is None for v in values.values()):
                raise WeatherError("No historical weather is available for this location and hour.", 404)
            result = {
                "hour_utc": hour + ":00Z", "latitude": lat, "longitude": lon,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                **values,
            }
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise WeatherError("Weather service returned incomplete or invalid hourly data.") from exc
        con.execute("INSERT OR REPLACE INTO weather_cache(cache_key, payload) VALUES (?, ?)",
                    (key, json.dumps(result, allow_nan=False)))
        con.commit()
        return result
    finally:
        _lock.release()
