"""Admin bulk import of documents: a ZIP with manifest.csv and the files (issue/PR 230, like the qualification import).

manifest.csv columns (title and file required, the rest optional, any order):
  title, group, description, valid_from, valid_until, admin_only, qualification, roles, file
- group: the name of a document group; an unknown name is created on confirmation (New group). Empty, or General
  when no group has that name: no group (the General tab).
- admin_only: yes/no (also 1/0, true/false); empty is no.
- qualification: names of existing qualification types separated by ";" (the document is then only for people
  with a current qualification of one of them).
- roles: profile role names separated by ";" (the document is then only for those roles).
A row whose title and group match an existing document replaces its file and settings (Replace); with the same
file and settings it is skipped, so a ZIP can be imported again, for example with renewed certificates. A column
left out of the header keeps the existing document's value; the preview lists what changes, and limits removed.
File paths are matched exactly (case-insensitive); a bare file name may also match one file in a subfolder.
The preview is staged on disk for 30 minutes and committed with its token from the same browser session.
"""
import csv
import hashlib
import io
import json
import os
import posixpath
import shutil
import time
import uuid
import zipfile
from collections import Counter
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
Specific category authorisation,Operations,,2026-03-01,2028-02-28,no,A2;STS,,authorisation.pdf
"""
MAX_ROWS = 500
STAGE_TTL_S = 30 * 60
YES, NO = {"yes", "y", "1", "true", "ja"}, {"no", "n", "0", "false", "nee", ""}
# columns that keep the existing document's value when they are left out of the header, and the row keys they set
KEPT = {"description": ("description",), "valid_from": ("valid_from",), "valid_until": ("valid_until",),
        "admin_only": ("admin_only",), "qualification": ("type_ids",), "roles": ("role_ids", "roles_limited")}
CHANGE_LABELS = (("description", "description"), ("valid_from", "valid from"), ("valid_until", "valid until"),
                 ("admin_only", "administrators only"), ("type_ids", "qualification"), ("role_ids", "roles"),
                 ("roles_limited", "roles"))


def _text(value):
    return " ".join((value or "").split())


def _names(items):
    return [part for part in (_text(item) for item in items.split(";")) if part]


def parse_csv(text):
    """Rows of manifest.csv; each has every column ("" when absent), its line and the columns present."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")), strict=True)
    try:
        return _parse_rows(reader)
    except csv.Error as exc:
        raise DocumentError(f"manifest.csv line {reader.line_num}: {exc}.") from None


def _parse_rows(reader):
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
        body["file"] = (raw.get("file") or "").strip()  # file names may have double spaces
        if any(body.values()):
            body["line"], body["present"] = reader.line_num, names
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
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = Counter(posixpath.normpath(i.filename.replace("\\", "/")) for i in archive.infolist()
                            if not i.is_dir())
        manifest, text, folder = qualification_import._manifest_from_files(files)
    except qualifications.QualificationError as exc:
        raise DocumentError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - zipfile raises several kinds for a broken archive
        raise DocumentError("The ZIP could not be read.") from exc
    twice = sorted(name for name, count in names.items() if count > 1)
    if twice:
        raise DocumentError(f"The ZIP has more than one file named {twice[0]!r}.")
    files.pop(manifest, None)
    return text, files, folder


def lookup_file(rel, files, folder):
    """The ZIP entry for a manifest path. A path with a folder must match that folder: a/x.pdf never finds b/x.pdf."""
    key = qualification_import._lookup_file(rel, files, folder)
    wanted = posixpath.normpath(rel.replace("\\", "/").strip("/")).casefold()
    if key and "/" in wanted and not (key.casefold() == wanted or key.casefold().endswith("/" + wanted)):
        return None
    return key


