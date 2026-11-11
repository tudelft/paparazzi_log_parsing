"""Booking agenda: a week agenda of bookable items. Off unless LOGDB_BOOKINGS=1.

Administrators set up the bookable items in Edit mode on the agenda page: a name and description, an optional
place from Locations, an optional colour, active/inactive and a sort order. An item may require a qualification
type, either for booking only (everyone signed in sees its agenda) or for viewing and booking; without a current
approved qualification of that type the API answers 403 with the id of an active training that grants it.
Administrators may always view, but booking needs the qualification for them too.

Each item has an optional list of booking flags (label, optional colour, and whether it blocks overlaps).
Bookings of the same item may overlap only when both set a common non-blocking flag and neither sets a
blocking flag, so an item without flags never overlaps. Bookings of different items never conflict.
Times are local wall-clock times (YYYY-MM-DDTHH:MM). The booker and administrators change or delete a booking.
"""
import datetime
import re
import sqlite3

from flask import abort, g, jsonify, request, send_from_directory

import db
from config import env_bool

MAX_DESCRIPTION = 500
MAX_DAYS = 7
MAX_FLAGS = 10
TIME_FORMAT = "%Y-%m-%dT%H:%M"
MODES = ("booking", "viewing")
COLOUR = re.compile(r"#[0-9a-fA-F]{6}")


class BookingError(ValueError):
    def __init__(self, message, status=400, **extra):
        super().__init__(message)
        self.status = status
        self.extra = extra


def enabled():
    return env_bool("BOOKINGS")


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def has_qualification(con, user_id, type_id):
    """True when the person holds a current approved qualification of this type."""
    today = datetime.date.today().isoformat()
    row = con.execute(
        "SELECT 1 FROM qualification_submissions WHERE user_id=? AND type_id=? AND status='approved'"
        " AND (valid_from IS NULL OR valid_from<=?) AND (valid_until IS NULL OR valid_until>=?)",
        (user_id, type_id, today, today)).fetchone()
    return row is not None


def granting_training(con, type_id):
    """Id of an active training that grants this qualification type, to link people to it."""
    row = con.execute("SELECT id FROM trainings WHERE type_id=? AND active=1 ORDER BY sort_order, id LIMIT 1",
                      (type_id,)).fetchone()
    return row["id"] if row else None


def access(con, item, user):
    """(can_view, can_book) for this person on this item."""
    qualified = not item["type_id"] or has_qualification(con, user["id"], item["type_id"])
    can_view = qualified or item["qualification_for"] == "booking" or bool(user["is_admin"])
    return can_view, qualified and bool(item["active"])


def clip(raw, max_len, label, required=True):
    text = str(raw or "").strip()
    if required and not text:
        raise BookingError(f"{label} is required.")
    if len(text) > max_len:
        raise BookingError(f"{label} can be at most {max_len} characters.")
    return text


def parse_time(raw, label):
    try:
        return datetime.datetime.strptime(str(raw or ""), TIME_FORMAT)
    except ValueError as exc:
        raise BookingError(f"{label} must be a date and time (YYYY-MM-DDTHH:MM).") from exc


def parse_colour(raw):
    if raw in (None, ""):
        return None
    if not isinstance(raw, str) or not COLOUR.fullmatch(raw):
        raise BookingError("A colour must look like #2da44e.")
    return raw.lower()


def parse_booking(body):
    description = clip(body.get("description"), MAX_DESCRIPTION, "A description")
    start = parse_time(body.get("start"), "Start")
    end = parse_time(body.get("end"), "End")
    if end <= start:
        raise BookingError("End must be after start.")
    if end - start > datetime.timedelta(days=MAX_DAYS):
        raise BookingError(f"A booking can last at most {MAX_DAYS} days.")
    flags = body.get("flags", [])
    if not isinstance(flags, list) or any(type(f) is not int for f in flags):
        raise BookingError("flags must be a list of flag ids.")
    return description, start.strftime(TIME_FORMAT), end.strftime(TIME_FORMAT), sorted(set(flags))


def load_flags(con, item_id):
    return [dict(r) for r in con.execute(
        "SELECT id, label, colour, blocks_overlap FROM booking_flags WHERE thing_id=? ORDER BY sort_order, id",
        (item_id,))]


