"""Admin bulk import of private qualifications from a CSV or a ZIP containing one."""
import csv
import io
import json
import os
import posixpath
import shutil
import time
import uuid
import zipfile
from pathlib import Path

from flask import g, session
from werkzeug.utils import secure_filename

import db
import qualifications
import storage

COLUMNS = ("person", "type", "valid_from", "valid_until", "file")
TEMPLATE = """person,type,valid_from,valid_until,file
Freek van Tienen,A2,2024-01-15,2026-01-14,Freek van Tienen/A2.pdf
"""
MAX_ROWS = 500
MAX_CSV_BYTES = 1_000_000
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_ZIP_FILES = 500
STAGE_TTL_S = 30 * 60


def person_keys(con):
    names = dict(qualifications.crew_keys(con))
    for user in con.execute("SELECT id, username FROM users"):
        key = user["username"].casefold()
        if key not in names:
            names[key] = user["id"]
    return names


def _strip(value):
    return (value or "").strip()


def parse_csv(text):
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")), strict=True)
    if (not reader.fieldnames or list(reader.fieldnames) != list(COLUMNS)
            or len(set(reader.fieldnames)) != len(reader.fieldnames)):
        raise qualifications.QualificationError(
            "CSV headers must be exactly: " + ",".join(COLUMNS) + ".")
    rows = []
    for raw in reader:
        if len(rows) >= MAX_ROWS:
            raise qualifications.QualificationError(f"Import at most {MAX_ROWS} rows at once.")
        if None in raw or any(value is None for value in raw.values()):
            raise qualifications.QualificationError(f"CSV line {reader.line_num}: incorrect number of columns.")
        body = {key: _strip(raw[key]) for key in COLUMNS}
        if not any(body.values()):
            continue
        body["line"] = reader.line_num
        rows.append(body)
    if not rows:
        raise qualifications.QualificationError("CSV has no qualification rows.")
    return rows


def _safe_member(name):
    path = name.replace("\\", "/")
    if path.startswith("/") or path.startswith("../") or "/../" in f"/{path}/" or path == "..":
        raise qualifications.QualificationError("ZIP contains an unsafe path.")
    return posixpath.normpath(path)


def _skip_member(name):
    base = name.split("/")[-1]
    return name.startswith("__MACOSX/") or base.startswith("._") or base in {".DS_Store", ""}


def read_zip(raw):
    files = {}
    total = 0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if len(archive.infolist()) > MAX_ZIP_FILES:
            raise qualifications.QualificationError(f"ZIP may contain at most {MAX_ZIP_FILES} files.")
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = _safe_member(info.filename)
            if _skip_member(name):
                continue
            size = info.file_size
            total += size
            if total > MAX_UPLOAD_BYTES:
                raise qualifications.QualificationError("Uncompressed import is larger than 200 MiB.")
            files[name] = archive.read(info)
    return files


def _manifest_from_files(files):
    named = [path for path in files if Path(path).name.casefold() == "manifest.csv"]
    if not named:
        csvs = [path for path in files if path.casefold().endswith(".csv")]
        if len(csvs) == 1:
            named = csvs
    if not named:
        raise qualifications.QualificationError("ZIP must contain manifest.csv.")
    named.sort(key=lambda path: (path.count("/"), path))
    path = named[0]
    try:
        text = files[path].decode("utf-8-sig")
    except UnicodeError as exc:
        raise qualifications.QualificationError("manifest.csv must be UTF-8.") from exc
    if len(files[path]) > MAX_CSV_BYTES:
        raise qualifications.QualificationError("CSV must be at most 1 MB.")
    return path, text, posixpath.dirname(path)


def split_upload(filename, raw):
    name = (filename or "").replace("\\", "/").split("/")[-1].casefold()
    if name.endswith(".zip"):
        files = read_zip(raw)
        manifest, text, folder = _manifest_from_files(files)
        files.pop(manifest, None)
        return text, files, folder
    if name.endswith(".csv"):
        if len(raw) > MAX_CSV_BYTES:
            raise qualifications.QualificationError("CSV must be at most 1 MB.")
        try:
            return raw.decode("utf-8-sig"), {}, ""
        except UnicodeError as exc:
            raise qualifications.QualificationError("CSV must be UTF-8.") from exc
    raise qualifications.QualificationError("Upload a UTF-8 CSV or a ZIP that contains manifest.csv.")


