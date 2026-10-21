"""Private qualification submissions, evidence and administrative approval."""
import datetime
import gzip
import json
import os
import re
import sqlite3
import uuid
from pathlib import Path

from flask import Response, g, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

import db
import storage

MAX_EVIDENCE_BYTES = 20 * 1024 * 1024
_SKIP_LOG_NAMES = {"unknown", "n/a", "na", "none", "tbd", "-", "--", "?", "??"}


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def user_keys(con, user_id=None):
    names = {}
    for user in con.execute("SELECT id,username,display_name FROM users" + (" WHERE id=?" if user_id else ""),
                            (user_id,) if user_id else ()):
        names.setdefault(user["username"].casefold(), user["id"])
        if user["display_name"]:
            names.setdefault(user["display_name"].casefold(), user["id"])
    for row in con.execute("SELECT user_id,alias FROM user_aliases" + (" WHERE user_id=?" if user_id else ""),
                           (user_id,) if user_id else ()):
        names[row["alias"].casefold()] = row["user_id"]
    return names


def can_see_quals(con, viewer, subject_id):
    return bool(viewer["is_admin"] or viewer["id"] == subject_id)


def directory_user_ids(con, viewer):
    if viewer["is_admin"]:
        return {r[0] for r in con.execute("SELECT id FROM users WHERE is_active=1")}
    return {viewer["id"]} | {r[0] for r in con.execute(
        "SELECT s.user_id FROM project_members m JOIN project_members s ON s.project_id=m.project_id"
        " WHERE m.user_id=?", (viewer["id"],))}


def username_from_name(name):
    return re.sub(r"[^A-Za-z0-9]+", "", name or "")[:40] or "pilot"


def unique_username(con, base):
    name, n = (base or "pilot")[:40], 2
    while con.execute("SELECT 1 FROM users WHERE username=?", (name,)).fetchone():
        suffix = str(n)
        name = base[:40 - len(suffix)] + suffix
        n += 1
    return name


def add_alias(con, user_id, alias):
    alias = (alias or "").strip()
    if not alias:
        return False
    try:
        con.execute("INSERT INTO user_aliases(user_id,alias) VALUES (?,?)", (user_id, alias))
        return True
    except sqlite3.IntegrityError:
        return False


def looks_like_person_name(name):
    name = (name or "").strip()
    return len(name) >= 2 and name.casefold() not in _SKIP_LOG_NAMES and any(c.isalpha() for c in name)


def ensure_users_for_log_names(con):
    keys, created = user_keys(con), 0
    for aircraft in con.execute("SELECT pic,pac,gcs FROM aircraft"):
        for role in ("pic", "pac", "gcs"):
            name = (aircraft[role] or "").strip()
            if not looks_like_person_name(name) or name.casefold() in keys:
                continue
            username = unique_username(con, username_from_name(name))
            uid = con.execute(
                "INSERT INTO users(username,display_name,password_hash,is_admin,provider,created_at)"
                " VALUES (?,?,NULL,0,'log',?)", (username, name[:80], _utc_now())).lastrowid
            add_alias(con, uid, name)
            keys[name.casefold()] = uid
            keys[username.casefold()] = uid
            created += 1
    return created


def qualifications_payload(con, viewer, user_id):
    if not can_see_quals(con, viewer, user_id):
        return None
    items = []
    for row in con.execute(
            "SELECT q.*,t.name AS category,t.requires_evidence,"
            " COALESCE(u.display_name,u.username) AS reviewer FROM qualification_submissions q"
            " JOIN qualification_types t ON t.id=q.type_id LEFT JOIN users u ON u.id=q.reviewed_by"
            " WHERE q.user_id=? ORDER BY q.id DESC", (user_id,)):
        item = {key: row[key] for key in (
            "id", "type_id", "category", "status", "valid_until", "submitted_at",
            "reviewed_at", "reviewer", "evidence_deleted_at", "filename", "requires_evidence")}
        item["has_evidence"] = bool(row["evidence_path"])
        item["expired"] = bool(row["valid_until"] and row["valid_until"] < datetime.date.today().isoformat())
        items.append(item)
    return dict(items=items, types=[dict(r) for r in con.execute(
        "SELECT * FROM qualification_types ORDER BY name")], can_manage=bool(viewer["is_admin"]),
        max_evidence_bytes=MAX_EVIDENCE_BYTES)


