"""Parse PX4 ULog, ArduPilot DataFlash and MAVLink tlog files into fastreader JSON.

Path points use the same schema as the C++ Paparazzi parser so the logbook, map and
charts work unchanged. Text, events and other log message types are kept for the
Messages tab, with a cap so full-rate IMU dumps do not freeze the UI.
"""
from __future__ import annotations

import csv
import hashlib
import math
import os
import re
import struct
import subprocess
import time
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

PARSER_NAME = "mav-log"
PARSER_VERSION = "1.0.3"
BLACKBOX_PARSER_NAME = "blackbox"
BLACKBOX_PARSER_VERSION = "1.0.0"

LOG_PX4 = "px4_ulog"
LOG_AP = "ardupilot"
LOG_TLOG = "mavlink_tlog"
LOG_BF = "betaflight"
LOG_INDI = "indiflight"
LOG_BLACKBOX = "blackbox"

BLACKBOX_EXTS = {".bfl", ".bbl"}
MAV_SINGLE_EXTS = {".ulg", ".bin", ".tlog"} | BLACKBOX_EXTS
MAV_EXTS = MAV_SINGLE_EXTS | {".log"}
BLACKBOX_MAGIC = b"H Product:Blackbox"
BLACKBOX_QUAT_SCALE = float((127 << 6) - 1)
BLACKBOX_U16_SCALE = float((127 << 7) - 1)
BLACKBOX_DEG = 57.2957796
BLACKBOX_G = 9.80665
BLACKBOX_DSHOT = (158.0, 2048.0)
BLACKBOX_FLAG_COLS = (
    "flightModeFlags", "stateFlags", "failsafePhase", "rxSignalReceived", "rxFlightChannelValid",
)
BLACKBOX_EKF_JUMP_M = 50.0
BLACKBOX_KEEP = frozenset(
    ("time", "flightModeFlags", "stateFlags",
     "GPS_coord[0]", "GPS_coord[1]", "GPS_numSat", "GPS_altitude", "GPS_speed",
     "vbatLatest", "baroAlt", "pitot_airSpeed")
    + tuple(f"motor[{i}]" for i in range(8))
    + tuple(f"{prefix}[{i}]" for prefix in ("quat", "ekf_quat") for i in range(4))
    + tuple(f"{prefix}[{i}]" for prefix in ("ekf_pos", "pos", "ekf_vel", "vel") for i in range(3))
)

MAX_INFO_MESSAGES = 50_000
MAX_PER_TYPE = 2_000

# IMU-class topics: used for the path when needed, not dumped sample-by-sample.
_HIGH_RATE = (
    "sensor_", "vehicle_imu", "vehicle_angular", "vehicle_acceleration", "vehicle_torque",
    "vehicle_rates", "vehicle_thrust", "estimator_innov", "estimator_gps_status", "esc_status",
    "px4io_status", "sensor_gyro", "sensor_accel", "sensor_mag", "sensor_baro",
)
_AP_HIGH_RATE = {
    "IMU", "IMU2", "IMU3", "GYR", "GYR2", "GYR3", "ACC", "ACC2", "ACC3", "MAG", "MAG2", "MAG3",
    "BARO", "BAR2", "BAR3", "FTN", "FTN1", "FTN2", "PIQR", "XKF1", "XKF2", "XKF3", "XKF4", "XKF5",
    "NKF1", "NKF2", "NKF3", "NKF4", "NKF5", "NKY0", "NKY1", "RATE", "PIDR", "PIDP", "PIDY", "PIDA",
    "PIDE", "QTUN", "BAT", "BAT2",
}

PX4_NAV = {
    0: "Manual", 1: "Altitude", 2: "Position", 3: "Auto Mission", 4: "Auto Loiter", 5: "Auto RTL",
    6: "Position Slow", 10: "Auto Takeoff", 11: "Auto Land", 12: "Follow Target", 13: "Precision Land",
    14: "Offboard", 15: "Stabilized", 17: "Auto VTOL Takeoff",
}
PX4_VEHICLE = {0: "unknown", 1: "rotary wing", 2: "fixed wing", 3: "rover", 4: "airship"}
PX4_MAV_TYPE = {
    1: "fixed_wing", 2: "quad_x", 3: "coaxial", 4: "helicopter", 13: "hexarotor",
    14: "octarotor", 19: "vtol", 20: "vtol", 21: "vtol", 22: "vtol",
}
_AIRFRAME_BOOT = re.compile(r"airframes/(\d+)_([a-z0-9_-]+)")
_KNOWN_AUTOSTART = {2100: "standard_plane", 4001: "quad_x"}

MAVLINK_MAGIC = {0xFD, 0xFE}
DF_HEAD = bytes((0xA3, 0x95))
ULOG_MAGIC = b"ULog\x01\x12\x35"


def sniff(path) -> str | None:
    """Return log_type for a PX4/ArduPilot/tlog/Blackbox file, or None for Paparazzi / unknown."""
    p = Path(path)
    try:
        head = p.read_bytes()[:4096]
    except OSError:
        return None
    if not head:
        return None
    suf = p.suffix.lower()
    if head.startswith(ULOG_MAGIC) or head.startswith(b"ULog"):
        return LOG_PX4
    if suf == ".ulg":
        return LOG_PX4
    if looks_blackbox(head) or suf in BLACKBOX_EXTS:
        return LOG_BLACKBOX
    if looks_tlog(head) or suf == ".tlog":
        return LOG_TLOG if looks_tlog(head) or suf == ".tlog" else None
    if head.startswith(DF_HEAD) or suf == ".bin":
        if head.startswith(DF_HEAD) or suf == ".bin":
            return LOG_AP
    if looks_ap_text(head):
        return LOG_AP
    return None


def looks_blackbox(head: bytes) -> bool:
    return BLACKBOX_MAGIC in head


def blackbox_decode_bin():
    return os.environ.get("LOGDB_BLACKBOX_DECODE") or str(
        Path(__file__).resolve().parent.parent / "cpp" / "build" / "blackbox_decode")


def looks_tlog(head: bytes) -> bool:
    if len(head) >= 9 and head[8] in MAVLINK_MAGIC:
        return True
    return bool(head) and head[0] in MAVLINK_MAGIC


def looks_ap_text(head: bytes) -> bool:
    text = head.lstrip().split(b"\n", 1)[0]
    return text.startswith(b"FMT,") or text.startswith(b"FMT ")


def looks_paparazzi_xml(path) -> bool:
    try:
        head = Path(path).read_bytes()[:800].lstrip()
    except OSError:
        return False
    low = head.lower()
    return low.startswith(b"<?xml") or low.startswith(b"<protocol") or b"<msg_class" in low or b"<configuration" in low


def parse(path, sha256=None, path_rate=5.0, log_type=None, blackbox_decode=None):
    """Parse a mavlog into the same JSON document fastreader emits."""
    p = Path(path)
    kind = log_type or sniff(p)
    if kind is None:
        raise RuntimeError(f"not a PX4, ArduPilot, MAVLink or Blackbox log: {p.name}")
    raw = p.read_bytes()
    digest = sha256 or hashlib.sha256(raw).hexdigest()
    if kind == LOG_PX4:
        return _parse_ulog(p, digest, len(raw), path_rate)
    if kind == LOG_TLOG:
        return _parse_tlog(p, digest, len(raw), path_rate)
    if kind in (LOG_BLACKBOX, LOG_BF, LOG_INDI):
        return _parse_blackbox(p, digest, len(raw), path_rate, decoder=blackbox_decode)
    return _parse_ardupilot(p, digest, len(raw), path_rate)


# ---------------------------------------------------------------------------------------------------------------------
# Shared JSON builders
# ---------------------------------------------------------------------------------------------------------------------
def _utc(epoch):
    if epoch is None or not math.isfinite(epoch) or epoch < 0:
        return None
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    except (OverflowError, OSError, ValueError):
        return None


def _num(v, nd=3):
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return round(x, nd)


