"""Home page (/home): a personal dashboard. The helpers here build its parts from rows the caller already scoped to
the user (the logbook read model, visible attachments), so they add no access rules of their own."""
import datetime
import json
from collections import Counter

import bookings
from storage import VIEWABLE, view_type

RECENT_FLIGHTS = 5
MEDIA_ITEMS = 8
MEDIA_SCAN = 400          # newest attachments looked at to find MEDIA_ITEMS the user may see
PLACE_DAYS = 365          # the default weather place: most flights in this many days
QUAL_SOON_DAYS = 30       # qualifications expiring within this many days need attention
QUAL_EXPIRED_DAYS = 60    # ... and those that expired at most this long ago (older ones were left on purpose)
BOOKING_DAYS = 14
BOOKINGS_SHOWN = 5


def real_flight(r):
    """A flight that counts: not a detected simulation, not judged a ground test or uncertain, and without a tag that
    is hidden by default (tags.py)."""
    return (not (r.get("simulation") or {}).get("detected") and r.get("verdict") not in ("ground", "uncertain")
            and not r.get("tags_hidden"))


def row_day(r):
    """YYYY-MM-DD of a logbook row, '' when unknown (as the browser's flight filters read it)."""
    return ((r.get("takeoff_utc") or r.get("date_override") or "") if r.get("date_ok")
            else (r.get("date_override") or ""))[:10]


def months_back(today, n=12):
    out, year, month = [], today.year, today.month
    for _ in range(n):
        out.append(f"{year:04d}-{month:02d}")
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return out[::-1]


def flying(rows, user_id, today):
    """The user's own flights as PIC (telemetry and paper records): last flight day, flights in 90 days, flights
    and time this year and flights per month for the last 12 months. With user_id None: every flight in `rows` (the
    ones the user may see, for All flying), with the number of pilots (PIC) who flew in the last 90 days."""
    mine = [r for r in rows if (user_id is None or (r.get("pic_user") or {}).get("id") == user_id)
            and real_flight(r) and row_day(r)]
    pilots = set()
    since90 = (today - datetime.timedelta(days=89)).isoformat()
    year = f"{today.year:04d}"
    months = months_back(today)
    per_month = Counter()
    last, n90, year_flights, year_seconds = None, 0, 0, 0.0
    for r in mine:
        day, count = row_day(r), int(r.get("flight_count") or 1)
        if day > today.isoformat():
            continue
        last = max(last or day, day)
        if day >= since90:
            n90 += count
            pic = (r.get("pic_user") or {}).get("id") or r.get("pic")
            if pic:
                pilots.add(pic)
        if day.startswith(year):
            year_flights += count
            year_seconds += float(r.get("duration_s") or 0)
        if day[:7] >= months[0]:
            per_month[day[:7]] += count
    out = dict(last_day=last,
               days_since=(today - datetime.date.fromisoformat(last)).days if last else None,
               flights_90d=n90, flights_year=year_flights, seconds_year=round(year_seconds),
               months=[dict(month=m, flights=per_month[m]) for m in months])
    if user_id is None:
        out["pilots_90d"] = len(pilots)
    return out


def person(u, name):
    return (u or {}).get("display_name") or name or None


def recent(rows, n=RECENT_FLIGHTS):
    out = []
    for r in rows:
        if not real_flight(r) or not row_day(r):  # undated rows are not "recent"
            continue
        paper = r.get("source_type") == "paper"
        out.append(dict(
            url=f"/historical/{r['manual_id']}" if paper else f"/flight/{r['flight_id']}",
            takeoff_utc=r.get("takeoff_utc") if r.get("date_ok") else None, day=row_day(r) or None, paper=paper,
            aircraft=r.get("aircraft"), goal=r.get("goal"), pic=person(r.get("pic_user"), r.get("pic")),
            duration_s=r.get("duration_s"), flight_count=r.get("flight_count") or 1,
            place=r.get("takeoff_place"), project=r.get("project_name")))
        if len(out) == n:
            break
    return out


AIRCRAFT_SHOWN = 5


