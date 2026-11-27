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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import abort, g, jsonify, request

import bookings
import db
import ground_risk
import home
import links
import mail
import weather
from bookings import BookingError, person_name, utc_now, when
from config import env, env_bool

ITEM_NAME = "Outdoor operations"
MAX_GOAL = 500
MAX_REFERENCE = 200
MAX_CREW = 10
MAX_DRONES = 10
MATCH_MARGIN = datetime.timedelta(minutes=30)  # flights this long before or after a slot still belong to it
REMIND_BEFORE = datetime.timedelta(hours=12)  # a slot still not booked this long before its deadline: reminder
BOOKING_TAGS = ("{place}", "{provider}", "{date}", "{start}", "{end}", "{book_by}", "{teams}", "{booker}",
                "{reference}", "{request}")


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
                       (ITEM_NAME, "Plan and book outdoor flights", db.item_colour(con), order, "operations")).lastrowid


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


def checks(con, thing_id, day, pic_id, crew_ids, drone_ids):
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
    for drone_id in drone_ids:
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
        return weather.forecast_window(lat, lon, start, end, zone().key)
    except weather.WeatherError as exc:
        return {"hours": [], "note": str(exc)}
    except (KeyError, IndexError, TypeError, ValueError):
        return {"hours": [], "note": "The location has no usable position."}


def place_geometry(con, place_id):
    """{id, name, shape, max_height_m, flight_area, booking} of a location, for the map, the buffer and the external
    booking notice (booking: {mode, provider, lead_hours, mail_to} when it is booked elsewhere, else None)."""
    r = con.execute("SELECT * FROM places WHERE id=?", (place_id,)).fetchone()
    booking = dict(mode=r["booking_mode"], provider=r["booking_provider"], lead_hours=r["booking_lead_hours"],
                   mail_to=r["booking_mail_to"]) if r["booking_mode"] != "none" else None
    return dict(id=r["id"], name=r["name"], shape=json.loads(r["shape"]), max_height_m=r["max_height_m"],
                flight_area=json.loads(r["flight_area"]) if r["flight_area"] else None, booking=booking)


def parse_booking_settings(body, row):
    """The external booking settings of a location from PUT /api/admin/places (missing keys keep `row`'s):
    (mode, provider, lead_hours, mail_to, mail_text); ValueError when invalid. By mail needs the mailbox and a text."""
    import training
    get = lambda key: body[key] if key in body else row[key]  # noqa: E731
    mode, provider, lead, to, text = (get("booking_mode"), get("booking_provider"), get("booking_lead_hours"),
                                      get("booking_mail_to"), get("booking_mail_text"))
    if mode not in ("none", "mail"):
        raise ValueError("booking_mode must be none or mail.")
    if not isinstance(provider, str) or len(provider.strip()) > 80 or not isinstance(text, str) or len(text) > 5000:
        raise ValueError("Who books it is at most 80 characters and the mail at most 5000.")
    if lead is not None and (type(lead) is not int or not 0 <= lead <= 720):
        raise ValueError("Book at least: a whole number of hours (0 to 720).")
    try:
        to = training.addresses_field(to or "")
    except training.TrainingError as exc:
        raise ValueError(str(exc)) from exc
    if mode == "mail" and not (to and text.strip()):
        raise ValueError("Booking by mail needs the mailbox and the mail text.")
    return mode, provider.strip(), lead, to, text.strip()


def local_time(t, hours=0):
    """A local agenda time (YYYY-MM-DDTHH:MM) moved by `hours`."""
    return (datetime.datetime.strptime(t, bookings.TIME_FORMAT) + datetime.timedelta(hours=hours)).strftime(
        bookings.TIME_FORMAT)


