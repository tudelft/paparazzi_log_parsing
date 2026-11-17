"""Shared files linked to telemetry flights, uploaded logs, and paper records."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, unquote

from flask import g, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.http import dump_options_header

import db
import historical
import storage
from storage import view_type

COLUMNS = {"flight": "flight_id", "log": "log_id", "paper": "record_id"}
# Word documents are shown as a PDF converted by LibreOffice (see `convert`), cached per file content.
CONVERTIBLE = {".docx"}
CONVERT_MAX_BYTES = 50 * 2**20
CONVERT_TIMEOUT_S = 90
PREVIEWS = "previews"
Error = historical.RecordError
MATCH_BEFORE_S = 15 * 60  # a file made this long before takeoff belongs to the flight
MATCH_AFTER_S = 30 * 60   # ... and this long after landing
_convert_lock = threading.Lock()  # one LibreOffice at a time: it is heavy and shares nothing between runs
DEFAULT_CATEGORIES = ["Preparation & permits", "Notes", "Media", "Data", "Analysis", "Archive"]
PREFIX = re.compile(r"\[([^\]\n]{1,40})\][ \t]*")


def playable(filename):
    """Video or audio the browser plays itself."""
    return (view_type(filename) or "").startswith(("video/", "audio/"))


def category_value(value, categories):
    """The listed category matching `value` (any case), None for no category; anything else is refused."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        for name in categories:
            if value.strip().casefold() == name.casefold():
                return name
    raise Error("Unknown category; choose one of: " + ", ".join(categories) + ".")


def description_value(value):
    if not isinstance(value, str) or len(value) > 1000:
        raise Error("Description must be at most 1000 characters.")
    return value.strip()


def inherit_category(con, attachment_id, replaced):
    """A new version without a category takes the category of the version it replaces."""
    ids = [r["id"] for r in replaced]
    if ids:
        con.execute(f"UPDATE attachments SET category=(SELECT category FROM attachments WHERE id IN"
                    f" ({','.join('?' * len(ids))}) AND category IS NOT NULL ORDER BY id DESC LIMIT 1)"
                    f" WHERE id=? AND category IS NULL", (*ids, attachment_id))


def parse_mapping(pairs, categories):
    """--map OLD=NEW pairs: {old prefix casefolded: listed category}."""
    mapping = {}
    for pair in pairs:
        old, sep, new = pair.partition("=")
        if not sep or not old.strip():
            raise ValueError(f"--map needs OLD=NEW, not {pair!r}")
        try:
            mapping[old.strip().casefold()] = category_value(new, categories)
        except Error as exc:
            raise ValueError(f"--map {pair}: {exc}") from exc
        if mapping[old.strip().casefold()] is None:
            raise ValueError(f"--map {pair}: name a category")
    return mapping


def backfill_categories(con, categories, mapping=None, apply=True):
    """Move a '[Category] ' prefix of uncategorised attachments into the category column and strip it.
    Returns ([(id, category, new description)], {unknown prefix: count})."""
    mapping = dict(mapping or {})
    changes, skipped = [], {}
    for row in con.execute("SELECT id, description FROM attachments WHERE category IS NULL AND description LIKE '[%'"
                           " ORDER BY id").fetchall():
        match = PREFIX.match(row["description"])
        if not match:
            continue
        prefix = match.group(1).strip()
        category = mapping.get(prefix.casefold())
        if category is None:
            try:
                category = category_value(prefix, categories)
            except Error:
                skipped[prefix] = skipped.get(prefix, 0) + 1
                continue
        description = row["description"][match.end():].strip()
        changes.append((row["id"], category, description))
        if apply:
            con.execute("UPDATE attachments SET category=?, description=? WHERE id=?",
                        (category, description, row["id"]))
    return changes, skipped


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


ADD_ROLES = ("add", "full", "manager")  # project roles that may add files ("Upload"); removing needs full/manager


def target(con, user, kind, identity, edit=False, add=False):
    """A flight, log or paper record the user may see, with can_add (may add files: Upload or more) and can_edit (may
    remove links: Delete or Manager). edit/add require that right (403)."""
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
            url = f"/logs#log{identity}"
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
    own = project is None and uploader is not None and uploader == user["id"]
    editable = bool(historical.can_edit(con, user, project) or own)
    addable = bool(editable or con.execute(
        f"SELECT 1 FROM project_members WHERE user_id=? AND project_id=? AND role IN ({','.join('?' * len(ADD_ROLES))})",
        (user["id"], project, *ADD_ROLES)).fetchone())
    if edit and not editable:
        raise Error("Full access to every selected flight/log is required.", 403)
    if add and not addable:
        raise Error("Upload access to every selected flight/log is required.", 403)
    return dict(kind=kind, id=identity, label=label, url=url, can_edit=editable, can_add=addable)


