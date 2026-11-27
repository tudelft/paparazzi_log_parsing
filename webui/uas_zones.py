"""Drone (UAS) zones: the geographical zones of a country, imported by an administrator from an ED-269 JSON file.

The Dutch government publishes its zones as such a file ("download the zones as a JSON file" on government.nl,
Where can I fly a drone?). An administrator uploads it on Locations (Manage ▾ → Import drone zones); every import is
kept (uas_zones), the newest is used unless it was removed. Everyone signed in sees the zones as a layer on the
Locations map and the Plan flight map, and Plan flight lists the zones its flight area with the ground risk buffer
overlaps (a warning, never a block; see operations.py). The server never downloads the file itself.

A zone as stored: {id, name, restriction, reasons, message, lower_m, upper_m, lower_ref, upper_ref, shapes}, with
each shape like a location's: {"type": "polygon", "points": [[lat, lon], ...]} or
{"type": "circle", "center": [lat, lon], "radius_m": r}.
"""
import datetime
import json
import math
from pathlib import Path

from flask import abort, g, jsonify, request

import db
import ground_risk

MAX_BYTES = 20 * 2**20
MAX_ZONES = 20000
MAX_POINTS = 20000
FEET = 0.3048
# most restrictive first (the order of the Plan flight warning)
RESTRICTIONS = ("PROHIBITED", "REQ_AUTHORISATION", "CONDITIONAL", "NO_RESTRICTION")
_cache = {}


class ZoneError(ValueError):
    pass


def _text(value, limit):
    return str(value).strip()[:limit] if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def _number(value, what):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ZoneError(f"{what} is not a number.")
    return float(value)


def _point(p, what):
    if not isinstance(p, list) or len(p) < 2:
        raise ZoneError(f"{what}: a point is [longitude, latitude].")
    lon, lat = _number(p[0], what), _number(p[1], what)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ZoneError(f"{what}: a point is out of range.")
    return [round(lat, 6), round(lon, 6)]


def _shape(projection, scale, what):
    if not isinstance(projection, dict):
        raise ZoneError(f"{what} has no horizontalProjection.")
    kind = projection.get("type")
    if kind == "Circle":
        radius = _number(projection.get("radius"), what) * scale
        if not 0 < radius <= 1_000_000:
            raise ZoneError(f"{what}: the radius is out of range.")
        return {"type": "circle", "center": _point(projection.get("center"), what), "radius_m": round(radius, 1)}
    if kind == "Polygon":
        rings = projection.get("coordinates")
        if not isinstance(rings, list) or not rings or not isinstance(rings[0], list):
            raise ZoneError(f"{what}: a polygon has a list of rings.")
        points = [_point(p, what) for p in rings[0][:MAX_POINTS]]
        if len(points) > 1 and points[0] == points[-1]:
            points.pop()
        if len(points) < 3:
            raise ZoneError(f"{what}: a polygon has at least 3 points.")
        return {"type": "polygon", "points": points}
    raise ZoneError(f"{what}: unknown shape {kind!r} (Circle or Polygon).")


