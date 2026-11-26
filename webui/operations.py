"""Outdoor operations: plan and book outdoor flights in the Agenda. Off unless LOGDB_OPERATIONS=1 (with LOGDB_BOOKINGS).

The fixed agenda item Outdoor operations (booking_things.kind='operations') is created when the switch is on and
cannot be deleted. A booking of it is a *slot* at a bookable location (places.bookable); several teams share one slot
(operation_teams: PIC, project, optional crew and aircraft, goal). Joining adds a team for the whole slot; when the
last team leaves, the slot is removed. Slots may overlap. Who may book or join follows the item's qualification
rules (booking_thing_rules): holders of a current approved qualification of a listed type, each type booking
directly or needing approval; without rules anyone may book. Administrators and the item's approvers decide waiting
teams (in the agenda and the People approval queue) and book without approval.

Plan flight shows the flight area with its ground risk buffer (ground_risk.py, 1:1: as wide as the planned maximum
height; the location's default area and maximum height unless the team draws or sets its own; a warning when it does
not stay inside the location), the weather forecast for the slot, red/green checks of the crew's qualifications and
the aircraft on that date, and a NOTAM check (a link from LOGDB_NOTAM_URL, a tick and a reference); these are stored
with the team
as they were when it booked (operation_teams.preparation). The slot itself is a bookings row: its status stays
'approved' while a team has not been rejected (team approval is separate), so the ordinary booking queue never lists
it; the generic booking endpoints refuse the operations item.
"""
import datetime
import json

from flask import abort, g, jsonify, request

import bookings
import db
import ground_risk
import home
import mail
import weather
from bookings import BookingError, person_name, utc_now, when
from config import env, env_bool

ITEM_NAME = "Outdoor operations"
MAX_GOAL = 500
MAX_REFERENCE = 200
MAX_CREW = 10


def enabled():
    return env_bool("OPERATIONS") and bookings.enabled()


def ensure_item(con):
    """The fixed Outdoor operations item, created the first time it is needed while the switch is on (an item
    already called that becomes it). Returns its id. Outside a transaction it takes the write lock itself."""
    row = con.execute("SELECT id FROM booking_things WHERE kind='operations'").fetchone()
    if row:
        return row["id"]
    if con.in_transaction:
        return _create_item(con)
    with db.write_lock, con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT id FROM booking_things WHERE kind='operations'").fetchone()
        return row["id"] if row else _create_item(con)


def _create_item(con):
    named = con.execute("SELECT id FROM booking_things WHERE name=?", (ITEM_NAME,)).fetchone()
    if named:
        con.execute("UPDATE booking_things SET kind='operations', type_id=NULL, needs_approval=0 WHERE id=?",
                    (named["id"],))
        return named["id"]
    order = con.execute("SELECT COALESCE(MIN(sort_order), 0) - 1 FROM booking_things").fetchone()[0]
    return con.execute("INSERT INTO booking_things(name, description, colour, sort_order, kind) VALUES (?,?,?,?,?)",
                       (ITEM_NAME, "Plan and book outdoor flights", "#2da44e", order, "operations")).lastrowid


def item(con):
    return con.execute(bookings.ITEM_SQL + " WHERE t.kind='operations'").fetchone()


def is_operations(row):
    return row is not None and "kind" in row.keys() and row["kind"] == "operations"


def rules(con, thing_id):
    return [dict(r) for r in con.execute(
        "SELECT r.type_id, r.needs_approval, t.name FROM booking_thing_rules r"
        " JOIN qualification_types t ON t.id=r.type_id WHERE r.thing_id=? ORDER BY t.sort_order, t.name", (thing_id,))]


def rule_for(con, thing_id, user_id):
    """'direct', 'approval' or None (may not book): from the person's current qualifications and the item's rules.
    Without rules everyone books directly; one type that books directly is enough."""
    listed = rules(con, thing_id)
    if not listed:
        return "direct"
    held = [r for r in listed if bookings.has_qualification(con, user_id, r["type_id"])]
    if not held:
        return None
    return "direct" if any(not r["needs_approval"] for r in held) else "approval"