def booking_flags(con, booking_id):
    return {r[0] for r in con.execute("SELECT flag_id FROM booking_flag_values WHERE booking_id=?", (booking_id,))}


def check_overlap(con, item, start, end, flags, ignore_id=None):
    """Bookings of one item overlap only with a shared non-blocking flag and no blocking flag on either."""
    all_flags = load_flags(con, item["id"])
    blocking = {f["id"] for f in all_flags if f["blocks_overlap"]}
    labels = {f["id"]: f["label"] for f in all_flags}
    mine = set(flags)
    for row in con.execute(
            "SELECT b.id, b.starts_at, b.ends_at, COALESCE(u.display_name,u.username) AS person FROM bookings b"
            " JOIN users u ON u.id=b.user_id WHERE b.thing_id=? AND b.starts_at<? AND b.ends_at>? AND b.id IS NOT ?",
            (item["id"], end, start, ignore_id)):
        theirs = booking_flags(con, row["id"])
        if not (mine & blocking or theirs & blocking) and (mine & theirs) - blocking:
            continue
        if theirs & blocking:
            reason = "marked " + ", ".join(labels[f] for f in sorted(theirs & blocking))
        elif mine & blocking:
            reason = "yours is marked " + ", ".join(labels[f] for f in sorted(mine & blocking))
        else:
            reason = "not shared"
        raise BookingError(f"{row['person']} has booked {item['name']} from {row['starts_at'].replace('T', ' ')}"
                           f" to {row['ends_at'].replace('T', ' ')} ({reason}).", 409)


def item_json(con, item, user):
    can_view, can_book = access(con, item, user)
    out = {
        "id": item["id"], "name": item["name"], "description": item["description"],
        "place_id": item["place_id"], "place_name": item["place_name"], "colour": item["colour"],
        "active": bool(item["active"]), "sort_order": item["sort_order"],
        "type_id": item["type_id"], "type_name": item["type_name"], "qualification_for": item["qualification_for"],
        "flags": [{**f, "blocks_overlap": bool(f["blocks_overlap"])} for f in load_flags(con, item["id"])],
        "can_view": can_view, "can_book": can_book,
    }
    if item["type_id"] and not has_qualification(con, user["id"], item["type_id"]):
        out["training_id"] = granting_training(con, item["type_id"])
    return out


def booking_json(row, flags, user):
    return {
        "id": row["id"], "thing_id": row["thing_id"], "user_id": row["user_id"], "person": row["person"],
        "description": row["description"], "start": row["starts_at"], "end": row["ends_at"], "flags": sorted(flags),
        "can_edit": row["user_id"] == user["id"] or bool(user["is_admin"]),
    }


ITEM_SQL = ("SELECT t.*, p.name AS place_name, q.name AS type_name FROM booking_things t"
            " LEFT JOIN places p ON p.id=t.place_id LEFT JOIN qualification_types q ON q.id=t.type_id")