def can_change(con, user, row, links):
    """May change a file's category and description: Delete/Manager on one of its flights/logs, or Upload on one for a
    file the user uploaded."""
    return any(link["can_edit"] for link in links) or (
        row["uploaded_by"] == user["id"] and any(link["can_add"] for link in links))


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
        t = target(con, user, item.get("kind"), item.get("id"), add=True)
        result[(t["kind"], t["id"])] = t
    return list(result.values())


def add_links(con, attachment_id, targets):
    for t in targets:
        column = COLUMNS[t["kind"]]
        con.execute(f"INSERT OR IGNORE INTO attachment_links(attachment_id,{column}) VALUES (?,?)",
                    (attachment_id, t["id"]))


def supersede(con, attachment_id, filename, targets, stamp):
    """A new upload replaces, per target, the current file with the same name (other content): the old link stays,
    marked as an earlier version. Returns the superseded files as {id, filename, uploaded_at}."""
    replaced = {}
    for t in targets:
        column = COLUMNS[t["kind"]]
        for row in con.execute(
                f"SELECT x.id AS link_id,a.id,a.filename,a.uploaded_at FROM attachment_links x"
                f" JOIN attachments a ON a.id=x.attachment_id WHERE x.{column}=? AND x.superseded_by IS NULL"
                f" AND a.id<>? AND a.filename=? COLLATE NOCASE", (t["id"], attachment_id, filename)).fetchall():
            con.execute("UPDATE attachment_links SET superseded_by=?,superseded_at=? WHERE id=?",
                        (attachment_id, stamp, row["link_id"]))
            replaced[row["id"]] = dict(id=row["id"], filename=row["filename"], uploaded_at=row["uploaded_at"])
    return list(replaced.values())


def merge_duplicates(con, root):
    """Merge attachments stored more than once with the same name and content (e.g. an upload retried after a
    restart) into the oldest: its links are kept for every target, the extra rows and stored copies removed.
    Idempotent; run at startup. Returns the number of removed copies."""
    removed = 0
    groups = con.execute(
        "SELECT sha256,size_bytes,filename,MIN(id) AS keep FROM attachments"
        " GROUP BY sha256,size_bytes,filename HAVING COUNT(*)>1").fetchall()
    for group in groups:
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            keep = group["keep"]
            copies = con.execute("SELECT id,path,description,category FROM attachments WHERE sha256=? AND size_bytes=?"
                                 " AND filename=? AND id<>?",
                                 (group["sha256"], group["size_bytes"], group["filename"], keep)).fetchall()
            for copy in copies:
                for column in COLUMNS.values():
                    con.execute(
                        f"INSERT OR IGNORE INTO attachment_links(attachment_id,{column},superseded_by,superseded_at)"
                        f" SELECT ?,{column},superseded_by,superseded_at FROM attachment_links"
                        f" WHERE attachment_id=? AND {column} IS NOT NULL", (keep, copy["id"]))
                con.execute("UPDATE attachment_links SET superseded_by=? WHERE superseded_by=?", (keep, copy["id"]))
                con.execute("UPDATE attachments SET description=? WHERE id=? AND description=''",
                            (copy["description"], keep))
                con.execute("UPDATE attachments SET category=? WHERE id=? AND category IS NULL",
                            (copy["category"], keep))
                con.execute("DELETE FROM attachments WHERE id=?", (copy["id"],))
                removed += 1
            con.execute("UPDATE attachment_links SET superseded_by=NULL,superseded_at=NULL"
                        " WHERE superseded_by=attachment_id")
        for copy in copies:
            if not con.execute("SELECT 1 FROM attachments WHERE path=?", (copy["path"],)).fetchone():
                (root / copy["path"]).unlink(missing_ok=True)
    return removed


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