def save_rules(con, thing_id, raw):
    """[{type_id, needs_approval}] for the operations item."""
    if not isinstance(raw, list) or len(raw) > 20:
        raise BookingError("rules must be a list of at most 20 qualification types.")
    parsed = {}
    for r in raw:
        if not isinstance(r, dict) or type(r.get("type_id")) is not int or type(r.get("needs_approval", False)) is not bool:
            raise BookingError("Each rule needs a qualification type and needs_approval true or false.")
        if not con.execute("SELECT 1 FROM qualification_types WHERE id=?", (r["type_id"],)).fetchone():
            raise BookingError("No such qualification.")
        parsed[r["type_id"]] = int(r.get("needs_approval", False))
    con.execute("DELETE FROM booking_thing_rules WHERE thing_id=?", (thing_id,))
    con.executemany("INSERT INTO booking_thing_rules(thing_id, type_id, needs_approval) VALUES (?,?,?)",
                    [(thing_id, t, n) for t, n in parsed.items()])


def qualification_on(con, user_id, type_id, day):
    """The person's approved qualification of a type that is valid on `day` (the one valid longest), else the one
    that ended last before it (to say it expired), as (row, valid)."""
    rows = con.execute("SELECT number, valid_from, valid_until FROM qualification_submissions WHERE user_id=?"
                       " AND type_id=? AND status='approved'", (user_id, type_id)).fetchall()
    valid = [r for r in rows if (r["valid_from"] or "") <= day and (r["valid_until"] is None or r["valid_until"] >= day)]
    if valid:
        return max(valid, key=lambda r: r["valid_until"] or "9999"), True
    ended = [r for r in rows if r["valid_until"] and r["valid_until"] < day]
    return (max(ended, key=lambda r: r["valid_until"]), False) if ended else (None, False)


def checks(con, thing_id, day, pic_id, crew_ids, drone_id):
    """Red/green rows for a planned flight on `day`: the PIC and crew's qualifications of the rule types, and the
    aircraft's fleet status. [{who, what, ok, detail}]"""
    types = rules(con, thing_id)
    out = []
    for uid in [pic_id, *crew_ids]:
        name = person_name(con, uid) + (" (PIC)" if uid == pic_id else "")
        found = False
        for t in types:
            row, valid = qualification_on(con, uid, t["type_id"], day)
            if row is None:
                continue
            found = found or valid
            until = f"until {row['valid_until']}" if row["valid_until"] else "no end date"
            number = f"No. {row['number']} · " if row["number"] else ""
            out.append(dict(who=name, what=t["name"], ok=valid,
                            detail=number + until if valid else f"ended {row['valid_until']}, before {day}"))
        if uid == pic_id and types and not found:
            out.append(dict(who=name, what=" or ".join(t["name"] for t in types), ok=False,
                            detail=f"none valid on {day}"))
    if drone_id:
        d = con.execute("SELECT name, status FROM drones WHERE id=?", (drone_id,)).fetchone()
        if d:
            status = d["status"] or ""
            out.append(dict(who=d["name"], what="fleet status", ok=status in ("", "Ready"),
                            detail=status or "not set"))
    return out


def forecast(con, place_id, start, end):
    row = con.execute("SELECT shape FROM places WHERE id=?", (place_id,)).fetchone()
    try:
        lat, lon = home.place_centre(row["shape"])
        return weather.forecast_window(lat, lon, start, end)
    except weather.WeatherError as exc:
        return {"hours": [], "note": str(exc)}
    except (KeyError, IndexError, TypeError, ValueError):
        return {"hours": [], "note": "The location has no usable position."}


def place_geometry(con, place_id):
    """{id, name, shape, max_height_m, flight_area} of a location, for the map and the buffer."""
    r = con.execute("SELECT id, name, shape, max_height_m, flight_area FROM places WHERE id=?", (place_id,)).fetchone()
    return dict(id=r["id"], name=r["name"], shape=json.loads(r["shape"]), max_height_m=r["max_height_m"],
                flight_area=json.loads(r["flight_area"]) if r["flight_area"] else None)


def area_plan(place, height_m=None, flight_area=None):
    """The flight area and ground risk buffer of a team at a location: its own area and height, else the location's.
    {area, own_area, height_m, buffer_m, method, inside, margin_m}"""
    area = flight_area or place["flight_area"]
    height = height_m or place["max_height_m"]
    return dict(area=area, own_area=bool(flight_area), height_m=height,
                **ground_risk.assess(area, height, place["shape"]))


