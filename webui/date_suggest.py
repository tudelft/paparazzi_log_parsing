"""Suggest a calendar date for an onboard log whose clock was never set (1980, GPS week 0).

Such a log keeps the right time of day. A dated ground station (GCS) recording of the same flight has the same
aircraft, the same duration and the same time of day, so a unique match gives the day. Nothing is changed here:
the suggestion is shown on the log and a person accepts it, which sets the existing date override.
"""
from datetime import date, datetime, timedelta, timezone

MIN_DURATION_S = 60        # short flights match too many others on duration alone
DURATION_TOLERANCE_S = 1.0
DURATION_TOLERANCE = 0.01  # relative, for long flights
TIME_OF_DAY_TOLERANCE_S = 30
ONBOARD = {"onboard_sd", "px4_ulog", "ardupilot"}
GROUND = {"gcs", "mavlink_tlog"}


def _ts(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def _log_type(row):
    if row["log_type"]:
        return row["log_type"]
    return "onboard_sd" if (row["log_name"] or "").endswith("_SD.data") else "gcs"  # logs parsed before types


def _same_aircraft(a, b):
    if a["drone_id"] is not None and b["drone_id"] is not None:
        return a["drone_id"] == b["drone_id"]
    return bool(a["name"]) and a["name"] == b["name"]


def _same_duration(a, b):
    return abs(a["duration"] - b["duration"]) <= max(DURATION_TOLERANCE_S, DURATION_TOLERANCE * max(a["duration"], b["duration"]))


def _time_of_day_offset(a, b):
    """Signed seconds b - a on the clock face, in (-43200, 43200]."""
    d = (b["start"] - a["start"]) % 86400
    return d - 86400 if d > 43200 else d


def suggestions(con, undated):
    """{log_id: suggestion} for logs whose clock is implausible (`undated(start_utc)`) or whose date is only
    estimated from firmware (a GCS match confirms the day; accepting it ends the estimate, so the recordings
    can merge), without a date override. A suggestion needs every matched flight of the log to have exactly one GCS partner (and that
    partner no other claimant), all agreeing on the same day shift."""
    flights = []
    for r in con.execute(
            "SELECT f.id, f.takeoff_utc, f.landing_utc, f.duration_s, a.drone_id, a.name, l.id AS log_id,"
            " l.name AS log_name, l.log_type, l.start_utc, l.date_override, l.date_estimated FROM flights f"
            " JOIN aircraft a ON a.id=f.aircraft_id JOIN logs l ON l.id=a.log_id"
            " WHERE l.status='done' AND f.hidden=0 AND f.duration_s IS NOT NULL"):
        start = _ts(r["takeoff_utc"])
        if start is None:
            continue
        flights.append(dict(id=r["id"], log_id=r["log_id"], start=start, duration=r["duration_s"],
                            end=_ts(r["landing_utc"]) or start + r["duration_s"], drone_id=r["drone_id"],
                            name=(r["name"] or "").strip().casefold(), type=_log_type(r), log_start=r["start_utc"],
                            dated=not undated(r["start_utc"]) and not r["date_override"] and not r["date_estimated"],
                            open=(undated(r["start_utc"]) or bool(r["date_estimated"])) and not r["date_override"],
                            estimated=bool(r["date_estimated"])))
    # A GCS flight that already has a dated onboard partner is taken.
    onboard_by_day = {}
    for f in flights:
        if f["dated"] and f["type"] in ONBOARD:
            onboard_by_day.setdefault(int(f["start"] // 86400), []).append(f)
    ground = [g for g in flights if g["dated"] and g["type"] in GROUND and g["duration"] >= MIN_DURATION_S
              and not any(o["log_id"] != g["log_id"] and _same_aircraft(o, g)
                          and min(o["end"], g["end"]) > max(o["start"], g["start"])
                          for day in range(int(g["start"] // 86400) - 1, int(g["end"] // 86400) + 1)
                          for o in onboard_by_day.get(day, []))]
    claims, per_flight = {}, {}
    for f in flights:
        # A ground station logs the PC clock, so a log whose date had to be estimated is onboard whatever its detected
        # type (e.g. an SD log renamed logNN_<aircraft>.data with a ground station's .log header next to it).
        if not f["open"] or (f["type"] not in ONBOARD and not f["estimated"]) or f["duration"] < MIN_DURATION_S:
            continue
        found = [g for g in ground if _same_aircraft(f, g) and _same_duration(f, g)
                 and abs(_time_of_day_offset(f, g)) <= TIME_OF_DAY_TOLERANCE_S]
        per_flight[f["id"]] = (f, found)
        for g in found:
            claims.setdefault(g["id"], set()).add(f["id"])
    by_log = {}
    for f, found in per_flight.values():
        by_log.setdefault(f["log_id"], []).append((f, found))
    out = {}
    for log_id, matches in by_log.items():
        matched = [(f, found[0]) for f, found in matches if found]
        if not matched or any(len(found) > 1 for _, found in matches) \
                or any(len(claims[g["id"]]) > 1 for _, g in matched):
            continue
        shifts = {round((g["start"] - f["start"]) / 86400) for f, g in matched}
        if len(shifts) != 1:
            continue
        shift = shifts.pop()
        first, partner = matched[0]
        raw_day = date.fromisoformat(first["log_start"][:10]) if first["log_start"] else \
            datetime.fromtimestamp(first["start"], timezone.utc).date()
        out[log_id] = dict(
            date=(raw_day + timedelta(days=shift)).isoformat(), gcs_log_id=partner["log_id"],
            gcs_flight_id=partner["id"], flights=len(matched),
            offset_s=round(_time_of_day_offset(first, partner), 1))
    return out
