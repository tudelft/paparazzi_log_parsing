"""Cyberzoo agenda: bookings of the indoor flight arena. Off unless LOGDB_CYBERZOO_QUALIFICATION is set.

Seeing and booking the agenda needs a current approved qualification of the type named by
LOGDB_CYBERZOO_QUALIFICATION (e.g. the Cyberzoo Safety Briefing); administrators may always view it.
Without it the API answers 403 with the id of an active training that grants the qualification.
Times are local wall-clock times of the arena (YYYY-MM-DDTHH:MM).
Bookings may overlap only when both allow others and neither is a demo.
The booker and administrators can change or delete a booking.
"""
import datetime
from pathlib import Path

from flask import abort, g, jsonify, request, send_from_directory

import db
from config import env

MAX_DESCRIPTION = 500
MAX_DAYS = 7
TIME_FORMAT = "%Y-%m-%dT%H:%M"


class CyberzooError(ValueError):
    def __init__(self, message, status=400, **extra):
        super().__init__(message)
        self.status = status
        self.extra = extra


def qualification_name():
    return (env("CYBERZOO_QUALIFICATION") or "").strip()


def enabled():
    return bool(qualification_name())


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def has_qualification(con, user_id):
    """True when the person holds a current approved qualification of the configured type."""
    today = datetime.date.today().isoformat()
    row = con.execute(
        "SELECT 1 FROM qualification_submissions q JOIN qualification_types t ON t.id=q.type_id"
        " WHERE q.user_id=? AND t.name=? COLLATE NOCASE AND q.status='approved'"
        " AND (q.valid_from IS NULL OR q.valid_from<=?) AND (q.valid_until IS NULL OR q.valid_until>=?)",
        (user_id, qualification_name(), today, today)).fetchone()
    return row is not None


def granting_training(con):
    """Id of an active training that grants the configured qualification, to link people to it."""
    row = con.execute(
        "SELECT tr.id FROM trainings tr JOIN qualification_types t ON t.id=tr.type_id"
        " WHERE t.name=? COLLATE NOCASE AND tr.active=1 ORDER BY tr.sort_order, tr.id LIMIT 1",
        (qualification_name(),)).fetchone()
    return row["id"] if row else None


def parse_time(raw, label):
    try:
        return datetime.datetime.strptime(str(raw or ""), TIME_FORMAT)
    except ValueError as exc:
        raise CyberzooError(f"{label} must be a date and time (YYYY-MM-DDTHH:MM).") from exc


def parse_flag(body, key, default):
    value = body.get(key, default)
    if type(value) is not bool:
        raise CyberzooError(f"{key} must be true or false.")
    return value


def parse_booking(body):
    description = str(body.get("description") or "").strip()
    if not description:
        raise CyberzooError("Describe the test.")
    if len(description) > MAX_DESCRIPTION:
        raise CyberzooError(f"The description can be at most {MAX_DESCRIPTION} characters.")
    start = parse_time(body.get("start"), "Start")
    end = parse_time(body.get("end"), "End")
    if end <= start:
        raise CyberzooError("End must be after start.")
    if end - start > datetime.timedelta(days=MAX_DAYS):
        raise CyberzooError(f"A booking can last at most {MAX_DAYS} days.")
    others = parse_flag(body, "others_allowed", True)
    demo = parse_flag(body, "demo", False)
    return description, start.strftime(TIME_FORMAT), end.strftime(TIME_FORMAT), others, demo


def check_overlap(con, start, end, others, demo, ignore_id=None):
    """Overlap is allowed only when both bookings allow others and neither is a demo."""
    for row in con.execute(
            "SELECT b.id, b.others_allowed, b.demo, b.starts_at, b.ends_at, COALESCE(u.display_name,u.username) AS person"
            " FROM cyberzoo_bookings b JOIN users u ON u.id=b.user_id"
            " WHERE b.starts_at<? AND b.ends_at>? AND b.id IS NOT ?", (end, start, ignore_id)):
        if others and not demo and row["others_allowed"] and not row["demo"]:
            continue
        if row["demo"] or not row["others_allowed"]:
            reason = "a demo" if row["demo"] else "others not allowed"
            raise CyberzooError(f"{row['person']} has booked the Cyberzoo from {row['starts_at'].replace('T', ' ')}"
                                f" to {row['ends_at'].replace('T', ' ')} ({reason}).", 409)
        raise CyberzooError("This overlaps a booking that allows others: to share the slot, allow others"
                            " and do not mark it as a demo.", 409)


