"""Private qualification submissions, evidence and administrative approval."""
import datetime
import gzip
import json
import os
import sqlite3
import uuid
from pathlib import Path

from flask import Response, g, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

import db
import qualification_import
import storage

MAX_EVIDENCE_BYTES = 20 * 1024 * 1024


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


def crew_keys(con):
    """Explicit aliases override unique display names; login usernames are not implicit aliases."""
    names = {}
    for user in con.execute("SELECT id,display_name FROM users WHERE display_name IS NOT NULL"):
        name = user["display_name"].strip().casefold()
        if name:
            names[name] = None if name in names else user["id"]
    for row in con.execute("SELECT user_id,alias FROM user_aliases"):
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


def qualifications_payload(con, viewer, user_id):
    if not can_see_quals(con, viewer, user_id):
        return None
    items = []
    for row in con.execute(
            "SELECT q.*,t.name AS category,t.requires_evidence,t.requires_start,t.requires_expiry,t.internal,"
            " COALESCE(u.display_name,u.username) AS reviewer FROM qualification_submissions q"
            " JOIN qualification_types t ON t.id=q.type_id LEFT JOIN users u ON u.id=q.reviewed_by"
            " WHERE q.user_id=? ORDER BY q.id DESC", (user_id,)):
        item = {key: row[key] for key in (
            "id", "type_id", "category", "status", "valid_from", "valid_until", "submitted_at",
            "reviewed_at", "reviewer", "evidence_deleted_at", "filename", "requires_evidence",
            "requires_start", "requires_expiry")}
        item["internal"] = bool(row["internal"])
        item["has_evidence"] = bool(row["evidence_path"])
        item["expired"] = bool(row["valid_until"] and row["valid_until"] < datetime.date.today().isoformat())
        items.append(item)
    return dict(items=items, types=[dict(r) for r in con.execute(
        "SELECT * FROM qualification_types ORDER BY sort_order, name")], can_manage=bool(viewer["is_admin"]),
        max_evidence_bytes=MAX_EVIDENCE_BYTES)


def current_payload(con, viewer, user_id):
    if not can_see_quals(con, viewer, user_id):
        return None
    return dict(items=[
        dict(id=r["id"], has_evidence=bool(r["evidence_path"]),
             category=r["category"], valid_from=r["valid_from"], valid_until=r["valid_until"], status="approved",
             expired=bool(r["valid_until"] and r["valid_until"] < datetime.date.today().isoformat()),
             internal=bool(r["internal"]))
        for r in con.execute(
            "SELECT q.id,q.evidence_path,t.name AS category,q.valid_from,q.valid_until,t.internal FROM qualification_submissions q"
            " JOIN qualification_types t ON t.id=q.type_id WHERE q.user_id=? AND q.status='approved'"
            " ORDER BY t.sort_order, t.name", (user_id,))])


class QualificationError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def audit(con, subject_id, actor_id, action, details):
    con.execute(
        "INSERT INTO qualification_audit(subject_id,actor_id,action,details_json,created_at) VALUES (?,?,?,?,?)",
        (subject_id, actor_id, action, json.dumps(details), _utc_now()))


