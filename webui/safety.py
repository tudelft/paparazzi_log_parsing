"""Safety: occurrence reporting, safety board meetings and safety promotion. Off unless LOGDB_SAFETY=1.

The page (/safety) has three tabs:
  - Reporting (everyone signed in): a danger, incident or accident report with the fields of a paper registration
    form (pilot in command, aircraft, location, date and time, conditions, flight phase, description, optional
    sketch as PDF/JPEG/PNG). People see their own reports and their status; administrators see all of them, set the
    status, a reference to where the investigation is kept (for example a repository with restricted access) and an
    internal note, and get a notice of each new report (mail kind "safety", immediate by default, separate from
    the other administrator notices). The reporter can ask to leave their name out of that notification email
    (leave_name_out); administrators still see who reported (open reporting culture). The reporter's name is
    stored at filing time, so a report keeps it when the account is deleted. The reporter gets a mail when the
    status changes (kind "safety_status"). Reports cannot be deleted.
  - Safety board (administrators): a schedule of safety board meetings with an attendance list per meeting, a text
    and a link to where the meeting documents are kept.
  - Safety promotion (administrators): the safety promotion programme (a text) and the safety meetings held, with
    their attendance lists.
Texts and the documents link are set by administrators in Edit mode and stored in safety_settings, so nothing about
one organisation is in the code. Attendance lists hold logbook people (by id, with their name at the time) and
names of people without an account. Every route needs a browser session; API keys get 403.
"""
import datetime
import gzip
import re
import uuid
from urllib.parse import quote
from pathlib import Path

from flask import Response, abort, g, jsonify, request, send_from_directory
from werkzeug.http import dump_options_header

import db
import mail
import storage
from config import env_bool

MAX_SKETCH_BYTES = 20 * 2**20
MAX_TEXT = 5000
MAX_FIELD = 200
MAX_ATTENDEES = 200
KINDS = ("danger", "incident", "accident")
STATUSES = ("new", "investigating", "closed")
STATUS_LABELS = {"new": "New", "investigating": "Investigating", "closed": "Closed"}
MEETING_KINDS = ("board", "promotion")
SKETCH_TYPES = {".pdf": ("application/pdf", b"%PDF-"), ".jpg": ("image/jpeg", b"\xff\xd8\xff"),
                ".jpeg": ("image/jpeg", b"\xff\xd8\xff"), ".png": ("image/png", b"\x89PNG\r\n\x1a\n")}
SETTINGS = ("reporting_text", "board_text", "board_link", "promotion_text")
NAME_SQL = "COALESCE(NULLIF(display_name,''), username)"


class SafetyError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def enabled():
    return env_bool("SAFETY")


def new_count(cid):
    """Reports with status New, for the count on the Safety menu tab (administrators)."""
    return cid.execute("SELECT COUNT(*) FROM safety_reports WHERE status='new'").fetchone()[0]


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


LINE_BREAKS = re.compile(r"[\x00-\x1f\x7f\u2028\u2029]")  # control characters, also line and paragraph breaks
TEXT_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # a multi-line text keeps tabs and line breaks


def text(raw, label, max_len=MAX_FIELD, required=False, multiline=False):
    """A text field. A single-line field (the default) refuses control characters, so a value cannot add lines to
    a mail subject or header; a multi-line one only allows tabs and line breaks."""
    value = str(raw if raw is not None else "").strip()
    if required and not value:
        raise SafetyError(f"{label} is required.")
    if len(value) > max_len:
        raise SafetyError(f"{label} can be at most {max_len} characters.")
    if (TEXT_CONTROL if multiline else LINE_BREAKS).search(value):
        raise SafetyError(f"{label} cannot contain control characters" + ("." if multiline else " or line breaks."))
    return value


def choice(raw, allowed, label):
    value = str(raw or "").strip().lower()
    if value not in allowed:
        raise SafetyError(f"{label} must be one of: {', '.join(allowed)}.")
    return value