def _haversine_m(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return 0.0
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _quat_euler_deg(w, x, y, z):
    sinr = 2 * (w * x + y * z)
    cosr = 1 - 2 * (x * x + y * y)
    roll = math.degrees(math.atan2(sinr, cosr))
    sinp = max(-1.0, min(1.0, 2 * (w * y - z * x)))
    pitch = math.degrees(math.asin(sinp))
    siny = 2 * (w * z + x * y)
    cosy = 1 - 2 * (y * y + z * z)
    yaw = math.degrees(math.atan2(siny, cosy))
    return roll, pitch, yaw


def _ins(n, e, d, vn=None, ve=None, vd=None, an=None, ae=None, ad=None):
    if n is None and e is None and d is None:
        return None
    gs = None
    if vn is not None and ve is not None:
        gs = math.hypot(vn, ve)
    return {
        "position_ned_m": {"north": _num(n), "east": _num(e), "down": _num(d)},
        "velocity_ned_ms": {
            "north": _num(vn) if vn is not None else None,
            "east": _num(ve) if ve is not None else None,
            "down": _num(vd) if vd is not None else None,
        },
        "groundspeed_ms": _num(gs, 2) if gs is not None else None,
        "acceleration_ned_ms2": {
            "north": _num(an) if an is not None else None,
            "east": _num(ae) if ae is not None else None,
            "down": _num(ad) if ad is not None else None,
        },
    }


def _wind(east, north, up=None, temp=None):
    if east is None and north is None:
        return None
    east = east or 0.0
    north = north or 0.0
    speed = math.hypot(east, north)
    from_deg = (math.degrees(math.atan2(-east, -north)) + 360.0) % 360.0
    return {
        "speed_ms": _num(speed, 2), "from_deg": _num(from_deg, 1),
        "east_ms": _num(east, 2), "north_ms": _num(north, 2),
        "up_ms": _num(up, 2) if up is not None else None,
        "temperature_c": _num(temp, 1) if temp is not None else None,
    }


def _event(t, epoch, lat, lon, alt, volt, wind=None, ins=None):
    loc = None
    if lat is not None and lon is not None:
        loc = {"lat": _num(lat, 7), "lon": _num(lon, 7), "alt_m": _num(alt, 1)}
    return {
        "time_s": _num(t, 3), "utc": _utc(epoch), "location": loc,
        "voltage_v": _num(volt, 2) if volt is not None else None,
        "wind": wind, "ins": ins,
    }


def _assess(duration_s, climb_m, horiz_m, max_speed, max_tilt, motors_on_frac):
    score, reasons = 0.0, []
    if duration_s >= 30:
        score += 1
        reasons.append("in flight for over 30 s")
    elif duration_s >= 5:
        score += 0.5
        reasons.append("short airborne segment")
    if climb_m is not None:
        if climb_m >= 3:
            score += 1.5
            reasons.append("climbed more than 3 m")
        elif climb_m < 1.5:
            score -= 1.5
            reasons.append("altitude hardly changed")
    if horiz_m is not None:
        if horiz_m >= 3:
            score += 1.5
            reasons.append("moved more than 3 m horizontally")
        elif horiz_m >= 1:
            score += 0.5
            reasons.append("moved more than 1 m horizontally")
    ground_roll = climb_m is not None and horiz_m is not None and climb_m < 1.5 and horiz_m >= 3
    if ground_roll:
        score -= 4
        reasons.append("moved on the ground without climbing")
    if max_speed is not None and max_speed >= 1:
        score += 1
        reasons.append("speed above 1 m/s")
    if max_tilt is not None and max_tilt >= 10:
        score += 0.5
        reasons.append("tilted more than 10 degrees")
    motors_off = False
    if motors_on_frac is not None:
        if motors_on_frac < 0.5:
            score -= 3
            reasons.append("motors off for most of the flight")
            motors_off = True
        elif motors_on_frac >= 0.9:
            score += 0.5
            reasons.append("motors on throughout")
    if climb_m is None and horiz_m is None and not motors_off and duration_s >= 30:
        score += 2
        reasons.append("in flight for over 30 s without GPS")
    if motors_off or ground_roll:
        verdict = "ground"
    elif score >= 2.5 and duration_s >= 2:
        verdict = "flight"
    elif score <= 0:
        verdict = "ground"
    else:
        verdict = "uncertain"
    return {
        "verdict": verdict, "score": round(score, 2), "reasons": reasons,
        "metrics": {
            "ins_up_range_m": _num(climb_m, 2) if climb_m is not None else None,
            "baro_range_m": None, "gps_alt_range_m": _num(climb_m, 2) if climb_m is not None else None,
            "ins_horizontal_m": None, "gps_horizontal_m": _num(horiz_m, 2) if horiz_m is not None else None,
            "max_speed_ms": _num(max_speed, 2), "max_vertical_speed_ms": None,
            "motors_on_fraction": _num(motors_on_frac, 3) if motors_on_frac is not None else None,
            "max_tilt_deg": _num(max_tilt, 1), "acc_rms_ms2": None,
        },
        "optitrack": False,
    }


def _path_point(t, epoch, sample):
    lat, lon = sample.get("lat"), sample.get("lon")
    pos = None
    if lat is not None and lon is not None:
        pos = {
            "lat": _num(lat, 7), "lon": _num(lon, 7),
            "gps_alt_m": _num(sample.get("alt"), 1),
            "baro_alt_m": _num(sample.get("baro_alt"), 2) if sample.get("baro_alt") is not None else None,
            "pressure_pa": _num(sample.get("pressure"), 0) if sample.get("pressure") is not None else None,
        }
    elif sample.get("baro_alt") is not None:
        pos = {
            "lat": None, "lon": None, "gps_alt_m": None,
            "baro_alt_m": _num(sample.get("baro_alt"), 2),
            "pressure_pa": _num(sample.get("pressure"), 0) if sample.get("pressure") is not None else None,
        }
    att = {"euler_deg": None, "quaternion": None}
    if sample.get("roll") is not None:
        att["euler_deg"] = {
            "roll": _num(sample["roll"], 2), "pitch": _num(sample.get("pitch"), 2),
            "yaw": _num(sample.get("yaw"), 2),
        }
    if sample.get("qi") is not None:
        att["quaternion"] = {
            "qi": _num(sample["qi"], 5), "qx": _num(sample.get("qx"), 5),
            "qy": _num(sample.get("qy"), 5), "qz": _num(sample.get("qz"), 5),
        }
    air = None
    if sample.get("eas") is not None or sample.get("tas") is not None:
        air = {
            "eas_ms": _num(sample.get("eas"), 2), "tas_ms": _num(sample.get("tas"), 2),
            "diff_pressure_pa": _num(sample.get("diff_p"), 2) if sample.get("diff_p") is not None else None,
        }
    acts = sample.get("actuators")
    return {
        "time_s": _num(t, 3), "utc": _utc(epoch),
        "distance_m": _num(sample.get("distance_m"), 2) or 0,
        "from_takeoff_m": _num(sample.get("from_takeoff_m"), 2) or 0,
        "position": pos,
        "ins": _ins(sample.get("n"), sample.get("e"), sample.get("d"),
                    sample.get("vn"), sample.get("ve"), sample.get("vd"),
                    sample.get("an"), sample.get("ae"), sample.get("ad")),
        "groundspeed_ms": _num(sample.get("gs"), 2) if sample.get("gs") is not None else None,
        "attitude": att, "airspeed": air,
        "battery_voltage_v": _num(sample.get("volt"), 2) if sample.get("volt") is not None else None,
        "wind": _wind(sample.get("we"), sample.get("wn"), sample.get("wu"), sample.get("temp")) if (
            sample.get("we") is not None or sample.get("wn") is not None) else None,
        "flight_mode": {
            "ap_mode": sample.get("mode"), "h_mode": None, "v_mode": None,
        },
        "nav": {"block_id": None, "block": None, "stage_id": None, "stage": None},
        "actuators": acts if acts else None,
    }


def _merge_latest(streams):
    """Forward-fill every stream onto the union of timestamps so GPS, attitude and battery share samples."""
    times = sorted({round(t, 3) for rows in streams for t, _ in rows})
    if not times:
        return []
    idxs = [0] * len(streams)
    latest, out = {}, []
    for t in times:
        for si, rows in enumerate(streams):
            while idxs[si] < len(rows) and rows[idxs[si]][0] <= t + 1e-6:
                latest.update(rows[idxs[si]][1])
                idxs[si] += 1
        if latest:
            out.append((t, dict(latest)))
    return out


def _split_flights(samples, armed, t0_epoch, path_rate, *, airborne=None, strict_positions=False):
    """samples: list of (t, dict). armed: list of (t, bool)."""
    if not samples:
        return []
    has_arming = bool(armed)
    if airborne is not None:
        events = sorted([(t, "armed", on) for t, on in armed]
                        + [(t, "airborne", on) for t, on in airborne])
        armed_on, flying = not armed, False
        active = []
        for t, kind, on in events:
            if kind == "armed":
                armed_on = on
            else:
                flying = on
            state = armed_on and flying
            if not active or state != active[-1][1]:
                active.append((t, state))
        armed = active
    t_end = samples[-1][0]
    if not armed:
        segments = [(samples[0][0], t_end, True)]
    else:
        segments = []
        state, start = False, None
        for t, on in armed:
            if on and not state:
                start = t
                state = True
            elif not on and state:
                complete = airborne is None or not next(
                    (flying for time_s, flying in reversed(airborne) if time_s <= t), True)
                segments.append((start, t, complete))
                state = False
                start = None
        if state:
            segments.append((start if start is not None else samples[0][0], t_end, False))
        if not segments and airborne is None:
            segments = [(samples[0][0], t_end, True)]

    dt = (1.0 / path_rate) if path_rate and path_rate > 0 else None
    sample_times = [s[0] for s in samples]
    flights = []
    for i, (t_a, t_b, complete) in enumerate(segments, start=1):
        window = [s for s in samples if t_a <= s[0] <= t_b]
        if not window:
            continue
        if dt:
            picked, nxt = [window[0]], window[0][0]
            for s in window[1:]:
                if s[0] + 1e-9 >= nxt + dt:
                    picked.append(s)
                    nxt = s[0]
            if picked[-1] is not window[-1]:
                picked.append(window[-1])
        else:
            picked = [window[0], window[-1]] if len(window) > 1 else window

        dist = 0.0
        max_from = 0.0
        prev = None
        origin = None
        climb_lo = climb_hi = None
        max_gs = None if strict_positions else 0.0
        max_tilt = 0.0
        path = []
        for t, sm in picked:
            if origin is None and sm.get("lat") is not None:
                origin = (sm["lat"], sm["lon"], sm.get("alt") or 0.0)
            if origin and sm.get("lat") is not None:
                horiz = _haversine_m(origin[0], origin[1], sm["lat"], sm["lon"])
                dalt = (sm.get("alt") or origin[2]) - origin[2]
                from_to = math.hypot(horiz, dalt)
            else:
                from_to = 0.0
            continuous = not strict_positions or (
                prev is not None and sm.get("_position_frame") == prev.get("_position_frame")
                and sm.get("_position_time") is not None and prev.get("_position_time") is not None
                and sm["_position_time"] - prev["_position_time"] <= 5)
            if prev and continuous and sm.get("lat") is not None and prev.get("lat") is not None:
                dist += _haversine_m(prev["lat"], prev["lon"], sm["lat"], sm["lon"])
            sm = dict(sm)
            sm["distance_m"] = dist
            sm["from_takeoff_m"] = from_to
            max_from = max(max_from, from_to)
            alt = sm.get("alt") if sm.get("alt") is not None else sm.get("baro_alt")
            if alt is not None:
                climb_lo = alt if climb_lo is None else min(climb_lo, alt)
                climb_hi = alt if climb_hi is None else max(climb_hi, alt)
            if sm.get("gs") is not None:
                max_gs = max(max_gs, sm["gs"]) if max_gs is not None else sm["gs"]
            if sm.get("roll") is not None and sm.get("pitch") is not None:
                max_tilt = max(max_tilt, math.hypot(sm["roll"], sm["pitch"]))
            path.append(_path_point(t, t0_epoch + t if t0_epoch is not None else None, sm))
            prev = sm

        first, last = window[0][1], window[-1][1]
        gps_first = next((s[1] for s in window if s[1].get("lat") is not None), first)
        gps_last = next((s[1] for s in reversed(window) if s[1].get("lat") is not None), last)
        if strict_positions:
            def endpoint(t):
                idx = bisect_right(sample_times, t) - 1
                sm = samples[idx][1] if idx >= 0 else {}
                if sm.get("_position_time") is None or not 0 <= t - sm["_position_time"] <= 5:
                    return {}
                return sm
            gps_first, gps_last = endpoint(t_a), endpoint(t_b)
        duration = t_b - t_a
        climb = (climb_hi - climb_lo) if climb_lo is not None else None
        if strict_positions:
            altitudes = [sm["alt"] for _, sm in window if sm.get("alt") is not None]
            if not altitudes:
                altitudes = [sm["baro_alt"] for _, sm in window if sm.get("baro_alt") is not None]
            climb = max(altitudes) - min(altitudes) if altitudes else None
        horiz = None
        gps_pts = [s[1] for s in window if s[1].get("lat") is not None]
        if len(gps_pts) >= 2:
            o = gps_pts[0]
            horiz = max(_haversine_m(o["lat"], o["lon"], p["lat"], p["lon"]) for p in gps_pts)
        motors = 1.0 if has_arming else None
        to = _event(t_a, t0_epoch + t_a if t0_epoch is not None else None,
                    gps_first.get("lat"), gps_first.get("lon"), gps_first.get("alt"), gps_first.get("volt") or first.get("volt"),
                    _wind(first.get("we"), first.get("wn")) if first.get("we") is not None else None,
                    _ins(first.get("n"), first.get("e"), first.get("d"), first.get("vn"), first.get("ve"), first.get("vd")))
        ld = _event(t_b, t0_epoch + t_b if t0_epoch is not None else None,
                    gps_last.get("lat"), gps_last.get("lon"), gps_last.get("alt"), gps_last.get("volt") or last.get("volt"),
                    _wind(last.get("we"), last.get("wn")) if last.get("we") is not None else None,
                    _ins(last.get("n"), last.get("e"), last.get("d"), last.get("vn"), last.get("ve"), last.get("vd")))
        flights.append({
            "flight": i, "duration_s": _num(duration, 3), "distance_m": _num(dist, 2) or 0,
            "from_takeoff_m": _num(from_to, 2) or 0, "max_from_takeoff_m": _num(max_from, 2) or 0,
            "landed": bool(complete),
            "assessment": _assess(duration, climb, horiz, max_gs, max_tilt, motors),
            "takeoff": to, "landing": ld, "path": path,
        })
    return flights


def _document(path, digest, size, log_type, type_basis, path_rate, t0_epoch, t_first, t_last,
              ac_id, name, versions, flights, info_messages, preflight, settings_timeline=None,
              parser_name=None, parser_version=None):
    total_time = sum(f["duration_s"] or 0 for f in flights)
    total_dist = sum(f["distance_m"] or 0 for f in flights)
    start_utc = _utc(t0_epoch) if t0_epoch is not None else None
    first_utc = _utc(t0_epoch + t_first if t0_epoch is not None else None)
    end_utc = _utc(t0_epoch + t_last if t0_epoch is not None else None)
    return {
        "parser": {
            "name": parser_name or PARSER_NAME,
            "version": parser_version or PARSER_VERSION,
            "path_rate_hz": path_rate,
        },
        "log": {
            "file": Path(path).name, "type": log_type, "type_basis": type_basis,
            "sha256": digest, "size_bytes": size,
            "start_utc": start_utc, "first_data_utc": first_utc, "end_utc": end_utc,
            "duration_s": _num(t_last - t_first, 3) if t_last is not None else 0,
            "total_flight_distance_m": _num(total_dist, 2) or 0,
        },
        "aircraft": [{
            "ac_id": ac_id, "name": name, "autopilot_versions": versions,
            "data_span": {"first_utc": first_utc, "last_utc": end_utc},
            "mission": {"pic": "", "pac": "", "gcs": "", "goal": ""},
            "summary": {
                "num_flights": len(flights), "total_flight_time_s": _num(total_time, 3) or 0,
                "total_flight_distance_m": _num(total_dist, 2) or 0,
            },
            "flights": flights,
            "preflight_checks": preflight,
            "info_messages": info_messages,
            "flight_plan": None, "settings": None,
            "settings_changes": {"timeline": settings_timeline or []},
            "xml": {"airframe": None, "flight_plan": None, "settings": None, "radio": None, "telemetry": None},
        }],
    }


def _push_msg(bucket, t, epoch, text, cap=MAX_INFO_MESSAGES):
    if len(bucket) >= cap:
        return
    bucket.append({"time_s": _num(t, 3) or 0, "utc": _utc(epoch), "text": text})


def _fmt_fields(fields, limit=10):
    parts = []
    for k, v in fields:
        if v is None or k in ("timestamp", "timestamp_sample", "time_utc_usec"):
            continue
        if isinstance(v, float):
            if not math.isfinite(v):
                continue
            s = f"{v:.4g}"
        else:
            s = str(v)
            if len(s) > 80:
                s = s[:77] + "..."
        parts.append(f"{k}={s}")
        if len(parts) >= limit:
            break
    return " ".join(parts)


def _high_rate_topic(name):
    n = name.lower()
    return any(n.startswith(p) or p in n for p in _HIGH_RATE)


def _mav_field(m, *names):
    fields = set(getattr(m, "_fieldnames", []) or [])
    for n in names:
        if n not in fields:
            continue
        try:
            return getattr(m, n)
        except (AttributeError, IndexError, TypeError):
            continue
    return None


# ---------------------------------------------------------------------------------------------------------------------
# PX4 ULog
# ---------------------------------------------------------------------------------------------------------------------
def _ds(ulog, name):
    matches = [d for d in ulog.data_list if d.name == name]
    if not matches:
        return None
    return max(matches, key=lambda d: len(d.data.get("timestamp", [])))


def _col(data, *names):
    for n in names:
        if n in data:
            return data[n]
    return None


def _at(arr, i, scale=1.0, none_if=None):
    if arr is None:
        return None
    try:
        v = arr[i]
    except (IndexError, TypeError):
        return None
    try:
        x = float(v) * scale
    except (TypeError, ValueError):
        return None
    if none_if is not None and x == none_if:
        return None
    if not math.isfinite(x):
        return None
    return x


def _px4_airframe(ulog):
    """PX4 airframe script name (quad_x, standard_plane, …), not the flight-controller hardware."""
    boot = ulog.msg_info_multiple_dict.get("boot_console_output") or []
    text = "\n".join(boot[0]) if boot and isinstance(boot[0], list) else "\n".join(str(x) for x in boot)
    m = _AIRFRAME_BOOT.search(text.replace("\n", ""))
    if not m:
        m = _AIRFRAME_BOOT.search(text)
    if m:
        return m.group(2).replace("-", "_")
    try:
        auto = int(ulog.initial_parameters.get("SYS_AUTOSTART") or 0)
    except (TypeError, ValueError):
        auto = 0
    if auto in _KNOWN_AUTOSTART:
        return _KNOWN_AUTOSTART[auto]
    if auto:
        return f"airframe_{auto}"
    try:
        mav = int(ulog.initial_parameters.get("MAV_TYPE") or 0)
    except (TypeError, ValueError):
        mav = 0
    return PX4_MAV_TYPE.get(mav)


def _parse_ulog(path, digest, size, path_rate):
    from pyulog import ULog

    ulog = ULog(str(path), disable_str_exceptions=True)
    datasets = list(ulog.data_list)
    if not datasets and not ulog.logged_messages:
        raise RuntimeError("ULog contains no datasets or logged messages")

    timestamps = []
    for d in datasets:
        ts = d.data.get("timestamp")
        if ts is not None and len(ts):
            timestamps.append(int(ts[0]))
            timestamps.append(int(ts[-1]))
    t_boot = int(ulog.start_timestamp or 0)
    if timestamps:
        t_min, t_max = min(timestamps), max(timestamps)
        if t_boot <= 0 or t_boot > t_min:
            t_boot = t_min
    else:
        t_min = t_boot
        t_max = t_boot
    t0 = t_boot

    def rel(us):
        return (int(us) - t0) / 1e6

    # Wall clock from GPS UTC when present.
    t0_epoch = None
    gps = _ds(ulog, "vehicle_gps_position")
    if gps is not None:
        utc_us = _col(gps.data, "time_utc_usec", "time_utc")
        ts = gps.data.get("timestamp")
        if utc_us is not None and ts is not None:
            for i, u in enumerate(utc_us):
                u = int(u)
                if u > 1_000_000_000_000:  # microseconds after ~2001
                    t0_epoch = u / 1e6 - rel(ts[i])
                    break
                if 1_000_000_000 < u < 1_000_000_000_000:  # already seconds
                    t0_epoch = u - rel(ts[i])
                    break
    if t0_epoch is None:
        ref = ulog.msg_info_dict.get("time_ref_utc")
        try:
            ref = int(ref)
        except (TypeError, ValueError):
            ref = 0
        if ref and ref > 1_000_000_000:
            t0_epoch = ref

    def _info_str(key):
        v = ulog.msg_info_dict.get(key)
        if v is None:
            return ""
        if isinstance(v, bytes):
            v = v.decode("utf-8", "replace")
        return str(v).replace("\x00", "").strip()

    sysid = 1
    vs = _ds(ulog, "vehicle_status")
    if vs is not None:
        sid = _col(vs.data, "system_id")
        if sid is not None and len(sid):
            sysid = int(sid[0])
    uuid = _info_str("sys_uuid")
    hw = _info_str("ver_hw")
    short = uuid.replace("-", "")[-8:] if uuid else ""
    airframe = _px4_airframe(ulog)
    if airframe and short:
        ac_name = f"{airframe} {short}"
    elif airframe:
        ac_name = airframe
    elif short:
        ac_name = f"PX4 {short}"
    else:
        ac_name = f"PX4-{sysid}" if sysid else Path(path).stem
    versions = []
    sw = _info_str("ver_sw")
    if sw:
        extra = []
        sw_rel = ulog.msg_info_dict.get("ver_sw_release")
        if sw_rel is not None:
            extra.append(f"release {sw_rel}")
        if hw:
            extra.append(hw)
        versions.append("PX4 " + sw + ((" (" + ", ".join(map(str, extra)) + ")") if extra else ""))

    # Latest-value merge of telemetry streams.
    def series_rows(ds, builder):
        if ds is None:
            return []
        ts = ds.data.get("timestamp")
        if ts is None:
            return []
        out = []
        for i, tus in enumerate(ts):
            row = builder(ds.data, i)
            if row is not None:
                out.append((rel(tus), row))
        return out

    def gps_builder(data, i):
        lat = _at(data.get("latitude_deg"), i)
        if lat is None:
            lat = _at(data.get("lat"), i)
            if lat is not None and abs(lat) > 90:
                lat *= 1e-7
        lon = _at(data.get("longitude_deg"), i)
        if lon is None:
            lon = _at(data.get("lon"), i)
            if lon is not None and abs(lon) > 180:
                lon *= 1e-7
        alt = _at(data.get("altitude_msl_m"), i)
        if alt is None:
            alt = _at(data.get("alt"), i)
            if alt is not None and abs(alt) > 100000:
                alt *= 0.001
        fix = _at(data.get("fix_type"), i)
        if not position_valid(lat, lon, data, i) or (fix is not None and fix < 3):
            return None
        satellites = _at(data.get("satellites_used"), i)
        if satellites is not None and satellites < 6:
            return None
        gs = _at(data.get("vel_m_s"), i)
        vn, ve, vd = _at(data.get("vel_n_m_s"), i), _at(data.get("vel_e_m_s"), i), _at(data.get("vel_d_m_s"), i)
        if gs is None and vn is not None and ve is not None:
            gs = math.hypot(vn, ve)
        return {"lat": lat, "lon": lon, "alt": alt, "gs": gs, "vn": vn, "ve": ve, "vd": vd}

    def gpos_builder(data, i):
        lat, lon, alt = _at(data.get("lat"), i), _at(data.get("lon"), i), _at(data.get("alt"), i)
        if not position_valid(lat, lon, data, i) or _at(data.get("dead_reckoning"), i):
            return None
        return {"lat": lat, "lon": lon, "alt": alt}

    def position_valid(lat, lon, data, i):
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return False
        if lat == 0 and lon == 0:
            return False
        for field, limit in (("eph", 10), ("epv", 15)):
            if field in data:
                error = _at(data[field], i)
                if error is None or not 0 <= error <= limit:
                    return False
        return True

    def att_builder(data, i):
        w = _at(_col(data, "q[0]", "q0"), i)
        x = _at(_col(data, "q[1]", "q1"), i)
        y = _at(_col(data, "q[2]", "q2"), i)
        z = _at(_col(data, "q[3]", "q3"), i)
        if w is None:
            return None
        roll, pitch, yaw = _quat_euler_deg(w, x, y, z)
        return {"qi": w, "qx": x, "qy": y, "qz": z, "roll": roll, "pitch": pitch, "yaw": yaw}

    def local_builder(data, i):
        def valid(field):
            return field not in data or bool(_at(data[field], i))

        return {
            "_local_time": rel(data["timestamp"][i]),
            "n": _at(data.get("x"), i) if valid("xy_valid") else None,
            "e": _at(data.get("y"), i) if valid("xy_valid") else None,
            "d": _at(data.get("z"), i) if valid("z_valid") else None,
            "vn": _at(data.get("vx"), i) if valid("v_xy_valid") else None,
            "ve": _at(data.get("vy"), i) if valid("v_xy_valid") else None,
            "vd": _at(data.get("vz"), i) if valid("v_z_valid") else None,
            "an": _at(data.get("ax"), i) if valid("v_xy_valid") else None,
            "ae": _at(data.get("ay"), i) if valid("v_xy_valid") else None,
            "ad": _at(data.get("az"), i) if valid("v_z_valid") else None,
        }

    def bat_builder(data, i):
        v = _at(data.get("voltage_v"), i)
        if v is None:
            v = _at(data.get("voltage_filtered_v"), i)
        return {"volt": v} if v is not None else None

    def air_builder(data, i):
        return {
            "baro_alt": _at(data.get("baro_alt_meter"), i),
            "pressure": _at(data.get("baro_pressure_pa"), i),
            "temp": _at(data.get("baro_temp_celcius"), i),
        }

    def aspd_builder(data, i):
        return {
            "eas": _at(_col(data, "indicated_airspeed_m_s", "calibrated_airspeed_m_s"), i),
            "tas": _at(data.get("true_airspeed_m_s"), i),
        }

    def wind_builder(data, i):
        return {
            "wn": _at(_col(data, "windspeed_north", "north"), i),
            "we": _at(_col(data, "windspeed_east", "east"), i),
            "wu": _at(_col(data, "windspeed_up", "up"), i),
        }

    def act_builder(data, i):
        n = int(_at(data.get("noutputs"), i) or 16)
        acts = {}
        for k in range(min(n, 16)):
            v = _at(data.get(f"output[{k}]"), i)
            if v is not None:
                acts[f"ACT_{k}"] = _num(v, 1)
        return {"actuators": acts} if acts else None

    def mode_builder(data, i):
        nav = _at(data.get("nav_state"), i)
        return {"mode": PX4_NAV.get(int(nav), f"nav_{int(nav)}") if nav is not None else None}

    streams = []
    global_position = _ds(ulog, "vehicle_global_position")
    gps_rows = series_rows(gps, gps_builder)
    recording_start = rel(ulog.start_timestamp)
    recording_end = rel(ulog.last_timestamp)
    continuous_gps = bool(gps_rows) and (
        gps_rows[0][0] <= recording_start + 5
        and gps_rows[-1][0] >= recording_end - 5
        and all(b[0] - a[0] <= 5 for a, b in zip(gps_rows, gps_rows[1:])))
    if continuous_gps:
        position_source, position_builder, ds = "vehicle_gps_position", gps_builder, gps
    elif global_position is not None and len(global_position.data.get("timestamp", [])):
        position_source, position_builder, ds = "vehicle_global_position", gpos_builder, global_position
    else:
        position_source, position_builder, ds = "vehicle_gps_position", gps_builder, gps
    positions = []
    if ds is not None:
        for i, tus in enumerate(ds.data["timestamp"]):
            t = rel(tus)
            row = position_builder(ds.data, i)
            frame = (position_source, _at(ds.data.get("lat_lon_reset_counter"), i),
                     _at(ds.data.get("alt_reset_counter"), i))
            position = dict(lat=None, lon=None, alt=None, gs=None, _position_time=None, _position_frame=None)
            if row is not None:
                position.update(row, _position_time=t, _position_frame=frame)
            positions.append((t, position))
    if positions:
        streams.append(positions)
    for topic, builder in (
        ("vehicle_attitude", att_builder),
        ("vehicle_local_position", local_builder),
        ("battery_status", bat_builder),
        ("vehicle_air_data", air_builder),
        ("airspeed_validated", aspd_builder),
        ("airspeed", aspd_builder),
        ("wind", wind_builder),
        ("estimator_wind", wind_builder),
        ("actuator_outputs", act_builder),
        ("vehicle_status", mode_builder),
    ):
        rows = series_rows(_ds(ulog, topic), builder)
        if rows:
            streams.append(rows)

    armed_series = []
    cm = _ds(ulog, "vehicle_control_mode")
    if cm is not None and "flag_armed" in cm.data:
        prev = None
        for i, tus in enumerate(cm.data["timestamp"]):
            on = bool(int(cm.data["flag_armed"][i]))
            if prev is None or on != prev:
                armed_series.append((rel(tus), on))
                prev = on
    elif vs is not None and "arming_state" in vs.data:
        states = [int(x) for x in vs.data["arming_state"]]
        armed_val = 3 if (states and max(states) >= 3) else 2
        prev = None
        for i, tus in enumerate(vs.data["timestamp"]):
            on = int(vs.data["arming_state"][i]) == armed_val
            if prev is None or on != prev:
                armed_series.append((rel(tus), on))
                prev = on

    land = _ds(ulog, "vehicle_land_detected")
    airborne = None
    if land is not None and "landed" in land.data and len(land.data["timestamp"]):
        airborne = []
        streams.append([(rel(tus), {"_airborne": not bool(land.data["landed"][i])})
                        for i, tus in enumerate(land.data["timestamp"])])
        for i, tus in enumerate(land.data["timestamp"]):
            on = not bool(land.data["landed"][i])
            if not airborne or on != airborne[-1][1]:
                airborne.append((rel(tus), on))
    if cm is not None and "flag_armed" in cm.data:
        streams.append([(rel(tus), {"_armed": bool(cm.data["flag_armed"][i])})
                        for i, tus in enumerate(cm.data["timestamp"])])
    else:
        streams.append([(t, {"_armed": on}) for t, on in armed_series])
    samples = _merge_latest(streams)
    for t, sm in samples:
        if sm.get("_position_time") is not None and t - sm["_position_time"] > 5:
            sm.update(lat=None, lon=None, alt=None, gs=None, _position_time=None, _position_frame=None)
        if sm.get("_local_time") is not None and t - sm["_local_time"] > 5:
            sm.update(n=None, e=None, d=None, vn=None, ve=None, vd=None, an=None, ae=None, ad=None)
        if sm.get("gs") is None and sm.get("vn") is not None and sm.get("ve") is not None:
            sm["gs"] = math.hypot(sm["vn"], sm["ve"])
    flights = _split_flights(samples, armed_series, t0_epoch, path_rate,
                             airborne=airborne, strict_positions=True)

    # Messages: every logged printf, events, status changes, then other topics up to the cap.
    info_messages = []
    preflight = []
    epoch = lambda t: (t0_epoch + t) if t0_epoch is not None else None

    topic_bits = []
    for d in datasets:
        n = len(d.data.get("timestamp", []))
        topic_bits.append(f"{d.name}[{d.multi_id}] x{n}")
    _push_msg(info_messages, 0, epoch(0),
              f"[topics] {len(datasets)} ULog topics, {sum(len(d.data.get('timestamp', [])) for d in datasets)} samples: "
              + ", ".join(topic_bits[:80]) + (" …" if len(topic_bits) > 80 else ""))

    level_name = {ord("0"): "EMERGENCY", ord("1"): "ALERT", ord("2"): "CRITICAL", ord("3"): "ERROR",
                  ord("4"): "WARNING", ord("5"): "NOTICE", ord("6"): "INFO", ord("7"): "DEBUG"}
    for m in ulog.logged_messages:
        t = rel(m.timestamp)
        lvl = level_name.get(m.log_level, "")
        text = (m.message or "").rstrip()
        if lvl and lvl != "INFO":
            line = f"[{lvl}] {text}"
        else:
            line = text
        _push_msg(info_messages, t, epoch(t), line)
        if "preflight" in text.lower() or "health_and_arming" in text.lower() or "arming check" in text.lower():
            preflight.append({"time_s": _num(t, 3) or 0, "utc": _utc(epoch(t)), "text": text})

    ev = _ds(ulog, "event")
    if ev is not None:
        ts = ev.data.get("timestamp")
        ids = ev.data.get("id")
        if ts is not None and ids is not None:
            for i, tus in enumerate(ts):
                t = rel(tus)
                _push_msg(info_messages, t, epoch(t), f"event id={int(ids[i])}")

    if vs is not None:
        prev_key = None
        ts = vs.data["timestamp"]
        for i, tus in enumerate(ts):
            arm = int(vs.data["arming_state"][i]) if "arming_state" in vs.data else None
            nav = int(vs.data["nav_state"][i]) if "nav_state" in vs.data else None
            fail = int(vs.data["failsafe"][i]) if "failsafe" in vs.data else None
            key = (arm, nav, fail)
            if key == prev_key:
                continue
            prev_key = key
            t = rel(tus)
            fields = []
            if arm is not None:
                fields.append(f"arming_state={arm}")
            if nav is not None:
                fields.append(f"nav_state={PX4_NAV.get(nav, nav)}")
            if fail:
                fields.append("failsafe=1")
            vt = _at(vs.data.get("vehicle_type"), i)
            if vt is not None:
                fields.append(f"vehicle={PX4_VEHICLE.get(int(vt), int(vt))}")
            _push_msg(info_messages, t, epoch(t), "vehicle_status " + " ".join(fields))

    # Parameter changes (rare in these logs, but they are messages).
    for item in ulog.changed_parameters:
        if len(item) >= 3:
            tus, key, val = item[0], item[1], item[2]
            t = rel(tus)
            _push_msg(info_messages, t, epoch(t), f"param {key}={val}")

    # Remaining topics, downsampled, skipping IMU-class.
    per_type = {}
    last_t = {}
    for d in datasets:
        if d.name in {"vehicle_gps_position", "vehicle_global_position", "vehicle_attitude",
                      "vehicle_local_position", "vehicle_status", "event", "battery_status",
                      "vehicle_air_data", "airspeed_validated"}:
            continue
        if _high_rate_topic(d.name):
            continue
        ts = d.data.get("timestamp")
        if ts is None:
            continue
        keys = [k for k in d.data.keys() if k not in ("timestamp", "timestamp_sample")][:12]
        type_key = d.name
        for i, tus in enumerate(ts):
            t = rel(tus)
            if last_t.get(type_key) is not None and t - last_t[type_key] < 1.0:
                continue
            if per_type.get(type_key, 0) >= MAX_PER_TYPE:
                break
            if len(info_messages) >= MAX_INFO_MESSAGES:
                break
            fields = [(k, d.data[k][i] if i < len(d.data[k]) else None) for k in keys]
            text = f"{d.name} " + _fmt_fields(fields)
            _push_msg(info_messages, t, epoch(t), text.rstrip())
            per_type[type_key] = per_type.get(type_key, 0) + 1
            last_t[type_key] = t
        if len(info_messages) >= MAX_INFO_MESSAGES:
            break

    info_messages.sort(key=lambda m: m["time_s"])
    t_first = 0.0
    t_last = rel(t_max) if t_max else (samples[-1][0] if samples else 0.0)
    return _document(path, digest, size, LOG_PX4, "ULog magic", path_rate, t0_epoch, t_first, t_last,
                     sysid, ac_name, versions, flights, info_messages, preflight)


# ---------------------------------------------------------------------------------------------------------------------
# ArduPilot DataFlash (.bin / text .log)
# ---------------------------------------------------------------------------------------------------------------------
def _parse_ardupilot(path, digest, size, path_rate):
    from pymavlink.DFReader import DFReader_binary, DFReader_text

    head = Path(path).read_bytes()[:8]
    df = DFReader_binary(str(path)) if head.startswith(DF_HEAD) else DFReader_text(str(path))
    samples_map = {}
    armed_series = []
    info_messages = []
    preflight = []
    versions = []
    settings = []
    per_type = {}
    last_t = {}
    t0_epoch = None
    t_first = None
    t_last = 0.0
    sysid = 1
    vehicle = None
    prev_armed = None

    def epoch(t):
        return (t0_epoch + t) if t0_epoch is not None else None

    while True:
        try:
            m = df.recv_msg()
        except (TypeError, IndexError, ValueError):
            break
        if m is None:
            break
        name = m.get_type()
        if name in ("FMT", "FMTU", "UNIT", "MULT"):
            continue
        ts = getattr(m, "_timestamp", None)
        if ts is None:
            continue
        ts = float(ts)
        if t0_epoch is None and ts > 1_000_000_000:
            # DFReader gives unix seconds when GPS time is known.
            t0_epoch = ts
            t_rel = 0.0
        elif t0_epoch is not None:
            t_rel = ts - t0_epoch
        else:
            t_rel = ts if ts < 1_000_000_000 else 0.0
        if t_first is None:
            t_first = t_rel
        t_last = t_rel

        if name == "MSG":
            text = str(_mav_field(m, "Message", "Msg") or "")
            _push_msg(info_messages, t_rel, epoch(t_rel), text)
            low = text.lower()
            if "apm:" in low or "ardupilot" in low or "chibios" in low or "px4v" in low:
                versions.append(text)
            if "prearm" in low or "preflight" in low or "arming" in low:
                preflight.append({"time_s": _num(t_rel, 3) or 0, "utc": _utc(epoch(t_rel)), "text": text})
            continue
        if name in ("ERR", "EV", "CMD", "MAV", "STATUSTEXT"):
            fields = [(k, _mav_field(m, k)) for k in getattr(m, "_fieldnames", [])]
            _push_msg(info_messages, t_rel, epoch(t_rel), f"{name} " + _fmt_fields(fields))
            continue
        if name == "MODE":
            mode = _mav_field(m, "Mode", "ModeNum")
            _push_msg(info_messages, t_rel, epoch(t_rel), f"MODE {mode}")
            samples_map.setdefault(round(t_rel, 3), {})["mode"] = str(mode) if mode is not None else None
            continue
        if name == "ARM":
            on = bool(int(_mav_field(m, "ArmState") or 0))
            if prev_armed is None or on != prev_armed:
                armed_series.append((t_rel, on))
                prev_armed = on
            _push_msg(info_messages, t_rel, epoch(t_rel), f"ARM ArmState={int(on)}")
            continue
        if name == "PARM":
            pname = _mav_field(m, "Name") or ""
            pval = _mav_field(m, "Value")
            settings.append({
                "time_s": _num(t_rel, 3), "utc": _utc(epoch(t_rel)), "previous_report_s": None,
                "index": len(settings), "var": str(pname), "from": None, "to": pval, "in_flight": False,
            })
            if str(pname) in {"SYSID_THISMAV", "MAV_SYSID"}:
                try:
                    sysid = int(float(pval))
                except (TypeError, ValueError):
                    pass
            if per_type.get("PARM", 0) < MAX_PER_TYPE:
                _push_msg(info_messages, t_rel, epoch(t_rel), f"PARM {pname}={pval}")
                per_type["PARM"] = per_type.get("PARM", 0) + 1
            continue
        if name == "GPS":
            lat = _mav_field(m, "Lat")
            lon = _mav_field(m, "Lng", "Lon")
            if lat is not None and abs(float(lat)) > 90:
                lat = float(lat) * 1e-7
            if lon is not None and abs(float(lon)) > 180:
                lon = float(lon) * 1e-7
            alt = _mav_field(m, "Alt")
            spd = _mav_field(m, "Spd")
            status = _mav_field(m, "Status")
            if status is not None and int(status) < 2:
                continue
            if lat and lon:
                slot = samples_map.setdefault(round(t_rel, 3), {})
                slot.update({"lat": float(lat), "lon": float(lon), "alt": float(alt) if alt is not None else None,
                             "gs": float(spd) if spd is not None else None})
            continue
        if name in ("ATT", "AHR2"):
            slot = samples_map.setdefault(round(t_rel, 3), {})
            slot.update({
                "roll": float(_mav_field(m, "Roll") or 0),
                "pitch": float(_mav_field(m, "Pitch") or 0),
                "yaw": float(_mav_field(m, "Yaw") or 0),
            })
            continue
        if name in ("BAT", "CURR", "POWR"):
            volt = _mav_field(m, "Volt", "Vcc")
            if volt is not None:
                samples_map.setdefault(round(t_rel, 3), {})["volt"] = float(volt)
            continue
        if name == "CTUN":
            aspd = _mav_field(m, "As", "E2T")
            if aspd is not None:
                samples_map.setdefault(round(t_rel, 3), {})["eas"] = float(aspd)
            continue
        if name == "RCOU":
            acts = {}
            for k in range(1, 17):
                v = _mav_field(m, f"C{k}")
                if v is not None:
                    acts[f"ACT_{k-1}"] = float(v)
            if acts:
                samples_map.setdefault(round(t_rel, 3), {})["actuators"] = acts
            continue
        if name in ("HEARTBEAT",):
            base = int(getattr(m, "base_mode", 0) or 0)
            on = bool(base & 128)
            if prev_armed is None or on != prev_armed:
                armed_series.append((t_rel, on))
                prev_armed = on
            continue

        if name in _AP_HIGH_RATE:
            continue
        if last_t.get(name) is not None and t_rel - last_t[name] < 1.0:
            continue
        if per_type.get(name, 0) >= MAX_PER_TYPE:
            continue
        fields = [(k, _mav_field(m, k)) for k in getattr(m, "_fieldnames", [])]
        _push_msg(info_messages, t_rel, epoch(t_rel), f"{name} " + _fmt_fields(fields))
        per_type[name] = per_type.get(name, 0) + 1
        last_t[name] = t_rel

    if t_first is None:
        t_first = 0.0
    latest, samples = {}, []
    for t, row in sorted(samples_map.items()):
        latest.update(row)
        samples.append((t, dict(latest)))
    flights = _split_flights(samples, armed_series, t0_epoch, path_rate)
    mavtype = getattr(df, "mav_type", None)
    name = f"ArduPilot-{sysid}"
    if vehicle:
        name = f"{vehicle}-{sysid}"
    _push_msg(info_messages, 0, epoch(0),
              f"[topics] ArduPilot DataFlash types seen: " + ", ".join(sorted(per_type)[:80]),
              cap=MAX_INFO_MESSAGES + 1)
    info_messages.sort(key=lambda m: m["time_s"])
    return _document(path, digest, size, LOG_AP, "DataFlash", path_rate, t0_epoch, t_first, t_last,
                     sysid, name, versions, flights, info_messages, preflight, settings)


# ---------------------------------------------------------------------------------------------------------------------
# MAVLink .tlog
# ---------------------------------------------------------------------------------------------------------------------
def _parse_tlog(path, digest, size, path_rate):
    from pymavlink import mavutil

    mlog = mavutil.mavlink_connection(str(path), robust_parsing=True)
    samples_map = {}
    armed_series = []
    info_messages = []
    preflight = []
    versions = []
    per_type = {}
    last_t = {}
    t0_epoch = None
    t_first = None
    t_last = 0.0
    sysid = 1
    prev_armed = None

    def epoch(t):
        return (t0_epoch + t) if t0_epoch is not None else None

    skip_full = {"BAD_DATA", "UNKNOWN"}
    high = {"HIGHRES_IMU", "RAW_IMU", "SCALED_IMU", "SCALED_IMU2", "SCALED_IMU3", "ATTITUDE_QUATERNION",
            "LOCAL_POSITION_NED", "VFR_HUD", "RC_CHANNELS", "SERVO_OUTPUT_RAW", "SCALED_PRESSURE"}

    while True:
        m = mlog.recv_match(blocking=False)
        if m is None:
            break
        name = m.get_type()
        if name in skip_full:
            continue
        ts = getattr(m, "_timestamp", None)
        if ts is None:
            continue
        ts = float(ts)
        if t0_epoch is None:
            t0_epoch = ts if ts > 1_000_000_000 else 0.0
        t_rel = ts - t0_epoch if t0_epoch else ts
        if t_first is None:
            t_first = t_rel
        t_last = t_rel
        sysid = getattr(m, "get_srcSystem", lambda: sysid)() if callable(getattr(m, "get_srcSystem", None)) else sysid
        try:
            sysid = m.get_srcSystem()
        except Exception:
            pass

        if name == "STATUSTEXT":
            text = str(getattr(m, "text", "") or "").rstrip("\x00")
            _push_msg(info_messages, t_rel, epoch(t_rel), text)
            low = text.lower()
            if "ardupilot" in low or "px4" in low:
                versions.append(text)
            if "prearm" in low or "preflight" in low:
                preflight.append({"time_s": _num(t_rel, 3) or 0, "utc": _utc(epoch(t_rel)), "text": text})
            continue
        if name == "HEARTBEAT":
            base = int(getattr(m, "base_mode", 0) or 0)
            on = bool(base & 128)
            if prev_armed is None or on != prev_armed:
                armed_series.append((t_rel, on))
                prev_armed = on
                _push_msg(info_messages, t_rel, epoch(t_rel),
                          f"HEARTBEAT armed={int(on)} type={getattr(m, 'type', '')} autopilot={getattr(m, 'autopilot', '')}")
            continue
        if name in ("GPS_RAW_INT", "GLOBAL_POSITION_INT"):
            lat = getattr(m, "lat", None)
            lon = getattr(m, "lon", None)
            if lat is not None:
                lat = float(lat) * 1e-7
            if lon is not None:
                lon = float(lon) * 1e-7
            alt = getattr(m, "alt", None)
            if alt is not None:
                alt = float(alt) / 1000.0
            gs = getattr(m, "vel", None)
            if gs is not None:
                gs = float(gs) / 100.0
            if lat and lon:
                samples_map.setdefault(round(t_rel, 3), {}).update(
                    {"lat": lat, "lon": lon, "alt": alt, "gs": gs})
            continue
        if name == "ATTITUDE":
            samples_map.setdefault(round(t_rel, 3), {}).update({
                "roll": math.degrees(float(m.roll)),
                "pitch": math.degrees(float(m.pitch)),
                "yaw": math.degrees(float(m.yaw)),
            })
            continue
        if name in ("SYS_STATUS", "BATTERY_STATUS"):
            mv = getattr(m, "voltage_battery", None)
            if mv is None:
                v = getattr(m, "voltages", None)
                mv = v[0] if v else None
            if mv is not None and int(mv) not in (0, 65535):
                samples_map.setdefault(round(t_rel, 3), {})["volt"] = float(mv) / 1000.0
            continue
        if name == "VFR_HUD":
            slot = samples_map.setdefault(round(t_rel, 3), {})
            if getattr(m, "airspeed", None):
                slot["eas"] = float(m.airspeed)
            if getattr(m, "groundspeed", None):
                slot["gs"] = float(m.groundspeed)
            continue
        if name == "COMMAND_LONG" or name == "COMMAND_ACK" or name.endswith("_ACK"):
            fields = [(k, getattr(m, k, None)) for k in getattr(m, "_fieldnames", [])]
            _push_msg(info_messages, t_rel, epoch(t_rel), f"{name} " + _fmt_fields(fields))
            continue
        if name in high:
            continue
        if last_t.get(name) is not None and t_rel - last_t[name] < 1.0:
            continue
        if per_type.get(name, 0) >= MAX_PER_TYPE:
            continue
        fields = [(k, getattr(m, k, None)) for k in getattr(m, "_fieldnames", [])]
        _push_msg(info_messages, t_rel, epoch(t_rel), f"{name} " + _fmt_fields(fields))
        per_type[name] = per_type.get(name, 0) + 1
        last_t[name] = t_rel

    mlog.close()
    if t_first is None:
        t_first = 0.0
    latest, samples = {}, []
    for t, row in sorted(samples_map.items()):
        latest.update(row)
        samples.append((t, dict(latest)))
    flights = _split_flights(samples, armed_series, t0_epoch, path_rate)
    _push_msg(info_messages, 0, epoch(0),
              "[topics] MAVLink types: " + ", ".join(sorted(per_type)[:80]))
    info_messages.sort(key=lambda m: m["time_s"])
    return _document(path, digest, size, LOG_TLOG, "MAVLink tlog", path_rate, t0_epoch, t_first, t_last,
                     sysid, f"MAVLink-{sysid}", versions, flights, info_messages, preflight)


# ---------------------------------------------------------------------------------------------------------------------
# Betaflight / INDIFlight Blackbox (.bfl / .bbl)
# ---------------------------------------------------------------------------------------------------------------------
def _blackbox_headers(path):
    headers = {}
    with open(path, "rb") as handle:
        while True:
            line = handle.readline()
            if not line:
                break
            if line.startswith(b"H "):
                text = line.decode("ascii", "replace").rstrip("\r\n")
                key, _, value = text[2:].partition(":")
                if key:
                    headers[key.strip()] = value.strip()
            elif headers:
                break
    return headers


def _blackbox_kind(headers):
    blob = " ".join(headers.get(key, "") for key in ("Firmware type", "Firmware revision")).lower()
    if "indiflight" in blob:
        return LOG_INDI, "INDIFlight Blackbox"
    return LOG_BF, "Betaflight Blackbox"


def _blackbox_epoch(headers):
    raw = headers.get("Log start datetime") or ""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _blackbox_dshot(headers):
    raw = headers.get("motorOutput") or ""
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) >= 2:
        try:
            return int(float(parts[0])), int(float(parts[1]))
        except ValueError:
            pass
    return tuple(int(v) for v in BLACKBOX_DSHOT)