def current_payload(con, viewer, user_id):
    if not can_see_quals(con, viewer, user_id):
        return None
    return dict(items=[
        dict(category=r["category"], valid_until=r["valid_until"], status="approved",
             expired=bool(r["valid_until"] and r["valid_until"] < datetime.date.today().isoformat()))
        for r in con.execute(
            "SELECT t.name AS category,q.valid_until FROM qualification_submissions q"
            " JOIN qualification_types t ON t.id=q.type_id WHERE q.user_id=? AND q.status='approved'"
            " ORDER BY t.name", (user_id,))])


class QualificationError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def audit(con, subject_id, actor_id, action, details):
    con.execute(
        "INSERT INTO qualification_audit(subject_id,actor_id,action,details_json,created_at) VALUES (?,?,?,?,?)",
        (subject_id, actor_id, action, json.dumps(details), _utc_now()))


def expiry(raw):
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise QualificationError("Expiry must be YYYY-MM-DD or empty.")
    try:
        if datetime.date.fromisoformat(raw).isoformat() != raw:
            raise ValueError
    except ValueError as exc:
        raise QualificationError("Expiry must be a valid YYYY-MM-DD date.") from exc
    return raw


def integer(raw):
    if isinstance(raw, bool) or not isinstance(raw, (str, int)) or not str(raw).isascii() or not str(raw).isdigit():
        raise QualificationError("Choose a valid qualification type.")
    if len(str(raw)) > 10 or int(raw) <= 0:
        raise QualificationError("Choose a valid qualification type.")
    return int(raw)


def private_subject(con, user, uid):
    if not can_see_quals(con, user, uid) or not con.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
        raise QualificationError("No such person.", 404)


def visible_submission(con, user, sid):
    row = con.execute(
        "SELECT q.*,t.name AS category,t.requires_evidence,t.requires_expiry,t.active FROM qualification_submissions q"
        " JOIN qualification_types t ON t.id=q.type_id WHERE q.id=?", (sid,)).fetchone()
    if row is None or not can_see_quals(con, user, row["user_id"]):
        raise QualificationError("No such qualification.", 404)
    return row


def evidence_format(path, compressed, filename):
    with (gzip.open(path, "rb") if compressed else open(path, "rb")) as source:
        head = source.read(16)
    formats = {".pdf": ("application/pdf", head.startswith(b"%PDF-")),
               ".jpg": ("image/jpeg", head.startswith(b"\xff\xd8\xff")),
               ".jpeg": ("image/jpeg", head.startswith(b"\xff\xd8\xff")),
               ".png": ("image/png", head.startswith(b"\x89PNG\r\n\x1a\n"))}
    mime, matches = formats.get(Path(filename).suffix.lower(), ("", False))
    if not matches:
        raise QualificationError("Evidence must be a PDF, JPEG or PNG with matching file content.")
    return mime