def resolve_rows(con, rows, files, folder=""):
    """Check every row against the database and the ZIP; each gets error/warning and outcome add/replace/skip."""
    groups = {r["name"].casefold(): r for r in con.execute("SELECT id, name FROM document_groups")}
    types = {r["name"].casefold(): r for r in con.execute("SELECT id, name, active FROM qualification_types")}
    roles = {r["name"].casefold(): r for r in con.execute("SELECT id, name FROM profile_roles")}
    seen = set()
    for row in rows:
        row.update(error=None, warning=None, group_id=None, new_group=None, type_ids=[], role_ids=[], file_key=None,
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
            elif row["group"].casefold() == "general":  # the General tab: documents without a group
                row["group"] = ""
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
        for name in _names(row["qualification"]):
            kind = types.get(name.casefold())
            if kind is None:
                fail(f"No qualification type {name!r}.")
            elif kind["id"] not in row["type_ids"]:
                row["type_ids"].append(kind["id"])
                if not kind["active"]:
                    row["warning"] = row["warning"] or f"Qualification type {kind['name']!r} is inactive."
        for name in _names(row["roles"]):
            role = roles.get(name.casefold())
            if role is None:
                fail(f"No profile role {name!r}.")
            elif role["id"] not in row["role_ids"]:
                row["role_ids"].append(role["id"])
        row["type_ids"].sort()
        row["role_ids"].sort()
        row["roles_limited"] = bool(row["role_ids"])
        key = lookup_file(row["file"], files, folder) if row["file"] else None
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
    """Outcome per valid row: add, replace (an existing document with that title and group) or skip (unchanged).
    For a replace, columns left out of the header take the document's values, and the row lists what changes
    (changes), which limits it removes or widens (removes), and a fingerprint of the document values it overwrites,
    so the commit can refuse a document edited since the preview."""
    type_names = {r[0]: r[1] for r in con.execute("SELECT id, name FROM qualification_types")}
    role_names = {r[0]: r[1] for r in con.execute("SELECT id, name FROM profile_roles")}
    for row in rows:
        row.update(outcome=None, document_id=None, changes=[], removes=[], fingerprint=None)
        if row["error"]:
            continue
        existing = con.execute(
            "SELECT d.*, g.name AS group_name FROM documents d LEFT JOIN document_groups g ON g.id=d.group_id"
            " WHERE d.title=? COLLATE NOCASE AND COALESCE(g.name,'')=? COLLATE NOCASE ORDER BY d.id",
            (row["title"], row["group"] or "")).fetchall()
        if len(existing) > 1:
            row["error"] = "More than one document already has this title in this group; rename one first."
            continue
        if existing:
            doc = existing[0]
            old = dict(description=doc["description"], valid_from=doc["valid_from"], valid_until=doc["valid_until"],
                       admin_only=bool(doc["admin_only"]), roles_limited=bool(doc["roles_limited"]),
                       type_ids=[r[0] for r in con.execute("SELECT type_id FROM document_qualification_types"
                                                           " WHERE document_id=? ORDER BY type_id", (doc["id"],))],
                       role_ids=[r[0] for r in con.execute("SELECT role_id FROM document_profile_roles"
                                                           " WHERE document_id=? ORDER BY role_id", (doc["id"],))])
            for column, keys in KEPT.items():
                if column not in row["present"]:
                    row.update({key: old[key] for key in keys})
            if row["valid_from"] and row["valid_until"] and row["valid_from"] > row["valid_until"]:
                row["error"] = "Valid from must be on or before Valid until (with the document's other date)."
                continue
            changes = ["file"] if doc["sha256"] != row.get("sha256") else []
            for key, label in CHANGE_LABELS:
                if row[key] != old[key] and label not in changes:
                    changes.append(label)
            if old["admin_only"] and not row["admin_only"]:
                row["removes"].append("administrators only")
            if old["type_ids"] and (not row["type_ids"] or set(row["type_ids"]) - set(old["type_ids"])):
                row["removes"].append("qualification")
            if old["roles_limited"] and (not row["roles_limited"] or set(row["role_ids"]) - set(old["role_ids"])):
                row["removes"].append("roles")
            row.update(document_id=doc["id"], changes=changes, outcome="replace" if changes else "skip")
            if changes:  # what the replace overwrites: the columns in the header and the file
                state = {key: old[key] for column, keys in KEPT.items() if column in row["present"] for key in keys}
                state.update(sha256=doc["sha256"], filename=doc["filename"], uploaded_at=doc["uploaded_at"])
                row["fingerprint"] = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
        else:
            row["outcome"] = "add"
        if row["role_ids"] and not profiles.enabled():  # after kept roles are filled in
            row["warning"] = row["warning"] or "Person profiles are off: only administrators will see it."
        row["qualification"] = "; ".join(type_names.get(t, "?") for t in row["type_ids"])
        row["roles"] = "; ".join(role_names.get(r, "?") for r in row["role_ids"])
    return rows


PUBLIC = ("line", "title", "group", "new_group", "description", "valid_from", "valid_until", "admin_only",
          "qualification", "roles", "file", "path", "outcome", "changes", "removes", "error", "warning")


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
        row["path"] = row["file_key"]
        item = {key: row.get(key) for key in PUBLIC + ("group_id", "type_ids", "role_ids", "roles_limited", "present",
                                                        "filename", "sha256", "document_id")}
        item.update(preview_outcome=row["outcome"], preview_document_id=row["document_id"],
                    preview_fingerprint=row["fingerprint"])
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
    """Import the staged preview: new groups, then each added or replaced document. All or nothing.
    The files are stored before the database transaction, so it stays short; a failure removes them again."""
    stage, rows = _stage(app, token)
    if any(row["error"] for row in rows):
        raise DocumentError("Fix the errors in manifest.csv before importing.")
    documents_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    written, old_files, done, prepared = [], [], dict(add=0, replace=0, skip=0), {}
    try:
        for index, row in enumerate(rows):
            if row.get("staged"):
                with open(stage / row["staged"], "rb") as handle:
                    staged, compressed, size, _, sha256 = storage.save_attachment(handle, documents_dir, MAX_BYTES)
                stored = uuid.uuid4().hex + (".gz" if compressed else "")
                os.replace(staged, documents_dir / stored)
                written.append(documents_dir / stored)
                prepared[index] = (stored, int(compressed), size, sha256)
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            for row in rows:  # the database may have changed since the preview
                row["error"] = None
                if row["group"] and not row["new_group"]:
                    hit = con.execute("SELECT id FROM document_groups WHERE id=?", (row["group_id"],)).fetchone()
                    if hit is None:
                        row["error"] = f"Group {row['group']!r} was deleted; upload the ZIP again."
            plan(con, rows)
            for row in rows:  # for example edited, deleted, or its group renamed since the preview
                if not row["error"] and ((row["outcome"], row["document_id"], row["fingerprint"]) != (
                        row["preview_outcome"], row["preview_document_id"], row["preview_fingerprint"])):
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
            for index, row in enumerate(rows):
                done[row["outcome"]] += 1
                if row["outcome"] == "skip":
                    continue
                group_id = new_groups.get(row["new_group"], row["group_id"])
                fields = (row["title"], row["description"], group_id, int(row["admin_only"]),
                          int(row["roles_limited"]), row["valid_from"], row["valid_until"], row["filename"],
                          *prepared[index], g.user["id"], now)
                if row["outcome"] == "add":
                    doc_id = con.execute(
                        "INSERT INTO documents(title,description,group_id,admin_only,roles_limited,valid_from,"
                        "valid_until,filename,stored_path,compressed,size_bytes,sha256,uploaded_by,uploaded_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", fields).lastrowid
                else:
                    doc_id = row["document_id"]
                    old_files.append(con.execute("SELECT stored_path FROM documents WHERE id=?",
                                                 (doc_id,)).fetchone()[0])
                    con.execute("UPDATE documents SET title=?,description=?,group_id=?,admin_only=?,roles_limited=?,"
                                "valid_from=?,valid_until=?,filename=?,stored_path=?,compressed=?,size_bytes=?,"
                                "sha256=?,uploaded_by=?,uploaded_at=? WHERE id=?", (*fields, doc_id))
                con.execute("DELETE FROM document_profile_roles WHERE document_id=?", (doc_id,))
                con.executemany("INSERT INTO document_profile_roles(document_id, role_id) VALUES (?,?)",
                                [(doc_id, role) for role in row["role_ids"]])
                con.execute("DELETE FROM document_qualification_types WHERE document_id=?", (doc_id,))
                con.executemany("INSERT INTO document_qualification_types(document_id, type_id) VALUES (?,?)",
                                [(doc_id, kind) for kind in row["type_ids"]])
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    for name in old_files:
        (documents_dir / name).unlink(missing_ok=True)
    session.pop("doc_import_token", None)
    shutil.rmtree(stage, ignore_errors=True)
    return dict(added=done["add"], replaced=done["replace"], skipped=done["skip"], new_groups=len(new_groups))
