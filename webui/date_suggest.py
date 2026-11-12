"""Suggest a calendar date for a log whose clock was never set (1980, GPS week 0) or whose date is only estimated.

Such a log keeps the right time of day. In order of preference the day comes from:

- gcs: a dated ground station (GCS) recording of the same flight (same aircraft, duration and time of day);
- name: a date in the uploaded file or folder name (2026-04-29, 20260429, or 26_04_29 as in Paparazzi log names);
- folder: the dated logs of the same aircraft just before and after it in the same folder, when they agree.

Nothing is changed here: the suggestion is shown on the log and a person accepts it, which sets the existing date
override with the suggestion's reason as its note.
"""
import re
from datetime import date, datetime, timedelta, timezone

MIN_DURATION_S = 60        # short flights match too many others on duration alone
DURATION_TOLERANCE_S = 1.0
DURATION_TOLERANCE = 0.01  # relative, for long flights
TIME_OF_DAY_TOLERANCE_S = 30
ONBOARD = {"onboard_sd", "px4_ulog", "ardupilot", "betaflight", "indiflight", "blackbox"}
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
    """{log_id: suggestion} with date, source (gcs, name or folder) and reason; see the module docstring."""
    out = gcs_suggestions(con, undated)
    for log_id, suggestion in name_suggestions(con, undated).items():
        out.setdefault(log_id, suggestion)
    return out


_YMD = re.compile(r"(?<!\d)(20\d\d)[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)")
_YY_MM_DD = re.compile(r"(?<!\d)(\d\d)_(0[1-9]|1[0-2])_(0[1-9]|[12]\d|3[01])(?!\d)")


def date_in_name(name, undated):
    """(YYYY-MM-DD, the part of the path it was read from) for the first plausible date in the file name, then in
    the folders from the deepest up; None if there is none."""
    parts = [p for p in re.split(r"[\\/]", name or "") if p]
    for part in reversed(parts):
        for pattern, century in ((_YMD, ""), (_YY_MM_DD, "20")):
            for m in pattern.finditer(part):
                try:
                    day = date(int(century + m[1]), int(m[2]), int(m[3])).isoformat()
                except ValueError:
                    continue
                if not undated(day + "T12:00:00Z"):
                    return day, part
    return None


def _natural(name):
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", name)]


def name_suggestions(con, undated):
    """Suggestions from the file or folder name, else from the agreeing dated neighbours in the same folder."""
    logs = {}
    for r in con.execute("SELECT id, name, start_utc, date_override, date_estimated FROM logs WHERE status='done'"):
        open_ = (undated(r["start_utc"]) or bool(r["date_estimated"])) and not r["date_override"]
        day = r["date_override"] or (None if open_ or undated(r["start_utc"]) else r["start_utc"][:10])
        folder, _, file = (r["name"] or "").replace("\\", "/").rpartition("/")
        logs[r["id"]] = dict(id=r["id"], name=r["name"] or "", folder=folder, file=file, open=open_, day=day, ac=set())
    for r in con.execute("SELECT log_id, drone_id, name FROM aircraft"):
        if r["log_id"] in logs:
            logs[r["log_id"]]["ac"].add(("d", r["drone_id"]) if r["drone_id"] is not None
                                        else ("n", (r["name"] or "").strip().casefold()))
    by_folder = {}
    for log in logs.values():
        by_folder.setdefault(log["folder"], []).append(log)
    for folder in by_folder.values():
        folder.sort(key=lambda l: _natural(l["file"]))
    out = {}
    for log in logs.values():
        if not log["open"]:
            continue
        found = date_in_name(log["name"], undated)
        if found:
            day, part = found
            where = "file name" if part == log["file"] else f"folder name {part}"
            out[log["id"]] = dict(date=day, source="name", reason=f"date in the {where}")
            continue
        if not log["ac"] or not log["folder"]:  # files uploaded without a folder are not neighbours
            continue
        same = [l for l in by_folder[log["folder"]] if l["ac"] & log["ac"]]
        i = same.index(log)
        before = next((l for l in reversed(same[:i]) if l["day"]), None)
        after = next((l for l in same[i + 1:] if l["day"]), None)
        near = [l for l in (before, after) if l]
        if near and len({l["day"] for l in near}) == 1:
            out[log["id"]] = dict(
                date=near[0]["day"], source="folder", neighbour_log_ids=[l["id"] for l in near],
                reason="same as neighbouring " + " and ".join(l["file"] for l in near)
                       + " (same aircraft and folder)")
    return out


def gcs_suggestions(con, undated):
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
        offset = round(_time_of_day_offset(first, partner), 1)
        out[log_id] = dict(
            date=(raw_day + timedelta(days=shift)).isoformat(), source="gcs", gcs_log_id=partner["log_id"],
            gcs_flight_id=partner["id"], flights=len(matched), offset_s=offset,
            reason=f"matches GCS log {partner['log_id']}: same aircraft, duration and time of day"
                   f" ({len(matched)} flight(s), offset {offset:+g} s)")
    return out