def _blackbox_high_res(headers):
    raw = headers.get("blackbox_high_resolution")
    if raw in (None, ""):
        return 1.0
    try:
        return 10.0 if int(float(raw)) else 1.0
    except (TypeError, ValueError):
        return 1.0


def _blackbox_si_value(col, x, high_res, acc_1g, motors_lo, motors_hi):
    """INDIFlight log-tools `_processData` scaling: decoder CSV in SI units."""
    if col == "loopIteration":
        return int(x)
    if col == "rcCommand[3]":
        return (x - 1000.0 * high_res) / (1000.0 * high_res)
    if col.startswith("rcCommand"):
        return x / (500.0 * high_res)
    if col.startswith("gyro"):
        return x / (BLACKBOX_DEG * high_res)
    if col.startswith("accSp"):
        return x / 100.0
    if col.startswith("acc"):
        return x * BLACKBOX_G / acc_1g
    if re.match(r"^motor\[[0-9]+\]$", col):
        span = max(1.0, motors_hi - motors_lo)
        return (x - motors_lo) / span
    if col.startswith(("quat", "extQuat", "ekf_quat", "localQuat")):
        return x / BLACKBOX_QUAT_SCALE
    if col.startswith("alpha"):
        return x / (0.1 * BLACKBOX_DEG)
    if col.startswith("spfSp"):
        return x / 100.0
    if col.startswith("dv"):
        return x / 10.0
    if col.startswith("u["):
        return x / BLACKBOX_QUAT_SCALE
    if col.startswith("u_state"):
        return x / BLACKBOX_QUAT_SCALE
    if col.startswith("omega_dot"):
        return x * 100.0
    if col.startswith("omega"):
        return x
    if col.startswith("servo_feedback"):
        return x / (100.0 * BLACKBOX_DEG)
    if col.startswith(("pos", "extPos", "ekf_pos", "localPos")):
        return x / 1000.0
    if col.startswith(("vel", "extVel", "ekf_vel", "localVel")):
        return x / 100.0
    if col.startswith(("extAtt", "ekf_att")):
        return x / 1000.0
    if col.startswith("ekf_acc_b"):
        return x / 1000.0
    if col.startswith("ekf_gyro_b"):
        return x / (BLACKBOX_DEG * 1000.0)
    if col.startswith("learnerGains"):
        return x / 10.0
    if col.startswith("hoverAttitude"):
        return x / BLACKBOX_QUAT_SCALE
    if col.endswith("_lambda"):
        return x / BLACKBOX_U16_SCALE
    if col.endswith("_e_var"):
        return x / (0.001 * ((1 << 16) - 1))
    if col in BLACKBOX_FLAG_COLS:
        return int(x)
    return x


