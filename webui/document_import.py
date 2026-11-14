"""Admin bulk import of documents: a ZIP with manifest.csv and the files (issue/PR 230, like the qualification import).

manifest.csv columns (title and file required, the rest optional, any order):
  title, group, description, valid_from, valid_until, admin_only, qualification, roles, file
- group: the name of a document group; an unknown name is created on confirmation (New group). Empty: General.
- admin_only: yes/no (also 1/0, true/false); empty is no.
- qualification: the name of an existing qualification type (the document is then only for its holders).
- roles: profile role names separated by ";" (the document is then only for those roles).
A row whose title and group match an existing document replaces its file and settings (Replace); with the same
file and settings it is skipped, so a ZIP can be imported again, for example with renewed certificates.
The preview is staged on disk for 30 minutes and committed with its token from the same browser session.
"""
import csv
import hashlib
import io
import json
import os
import shutil
import time
import uuid
from pathlib import Path

from flask import g, session

import db
import profiles
import qualification_import
import qualifications
import storage
from documents import MAX_BYTES, MAX_TEXT, DocumentError

COLUMNS = ("title", "group", "description", "valid_from", "valid_until", "admin_only", "qualification", "roles", "file")
REQUIRED = ("title", "file")
TEMPLATE = """title,group,description,valid_from,valid_until,admin_only,qualification,roles,file
Insurance certificate,Company,Third-party liability,2026-01-01,2026-12-31,no,,,insurance.pdf
Operations manual,Operations,,,,no,,,OM/operations-manual.pdf
Specific category authorisation,Operations,,2026-03-01,2028-02-28,no,A2,,authorisation.pdf
"""
MAX_ROWS = 500
STAGE_TTL_S = 30 * 60
YES, NO = {"yes", "y", "1", "true", "ja"}, {"no", "n", "0", "false", "nee", ""}


def _text(value):
    return " ".join((value or "").split())


def parse_csv(text):
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")), strict=True)
    names = [(name or "").strip().casefold() for name in reader.fieldnames or []]
    unknown = [name for name in names if name not in COLUMNS]
    if unknown or len(set(names)) != len(names) or any(name not in names for name in REQUIRED):
        raise DocumentError("manifest.csv headers: title and file, and optionally "
                            + ", ".join(c for c in COLUMNS if c not in REQUIRED) + " (each once).")
    reader.fieldnames = names
    rows = []
    for raw in reader:
        if len(rows) >= MAX_ROWS:
            raise DocumentError(f"Import at most {MAX_ROWS} documents at once.")
        if None in raw or any(value is None for value in raw.values()):
            raise DocumentError(f"manifest.csv line {reader.line_num}: incorrect number of columns.")
        body = {key: _text(raw.get(key)) for key in COLUMNS}
        body["description"] = (raw.get("description") or "").strip()
        if any(body.values()):
            body["line"] = reader.line_num
            rows.append(body)
    if not rows:
        raise DocumentError("manifest.csv has no document rows.")
    return rows


def split_upload(filename, raw):
    """(manifest text, {path: bytes}, folder of the manifest) of an uploaded ZIP."""
    if not (filename or "").casefold().endswith(".zip"):
        raise DocumentError("Upload a ZIP that contains manifest.csv and the files.")
    try:
        files = qualification_import.read_zip(raw)
        manifest, text, folder = qualification_import._manifest_from_files(files)
    except qualifications.QualificationError as exc:
        raise DocumentError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - zipfile raises several kinds for a broken archive
        raise DocumentError("The ZIP could not be read.") from exc
    files.pop(manifest, None)
    return text, files, folder