def parse_area_fields(body, place):
    """(height_m or None, own flight area or None) from a Plan flight body; the height at most the location's."""
    height = body.get("height_m")
    if height is not None and (type(height) is not int or not 1 <= height <= 1000):
        raise BookingError("The maximum height is a whole number of metres (1 to 1000).")
    if height and place["max_height_m"] and height > place["max_height_m"]:
        raise BookingError(f"At {place['name']} the maximum height is {place['max_height_m']} m.")
    try:
        area = ground_risk.parse_area(body.get("flight_area"))
    except ValueError as exc:
        raise BookingError(str(exc)) from exc
    return height, area


def team_rows(con, booking_id):
    return con.execute(
        "SELECT o.*, COALESCE(u.display_name,u.username) AS pic, p.name AS project, d.name AS drone,"
        " COALESCE(c.display_name,c.username) AS creator FROM operation_teams o JOIN users u ON u.id=o.pic_id"
        " LEFT JOIN projects p ON p.id=o.project_id LEFT JOIN drones d ON d.id=o.drone_id"
        " LEFT JOIN users c ON c.id=o.created_by WHERE o.booking_id=? ORDER BY o.id", (booking_id,)).fetchall()


def crew_of(con, team_id):
    return [dict(id=r[0], name=r[1]) for r in con.execute(
        "SELECT u.id, COALESCE(u.display_name,u.username) FROM operation_crew c JOIN users u ON u.id=c.user_id"
        " WHERE c.team_id=? ORDER BY 2", (team_id,))]


def summaries(con, booking_ids):
    """{booking id: {place_id, place, teams, pending, pics}} for the agenda's slots (rejected teams not counted)."""
    out = {}
    if not booking_ids:
        return out
    marks = ",".join("?" * len(booking_ids))
    for r in con.execute(f"SELECT s.booking_id, s.place_id, p.name FROM operation_slots s JOIN places p ON p.id=s.place_id"
                         f" WHERE s.booking_id IN ({marks})", booking_ids):
        out[r[0]] = dict(place_id=r[1], place=r[2], teams=0, pending=0, pics=[])
    for r in con.execute(f"SELECT o.booking_id, o.status, COALESCE(u.display_name,u.username) FROM operation_teams o"
                         f" JOIN users u ON u.id=o.pic_id WHERE o.booking_id IN ({marks}) AND o.status!='rejected'"
                         f" ORDER BY o.id", booking_ids):
        s = out.get(r[0])
        if s:
            s["teams"] += 1
            s["pending"] += r[1] == "pending"
            s["pics"].append(r[2])
    return out


def sync_slot(con, booking_id):
    """After a team changed: the slot keeps its time while a team is not rejected (status approved), is 'rejected'
    when all are, and is removed without teams. Its description names the place and the teams' PICs."""
    teams = con.execute("SELECT o.status, COALESCE(u.display_name,u.username) FROM operation_teams o"
                        " JOIN users u ON u.id=o.pic_id WHERE o.booking_id=? ORDER BY o.id", (booking_id,)).fetchall()
    if not teams:
        con.execute("DELETE FROM bookings WHERE id=?", (booking_id,))
        return False
    place = con.execute("SELECT p.name FROM operation_slots s JOIN places p ON p.id=s.place_id WHERE s.booking_id=?",
                        (booking_id,)).fetchone()[0]
    live = [t for t in teams if t[0] != "rejected"]
    pics = ", ".join(dict.fromkeys(t[1] for t in (live or teams)))
    con.execute("UPDATE bookings SET status=?, description=? WHERE id=?",
                ("approved" if live else "rejected", f"{place}: {pics}"[:bookings.MAX_DESCRIPTION], booking_id))
    return True