def _blackbox_si_row(row, high_res, acc_1g, motors_lo, motors_hi):
    si = {}
    for col, val in row.items():
        if col not in BLACKBOX_KEEP:
            continue
        if col in BLACKBOX_FLAG_COLS and val in (None, ""):
            si[col] = 0
            continue
        number = _csv_num({col: val}, col)
        si[col] = _blackbox_si_value(col, number, high_res, acc_1g, motors_lo, motors_hi) if number is not None else val
    return si


def _csv_num(row, *keys):
    for key in keys:
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            return number
    return None


def _norm_csv_fields(names):
    out = []
    for name in names or []:
        name = (name or "").strip()
        if " (" in name:
            name = name.split(" (", 1)[0]
        out.append(name)
    return out


def _blackbox_indexes(stderr):
    if "multiple flight logs" not in (stderr or ""):
        return None
    out = []
    for line in stderr.splitlines():
        parts = line.split()
        if parts and parts[0].isdigit() and len(parts) >= 3:
            out.append(int(parts[0]))
    return out or [1]


def _blackbox_decode_cmd(decoder, path, index=None):
    cmd = [decoder, "--stdout", "--merge-gps", "--unit-flags", "raw"]
    if index is not None:
        cmd.extend(["--index", str(index)])
    cmd.append(str(path))
    return cmd


