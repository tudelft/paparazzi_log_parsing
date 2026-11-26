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


# Forecast for the Home page: the next hours at one place, cached in memory per rounded position.
FORECAST_FIELDS = {
    "temperature_2m": "temperature_c",
    "precipitation": "precipitation_mm",
    "cloud_cover": "cloud_cover_percent",
    "wind_speed_10m": "wind_speed_ms",
    "wind_direction_10m": "wind_from_deg",
    "wind_gusts_10m": "wind_gusts_ms",
    "weather_code": "weather_code",
}
FORECAST_TTL_S = 30 * 60
FORECAST_HOURS = 8
_forecast_lock = threading.Lock()
_forecasts = {}  # "lat,lon" -> (fetched monotonic, result)


def _number(value):
    return value is None or (not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value))


WINDOW_DAYS = 16  # how far ahead Open-Meteo forecasts; a slot later than that has no forecast yet
_windows = {}  # "lat,lon" -> (fetched monotonic, data)


def _fetch_forecast(lat, lon, days):
    """The raw hourly forecast of Open-Meteo for `days` days at a position (local times of the place)."""
    params = urlencode({
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "hourly": ",".join(FORECAST_FIELDS),
        "timezone": "auto", "wind_speed_unit": "ms", "forecast_days": days,
    })
    try:
        with urlopen("https://api.open-meteo.com/v1/forecast?" + params, timeout=15) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise WeatherError("Weather service returned an oversized response.")
        return json.loads(raw)
    except HTTPError as exc:
        raise WeatherError(f"Weather service returned HTTP {exc.code}. Please try again later.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise WeatherError("Could not reach the weather service. Please try again later.") from exc
    except (ValueError, UnicodeError) as exc:
        raise WeatherError("Weather service returned invalid JSON.") from exc


def forecast_window(lat, lon, start, end):
    """{timezone, fetched_at, hours, note} of the forecast for the local hours from `start` to `end`
    (YYYY-MM-DDTHH:MM, the place's local time) for a planned flight. hours is empty with a note when the window is
    past or more than WINDOW_DAYS days ahead. Cached for FORECAST_TTL_S per rounded position."""
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (lat, lon)) \
            or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise WeatherError("The place has no valid position.", 400)
    key = f"{lat:.2f},{lon:.2f}"
    with _forecast_lock:
        hit = _windows.get(key)
        if hit and time.monotonic() - hit[0] < FORECAST_TTL_S:
            data, fetched = hit[1]
        else:
            data, fetched = _fetch_forecast(lat, lon, WINDOW_DAYS), datetime.now(timezone.utc).isoformat()
            _windows[key] = (time.monotonic(), (data, fetched))
    try:
        hourly = data["hourly"]
        first = start[:13] + ":00"  # the hour the window starts in, to its last started hour
        hours = []
        for i, t in enumerate(hourly["time"]):
            if first <= t < end:
                values = {label: hourly[field][i] for field, label in FORECAST_FIELDS.items()}
                if not all(_number(v) for v in values.values()):
                    raise ValueError("Invalid weather values")
                hours.append({"time": t, **values})
        times = hourly["time"]
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
        raise WeatherError("Weather service returned incomplete or invalid forecast data.") from exc
    note = None
    if not hours:
        note = ("No forecast yet: it is available about {} days ahead.".format(WINDOW_DAYS)
                if times and start > times[-1] else "No forecast for this time.")
    return {"timezone": data.get("timezone"), "fetched_at": fetched, "hours": hours, "note": note}


def forecast(lat, lon, now=None):
    """{timezone, fetched_at, hours: [{time (local), <FORECAST_FIELDS labels>}]} for the next FORECAST_HOURS
    hours from Open-Meteo, the same provider as the historical weather. Cached for FORECAST_TTL_S."""
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (lat, lon)) \
            or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise WeatherError("The place has no valid position.", 400)
    key = f"{lat:.2f},{lon:.2f}"
    with _forecast_lock:
        hit = _forecasts.get(key)
        if hit and time.monotonic() - hit[0] < FORECAST_TTL_S:
            return hit[1]
        params = urlencode({
            "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "hourly": ",".join(FORECAST_FIELDS),
            "timezone": "auto", "wind_speed_unit": "ms", "forecast_days": 2,
        })
        try:
            with urlopen("https://api.open-meteo.com/v1/forecast?" + params, timeout=15) as response:
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
            hourly, offset = data["hourly"], int(data.get("utc_offset_seconds") or 0)
            local_now = (now or datetime.now(timezone.utc)) + timedelta(seconds=offset)
            current = local_now.strftime("%Y-%m-%dT%H:00")
            start = next(i for i, t in enumerate(hourly["time"]) if t >= current)
            hours = []
            for i in range(start, min(start + FORECAST_HOURS, len(hourly["time"]))):
                values = {label: hourly[field][i] for field, label in FORECAST_FIELDS.items()}
                if not all(_number(v) for v in values.values()):
                    raise ValueError("Invalid weather values")
                hours.append({"time": hourly["time"][i], **values})
        except (KeyError, IndexError, TypeError, ValueError, AttributeError, StopIteration) as exc:
            raise WeatherError("Weather service returned incomplete or invalid forecast data.") from exc
        result = {"timezone": data.get("timezone"), "latitude": lat, "longitude": lon,
                  "fetched_at": datetime.now(timezone.utc).isoformat(), "hours": hours}
        _forecasts[key] = (time.monotonic(), result)
        return result
