"""Documents: a page of shared documents and the person's own qualifications. Off unless LOGDB_DOCUMENTS=1.

Administrators set it up in Edit mode on the page: a text at the top, groups (one level, each with a short text,
in a set order) and documents (title, description, file, optional group). A document may require a qualification
type: only people with a current approved qualification of that type see it; administrators see every document.
Below the shared documents, every signed-in person sees their own current approved qualifications, with links to
the evidence file when there is one, and a link to My account to change them.
"""
import datetime
import os
import sqlite3
import uuid
from pathlib import Path

from flask import Response, abort, g, jsonify, request, send_from_directory

import db
import storage
from bookings import has_qualification
from config import env_bool

MAX_BYTES = 100 * 2**20
MAX_INTRO = 5000
MAX_TEXT = 1000


class DocumentError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def enabled():
    return env_bool("DOCUMENTS")


def clip(raw, max_len, label, required=True):
    text = str(raw or "").strip()
    if required and not text:
        raise DocumentError(f"{label} is required.")
    if len(text) > max_len:
        raise DocumentError(f"{label} can be at most {max_len} characters.")
    return text


def optional_id(cid, value, table, label):
    if value in (None, ""):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise DocumentError(f"No such {label}.") from None
    if not cid.execute(f"SELECT 1 FROM {table} WHERE id=?", (value,)).fetchone():
        raise DocumentError(f"No such {label}.")
    return value


def can_see(cid, row, user):
    return bool(user["is_admin"]) or not row["type_id"] or has_qualification(cid, user["id"], row["type_id"])


def document_json(row):
    return {
        "id": row["id"], "group_id": row["group_id"], "title": row["title"], "description": row["description"],
        "type_id": row["type_id"], "type_name": row["type_name"], "filename": row["filename"],
        "size_bytes": row["size_bytes"], "uploaded_at": row["uploaded_at"], "sort_order": row["sort_order"],
        "viewable": storage.view_type(row["filename"]) is not None,
        "view": f"/api/documents/{row['id']}/view", "download": f"/api/documents/{row['id']}/download",
    }


def own_qualifications(cid, user_id):
    """The person's approved qualifications that are valid today, with evidence links when there is a file."""
    today = datetime.date.today().isoformat()
    out = []
    for r in cid.execute(
            "SELECT q.id,t.name AS type,q.number,q.valid_from,q.valid_until,q.filename,q.evidence_path,"
            " q.evidence_deleted_at FROM qualification_submissions q JOIN qualification_types t ON t.id=q.type_id"
            " WHERE q.user_id=? AND q.status='approved' AND (q.valid_until IS NULL OR q.valid_until>=?)"
            " AND (q.valid_from IS NULL OR q.valid_from<=?) ORDER BY t.sort_order, t.name",
            (user_id, today, today)):
        item = {"id": r["id"], "type": r["type"], "number": r["number"], "valid_until": r["valid_until"]}
        if r["evidence_path"] and not r["evidence_deleted_at"]:
            item["download"] = f"/api/qualification-submissions/{r['id']}/evidence"
            if storage.view_type(r["filename"] or ""):
                item["view"] = f"/api/qualification-submissions/{r['id']}/evidence/view"
        out.append(item)
    return out


DOCUMENT_SQL = ("SELECT d.*, t.name AS type_name FROM documents d"
                " LEFT JOIN qualification_types t ON t.id=d.type_id")