def _iter_blackbox_csv(path, decoder, index=None):
    decoder = decoder or blackbox_decode_bin()
    if not (os.path.isfile(decoder) and os.access(decoder, os.X_OK)):
        raise RuntimeError(f"Blackbox decoder not found or not executable: {decoder} (build it in cpp/)")
    try:
        proc = subprocess.Popen(
            _blackbox_decode_cmd(decoder, path, index),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError as exc:
        raise RuntimeError(f"Blackbox decoder failed to start: {exc}") from exc
    try:
        header_line = proc.stdout.readline()
        if not header_line.strip():
            err = (proc.stderr.read() or "").strip()
            proc.wait(timeout=30)
            indexes = _blackbox_indexes(err) if index is None else None
            if indexes:
                yield from ()
                return indexes
            raise RuntimeError(err or f"Blackbox decoder exit code {proc.returncode}")
        names = _norm_csv_fields(next(csv.reader([header_line], skipinitialspace=True)))
        idx_map = {name: i for i, name in enumerate(names) if name in BLACKBOX_KEEP}
        deadline = time.monotonic() + 300
        for line in proc.stdout:
            if time.monotonic() > deadline:
                proc.kill()
                raise RuntimeError(f"Blackbox decoder timed out on {Path(path).name}")
            parts = next(csv.reader([line], skipinitialspace=True))
            yield {name: parts[i] for name, i in idx_map.items() if i < len(parts)}
        err = (proc.stderr.read() or "").strip()
        proc.wait(timeout=30)
        if proc.returncode not in (0, None):
            raise RuntimeError(err or f"Blackbox decoder exit code {proc.returncode}")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()
    return None


def _blackbox_row_streams(path, decoder):
    stream = _iter_blackbox_csv(path, decoder)
    try:
        first = next(stream)
    except StopIteration as stop:
        indexes = stop.value
        if indexes:
            for index in indexes:
                yield _iter_blackbox_csv(path, decoder, index)
            return
        raise RuntimeError(f"Blackbox log {Path(path).name} contained no frames") from stop
    def with_first():
        yield first
        yield from stream
    yield with_first()


def _gps_point(row):
    lat, lon = _csv_num(row, "GPS_coord[0]"), _csv_num(row, "GPS_coord[1]")
    if lat is None or lon is None:
        return None, None, None, None
    if abs(lat) > 180 or abs(lon) > 180:
        lat, lon = lat / 1e7, lon / 1e7
    sats = _csv_num(row, "GPS_numSat")
    if sats is not None and sats < 5:
        return None, None, None, None
    if not -90 <= lat <= 90 or not -180 <= lon <= 180 or (lat == 0 and lon == 0):
        return None, None, None, None
    alt = _int32(_csv_num(row, "GPS_altitude"))
    if alt is not None and not -500 <= alt <= 20000:
        alt = None
    speed = _csv_num(row, "GPS_speed")
    if speed is not None and speed > 80:
        speed /= 100.0
    return lat, lon, alt, speed


def _int32(value):
    if value is None:
        return None
    x = int(round(value))
    if x >= 2 ** 31:
        x -= 2 ** 32
    elif x < -2 ** 31:
        x += 2 ** 32
    return float(x)


def _ned(row, prefix, scale):
    values = [_csv_num(row, f"{prefix}[{i}]") for i in range(3)]
    if any(v is None for v in values) or all(v == 0 for v in values):
        return None, None, None
    return tuple(v / scale for v in values)


def _ekf_axis_jump(prev, row, jump=BLACKBOX_EKF_JUMP_M):
    if prev is None:
        return False
    for i in range(3):
        a, b = _csv_num(prev, f"ekf_pos[{i}]"), _csv_num(row, f"ekf_pos[{i}]")
        if a is not None and b is not None and abs(b - a) >= jump:
            return True
    return False


def _blackbox_sample(row, indi):
    lat, lon, alt, gs = _gps_point(row)
    flags = int(_csv_num(row, "flightModeFlags") or 0)
    modes = []
    if flags & 1:
        modes.append("ARM")
    if flags & 2:
        modes.append("ANGLE")
    if flags & 4:
        modes.append("HORIZON")
    volt = _csv_num(row, "vbatLatest")
    if volt is not None and volt > 60:
        volt /= 100.0
    baro = _csv_num(row, "baroAlt")
    if baro is not None:
        baro /= 100.0
    if alt is not None and baro is not None and abs(alt - baro) > 50:
        alt = None
    actuators = []
    for i in range(8):
        value = _csv_num(row, f"motor[{i}]")
        if value is None:
            break
        actuators.append(round(max(0.0, min(1.0, value)), 3))
    quat_keys = "quat" if _csv_num(row, "quat[0]") is not None else "ekf_quat"
    quat = [_csv_num(row, f"{quat_keys}[{i}]") for i in range(4)]
    roll = pitch = yaw = None
    qi = qx = qy = qz = None
    if all(v is not None for v in quat):
        qi, qx, qy, qz = quat
        roll, pitch, yaw = _quat_euler_deg(*quat)
    pos = vel = ekf = ekf_vel = (None, None, None)
    if indi:
        pos, vel = _ned(row, "pos", 1.0), _ned(row, "vel", 1.0)
        ekf, ekf_vel = _ned(row, "ekf_pos", 1.0), _ned(row, "ekf_vel", 1.0)
    state = _csv_num(row, "stateFlags")
    tas = _csv_num(row, "pitot_airSpeed")
    return {
        "lat": lat, "lon": lon, "alt": alt, "gs": gs, "baro_alt": baro, "volt": volt,
        "roll": roll, "pitch": pitch, "yaw": yaw, "qi": qi, "qx": qx, "qy": qy, "qz": qz,
        "tas": tas, "mode": "+".join(modes) or "DISARM",
        "actuators": actuators or None,
        "_pos": pos, "_vel": vel, "_ekf": ekf, "_ekf_vel": ekf_vel,
        "_box_arm": bool(flags & 1),
        "_state_arm": None if state is None else bool(int(state) & 1),
    }


def _blackbox_collect(headers, rows, path_rate, indi):
    high_res = _blackbox_high_res(headers)
    acc_1g = _csv_num(headers, "acc_1G") or 2048.0
    motors_lo, motors_hi = (float(v) for v in _blackbox_dshot(headers))
    dt = (1.0 / path_rate) if path_rate and path_rate > 0 else None
    samples, wrapped = [], False
    t_first = t_last = t0_us = nxt = prev_si = None
    for raw in rows:
        row = _blackbox_si_row(raw, high_res, acc_1g, motors_lo, motors_hi)
        raw_t = _csv_num(row, "time")
        if raw_t is None:
            continue
        if t0_us is None:
            t0_us = raw_t
        t = (raw_t - t0_us) / 1e6
        t_first = t if t_first is None else t_first
        t_last = t
        if _ekf_axis_jump(prev_si, row):
            wrapped = True
        prev_si = row
        sample = _blackbox_sample(row, indi)
        keep = nxt is None or dt is None or t + 1e-9 >= nxt
        if not keep and samples:
            prev = samples[-1][1]
            keep = sample["_box_arm"] != prev["_box_arm"] or sample["_state_arm"] != prev["_state_arm"]
        if keep:
            samples.append((t, sample))
            if nxt is None or t + 1e-9 >= nxt:
                nxt = None if dt is None else t + dt
    if not samples:
        return [], [], None, None
    use_state = any(s["_state_arm"] for _, s in samples)
    use_ekf = indi and not wrapped
    armed, prev_armed = [], None
    for t, sample in samples:
        on = sample["_state_arm"] if use_state and sample["_state_arm"] is not None else sample["_box_arm"]
        n, e, d = sample["_ekf"] if use_ekf and sample["_ekf"][0] is not None else sample["_pos"]
        vn, ve, vd = sample["_ekf_vel"] if use_ekf and sample["_ekf_vel"][0] is not None else sample["_vel"]
        sample["n"], sample["e"], sample["d"] = n, e, d
        sample["vn"], sample["ve"], sample["vd"] = vn, ve, vd
        if prev_armed is None or on != prev_armed:
            armed.append((t, on))
            prev_armed = on
        for key in ("_pos", "_vel", "_ekf", "_ekf_vel", "_box_arm", "_state_arm"):
            sample.pop(key, None)
    return samples, armed, t_first, t_last


def _blackbox_finish(path, digest, size, headers, flights, t_first, t_last, path_rate):
    if not flights:
        raise RuntimeError(f"Blackbox log {Path(path).name} contained no frames")
    for i, flight in enumerate(flights, start=1):
        flight["flight"] = i
    kind, basis = _blackbox_kind(headers)
    t0_epoch = _blackbox_epoch(headers)
    craft = (headers.get("Craft name") or "").strip()
    name = craft or Path(path).stem
    revision = headers.get("Firmware revision") or headers.get("Firmware type") or basis
    versions = [revision] if revision else []
    info, preflight = [], []
    epoch = (lambda t: t0_epoch + t if t0_epoch is not None else None)
    for key in ("Firmware type", "Firmware revision", "Firmware date", "Board information",
                "Craft name", "Log start datetime"):
        value = headers.get(key)
        if value:
            _push_msg(info, 0, epoch(0) if t0_epoch is not None else None, f"{key}: {value}")
    return _document(path, digest, size, kind, basis, path_rate, t0_epoch, t_first, t_last,
                     1, name, versions, flights, info, preflight,
                     parser_name=BLACKBOX_PARSER_NAME, parser_version=BLACKBOX_PARSER_VERSION)


def _blackbox_document(path, digest, size, headers, rows, path_rate):
    kind, _basis = _blackbox_kind(headers)
    t0_epoch = _blackbox_epoch(headers)
    samples, armed, t_first, t_last = _blackbox_collect(headers, rows, path_rate, kind == LOG_INDI)
    flights = _split_flights(samples, armed, t0_epoch, path_rate) if samples else []
    return _blackbox_finish(path, digest, size, headers, flights, t_first, t_last, path_rate)


def _parse_blackbox(path, digest, size, path_rate, decoder=None):
    headers = _blackbox_headers(path)
    if "Blackbox" not in headers.get("Product", ""):
        raise RuntimeError(f"not a Blackbox log: {Path(path).name}")
    kind, _basis = _blackbox_kind(headers)
    t0_epoch = _blackbox_epoch(headers)
    flights, t_first, t_last = [], None, 0.0
    for rows in _blackbox_row_streams(path, decoder):
        samples, armed, tf, tl = _blackbox_collect(headers, rows, path_rate, kind == LOG_INDI)
        if not samples:
            continue
        flights.extend(_split_flights(samples, armed, t0_epoch, path_rate))
        t_first = 0.0 if t_first is None else t_first
        if tf is not None and tl is not None:
            t_last += tl - tf
    return _blackbox_finish(path, digest, size, headers, flights, t_first, t_last, path_rate)


def write_ulog(path, *, start_us=0, info=None, formats=None, logged=None, add=None, samples=None):
    """Minimal ULog writer used by tests. Not a general PX4 logger."""
    def msg(typ, payload):
        return struct.pack("<HB", len(payload), typ) + payload

    chunks = [ULOG_MAGIC + struct.pack("<BQ", 1, start_us)]
    chunks.append(msg(ord("B"), b"\x00" * 40))
    for fmt in formats or []:
        chunks.append(msg(ord("F"), fmt.encode("utf-8")))
    for key, value in (info or {}).items():
        raw = value.encode("utf-8") + b"\x00"
        key_s = f"char[{len(raw)}] {key}".encode("utf-8")
        chunks.append(msg(ord("I"), bytes([len(key_s)]) + key_s + raw))
    for multi_id, msg_id, name in add or []:
        chunks.append(msg(ord("A"), struct.pack("<BH", multi_id, msg_id) + name.encode("utf-8")))
    for ts, level, text in logged or []:
        chunks.append(msg(ord("L"), bytes([level]) + struct.pack("<Q", ts) + text.encode("utf-8")))
    for msg_id, payload in samples or []:
        chunks.append(msg(ord("D"), struct.pack("<H", msg_id) + payload))
    Path(path).write_bytes(b"".join(chunks))
