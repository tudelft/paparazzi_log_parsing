"""Ground risk buffer of a planned outdoor flight (operations.py), by the 1:1 rule of the operations manual.

The flight area (flight geography) is a polygon of [lat, lon] points. Its ground risk buffer is as wide as the
planned maximum height (BUFFER_METHOD '1:1'; contingency volume and the SORA 2.5 methods come later). The check
says whether the flight area with its buffer stays inside the location's boundary (a polygon or a circle). It
works in metres on a local flat projection around the location, which is exact enough for a field of a few km.
"""
import math

BUFFER_METHOD = "1:1"
MAX_POINTS = 100
EARTH_M = 6371008.8


def parse_area(raw):
    """A flight area from the API: [[lat, lon], ...] with 3 to MAX_POINTS points (None stays None); ValueError."""
    if raw is None:
        return None
    if not isinstance(raw, list) or not 3 <= len(raw) <= MAX_POINTS:
        raise ValueError(f"A flight area is a polygon of 3 to {MAX_POINTS} points.")
    points = []
    for p in raw:
        if (not isinstance(p, list) or len(p) != 2
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in p)
                or not -90 <= p[0] <= 90 or not -180 <= p[1] <= 180):
            raise ValueError("Each point of a flight area is [latitude, longitude].")
        points.append([round(float(p[0]), 7), round(float(p[1]), 7)])
    return points


def _projector(lat0, lon0):
    scale = math.cos(math.radians(lat0))
    return lambda lat, lon: (math.radians(lon - lon0) * EARTH_M * scale, math.radians(lat - lat0) * EARTH_M)


def _inside(pt, poly):
    x, y = pt
    hit = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def _point_segment(p, a, b):
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def _cross(a, b, c, d):
    def side(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0


def margin(area, shape):
    """How far (m) the flight area stays from the location's boundary: negative when it reaches outside. The buffer
    stays inside when margin >= buffer width."""
    if shape.get("type") == "circle":
        (lat0, lon0), radius = shape["center"], float(shape["radius_m"])
        to_xy = _projector(lat0, lon0)
        # the point of a polygon farthest from a centre is one of its corners
        return radius - max(math.hypot(*to_xy(*p)) for p in area)
    boundary = shape["points"]
    lat0 = sum(p[0] for p in boundary) / len(boundary)
    lon0 = sum(p[1] for p in boundary) / len(boundary)
    to_xy = _projector(lat0, lon0)
    poly, inner = [to_xy(*p) for p in boundary], [to_xy(*p) for p in area]
    edges = list(zip(poly, poly[1:] + poly[:1]))
    inner_edges = list(zip(inner, inner[1:] + inner[:1]))
    if not all(_inside(p, poly) for p in inner) or any(_cross(a, b, c, d) for a, b in inner_edges for c, d in edges):
        return -1.0
    nearest = min(min(_point_segment(p, a, b) for a, b in edges) for p in inner)
    nearest = min(nearest, min(min(_point_segment(p, a, b) for a, b in inner_edges) for p in poly))
    return nearest


def assess(area, height_m, shape):
    """The ground risk buffer of a flight area: {buffer_m, method, inside, margin_m} (inside None without an area or
    height)."""
    buffer_m = float(height_m) if height_m else None
    if not area or buffer_m is None:
        return {"buffer_m": buffer_m, "method": BUFFER_METHOD, "inside": None, "margin_m": None}
    room = margin(area, shape)
    return {"buffer_m": buffer_m, "method": BUFFER_METHOD, "inside": room >= buffer_m, "margin_m": round(room, 1)}