def register(app, connection, root, require_session, pages_dir):
    app.config["DOCUMENTS_DIR"] = Path(root) / "documents"

    def folder():
        return Path(app.config["DOCUMENTS_DIR"])

    def signed_in():
        require_session()
        if not enabled():
            abort(404)

    def admin():
        signed_in()
        if not g.user["is_admin"]:
            raise DocumentError("Administrator access required.", 403)

    def document_row(cid, doc_id):
        row = cid.execute(DOCUMENT_SQL + " WHERE d.id=?", (doc_id,)).fetchone()
        if row is None or not can_see(cid, row, g.user):
            raise DocumentError("No such document.", 404)
        return row

    @app.get("/documents")
    def documents_page():
        if not enabled():
            abort(404)
        return send_from_directory(pages_dir, "documents.html")

    @app.get("/api/documents")
    def list_documents():
        signed_in()
        cid = connection()
        intro = cid.execute("SELECT intro FROM document_settings WHERE id=1").fetchone()
        docs = [document_json(r) for r in cid.execute(DOCUMENT_SQL + " ORDER BY d.sort_order, d.title")
                if can_see(cid, r, g.user)]
        groups = [dict(r) for r in cid.execute(
            "SELECT id, name, description, sort_order FROM document_groups ORDER BY sort_order, name")]
        out = {"intro": intro["intro"] if intro else "", "groups": groups, "documents": docs,
               "qualifications": own_qualifications(cid, g.user["id"]), "can_edit": bool(g.user["is_admin"])}
        if g.user["is_admin"]:
            out["types"] = [dict(r) for r in cid.execute(
                "SELECT id, name FROM qualification_types WHERE active=1 ORDER BY sort_order, name")]
            out["max_bytes"] = MAX_BYTES
        return jsonify(out)

    def send(doc_id, inline):
        signed_in()
        row = document_row(connection(), doc_id)
        path = folder() / row["stored_path"]
        if not path.is_file():
            app.logger.error("Missing document file for document %s", doc_id)
            raise DocumentError("The file is unavailable. Contact an administrator.", 503)
        mimetype = storage.view_type(row["filename"]) if inline else "application/octet-stream"
        if not mimetype:
            raise DocumentError("This file type cannot be viewed in the browser; download it instead.", 415)
        headers = {"Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{row["filename"]}"',
                   "Content-Length": str(row["size_bytes"]), "Cache-Control": "private, no-store",
                   "X-Content-Type-Options": "nosniff"}
        if not (inline and mimetype == "application/pdf"):
            headers["Content-Security-Policy"] = "sandbox; default-src 'none'" + ("; img-src 'self'" if inline else "")
        return Response(storage.chunks(path, bool(row["compressed"])), mimetype=mimetype, headers=headers)

    @app.get("/api/documents/<int:doc_id>/view")
    def view_document(doc_id):
        return send(doc_id, inline=True)

    @app.get("/api/documents/<int:doc_id>/download")
    def download_document(doc_id):
        return send(doc_id, inline=False)

    @app.put("/api/admin/documents/intro")
    def save_intro():
        admin()
        text = clip((request.get_json(silent=True) or {}).get("intro"), MAX_INTRO, "The text", required=False)
        cid = connection()
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            cid.execute("INSERT INTO document_settings(id,intro) VALUES (1,?)"
                        " ON CONFLICT(id) DO UPDATE SET intro=excluded.intro", (text,))
        return jsonify(ok=True)

    def group_fields(body):
        order = body.get("sort_order", 0)
        if type(order) is not int:
            raise DocumentError("sort_order must be a whole number.")
        return (clip(body.get("name"), 80, "A name"),
                clip(body.get("description"), MAX_TEXT, "The description", required=False), order)

    @app.post("/api/admin/document-groups")
    def add_group():
        admin()
        fields = group_fields(request.get_json(silent=True) or {})
        cid = connection()
        try:
            with db.write_lock, cid:
                cid.execute("BEGIN IMMEDIATE")
                gid = cid.execute("INSERT INTO document_groups(name,description,sort_order) VALUES (?,?,?)",
                                  fields).lastrowid
        except sqlite3.IntegrityError as exc:
            raise DocumentError("A group with that name already exists.", 409) from exc
        return jsonify(ok=True, id=gid), 201

    @app.put("/api/admin/document-groups/<int:group_id>")
    def edit_group(group_id):
        admin()
        fields = group_fields(request.get_json(silent=True) or {})
        cid = connection()
        try:
            with db.write_lock, cid:
                cid.execute("BEGIN IMMEDIATE")
                if not cid.execute("UPDATE document_groups SET name=?,description=?,sort_order=? WHERE id=?",
                                   (*fields, group_id)).rowcount:
                    raise DocumentError("No such group.", 404)
        except sqlite3.IntegrityError as exc:
            raise DocumentError("A group with that name already exists.", 409) from exc
        return jsonify(ok=True)

    @app.delete("/api/admin/document-groups/<int:group_id>")
    def delete_group(group_id):
        admin()
        cid = connection()
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            if cid.execute("SELECT 1 FROM documents WHERE group_id=?", (group_id,)).fetchone():
                raise DocumentError("This group still holds documents; move or delete them first.", 409)
            if not cid.execute("DELETE FROM document_groups WHERE id=?", (group_id,)).rowcount:
                raise DocumentError("No such group.", 404)
        return jsonify(ok=True)

    def document_fields(cid, body):
        order = body.get("sort_order", 0)
        try:
            order = int(order)
        except (TypeError, ValueError):
            raise DocumentError("sort_order must be a whole number.") from None
        return (clip(body.get("title"), 120, "A title"),
                clip(body.get("description"), MAX_TEXT, "The description", required=False),
                optional_id(cid, body.get("group_id"), "document_groups", "group"),
                optional_id(cid, body.get("type_id"), "qualification_types", "qualification"), order)

    @app.post("/api/admin/documents")
    def add_document():
        """Multipart form: file, title, description, group_id, type_id, sort_order."""
        admin()
        cid = connection()
        fields = document_fields(cid, request.form)
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            raise DocumentError("Choose a file.")
        filename = Path(upload.filename.replace("\\", "/")).name.replace('"', "")[:200]
        if not filename:
            raise DocumentError("The file needs a name.")
        try:
            staged, compressed, size, _, sha256 = storage.save_attachment(upload.stream, folder(), MAX_BYTES)
        except ValueError as exc:
            raise DocumentError(str(exc), 413 if "limit" in str(exc) else 400) from exc
        stored = uuid.uuid4().hex + (".gz" if compressed else "")
        os.replace(staged, folder() / stored)
        try:
            with db.write_lock, cid:
                cid.execute("BEGIN IMMEDIATE")
                did = cid.execute(
                    "INSERT INTO documents(title,description,group_id,type_id,sort_order,filename,stored_path,"
                    "compressed,size_bytes,sha256,uploaded_by,uploaded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (*fields, filename, stored, int(compressed), size, sha256, g.user["id"],
                     datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))).lastrowid
        except Exception:
            (folder() / stored).unlink(missing_ok=True)
            raise
        return jsonify(ok=True, id=did), 201

    @app.put("/api/admin/documents/<int:doc_id>")
    def edit_document(doc_id):
        admin()
        cid = connection()
        fields = document_fields(cid, request.get_json(silent=True) or {})
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            if not cid.execute("UPDATE documents SET title=?,description=?,group_id=?,type_id=?,sort_order=?"
                               " WHERE id=?", (*fields, doc_id)).rowcount:
                raise DocumentError("No such document.", 404)
        return jsonify(ok=True)

    @app.delete("/api/admin/documents/<int:doc_id>")
    def delete_document(doc_id):
        admin()
        cid = connection()
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            row = cid.execute("SELECT stored_path FROM documents WHERE id=?", (doc_id,)).fetchone()
            if row is None:
                raise DocumentError("No such document.", 404)
            cid.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        (folder() / row["stored_path"]).unlink(missing_ok=True)
        return jsonify(ok=True)

    @app.errorhandler(DocumentError)
    def document_error(exc):
        return jsonify(error=str(exc)), exc.status