def register(app, connection, require_session):
    def signed_in():
        require_session()
        if not enabled():
            abort(404)

    def operations_item(cid):
        row = item(cid)
        if row is None:
            ensure_item(cid)
            row = item(cid)
        return row

    def may_decide(cid, thing):
        return bookings.can_approve(cid, thing, g.user)

    def projects_for(cid, user):
        sql = "SELECT id, name FROM projects"
        if not user["is_admin"]:
            sql += " WHERE id IN (SELECT project_id FROM project_members WHERE user_id=?)"
        return [dict(r) for r in cid.execute(sql + " ORDER BY name COLLATE NOCASE", () if user["is_admin"] else (user["id"],))]

    def slot_row(cid, booking_id):
        row = cid.execute("SELECT b.*, s.place_id, p.name AS place FROM bookings b JOIN operation_slots s"
                          " ON s.booking_id=b.id JOIN places p ON p.id=s.place_id WHERE b.id=?", (booking_id,)).fetchone()
        if row is None:
            raise BookingError("No such slot.", 404)
        return row

    def parse_time_window(body):
        start = bookings.parse_time(body.get("start"), "Start")
        end = bookings.parse_time(body.get("end"), "End")
        if end <= start:
            raise BookingError("End must be after start.")
        if end - start > datetime.timedelta(days=1):
            raise BookingError("A slot can last at most one day.")
        return start.strftime(bookings.TIME_FORMAT), end.strftime(bookings.TIME_FORMAT)

    def bookable_place(cid, raw):
        if type(raw) is not int:
            raise BookingError("Choose a location.")
        row = cid.execute("SELECT id, name FROM places WHERE id=? AND bookable=1 AND indoor=0", (raw,)).fetchone()
        if row is None:
            raise BookingError("Choose a location that can be booked for outdoor operations.")
        return row

    def active_person(cid, raw, label):
        if type(raw) is not int or not cid.execute(
                "SELECT 1 FROM users WHERE id=? AND is_active=1 AND is_external=0", (raw,)).fetchone():
            raise BookingError(f"Choose an active account as {label}.")
        return raw

    def parse_team(cid, thing, body, start, place):
        """(fields, crew ids, status) of a team from the form; checks who may book or join, and whether it waits for
        approval: the booker and the PIC each need a qualification of the rules (an administrator or approver
        books for anyone, without approval)."""
        pic = active_person(cid, body.get("pic_id", g.user["id"]), "PIC")
        project = body.get("project_id")
        if type(project) is not int or project not in {p["id"] for p in projects_for(cid, g.user)}:
            raise BookingError("Choose one of your projects.")
        drone = body.get("drone_id")
        if drone is not None and (type(drone) is not int
                                  or not cid.execute("SELECT 1 FROM drones WHERE id=?", (drone,)).fetchone()):
            raise BookingError("No such aircraft.")
        crew = body.get("crew", [])
        if not isinstance(crew, list) or len(crew) > MAX_CREW or any(type(c) is not int for c in crew):
            raise BookingError(f"crew must be a list of at most {MAX_CREW} account ids.")
        crew = [active_person(cid, c, "crew") for c in dict.fromkeys(crew) if c != pic]
        goal = bookings.clip(body.get("goal"), MAX_GOAL, "The goal", required=False)
        notam = body.get("notam") or {}
        if not isinstance(notam, dict) or type(notam.get("checked", False)) is not bool:
            raise BookingError("notam must be {checked, reference}.")
        reference = bookings.clip(notam.get("reference"), MAX_REFERENCE, "The NOTAM reference", required=False)
        decider = may_decide(cid, thing)
        mine = theirs = "direct"
        if not decider:
            if not thing["active"]:
                raise BookingError(f"{thing['name']} is not open for bookings.", 409)
            mine = rule_for(cid, thing["id"], g.user["id"])
            theirs = mine if pic == g.user["id"] else rule_for(cid, thing["id"], pic)
            if mine is None or theirs is None:
                names = " or ".join(r["name"] for r in rules(cid, thing["id"]))
                who = "you" if mine is None else person_name(cid, pic)
                raise BookingError(f"To plan an outdoor flight {who} need{'' if who == 'you' else 's'} a current "
                                   f"qualification: {names}.", 403)
        status = "approved" if decider or "approval" not in (mine, theirs) else "pending"
        height, area = parse_area_fields(body, place)
        return dict(pic_id=pic, project_id=project, drone_id=drone, goal=goal, height_m=height, flight_area=area,
                    notam=dict(checked=bool(notam.get("checked")), reference=reference)), crew, status

    def preparation(cid, thing, place_id, start, end, team, crew):
        return dict(prepared_at=utc_now(), weather=forecast(cid, place_id, start, end),
                    area=area_plan(place_geometry(cid, place_id), team["height_m"], team["flight_area"]),
                    checks=checks(cid, thing["id"], start[:10], team["pic_id"], crew, team["drone_id"]),
                    notam=team["notam"], notam_url=env("NOTAM_URL") or None)

    def area_columns(prep):
        """The flight area, height and buffer of a team as stored in its own columns (for audits and linking)."""
        a = prep["area"]
        return (a["height_m"], json.dumps(a["area"]) if a["area"] else None, a["buffer_m"], a["method"],
                None if a["inside"] is None else int(a["inside"]))

    def insert_team(cid, booking_id, team, crew, status, prep):
        tid = cid.execute(
            "INSERT INTO operation_teams(booking_id,pic_id,project_id,drone_id,goal,status,created_by,created_at,"
            "preparation,height_m,flight_area,buffer_m,buffer_method,buffer_inside) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (booking_id, team["pic_id"], team["project_id"], team["drone_id"], team["goal"], status, g.user["id"],
             utc_now(), json.dumps(prep), *area_columns(prep))).lastrowid
        cid.executemany("INSERT INTO operation_crew(team_id,user_id) VALUES (?,?)", [(tid, c) for c in crew])
        return tid

    def mail_waiting(cid, thing, slot, team_id, pic_id):
        who = person_name(cid, pic_id)
        span = when(slot["starts_at"], slot["ends_at"])
        for uid, is_admin in bookings.deciders(cid, thing):
            if uid == g.user["id"]:
                continue
            mail.notify(cid, uid, "booking_requests", f"Flight plan to approve: {slot['place']}, {span}",
                        f"{person_name(cid, g.user['id'])} plans an outdoor flight at {slot['place']} for {span}"
                        f" (PIC {who}). It waits for approval.",
                        "/people?tab=approvals&kind=operation" if is_admin
                        else f"/agenda?item={thing['id']}&week={slot['starts_at'][:10]}",
                        key=f"operation-request:{team_id}:{uid}")

    def mail_joined(cid, slot, team_id):
        span = when(slot["starts_at"], slot["ends_at"])
        for r in cid.execute("SELECT DISTINCT pic_id FROM operation_teams WHERE booking_id=? AND id!=?"
                             " AND status!='rejected'", (slot["id"], team_id)).fetchall():
            if r[0] != g.user["id"]:
                mail.notify(cid, r[0], "bookings", f"Joined: {slot['place']}, {span}",
                            f"{person_name(cid, g.user['id'])} joined your slot at {slot['place']} for {span}.",
                            f"/agenda?week={slot['starts_at'][:10]}")

    def team_json(cid, row, thing, decide):
        prep = json.loads(row["preparation"] or "{}")
        mine = g.user["id"] in (row["created_by"], row["pic_id"])
        return dict(id=row["id"], pic_id=row["pic_id"], pic=row["pic"], project_id=row["project_id"],
                    project=row["project"], drone_id=row["drone_id"], drone=row["drone"], goal=row["goal"],
                    crew=crew_of(cid, row["id"]), status=row["status"], created_by=row["created_by"],
                    height_m=row["height_m"], own_area=bool((prep.get("area") or {}).get("own_area")),
                    creator=row["creator"], created_at=row["created_at"], review_note=row["review_note"],
                    preparation=prep, can_edit=mine or bool(g.user["is_admin"]),
                    can_decide=decide and row["status"] == "pending")

    def slot_json(cid, slot, thing):
        decide = may_decide(cid, thing)
        teams = [team_json(cid, r, thing, decide) for r in team_rows(cid, slot["id"])
                 if r["status"] != "rejected" or g.user["id"] in (r["created_by"], r["pic_id"]) or decide]
        mine = any(t["can_edit"] and g.user["id"] in (t["created_by"], t["pic_id"]) for t in teams)
        live = [t for t in teams if t["status"] != "rejected"]
        return dict(id=slot["id"], place_id=slot["place_id"], place=slot["place"], start=slot["starts_at"],
                    geometry=place_geometry(cid, slot["place_id"]),
                    end=slot["ends_at"], user_id=slot["user_id"], teams=teams,
                    can_join=not mine and (decide or rule_for(cid, thing["id"], g.user["id"]) is not None),
                    can_move=bool(g.user["is_admin"]) or decide or (len(live) <= 1 and mine), can_decide=decide)

    @app.get("/api/operations/options")
    def operation_options():
        """What Plan flight offers: bookable locations, your projects, people, aircraft and whether you may book."""
        signed_in()
        cid = connection()
        thing = operations_item(cid)
        rule = "direct" if may_decide(cid, thing) else rule_for(cid, thing["id"], g.user["id"])
        return jsonify(
            item_id=thing["id"], rule=rule, rules=rules(cid, thing["id"]), notam_url=env("NOTAM_URL") or None,
            places=[place_geometry(cid, r[0]) for r in cid.execute(
                "SELECT id FROM places WHERE bookable=1 AND indoor=0 ORDER BY name")],
            projects=projects_for(cid, g.user),
            people=[dict(r) for r in cid.execute("SELECT id, COALESCE(display_name, username) AS name FROM users"
                                                 " WHERE is_active=1 AND is_external=0 ORDER BY name COLLATE NOCASE")],
            drones=[dict(r) for r in cid.execute("SELECT id, name, status FROM drones WHERE COALESCE(status,'')!='Discard'"
                                                 " ORDER BY name COLLATE NOCASE")])

    @app.post("/api/operations/prepare")
    def operation_prepare():
        """Weather, checks and overlapping slots for the Plan flight form (nothing is stored)."""
        signed_in()
        cid = connection()
        body = request.get_json(silent=True) or {}
        thing = operations_item(cid)
        place = bookable_place(cid, body.get("place_id"))
        start, end = parse_time_window(body)
        pic = active_person(cid, body.get("pic_id", g.user["id"]), "PIC")
        crew = body.get("crew", [])
        if not isinstance(crew, list) or any(type(c) is not int for c in crew):
            raise BookingError("crew must be a list of account ids.")
        drone = body.get("drone_id") if type(body.get("drone_id")) is int else None
        geometry = place_geometry(cid, place["id"])
        height, area = parse_area_fields(body, geometry)
        overlapping = [dict(id=r["id"], start=r["starts_at"], end=r["ends_at"], person=r["person"])
                       for r in cid.execute(
                           "SELECT b.id, b.starts_at, b.ends_at, COALESCE(u.display_name,u.username) AS person"
                           " FROM bookings b JOIN operation_slots s ON s.booking_id=b.id JOIN users u ON u.id=b.user_id"
                           " WHERE s.place_id=? AND b.starts_at<? AND b.ends_at>? AND b.status!='rejected'"
                           " AND b.id IS NOT ? ORDER BY b.starts_at", (place["id"], end, start, body.get("ignore_id")))]
        return jsonify(weather=forecast(cid, place["id"], start, end), area=area_plan(geometry, height, area),
                       checks=checks(cid, thing["id"], start[:10], pic, [c for c in crew if c != pic], drone),
                       slots=overlapping, notam_url=env("NOTAM_URL") or None)

    @app.post("/api/operations")
    def plan_flight():
        """A new slot with the booker's team: {place_id, start, end, project_id, pic_id, crew, drone_id, goal,
        notam: {checked, reference}}."""
        signed_in()
        cid = connection()
        body = request.get_json(silent=True) or {}
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            thing = operations_item(cid)
            place = bookable_place(cid, body.get("place_id"))
            start, end = parse_time_window(body)
            team, crew, status = parse_team(cid, thing, body, start, place_geometry(cid, place["id"]))
            stamp = utc_now()
            bid = cid.execute("INSERT INTO bookings(thing_id,user_id,description,starts_at,ends_at,created_at,status)"
                              " VALUES (?,?,?,?,?,?,'approved')",
                              (thing["id"], g.user["id"], place["name"], start, end, stamp)).lastrowid
            cid.execute("INSERT INTO operation_slots(booking_id, place_id) VALUES (?,?)", (bid, place["id"]))
            tid = insert_team(cid, bid, team, crew, status, preparation(cid, thing, place["id"], start, end, team, crew))
            sync_slot(cid, bid)
            if status == "pending":
                mail_waiting(cid, thing, slot_row(cid, bid), tid, team["pic_id"])
        return jsonify(ok=True, id=bid, team_id=tid, status=status), 201

    @app.get("/api/operations/<int:booking_id>")
    def get_slot(booking_id):
        signed_in()
        cid = connection()
        return jsonify(slot_json(cid, slot_row(cid, booking_id), operations_item(cid)))

    @app.post("/api/operations/<int:booking_id>/teams")
    def join_slot(booking_id):
        """Join a slot with your team for its whole time (same fields as Plan flight, without place and time)."""
        signed_in()
        cid = connection()
        body = request.get_json(silent=True) or {}
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            thing = operations_item(cid)
            slot = slot_row(cid, booking_id)
            if slot["status"] == "rejected":
                raise BookingError("This slot was not approved; plan a new one.", 409)
            team, crew, status = parse_team(cid, thing, body, slot["starts_at"], place_geometry(cid, slot["place_id"]))
            if cid.execute("SELECT 1 FROM operation_teams WHERE booking_id=? AND pic_id=? AND status!='rejected'",
                           (booking_id, team["pic_id"])).fetchone():
                raise BookingError("That PIC already has a team in this slot.", 409)
            tid = insert_team(cid, booking_id, team, crew, status,
                              preparation(cid, thing, slot["place_id"], slot["starts_at"], slot["ends_at"], team, crew))
            sync_slot(cid, booking_id)
            mail_joined(cid, slot, tid)
            if status == "pending":
                mail_waiting(cid, thing, slot, tid, team["pic_id"])
        return jsonify(ok=True, team_id=tid, status=status), 201

    def own_team(cid, team_id):
        row = cid.execute("SELECT * FROM operation_teams WHERE id=?", (team_id,)).fetchone()
        if row is None:
            raise BookingError("No such team.", 404)
        if g.user["id"] not in (row["created_by"], row["pic_id"]) and not g.user["is_admin"]:
            raise BookingError("Only the team's PIC, the person who added it and administrators can change it.", 403)
        return row

    @app.put("/api/operations/teams/<int:team_id>")
    def edit_team(team_id):
        """Change a team (PIC, project, crew, aircraft, goal, NOTAM check); the preparation is taken again. A team
        that waits for approval, or was rejected, waits again when its people still need approval."""
        signed_in()
        cid = connection()
        body = request.get_json(silent=True) or {}
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            thing = operations_item(cid)
            row = own_team(cid, team_id)
            slot = slot_row(cid, row["booking_id"])
            team, crew, status = parse_team(cid, thing, body, slot["starts_at"], place_geometry(cid, slot["place_id"]))
            if row["status"] == "approved" and team["pic_id"] == row["pic_id"]:
                status = "approved"  # an approved team stays approved while its PIC is the same
            prep = preparation(cid, thing, slot["place_id"], slot["starts_at"], slot["ends_at"], team, crew)
            reset = status != row["status"]
            cid.execute("UPDATE operation_teams SET pic_id=?, project_id=?, drone_id=?, goal=?, status=?, preparation=?,"
                        " height_m=?, flight_area=?, buffer_m=?, buffer_method=?, buffer_inside=?,"
                        " decided_by=CASE WHEN ? THEN NULL ELSE decided_by END,"
                        " decided_at=CASE WHEN ? THEN NULL ELSE decided_at END,"
                        " review_note=CASE WHEN ? THEN NULL ELSE review_note END WHERE id=?",
                        (team["pic_id"], team["project_id"], team["drone_id"], team["goal"], status, json.dumps(prep),
                         *area_columns(prep), reset, reset, reset, team_id))
            cid.execute("DELETE FROM operation_crew WHERE team_id=?", (team_id,))
            cid.executemany("INSERT INTO operation_crew(team_id,user_id) VALUES (?,?)", [(team_id, c) for c in crew])
            sync_slot(cid, slot["id"])
            if status == "pending" and row["status"] != "pending":
                mail_waiting(cid, thing, slot, team_id, team["pic_id"])
        return jsonify(ok=True, status=status)

    @app.delete("/api/operations/teams/<int:team_id>")
    def leave_slot(team_id):
        """Take a team out of its slot; the slot goes when its last team leaves."""
        signed_in()
        cid = connection()
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            row = own_team(cid, team_id)
            slot = slot_row(cid, row["booking_id"])
            cid.execute("DELETE FROM operation_teams WHERE id=?", (team_id,))
            kept = sync_slot(cid, slot["id"])
            span = when(slot["starts_at"], slot["ends_at"])
            for r in cid.execute("SELECT DISTINCT pic_id FROM operation_teams WHERE booking_id=? AND status!='rejected'",
                                 (slot["id"],)).fetchall() if kept else ():
                if r[0] != g.user["id"]:
                    mail.notify(cid, r[0], "bookings", f"Left: {slot['place']}, {span}",
                                f"{person_name(cid, row['pic_id'])}'s team left your slot at {slot['place']} for {span}.",
                                f"/agenda?week={slot['starts_at'][:10]}")
        return jsonify(ok=True, slot_removed=not kept)

    @app.put("/api/operations/<int:booking_id>")
    def move_slot(booking_id):
        """Change the time of a slot: administrators and approvers, or its only team."""
        signed_in()
        cid = connection()
        body = request.get_json(silent=True) or {}
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            thing = operations_item(cid)
            slot = slot_row(cid, booking_id)
            if not slot_json(cid, slot, thing)["can_move"]:
                raise BookingError("Only administrators, approvers or a slot's only team can change its time.", 403)
            start, end = parse_time_window(body)
            cid.execute("UPDATE bookings SET starts_at=?, ends_at=? WHERE id=?", (start, end, booking_id))
        return jsonify(ok=True)

    @app.post("/api/operations/teams/<int:team_id>/review")
    def review_team(team_id):
        """Approve or reject a waiting team: administrators and the item's approvers. {action, reason}"""
        signed_in()
        body = request.get_json(silent=True) or {}
        action, reason = body.get("action"), body.get("reason") or ""
        if action not in ("approve", "reject"):
            raise BookingError("Choose approve or reject.")
        if not isinstance(reason, str) or len(reason.strip()) > bookings.MAX_DESCRIPTION:
            raise BookingError(f"The reason can be at most {bookings.MAX_DESCRIPTION} characters.")
        cid = connection()
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            thing = operations_item(cid)
            if not may_decide(cid, thing):
                raise BookingError("Only administrators and the approvers of Outdoor operations decide flight plans.", 403)
            row = cid.execute("SELECT * FROM operation_teams WHERE id=?", (team_id,)).fetchone()
            if row is None:
                raise BookingError("No such team.", 404)
            if row["status"] != "pending":
                raise BookingError("Only a team waiting for approval can be approved or rejected.", 409)
            note = reason.strip() if action == "reject" else None
            cid.execute("UPDATE operation_teams SET status=?, decided_by=?, decided_at=?, review_note=? WHERE id=?",
                        ("approved" if action == "approve" else "rejected", g.user["id"], utc_now(), note, team_id))
            slot = slot_row(cid, row["booking_id"])
            sync_slot(cid, slot["id"])
            span = when(slot["starts_at"], slot["ends_at"])
            for uid in {row["pic_id"], row["created_by"]} - {g.user["id"], None}:
                mail.notify(cid, uid, "booking_requests",
                            f"{'Approved' if action == 'approve' else 'Not approved'}: {slot['place']}, {span}",
                            f"{person_name(cid, g.user['id'])} "
                            + ("approved" if action == "approve" else "did not approve")
                            + f" the outdoor flight at {slot['place']} for {span}."
                            + (f"\n\nReason: {note}" if note else ""),
                            f"/agenda?item={thing['id']}&week={slot['starts_at'][:10]}")
        return jsonify(ok=True)

    @app.get("/api/admin/operation-pending")
    def pending_teams():
        """Teams waiting for approval, oldest first, for the People approval queue."""
        signed_in()
        if not g.user["is_admin"]:
            raise BookingError("Administrator access required.", 403)
        cid = connection()
        out = []
        for r in cid.execute(
                "SELECT o.*, b.starts_at, b.ends_at, p.name AS place, COALESCE(u.display_name,u.username) AS person,"
                " pr.name AS project, d.name AS drone FROM operation_teams o JOIN bookings b ON b.id=o.booking_id"
                " JOIN operation_slots s ON s.booking_id=b.id JOIN places p ON p.id=s.place_id"
                " JOIN users u ON u.id=o.pic_id LEFT JOIN projects pr ON pr.id=o.project_id"
                " LEFT JOIN drones d ON d.id=o.drone_id WHERE o.status='pending' ORDER BY o.created_at, o.id"):
            prep = json.loads(r["preparation"] or "{}")
            out.append(dict(id=r["id"], booking_id=r["booking_id"], user_id=r["pic_id"], person=r["person"],
                            place=r["place"], start=r["starts_at"], end=r["ends_at"], project=r["project"],
                            drone=r["drone"], goal=r["goal"], crew=[c["name"] for c in crew_of(cid, r["id"])],
                            checks=prep.get("checks") or [], notam=prep.get("notam") or {},
                            area={k: (prep.get("area") or {}).get(k) for k in ("height_m", "buffer_m", "method", "inside", "own_area")},
                            submitted_at=r["created_at"]))
        return jsonify(teams=out)