def my_aircraft(con, rows, user_id, n=AIRCRAFT_SHOWN):
    """The Home card Your aircraft: the aircraft the user is the responsible person of, then the ones they flew as PIC
    most recently (from the logbook rows they can see), each with its status and the day they last flew it."""
    last = {}
    for r in rows:
        if r.get("drone_id") and (r.get("pic_user") or {}).get("id") == user_id and real_flight(r) and row_day(r):
            last[r["drone_id"]] = max(last.get(r["drone_id"], ""), row_day(r))
    responsible = [dict(r) for r in con.execute(
        "SELECT id, name, status FROM drones WHERE manager_id=? AND COALESCE(status,'')<>'Discard' ORDER BY name COLLATE NOCASE",
        (user_id,))]
    out = [dict(id=d["id"], name=d["name"], status=d["status"], responsible=True, last_flown=last.get(d["id"]))
           for d in responsible]
    seen = {d["id"] for d in out}
    flown = sorted((d for d in last if d not in seen), key=lambda d: last[d], reverse=True)
    for d in con.execute(f"SELECT id, name, status FROM drones WHERE id IN ({','.join('?' * len(flown))})", flown) if flown else []:
        out.append(dict(id=d["id"], name=d["name"], status=d["status"], responsible=False, last_flown=last[d["id"]]))
    out = out[:len(responsible)] + sorted(out[len(responsible):], key=lambda d: d["last_flown"], reverse=True)
    return out[:max(n, len(responsible))]


