"""Safety: danger, incident and accident reports (occurrence reporting). Off unless LOGDB_SAFETY=1.

The page lives at /safety (the Operations Manual links that path) and is called Safety in the menu. It shows the
text set by administrators (who to contact, where else to report; a general default until they do) and a prominent
Report incident button that opens the report form with the fields of a paper registration form: type (the definitions
are behind a ? next to it), date and time and description, and optionally pilot in command, aircraft, location,
conditions, flight phase and a sketch as PDF/JPEG/PNG. There is no list of reports on the page: each report
is stored (never deleted) and mailed in full to the administrators (mail kind "safety", immediate by default,
separate from the other administrator notices; to LOGDB_MAIL_ADMIN_TO when set), with a link to the sketch. The
reporter can ask to leave their name out of that email (leave_name_out); the name is still stored with the report,
as it was at filing time. Every route needs a browser session; API keys get 403.
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
KINDS = ("danger", "incident", "accident")
SKETCH_TYPES = {".pdf": ("application/pdf", b"%PDF-"), ".jpg": ("image/jpeg", b"\xff\xd8\xff"),
                ".jpeg": ("image/jpeg", b"\xff\xd8\xff"), ".png": ("image/png", b"\x89PNG\r\n\x1a\n")}
SETTINGS = ("reporting_text",)
# Shown above Report incident until administrators set their own text (an empty setting means this one).
DEFAULT_REPORTING_TEXT = ("Seen something unsafe, or had a near miss, incident or accident? Report it here, also when "
                          "nothing went wrong in the end: every report helps prevent the next one. In an emergency, "
                          "call the emergency services first.")
NAME_SQL = "COALESCE(NULLIF(display_name,''), username)"


class SafetyError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def enabled():
    return env_bool("SAFETY")


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


def settings(cid):
    row = cid.execute("SELECT * FROM safety_settings WHERE id=1").fetchone()
    return {key: (row[key] if row else "") for key in SETTINGS}


NOTICE_FIELDS = (("Type", "kind"), ("Date and time", "occurred_at"), ("Location", "location"),
                 ("Pilot in command", "pic"), ("Aircraft", "rpas"), ("Flight phase", "flight_phase"),
                 ("Conditions", "conditions"))


def notice_text(rid, fields, reporter, sketch_name):
    """The administrators' mail: the whole report, since the page has no list of reports."""
    lines = [f"A new {fields['kind']} report (number {rid}) was filed by "
             + (reporter or "a reporter who asked to leave their name out") + ".", ""]
    lines += [f"{label}: {fields[key].replace('T', ' ') if key == 'occurred_at' else fields[key]}"
              for label, key in NOTICE_FIELDS if fields[key]]
    lines += ["", "Description:", fields["description"]]
    if sketch_name:
        lines += ["", f"Sketch or photo: {sketch_name} (open the link below while signed in)"]
    return "\n".join(lines)


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
        out = {"reporting_text": current["reporting_text"] or DEFAULT_REPORTING_TEXT, "is_admin": is_admin, "kinds": list(KINDS),
               "max_sketch_bytes": MAX_SKETCH_BYTES,
               "me": cid.execute(f"SELECT {NAME_SQL} FROM users WHERE id=?", (g.user["id"],)).fetchone()[0]}
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
                    values[key] = text(body[key], "The text", MAX_TEXT, multiline=True)
            if values["reporting_text"] == DEFAULT_REPORTING_TEXT:  # kept empty, so a later default applies
                values["reporting_text"] = ""
            cid.execute("INSERT INTO safety_settings(id,reporting_text) VALUES (1,?)"
                        " ON CONFLICT(id) DO UPDATE SET reporting_text=excluded.reporting_text",
                        (values["reporting_text"],))
        return jsonify(ok=True)

    @app.post("/api/safety/reports")
    def create_report():
        signed_in()
        form = request.form
        fields = dict(
            kind=choice(form.get("kind"), KINDS, "Type"),
            pic=text(form.get("pic"), "Pilot in command"),
            rpas=text(form.get("rpas"), "Aircraft"),
            location=text(form.get("location"), "Location"),
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
                    "leave_name_out,reporter_id,reporter_name,sketch_name,sketch_mime,sketch_path,"
                    "sketch_compressed,sketch_bytes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (*fields.values(), int(leave_name_out), g.user["id"], name, sketch_name, sketch_mime,
                     stored.name if stored else "", int(compressed), size, now)).lastrowid
                at = f" at {fields['location']}" if fields["location"] else ""  # the location is optional
                mail.notify_admins(cid, f"Safety report: {fields['kind']}{at}",
                                   notice_text(rid, fields, None if leave_name_out else name, sketch_name),
                                   f"/api/safety/reports/{rid}/sketch" if sketch_name else None, kind="safety")
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
        if problem := storage.stored_file_problem(path):
            app.logger.error("Missing sketch file for safety report %s (%s): %s", report_id, path, problem)
            raise SafetyError(storage.UNAVAILABLE, 503)
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

    @app.errorhandler(SafetyError)
    def safety_error(exc):
        return jsonify(error=str(exc)), exc.status