def _lookup_file(rel, files, folder):
    if not rel:
        return None
    rel = rel.replace("\\", "/")
    candidates = [posixpath.normpath(rel)]
    if folder:
        candidates.append(posixpath.normpath(posixpath.join(folder, rel)))
    by_cf = {}
    for path in files:
        key = path.casefold()
        by_cf[key] = None if key in by_cf else path
    for candidate in candidates:
        if candidate in files:
            return candidate
        hit = by_cf.get(candidate.casefold())
        if hit:
            return hit
    base = posixpath.basename(rel).casefold()
    suffix = [path for path in files if path.casefold() == base or path.casefold().endswith("/" + base)]
    if len(suffix) == 1:
        return suffix[0]
    return None


def resolve_rows(con, rows, files, folder=""):
    people = person_keys(con)
    types = {row["name"].casefold(): row for row in con.execute("SELECT * FROM qualification_types")}
    users = {row["id"]: row for row in con.execute("SELECT id, username, display_name FROM users")}
    out = []
    for raw in rows:
        item = dict(raw, error=None, warning=None, user_id=None, type_id=None, person_name=None,
                    category=None, evidence_key=None)
        uid = people.get(raw["person"].casefold()) if raw["person"] else None
        item["new_person"] = None
        if not raw["person"]:
            item["error"] = "Person is required."
        elif raw["person"].casefold() in people and not uid:
            item["error"] = f"Ambiguous person: {raw['person']!r}. Add an alias to choose who is meant."
        elif not uid:
            if len(raw["person"]) > 80 or not new_username(raw["person"]):
                item["error"] = f"Cannot add a person called {raw['person']!r}."
            else:
                item["new_person"] = raw["person"].casefold()
                item["person_name"] = raw["person"]
        else:
            item["user_id"] = uid
            user = users[uid]
            item["person_name"] = user["display_name"] or user["username"]
        kind = types.get(raw["type"].casefold()) if raw["type"] else None
        if not raw["type"]:
            item["error"] = item["error"] or "Type is required."
        elif kind is None:
            item["error"] = item["error"] or f"Unknown qualification type: {raw['type']!r}."
        elif not kind["active"]:
            item["error"] = item["error"] or f"Qualification type {kind['name']!r} is disabled."
        else:
            item["type_id"] = kind["id"]
            item["category"] = kind["name"]
        try:
            start = qualifications.expiry(raw["valid_from"] or None, "Valid from")
            until = qualifications.expiry(raw["valid_until"] or None)
        except qualifications.QualificationError as exc:
            item["error"] = item["error"] or str(exc)
            start = until = None
        if start and until and start > until:
            item["error"] = item["error"] or "Valid from must be on or before Valid until."
        item["valid_from"], item["valid_until"] = start, until
        if kind and kind["requires_start"] and not start:
            item["error"] = item["error"] or "This qualification requires a Valid from date."
        if kind and kind["requires_expiry"] and not until:
            item["error"] = item["error"] or "This qualification requires an expiry date."
        key = _lookup_file(raw["file"], files, folder) if raw["file"] else None
        if raw["file"] and key is None:
            item["error"] = item["error"] or (
                f"Evidence file not in the ZIP: {raw['file']!r}." if files
                else "Evidence files must be uploaded in a ZIP with manifest.csv.")
        elif key:
            payload = files[key]
            if len(payload) > qualifications.MAX_EVIDENCE_BYTES:
                item["error"] = item["error"] or "Evidence must be non-empty and at most 20 MiB."
            else:
                item["evidence_key"] = key
                item["filename"] = Path(key).name
        if kind and kind["requires_evidence"] and not item.get("evidence_key"):
            item["error"] = item["error"] or "This qualification requires evidence."
        out.append(item)
    return plan(con, out)


def new_username(name):
    """Same convention as existing accounts: the display name without spaces."""
    return "".join(ch for ch in name if not ch.isspace() and ch.isprintable())[:40]


def add_people(con, rows):
    """Create a profile, without password or email, for each new name in the import."""
    created = {}
    people = person_keys(con)
    for row in rows:
        key = row.get("new_person")
        if not key:
            continue
        if key not in created:
            uid = people.get(key)
            if uid is None:
                if key in people:
                    raise qualifications.QualificationError(f"Ambiguous person: {row['person']!r}.", 409)
                base = name = new_username(row["person"])
                suffix = 2
                while con.execute("SELECT 1 FROM users WHERE username=? COLLATE NOCASE", (name,)).fetchone():
                    name = f"{base[:38]}{suffix}"
                    suffix += 1
                uid = con.execute("INSERT INTO users(username,display_name,created_at) VALUES (?,?,?)",
                                  (name, row["person"], qualifications._utc_now())).lastrowid
            created[key] = uid
        row["user_id"] = created[key]
    return len([key for key in created if key not in people])