def booking_mail(con, booking_id, event, was=None):
    """(subject, body, reply-to) of a mail about a slot's external booking to the location's mailbox. event:
    request, change (was: the old time), joined, left, cancelled or reminder. The location's own text (tags filled in)
    is in a request, change and reminder; every mail ends with a fixed "Request details:" block for mail flows."""
    r = con.execute(
        "SELECT b.id, b.starts_at, b.ends_at, b.user_id, s.*, p.name AS place, p.booking_provider, p.booking_mail_text,"
        " p.country,"
        " COALESCE(u.display_name,u.username) AS booker, u.email FROM bookings b JOIN operation_slots s ON s.booking_id=b.id"
        " JOIN places p ON p.id=s.place_id JOIN users u ON u.id=b.user_id WHERE b.id=?", (booking_id,)).fetchone()
    teams = [f"{t['pic']} ({', '.join(x for x in (t['project'], *(d['name'] for d in drones_of(con, t['id']))) if x) or 'no project'})"
             for t in con.execute(
                 "SELECT o.id, COALESCE(u.display_name,u.username) AS pic, pr.name AS project FROM operation_teams o"
                 " JOIN users u ON u.id=o.pic_id LEFT JOIN projects pr ON pr.id=o.project_id"
                 " WHERE o.booking_id=? AND o.status='approved' ORDER BY o.id", (booking_id,))]
    span = when(r["starts_at"], r["ends_at"])
    values = {"{place}": r["place"], "{provider}": r["booking_provider"] or "-", "{date}": r["starts_at"][:10],
              "{start}": r["starts_at"][11:], "{end}": r["ends_at"][11:],
              "{book_by}": (r["book_by"] or "-").replace("T", " "), "{teams}": "; ".join(teams) or "-",
              "{booker}": r["booker"], "{reference}": f"S{r['id']}", "{request}": event}
    text = r["booking_mail_text"]
    for key in BOOKING_TAGS:
        text = text.replace(key, values[key])
    ref = f" (booking number {r['external_ref']})" if r["external_ref"] else ""
    intro = {"request": "", "change": f"Changed: this slot is now {span} (was {was}). Please change the booking{ref}.",
             "joined": f"Another team joined this slot{ref}; the teams are listed below.",
             "left": f"A team left this slot{ref}; the teams are listed below.",
             "cancelled": f"Cancelled: this slot is no longer needed. Please cancel the booking{ref}.",
             "reminder": f"Reminder: this slot is not booked yet. It must be booked by {values['{book_by}']}."}[event]
    details = "\n".join(" ".join(line.split()) for line in [
        "Request details:", f"Type: {event}", f"Location: {r['place']}", f"Booked at: {values['{provider}']}",
        f"Date: {values['{date}']}", f"Start: {values['{start}']}", f"End: {values['{end}']}",
        f"Book by: {values['{book_by}']}",
        # the times are the agenda's: say in which zone only for a location in another country than that zone's
        *([f"Time zone: {zone().key}"] if r["country"] and r["country"] != zone_country() else []),
        f"Teams: {values['{teams}']}", f"Booked by: {r['booker']}",
        f"Email: {r['email'] or '-'}", f"Booking number: {r['external_ref'] or '-'}", f"Reference: S{r['id']}"])
    subject = {"request": "Booking request", "change": "Booking change", "joined": "Booking update",
               "left": "Booking update", "cancelled": "Booking cancelled", "reminder": "Not booked yet"}[event]
    parts = [intro, text if event in ("request", "change", "reminder") else "", details]
    return f"{subject}: {r['place']}, {span}", "\n\n".join(x for x in parts if x), r["email"] or None


def external_event(con, booking_id, event, was=None):
    """Keep a slot's external booking up to date after a change (in the caller's transaction). A slot at a location
    booked by mail asks for the booking when it gets its first approved team (status requested); after that the
    mailbox hears of joins, leaves, a new time (requested again) and the cancellation (before the slot is removed)."""
    r = con.execute("SELECT s.*, b.starts_at, p.booking_mode, p.booking_lead_hours, p.booking_mail_to FROM operation_slots s"
                    " JOIN bookings b ON b.id=s.booking_id JOIN places p ON p.id=s.place_id WHERE s.booking_id=?",
                    (booking_id,)).fetchone()
    if r is None:
        return
    approved = con.execute("SELECT COUNT(*) FROM operation_teams WHERE booking_id=? AND status='approved'",
                           (booking_id,)).fetchone()[0]
    book_by = local_time(r["starts_at"], -r["booking_lead_hours"]) if r["booking_lead_hours"] is not None else None
    def send(kind):
        subject, body, reply_to = booking_mail(con, booking_id, kind, was)
        mail.queue_access(con, r["booking_mail_to"], subject, body, reply_to=reply_to, copies=False, kind="booking")
    if r["external"] is None:
        if r["booking_mode"] == "mail" and r["booking_mail_to"] and approved and event != "cancelled":
            con.execute("UPDATE operation_slots SET external='mail', external_status='requested', requested_at=?,"
                        " book_by=? WHERE booking_id=?", (utc_now(), book_by, booking_id))
            send("request")
        return
    if not r["booking_mail_to"]:
        return
    if event == "moved":
        con.execute("UPDATE operation_slots SET external_status='requested', book_by=?, reminded_at=NULL"
                    " WHERE booking_id=?", (book_by, booking_id))
        send("change")
    elif event in ("joined", "left", "cancelled"):
        send(event)