def register(app, connection, root, require_session, timestamp, max_bytes, office=None, categories=None,
             same_day=None, flights_on=None):
    root = Path(root)
    app.config["ATTACHMENT_OFFICE"] = office_binary(office)
    app.config["ATTACHMENT_CATEGORIES"] = list(categories or DEFAULT_CATEGORIES)
    if max_bytes <= 0:
        raise ValueError("LOGDB_ATTACHMENT_MAX_BYTES must be positive")
    app.config["ATTACHMENT_MAX_BYTES"] = max_bytes

    @app.errorhandler(Error)
    def attachment_error(exc):
        return jsonify(error=str(exc)), exc.status

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
            sql += (f" AND (? OR {project} IN (SELECT project_id FROM project_members WHERE user_id=? AND role IN ('add','full','manager'))"
                    f" OR ({project} IS NULL AND {uploader}=?)) ORDER BY {order} DESC LIMIT 100")
            for r in con.execute(sql, (*params, g.user["is_admin"], g.user["id"], g.user["id"])):
                found.append(target(con, g.user, category, r["id"], add=True))
        return jsonify(targets=found[:100])

    @app.get("/api/attachments")
    def list_attachments():
        con = connection()
        kind = request.args.get("kind")
        t = target(con, g.user, kind, request.args.get("id"))
        column = COLUMNS[kind]
        ids = {r[0] for r in con.execute(f"SELECT attachment_id FROM attachment_links WHERE {column}=?", (t["id"],))}
        inherited = parent = None
        if kind == "flight":
            log = con.execute("SELECT a.log_id FROM flights f JOIN aircraft a ON a.id=f.aircraft_id WHERE f.id=?",
                              (t["id"],)).fetchone()
            # Parent-log files are inherited only when the user can also see that log.
            try:
                parent = target(con, g.user, "log", log["log_id"])  # files linked here show on all its flights
            except Error as exc:
                if exc.status != 404:
                    raise
            else:
                ids.update(r[0] for r in con.execute(
                    "SELECT attachment_id FROM attachment_links WHERE log_id=?", (log["log_id"],)))
                inherited = log["log_id"]
        # earlier versions on this target, or on the parent log for inherited files
        superseded = {}
        for col, value in ((column, t["id"]), ("log_id", inherited)):
            superseded.update((r["attachment_id"], dict(by=r["superseded_by"], at=r["superseded_at"])) for r in con.execute(
                f"SELECT attachment_id,superseded_by,superseded_at FROM attachment_links"
                f" WHERE {col}=? AND superseded_by IS NOT NULL", (value,)))
        result = []
        for aid in sorted(ids, reverse=True):
            row, links = visible(con, g.user, aid)
            item = {k: row[k] for k in ("id", "filename", "description", "category", "size_bytes", "stored_bytes",
                                        "compressed", "uploaded_at", "sha256")}
            item["links"] = links
            item["superseded"] = superseded.get(aid)  # an earlier version on this target
            item["viewable"] = view_type(row["filename"]) is not None or convertible(
                row, app.config["ATTACHMENT_OFFICE"])
            item["can_share"] = all(x["can_add"] for x in links) and len(links) == con.execute(
                "SELECT COUNT(*) FROM attachment_links WHERE attachment_id=?", (aid,)).fetchone()[0]
            item["can_edit"] = can_change(con, g.user, row, links)
            result.append(item)
        return jsonify(files=result, target=t, parent=parent, max_bytes=app.config["ATTACHMENT_MAX_BYTES"],
                       categories=app.config["ATTACHMENT_CATEGORIES"],
                       day=day_targets(con, kind, t) if same_day else None,
                       day_all=day_targets(con, kind, t, any_aircraft=True) if same_day else None)

    def day_targets(con, kind, t, any_aircraft=False):
        """For a flight: the flights of its aircraft (or of any aircraft) that day the user may add files to, which the
        upload dialog offers together; None when there is no other one."""
        found = same_day(con, t["id"], any_aircraft) if kind == "flight" else None
        if not found:
            return None
        name, date, ids = found
        flights = []
        for fid in ids[:100]:  # an upload goes to at most 100 targets
            try:
                item = target(con, g.user, "flight", fid)
            except Error as exc:
                if exc.status != 404:
                    raise
                continue
            if item["can_add"]:
                flights.append(item)
        if not any(f["id"] != t["id"] for f in flights):
            return None
        return dict(aircraft=name, date=date, flights=flights)

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
        if (not isinstance(filename, str) or not 1 <= len(filename) <= 255
                or any(ord(c) < 32 or c in "/\\" for c in filename) or filename in (".", "..")):
            raise Error("Supply a filename without directories (at most 255 characters).")
        description = description_value(metadata.get("description", ""))
        category = category_value(metadata.get("category"), app.config["ATTACHMENT_CATEGORIES"])
        con = connection()
        checked_targets(con, g.user, metadata.get("targets"))
        staged = final = None
        try:
            try:
                staged, compressed, size, stored, sha = storage.save_attachment(
                    request.stream, root / ".incoming-attachments", limit, compress=not playable(filename))
            except ValueError as exc:
                raise Error(str(exc), 413 if "exceeds" in str(exc) else 400) from exc
            relative = f"attachments/{uuid.uuid4().hex}" + (".gz" if compressed else ".bin")
            final = root / relative
            final.parent.mkdir(parents=True, exist_ok=True)
            with db.write_lock, con:
                con.execute("BEGIN IMMEDIATE")
                targets = checked_targets(con, g.user, metadata.get("targets"))
                stamp = timestamp()
                # The same file (name and content) that this user can already see: link it, store nothing new.
                for same in con.execute("SELECT id FROM attachments WHERE sha256=? AND size_bytes=? AND filename=? COLLATE NOCASE"
                                        " ORDER BY id", (sha, size, filename)).fetchall():
                    try:
                        visible(con, g.user, same["id"])
                    except Error:
                        continue
                    add_links(con, same["id"], targets)
                    for t in targets:  # an earlier version uploaded again becomes current again
                        con.execute(f"UPDATE attachment_links SET superseded_by=NULL,superseded_at=NULL"
                                    f" WHERE attachment_id=? AND {COLUMNS[t['kind']]}=?", (same["id"], t["id"]))
                    replaced = supersede(con, same["id"], filename, targets, stamp)
                    # an existing category stays; a missing one is filled in
                    con.execute("UPDATE attachments SET category=? WHERE id=? AND category IS NULL",
                                (category, same["id"]))
                    inherit_category(con, same["id"], replaced)
                    final = None
                    return jsonify(id=same["id"], duplicate=True, replaces=replaced), 200
                os.replace(staged, final)
                cur = con.execute(
                    "INSERT INTO attachments(filename,description,path,compressed,size_bytes,stored_bytes,sha256,"
                    " uploaded_by,uploaded_at,category) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (filename, description, relative, int(compressed), size, stored, sha, g.user["id"], stamp,
                     category))
                add_links(con, cur.lastrowid, targets)
                replaced = supersede(con, cur.lastrowid, filename, targets, stamp)
                inherit_category(con, cur.lastrowid, replaced)
            final = None
            return jsonify(id=cur.lastrowid, duplicate=False, replaces=replaced), 201
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
        if problem := storage.stored_file_problem(path):
            app.logger.error("Missing attachment %s (%s): %s", row["id"], path, problem)
            raise Error(storage.UNAVAILABLE, 503)
        disposition = "inline" if inline else "attachment"
        response = storage.send_stored(path, row["compressed"], mimetype)
        response.headers["Content-Disposition"] = dump_options_header(
            disposition, {"filename": row["filename"].encode("ascii", "ignore").decode() or "attachment"}) \
            + "; filename*=UTF-8''" + quote(row["filename"], safe="")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "private, no-store"
        if inline and mimetype != "application/pdf":
            # Defense in depth. Browsers' built-in PDF viewers break under a sandbox, and so does their video/audio
            # player: those get no sandbox but may load nothing except the media itself (no script either way).
            response.headers["Content-Security-Policy"] = ("default-src 'none'; media-src 'self'" if playable(row["filename"])
                                                           else "sandbox; default-src 'none'; img-src 'self'")
        return response

    def send_converted(row):
        path = root / row["path"]
        if problem := storage.stored_file_problem(path):
            app.logger.error("Missing attachment %s (%s): %s", row["id"], path, problem)
            raise Error(storage.UNAVAILABLE, 503)
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

    @app.patch("/api/attachments/<int:attachment_id>")
    def edit_attachment(attachment_id):
        """Change the category and/or description. The file is shared by all its links, so the change shows on
        every flight/log it is linked to; full access to one of them is enough (as uploading it there)."""
        require_session()
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not {"category", "description"} & body.keys():
            raise Error("Send {\"category\": ..., \"description\": ...}.")
        changes = {}
        if "category" in body:
            changes["category"] = category_value(body["category"], app.config["ATTACHMENT_CATEGORIES"])
        if "description" in body:
            changes["description"] = description_value(body["description"])
        con = connection()
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            row, links = visible(con, g.user, attachment_id)
            if not can_change(con, g.user, row, links):
                raise Error("Full access to a flight/log this file is linked to, or uploading it yourself, is required.", 403)
            con.execute(f"UPDATE attachments SET {', '.join(f'{k}=?' for k in changes)} WHERE id=?",
                        (*changes.values(), attachment_id))
        return jsonify(ok=True, **changes)

    @app.post("/api/attachments/match")
    def match_attachments():
        """Flights for files by when they were made: a moment (photo capture time, a time in the name, ...) or a
        range {start, end} (a video). Per item: `flight`, the flight it overlaps most, else the nearest one within 15
        min before takeoff to 30 min after landing; `flights`, every flight a range overlaps (when several); `how`;
        and `day`, all flights of that UTC day. Only flights the user may add files to. Reads only."""
        body = request.get_json(silent=True)
        items = body.get("times") if isinstance(body, dict) else None
        if not isinstance(items, list) or len(items) > 1000:
            raise Error("Send {\"times\": [ISO time with a time zone, or {\"start\": ..., \"end\": ...}]} (at most 1000).")

        def stamp(value):
            try:
                moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                return None
            return moment.timestamp() if moment.tzinfo else None
        spans = []
        for item in items:
            first, last = (stamp(item.get("start")), stamp(item.get("end"))) if isinstance(item, dict) else (stamp(item),) * 2
            if first is not None and (last is None or last < first or last - first > 86400):
                last = first  # no usable end: a moment
            spans.append((first, last) if first is not None else None)
        days = {datetime.fromtimestamp(ts + shift, timezone.utc).date().isoformat()
                for span in spans if span for ts in span for shift in (-86400, 0, 86400)}
        con = connection()
        windows = flights_on(con, days) if flights_on and days else []
        editable = {}

        def allowed(fid):
            if fid not in editable:
                try:
                    item = target(con, g.user, "flight", fid)
                except Error as exc:
                    if exc.status != 404:
                        raise
                    item = None
                editable[fid] = item if item and item["can_add"] else None
            return editable[fid]

        def overlap(span, w):
            return max(0.0, min(span[1], w[2]) - max(span[0], w[1]))

        def gap(span, w):
            return 0 if span[0] <= w[2] and span[1] >= w[1] else min(abs(span[0] - w[2]), abs(span[1] - w[1]))
        matches = []
        for span in spans:
            if span is None:
                matches.append(None)
                continue
            mine = [w for w in windows if allowed(w[0])]
            hits = [w for w in mine if span[0] <= w[2] and span[1] >= w[1]]  # the file's time touches the flight
            near = hits or [w for w in mine if w[1] - MATCH_BEFORE_S <= span[1] and span[0] <= w[2] + MATCH_AFTER_S]
            best = max(near, key=lambda w: (overlap(span, w), -gap(span, w), -w[1])) if near else None
            how = None
            if best:
                how = ("during" if best[1] <= span[0] and span[1] <= best[2] else "overlaps" if hits
                       else "before takeoff" if span[1] < best[1] else "after landing")
            date = datetime.fromtimestamp(span[0], timezone.utc).date().isoformat()
            that_day = [allowed(w[0]) for w in mine if w[3] == date][:100]
            matches.append(dict(flight=allowed(best[0]) if best else None, how=how,
                                flights=[allowed(w[0]) for w in hits][:100] if len(hits) > 1 else None,
                                day=dict(date=date, flights=that_day) if that_day else None))
        return jsonify(matches=matches, categories=app.config["ATTACHMENT_CATEGORIES"],
                       max_bytes=app.config["ATTACHMENT_MAX_BYTES"])

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
                target(con, g.user, kind, row[COLUMNS[kind]], add=True)
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
                # removing the newest version on a target makes the version it replaced current again
                con.execute(f"UPDATE attachment_links SET superseded_by=NULL,superseded_at=NULL"
                            f" WHERE superseded_by=? AND {COLUMNS[kind]}=?", (attachment_id, row[COLUMNS[kind]]))
            prune(con, root)
        return jsonify(ok=True)