def _period(row):
    return (row["valid_until"] or "9999-12-31", row["valid_from"] or "")


def plan(con, rows):
    """Per person and type, the newest new period becomes current; older ones are archived.
    Periods already recorded (or repeated in the file) are skipped, so re-importing is harmless."""
    groups = {}
    for row in rows:
        row["outcome"] = None
        if not row.get("error"):
            groups.setdefault((row["user_id"] or row["new_person"], row["type_id"]), []).append(row)
    for pair, group in groups.items():
        if not isinstance(pair[0], int):
            pair = (0, pair[1])
        known = {(r["valid_from"], r["valid_until"]) for r in con.execute(
            "SELECT valid_from,valid_until FROM qualification_submissions"
            " WHERE user_id=? AND type_id=? AND status IN ('approved','superseded')", pair)}
        current = con.execute("SELECT valid_from,valid_until FROM qualification_submissions"
                              " WHERE user_id=? AND type_id=? AND status='approved'", pair).fetchone()
        fresh = []
        for row in group:
            period = (row["valid_from"], row["valid_until"])
            row["outcome"] = "skip" if period in known else "archive"
            row["warning"] = "Already recorded; skipped." if period in known else None
            if period not in known:
                known.add(period)
                fresh.append(row)
        newest = max(fresh, key=_period, default=None)
        if newest and (current is None or _period(newest) > _period(current)):
            newest["outcome"] = "current"
            if current is not None:
                newest["warning"] = "Moves the current approval of this type to Archive."
            if con.execute("SELECT 1 FROM qualification_submissions WHERE user_id=? AND type_id=? AND status='pending'",
                           pair).fetchone():
                newest["error"] = "There is already a pending submission for this type. Withdraw it first."
    return rows


def public_rows(rows):
    keys = ("line", "person", "person_name", "new_person", "type", "category", "valid_from", "valid_until",
            "file", "outcome", "error", "warning")
    return [{key: row.get(key) for key in keys} for row in rows]


def preview(con, filename, raw):
    text, files, folder = split_upload(filename, raw)
    rows = resolve_rows(con, parse_csv(text), files, folder)
    return rows, files


def _stage_root(app):
    root = Path(app.config["QUALIFICATION_IMPORT_DIR"])
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    now = time.time()
    for path in root.glob("*/meta.json"):
        try:
            meta = json.loads(path.read_text())
            if now - meta.get("created", 0) > STAGE_TTL_S:
                shutil.rmtree(path.parent, ignore_errors=True)
        except (OSError, json.JSONDecodeError, TypeError):
            shutil.rmtree(path.parent, ignore_errors=True)
    return root


def store_preview(app, con, filename, raw):
    rows, files = preview(con, filename, raw)
    token = uuid.uuid4().hex
    folder = _stage_root(app) / f"{g.user['id']}-{token}"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    stored = []
    for index, row in enumerate(rows):
        item = {key: row.get(key) for key in (
            "line", "person", "user_id", "new_person", "type_id", "category", "valid_from", "valid_until",
            "file", "filename", "error", "warning")}
        key = row.get("evidence_key")
        if key and not row.get("error") and row["outcome"] != "skip":
            dest = folder / f"{index}.bin"
            dest.write_bytes(files[key])
            item["evidence_file"] = dest.name
            item["filename"] = row["filename"]
        stored.append(item)
    (folder / "meta.json").write_text(json.dumps(
        {"created": time.time(), "user_id": g.user["id"], "rows": stored}))
    session["qual_import_token"] = token
    errors = sum(1 for row in rows if row.get("error"))
    count = lambda outcome: sum(1 for row in rows if not row.get("error") and row["outcome"] == outcome)
    return dict(token=token, rows=public_rows(rows), ok=len(rows) - errors, errors=errors,
                current=count("current"), archived=count("archive"), skipped=count("skip"),
                new_people=len({row["new_person"] for row in rows if row["new_person"] and not row.get("error")}),
                can_commit=errors == 0 and count("skip") < len(rows))