def resolve_rows(con, rows, files, folder=""):
    """Check every row against the database and the ZIP; each gets error/warning and outcome add/replace/skip."""
    groups = {r["name"].casefold(): r for r in con.execute("SELECT id, name FROM document_groups")}
    types = {r["name"].casefold(): r for r in con.execute("SELECT id, name, active FROM qualification_types")}
    roles = {r["name"].casefold(): r for r in con.execute("SELECT id, name FROM profile_roles")}
    seen = set()
    for row in rows:
        row.update(error=None, warning=None, group_id=None, new_group=None, type_id=None, role_ids=[], file_key=None,
                   outcome=None, document_id=None)

        def fail(message):
            row["error"] = row["error"] or message
        if not row["title"]:
            fail("Title is required.")
        elif len(row["title"]) > 120:
            fail("Title can be at most 120 characters.")
        if len(row["description"]) > MAX_TEXT:
            fail(f"Description can be at most {MAX_TEXT} characters.")
        if row["group"]:
            hit = groups.get(row["group"].casefold())
            if hit:
                row["group_id"], row["group"] = hit["id"], hit["name"]
            elif len(row["group"]) > 80:
                fail("Group name can be at most 80 characters.")
            else:
                row["new_group"] = row["group"].casefold()
        try:
            start = qualifications.expiry(row["valid_from"] or None, "Valid from")
            until = qualifications.expiry(row["valid_until"] or None)
        except qualifications.QualificationError as exc:
            fail(str(exc))
            start = until = None
        if start and until and start > until:
            fail("Valid from must be on or before Valid until.")
        row["valid_from"], row["valid_until"] = start, until
        flag = row["admin_only"].casefold()
        if flag not in YES | NO:
            fail("admin_only must be yes or no.")
        row["admin_only"] = flag in YES
        if row["qualification"]:
            kind = types.get(row["qualification"].casefold())
            if kind is None:
                fail(f"No qualification type {row['qualification']!r}.")
            else:
                row["type_id"], row["qualification"] = kind["id"], kind["name"]
                if not kind["active"]:
                    row["warning"] = f"Qualification type {kind['name']!r} is inactive."
        for name in filter(None, (_text(part) for part in row["roles"].split(";"))):
            role = roles.get(name.casefold())
            if role is None:
                fail(f"No profile role {name!r}.")
            elif role["id"] not in row["role_ids"]:
                row["role_ids"].append(role["id"])
        if row["role_ids"] and not profiles.enabled():
            row["warning"] = row["warning"] or "Person profiles are off: only administrators will see it."
        key = qualification_import._lookup_file(row["file"], files, folder) if row["file"] else None
        if not row["file"]:
            fail("File is required.")
        elif key is None:
            fail(f"File not in the ZIP: {row['file']!r}.")
        elif not files[key]:
            fail("The file is empty.")
        elif len(files[key]) > MAX_BYTES:
            fail(f"The file is larger than {MAX_BYTES // 2**20} MiB.")
        else:
            row["file_key"], row["filename"] = key, Path(key).name.replace('"', "")[:200]
            row["sha256"] = hashlib.sha256(files[key]).hexdigest()
        place = (row["title"].casefold(), (row["group"] or "").casefold())
        if place in seen:
            fail("The same title and group appear twice in manifest.csv.")
        seen.add(place)
    return plan(con, rows)


def plan(con, rows):
    """Outcome per valid row: add, replace (an existing document with that title and group) or skip (unchanged)."""
    for row in rows:
        if row["error"]:
            continue
        existing = con.execute(
            "SELECT d.*, g.name AS group_name FROM documents d LEFT JOIN document_groups g ON g.id=d.group_id"
            " WHERE d.title=? COLLATE NOCASE AND COALESCE(g.name,'')=? COLLATE NOCASE ORDER BY d.id",
            (row["title"], row["group"] or "")).fetchall()
        if len(existing) > 1:
            row["error"] = "More than one document already has this title in this group; rename one first."
            continue
        if not existing:
            row["outcome"], row["document_id"] = "add", None
            continue
        doc = existing[0]
        row["document_id"] = doc["id"]
        roles = [r[0] for r in con.execute("SELECT role_id FROM document_profile_roles WHERE document_id=?"
                                           " ORDER BY role_id", (doc["id"],))]
        same = (doc["sha256"] == row.get("sha256") and doc["description"] == row["description"]
                and doc["valid_from"] == row["valid_from"] and doc["valid_until"] == row["valid_until"]
                and bool(doc["admin_only"]) == row["admin_only"] and doc["type_id"] == row["type_id"]
                and bool(doc["roles_limited"]) == bool(row["role_ids"]) and roles == sorted(row["role_ids"]))
        row["outcome"] = "skip" if same else "replace"
    return rows


PUBLIC = ("line", "title", "group", "new_group", "description", "valid_from", "valid_until", "admin_only",
          "qualification", "roles", "file", "outcome", "error", "warning")


def summary(rows):
    errors = sum(1 for row in rows if row["error"])
    count = lambda outcome: sum(1 for row in rows if not row["error"] and row["outcome"] == outcome)
    return dict(rows=[{key: row.get(key) for key in PUBLIC} for row in rows], errors=errors, added=count("add"),
                replaced=count("replace"), skipped=count("skip"),
                new_groups=len({row["new_group"] for row in rows if row["new_group"] and not row["error"]}),
                can_commit=errors == 0 and count("skip") < len(rows))


def _stage_root(app):
    root = Path(app.config["DOCUMENT_IMPORT_DIR"])
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    now = time.time()
    for path in root.glob("*/meta.json"):
        try:
            if now - json.loads(path.read_text()).get("created", 0) > STAGE_TTL_S:
                shutil.rmtree(path.parent, ignore_errors=True)
        except (OSError, ValueError, AttributeError):
            shutil.rmtree(path.parent, ignore_errors=True)
    return root