def parse(data):
    """(title, zones) from the bytes of an ED-269 JSON file; ZoneError when it is not one."""
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise ZoneError("The file is not JSON.") from None
    features = doc.get("features") if isinstance(doc, dict) else None
    if not isinstance(features, list) or not features:
        raise ZoneError("This is not a UAS zone file (ED-269 JSON with a list of features).")
    if len(features) > MAX_ZONES:
        raise ZoneError(f"The file has more than {MAX_ZONES} zones.")
    zones = []
    for n, f in enumerate(features, 1):
        what = f"Zone {n}"
        if not isinstance(f, dict) or not isinstance(f.get("geometry"), list) or not f["geometry"]:
            raise ZoneError(f"{what} has no geometry.")
        what = f"Zone {_text(f.get('identifier'), 40) or n}"
        shapes, lower, upper, refs = [], [], [], None
        for volume in f["geometry"]:
            if not isinstance(volume, dict):
                raise ZoneError(f"{what} has a bad geometry.")
            unit = str(volume.get("uomDimensions") or "M").upper()
            if unit not in ("M", "FT"):
                raise ZoneError(f"{what}: unknown unit {unit!r} (M or FT).")
            scale = FEET if unit == "FT" else 1.0
            shapes.append(_shape(volume.get("horizontalProjection"), scale, what))
            for key, into in (("lowerLimit", lower), ("upperLimit", upper)):
                if volume.get(key) is not None:
                    into.append(_number(volume[key], what) * scale)
            refs = refs or (_text(volume.get("lowerVerticalReference"), 10), _text(volume.get("upperVerticalReference"), 10))
        reasons = f.get("reason") if isinstance(f.get("reason"), list) else []
        zones.append(dict(id=_text(f.get("identifier"), 40), name=_text(f.get("name"), 300) or what,
                          restriction=_text(f.get("restriction"), 40).upper(),
                          reasons=[_text(r, 40) for r in reasons if _text(r, 40)][:10],
                          message=_text(f.get("message"), 1000),
                          lower_m=round(min(lower)) if lower else None, upper_m=round(max(upper)) if upper else None,
                          lower_ref=refs[0] if refs else "", upper_ref=refs[1] if refs else "", shapes=shapes))
    title = _text(doc.get("title"), 200) or "UAS zones"
    return title, zones


def current_row(con):
    """The newest import, or None when there is none or it was removed (older imports never come back)."""
    row = con.execute("SELECT z.*, COALESCE(u.display_name,u.username) AS uploader FROM uas_zones z"
                      " LEFT JOIN users u ON u.id=z.uploaded_by ORDER BY z.id DESC LIMIT 1").fetchone()
    return None if row is None or row["removed_at"] else row


def current(con):
    """(row, zones) of the zones in use, or (None, [])."""
    row = current_row(con)
    if row is None:
        return None, []
    key = (row["id"], row["uploaded_at"], row["zone_count"], row["title"])
    if key not in _cache:
        _cache.clear()
        _cache[key] = json.loads(row["zones"])
    return row, _cache[key]


def info(row):
    return None if row is None else dict(id=row["id"], title=row["title"], name=row["name"], count=row["zone_count"],
                                         uploaded_at=row["uploaded_at"], uploaded_by=row["uploader"])


def _distance(area_xy, shape, project):
    """Metres between a flight area (projected polygon) and a zone shape; 0 when they touch or overlap."""
    if shape["type"] == "circle":
        c = project(*shape["center"])
        if ground_risk._inside(c, area_xy):
            return 0.0
        edge = min(ground_risk._point_segment(c, a, b) for a, b in zip(area_xy, area_xy[1:] + area_xy[:1]))
        return max(0.0, edge - shape["radius_m"])
    zone = [project(lat, lon) for lat, lon in shape["points"]]
    if any(ground_risk._inside(p, zone) for p in area_xy) or any(ground_risk._inside(p, area_xy) for p in zone):
        return 0.0
    edges_a = list(zip(area_xy, area_xy[1:] + area_xy[:1]))
    edges_z = list(zip(zone, zone[1:] + zone[:1]))
    if any(ground_risk._cross(a, b, c, d) for a, b in edges_a for c, d in edges_z):
        return 0.0
    return min(min(ground_risk._point_segment(p, c, d) for p in area_xy for c, d in edges_z),
               min(ground_risk._point_segment(p, a, b) for p in zone for a, b in edges_a))


def _near(shape, box, reach_deg):
    """Quick bounding-box test before the exact distance."""
    (lat0, lon0), (lat1, lon1) = box
    if shape["type"] == "circle":
        lat, lon = shape["center"]
        r = shape["radius_m"] / 111_000 + reach_deg
        return lat0 - r <= lat <= lat1 + r and lon0 - r * 2 <= lon <= lon1 + r * 2
    lats = [p[0] for p in shape["points"]]
    lons = [p[1] for p in shape["points"]]
    return (min(lats) <= lat1 + reach_deg and max(lats) >= lat0 - reach_deg
            and min(lons) <= lon1 + reach_deg * 2 and max(lons) >= lon0 - reach_deg * 2)