def queue_booking_reminders(con, now=None):
    """Remind the location's mailbox and the administrators of slots still not booked REMIND_BEFORE before their
    deadline (or before the slot without one), once per slot. Run by the mail thread."""
    now = now or datetime.datetime.now(zone()).strftime(bookings.TIME_FORMAT)
    for r in con.execute(
            "SELECT s.booking_id, s.book_by, b.starts_at, b.ends_at, p.name AS place FROM operation_slots s"
            " JOIN bookings b ON b.id=s.booking_id JOIN places p ON p.id=s.place_id"
            " WHERE s.external_status='requested' AND s.reminded_at IS NULL AND b.ends_at>? AND b.status!='rejected'",
            (now,)).fetchall():
        due = local_time(r["book_by"] or r["starts_at"], -REMIND_BEFORE.total_seconds() / 3600)
        if now < due:
            continue
        con.execute("UPDATE operation_slots SET reminded_at=? WHERE booking_id=?", (utc_now(), r["booking_id"]))
        external_reminder(con, r)
    con.commit()


def external_reminder(con, r):
    subject, body, reply_to = booking_mail(con, r["booking_id"], "reminder")
    to = con.execute("SELECT p.booking_mail_to FROM operation_slots s JOIN places p ON p.id=s.place_id"
                     " WHERE s.booking_id=?", (r["booking_id"],)).fetchone()[0]
    mail.queue_access(con, to, subject, body, reply_to=reply_to, copies=False, kind="booking")
    item_id = con.execute("SELECT thing_id FROM bookings WHERE id=?", (r["booking_id"],)).fetchone()[0]
    mail.notify_admins(con, subject, f"{r['place']}, {when(r['starts_at'], r['ends_at'])} is not booked yet"
                       + (f" (book by {r['book_by'].replace('T', ' ')})" if r["book_by"] else "")
                       + ". Enter the booking number in the slot once it is booked.",
                       f"/agenda?item={item_id}&slot={r['booking_id']}", key=f"booking-reminder:{r['booking_id']}")


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
        "SELECT o.*, COALESCE(u.display_name,u.username) AS pic, p.name AS project,"
        " COALESCE(c.display_name,c.username) AS creator FROM operation_teams o JOIN users u ON u.id=o.pic_id"
        " LEFT JOIN projects p ON p.id=o.project_id"
        " LEFT JOIN users c ON c.id=o.created_by WHERE o.booking_id=? ORDER BY o.id", (booking_id,)).fetchall()


def crew_of(con, team_id):
    return [dict(id=r[0], name=r[1]) for r in con.execute(
        "SELECT u.id, COALESCE(u.display_name,u.username) FROM operation_crew c JOIN users u ON u.id=c.user_id"
        " WHERE c.team_id=? ORDER BY 2", (team_id,))]


def drones_of(con, team_id):
    return [dict(id=r[0], name=r[1]) for r in con.execute(
        "SELECT d.id, d.name FROM operation_drones o JOIN drones d ON d.id=o.drone_id WHERE o.team_id=?"
        " ORDER BY d.name COLLATE NOCASE", (team_id,))]


def summaries(con, booking_ids):
    """{booking id: {place_id, place, teams, pending, pics}} for the agenda's slots (rejected teams not counted)."""
    out = {}
    if not booking_ids:
        return out
    marks = ",".join("?" * len(booking_ids))
    for r in con.execute(f"SELECT s.booking_id, s.place_id, p.name, s.external_status, s.external_ref FROM operation_slots s"
                         f" JOIN places p ON p.id=s.place_id"
                         f" WHERE s.booking_id IN ({marks})", booking_ids):
        out[r[0]] = dict(place_id=r[1], place=r[2], teams=0, pending=0, pics=[], external_status=r[3], external_ref=r[4])
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


def zone():
    """The time zone of the agenda's local times (LOGDB_TIME_ZONE)."""
    try:
        return ZoneInfo(env("TIME_ZONE") or "Europe/Amsterdam")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def zone_country():
    """The country (ISO code) of LOGDB_TIME_ZONE from the system's zone.tab, or None when it cannot be told."""
    import zoneinfo
    from pathlib import Path
    name = zone().key
    for folder in zoneinfo.TZPATH:
        table = Path(folder) / "zone.tab"
        if table.is_file():
            for line in table.read_text(encoding="utf-8", errors="replace").splitlines():
                cols = line.split("\t")
                if len(cols) >= 3 and cols[2] == name:
                    return cols[0]
            return None
    return None