def store_preview(app, con, filename, raw):
    text, files, folder = split_upload(filename, raw)
    rows = resolve_rows(con, parse_csv(text), files, folder)
    token = uuid.uuid4().hex
    stage = _stage_root(app) / f"{g.user['id']}-{token}"
    stage.mkdir(mode=0o700)
    stored = []
    for index, row in enumerate(rows):
        item = {key: row.get(key) for key in PUBLIC + ("group_id", "type_id", "role_ids", "filename", "sha256")}
        if row["file_key"] and not row["error"] and row["outcome"] != "skip":
            (stage / f"{index}.bin").write_bytes(files[row["file_key"]])
            item["staged"] = f"{index}.bin"
        stored.append(item)
    (stage / "meta.json").write_text(json.dumps({"created": time.time(), "user_id": g.user["id"], "rows": stored}))
    session["doc_import_token"] = token
    return dict(summary(rows), token=token)


def _stage(app, token):
    if not token or token != session.get("doc_import_token"):
        raise DocumentError("Upload the ZIP again to preview it.")
    stage = Path(app.config["DOCUMENT_IMPORT_DIR"]) / f"{g.user['id']}-{token}"
    try:
        meta = json.loads((stage / "meta.json").read_text())
    except (OSError, ValueError):
        raise DocumentError("That import preview has expired. Upload the ZIP again.") from None
    if meta.get("user_id") != g.user["id"] or time.time() - meta.get("created", 0) > STAGE_TTL_S:
        shutil.rmtree(stage, ignore_errors=True)
        raise DocumentError("That import preview has expired. Upload the ZIP again.")
    return stage, meta["rows"]


def commit(app, con, token, documents_dir, now):
    """Import the staged preview: new groups, then each added or replaced document. All or nothing."""
    stage, rows = _stage(app, token)
    if any(row["error"] for row in rows):
        raise DocumentError("Fix the errors in manifest.csv before importing.")
    documents_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    written, old_files, done = [], [], dict(add=0, replace=0, skip=0)
    try:
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            for row in rows:  # the database may have changed since the preview
                row["error"] = None
                if row["group"] and not row["new_group"]:
                    hit = con.execute("SELECT id FROM document_groups WHERE id=?", (row["group_id"],)).fetchone()
                    if hit is None:
                        row["error"] = f"Group {row['group']!r} was deleted; upload the ZIP again."
            plan(con, rows)
            for row in rows:  # unchanged in the preview, so its file was not staged, but changed since
                if not row["error"] and row["outcome"] != "skip" and not row.get("staged"):
                    row["error"] = f"{row['title']!r} changed since the preview; upload the ZIP again."
            failed = next((row["error"] for row in rows if row["error"]), None)
            if failed:
                raise DocumentError(failed, 409)
            new_groups = {}
            for row in rows:
                if row["new_group"] and row["outcome"] != "skip" and row["new_group"] not in new_groups:
                    hit = con.execute("SELECT id FROM document_groups WHERE name=? COLLATE NOCASE",
                                      (row["group"],)).fetchone()
                    new_groups[row["new_group"]] = hit[0] if hit else con.execute(
                        "INSERT INTO document_groups(name,description,sort_order) VALUES (?,?,0)",
                        (row["group"], "")).lastrowid
            for row in rows:
                done[row["outcome"]] += 1
                if row["outcome"] == "skip":
                    continue
                with open(stage / row["staged"], "rb") as handle:
                    staged, compressed, size, _, sha256 = storage.save_attachment(handle, documents_dir, MAX_BYTES)
                stored = uuid.uuid4().hex + (".gz" if compressed else "")
                os.replace(staged, documents_dir / stored)
                written.append(documents_dir / stored)
                group_id = new_groups.get(row["new_group"], row["group_id"])
                fields = (row["title"], row["description"], group_id, row["type_id"], int(row["admin_only"]),
                          int(bool(row["role_ids"])), row["valid_from"], row["valid_until"], row["filename"], stored,
                          int(compressed), size, sha256, g.user["id"], now)
                if row["outcome"] == "add":
                    doc_id = con.execute(
                        "INSERT INTO documents(title,description,group_id,type_id,admin_only,roles_limited,valid_from,"
                        "valid_until,filename,stored_path,compressed,size_bytes,sha256,uploaded_by,uploaded_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", fields).lastrowid
                else:
                    doc_id = row["document_id"]
                    old_files.append(con.execute("SELECT stored_path FROM documents WHERE id=?",
                                                 (doc_id,)).fetchone()[0])
                    con.execute("UPDATE documents SET title=?,description=?,group_id=?,type_id=?,admin_only=?,"
                                "roles_limited=?,valid_from=?,valid_until=?,filename=?,stored_path=?,compressed=?,"
                                "size_bytes=?,sha256=?,uploaded_by=?,uploaded_at=? WHERE id=?", (*fields, doc_id))
                con.execute("DELETE FROM document_profile_roles WHERE document_id=?", (doc_id,))
                con.executemany("INSERT INTO document_profile_roles(document_id, role_id) VALUES (?,?)",
                                [(doc_id, role) for role in row["role_ids"]])
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    for name in old_files:
        (documents_dir / name).unlink(missing_ok=True)
    session.pop("doc_import_token", None)
    shutil.rmtree(stage, ignore_errors=True)
    return dict(added=done["add"], replaced=done["replace"], skipped=done["skip"], new_groups=len(new_groups))
