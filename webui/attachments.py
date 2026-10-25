"""Shared files linked to telemetry flights, uploaded logs, and paper records."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from urllib.parse import quote, unquote

from flask import Response, g, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.http import dump_options_header

import db
import historical
import storage

COLUMNS = {"flight": "flight_id", "log": "log_id", "paper": "record_id"}
# Types the browser may show inline, chosen by extension only. HTML, SVG, XML and other active
# content are deliberately absent: they stay downloads so a stored file can never run script here.
# Flask adds "charset=utf-8" to text/plain.
VIEWABLE = {".pdf": "application/pdf", ".txt": "text/plain", ".csv": "text/plain",
            ".md": "text/plain", ".log": "text/plain", ".png": "image/png",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
# Word documents are shown as a PDF converted by LibreOffice (see `convert`), cached per file content.
CONVERTIBLE = {".docx"}
CONVERT_MAX_BYTES = 50 * 2**20
CONVERT_TIMEOUT_S = 90
PREVIEWS = "previews"
Error = historical.RecordError
_convert_lock = threading.Lock()  # one LibreOffice at a time: it is heavy and shares nothing between runs


def view_type(filename):
    return VIEWABLE.get(Path(filename).suffix.lower())


def office_binary(setting):
    if setting in (None, "", "off", "0"):
        return None
    return shutil.which("soffice") if setting == "auto" else setting


def convertible(row, office):
    return bool(office) and Path(row["filename"]).suffix.lower() in CONVERTIBLE and row["size_bytes"] <= CONVERT_MAX_BYTES


def convert(source, compressed, suffix, office, target):
    """Convert one stored document to PDF at `target`. LibreOffice runs headless with a throwaway profile (so no
    user macros or settings apply), on a temporary copy, under a timeout; any failure leaves no file behind."""
    with tempfile.TemporaryDirectory(prefix="logdb-convert-") as tmp:
        document = Path(tmp) / ("document" + suffix)
        with open(document, "wb") as out:
            for chunk in storage.chunks(source, compressed=compressed):
                out.write(chunk)
        # LibreOffice picks an import filter from the content, so a renamed file of any type would be opened:
        # require the ZIP container of a .docx and force the Word import filter.
        with open(document, "rb") as f:
            if f.read(4) != b"PK\x03\x04":
                return False
        try:
            subprocess.run([office, f"-env:UserInstallation={Path(tmp, 'profile').as_uri()}", "--headless",
                            "--norestore", "--nolockcheck", "--infilter=MS Word 2007 XML",
                            "--convert-to", "pdf", "--outdir", tmp, str(document)],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=CONVERT_TIMEOUT_S, check=True)
        except (OSError, subprocess.SubprocessError):
            return False
        result = Path(tmp) / "document.pdf"
        if not result.is_file() or result.stat().st_size == 0:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.with_name(f".{uuid.uuid4().hex}.tmp")
        shutil.copyfile(result, staged)
        os.replace(staged, target)
        return True


def target(con, user, kind, identity, edit=False):
    if not isinstance(kind, str) or kind not in COLUMNS:
        raise Error("Unknown attachment target.")
    identity = historical.integer(identity, "Target ID")
    if kind == "paper":
        row = historical.visible(con, user, identity)
        project, uploader = row["project_id"], None
        label = f'Paper record {identity} - {row["date"]}'
        url = f"/historical/{identity}"
    else:
        if kind == "log":
            row = con.execute("SELECT project_id,uploaded_by,name FROM logs WHERE id=?", (identity,)).fetchone()
            label = f'Log {identity} - {row["name"]}' if row else ""
            url = f"/uploads#log{identity}"
        else:
            row = con.execute(
                "SELECT COALESCE(f.project_id,l.project_id) AS project_id,l.uploaded_by,"
                " a.name,f.idx,f.takeoff_utc FROM flights f JOIN aircraft a ON a.id=f.aircraft_id"
                " JOIN logs l ON l.id=a.log_id WHERE f.id=?", (identity,)).fetchone()
            label = f'{row["name"]} - flight {row["idx"]} - {row["takeoff_utc"] or "unknown time"}' if row else ""
            url = f"/flight/{identity}"
        if row is None:
            raise Error("No such flight or log.", 404)
        project, uploader = row["project_id"], row["uploaded_by"]
        if not user["is_admin"] and not (
                project is None and uploader == user["id"] or con.execute(
                    "SELECT 1 FROM project_members WHERE user_id=? AND project_id=?",
                    (user["id"], project)).fetchone()):
            raise Error("No such flight or log.", 404)
    editable = historical.can_edit(con, user, project) or project is None and uploader == user["id"]
    if edit and not editable:
        raise Error("Full access to every selected flight/log is required.", 403)
    return dict(kind=kind, id=identity, label=label, url=url, can_edit=bool(editable))


def link_targets(con, user, attachment_id):
    links = []
    for row in con.execute("SELECT * FROM attachment_links WHERE attachment_id=?", (attachment_id,)):
        kind = next(k for k, column in COLUMNS.items() if row[column] is not None)
        try:
            item = target(con, user, kind, row[COLUMNS[kind]])
        except Error as exc:
            if exc.status != 404:
                raise
            continue  # Do not disclose the names or IDs of links in inaccessible projects.
        links.append(dict(item, link_id=row["id"]))
    return links


def visible(con, user, attachment_id):
    row = con.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
    links = link_targets(con, user, attachment_id) if row else []
    if not links:
        raise Error("No such attachment.", 404)
    return row, links


def checked_targets(con, user, values):
    if not isinstance(values, list) or not 1 <= len(values) <= 100:
        raise Error("Select between 1 and 100 flights/logs.")
    result = {}
    for item in values:
        if not isinstance(item, dict):
            raise Error("Invalid attachment target.")
        t = target(con, user, item.get("kind"), item.get("id"), edit=True)
        result[(t["kind"], t["id"])] = t
    return list(result.values())


def add_links(con, attachment_id, targets):
    for t in targets:
        column = COLUMNS[t["kind"]]
        con.execute(f"INSERT OR IGNORE INTO attachment_links(attachment_id,{column}) VALUES (?,?)",
                    (attachment_id, t["id"]))


def prune(con, root):
    """Called under the write lock after commit; cleanup errors propagate for logging."""
    rows = con.execute("SELECT id,path,sha256 FROM attachments WHERE NOT EXISTS"
                       " (SELECT 1 FROM attachment_links WHERE attachment_id=attachments.id)").fetchall()
    for row in rows:
        with con:
            (root / row["path"]).unlink(missing_ok=True)
            con.execute("DELETE FROM attachments WHERE id=?", (row["id"],))
            if not con.execute("SELECT 1 FROM attachments WHERE sha256=?", (row["sha256"],)).fetchone():
                (root / PREVIEWS / f"{row['sha256']}.pdf").unlink(missing_ok=True)


def snapshot(con, log_id):
    return [dict(r) for r in con.execute(
        "SELECT x.attachment_id,a.ac_id,f.idx,f.takeoff_time_s FROM attachment_links x"
        " JOIN flights f ON f.id=x.flight_id JOIN aircraft a ON a.id=f.aircraft_id WHERE a.log_id=?", (log_id,))]


def restore(con, log_id, saved):
    for old in saved:
        match = con.execute(
            "SELECT f.id FROM flights f JOIN aircraft a ON a.id=f.aircraft_id"
            " WHERE a.log_id=? AND a.ac_id IS ? AND f.idx IS ? AND f.takeoff_time_s IS ?",
            (log_id, old["ac_id"], old["idx"], old["takeoff_time_s"])).fetchone()
        add_links(con, old["attachment_id"], [
            dict(kind="flight", id=match["id"]) if match else dict(kind="log", id=log_id)])


def register(app, connection, root, require_session, timestamp, max_bytes, office=None):
    root = Path(root)
    app.config["ATTACHMENT_OFFICE"] = office_binary(office)
    if max_bytes <= 0:
        raise ValueError("LOGDB_ATTACHMENT_MAX_BYTES must be positive")
    app.config["ATTACHMENT_MAX_BYTES"] = max_bytes

    @app.errorhandler(Error)
    def attachment_error(exc):
        return jsonify(error=str(exc)), exc.status

    @app.get("/attachments")
    def attachments_page():
        return send_from_directory(Path(__file__).parent / "static", "attachments.html")

    @app.get("/attachments.js")
    def attachments_script():
        return send_from_directory(Path(__file__).parent / "static", "attachments.js")

    @app.get("/api/attachments/targets")
    def attachment_targets():
        con = connection()
        kind, identity = request.args.get("kind"), request.args.get("id")
        if kind is not None:
            return jsonify(target=target(con, g.user, kind, identity),
                           max_bytes=app.config["ATTACHMENT_MAX_BYTES"])
        query = request.args.get("q", "").strip().casefold()
        if len(query) < 2 and not (query.isascii() and query.isdigit()):
            return jsonify(targets=[])
        found = []
        for category in COLUMNS:
            if category == "log":
                sql = "SELECT l.id FROM logs l WHERE (l.name LIKE ? OR CAST(l.id AS TEXT)=?)"
                project, uploader, order = "l.project_id", "l.uploaded_by", "l.id"
            elif category == "flight":
                sql = ("SELECT f.id FROM flights f JOIN aircraft a ON a.id=f.aircraft_id"
                       " LEFT JOIN drones d ON d.id=a.drone_id"
                       " JOIN logs l ON l.id=a.log_id"
                       " WHERE (a.name LIKE ? OR d.name LIKE ? OR f.takeoff_utc LIKE ? OR CAST(f.id AS TEXT)=?)")
                project, uploader, order = "COALESCE(f.project_id,l.project_id)", "l.uploaded_by", "f.id"
            else:
                sql = ("SELECT r.id FROM flight_records r JOIN drones d ON d.id=r.drone_id"
                       " WHERE (d.name LIKE ? OR r.date LIKE ? OR CAST(r.id AS TEXT)=?)")
                project, uploader, order = "r.project_id", "NULL", "r.id"
            if category == "log":
                params = (f"%{query}%", query)
            elif category == "flight":
                params = (f"%{query}%", f"%{query}%", f"%{query}%", query)
            else:
                params = (f"%{query}%", f"%{query}%", query)
            sql += (f" AND (? OR {project} IN (SELECT project_id FROM project_members WHERE user_id=? AND role='full')"
                    f" OR ({project} IS NULL AND {uploader}=?)) ORDER BY {order} DESC LIMIT 100")
            for r in con.execute(sql, (*params, g.user["is_admin"], g.user["id"], g.user["id"])):
                found.append(target(con, g.user, category, r["id"], edit=True))
        return jsonify(targets=found[:100])

    @app.get("/api/attachments")
    def list_attachments():
        con = connection()
        kind = request.args.get("kind")
        t = target(con, g.user, kind, request.args.get("id"))
        column = COLUMNS[kind]
        ids = {r[0] for r in con.execute(f"SELECT attachment_id FROM attachment_links WHERE {column}=?", (t["id"],))}
        if kind == "flight":
            log = con.execute("SELECT a.log_id FROM flights f JOIN aircraft a ON a.id=f.aircraft_id WHERE f.id=?",
                              (t["id"],)).fetchone()
            # Parent-log files are inherited only when the user can also see that log.
            try:
                target(con, g.user, "log", log["log_id"])
            except Error as exc:
                if exc.status != 404:
                    raise
            else:
                ids.update(r[0] for r in con.execute(
                    "SELECT attachment_id FROM attachment_links WHERE log_id=?", (log["log_id"],)))
        result = []
        for aid in sorted(ids, reverse=True):
            row, links = visible(con, g.user, aid)
            item = {k: row[k] for k in ("id", "filename", "description", "size_bytes", "stored_bytes",
                                        "compressed", "uploaded_at", "sha256")}
            item["links"] = links
            item["viewable"] = view_type(row["filename"]) is not None or convertible(
                row, app.config["ATTACHMENT_OFFICE"])
            item["can_share"] = all(x["can_edit"] for x in links) and len(links) == con.execute(
                "SELECT COUNT(*) FROM attachment_links WHERE attachment_id=?", (aid,)).fetchone()[0]
            result.append(item)
        return jsonify(files=result, target=t, max_bytes=app.config["ATTACHMENT_MAX_BYTES"])

    @app.post("/api/attachments")
    def upload_attachment():
        # Also with an API key, for scripted evidence uploads; the key's user needs edit rights on every target
        limit = app.config["ATTACHMENT_MAX_BYTES"]
        request.max_content_length = limit
        try:
            metadata = json.loads(unquote(request.headers.get("X-Attachment-Metadata", "")))
        except (ValueError, UnicodeError) as exc:
            raise Error("Invalid attachment metadata.") from exc
        if not isinstance(metadata, dict):
            raise Error("Invalid attachment metadata.")
        filename = metadata.get("filename")
        description = metadata.get("description", "")
        if (not isinstance(filename, str) or not 1 <= len(filename) <= 255
                or any(ord(c) < 32 or c in "/\\" for c in filename) or filename in (".", "..")):
            raise Error("Supply a filename without directories (at most 255 characters).")
        if not isinstance(description, str) or len(description) > 1000:
            raise Error("Description must be at most 1000 characters.")
        con = connection()
        checked_targets(con, g.user, metadata.get("targets"))
        staged = final = None
        try:
            try:
                staged, compressed, size, stored, sha = storage.save_attachment(
                    request.stream, root / ".incoming-attachments", limit)
            except ValueError as exc:
                raise Error(str(exc), 413 if "exceeds" in str(exc) else 400) from exc
            relative = f"attachments/{uuid.uuid4().hex}" + (".gz" if compressed else ".bin")
            final = root / relative
            final.parent.mkdir(parents=True, exist_ok=True)
            with db.write_lock, con:
                con.execute("BEGIN IMMEDIATE")
                targets = checked_targets(con, g.user, metadata.get("targets"))
                os.replace(staged, final)
                cur = con.execute(
                    "INSERT INTO attachments(filename,description,path,compressed,size_bytes,stored_bytes,sha256,"
                    " uploaded_by,uploaded_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (filename, description.strip(), relative, int(compressed), size, stored, sha, g.user["id"], timestamp()))
                add_links(con, cur.lastrowid, targets)
            final = None
            return jsonify(id=cur.lastrowid), 201
        except RequestEntityTooLarge:
            return jsonify(error=f"File exceeds the {limit / (1 << 30):g} GiB limit."), 413
        finally:
            if staged:
                staged.unlink(missing_ok=True)
            if final:
                final.unlink(missing_ok=True)

    def send(attachment_id, inline):
        row, _ = visible(connection(), g.user, attachment_id)
        if inline and view_type(row["filename"]) is None and convertible(row, app.config["ATTACHMENT_OFFICE"]):
            return send_converted(row)
        mimetype = view_type(row["filename"]) if inline else "application/octet-stream"
        if not mimetype:
            raise Error("This file type cannot be viewed in the browser; download it instead.", 415)
        path = root / row["path"]
        if not path.is_file():
            raise Error("Attachment is missing from storage; contact an administrator.", 410)
        disposition = "inline" if inline else "attachment"
        if not row["compressed"]:
            response = send_file(path, mimetype=mimetype, conditional=True)
        else:
            response = Response(storage.chunks(path, compressed=True), mimetype=mimetype)
        response.headers["Content-Disposition"] = dump_options_header(
            disposition, {"filename": row["filename"].encode("ascii", "ignore").decode() or "attachment"}) \
            + "; filename*=UTF-8''" + quote(row["filename"], safe="")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "private, no-store"
        if inline and mimetype != "application/pdf":
            # Defense in depth for text and images; browsers' built-in PDF viewers break under a sandbox.
            response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self'"
        return response

    def send_converted(row):
        path = root / row["path"]
        if not path.is_file():
            raise Error("Attachment is missing from storage; contact an administrator.", 410)
        preview = root / PREVIEWS / f"{row['sha256']}.pdf"
        with _convert_lock:
            if not preview.is_file() and not convert(path, row["compressed"], Path(row["filename"]).suffix.lower(),
                                                     app.config["ATTACHMENT_OFFICE"], preview):
                raise Error("This document could not be converted for viewing; download it instead.", 422)
        name = Path(row["filename"]).stem + ".pdf"
        response = send_file(preview, mimetype="application/pdf", conditional=True)
        response.headers["Content-Disposition"] = dump_options_header(
            "inline", {"filename": name.encode("ascii", "ignore").decode() or "document.pdf"}) \
            + "; filename*=UTF-8''" + quote(name, safe="")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.get("/api/attachments/<int:attachment_id>/download")
    def download_attachment(attachment_id):
        return send(attachment_id, inline=False)

    @app.get("/api/attachments/<int:attachment_id>/view")
    def view_attachment(attachment_id):
        return send(attachment_id, inline=True)

    @app.post("/api/attachments/<int:attachment_id>/links")
    def share_attachment(attachment_id):
        # Also with an API key: one file (e.g. a CTR request) can cover several flights
        con = connection()
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise Error("Send a JSON object.")
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            visible(con, g.user, attachment_id)
            for row in con.execute("SELECT * FROM attachment_links WHERE attachment_id=?", (attachment_id,)):
                kind = next(k for k, column in COLUMNS.items() if row[column] is not None)
                target(con, g.user, kind, row[COLUMNS[kind]], edit=True)
            add_links(con, attachment_id, checked_targets(con, g.user, body.get("targets")))
        return jsonify(ok=True)

    @app.delete("/api/attachments/<int:attachment_id>/links/<int:link_id>")
    def unlink_attachment(attachment_id, link_id):
        require_session()
        con = connection()
        with db.write_lock:
            with con:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute("SELECT * FROM attachment_links WHERE id=? AND attachment_id=?",
                                  (link_id, attachment_id)).fetchone()
                if row is None:
                    raise Error("No such attachment link.", 404)
                kind = next(k for k, column in COLUMNS.items() if row[column] is not None)
                target(con, g.user, kind, row[COLUMNS[kind]], edit=True)
                con.execute("DELETE FROM attachment_links WHERE id=?", (link_id,))
            prune(con, root)
        return jsonify(ok=True)