def date_value(raw, label, required=True):
    value = str(raw or "").strip()
    if not value:
        if required:
            raise SafetyError(f"{label} is required.")
        return None
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError:
        raise SafetyError(f"{label} must be a date (YYYY-MM-DD).") from None


def datetime_value(raw, label):
    """YYYY-MM-DDTHH:MM (local time, as a datetime-local field sends it) or a date alone; not in the future. The
    browser's time zone is unknown, so a date up to the latest date anywhere on earth now (UTC+14) is accepted."""
    value = str(raw or "").strip()
    if not value:
        raise SafetyError(f"{label} is required.")
    try:
        if "T" in value:
            value = datetime.datetime.fromisoformat(value).strftime("%Y-%m-%dT%H:%M")
        else:
            value = datetime.date.fromisoformat(value).isoformat()
    except ValueError:
        raise SafetyError(f"{label} must be a date and time (YYYY-MM-DDTHH:MM).") from None
    latest = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=14))).strftime("%Y-%m-%dT%H:%M")
    if value[:len(latest)] > latest[:len(value)]:
        raise SafetyError(f"{label} cannot be in the future.")
    return value


def time_value(raw, label):
    value = str(raw or "").strip()
    if not value:
        return ""
    try:
        return datetime.time.fromisoformat(value).strftime("%H:%M")
    except ValueError:
        raise SafetyError(f"{label} must be a time (HH:MM).") from None


def link_value(raw):
    value = text(raw, "The documents link", 500)
    if value and not value.lower().startswith(("https://", "http://")):
        raise SafetyError("The documents link must start with https:// or http://.")
    return value


def settings(cid):
    row = cid.execute("SELECT * FROM safety_settings WHERE id=1").fetchone()
    return {key: (row[key] if row else "") for key in SETTINGS}


def report_json(row, admin):
    out = {key: row[key] for key in ("id", "kind", "pic", "rpas", "location", "occurred_at", "conditions",
                                     "flight_phase", "description", "status", "created_at", "updated_at")}
    out["leave_name_out"] = bool(row["leave_name_out"])
    out["sketch"] = row["sketch_name"] or None
    out["mine"] = row["reporter_id"] == g.user["id"]
    if admin:  # the account's current name; the name at filing time when the account was deleted
        out.update(reporter_id=row["reporter_id"], reporter=row["reporter"] or row["reporter_name"] or None,
                   reporter_deleted=row["reporter_id"] is None, reference=row["reference"],
                   admin_note=row["admin_note"])
    return out


def meeting_json(cid, row):
    attendees = [{"user_id": a["user_id"], "name": a["name"]} for a in cid.execute(
        "SELECT user_id, name FROM safety_meeting_attendees WHERE meeting_id=? ORDER BY id", (row["id"],))]
    out = {key: row[key] for key in ("id", "kind", "held_on", "start_time", "location", "title", "notes")}
    out["attendees"] = attendees
    return out


def attendee_rows(cid, raw):
    """[(user_id or None, name)] from [{user_id} | {name}]; logbook people keep their current name."""
    if not isinstance(raw, list):
        raise SafetyError("Attendees must be a list.")
    if len(raw) > MAX_ATTENDEES:
        raise SafetyError(f"At most {MAX_ATTENDEES} attendees.")
    rows, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise SafetyError("Each attendee is a person or a name.")
        if item.get("user_id") not in (None, ""):
            try:
                user_id = int(item["user_id"])
            except (TypeError, ValueError):
                raise SafetyError("No such person.") from None
            person = cid.execute(f"SELECT {NAME_SQL} AS name FROM users WHERE id=?", (user_id,)).fetchone()
            if person is None:
                raise SafetyError("No such person.")
            key, entry = ("user", user_id), (user_id, person["name"])
        else:
            name = text(item.get("name"), "An attendee's name", 120, required=True)
            key, entry = ("name", name.casefold()), (None, name)
        if key not in seen:
            seen.add(key)
            rows.append(entry)
    return rows