def overlaps(zones, area, buffer_m=None, height_m=None):
    """The zones a flight area ([[lat, lon], ...]) with its ground risk buffer reaches, most restrictive first:
    [{id, name, restriction, message, lower_m, upper_m, in_area}] (in_area False: only the buffer reaches it).
    A zone that starts above the planned maximum height (above ground) is left out."""
    if not zones or not area:
        return []
    buffer_m = float(buffer_m or 0)
    lat0 = sum(p[0] for p in area) / len(area)
    lon0 = sum(p[1] for p in area) / len(area)
    project = ground_risk._projector(lat0, lon0)
    area_xy = [project(lat, lon) for lat, lon in area]
    box = ((min(p[0] for p in area), min(p[1] for p in area)), (max(p[0] for p in area), max(p[1] for p in area)))
    reach = buffer_m / 111_000 + 0.001
    hits = []
    for z in zones:
        if height_m and z["lower_m"] and z["lower_ref"] in ("AGL", "") and z["lower_m"] >= height_m:
            continue
        near = [s for s in z["shapes"] if _near(s, box, reach)]
        if not near:
            continue
        gap = min(_distance(area_xy, s, project) for s in near)
        if gap <= buffer_m:
            hits.append(dict(id=z["id"], name=z["name"], restriction=z["restriction"], message=z["message"],
                             lower_m=z["lower_m"], upper_m=z["upper_m"], in_area=gap == 0))
    rank = {r: i for i, r in enumerate(RESTRICTIONS)}
    return sorted(hits, key=lambda h: (rank.get(h["restriction"], len(rank)), not h["in_area"], h["name"]))


def check(con, area, buffer_m=None, height_m=None):
    """{title, zones: overlaps} for Plan flight, or None when no zones are imported."""
    row, zones = current(con)
    return None if row is None else dict(title=row["title"], zones=overlaps(zones, area, buffer_m, height_m))


def register(app, connection, admin_required):
    @app.errorhandler(ZoneError)
    def zone_error(exc):
        return jsonify(error=str(exc)), 400

    @app.get("/api/uas-zones")
    def list_uas_zones():
        """The drone zones in use ({info, zones}; info None and no zones when none are imported)."""
        row, zones = current(connection())
        response = jsonify(info=info(row), zones=zones)
        response.headers["Cache-Control"] = "private, no-cache"
        return response

    @app.post("/api/admin/uas-zones")
    def admin_import_uas_zones():
        """Import an ED-269 JSON file of drone zones; it replaces the zones in use (older imports are kept)."""
        admin_required()
        uploaded = request.files.get("file")
        if not uploaded or not uploaded.filename:
            raise ZoneError("Choose the zone file (JSON).")
        data = uploaded.stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ZoneError("The file is larger than 20 MiB.")
        title, zones = parse(data)
        con = connection()
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("INSERT INTO uas_zones(title,name,zones,zone_count,uploaded_at,uploaded_by) VALUES (?,?,?,?,?,?)",
                        (title, Path(uploaded.filename).name[:160], json.dumps(zones, separators=(",", ":")),
                         len(zones), now, g.user["id"]))
        return jsonify(info=info(current_row(con))), 201

    @app.delete("/api/admin/uas-zones")
    def admin_remove_uas_zones():
        """Stop using the drone zones (the import stays in the history; a new import uses zones again)."""
        admin_required()
        con = connection()
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            row = current_row(con)
            if row is None:
                abort(404)
            con.execute("UPDATE uas_zones SET removed_at=? WHERE id=?",
                        (datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), row["id"]))
        return jsonify(ok=True)