def register(app, connection, root, require_session):
    app.config["QUALIFICATION_EVIDENCE_DIR"] = Path(root) / "qualification-evidence"

    @app.get("/qualifications.js")
    def qualifications_script():
        return send_from_directory(Path(__file__).parent / "static", "qualifications.js")

    @app.get("/api/admin/qualification-types")
    def list_qualification_types():
        admin()
        return jsonify(types=[dict(r) for r in connection().execute("SELECT * FROM qualification_types ORDER BY name")])

    @app.get("/api/admin/qualification-pending")
    def qualification_pending():
        admin()
        rows = connection().execute(
            "SELECT q.id,q.user_id,t.name AS category,q.valid_until,q.submitted_at,"
            " COALESCE(u.display_name,u.username) AS person FROM qualification_submissions q"
            " JOIN users u ON u.id=q.user_id JOIN qualification_types t ON t.id=q.type_id"
            " WHERE q.status='pending' ORDER BY q.id").fetchall()
        return jsonify(submissions=[dict(r) for r in rows])

    @app.errorhandler(QualificationError)
    def qualification_error(exc):
        return jsonify(error=str(exc)), exc.status

    def admin():
        require_session()
        if not g.user["is_admin"]:
            raise QualificationError("Administrator access required.", 403)

    def evidence_root():
        return Path(app.config["QUALIFICATION_EVIDENCE_DIR"])

    def json_body(allowed):
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) - set(allowed):
            raise QualificationError("Invalid qualification request.")
        return body

    @app.post("/api/qualification-types")
    @app.put("/api/qualification-types/<int:type_id>")
    def save_qualification_type(type_id=None):
        admin()
        body = json_body(("name", "requires_evidence", "requires_expiry", "active"))
        name = body.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise QualificationError("Type name must be 1-80 characters.")
        if (type(body.get("requires_evidence")) is not bool
                or type(body.get("requires_expiry", False)) is not bool
                or type(body.get("active", True)) is not bool):
            raise QualificationError("Evidence requirement, expiry requirement and active must be true or false.")
        con = connection()
        try:
            with db.write_lock, con:
                con.execute("BEGIN IMMEDIATE")
                if type_id is not None:
                    existing = con.execute("SELECT * FROM qualification_types WHERE id=?", (type_id,)).fetchone()
                    if existing is None:
                        raise QualificationError("No such qualification type.", 404)
                    requires_expiry = body.get("requires_expiry", bool(existing["requires_expiry"]))
                    con.execute("UPDATE qualification_types SET name=?,requires_evidence=?,requires_expiry=?,active=? WHERE id=?",
                                (name.strip(), body["requires_evidence"], requires_expiry, body.get("active", True), type_id))
                else:
                    requires_expiry = body.get("requires_expiry", False)
                    type_id = con.execute("INSERT INTO qualification_types(name,requires_evidence,requires_expiry,active)"
                                          " VALUES (?,?,?,?)", (name.strip(), body["requires_evidence"], requires_expiry,
                                                              body.get("active", True))).lastrowid
                audit(con, None, g.user["id"], "type-updated",
                      dict(type_id=type_id, name=name.strip(), requires_evidence=body["requires_evidence"],
                           requires_expiry=requires_expiry, active=body.get("active", True)))
        except sqlite3.IntegrityError as exc:
            if "qualification_types.name" not in str(exc):
                raise
            raise QualificationError("A type with that name already exists.", 409) from exc
        return jsonify(ok=True, id=type_id)

    @app.get("/api/users/<int:user_id>/qualifications")
    def list_qualifications(user_id):
        con = connection()
        private_subject(con, g.user, user_id)
        return jsonify(qualifications_payload(con, g.user, user_id))

    @app.post("/api/users/<int:user_id>/qualifications")
    def submit_qualification(user_id):
        require_session()
        con = connection()
        private_subject(con, g.user, user_id)
        request.max_content_length = MAX_EVIDENCE_BYTES + 1024 * 1024
        if set(request.form) - {"type_id", "valid_until"} or set(request.files) - {"evidence"}:
            raise QualificationError("Send only a type, expiry and optional evidence.")
        if len(request.files.getlist("evidence")) > 1:
            raise QualificationError("Attach one PDF, JPEG or PNG per submission.")
        tid, until = integer(request.form.get("type_id")), expiry(request.form.get("valid_until"))
        uploaded = request.files.get("evidence")
        staged = destination = None
        committed = False
        filename = mime = None
        compressed, size = False, 0
        try:
            if uploaded and uploaded.filename:
                filename = secure_filename(uploaded.filename)[:160]
                if not filename:
                    raise QualificationError("Choose an evidence filename.")
                folder = evidence_root()
                folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                try:
                    staged, compressed, size, _, _ = storage.save_attachment(uploaded.stream, folder, MAX_EVIDENCE_BYTES)
                except ValueError as exc:
                    raise QualificationError("Evidence must be non-empty and at most 20 MiB.") from exc
                mime = evidence_format(staged, compressed, filename)
            with db.write_lock, con:
                con.execute("BEGIN IMMEDIATE")
                private_subject(con, g.user, user_id)
                kind = con.execute("SELECT * FROM qualification_types WHERE id=? AND active=1", (tid,)).fetchone()
                if kind is None:
                    raise QualificationError("Choose an active qualification type.")
                if kind["requires_evidence"] and staged is None:
                    raise QualificationError("This qualification requires evidence.")
                if kind["requires_expiry"] and until is None:
                    raise QualificationError("This qualification requires an expiry date.")
                if con.execute("SELECT 1 FROM qualification_submissions WHERE user_id=? AND type_id=? AND status='pending'",
                               (user_id, tid)).fetchone():
                    raise QualificationError("There is already a pending submission for this type. Withdraw it first.", 409)
                if staged:
                    destination = evidence_root() / (uuid.uuid4().hex + (".gz" if compressed else ".bin"))
                    os.replace(staged, destination)
                sid = con.execute(
                    "INSERT INTO qualification_submissions(user_id,type_id,valid_until,status,submitted_by,submitted_at,"
                    " evidence_path,filename,mime,compressed,size_bytes) VALUES (?,?,?,'pending',?,?,?,?,?,?,?)",
                    (user_id, tid, until, g.user["id"], _utc_now(), destination.name if destination else None,
                     filename, mime, int(compressed), size)).lastrowid
                audit(con, user_id, g.user["id"], "submitted",
                      dict(id=sid, category=kind["name"], valid_until=until, has_evidence=bool(destination)))
            committed = True
            return jsonify(ok=True, id=sid), 201
        finally:
            if staged:
                staged.unlink(missing_ok=True)
            if destination and not committed:
                destination.unlink(missing_ok=True)

    @app.post("/api/qualification-submissions/<int:sid>/review")
    def review_qualification(sid):
        require_session()
        body = json_body(("action",))
        action = body.get("action")
        if action not in ("approve", "reject", "withdraw"):
            raise QualificationError("Choose approve, reject or withdraw.")
        con = connection()
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            row = visible_submission(con, g.user, sid)
            if action != "withdraw" and not g.user["is_admin"]:
                raise QualificationError("Administrator approval required.", 403)
            if row["status"] != "pending":
                raise QualificationError("Only pending submissions can be reviewed or withdrawn.", 409)
            if action == "approve":
                if not row["active"]:
                    raise QualificationError("This qualification type is disabled.")
                if row["requires_expiry"] and not row["valid_until"]:
                    raise QualificationError("An expiry date is required before approval. Withdraw and resubmit with an expiry date.")
                if row["requires_evidence"] and (
                        not row["evidence_path"] or not (evidence_root() / row["evidence_path"]).is_file()):
                    raise QualificationError("Evidence must be available before approval.")
                con.execute("UPDATE qualification_submissions SET status='superseded'"
                            " WHERE user_id=? AND type_id=? AND status='approved'", (row["user_id"], row["type_id"]))
            state = {"approve": "approved", "reject": "rejected", "withdraw": "withdrawn"}[action]
            con.execute("UPDATE qualification_submissions SET status=?,reviewed_by=?,reviewed_at=? WHERE id=?",
                        (state, g.user["id"], _utc_now(), sid))
            audit(con, row["user_id"], g.user["id"], state,
                  dict(id=sid, category=row["category"], valid_until=row["valid_until"]))
        return jsonify(ok=True)

    @app.get("/api/qualification-submissions/<int:sid>/evidence")
    def download_qualification_evidence(sid):
        row = visible_submission(connection(), g.user, sid)
        if not row["evidence_path"]:
            raise QualificationError("No evidence file is available.", 404)
        path = evidence_root() / row["evidence_path"]
        if not path.is_file():
            app.logger.error("Missing private evidence for qualification submission %s", sid)
            raise QualificationError("Evidence is unavailable. Contact an administrator.", 503)
        return Response(storage.chunks(path, bool(row["compressed"])),
                        mimetype=row["mime"], headers={
                            "Content-Disposition": f'attachment; filename="{row["filename"]}"',
                            "Content-Length": str(row["size_bytes"]), "Cache-Control": "private, no-store",
                            "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'"})

    @app.delete("/api/qualification-submissions/<int:sid>/evidence")
    @app.delete("/api/qualification-submissions/<int:sid>")
    def delete_qualification(sid):
        admin()
        con = connection()
        evidence_only = request.path.endswith("/evidence")
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            row = visible_submission(con, g.user, sid)
            if row["evidence_path"]:
                (evidence_root() / row["evidence_path"]).unlink(missing_ok=True)
            if evidence_only:
                con.execute("UPDATE qualification_submissions SET evidence_path=NULL,filename=NULL,mime=NULL,"
                            " evidence_deleted_at=? WHERE id=?", (_utc_now(), sid))
            else:
                con.execute("DELETE FROM qualification_submissions WHERE id=?", (sid,))
            audit(con, row["user_id"], g.user["id"], "evidence-deleted" if evidence_only else "deleted",
                  dict(id=sid, category=row["category"], valid_until=row["valid_until"], status=row["status"]))
        return jsonify(ok=True)

    @app.get("/api/users/<int:user_id>/qualification-audit")
    def qualification_history(user_id):
        admin()
        con = connection()
        private_subject(con, g.user, user_id)
        rows = con.execute(
            "SELECT a.action,a.created_at,a.details_json,COALESCE(u.display_name,u.username) AS actor"
            " FROM qualification_audit a LEFT JOIN users u ON u.id=a.actor_id"
            " WHERE a.subject_id=? ORDER BY a.id DESC LIMIT 100", (user_id,)).fetchall()
        return jsonify(events=[dict(r) for r in rows])