def register(app, connection, root, pages_dir, require_session):
    app.config["SAFETY_DIR"] = Path(root) / "safety"

    def folder():
        return Path(app.config["SAFETY_DIR"])

    def signed_in():
        if not enabled():
            abort(404)
        require_session()

    def admin():
        signed_in()
        if not g.user["is_admin"]:
            raise SafetyError("Administrator access required.", 403)

    def report_row(cid, report_id):
        row = cid.execute(f"SELECT r.*, (SELECT {NAME_SQL} FROM users u WHERE u.id=r.reporter_id) AS reporter"
                          " FROM safety_reports r WHERE r.id=?", (report_id,)).fetchone()
        if row is None or not (g.user["is_admin"] or row["reporter_id"] == g.user["id"]):
            raise SafetyError("No such report.", 404)
        return row

    def meeting_row(cid, meeting_id):
        row = cid.execute("SELECT * FROM safety_meetings WHERE id=?", (meeting_id,)).fetchone()
        if row is None:
            raise SafetyError("No such meeting.", 404)
        return row

    @app.get("/safety")
    def safety_page():
        if not enabled():
            abort(404)
        return send_from_directory(pages_dir, "safety.html")

    @app.get("/api/safety")
    def safety_overview():
        signed_in()
        cid = connection()
        current = settings(cid)
        is_admin = bool(g.user["is_admin"])
        out = {"reporting_text": current["reporting_text"], "is_admin": is_admin, "kinds": list(KINDS),
               "statuses": list(STATUSES), "max_sketch_bytes": MAX_SKETCH_BYTES,
               "me": cid.execute(f"SELECT {NAME_SQL} FROM users WHERE id=?", (g.user["id"],)).fetchone()[0]}
        if is_admin:
            out.update({key: current[key] for key in ("board_text", "board_link", "promotion_text")})
        return jsonify(out)

    @app.put("/api/admin/safety/settings")
    def save_safety_settings():
        admin()
        body = request.get_json(silent=True) or {}
        cid = connection()
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            values = settings(cid)
            for key in SETTINGS:
                if key in body:
                    values[key] = link_value(body[key]) if key == "board_link" else text(body[key], "The text", MAX_TEXT, multiline=True)
            cid.execute("INSERT INTO safety_settings(id,reporting_text,board_text,board_link,promotion_text)"
                        " VALUES (1,?,?,?,?) ON CONFLICT(id) DO UPDATE SET reporting_text=excluded.reporting_text,"
                        " board_text=excluded.board_text, board_link=excluded.board_link,"
                        " promotion_text=excluded.promotion_text", tuple(values[k] for k in SETTINGS))
        return jsonify(ok=True)

    # --- reports -----------------------------------------------------------------------------------------------
    @app.get("/api/safety/reports")
    def list_reports():
        signed_in()
        cid = connection()
        is_admin = bool(g.user["is_admin"])
        sql = (f"SELECT r.*, (SELECT {NAME_SQL} FROM users u WHERE u.id=r.reporter_id) AS reporter"
               " FROM safety_reports r")
        params = ()
        if not is_admin:
            sql += " WHERE r.reporter_id=?"
            params = (g.user["id"],)
        rows = cid.execute(sql + " ORDER BY r.created_at DESC, r.id DESC", params).fetchall()
        return jsonify(reports=[report_json(r, is_admin) for r in rows])

    @app.post("/api/safety/reports")
    def create_report():
        signed_in()
        form = request.form
        fields = dict(
            kind=choice(form.get("kind"), KINDS, "Type"),
            pic=text(form.get("pic"), "Pilot in command"),
            rpas=text(form.get("rpas"), "Aircraft"),
            location=text(form.get("location"), "Location", required=True),
            occurred_at=datetime_value(form.get("occurred_at"), "Date and time"),
            conditions=text(form.get("conditions"), "Conditions", 1000),
            flight_phase=text(form.get("flight_phase"), "Flight phase"),
            description=text(form.get("description"), "Description", MAX_TEXT, required=True, multiline=True),
        )
        leave_name_out = str(form.get("leave_name_out", "")).lower() in ("1", "true", "on", "yes")
        uploaded = request.files.get("sketch")
        stored = None
        sketch_name, sketch_mime, compressed, size = "", "", 0, 0
        if uploaded and uploaded.filename:
            name = Path(uploaded.filename).name[:160]
            kind = SKETCH_TYPES.get(Path(name).suffix.lower())
            if kind is None:
                raise SafetyError("The sketch must be a PDF, JPEG or PNG.")
            folder().mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                staged, compressed, size, _, _ = storage.save_attachment(uploaded.stream, folder(), MAX_SKETCH_BYTES)
            except ValueError:
                raise SafetyError("The sketch must be non-empty and at most 20 MiB.") from None
            stored = folder() / (uuid.uuid4().hex + (".gz" if compressed else ""))
            staged.rename(stored)
            with (gzip.open(stored, "rb") if compressed else open(stored, "rb")) as f:
                head = f.read(8)
            if not head.startswith(kind[1]):
                stored.unlink(missing_ok=True)
                raise SafetyError("The sketch must be a PDF, JPEG or PNG with matching file content.")
            sketch_name, sketch_mime = name, kind[0]
        cid = connection()
        try:
            with db.write_lock, cid:
                cid.execute("BEGIN IMMEDIATE")
                now = utc_now()
                name = cid.execute(f"SELECT {NAME_SQL} FROM users WHERE id=?", (g.user["id"],)).fetchone()[0]
                rid = cid.execute(
                    "INSERT INTO safety_reports(kind,pic,rpas,location,occurred_at,conditions,flight_phase,description,"
                    "leave_name_out,reporter_id,reporter_name,status,sketch_name,sketch_mime,sketch_path,"
                    "sketch_compressed,sketch_bytes,created_at,updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,'new',?,?,?,?,?,?,?)",
                    (*fields.values(), int(leave_name_out), g.user["id"], name, sketch_name, sketch_mime,
                     stored.name if stored else "", int(compressed), size, now, now)).lastrowid
                who = "a reporter who asked to leave their name out" if leave_name_out else name
                mail.notify_admins(cid, f"Safety report: {fields['kind']} at {fields['location']}",
                                   f"A new {fields['kind']} report ({fields['occurred_at'].replace('T', ' ')}, "
                                   f"{fields['location']}) was filed by {who}.", "/safety?tab=reporting",
                                   kind="safety")
        except Exception:
            if stored:
                stored.unlink(missing_ok=True)
            raise
        return jsonify(ok=True, id=rid), 201

    @app.get("/api/safety/reports/<int:report_id>/sketch")
    def report_sketch(report_id):
        signed_in()
        row = report_row(connection(), report_id)
        if not row["sketch_path"]:
            raise SafetyError("This report has no sketch.", 404)
        path = folder() / row["sketch_path"]
        if not path.is_file():
            app.logger.error("Missing sketch file for safety report %s", report_id)
            raise SafetyError("The file is unavailable. Contact an administrator.", 503)
        name = row["sketch_name"]  # as attachments.py: an ASCII fallback, and the full name in filename*
        disposition = dump_options_header("inline", {"filename": name.encode("ascii", "ignore").decode() or "sketch"}) \
            + "; filename*=UTF-8''" + quote(name, safe="")
        headers = {"Content-Disposition": disposition,
                   "Content-Length": str(row["sketch_bytes"]), "Cache-Control": "private, no-store",
                   "X-Content-Type-Options": "nosniff"}
        if row["sketch_mime"] != "application/pdf":
            headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self'"
        return Response(storage.chunks(path, bool(row["sketch_compressed"])), mimetype=row["sketch_mime"],
                        headers=headers)

    @app.put("/api/admin/safety/reports/<int:report_id>")
    def update_report(report_id):
        admin()
        body = request.get_json(silent=True) or {}
        cid = connection()
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            row = report_row(cid, report_id)
            status = choice(body.get("status", row["status"]), STATUSES, "Status")
            reference = text(body.get("reference", row["reference"]), "The reference", 500)
            note = text(body.get("admin_note", row["admin_note"]), "The note", MAX_TEXT, multiline=True)
            cid.execute("UPDATE safety_reports SET status=?, reference=?, admin_note=?, updated_at=? WHERE id=?",
                        (status, reference, note, utc_now(), report_id))
            if status != row["status"] and row["reporter_id"] not in (None, g.user["id"]):
                mail.notify(cid, row["reporter_id"], "safety_status",
                            f"Your safety report: {STATUS_LABELS[status].lower()}",
                            f"The status of your {row['kind']} report ({row['occurred_at'].replace('T', ' ')}, "
                            f"{row['location']}) is now {STATUS_LABELS[status]}.", "/safety?tab=reporting")
        return jsonify(ok=True)

    # --- meetings ----------------------------------------------------------------------------------------------
    @app.get("/api/admin/safety/meetings")
    def list_meetings():
        admin()
        cid = connection()
        meetings = [meeting_json(cid, r) for r in cid.execute(
            "SELECT * FROM safety_meetings ORDER BY held_on DESC, start_time DESC, id DESC")]
        people = [dict(r) for r in cid.execute(
            f"SELECT id, {NAME_SQL} AS name FROM users WHERE is_active=1 ORDER BY {NAME_SQL} COLLATE NOCASE")]
        return jsonify(meetings=meetings, people=people, today=datetime.date.today().isoformat())

    def meeting_fields(cid, body, row=None):
        get = (lambda k: body[k] if k in body else (row[k] if row is not None else None))
        fields = dict(
            kind=choice(get("kind"), MEETING_KINDS, "Meeting type"),
            held_on=date_value(get("held_on"), "Date"),
            start_time=time_value(get("start_time"), "Time"),
            location=text(get("location"), "Location"),
            title=text(get("title"), "Topic"),
            notes=text(get("notes"), "Notes", MAX_TEXT, multiline=True),
        )
        attendees = attendee_rows(cid, body["attendees"]) if "attendees" in body else None
        return fields, attendees

    def save_attendees(cid, meeting_id, attendees):
        if attendees is None:
            return
        cid.execute("DELETE FROM safety_meeting_attendees WHERE meeting_id=?", (meeting_id,))
        cid.executemany("INSERT INTO safety_meeting_attendees(meeting_id,user_id,name) VALUES (?,?,?)",
                        [(meeting_id, user_id, name) for user_id, name in attendees])

    @app.post("/api/admin/safety/meetings")
    def create_meeting():
        admin()
        body = request.get_json(silent=True) or {}
        cid = connection()
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            fields, attendees = meeting_fields(cid, body)
            mid = cid.execute("INSERT INTO safety_meetings(kind,held_on,start_time,location,title,notes,created_by,"
                              "created_at) VALUES (?,?,?,?,?,?,?,?)",
                              (*fields.values(), g.user["id"], utc_now())).lastrowid
            save_attendees(cid, mid, attendees)
        return jsonify(ok=True, id=mid), 201

    @app.put("/api/admin/safety/meetings/<int:meeting_id>")
    def update_meeting(meeting_id):
        admin()
        body = request.get_json(silent=True) or {}
        cid = connection()
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            row = meeting_row(cid, meeting_id)
            fields, attendees = meeting_fields(cid, body, row)
            cid.execute("UPDATE safety_meetings SET kind=?, held_on=?, start_time=?, location=?, title=?, notes=?"
                        " WHERE id=?", (*fields.values(), meeting_id))
            save_attendees(cid, meeting_id, attendees)
        return jsonify(ok=True)

    @app.delete("/api/admin/safety/meetings/<int:meeting_id>")
    def delete_meeting(meeting_id):
        admin()
        cid = connection()
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            meeting_row(cid, meeting_id)
            cid.execute("DELETE FROM safety_meetings WHERE id=?", (meeting_id,))
        return jsonify(ok=True)

    @app.errorhandler(SafetyError)
    def safety_error(exc):
        return jsonify(error=str(exc)), exc.status