def register(app, connection, require_session, pages_dir):
    def signed_in():
        require_session()
        if not enabled():
            abort(404)

    def admin():
        signed_in()
        if not g.user["is_admin"]:
            raise BookingError("Administrator access required.", 403)

    def item_row(cid, item_id):
        row = cid.execute(ITEM_SQL + " WHERE t.id=?", (item_id,)).fetchone()
        if row is None or (not row["active"] and not g.user["is_admin"]):
            raise BookingError("No such bookable item.", 404)
        return row

    def require(cid, item, booking=False):
        can_view, can_book = access(cid, item, g.user)
        if booking and not item["active"]:
            raise BookingError(f"{item['name']} is not open for bookings.", 409)
        if (booking and not can_book) or not can_view:
            verb = "Booking" if booking and can_view else "Viewing the agenda of"
            raise BookingError(f"{verb} {item['name']} needs a current {item['type_name']}.", 403,
                               thing_id=item["id"], qualification=item["type_name"],
                               training_id=granting_training(cid, item["type_id"]))

    def check_flags(cid, item_id, flags):
        if set(flags) - {f["id"] for f in load_flags(cid, item_id)}:
            raise BookingError("Unknown flag for this bookable item.")

    def save_flags(cid, booking_id, flags):
        cid.execute("DELETE FROM booking_flag_values WHERE booking_id=?", (booking_id,))
        cid.executemany("INSERT INTO booking_flag_values(booking_id,flag_id) VALUES (?,?)",
                        [(booking_id, f) for f in flags])

    def booking_row(cid, booking_id):
        row = cid.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
        if row is None:
            raise BookingError("No such booking.", 404)
        if row["user_id"] != g.user["id"] and not g.user["is_admin"]:
            raise BookingError("Only the person who booked and administrators can change this booking.", 403)
        return row

    @app.get("/agenda")
    def agenda_page():
        if not enabled():
            abort(404)
        return send_from_directory(pages_dir, "agenda.html")

    @app.get("/api/bookings")
    def list_bookings():
        signed_in()
        cid = connection()
        try:
            first = datetime.date.fromisoformat(request.args.get("from", ""))
            last = datetime.date.fromisoformat(request.args.get("to", ""))
        except ValueError as exc:
            raise BookingError("from and to must be dates (YYYY-MM-DD).") from exc
        if last < first or (last - first).days > 62:
            raise BookingError("Choose a range of at most two months.")
        items = [item_json(cid, t, g.user) for t in cid.execute(ITEM_SQL + " ORDER BY t.sort_order, t.name")
                 if t["active"] or g.user["is_admin"]]
        visible = [t["id"] for t in items if t["can_view"]]
        rows = cid.execute(
            "SELECT b.*, COALESCE(u.display_name,u.username) AS person FROM bookings b JOIN users u ON u.id=b.user_id"
            f" WHERE b.thing_id IN ({','.join('?' * len(visible))}) AND b.starts_at<? AND b.ends_at>?"
            " ORDER BY b.starts_at, b.id",
            (*visible, (last + datetime.timedelta(days=1)).isoformat(), first.isoformat())).fetchall() if visible else []
        out = {"things": items, "bookings": [booking_json(r, booking_flags(cid, r["id"]), g.user) for r in rows]}
        if g.user["is_admin"]:
            out["types"] = [dict(r) for r in cid.execute(
                "SELECT id, name FROM qualification_types WHERE active=1 ORDER BY sort_order, name")]
            out["places"] = [dict(r) for r in cid.execute("SELECT id, name FROM places ORDER BY name")]
        return jsonify(out)

    @app.post("/api/bookings")
    def add_booking():
        signed_in()
        cid = connection()
        body = request.get_json(silent=True) or {}
        description, start, end, flags = parse_booking(body)
        if type(body.get("thing_id")) is not int:
            raise BookingError("Choose what to book.")
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            item = item_row(cid, body["thing_id"])
            require(cid, item, booking=True)
            check_flags(cid, item["id"], flags)
            check_overlap(cid, item, start, end, flags)
            bid = cid.execute(
                "INSERT INTO bookings(thing_id,user_id,description,starts_at,ends_at,created_at) VALUES (?,?,?,?,?,?)",
                (item["id"], g.user["id"], description, start, end, utc_now())).lastrowid
            save_flags(cid, bid, flags)
        return jsonify(ok=True, id=bid), 201

    @app.put("/api/bookings/<int:booking_id>")
    def edit_booking(booking_id):
        signed_in()
        cid = connection()
        description, start, end, flags = parse_booking(request.get_json(silent=True) or {})
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            row = booking_row(cid, booking_id)
            item = item_row(cid, row["thing_id"])
            if row["user_id"] == g.user["id"]:
                require(cid, item, booking=True)
            check_flags(cid, item["id"], flags)
            check_overlap(cid, item, start, end, flags, ignore_id=booking_id)
            cid.execute("UPDATE bookings SET description=?, starts_at=?, ends_at=? WHERE id=?",
                        (description, start, end, booking_id))
            save_flags(cid, booking_id, flags)
        return jsonify(ok=True)

    @app.delete("/api/bookings/<int:booking_id>")
    def delete_booking(booking_id):
        signed_in()
        cid = connection()
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            row = booking_row(cid, booking_id)
            require(cid, item_row(cid, row["thing_id"]))
            cid.execute("DELETE FROM bookings WHERE id=?", (booking_id,))
        return jsonify(ok=True)

    def item_fields(cid, body):
        name = clip(body.get("name"), 80, "A name")
        description = clip(body.get("description"), 500, "The description", required=False)
        place_id, type_id = body.get("place_id"), body.get("type_id")
        for value, table, label in ((place_id, "places", "place"), (type_id, "qualification_types", "qualification")):
            if value is not None and (type(value) is not int
                                      or not cid.execute(f"SELECT 1 FROM {table} WHERE id=?", (value,)).fetchone()):
                raise BookingError(f"No such {label}.")
        mode = body.get("qualification_for", "booking")
        if mode not in MODES:
            raise BookingError("qualification_for must be booking or viewing.")
        active, order = body.get("active", True), body.get("sort_order", 0)
        if type(active) is not bool or type(order) is not int:
            raise BookingError("active must be true or false and sort_order a whole number.")
        flags = body.get("flags", [])
        if not isinstance(flags, list) or len(flags) > MAX_FLAGS:
            raise BookingError(f"Give at most {MAX_FLAGS} flags.")
        parsed = []
        for f in flags:
            if not isinstance(f, dict) or type(f.get("blocks_overlap", False)) is not bool:
                raise BookingError("Each flag needs a label and blocks_overlap true or false.")
            if f.get("id") is not None and type(f.get("id")) is not int:
                raise BookingError("Unknown flag.")
            parsed.append((f.get("id"), clip(f.get("label"), 40, "A flag label"), parse_colour(f.get("colour")),
                           f.get("blocks_overlap", False)))
        return (name, description, place_id, type_id, mode, parse_colour(body.get("colour")), int(active), order), parsed

    def save_item_flags(cid, item_id, flags):
        existing = {f["id"] for f in load_flags(cid, item_id)}
        kept = {fid for fid, *_ in flags if fid is not None}
        if kept - existing:
            raise BookingError("Unknown flag.")
        for fid in existing - kept:
            cid.execute("DELETE FROM booking_flags WHERE id=?", (fid,))
        for i, (fid, label, colour, blocks) in enumerate(flags):
            if fid is None:
                cid.execute("INSERT INTO booking_flags(thing_id,label,colour,blocks_overlap,sort_order)"
                            " VALUES (?,?,?,?,?)", (item_id, label, colour, int(blocks), i))
            else:
                cid.execute("UPDATE booking_flags SET label=?, colour=?, blocks_overlap=?, sort_order=? WHERE id=?",
                            (label, colour, int(blocks), i, fid))

    @app.post("/api/admin/booking-things")
    def add_booking_thing():
        admin()
        cid = connection()
        fields, flags = item_fields(cid, request.get_json(silent=True) or {})
        try:
            with db.write_lock, cid:
                cid.execute("BEGIN IMMEDIATE")
                tid = cid.execute(
                    "INSERT INTO booking_things(name,description,place_id,type_id,qualification_for,colour,active,"
                    "sort_order) VALUES (?,?,?,?,?,?,?,?)", fields).lastrowid
                save_item_flags(cid, tid, flags)
        except sqlite3.IntegrityError as exc:
            raise BookingError("A bookable item with that name already exists.", 409) from exc
        return jsonify(ok=True, id=tid), 201

    @app.put("/api/admin/booking-things/<int:thing_id>")
    def edit_booking_thing(thing_id):
        admin()
        cid = connection()
        fields, flags = item_fields(cid, request.get_json(silent=True) or {})
        try:
            with db.write_lock, cid:
                cid.execute("BEGIN IMMEDIATE")
                item_row(cid, thing_id)
                cid.execute("UPDATE booking_things SET name=?, description=?, place_id=?, type_id=?,"
                            " qualification_for=?, colour=?, active=?, sort_order=? WHERE id=?", (*fields, thing_id))
                save_item_flags(cid, thing_id, flags)
        except sqlite3.IntegrityError as exc:
            raise BookingError("A bookable item with that name already exists.", 409) from exc
        return jsonify(ok=True)

    @app.delete("/api/admin/booking-things/<int:thing_id>")
    def delete_booking_thing(thing_id):
        admin()
        cid = connection()
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            item_row(cid, thing_id)
            if cid.execute("SELECT 1 FROM bookings WHERE thing_id=?", (thing_id,)).fetchone():
                raise BookingError("This item has bookings; make it inactive instead.", 409)
            cid.execute("DELETE FROM booking_things WHERE id=?", (thing_id,))
        return jsonify(ok=True)

    @app.errorhandler(BookingError)
    def booking_error(exc):
        return jsonify(error=str(exc), **exc.extra), exc.status