def place_centre(shape):
    """(lat, lon) in the middle of a place: a circle's centre, else the middle of the polygon's bounds."""
    sh = json.loads(shape) if isinstance(shape, str) else shape
    if sh.get("type") == "circle":
        return float(sh["center"][0]), float(sh["center"][1])
    lats, lons = [p[0] for p in sh["points"]], [p[1] for p in sh["points"]]
    return (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2


def places(con, rows, user, today):
    """Outdoor places the user can pick for the weather: those on flights they can see (admins: all), with the
    number of flights there in the last PLACE_DAYS days; the most flown first."""
    since = (today - datetime.timedelta(days=PLACE_DAYS)).isoformat()
    counts, seen = Counter(), set()
    for r in rows:
        pid = r.get("takeoff_place_id")
        if pid is None:
            continue
        seen.add(pid)
        day = row_day(r)
        if day and day >= since and real_flight(r):
            counts[pid] += int(r.get("flight_count") or 1)
    out = []
    for p in con.execute("SELECT id, name, indoor FROM places ORDER BY name COLLATE NOCASE"):
        if p["indoor"] or not (user["is_admin"] or p["id"] in seen):
            continue
        out.append(dict(id=p["id"], name=p["name"], flights=counts[p["id"]]))
    out.sort(key=lambda p: -p["flights"])  # stable: by name within equal counts
    return out


def default_place(choices, stored):
    """The stored place when it can still be chosen, else the most flown one (None without any)."""
    if stored in {p["id"] for p in choices}:
        return stored
    return choices[0]["id"] if choices else None


def qualification_alerts(con, user_id, today):
    """The user's qualification types whose newest approved validity ends within QUAL_SOON_DAYS days, or ended at
    most QUAL_EXPIRED_DAYS ago, without a later approved one. Internal (training) types are left to the trainings."""
    best = {}
    for r in con.execute(
            "SELECT q.type_id, t.name, q.valid_until FROM qualification_submissions q"
            " JOIN qualification_types t ON t.id=q.type_id WHERE q.user_id=? AND q.status='approved'"
            " AND t.internal=0 AND t.active=1", (user_id,)):
        current = best.get(r["type_id"])
        if current is None or current["until"] is not None and (r["valid_until"] is None
                                                               or r["valid_until"] > current["until"]):
            best[r["type_id"]] = dict(name=r["name"], until=r["valid_until"])
    soon = (today + datetime.timedelta(days=QUAL_SOON_DAYS)).isoformat()
    oldest = (today - datetime.timedelta(days=QUAL_EXPIRED_DAYS)).isoformat()
    out = []
    for q in best.values():
        if q["until"] and oldest <= q["until"] <= soon:
            days = (datetime.date.fromisoformat(q["until"][:10]) - today).days
            out.append(dict(name=q["name"], valid_until=q["until"], days=days))
    return sorted(out, key=lambda q: q["valid_until"])


def upcoming_bookings(con, user, now_local, everyone=False):
    """Bookings that have not ended, in the next BOOKING_DAYS days, on things the user can view: their own, or
    (everyone) everybody's, as the agenda shows them (with the person who booked)."""
    end = (now_local + datetime.timedelta(days=BOOKING_DAYS)).strftime("%Y-%m-%dT%H:%M")
    items = {}
    out = []
    for b in con.execute(
            "SELECT b.*, COALESCE(u.display_name, u.username) AS person FROM bookings b JOIN users u ON u.id=b.user_id"
            " WHERE (? OR b.user_id=?) AND b.ends_at>? AND b.starts_at<? AND b.status!='rejected'"
            " ORDER BY b.starts_at, b.id", (bool(everyone), user["id"], now_local.strftime("%Y-%m-%dT%H:%M"), end)):
        item = thing(con, items, b["thing_id"], user)
        if item is None:
            continue
        flags = bookings.booking_flags(con, b["id"])
        out.append(dict(id=b["id"], thing_id=b["thing_id"], thing=item["name"], place=item["place_name"], description=b["description"],
                        start=b["starts_at"], end=b["ends_at"], pending=b["status"] == "pending",
                        person=b["person"], mine=b["user_id"] == user["id"],
                        flags=[f["label"] for f in item["flags"] if f["id"] in flags]))
        if len(out) == BOOKINGS_SHOWN * (2 if everyone else 1):
            break
    return out


def bookings_today(con, user, now_local):
    """Everyone's bookings today on things the user can view (what is planned today), without descriptions."""
    day = now_local.strftime("%Y-%m-%d")
    nxt = (now_local + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    items, out = {}, []
    for b in con.execute(
            "SELECT b.*, COALESCE(u.display_name, u.username) AS person FROM bookings b JOIN users u ON u.id=b.user_id"
            " WHERE b.starts_at<? AND b.ends_at>? AND b.status!='rejected' ORDER BY b.starts_at, b.id", (nxt, day)):
        item = thing(con, items, b["thing_id"], user)
        if item is not None:
            out.append(dict(thing=item["name"], place=item["place_name"], person=b["person"],
                            start=b["starts_at"], end=b["ends_at"]))
    return out[:10]


def bookings_to_approve(con, user):
    """Pending bookings this person decides (administrators: all; approvers: their items): the count and where the
    first one is, for the Needs your attention card."""
    rows = con.execute(
        "SELECT b.thing_id, b.starts_at FROM bookings b WHERE b.status='pending'"
        + ("" if user["is_admin"] else " AND b.thing_id IN (SELECT thing_id FROM booking_approvers WHERE user_id=?)")
        + " ORDER BY b.starts_at", () if user["is_admin"] else (user["id"],)).fetchall()
    return dict(count=len(rows), thing_id=rows[0]["thing_id"] if rows else None,
                week=rows[0]["starts_at"][:10] if rows else None)


def thing(con, cache, thing_id, user):
    if thing_id not in cache:
        row = con.execute(bookings.ITEM_SQL + " WHERE t.id=?", (thing_id,)).fetchone()
        item = bookings.item_json(con, row, user) if row is not None else None
        cache[thing_id] = item if item and item["can_view"] and (item["active"] or user["is_admin"]) else None
    return cache[thing_id]


MEDIA_EXTENSIONS = sorted(ext for ext, mime in VIEWABLE.items() if mime.split("/")[0] in ("image", "video"))
# When the media was taken: the date of the flight, log or paper record it is attached to (the latest when on several;
# a log's typed-in date before its file clock), else the upload time. Earlier versions of a replaced file are skipped.
MEDIA_SQL = (
    "SELECT a.id, a.filename, MAX(CASE"
    " WHEN k.flight_id IS NOT NULL THEN COALESCE(lf.date_override, f.takeoff_utc, lf.start_utc)"
    " WHEN k.log_id IS NOT NULL THEN COALESCE(l.date_override, l.start_utc)"
    " ELSE r.date || COALESCE('T' || r.takeoff_time, '') END) AS flown"
    " FROM attachments a JOIN attachment_links k ON k.attachment_id=a.id AND k.superseded_by IS NULL"
    " LEFT JOIN flights f ON f.id=k.flight_id LEFT JOIN aircraft ac ON ac.id=f.aircraft_id"
    " LEFT JOIN logs lf ON lf.id=ac.log_id LEFT JOIN logs l ON l.id=k.log_id"
    " LEFT JOIN flight_records r ON r.id=k.record_id"
    " WHERE (" + " OR ".join(f"lower(a.filename) LIKE '%{ext}'" for ext in MEDIA_EXTENSIONS) + ")"
    " GROUP BY a.id ORDER BY COALESCE(flown, a.uploaded_at) DESC, a.uploaded_at DESC, a.id DESC LIMIT ?")


def media(con, user, visible, n=MEDIA_ITEMS):
    """The image and video attachments the user may see, newest flight first (visible(con, user, id) -> (row, links), raising
    for files they may not), each with where it is linked."""
    out = []
    for r in con.execute(MEDIA_SQL, (MEDIA_SCAN,)):
        kind = (view_type(r["filename"]) or "").split("/")[0]
        if kind not in ("image", "video"):
            continue
        try:
            row, links = visible(con, user, r["id"])
        except Exception as exc:  # noqa: BLE001 - only "not visible" is expected; anything else is re-raised
            if getattr(exc, "status", None) == 404:
                continue
            raise
        link = next((x for kind_ in ("flight", "log", "paper") for x in links if x["kind"] == kind_), links[0])
        out.append(dict(id=row["id"], filename=row["filename"], description=row["description"], kind=kind,
                        uploaded_at=row["uploaded_at"], flown=r["flown"], url=link["url"], label=link["label"]))
        if len(out) == n:
            break
    return out