def slot_window(start, end):
    """(first, last) UTC epoch seconds of takeoffs that belong to a slot of local times: the slot with MATCH_MARGIN."""
    z = zone()
    epoch = lambda t: datetime.datetime.strptime(t, bookings.TIME_FORMAT).replace(tzinfo=z).timestamp()  # noqa: E731
    return epoch(start) - MATCH_MARGIN.total_seconds(), epoch(end) + MATCH_MARGIN.total_seconds()


def flight_decisions(con):
    """{flight id: [(team id, 'linked' or 'unlinked')]} of the links made by hand, for the present flight ids; a link
    whose flight was renumbered by a re-parse (another takeoff time) is left out, as in links.resolved."""
    out = {}
    for r in con.execute(
            "SELECT k.team_id, k.decision, k.takeoff_utc AS kept, f.id AS flight_id, f.takeoff_utc FROM operation_flight_links k"
            " JOIN aircraft a ON a.log_id=k.log_id AND COALESCE(a.ac_id, ?)=k.ac_id"
            " JOIN flights f ON f.aircraft_id=a.id AND f.idx=k.flight_idx", (links.NO_AC_ID,)):
        kept, now = links._ts(r["kept"]), links._ts(r["takeoff_utc"])
        if kept is not None and now is not None and abs(kept - now) > links.STALE_S:
            continue
        out.setdefault(r["flight_id"], []).append((r["team_id"], r["decision"]))
    return out


def slot_teams(con, booking_id):
    """{team id: (people: PIC and crew, aircraft ids)} of a slot's teams that were not rejected."""
    return {t["id"]: ({t["pic_id"], *(c["id"] for c in crew_of(con, t["id"]))}, {d["id"] for d in drones_of(con, t["id"])})
            for t in con.execute("SELECT id, pic_id FROM operation_teams WHERE booking_id=? AND status!='rejected'"
                                 " ORDER BY id", (booking_id,))}


def last_flown_project(rows, user_id, allowed):
    """The project of the user's latest real flight (PIC, PAC or GCS; telemetry or paper) among the logbook rows
    the user may see, if it is one of `allowed` project ids; else None."""
    best, best_key = None, None
    for r in rows:
        pid = r.get("project_id")
        if pid not in allowed or user_id not in flight_people(r) or not home.real_flight(r):
            continue
        key = (home.row_day(r), r.get("takeoff_utc") or r.get("takeoff_time") or "")
        if key[0] and (best_key is None or key > best_key):
            best, best_key = pid, key
    return best


def flight_people(entry):
    return {u["id"] for role in ("pic", "pac", "gcs") if (u := entry.get(role + "_user")) and u.get("id")}


def assign(entry, teams, decisions):
    """(team id or None, automatic) of a logbook entry among a slot's teams: the team it was linked to by hand, else the
    first team whose PIC, crew or aircraft flew it (unless kept out by hand). A flight linked by hand to a team of
    another slot belongs to none of these."""
    mine = [d for s in entry["sources"] for d in decisions.get(s["flight_id"], [])]
    linked = [t for t, d in mine if d == "linked"]
    if linked:
        return next((t for t in linked if t in teams), None), False
    kept_out = {t for t, d in mine if d == "unlinked"}
    people, drone = flight_people(entry), entry.get("drone_id")
    for tid, (team_people, team_drones) in teams.items():
        if tid not in kept_out and (people & team_people or (drone and drone in team_drones)):
            return tid, True
    return None, True


def local_of(utc_iso):
    """A UTC time from a log as the agenda's local time (YYYY-MM-DDTHH:MM, LOGDB_TIME_ZONE), or None."""
    ts = links._ts(utc_iso)
    return datetime.datetime.fromtimestamp(ts, zone()).strftime(bookings.TIME_FORMAT) if ts is not None else None


def flight_brief(entry, automatic):
    return dict(flight_id=entry["flight_id"], takeoff_utc=entry.get("takeoff_utc"), duration_s=entry.get("duration_s"),
                takeoff_local=local_of(entry.get("takeoff_utc")),
                aircraft=entry.get("aircraft"), pic=(entry.get("pic_user") or {}).get("display_name") or entry.get("pic"),
                automatic=automatic)


