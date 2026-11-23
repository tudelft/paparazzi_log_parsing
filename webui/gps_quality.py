"""When a GPS fix is good enough to place a flight (takeoff/landing location, indoor, map).

One rule for every log type: the Python parsers (mav_parse) select endpoint fixes with it, and the web UI applies it
again to the quality stored with each endpoint (`takeoff.gps` / `landing.gps`), so it can be tuned here without
re-parsing. The C++ Paparazzi parser uses the same constants (cpp/fastreader.cpp, GPS_MIN_*/GPS_MAX_*).
"""
from __future__ import annotations

MIN_FIX_TYPE = 3          # 3D fix
MIN_SATELLITES = 6
MAX_HDOP = 2.5            # HDOP, or PDOP where only that is logged (Paparazzi)
MAX_EPH_M = 5.0           # horizontal accuracy (eph_m), or Paparazzi's position accuracy (pacc_m), only without a DOP
ENDPOINT_WINDOW_S = 30.0  # takeoff: first good fix at most this long after takeoff; landing: last good fix before


def quality(fix_type=None, satellites=None, hdop=None, eph_m=None):
    """The logged quality fields of one fix (missing ones left out) and whether the fix is good."""
    q = {k: v for k, v in (("fix_type", fix_type), ("satellites", satellites), ("hdop", hdop), ("eph_m", eph_m))
         if v is not None}
    q["good"] = good(q)
    return q


def good(q):
    """Whether a stored quality dict passes the rule. Fields a log does not have are not checked; a fix from motion
    capture (`external`) is a trusted position, not a GPS fix."""
    if not isinstance(q, dict):
        return True
    if q.get("external"):
        return True
    try:
        if q.get("fix_type") is not None and float(q["fix_type"]) < MIN_FIX_TYPE:
            return False
        if q.get("satellites") is not None and float(q["satellites"]) < MIN_SATELLITES:
            return False
        dop = q["hdop"] if q.get("hdop") is not None else q.get("pdop")
        if dop is not None:
            return 0 <= float(dop) <= MAX_HDOP
        accuracy = q["eph_m"] if q.get("eph_m") is not None else q.get("pacc_m")
        if accuracy is not None:
            return 0 <= float(accuracy) <= MAX_EPH_M
    except (TypeError, ValueError):
        return False
    return True


def endpoint_position(event, lat, lon):
    """(lat, lon) of a parsed takeoff/landing that the web UI trusts, from the GPS quality stored with it.

    Results without stored quality (older parsers) keep their logged coordinates. When the parser found no good fix
    it stores the raw sample in `gps` (selected "raw"); that is used only if it passes the current rule."""
    q = (event or {}).get("gps") if isinstance(event, dict) else None
    if not isinstance(q, dict):
        return lat, lon
    if lat is not None and lon is not None and q.get("selected") != "raw":
        return (lat, lon) if good(q) else (None, None)
    if q.get("selected") == "raw" and q.get("lat") is not None and q.get("lon") is not None and good(q):
        return q["lat"], q["lon"]
    return None, None