def expiry(raw, label="Valid until"):
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise QualificationError(f"{label} must be YYYY-MM-DD or empty.")
    try:
        if datetime.date.fromisoformat(raw).isoformat() != raw:
            raise ValueError
    except ValueError as exc:
        raise QualificationError(f"{label} must be a valid YYYY-MM-DD date.") from exc
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
        "SELECT q.*,t.name AS category,t.requires_evidence,t.requires_expiry,t.requires_start,t.active FROM qualification_submissions q"
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
    app.config.setdefault("QUALIFICATION_IMPORT_DIR", Path(root).parent / "tmp" / "qual-import")

    @app.get("/qualifications.js")
    def qualifications_script():
        return send_from_directory(Path(__file__).parent / "static", "qualifications.js")

    @app.get("/api/admin/qualification-types")
    def list_qualification_types():
        admin()
        return jsonify(types=[dict(r) for r in connection().execute(
            "SELECT * FROM qualification_types ORDER BY sort_order, name")])

    @app.put("/api/admin/qualification-types")
    def save_qualification_types():
        admin()
        body = request.get_json(silent=True)
        allowed = {"id", "name", "requires_evidence", "requires_expiry", "requires_start", "internal", "active"}
        if not isinstance(body, dict) or set(body) - {"types"} or not isinstance(body.get("types"), list):
            raise QualificationError("Invalid qualification request.")
        parsed = []
        seen_names, seen_ids = set(), set()
        for raw in body["types"]:
            if not isinstance(raw, dict) or set(raw) - allowed:
                raise QualificationError("Invalid qualification request.")
            name = raw.get("name")
            if isinstance(name, str) and not name.strip() and raw.get("id") is None:
                continue
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                raise QualificationError("Type name must be 1-80 characters.")
            name = name.strip()
            if name.casefold() in seen_names:
                raise QualificationError("A type with that name already exists.", 409)
            seen_names.add(name.casefold())
            tid = raw.get("id")
            if tid is not None:
                if type(tid) is not int or isinstance(tid, bool) or tid <= 0:
                    raise QualificationError("Choose a valid qualification type.")
                if tid in seen_ids:
                    raise QualificationError("Invalid qualification request.")
                seen_ids.add(tid)
            if (type(raw.get("requires_evidence")) is not bool
                    or type(raw.get("requires_expiry", False)) is not bool
                    or type(raw.get("requires_start", False)) is not bool
                    or type(raw.get("internal", False)) is not bool
                    or type(raw.get("active", True)) is not bool):
                raise QualificationError(
                    "Evidence requirement, date requirements, internal and active must be true or false.")
            parsed.append(dict(
                id=tid, name=name, requires_evidence=raw["requires_evidence"],
                requires_expiry=raw.get("requires_expiry", True), requires_start=raw.get("requires_start", True),
                internal=raw.get("internal", False), active=raw.get("active", True)))
        con = connection()
        try:
            with db.write_lock, con:
                con.execute("BEGIN IMMEDIATE")
                existing = {r["id"]: r for r in con.execute("SELECT * FROM qualification_types")}
                for item in parsed:
                    if item["id"] is not None and item["id"] not in existing:
                        raise QualificationError("No such qualification type.", 404)
                for order, item in enumerate(parsed, 1):
                    if item["id"] is None:
                        item["id"] = con.execute(
                            "INSERT INTO qualification_types(name,requires_evidence,requires_expiry,requires_start,"
                            "internal,sort_order,active) VALUES (?,?,?,?,?,?,?)",
                            (item["name"], item["requires_evidence"], item["requires_expiry"], item["requires_start"],
                             item["internal"], order, item["active"])).lastrowid
                    else:
                        con.execute(
                            "UPDATE qualification_types SET name=?,requires_evidence=?,requires_expiry=?,"
                            "requires_start=?,internal=?,sort_order=?,active=? WHERE id=?",
                            (item["name"], item["requires_evidence"], item["requires_expiry"], item["requires_start"],
                             item["internal"], order, item["active"], item["id"]))
                leftover = [rid for rid in existing if rid not in {item["id"] for item in parsed}]
                leftover.sort(key=lambda rid: (existing[rid]["sort_order"], existing[rid]["name"].casefold()))
                for offset, rid in enumerate(leftover, len(parsed) + 1):
                    con.execute("UPDATE qualification_types SET sort_order=? WHERE id=?", (offset, rid))
                audit(con, None, g.user["id"], "types-updated", dict(types=[
                    {key: item[key] for key in (
                        "id", "name", "requires_evidence", "requires_expiry", "requires_start", "internal", "active")}
                    for item in parsed]))
        except sqlite3.IntegrityError as exc:
            if "qualification_types.name" not in str(exc):
                raise
            raise QualificationError("A type with that name already exists.", 409) from exc
        return jsonify(ok=True, ids=[item["id"] for item in parsed])

    @app.get("/api/admin/qualification-pending")
    def qualification_pending():
        admin()
        rows = connection().execute(
            "SELECT q.id,q.user_id,q.type_id,t.name AS category,t.requires_start,t.requires_expiry,"
            " q.valid_from,q.valid_until,q.submitted_at,"
            " COALESCE(u.display_name,u.username) AS person FROM qualification_submissions q"
            " JOIN users u ON u.id=q.user_id JOIN qualification_types t ON t.id=q.type_id"
            " WHERE q.status='pending' ORDER BY q.id").fetchall()
        return jsonify(submissions=[dict(r) for r in rows])

    @app.get("/api/admin/qualifications/import/template")
    def qualification_import_template():
        admin()
        return Response(qualification_import.TEMPLATE, mimetype="text/csv", headers={
            "Content-Disposition": 'attachment; filename="qualification-import.csv"',
            "Cache-Control": "private, no-store"})

    @app.post("/api/admin/qualifications/import")
    def qualification_import_preview():
        admin()
        request.max_content_length = qualification_import.MAX_UPLOAD_BYTES + 1024 * 1024
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            raise QualificationError("Choose a CSV or a ZIP that contains manifest.csv.")
        raw = upload.stream.read(qualification_import.MAX_UPLOAD_BYTES + 1)
        if len(raw) > qualification_import.MAX_UPLOAD_BYTES:
            raise QualificationError("Import must be at most 200 MiB.")
        return jsonify(qualification_import.store_preview(app, connection(), upload.filename, raw))

    @app.post("/api/admin/qualifications/import/commit")
    def qualification_import_commit():
        admin()
        body = json_body(("token",))
        token = body.get("token")
        if not isinstance(token, str) or not token:
            raise QualificationError("Upload the CSV or ZIP again to preview it.")
        return jsonify(qualification_import.commit(app, connection(), token)), 201

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
        body = json_body(("name", "requires_evidence", "requires_expiry", "requires_start", "internal", "active"))
        name = body.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise QualificationError("Type name must be 1-80 characters.")
        if (type(body.get("requires_evidence")) is not bool
                or type(body.get("requires_expiry", False)) is not bool
                or type(body.get("requires_start", False)) is not bool
                or type(body.get("internal", False)) is not bool
                or type(body.get("active", True)) is not bool):
            raise QualificationError("Evidence requirement, date requirements, internal and active must be true or false.")
        con = connection()
        try:
            with db.write_lock, con:
                con.execute("BEGIN IMMEDIATE")
                if type_id is not None:
                    existing = con.execute("SELECT * FROM qualification_types WHERE id=?", (type_id,)).fetchone()
                    if existing is None:
                        raise QualificationError("No such qualification type.", 404)
                    requires_expiry = body.get("requires_expiry", bool(existing["requires_expiry"]))
                    requires_start = body.get("requires_start", bool(existing["requires_start"]))
                    internal = body.get("internal", bool(existing["internal"]))
                    con.execute("UPDATE qualification_types SET name=?,requires_evidence=?,requires_expiry=?,"
                                "requires_start=?,internal=?,active=? WHERE id=?",
                                (name.strip(), body["requires_evidence"], requires_expiry, requires_start,
                                 internal, body.get("active", True), type_id))
                else:
                    requires_expiry = body.get("requires_expiry", True)
                    requires_start = body.get("requires_start", True)
                    internal = body.get("internal", False)
                    nxt = con.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM qualification_types").fetchone()[0]
                    type_id = con.execute(
                        "INSERT INTO qualification_types(name,requires_evidence,requires_expiry,requires_start,"
                        "internal,sort_order,active) VALUES (?,?,?,?,?,?,?)",
                        (name.strip(), body["requires_evidence"], requires_expiry, requires_start, internal, nxt,
                         body.get("active", True))).lastrowid
                audit(con, None, g.user["id"], "type-updated",
                      dict(type_id=type_id, name=name.strip(), requires_evidence=body["requires_evidence"],
                           requires_expiry=requires_expiry, requires_start=requires_start, internal=internal,
                           active=body.get("active", True)))
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
        if set(request.form) - {"type_id", "valid_from", "valid_until"} or set(request.files) - {"evidence"}:
            raise QualificationError("Send only a type, validity dates and optional evidence.")
        if len(request.files.getlist("evidence")) > 1:
            raise QualificationError("Attach one PDF, JPEG or PNG per submission.")
        tid, until = integer(request.form.get("type_id")), expiry(request.form.get("valid_until"))
        start = expiry(request.form.get("valid_from"), "Valid from")
        if start and until and start > until:
            raise QualificationError("Valid from must be on or before Valid until.")
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
                if kind["requires_start"] and start is None:
                    raise QualificationError("This qualification requires a Valid from date.")
                if con.execute("SELECT 1 FROM qualification_submissions WHERE user_id=? AND type_id=? AND status='pending'",
                               (user_id, tid)).fetchone():
                    raise QualificationError("There is already a pending submission for this type. Withdraw it first.", 409)
                if staged:
                    destination = evidence_root() / (uuid.uuid4().hex + (".gz" if compressed else ".bin"))
                    os.replace(staged, destination)
                sid = con.execute(
                    "INSERT INTO qualification_submissions(user_id,type_id,valid_from,valid_until,status,submitted_by,submitted_at,"
                    " evidence_path,filename,mime,compressed,size_bytes) VALUES (?,?,?,?,'pending',?,?,?,?,?,?,?)",
                    (user_id, tid, start, until, g.user["id"], _utc_now(), destination.name if destination else None,
                     filename, mime, int(compressed), size)).lastrowid
                audit(con, user_id, g.user["id"], "submitted",
                      dict(id=sid, category=kind["name"], valid_from=start, valid_until=until, has_evidence=bool(destination)))
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
                if row["requires_start"] and not row["valid_from"]:
                    raise QualificationError("A Valid from date is required before approval. Withdraw and resubmit with a Valid from date.")
                if row["requires_evidence"] and (
                        not row["evidence_path"] or not (evidence_root() / row["evidence_path"]).is_file()):
                    raise QualificationError("Evidence must be available before approval.")
                con.execute("UPDATE qualification_submissions SET status='superseded'"
                            " WHERE user_id=? AND type_id=? AND status='approved'", (row["user_id"], row["type_id"]))
            state = {"approve": "approved", "reject": "rejected", "withdraw": "withdrawn"}[action]
            con.execute("UPDATE qualification_submissions SET status=?,reviewed_by=?,reviewed_at=? WHERE id=?",
                        (state, g.user["id"], _utc_now(), sid))
            audit(con, row["user_id"], g.user["id"], state,
                  dict(id=sid, category=row["category"], valid_from=row["valid_from"], valid_until=row["valid_until"]))
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
                  dict(id=sid, category=row["category"], valid_from=row["valid_from"],
                       valid_until=row["valid_until"], status=row["status"]))
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