def slot_flights(con, slot, rows):
    """({team id: [flights]}, [other flights]) of a slot: the logbook entries the viewer may see that took off or
    landed at its location within its time (MATCH_MARGIN around it), and those linked to one of its teams by hand.
    Other flights are there but match no team: for a person to link."""
    teams = slot_teams(con, slot["id"])
    first, last = slot_window(slot["starts_at"], slot["ends_at"])
    decisions = flight_decisions(con)
    linked_here = {fid for fid, ds in decisions.items() for t, d in ds if d == "linked" and t in teams}
    by_team, other = {t: [] for t in teams}, []
    for e in rows:
        if e.get("source_type") != "telemetry" or not e.get("flight_id"):
            continue
        ts = links._ts(e.get("takeoff_utc"))
        here = slot["place_id"] in (e.get("takeoff_place_id"), e.get("landing_place_id")) and ts is not None \
            and first <= ts <= last
        if not here and not {s["flight_id"] for s in e["sources"]} & linked_here:
            continue
        tid, automatic = assign(e, teams, decisions)
        (by_team[tid] if tid else other).append(flight_brief(e, automatic))
    for flights in (*by_team.values(), other):
        flights.sort(key=lambda f: f["takeoff_utc"] or "")
    return by_team, other


def flight_operation(con, entry):
    """The outdoor operation a logbook entry belongs to, for the flight page: {booking_id, thing_id, place, start, end,
    team_id, pic, height_m, buffer_m, buffer_inside, automatic, map}; {missing: place, map} for a flight at a bookable
    location without one; None otherwise. map: {boundary, area, buffer_m, inside} to draw under the path: the team's
    flight area and buffer, or (missing) the location's default area with its maximum height; area None when unset."""
    if not enabled() or entry.get("source_type") != "telemetry":
        return None
    decisions = flight_decisions(con)
    linked = [t for s in entry["sources"] for t, d in decisions.get(s["flight_id"], []) if d == "linked"]
    slots = []
    if linked:
        slots = con.execute("SELECT booking_id FROM operation_teams WHERE id=?", (linked[0],)).fetchall()
    else:
        places = {entry.get("takeoff_place_id"), entry.get("landing_place_id")} - {None}
        ts = links._ts(entry.get("takeoff_utc"))
        if places and ts is not None:
            margin = MATCH_MARGIN.total_seconds()
            local = lambda t: datetime.datetime.fromtimestamp(t, zone()).strftime(bookings.TIME_FORMAT)  # noqa: E731
            slots = con.execute(
                f"SELECT s.booking_id FROM operation_slots s JOIN bookings b ON b.id=s.booking_id"
                f" WHERE s.place_id IN ({','.join('?' * len(places))}) AND b.status!='rejected'"
                f" AND b.starts_at<=? AND b.ends_at>=? ORDER BY b.starts_at",
                (*places, local(ts + margin), local(ts - margin))).fetchall()
    for row in slots:
        tid, automatic = assign(entry, slot_teams(con, row[0]), decisions)
        if tid is None:
            continue
        r = con.execute(
            "SELECT b.id, b.thing_id, b.starts_at, b.ends_at, p.name AS place, o.height_m, o.buffer_m, o.buffer_inside,"
            " o.flight_area, p.shape,"
            " COALESCE(u.display_name,u.username) AS pic FROM operation_teams o JOIN bookings b ON b.id=o.booking_id"
            " JOIN operation_slots s ON s.booking_id=b.id JOIN places p ON p.id=s.place_id JOIN users u ON u.id=o.pic_id"
            " WHERE o.id=?", (tid,)).fetchone()
        return dict(booking_id=r["id"], thing_id=r["thing_id"], place=r["place"], start=r["starts_at"], end=r["ends_at"],
                    team_id=tid, pic=r["pic"], height_m=r["height_m"], buffer_m=r["buffer_m"],
                    buffer_inside=None if r["buffer_inside"] is None else bool(r["buffer_inside"]), automatic=automatic,
                    map=dict(boundary=json.loads(r["shape"]), area=json.loads(r["flight_area"]) if r["flight_area"] else None,
                             buffer_m=r["buffer_m"],
                             inside=None if r["buffer_inside"] is None else bool(r["buffer_inside"])))
    bookable = con.execute(
        f"SELECT id FROM places WHERE bookable=1 AND id IN ({','.join('?' * 2)})",
        (entry.get("takeoff_place_id") or -1, entry.get("landing_place_id") or -1)).fetchall()
    if not bookable:
        return None
    place = place_geometry(con, bookable[0][0])
    a = area_plan(place)
    return {"missing": place["name"],
            "map": dict(boundary=place["shape"], area=a["area"], buffer_m=a["buffer_m"], inside=a["inside"])}