def _stage_folder(app, token):
    if not token or token != session.get("qual_import_token"):
        raise qualifications.QualificationError("Upload the CSV or ZIP again to preview it.")
    folder = Path(app.config["QUALIFICATION_IMPORT_DIR"]) / f"{g.user['id']}-{token}"
    meta_path = folder / "meta.json"
    if not meta_path.is_file():
        raise qualifications.QualificationError("That import preview has expired. Upload the file again.")
    meta = json.loads(meta_path.read_text())
    if meta.get("user_id") != g.user["id"] or time.time() - meta.get("created", 0) > STAGE_TTL_S:
        shutil.rmtree(folder, ignore_errors=True)
        raise qualifications.QualificationError("That import preview has expired. Upload the file again.")
    return folder, meta


def commit(app, con, token):
    folder, meta = _stage_folder(app, token)
    rows = meta["rows"]
    if any(row.get("error") for row in rows):
        raise qualifications.QualificationError("Fix the CSV errors before importing.")
    evidence_root = Path(app.config["QUALIFICATION_EVIDENCE_DIR"])
    evidence_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    written = []
    imported = []
    staged = None
    try:
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            added = add_people(con, rows)
            plan(con, rows)
            failed = next((row["error"] for row in rows if row.get("error")), None)
            if failed:
                raise qualifications.QualificationError(failed, 409)
            for row in rows:
                if row["outcome"] == "skip":
                    continue
                kind = con.execute("SELECT * FROM qualification_types WHERE id=? AND active=1",
                                   (row["type_id"],)).fetchone()
                if kind is None:
                    raise qualifications.QualificationError("Choose an active qualification type.")
                destination = None
                filename = mime = None
                compressed, size = False, 0
                if row.get("evidence_file"):
                    source = folder / row["evidence_file"]
                    filename = secure_filename(row.get("filename") or "evidence.pdf")[:160]
                    if not filename:
                        raise qualifications.QualificationError("Choose an evidence filename.")
                    with open(source, "rb") as handle:
                        staged, compressed, size, _, _ = storage.save_attachment(
                            handle, evidence_root, qualifications.MAX_EVIDENCE_BYTES)
                    mime = qualifications.evidence_format(staged, compressed, filename)
                    destination = evidence_root / (uuid.uuid4().hex + (".gz" if compressed else ".bin"))
                    os.replace(staged, destination)
                    staged = None
                    written.append(destination)
                if kind["requires_evidence"] and destination is None:
                    raise qualifications.QualificationError("This qualification requires evidence.")
                current = row["outcome"] == "current"
                if current:
                    con.execute("UPDATE qualification_submissions SET status='superseded'"
                                " WHERE user_id=? AND type_id=? AND status='approved'",
                                (row["user_id"], row["type_id"]))
                stamp = qualifications._utc_now()
                sid = con.execute(
                    "INSERT INTO qualification_submissions(user_id,type_id,valid_from,valid_until,status,submitted_by,"
                    "submitted_at,reviewed_by,reviewed_at,evidence_path,filename,mime,compressed,size_bytes)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (row["user_id"], row["type_id"], row["valid_from"], row["valid_until"],
                     "approved" if current else "superseded", g.user["id"], stamp,
                     g.user["id"], stamp, destination.name if destination else None, filename, mime,
                     int(compressed), size)).lastrowid
                qualifications.audit(con, row["user_id"], g.user["id"], "submitted",
                                     dict(id=sid, category=row["category"], valid_from=row["valid_from"],
                                          valid_until=row["valid_until"], has_evidence=bool(destination),
                                          source="csv-import"))
                qualifications.audit(con, row["user_id"], g.user["id"], "approved" if current else "archived",
                                     dict(id=sid, category=row["category"], valid_from=row["valid_from"],
                                          valid_until=row["valid_until"], source="csv-import"))
                imported.append(dict(id=sid, user_id=row["user_id"], person=row.get("person"),
                                     category=row["category"], outcome=row["outcome"]))
        session.pop("qual_import_token", None)
        shutil.rmtree(folder, ignore_errors=True)
        count = lambda outcome: sum(1 for row in rows if row["outcome"] == outcome)
        return dict(imported=len(imported), new_people=added, current=count("current"), archived=count("archive"),
                    skipped=count("skip"), ids=[row["id"] for row in imported], rows=imported)
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    finally:
        if staged:
            staged.unlink(missing_ok=True)