def booking_json(row, user):
    return {
        "id": row["id"], "user_id": row["user_id"], "person": row["person"], "description": row["description"],
        "start": row["starts_at"], "end": row["ends_at"], "others_allowed": bool(row["others_allowed"]),
        "demo": bool(row["demo"]), "can_edit": row["user_id"] == user["id"] or bool(user["is_admin"]),
    }


def register(app, connection, require_session):
    def signed_in():
        require_session()
        if not enabled():
            abort(404)

    def booking_row(cid, booking_id):
        row = cid.execute("SELECT * FROM cyberzoo_bookings WHERE id=?", (booking_id,)).fetchone()
        if row is None:
            raise CyberzooError("No such booking.", 404)
        if row["user_id"] != g.user["id"] and not g.user["is_admin"]:
            raise CyberzooError("Only the person who booked and administrators can change this booking.", 403)
        return row

    def require_qualified(cid, viewing=False):
        if viewing and g.user["is_admin"]:
            return
        if not has_qualification(cid, g.user["id"]):
            raise CyberzooError(f"You can use the Cyberzoo agenda once you have a current {qualification_name()}.",
                                403, qualification=qualification_name(), training_id=granting_training(cid))

    @app.get("/cyberzoo")
    def cyberzoo_page():
        if not enabled():
            abort(404)
        return send_from_directory(Path(__file__).parent / "static", "cyberzoo.html")

    @app.get("/api/cyberzoo/bookings")
    def list_cyberzoo_bookings():
        signed_in()
        cid = connection()
        require_qualified(cid, viewing=True)
        try:
            first = datetime.date.fromisoformat(request.args.get("from", ""))
            last = datetime.date.fromisoformat(request.args.get("to", ""))
        except ValueError as exc:
            raise CyberzooError("from and to must be dates (YYYY-MM-DD).") from exc
        if last < first or (last - first).days > 62:
            raise CyberzooError("Choose a range of at most two months.")
        rows = cid.execute(
            "SELECT b.*, COALESCE(u.display_name,u.username) AS person FROM cyberzoo_bookings b"
            " JOIN users u ON u.id=b.user_id WHERE b.starts_at<? AND b.ends_at>? ORDER BY b.starts_at, b.id",
            ((last + datetime.timedelta(days=1)).isoformat(), first.isoformat())).fetchall()
        return jsonify(bookings=[booking_json(r, g.user) for r in rows],
                       can_book=has_qualification(cid, g.user["id"]), qualification=qualification_name())

    @app.post("/api/cyberzoo/bookings")
    def add_cyberzoo_booking():
        signed_in()
        cid = connection()
        description, start, end, others, demo = parse_booking(request.get_json(silent=True) or {})
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            require_qualified(cid)
            check_overlap(cid, start, end, others, demo)
            bid = cid.execute(
                "INSERT INTO cyberzoo_bookings(user_id,description,starts_at,ends_at,others_allowed,demo,created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (g.user["id"], description, start, end, int(others), int(demo), utc_now())).lastrowid
        return jsonify(ok=True, id=bid), 201

    @app.put("/api/cyberzoo/bookings/<int:booking_id>")
    def edit_cyberzoo_booking(booking_id):
        signed_in()
        cid = connection()
        description, start, end, others, demo = parse_booking(request.get_json(silent=True) or {})
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            row = booking_row(cid, booking_id)
            if row["user_id"] == g.user["id"]:
                require_qualified(cid)
            check_overlap(cid, start, end, others, demo, ignore_id=booking_id)
            cid.execute("UPDATE cyberzoo_bookings SET description=?, starts_at=?, ends_at=?, others_allowed=?, demo=?"
                        " WHERE id=?", (description, start, end, int(others), int(demo), booking_id))
        return jsonify(ok=True)

    @app.delete("/api/cyberzoo/bookings/<int:booking_id>")
    def delete_cyberzoo_booking(booking_id):
        signed_in()
        cid = connection()
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            booking_row(cid, booking_id)
            cid.execute("DELETE FROM cyberzoo_bookings WHERE id=?", (booking_id,))
        return jsonify(ok=True)

    @app.errorhandler(CyberzooError)
    def cyberzoo_error(exc):
        return jsonify(error=str(exc), **exc.extra), exc.status