def register(app, connection, require_session, logbook=None):
    """logbook: the logbook read model of the signed-in user (app.logbook_rows), to match flights to slots."""
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
        drones = body.get("drones", [])
        if not isinstance(drones, list) or len(drones) > MAX_DRONES or any(type(d) is not int for d in drones):
            raise BookingError(f"drones must be a list of at most {MAX_DRONES} aircraft ids.")
        drones = list(dict.fromkeys(drones))
        if any(not cid.execute("SELECT 1 FROM drones WHERE id=?", (d,)).fetchone() for d in drones):
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
        return dict(pic_id=pic, project_id=project, drones=drones, goal=goal, height_m=height, flight_area=area,
                    notam=dict(checked=bool(notam.get("checked")), reference=reference)), crew, status

    def preparation(cid, thing, place_id, start, end, team, crew):
        return dict(prepared_at=utc_now(), weather=forecast(cid, place_id, start, end),
                    area=area_plan(place_geometry(cid, place_id), team["height_m"], team["flight_area"]),
                    checks=checks(cid, thing["id"], start[:10], team["pic_id"], crew, team["drones"]),
                    notam=team["notam"], notam_url=env("NOTAM_URL") or None)

    def area_columns(prep):
        """The flight area, height and buffer of a team as stored in its own columns (for audits and linking)."""
        a = prep["area"]
        return (a["height_m"], json.dumps(a["area"]) if a["area"] else None, a["buffer_m"], a["method"],
                None if a["inside"] is None else int(a["inside"]))

    def insert_team(cid, booking_id, team, crew, status, prep):
        tid = cid.execute(
            "INSERT INTO operation_teams(booking_id,pic_id,project_id,goal,status,created_by,created_at,"
            "preparation,height_m,flight_area,buffer_m,buffer_method,buffer_inside) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (booking_id, team["pic_id"], team["project_id"], team["goal"], status, g.user["id"],
             utc_now(), json.dumps(prep), *area_columns(prep))).lastrowid
        cid.executemany("INSERT INTO operation_crew(team_id,user_id) VALUES (?,?)", [(tid, c) for c in crew])
        cid.executemany("INSERT INTO operation_drones(team_id,drone_id) VALUES (?,?)", [(tid, d) for d in team["drones"]])
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
                    project=row["project"], drones=drones_of(cid, row["id"]), goal=row["goal"],
                    crew=crew_of(cid, row["id"]), status=row["status"], created_by=row["created_by"],
                    height_m=row["height_m"], own_area=bool((prep.get("area") or {}).get("own_area")),
                    creator=row["creator"], created_at=row["created_at"], review_note=row["review_note"],
                    preparation=prep, can_edit=mine or bool(g.user["is_admin"]),
                    can_decide=decide and row["status"] == "pending")

    def slot_json(cid, slot, thing, flights=False):
        """flights: also the flights matched to its teams and the other flights there (the logbook read model)."""
        decide = may_decide(cid, thing)
        teams = [team_json(cid, r, thing, decide) for r in team_rows(cid, slot["id"])
                 if r["status"] != "rejected" or g.user["id"] in (r["created_by"], r["pic_id"]) or decide]
        mine = any(t["can_edit"] and g.user["id"] in (t["created_by"], t["pic_id"]) for t in teams)
        live = [t for t in teams if t["status"] != "rejected"]
        by_team, other = slot_flights(cid, slot, logbook()) if flights and logbook else ({}, [])
        for t in teams:
            t["flights"] = by_team.get(t["id"], [])
            t["can_link"] = t["can_edit"] or decide
        ext = cid.execute("SELECT s.*, p.booking_provider, p.booking_mail_to, COALESCE(u.display_name,u.username) AS booker"
                          " FROM operation_slots s JOIN places p ON p.id=s.place_id LEFT JOIN users u ON u.id=s.booked_by"
                          " WHERE s.booking_id=?", (slot["id"],)).fetchone()
        external = dict(status=ext["external_status"], ref=ext["external_ref"], provider=ext["booking_provider"],
                        mail_to=ext["booking_mail_to"], requested_at=ext["requested_at"], book_by=ext["book_by"],
                        booked_at=ext["booked_at"], booked_by=ext["booker"], reminded_at=ext["reminded_at"],
                        can_set=bool(g.user["is_admin"])) if ext["external"] else None
        return dict(id=slot["id"], place_id=slot["place_id"], place=slot["place"], start=slot["starts_at"],
                    geometry=place_geometry(cid, slot["place_id"]), other_flights=other, external=external,
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
        projects = projects_for(cid, g.user)
        allowed = {p["id"] for p in projects}
        last_project = last_flown_project(logbook() if logbook else [], g.user["id"], allowed)
        if last_project is None:
            default = cid.execute("SELECT default_project_id FROM users WHERE id=?", (g.user["id"],)).fetchone()
            last_project = default[0] if default and default[0] in allowed else None
        return jsonify(
            item_id=thing["id"], rule=rule, rules=rules(cid, thing["id"]), notam_url=env("NOTAM_URL") or None,
            places=[place_geometry(cid, r[0]) for r in cid.execute(
                "SELECT id FROM places WHERE bookable=1 AND indoor=0 ORDER BY name")],
            projects=projects,
            last_project_id=last_project,
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
        drones = body.get("drones", [])
        if not isinstance(drones, list) or any(type(d) is not int for d in drones):
            raise BookingError("drones must be a list of aircraft ids.")
        geometry = place_geometry(cid, place["id"])
        height, area = parse_area_fields(body, geometry)
        overlapping = [dict(id=r["id"], start=r["starts_at"], end=r["ends_at"], person=r["person"])
                       for r in cid.execute(
                           "SELECT b.id, b.starts_at, b.ends_at, COALESCE(u.display_name,u.username) AS person"
                           " FROM bookings b JOIN operation_slots s ON s.booking_id=b.id JOIN users u ON u.id=b.user_id"
                           " WHERE s.place_id=? AND b.starts_at<? AND b.ends_at>? AND b.status!='rejected'"
                           " AND b.id IS NOT ? ORDER BY b.starts_at", (place["id"], end, start, body.get("ignore_id")))]
        return jsonify(weather=forecast(cid, place["id"], start, end), area=area_plan(geometry, height, area),
                       checks=checks(cid, thing["id"], start[:10], pic, [c for c in crew if c != pic], list(dict.fromkeys(drones))),
                       slots=overlapping, notam_url=env("NOTAM_URL") or None)

    @app.post("/api/operations")
    def plan_flight():
        """A new slot with the booker's team: {place_id, start, end, project_id, pic_id, crew, drones, goal,
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
            external_event(cid, bid, "request")
            if status == "pending":
                mail_waiting(cid, thing, slot_row(cid, bid), tid, team["pic_id"])
        return jsonify(ok=True, id=bid, team_id=tid, status=status), 201

    @app.get("/api/operations/<int:booking_id>")
    def get_slot(booking_id):
        signed_in()
        cid = connection()
        return jsonify(slot_json(cid, slot_row(cid, booking_id), operations_item(cid), flights=True))

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
            if status == "approved":
                external_event(cid, booking_id, "joined")
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
            cid.execute("UPDATE operation_teams SET pic_id=?, project_id=?, drone_id=NULL, goal=?, status=?, preparation=?,"
                        " height_m=?, flight_area=?, buffer_m=?, buffer_method=?, buffer_inside=?,"
                        " decided_by=CASE WHEN ? THEN NULL ELSE decided_by END,"
                        " decided_at=CASE WHEN ? THEN NULL ELSE decided_at END,"
                        " review_note=CASE WHEN ? THEN NULL ELSE review_note END WHERE id=?",
                        (team["pic_id"], team["project_id"], team["goal"], status, json.dumps(prep),
                         *area_columns(prep), reset, reset, reset, team_id))
            cid.execute("DELETE FROM operation_crew WHERE team_id=?", (team_id,))
            cid.executemany("INSERT INTO operation_crew(team_id,user_id) VALUES (?,?)", [(team_id, c) for c in crew])
            cid.execute("DELETE FROM operation_drones WHERE team_id=?", (team_id,))
            cid.executemany("INSERT INTO operation_drones(team_id,drone_id) VALUES (?,?)",
                            [(team_id, d) for d in team["drones"]])
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
            last = not cid.execute("SELECT 1 FROM operation_teams WHERE booking_id=? AND id!=? AND status!='rejected'",
                                   (slot["id"], team_id)).fetchone()
            if last:  # the slot goes: the mailbox cancels its booking
                external_event(cid, slot["id"], "cancelled")
            cid.execute("DELETE FROM operation_teams WHERE id=?", (team_id,))
            kept = sync_slot(cid, slot["id"])
            if kept and not last and row["status"] == "approved":
                external_event(cid, slot["id"], "left")
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
            if (start, end) != (slot["starts_at"], slot["ends_at"]):
                external_event(cid, booking_id, "moved", was=when(slot["starts_at"], slot["ends_at"]))
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
            if action == "approve":  # the first approved team asks for the booking; later ones join it
                external_event(cid, slot["id"], "joined")
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

    @app.post("/api/operations/<int:booking_id>/external")
    def set_external(booking_id):
        """Administrators enter the external booking number of a requested slot ({ref}): it is then booked;
        an empty ref makes it requested again."""
        signed_in()
        if not g.user["is_admin"]:
            raise BookingError("Only administrators enter the booking number.", 403)
        ref = (request.get_json(silent=True) or {}).get("ref")
        if ref is not None and (not isinstance(ref, str) or len(ref.strip()) > 80):
            raise BookingError("The booking number is text of at most 80 characters.")
        ref = (ref or "").strip() or None
        cid = connection()
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            slot = slot_row(cid, booking_id)
            if not cid.execute("SELECT external FROM operation_slots WHERE booking_id=?", (booking_id,)).fetchone()[0]:
                raise BookingError("This slot was not requested from an external booking.", 409)
            cid.execute("UPDATE operation_slots SET external_ref=?, external_status=?, booked_by=?, booked_at=?"
                        " WHERE booking_id=?", (ref, "booked" if ref else "requested", g.user["id"] if ref else None,
                                                utc_now() if ref else None, slot["id"]))
        return jsonify(ok=True)

    @app.post("/api/operations/teams/<int:team_id>/flights")
    def link_flight(team_id):
        """Link a flight to a team by hand ({flight_id, action: link}), which takes it from any other team, or keep a
        flight out of a team ({action: unlink}). The team's PIC, whoever added it, administrators and approvers."""
        signed_in()
        body = request.get_json(silent=True) or {}
        if body.get("action") not in ("link", "unlink") or type(body.get("flight_id")) is not int:
            raise BookingError("Send flight_id and action link or unlink.")
        cid = connection()
        if not any(body["flight_id"] in {s["flight_id"] for s in e.get("sources") or []} for e in logbook()):
            raise BookingError("No such flight.", 404)
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            row = cid.execute("SELECT * FROM operation_teams WHERE id=?", (team_id,)).fetchone()
            if row is None:
                raise BookingError("No such team.", 404)
            if g.user["id"] not in (row["created_by"], row["pic_id"]) and not g.user["is_admin"] \
                    and not may_decide(cid, operations_item(cid)):
                raise BookingError("Only the team's PIC, the person who added it, administrators and approvers link flights.", 403)
            log_id, ac_id, idx, takeoff = links.flight_key(cid, body["flight_id"])
            key = (log_id, ac_id, idx)
            if body["action"] == "link":  # one team per flight
                cid.execute("DELETE FROM operation_flight_links WHERE log_id=? AND ac_id=? AND flight_idx=?"
                            " AND decision='linked'", key)
            cid.execute("INSERT INTO operation_flight_links(team_id,log_id,ac_id,flight_idx,takeoff_utc,decision,"
                        "decided_by,decided_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(team_id,log_id,ac_id,flight_idx)"
                        " DO UPDATE SET decision=excluded.decision, takeoff_utc=excluded.takeoff_utc,"
                        " decided_by=excluded.decided_by, decided_at=excluded.decided_at",
                        (team_id, *key, takeoff, "linked" if body["action"] == "link" else "unlinked", g.user["id"],
                         utc_now()))
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
                " pr.name AS project FROM operation_teams o JOIN bookings b ON b.id=o.booking_id"
                " JOIN operation_slots s ON s.booking_id=b.id JOIN places p ON p.id=s.place_id"
                " JOIN users u ON u.id=o.pic_id LEFT JOIN projects pr ON pr.id=o.project_id"
                " WHERE o.status='pending' ORDER BY o.created_at, o.id"):
            prep = json.loads(r["preparation"] or "{}")
            out.append(dict(id=r["id"], booking_id=r["booking_id"], user_id=r["pic_id"], person=r["person"],
                            place=r["place"], start=r["starts_at"], end=r["ends_at"], project=r["project"],
                            drones=[d["name"] for d in drones_of(cid, r["id"])], goal=r["goal"], crew=[c["name"] for c in crew_of(cid, r["id"])],
                            checks=prep.get("checks") or [], notam=prep.get("notam") or {},
                            area={k: (prep.get("area") or {}).get(k) for k in ("height_m", "buffer_m", "method", "inside", "own_area")},
                            submitted_at=r["created_at"]))
        return jsonify(teams=out)
