"""Web interface to upload flight logs (Paparazzi pairs, PX4 .ulg, ArduPilot, MAVLink tlog, zips).

Uploaded logs are kept on disk, hashed (sha256 of the main log file) and parsed into a SQLite
database. Paparazzi .data files go through the C++ parser; PX4/ArduPilot/tlog files go through
the Python mav parser. A log with a known hash is never stored or parsed twice. The version of
the parser that produced the stored result is recorded so outdated results can be re-parsed later.
"""
import contextlib
import copy
import csv
import errno
import hashlib
import difflib
import functools
import gzip
import io
import json
import math
import os
import posixpath
import queue
import re
import secrets
import sys
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import (Flask, Response, abort, g, jsonify, redirect, request, send_from_directory, session,
                   stream_with_context)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

if __name__ == "__main__" and sys.argv[1:2] == ["check-config"]:
    import config
    sys.exit(1 if config.check() else 0)

import db
import attachments
import historical
import storage
import saml
import config
import qualifications
import weather
import mav_parse
from config import env, env_bool, env_float, env_int, env_path

HERE = Path(__file__).resolve().parent
DATA_DIR = env_path("WEB_DATA")
STORAGE_DIR = config.log_dir()
STAGE_DIR = STORAGE_DIR / ".incoming"  # legacy queued uploads remain readable after upgrading
LOCAL_STAGE_DIR = DATA_DIR / "incoming"
TMP_DIR = DATA_DIR / "tmp"
DB_PATH = str(DATA_DIR / "logs.db")
FASTREADER = str(env_path("FASTREADER"))
PATH_RATE = env_float("PATH_RATE")
# Parser binaries run in parallel; SQLite writes are still one-at-a-time (db.write_lock).
WORKERS = env_int("WORKERS")
INGEST_WORKERS = env_int("INGEST_WORKERS")

PAGES_DIR = HERE / "static"
# Pages are served by explicit routes so nothing is reachable without logging in (except the login page)
app = Flask(__name__, static_folder=None)
if env_int("PROXIES"):
    n = env_int("PROXIES")
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)


def load_secret_key():
    """Session signing key, generated once and kept in the data directory."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    key_file = DATA_DIR / "secret_key"
    if not key_file.exists():
        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    return key_file.read_text().strip()


app.secret_key = env("SECRET_KEY") or load_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=env_bool("COOKIE_SECURE"),  # set to 1 when served over https
    PERMANENT_SESSION_LIFETIME=env_int("SESSION_HOURS") * 3600,
    MAX_CONTENT_LENGTH=None,
)
jobs: "queue.Queue[int]" = queue.Queue()
ingest_jobs: "queue.Queue[int]" = queue.Queue()
HAVE_PATH_GZ = False  # older databases kept each flight path on the flight row


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def conn():
    return db.connect(DB_PATH)


def parser_version():
    """Version reported by the installed parser binary (None if it is missing)."""
    try:
        return subprocess.run([FASTREADER, "--version"], capture_output=True, text=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8 << 20):
            h.update(chunk)
    return h.hexdigest()


def clean_relpath(name):
    """Upload path without drive, leading slashes or '..' parts."""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    return "/".join(parts)


def write_atomic(path, data):
    """Write a file so readers (and backups of a network share) never see it half-written."""
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


@functools.lru_cache(maxsize=32)
def load_paths(paths_file):
    """All flight paths of one log. File names change on every parse, so the cache never goes stale."""
    try:
        return db.unpack((STORAGE_DIR / paths_file).read_bytes()) or []
    except FileNotFoundError:
        return []


def flight_path(paths_file, idx, days=0, path_gz=None):
    """One flight's path, with its times shifted by `days` (a copy, so the cached paths stay unchanged)."""
    path = []
    if paths_file:
        paths = load_paths(paths_file)
        if idx is not None and 0 <= idx < len(paths):
            path = paths[idx]
    if not path and path_gz:
        path = db.unpack(path_gz) or []
    return apply_clock_to_obj(copy.deepcopy(path), days) if days else path


# ---------------------------------------------------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------------------------------------------------
def parse_log(log_id):
    con = conn()
    row = con.execute("SELECT * FROM logs WHERE id=?", (log_id,)).fetchone()
    if row is None:
        return

    def mark_parsing():
        con.execute("UPDATE logs SET status='parsing', error=NULL WHERE id=?", (log_id,))
        con.commit()

    db.retry_locked(mark_parsing, con)
    out_path = new_paths = None
    unpacked = None
    try:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        unpacked = tempfile.TemporaryDirectory(dir=TMP_DIR, prefix="parse-")
        for rel in (row["data_path"], row["log_path"]):
            if rel:
                if not rel.endswith(".gz"):
                    raise RuntimeError("Uncompressed legacy log storage is unsupported; delete and re-upload this test log.")
                with gzip.open(STORAGE_DIR / rel, "rb") as src:
                    storage.copy_local(src, Path(unpacked.name) / Path(rel).name[:-3],
                                       env_int("LOCAL_MIN_FREE_BYTES"))
        data_path = Path(unpacked.name) / Path(row["data_path"]).name[:-3]
        kind = mav_parse.sniff(data_path)
        if kind:
            doc = mav_parse.parse(data_path, sha256=row["sha256"], path_rate=PATH_RATE, log_type=kind)
        else:
            cmd = [FASTREADER, "-r", str(PATH_RATE), "--sha256", row["sha256"], str(data_path)]
            fd, out_path = tempfile.mkstemp(dir=TMP_DIR, suffix=".json")
            with os.fdopen(fd, "wb") as out:
                proc = subprocess.run(cmd, stdout=out, stderr=subprocess.PIPE)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.decode(errors="replace").strip() or f"parser exit code {proc.returncode}")
            with open(out_path, "rb") as f:
                doc = json.load(f)
            os.remove(out_path)
            out_path = None
        seconds = time.time() - t0
        paths_rel = str(Path(row["data_path"]).parent / f"paths-{uuid.uuid4().hex[:12]}.json.gz")
        write_atomic(STORAGE_DIR / paths_rel, db.pack(db.flight_paths(doc)))
        new_paths = STORAGE_DIR / paths_rel
        old_paths = None

        def save():
            nonlocal old_paths
            with db.write_lock:
                con.execute("BEGIN IMMEDIATE")
                old_paths = con.execute("SELECT paths_file FROM logs WHERE id=?", (log_id,)).fetchone()[0]
                kept = snapshot_flight_edits(con, log_id)
                kept_files = attachments.snapshot(con, log_id)
                db.store_parsed(con, log_id, doc, paths_rel)
                link_log_aircraft(con, log_id)
                assign_log_flights(con, log_id, kept)
                attachments.restore(con, log_id, kept_files)
                con.execute("UPDATE logs SET status='done', error=NULL, parsed_at=?, parse_seconds=? WHERE id=?",
                            (now(), round(seconds, 3), log_id))
                con.commit()

        db.retry_locked(save, con)
        new_paths = None
        if old_paths and old_paths != paths_rel:
            (STORAGE_DIR / old_paths).unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001 - any failure is reported on the log row
        try:
            con.rollback()
        except sqlite3.Error:
            pass
        if new_paths is not None:
            new_paths.unlink(missing_ok=True)

        def mark_error():
            con.execute("UPDATE logs SET status='error', error=? WHERE id=?", (str(e)[:2000], log_id))
            con.commit()

        db.retry_locked(mark_error, con)
    finally:
        if unpacked is not None:
            unpacked.cleanup()
        if out_path and os.path.exists(out_path):
            os.remove(out_path)


def worker():
    while True:
        log_id = jobs.get()
        try:
            parse_log(log_id)
        finally:
            jobs.task_done()


def start_workers():
    global HAVE_PATH_GZ
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_STAGE_DIR.mkdir(parents=True, exist_ok=True)
    db.init(DB_PATH)
    con = conn()
    HAVE_PATH_GZ = "path_gz" in {r[1] for r in con.execute("PRAGMA table_info(flights)")}
    seed_drone_projects_from_defaults(con)
    assign_open_flights(con)
    con.execute("UPDATE logs SET status='queued' WHERE status IN ('queued', 'parsing')")
    con.execute("UPDATE ingest SET status='accepted' WHERE status='hashing'")
    con.commit()
    for r in con.execute("SELECT id FROM logs WHERE status='queued' ORDER BY id"):
        jobs.put(r["id"])
    for r in con.execute("SELECT id FROM ingest WHERE status='accepted' ORDER BY id"):
        ingest_jobs.put(r["id"])
    for _ in range(max(1, WORKERS)):
        threading.Thread(target=worker, daemon=True).start()
    for _ in range(max(1, INGEST_WORKERS)):
        threading.Thread(target=ingest_worker, daemon=True).start()


# ---------------------------------------------------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------------------------------------------------
def safe_filename(name):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name) or "log"


def store_files(sha, name, data_src, log_src):
    """Compress a staged log into permanent storage, returning relative paths."""
    folder = Path(sha[:2]) / sha
    (STORAGE_DIR / folder).mkdir(parents=True, exist_ok=True)
    base = safe_filename(posixpath.basename(name))
    data_rel = str(folder / (base + ".gz"))
    log_rel = None
    storage.gzip_file(data_src, STORAGE_DIR / data_rel, work_dir=TMP_DIR,
                      minimum_free=env_int("LOCAL_MIN_FREE_BYTES"))
    if log_src:
        log_rel = str(folder / (posixpath.splitext(base)[0] + ".log.gz"))
        storage.gzip_file(log_src, STORAGE_DIR / log_rel, work_dir=TMP_DIR,
                          minimum_free=env_int("LOCAL_MIN_FREE_BYTES"))
    return data_rel, log_rel


def ingest_stage(rel):
    if Path(rel).parts[0] == "local":
        return LOCAL_STAGE_DIR / Path(rel).relative_to("local")
    return STAGE_DIR / rel


def new_stage_rel():
    return str(Path("local") / uuid.uuid4().hex)


def drop_stage(stage):
    """Delete a staging directory and its parents in STAGE_DIR once they are empty."""
    shutil.rmtree(stage, ignore_errors=True)
    for parent in stage.parents:
        if parent in (STAGE_DIR, LOCAL_STAGE_DIR):
            break
        try:
            parent.rmdir()
        except OSError:
            break


def insert_ingest(con, kind, name, stage_rel, data_name, log_name, size_bytes, user_id, reservation=None):
    def go():
        with db.write_lock:
            con.execute("BEGIN IMMEDIATE")
            cur = con.execute(
                "INSERT INTO ingest (kind, name, stage_dir, data_name, log_name, size_bytes, status,"
                " uploaded_by, uploaded_at) VALUES (?,?,?,?,?,?,'accepted',?,?)",
                (kind, name, stage_rel, data_name, log_name, size_bytes, user_id, now()))
            if reservation:
                con.execute("UPDATE bulk_reservations SET ingest_id=?,uploading=0 WHERE token=?",
                            (cur.lastrowid, reservation))
            con.commit()
            return cur.lastrowid

    return db.retry_locked(go, con)


def set_ingest(con, ingest_id, **fields):
    cols = ", ".join(f"{k}=?" for k in fields)
    def go():
        with db.write_lock:
            con.execute("BEGIN IMMEDIATE")
            con.execute(f"UPDATE ingest SET {cols} WHERE id=?", (*fields.values(), ingest_id))
            con.commit()

    db.retry_locked(go, con)


def ingest_worker():
    while True:
        ingest_id = ingest_jobs.get()
        try:
            run_ingest(ingest_id)
        except Exception as e:  # noqa: BLE001 - any failure is recorded on the ingest row
            try:
                set_ingest(conn(), ingest_id, status="error", error=str(e)[:2000])
            except Exception:
                pass
        finally:
            ingest_jobs.task_done()


def run_ingest(ingest_id):
    con = conn()
    row = con.execute("SELECT * FROM ingest WHERE id=?", (ingest_id,)).fetchone()
    if row is None or row["status"] not in ("accepted", "hashing"):
        return
    set_ingest(con, ingest_id, status="hashing", error=None)
    if row["kind"] == "zip":
        explode_zip(con, row)
    else:
        try:
            ingest_pair(con, row)
        except OSError:
            drop_stage(ingest_stage(row["stage_dir"]))
            raise


def enqueue_staged_log(con, parent_stage, name, src, user_id, companion=None):
    """Move a staged log (and optional Paparazzi .log twin) into its own ingest row."""
    pair_rel = str(Path(parent_stage) / uuid.uuid4().hex)
    pair_dir = ingest_stage(pair_rel)
    pair_dir.mkdir(parents=True, exist_ok=True)
    if companion:
        data_name, log_name = "log.data", "log.log"
        shutil.move(str(src), pair_dir / data_name)
        shutil.move(str(companion), pair_dir / log_name)
        kind = "pair"
    else:
        data_name, log_name = "log" + Path(name).suffix.lower(), None
        shutil.move(str(src), pair_dir / data_name)
        kind = "file"
    size = (pair_dir / data_name).stat().st_size
    iid = insert_ingest(con, kind, name, pair_rel, data_name, log_name, size, user_id)
    ingest_jobs.put(iid)
    return iid, size


def explode_zip(con, row):
    """Unpack a zip in the background and enqueue Paparazzi pairs plus PX4/ArduPilot/tlog files."""
    stage = ingest_stage(row["stage_dir"])
    zip_path = stage / row["data_name"]
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        set_ingest(con, row["id"], status="error", error="not a valid zip file")
        drop_stage(stage)
        return
    unpacked = stage / "unpacked"
    unpacked.mkdir(parents=True, exist_ok=True)
    found = {}
    with zf:
        for info in zf.infolist():
            member = clean_relpath(info.filename)
            ext = posixpath.splitext(member)[1].lower()
            if info.is_dir() or ext not in (".data", ".log", ".ulg", ".bin", ".tlog"):
                continue
            dest = unpacked / uuid.uuid4().hex
            with zf.open(info) as src, open(dest, "wb") as dst:
                storage.ensure_space(dest.parent, info.file_size, env_int("LOCAL_MIN_FREE_BYTES"))
                shutil.copyfileobj(src, dst, 8 << 20)
            d, base = posixpath.split(member)
            stem, _ = posixpath.splitext(base)
            found.setdefault((d, stem), {})[ext] = (posixpath.join(d, stem + ext) if d else stem + ext, str(dest))
    n = 0
    for (d, stem), parts in sorted(found.items()):
        if ".data" in parts and ".log" in parts:
            name = posixpath.join(d, stem + ".data") if d else stem + ".data"
            enqueue_staged_log(con, row["stage_dir"], name, parts[".data"][1], row["uploaded_by"],
                               companion=parts[".log"][1])
            n += 1
            continue
        for ext in (".ulg", ".bin", ".tlog"):
            if ext not in parts:
                continue
            enqueue_staged_log(con, row["stage_dir"], parts[ext][0], parts[ext][1], row["uploaded_by"])
            n += 1
        if ".log" in parts and ".data" not in parts and mav_parse.sniff(parts[".log"][1]) == mav_parse.LOG_AP:
            enqueue_staged_log(con, row["stage_dir"], parts[".log"][0], parts[".log"][1], row["uploaded_by"])
            n += 1
    shutil.rmtree(unpacked, ignore_errors=True)
    try:
        zip_path.unlink(missing_ok=True)
    except OSError:
        pass
    if n == 0:
        set_ingest(con, row["id"], status="rejected", error="zip had no recognised log files")
        drop_stage(stage)
        return
    set_ingest(con, row["id"], status="stored", error=None)


def ingest_pair(con, row):
    """Hash a staged pair, skip duplicates, then queue parse."""
    stage = ingest_stage(row["stage_dir"])
    data_src = stage / row["data_name"]
    log_src = (stage / row["log_name"]) if row["log_name"] else None
    name = row["name"]
    if not data_src.is_file():
        set_ingest(con, row["id"], status="error", error="staged log file is missing")
        drop_stage(stage)
        return
    size = data_src.stat().st_size
    if size == 0:
        set_ingest(con, row["id"], status="rejected", error="empty log file")
        drop_stage(stage)
        return
    kind = mav_parse.sniff(data_src)
    if row["log_name"] is None and Path(name).suffix.lower() == ".log" and kind != mav_parse.LOG_AP:
        err = ("needs the matching .data file" if mav_parse.looks_paparazzi_xml(data_src)
               else "unrecognised .log file (not ArduPilot)")
        set_ingest(con, row["id"], status="rejected", error=err)
        drop_stage(stage)
        return
    sha = sha256_file(str(data_src))
    existing = con.execute("SELECT id, data_path, log_path, uploaded_by FROM logs WHERE sha256=?", (sha,)).fetchone()
    user_id = row["uploaded_by"]
    is_admin = False
    if user_id:
        u = con.execute("SELECT is_admin FROM users WHERE id=?", (user_id,)).fetchone()
        is_admin = bool(u and u["is_admin"])
    if existing is not None and not is_admin and existing["uploaded_by"] != user_id:
        set_ingest(con, row["id"], status="duplicate", error="already uploaded by another user", log_id=existing["id"])
        drop_stage(stage)
        return
    if existing is not None:
        if existing["log_path"] is None and log_src and log_src.is_file():
            stored = Path(existing["data_path"])
            stem = Path(stored.name.removesuffix(".gz")).stem
            log_rel = str(stored.parent / (stem + ".log.gz"))
            storage.gzip_file(log_src, STORAGE_DIR / log_rel, work_dir=TMP_DIR,
                              minimum_free=env_int("LOCAL_MIN_FREE_BYTES"))

            def add_log_file():
                with db.write_lock:
                    con.execute("BEGIN IMMEDIATE")
                    con.execute("UPDATE logs SET log_path=?, status='queued' WHERE id=?",
                                (log_rel, existing["id"]))
                    con.commit()

            db.retry_locked(add_log_file, con)
            jobs.put(existing["id"])
            set_ingest(con, row["id"], status="duplicate", error="already uploaded, missing .log added",
                       log_id=existing["id"])
        else:
            set_ingest(con, row["id"], status="duplicate", error="already uploaded", log_id=existing["id"])
        drop_stage(stage)
        return
    data_rel, log_rel = store_files(sha, name, str(data_src), str(log_src) if log_src and log_src.is_file() else None)

    def insert_log():
        with db.write_lock:
            con.execute("BEGIN IMMEDIATE")
            try:
                cur = con.execute(
                    "INSERT INTO logs (name, sha256, size_bytes, data_path, log_path, status,"
                    " uploaded_at, uploaded_by, project_id) VALUES (?,?,?,?,?,'queued',?,?,?)",
                    (name, sha, size, data_rel, log_rel, now(), user_id, None))
                con.commit()
                return cur.lastrowid
            except sqlite3.IntegrityError:
                con.rollback()
                return None

    log_id = db.retry_locked(insert_log, con)
    drop_stage(stage)
    if log_id is None:
        set_ingest(con, row["id"], status="duplicate", error="already uploaded")
        return
    jobs.put(log_id)
    set_ingest(con, row["id"], status="stored", log_id=log_id)


def pending_upload_work(con):
    return dict(con.execute(
        "SELECT COUNT(*) AS count, COALESCE(SUM(size_bytes),0) AS bytes FROM ("
        " SELECT size_bytes FROM ingest WHERE status IN ('accepted','hashing')"
        " UNION ALL SELECT size_bytes FROM logs WHERE status IN ('queued','parsing'))").fetchone())


@app.post("/api/upload-reservations")
def reserve_bulk_upload():
    con = conn()
    if not can_upload_any(con):
        return jsonify(error="you cannot add logs"), 403
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Send a JSON object."), 400
    token, size = body.get("token"), body.get("size_bytes")
    if (not isinstance(token, str) or len(token) != 32 or any(c not in "0123456789abcdef" for c in token)
            or type(size) is not int or not 0 < size <= 2**63 - 1):
        return jsonify(error="Supply a 32-character hex token and positive size_bytes."), 400
    with db.write_lock, con:
        con.execute("BEGIN IMMEDIATE")
        con.execute("DELETE FROM bulk_reservations WHERE expires_at<?", (time.time(),))
        existing = con.execute("SELECT * FROM bulk_reservations WHERE token=?", (token,)).fetchone()
        if existing:
            if existing["user_id"] != g.user["id"] or existing["size_bytes"] != size:
                return jsonify(error="Reservation token conflict."), 409
            return jsonify(token=token)
        backlog = pending_upload_work(con)
        held = con.execute("SELECT COUNT(*) AS count,COALESCE(SUM(size_bytes),0) AS bytes"
                           " FROM bulk_reservations WHERE ingest_id IS NULL").fetchone()
        count, size_pending = backlog["count"] + held["count"], backlog["bytes"] + held["bytes"]
        if count and (count >= env_int("BULK_MAX_PENDING") or size_pending + size > env_int("BULK_MAX_BYTES")):
            return jsonify(error="Bulk upload budget is full; retry later."), 429, {"Retry-After": "5"}
        try:
            storage.ensure_space(DATA_DIR, 2 * size + held["bytes"], env_int("LOCAL_MIN_FREE_BYTES"))
        except OSError as exc:
            if exc.errno != errno.ENOSPC:
                raise
            return jsonify(error=str(exc)), 429, {"Retry-After": "5"}
        con.execute("INSERT INTO bulk_reservations(token,user_id,size_bytes,expires_at) VALUES (?,?,?,?)",
                    (token, g.user["id"], size, time.time() + 3600))
    return jsonify(token=token), 201


def upload_reserved_pair(con, token):
    with db.write_lock, con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM bulk_reservations WHERE token=? AND user_id=?",
                          (token, g.user["id"])).fetchone()
        if row is None or row["expires_at"] < time.time():
            return jsonify(error="Bulk reservation expired; rerun the script."), 409
        if row["ingest_id"] is not None:
            return jsonify(results=[dict(status="accepted", ingest_id=row["ingest_id"])])
        if row["uploading"]:
            return jsonify(error="This upload is still being received."), 429, {"Retry-After": "5"}
        con.execute("UPDATE bulk_reservations SET uploading=1 WHERE token=?", (token,))
    request.max_content_length = row["size_bytes"] + (1 << 20)
    stage_rel = new_stage_rel()
    stage = ingest_stage(stage_rel)
    stage.mkdir(parents=True, exist_ok=True)
    accepted = False
    try:
        files, names = request.files.getlist("files"), request.form.getlist("names")
        if (len(files) != 2 or len(names) != 2 or not names[0].endswith(".data")
                or names[1] != names[0][:-5] + ".log"):
            return jsonify(error="Reserved uploads require exactly one .data/.log pair."), 400
        storage.ensure_space(stage, row["size_bytes"], env_int("LOCAL_MIN_FREE_BYTES"))
        storage.copy_local(files[0].stream, stage / "log.data", env_int("LOCAL_MIN_FREE_BYTES"))
        storage.copy_local(files[1].stream, stage / "log.log", env_int("LOCAL_MIN_FREE_BYTES"))
        size = sum((stage / name).stat().st_size for name in ("log.data", "log.log"))
        if size != row["size_bytes"]:
            return jsonify(error="Files changed size after reservation; rerun the script."), 400
        iid = insert_ingest(con, "pair", clean_relpath(names[0]), stage_rel, "log.data", "log.log",
                            size, g.user["id"], reservation=token)
        accepted = True
        ingest_jobs.put(iid)
        return jsonify(results=[dict(status="accepted", ingest_id=iid)])
    except OSError as exc:
        app.logger.exception("Could not stage reserved upload")
        return jsonify(error=f"Could not stage upload: {exc}"), 503, {"Retry-After": "5"}
    finally:
        if not accepted:
            drop_stage(stage)
            with db.write_lock, con:
                con.execute("DELETE FROM bulk_reservations WHERE token=? AND ingest_id IS NULL", (token,))


@app.post("/api/upload")
def upload():
    """Save files to disk and return immediately. Hashing, duplicates and parse run in the background."""
    con = conn()
    if not can_upload_any(con):
        return jsonify(error="you cannot add logs"), 403
    reservation = request.headers.get("X-Bulk-Reservation")
    if reservation:
        return upload_reserved_pair(con, reservation)
    files = request.files.getlist("files")
    names = request.form.getlist("names")
    if not files:
        return jsonify(error="no files"), 400
    stage_rel = new_stage_rel()
    stage = ingest_stage(stage_rel)
    stage.mkdir(parents=True, exist_ok=True)
    saved = []
    try:
        for i, f in enumerate(files):
            rel = clean_relpath(names[i] if i < len(names) else f.filename or f"file{i}")
            if not rel:
                continue
            dest_name = f"{i}_{safe_filename(posixpath.basename(rel))}"
            dest = stage / dest_name
            storage.copy_local(f.stream, dest, env_int("LOCAL_MIN_FREE_BYTES"))
            saved.append((rel, dest_name, dest))
    except OSError as exc:
        drop_stage(stage)
        app.logger.exception("Could not stage upload")
        return jsonify(error=f"Could not stage upload: {exc}"), 503, {"Retry-After": "5"}
    if not saved:
        drop_stage(stage)
        return jsonify(error="no files"), 400
    results = []
    zips = [(rel, dest_name, dest) for rel, dest_name, dest in saved if rel.lower().endswith(".zip")]
    others = [(rel, dest_name, dest) for rel, dest_name, dest in saved if not rel.lower().endswith(".zip")]
    for rel, dest_name, dest in zips:
        zip_rel = new_stage_rel()
        zip_dir = ingest_stage(zip_rel)
        zip_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dest), zip_dir / dest_name)
        iid = insert_ingest(con, "zip", rel, zip_rel, dest_name, None,
                            (zip_dir / dest_name).stat().st_size, g.user["id"])
        ingest_jobs.put(iid)
        results.append({"name": rel, "status": "accepted", "ingest_id": iid})
    found = {}
    ignored = []
    for rel, dest_name, dest in others:
        d, base = posixpath.split(rel)
        stem, ext = posixpath.splitext(base)
        ext = ext.lower()
        if ext in mav_parse.MAV_SINGLE_EXTS:
            iid, size = enqueue_staged_log(con, stage_rel, rel, dest, g.user["id"])
            results.append({"name": rel, "status": "accepted", "ingest_id": iid, "size_bytes": size})
            continue
        if ext not in (".data", ".log"):
            ignored.append(rel)
            continue
        found.setdefault((d, stem), {})[ext] = dest_name
    for rel in ignored:
        results.append({"name": rel, "status": "rejected", "reason": "not a recognised log or zip file"})
    for (d, stem), parts in sorted(found.items()):
        name = posixpath.join(d, stem + ".data") if d else stem + ".data"
        if ".data" in parts and ".log" in parts:
            iid, size = enqueue_staged_log(con, stage_rel, name, stage / parts[".data"], g.user["id"],
                                           companion=stage / parts[".log"])
            results.append({"name": name, "status": "accepted", "ingest_id": iid, "size_bytes": size})
            continue
        if ".log" in parts and ".data" not in parts:
            log_name = posixpath.join(d, stem + ".log") if d else stem + ".log"
            iid, size = enqueue_staged_log(con, stage_rel, log_name, stage / parts[".log"], g.user["id"])
            results.append({"name": log_name, "status": "accepted", "ingest_id": iid, "size_bytes": size})
            continue
        if ".data" in parts:
            results.append({"name": name, "status": "rejected", "reason": "no matching .log file"})
        else:
            results.append({"name": posixpath.join(d, stem + ".log") if d else stem + ".log",
                            "status": "rejected", "reason": "no matching .data file"})
    for _, _, dest in saved:
        dest.unlink(missing_ok=True)  # files that were not part of a pair
    with contextlib.suppress(OSError):
        stage.rmdir()
    return jsonify(results=results)


# ---------------------------------------------------------------------------------------------------------------------
# Accounts and login
# ---------------------------------------------------------------------------------------------------------------------
# Local passwords plus TU Delft NetID SAML 2.0. IdP metadata:
#   https://login.tudelft.nl/sso/saml2/idp/metadata.php
PUBLIC_ENDPOINTS = {"login_page", "login", "login_options", "setup_page", "setup_info", "setup_password",
                    "theme_css", "theme_js", "saml_login", "saml_acs", "saml_metadata", "saml_sls"}
SETUP_HOURS = 48
MAX_FAILURES, FAILURE_WINDOW_S = 8, 300
_failures: dict = {}
_failures_lock = threading.Lock()


def too_many_failures(key):
    with _failures_lock:
        recent = [t for t in _failures.get(key, []) if time.time() - t < FAILURE_WINDOW_S]
        _failures[key] = recent
        return len(recent) >= MAX_FAILURES


def note_failure(key):
    with _failures_lock:
        _failures.setdefault(key, []).append(time.time())


def user_public(u):
    return {"id": u["id"], "username": u["username"], "display_name": u["display_name"] or u["username"],
            "is_admin": bool(u["is_admin"]), "provider": u["provider"],
            "has_password": bool(u["password_hash"])}


@app.after_request
def no_cache_pages(resp):
    if request.path in ("/api/people-directory", "/api/admin/people") or request.path.startswith((
            "/api/detail/people/", "/api/users/", "/api/qualification-", "/api/admin/qualification-", "/api/admin/users/")):
        resp.headers["Cache-Control"] = "private, no-store"
    if resp.mimetype == "text/html" or request.path in ("/theme.css", "/theme.js", "/attachments.js", "/qualifications.js"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/theme.css")
def theme_css():
    return send_from_directory(PAGES_DIR, "theme.css")


@app.get("/theme.js")
def theme_js():
    return send_from_directory(PAGES_DIR, "theme.js")


API_KEY_PREFIX = "logdb_"


def user_for_key(key):
    if not key.startswith(API_KEY_PREFIX):
        return None
    con = conn()
    h = hashlib.sha256(key.encode()).hexdigest()
    row = con.execute("SELECT k.id AS key_id, k.last_used_at AS key_used, u.* FROM api_keys k JOIN users u ON u.id = k.user_id"
                      " WHERE k.key_hash=? AND u.is_active=1", (h,)).fetchone()
    if row is None:
        return None
    stamp = now()
    if (row["key_used"] or "")[:16] != stamp[:16]:
        try:
            con.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (stamp, row["key_id"]))
            con.commit()
        except sqlite3.OperationalError:
            con.rollback()
    return row


def session_required():
    """Keys can upload and read, but cannot manage keys or the account."""
    if g.via_key:
        abort(403)


@app.before_request
def require_login():
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    g.via_key = False
    auth = request.headers.get("Authorization", "")
    if auth[:7].lower() == "bearer ":
        # API keys only work on the API, never on pages
        user = user_for_key(auth[7:].strip()) if request.path.startswith("/api/") else None
        if user is None:
            return jsonify(error="invalid API key"), 401
        g.user, g.via_key = user, True
        return None
    uid = session.get("uid")
    user = conn().execute("SELECT * FROM users WHERE id=? AND is_active=1", (uid,)).fetchone() if uid else None
    if user is None:
        session.clear()
        if request.path.startswith("/api/"):
            return jsonify(error="login required"), 401
        return redirect("/login")
    g.user = user
    return None


@app.get("/login")
def login_page():
    return send_from_directory(PAGES_DIR, "login.html")


@app.get("/api/login-options")
def login_options():
    return jsonify(saml=saml.saml_enabled(),
                   idp_metadata_url=saml.metadata_url() if saml.saml_enabled() else None)


def unique_username(con, base):
    name = base
    n = 2
    while con.execute("SELECT 1 FROM users WHERE username=?", (name,)).fetchone():
        suffix = str(n)
        name = (base[:40 - len(suffix)] + suffix)
        n += 1
    return name


def login_saml_identity(ident):
    """Find or create a user for a finished TU Delft SAML login, then open a session."""
    subject, email = ident.get("subject"), ident.get("email")
    if not subject:
        return None, "TU Delft did not send a user id"
    con = conn()
    row = con.execute("SELECT * FROM users WHERE subject=?", (subject,)).fetchone()
    if row is None and email:
        row = con.execute("SELECT * FROM users WHERE email=? COLLATE NOCASE AND is_active=1", (email,)).fetchone()
    if row is None:
        names = qualifications.user_keys(con)
        for key in (ident.get("display_name"), ident.get("username")):
            uid = names.get((key or "").casefold()) if key else None
            if uid:
                row = con.execute("SELECT * FROM users WHERE id=? AND is_active=1", (uid,)).fetchone()
                if row:
                    break
    if row is None:
        username = unique_username(con, ident.get("username") or "user")
        cur = con.execute(
            "INSERT INTO users (username, display_name, email, password_hash, is_admin, provider, subject, created_at)"
            " VALUES (?,?,?,?,0,'saml',?,?)",
            (username, ident.get("display_name"), email, None, subject, now()))
        row = con.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    elif not row["is_active"]:
        return None, "this account is disabled"
    else:
        if ident.get("display_name") and not row["display_name"]:
            con.execute("UPDATE users SET display_name=? WHERE id=?", (ident["display_name"], row["id"]))
        if email and not row["email"]:
            con.execute("UPDATE users SET email=? WHERE id=?", (email, row["id"]))
        if not row["subject"]:
            prov = row["provider"] or ""
            if prov in ("", "log", "saml"):
                prov = "saml"
            con.execute("UPDATE users SET subject=?, provider=? WHERE id=?", (subject, prov, row["id"]))
        row = con.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
    con.execute("UPDATE users SET last_login=? WHERE id=?", (now(), row["id"]))
    con.commit()
    session.clear()
    session.permanent = True
    session["uid"] = row["id"]
    return row, None


@app.get("/login/tudelft")
def saml_login():
    if not saml.saml_enabled():
        return redirect("/login")
    try:
        auth = saml.auth_for(request, DATA_DIR)
        url = auth.login(return_to="/")
    except Exception:
        return redirect("/login?error=sso")
    return redirect(url)


@app.post("/saml/acs")
def saml_acs():
    if not saml.saml_enabled():
        return redirect("/login")
    try:
        auth = saml.auth_for(request, DATA_DIR)
        auth.process_response()
        errors = auth.get_errors()
        if errors or not auth.is_authenticated():
            return redirect("/login?error=sso")
        ident = saml.identity_from_auth(auth)
        user, err = login_saml_identity(ident)
    except Exception:
        return redirect("/login?error=sso")
    if err or user is None:
        return redirect("/login?error=sso")
    nxt = request.form.get("RelayState") or "/"
    if not nxt.startswith("/"):
        nxt = "/"
    return redirect(nxt)


@app.get("/saml/metadata")
def saml_metadata():
    auth = saml.auth_for(request, DATA_DIR)
    xml = auth.get_settings().get_sp_metadata()
    errors = auth.get_settings().validate_metadata(xml)
    if errors:
        return jsonify(error="; ".join(errors)), 500
    return Response(xml, mimetype="application/samlmetadata+xml")


@app.get("/saml/sls")
def saml_sls():
    session.clear()
    return redirect("/login")


@app.post("/login")
def login():
    body = request.get_json(silent=True) or request.form
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    key = f"{request.remote_addr}|{username.lower()}"
    if too_many_failures(key):
        return jsonify(error="too many failed attempts, try again in a few minutes"), 429
    user = conn().execute("SELECT * FROM users WHERE username=? AND is_active=1 AND provider='local'",
                          (username,)).fetchone()
    # check against a dummy hash for unknown users so timing does not reveal which usernames exist
    pw_hash = (user and user["password_hash"]) or generate_password_hash("x")
    if not check_password_hash(pw_hash, password) or user is None or not user["password_hash"]:
        note_failure(key)
        return jsonify(error="wrong username or password"), 401
    con = conn()
    con.execute("UPDATE users SET last_login=? WHERE id=?", (now(), user["id"]))
    con.commit()
    session.clear()
    session.permanent = True
    session["uid"] = user["id"]
    return jsonify(user=user_public(user))


@app.post("/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/account")
def account_page():
    return send_from_directory(PAGES_DIR, "account.html")


@app.get("/download/upload_logs.py")
def download_upload_script():
    return send_from_directory(HERE, "upload_logs.py", as_attachment=True, mimetype="text/x-python")


@app.post("/api/account/password")
def change_password():
    session_required()
    if g.user["provider"] != "local" or not g.user["password_hash"]:
        return jsonify(error="Passwords for this account are managed by its identity provider."), 403
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="send a JSON object"), 400
    current, password = body.get("current_password"), body.get("password")
    if not isinstance(current, str) or not isinstance(password, str) or not 10 <= len(password) <= 1024:
        return jsonify(error="new password must be 10–1024 characters"), 400
    key = ("password-change", g.user["id"])
    if too_many_failures(key):
        return jsonify(error="Too many attempts. Try again in five minutes."), 429
    if len(current) > 1024 or not check_password_hash(g.user["password_hash"], current):
        note_failure(key)
        return jsonify(error="Current password is incorrect."), 403
    con = conn()
    con.execute("UPDATE users SET password_hash=? WHERE id=?",
                (generate_password_hash(password), g.user["id"]))
    con.execute("UPDATE password_setup_tokens SET used_at=? WHERE user_id=? AND used_at IS NULL",
                (now(), g.user["id"]))
    con.commit()
    return jsonify(ok=True)


@app.get("/api/keys")
def list_keys():
    session_required()
    rows = conn().execute("SELECT id, name, prefix, created_at, last_used_at FROM api_keys WHERE user_id=?"
                          " ORDER BY id DESC", (g.user["id"],))
    return jsonify([dict(r) for r in rows])


@app.post("/api/keys")
def create_key():
    session_required()
    name = ((request.get_json(silent=True) or {}).get("name") or "").strip()
    if not name or len(name) > 60:
        return jsonify(error="give the key a name (max 60 characters)"), 400
    con = conn()
    if con.execute("SELECT COUNT(*) FROM api_keys WHERE user_id=?", (g.user["id"],)).fetchone()[0] >= 20:
        return jsonify(error="at most 20 keys per user"), 400
    key = API_KEY_PREFIX + secrets.token_urlsafe(32)
    cur = con.execute("INSERT INTO api_keys (user_id, name, key_hash, prefix, created_at) VALUES (?,?,?,?,?)",
                      (g.user["id"], name, hashlib.sha256(key.encode()).hexdigest(), key[:10], now()))
    con.commit()
    return jsonify(id=cur.lastrowid, name=name, key=key)


@app.delete("/api/keys/<int:key_id>")
def delete_key(key_id):
    session_required()
    con = conn()
    cur = con.execute("DELETE FROM api_keys WHERE id=? AND user_id=?", (key_id, g.user["id"]))
    con.commit()
    if cur.rowcount == 0:
        return jsonify(error="no such key"), 404
    return jsonify(ok=True)


@app.post("/api/logs/check")
def check_logs():
    """Which of these .data hashes are already stored? Lets a client skip uploading them."""
    hashes = (request.get_json(silent=True) or {}).get("sha256")
    if not isinstance(hashes, list) or len(hashes) > 5000:
        return jsonify(error="send {\"sha256\": [up to 5000 hashes]}"), 400
    wanted = [h.lower() for h in hashes if isinstance(h, str) and len(h) == 64]
    con = conn()
    known = {}
    for i in range(0, len(wanted), 500):
        chunk = wanted[i:i + 500]
        for r in con.execute("SELECT sha256, log_path IS NOT NULL AS has_log FROM logs WHERE sha256 IN (%s)"
                             % ",".join("?" * len(chunk)), chunk):
            known[r["sha256"]] = {"has_log": bool(r["has_log"])}
    return jsonify(known=known)


@app.get("/api/me")
def me():
    return jsonify(user=user_public(g.user))


@app.get("/api/people")
def list_people():
    """Active accounts for PIC/PAC/GCS menus and the People directory (name and username only)."""
    rows = conn().execute(
        "SELECT id, username, display_name, is_admin FROM users WHERE is_active=1"
        " ORDER BY COALESCE(display_name, username), username")
    return jsonify([{"id": r["id"], "username": r["username"],
                     "display_name": r["display_name"] or r["username"],
                     "is_admin": bool(r["is_admin"])} for r in rows])


@app.get("/api/people-directory")
def people_directory():
    """People the current user may open, with aliases and qualifications they are allowed to see."""
    con = conn()
    allowed = qualifications.directory_user_ids(con, g.user)
    users = []
    for r in con.execute(
            "SELECT id, username, display_name, is_admin, provider FROM users WHERE is_active=1"
            " ORDER BY COALESCE(display_name, username), username"):
        if r["id"] not in allowed:
            continue
        quals = None
        if qualifications.can_see_quals(con, g.user, r["id"]):
            quals = qualifications.qualifications_payload(con, g.user, r["id"])
        users.append({"id": r["id"], "username": r["username"],
                      "display_name": r["display_name"] or r["username"],
                      "is_admin": bool(r["is_admin"]), "provider": r["provider"] or "local",
                      "pending": (r["provider"] or "") == "log", "qualifications": quals,
                      "aliases": [a["alias"] for a in con.execute(
                          "SELECT alias FROM user_aliases WHERE user_id=? ORDER BY alias", (r["id"],))]})
    return jsonify(users=users)


# ---- command line user management:  python3 app.py create-user|set-password|list-users|deactivate-user ----
def backup():
    """Consistent copy of the live database (and the session/SAML keys) in LOGDB_BACKUP_DIR.

    SQLite's online backup copies the database while the server keeps running. The copy is made and checked on the
    local disk first, then written to the backup directory under a temporary name and renamed, so the directory
    (and a backup of that share) always holds a complete, working database."""
    if not env("BACKUP_DIR"):
        return "LOGDB_BACKUP_DIR is not set"
    if not os.path.exists(DB_PATH):
        return f"no database at {DB_PATH}"
    dest = env_path("BACKUP_DIR")
    external_permissions = env_bool("BACKUP_EXTERNAL_PERMISSIONS")
    if external_permissions:
        print("WARNING: Backup permissions are externally managed. The destination must restrict access "
              "to account data and private keys using share/host ACLs.", file=sys.stderr)
    dest.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    local = TMP_DIR / "backup.db"
    local.unlink(missing_ok=True)
    src = sqlite3.connect(DB_PATH, timeout=db.BUSY_TIMEOUT_MS / 1000)
    dst = sqlite3.connect(local)
    try:
        src.backup(dst)
        result = dst.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        dst.close()
        src.close()
    if result != "ok":
        local.unlink(missing_ok=True)
        return f"backup copy failed the integrity check ({result}); the previous backup was kept"
    files = [(local, dest / "logs.db")]
    if (DATA_DIR / "secret_key").exists():
        files.append((DATA_DIR / "secret_key", dest / "secret_key"))
    for pem in sorted((DATA_DIR / "saml").glob("*.pem")):
        files.append((pem, dest / "saml" / pem.name))
    try:
        for src_file, dest_file in files:
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest_file.with_name(f".{dest_file.name}.tmp")
            try:
                with open(src_file, "rb") as fi, open(tmp, "wb") as fo:
                    try:
                        os.chmod(tmp, 0o600)
                    except OSError as exc:
                        if exc.errno not in (errno.EACCES, errno.EPERM, errno.ENOTSUP, errno.ENOSYS):
                            raise
                        if not external_permissions:
                            return (f"Cannot enforce private permissions on {tmp}: {exc}. "
                                    "Use a local backup directory, or set LOGDB_BACKUP_EXTERNAL_PERMISSIONS=1 "
                                    "only after restricting access with share/host ACLs.")
                        print(f"WARNING: Cannot enforce chmod 600 on {tmp}: {exc}; "
                              "continuing with externally managed permissions.", file=sys.stderr)
                    shutil.copyfileobj(fi, fo, 8 << 20)
                    fo.flush()
                    os.fsync(fo.fileno())
                os.replace(tmp, dest_file)
            finally:
                tmp.unlink(missing_ok=True)
    finally:
        local.unlink(missing_ok=True)
    size = (dest / "logs.db").stat().st_size / 1e6
    print(f"{now()} backup of {DB_PATH} ({size:.1f} MB) written to {dest} in {time.time() - t0:.1f} s")
    return 0


def cli(argv):
    import argparse
    import getpass

    p = argparse.ArgumentParser(prog="app.py", description="Manage accounts of the log database")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-user", help="create a local account")
    c.add_argument("username")
    c.add_argument("--admin", action="store_true")
    c.add_argument("--name", help="display name")
    c.add_argument("--email")
    sp = sub.add_parser("set-password", help="change the password of an account")
    sp.add_argument("username")
    sub.add_parser("list-users")
    sub.add_parser("check-config", help="show the effective settings and check them")
    al = sub.add_parser("add-alias", help="link a PIC/PAC/GCS name from the logs to a user")
    al.add_argument("username")
    al.add_argument("alias")
    ra = sub.add_parser("remove-alias")
    ra.add_argument("alias")
    d = sub.add_parser("deactivate-user", help="block an account (its uploads are kept)")
    d.add_argument("username")
    ac = sub.add_parser("activate-user")
    ac.add_argument("username")
    sub.add_parser("backup", help="copy the database and keys to LOGDB_BACKUP_DIR (overwrites the previous copy)")
    a = p.parse_args(argv)

    if a.cmd == "backup":
        sys.exit(backup())
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db.init(DB_PATH)
    con = conn()

    def ask_password():
        pw = getpass.getpass("Password: ")
        if len(pw) < 10:
            sys.exit("Password must be at least 10 characters")
        if pw != getpass.getpass("Repeat password: "):
            sys.exit("Passwords do not match")
        return generate_password_hash(pw)

    if a.cmd == "create-user":
        try:
            con.execute("INSERT INTO users (username, display_name, email, password_hash, is_admin, created_at)"
                        " VALUES (?,?,?,?,?,?)",
                        (a.username, a.name, a.email, ask_password(), int(a.admin), now()))
        except sqlite3.IntegrityError:
            sys.exit(f"User '{a.username}' already exists")
        print(f"Created {'admin ' if a.admin else ''}user {a.username}")
    elif a.cmd == "set-password":
        if not con.execute("SELECT 1 FROM users WHERE username=?", (a.username,)).fetchone():
            sys.exit("No such user")
        con.execute("UPDATE users SET password_hash=? WHERE username=?", (ask_password(), a.username))
        print("Password changed")
    elif a.cmd == "list-users":
        for u in con.execute("SELECT * FROM users ORDER BY id"):
            print(f"{u['id']:3} {u['username']:20} {u['provider']:6} {'admin' if u['is_admin'] else '     '} "
                  f"{'active' if u['is_active'] else 'BLOCKED'}  last login: {u['last_login'] or '-'}  aliases: "
                  + (", ".join(r["alias"] for r in con.execute(
                      "SELECT alias FROM user_aliases WHERE user_id=?", (u["id"],))) or "-"))
    elif a.cmd == "add-alias":
        u = con.execute("SELECT id FROM users WHERE username=?", (a.username,)).fetchone()
        if not u:
            sys.exit("No such user")
        try:
            con.execute("INSERT INTO user_aliases (user_id, alias) VALUES (?,?)", (u["id"], a.alias.strip()))
        except sqlite3.IntegrityError:
            sys.exit("Alias already in use")
        print("Done")
    elif a.cmd == "remove-alias":
        if not con.execute("DELETE FROM user_aliases WHERE alias=?", (a.alias,)).rowcount:
            sys.exit("No such alias")
        print("Done")
    else:
        active = int(a.cmd == "activate-user")
        if not con.execute("UPDATE users SET is_active=? WHERE username=?", (active, a.username)).rowcount:
            sys.exit("No such user")
        print("Done")
    con.commit()


# ---------------------------------------------------------------------------------------------------------------------
# Linking PIC/PAC/GCS names to users
# ---------------------------------------------------------------------------------------------------------------------
ROLES = ("pic", "pac", "gcs")


def person_resolver(con):
    """Return resolve(role, name, uploader_id) -> {id, username, display_name, source} or None.

    A logged name always stays visible to the caller; this only finds the linked user. Matching is
    case-insensitive on explicit aliases, then unique display names, never login usernames. Without any name in the log only the
    PIC falls back to the uploader (source 'uploader'); PAC and GCS stay empty."""
    users = {u["id"]: dict(id=u["id"], username=u["username"], display_name=u["display_name"] or u["username"])
             for u in con.execute("SELECT id, username, display_name FROM users")}
    names = qualifications.crew_keys(con)

    def resolve(role, name, uploader_id):
        name = (name or "").strip()
        if name:
            uid = names.get(name.casefold())
            return dict(users[uid], source="alias") if uid in users else None
        if role == "pic" and uploader_id in users:
            return dict(users[uploader_id], source="uploader")
        return None

    return resolve


def add_people(rows, resolve, users=None):
    """Attach pic_user/pac_user/gcs_user to dict rows that carry pic/pac/gcs and uploaded_by.

    An explicit *_user_id from a logbook edit wins over the name in the log."""
    if users is None:
        users = {u["id"]: dict(id=u["id"], username=u["username"], display_name=u["display_name"] or u["username"])
                 for u in conn().execute("SELECT id, username, display_name FROM users")}
    for r in rows:
        for role in ROLES:
            uid = r.get(role + "_user_id")
            if uid and uid in users:
                r[role + "_user"] = dict(users[uid], source="edit")
            else:
                r[role + "_user"] = resolve(role, r.get(role), r.get("uploaded_by"))
    return rows


# ---------------------------------------------------------------------------------------------------------------------
# Fleet aircraft (drones) and aliases that match names in parsed logs
# ---------------------------------------------------------------------------------------------------------------------
def drone_name_map(con):
    """Case-insensitive map of drone names and aliases -> drone id. Explicit aliases win."""
    drones = {d["id"]: dict(d) for d in con.execute("SELECT id, name, unique_id, default_project_id FROM drones")}
    names = {d["name"].casefold(): d["id"] for d in drones.values()}
    for d in drones.values():
        uid = (d.get("unique_id") or "").strip()
        if uid:
            names.setdefault(uid.casefold(), d["id"])
    for r in con.execute("SELECT drone_id, alias FROM drone_aliases"):
        names[r["alias"].casefold()] = r["drone_id"]
    return drones, names


def resolve_drone(names, drones, name):
    key = (name or "").strip().casefold()
    if not key:
        return None
    did = names.get(key)
    if did:
        return drones.get(did)
    for token in identity_tokens(name):
        did = names.get(token.casefold())
        if did:
            return drones.get(did)
    return None


_HEX_ID = re.compile(r"[0-9A-Fa-f]{8,36}")
_GENERIC_LOG_FOLDERS = {
    "log", "logs", "ulg", "bin", "tlog", "data", "px4", "px4logs", "ardupilot", "upload",
    "uploads", "download", "downloads", "desktop", "documents", "incoming",
}


def identity_tokens(logged_name):
    """Board ids embedded in a logged name such as 'quad_x 00500040'."""
    out = []
    for tok in _HEX_ID.findall(logged_name or ""):
        if tok not in out:
            out.append(tok)
        if len(tok) > 8:
            short = tok[-8:]
            if short not in out:
                out.append(short)
    return out


def log_folder_label(log_name):
    """Immediate parent folder of an uploaded log, or None for generic dump folders."""
    rel = (log_name or "").replace("\\", "/")
    parent = posixpath.basename(posixpath.dirname(rel)).strip()
    if not parent or fold_ac(parent) in _GENERIC_LOG_FOLDERS:
        return None
    return parent


def matching_drones_for_folder(con, folder):
    """Fleet aircraft whose name overlaps an upload folder (Ranger 1600, Quadrotor, …)."""
    want = fold_ac(folder or "")
    if len(want) < 4:
        return []
    hits = []
    for d in con.execute("SELECT id, name, unique_id, default_project_id FROM drones"):
        have = fold_ac(d["name"])
        if want == have or want in have or (len(have) >= 6 and have in want):
            hits.append(dict(d))
    return hits


def guess_drone_from_log_path(con, log_name):
    """If the upload folder uniquely matches one fleet aircraft, use that.

    PX4 does not store a nickname; the folder the files were dropped from often is the aircraft.
    Only links when exactly one drone matches, so 'Quadrotor' does not steal every quad in the fleet.
    """
    folder = log_folder_label(log_name)
    if not folder:
        return None
    hits = matching_drones_for_folder(con, folder)
    if len(hits) == 1:
        return hits[0]
    return None


def remember_drone_log_identity(con, drone_id, logged_name):
    """After a PX4/ArduPilot log is linked, remember the logged name and board id as aliases."""
    labels = [(logged_name or "").strip()]
    labels.extend(identity_tokens(logged_name))
    for label in labels:
        if len(label) < 4:
            continue
        if fold_ac(label) in _GENERIC_LOG_FOLDERS:
            continue
        if name_taken(con, label, drone_id):
            continue
        try:
            con.execute("INSERT INTO drone_aliases (drone_id, alias) VALUES (?,?)", (drone_id, label))
        except sqlite3.IntegrityError:
            pass


def name_taken(con, name, drone_id=None):
    """True if this name is already a drone name or alias (optionally ignoring one drone)."""
    name = (name or "").strip()
    if not name:
        return False
    row = con.execute("SELECT id FROM drones WHERE name=?", (name,)).fetchone()
    if row and row["id"] != drone_id:
        return True
    row = con.execute("SELECT drone_id FROM drone_aliases WHERE alias=?", (name,)).fetchone()
    return bool(row and row["drone_id"] != drone_id)


def parse_unique_id(raw):
    uid = (raw or "").strip()
    if not uid or len(uid) > 40:
        return False
    return uid


DRONE_STATUSES = ("Ready", "Maintenance", "Development", "Discard")


def parse_status(raw):
    if raw in (None, ""):
        return None
    key = str(raw).strip().casefold()
    for s in DRONE_STATUSES:
        if s.casefold() == key:
            return s
    return False


def clip_text(raw, max_len):
    v = (raw or "").strip()
    return v[:max_len] if v else None


def fold_ac(name):
    return "".join(c for c in (name or "").casefold() if c.isalnum())


def match_inventory_project(con, label):
    label = (label or "").strip()
    if not label or label.casefold() in ("none", "n/a", "-"):
        return None, None
    row = con.execute("SELECT id, name FROM projects WHERE name=?", (label,)).fetchone()
    if row:
        return row["id"], row["name"]
    want = fold_ac(label)
    hits = [r for r in con.execute("SELECT id, name FROM projects") if fold_ac(r["name"]) == want]
    if len(hits) == 1:
        return hits[0]["id"], hits[0]["name"]
    return None, label


def find_import_drone(con, name, uid):
    row = con.execute("SELECT * FROM drones WHERE unique_id=?", (uid,)).fetchone()
    if row:
        return row
    row = con.execute("SELECT * FROM drones WHERE name=?", (name,)).fetchone()
    if row:
        return row
    # Only fuzzy-match aircraft that do not already have a fleet serial, so a short
    # name like Splash cannot swallow later rows such as Splash White.
    want = fold_ac(name)
    hits = []
    for d in con.execute("SELECT * FROM drones"):
        have_uid = (d["unique_id"] or "").upper()
        if have_uid.startswith("TUD"):
            continue
        have = fold_ac(d["name"])
        if have == want or (have and len(have) >= 8 and want.startswith(have)):
            hits.append(d)
    if len(hits) == 1:
        return hits[0]
    return None


def unique_import_name(con, name, uid, drone_id=None):
    if not name_taken(con, name, drone_id):
        return name
    alt = f"{name} ({uid})"
    if len(alt) > 80:
        alt = alt[:80]
    if not name_taken(con, alt, drone_id):
        return alt
    n = 2
    while True:
        alt = f"{name} ({uid}-{n})"[:80]
        if not name_taken(con, alt, drone_id):
            return alt
        n += 1


def apply_drone_fields(con, drone_id, fields, old_name=None):
    con.execute(
        "UPDATE drones SET name=?, unique_id=?, default_project_id=?, manager=?, current_user=?,"
        " status=?, bought_on=?, inventory_project=?, comments=? WHERE id=?",
        (fields["name"], fields["unique_id"], fields["default_project_id"], fields["manager"],
         fields["current_user"], fields["status"], fields["bought_on"], fields["inventory_project"],
         fields["comments"], drone_id))
    if old_name and old_name.casefold() != fields["name"].casefold() and not name_taken(con, old_name, drone_id):
        try:
            con.execute("INSERT INTO drone_aliases (drone_id, alias) VALUES (?,?)", (drone_id, old_name))
        except sqlite3.IntegrityError:
            pass


def unique_id_taken(con, uid, drone_id=None):
    row = con.execute("SELECT id FROM drones WHERE unique_id=?", (uid,)).fetchone()
    return bool(row and row["id"] != drone_id)


def parse_project_id(raw):
    if raw in (None, ""):
        return None
    if isinstance(raw, int) or (isinstance(raw, str) and raw.isdigit()):
        return int(raw)
    return False


def link_log_aircraft(con, log_id):
    drones, names = drone_name_map(con)
    log = con.execute("SELECT name FROM logs WHERE id=?", (log_id,)).fetchone()
    log_name = log["name"] if log else ""
    for a in con.execute("SELECT id, name FROM aircraft WHERE log_id=?", (log_id,)):
        d = resolve_drone(names, drones, a["name"])
        if d is None:
            d = guess_drone_from_log_path(con, log_name)
        if d is not None:
            remember_drone_log_identity(con, d["id"], a["name"])
            drones, names = drone_name_map(con)
        con.execute("UPDATE aircraft SET drone_id=? WHERE id=?", (d["id"] if d else None, a["id"]))


def relink_all_aircraft(con):
    for r in con.execute("SELECT id FROM logs"):
        link_log_aircraft(con, r["id"])


def live_project(con, project_id):
    if not project_id:
        return None
    if con.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
        return project_id
    return None


def user_main_project(con, user_id):
    if not user_id:
        return None
    row = con.execute("SELECT default_project_id FROM users WHERE id=?", (user_id,)).fetchone()
    return live_project(con, row["default_project_id"] if row else None)


def user_in_project(con, user_id, project_id):
    """True if this account is on the project. Admins count as on every project."""
    if not user_id or not project_id:
        return False
    row = con.execute("SELECT is_admin FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        return False
    if row["is_admin"]:
        return True
    return con.execute("SELECT 1 FROM project_members WHERE project_id=? AND user_id=?",
                       (project_id, user_id)).fetchone() is not None


def pick_project_for_user(con, user_id, aircraft_project_id):
    """Aircraft project if the user is on it, otherwise the user's own default project."""
    ac = live_project(con, aircraft_project_id)
    if ac and user_in_project(con, user_id, ac):
        return ac
    return user_main_project(con, user_id)


def snapshot_flight_edits(con, log_id):
    """Keep edited flight project and crew across a re-parse (flights are deleted with the aircraft rows)."""
    return {(r["ac_id"], r["idx"]): dict(r) for r in con.execute(
        "SELECT a.ac_id, f.idx, f.project_id, f.project_source, f.pic_user_id, f.pac_user_id, f.gcs_user_id, f.comment, f.hidden"
        " FROM flights f JOIN aircraft a ON a.id = f.aircraft_id WHERE a.log_id=?", (log_id,))}


def drone_periods(con, drone_id):
    return [dict(r) for r in con.execute(
        "SELECT dp.id, dp.project_id, p.name AS project_name, dp.start_date, dp.end_date"
        " FROM drone_projects dp JOIN projects p ON p.id = dp.project_id"
        " WHERE dp.drone_id=? ORDER BY dp.start_date, dp.id", (drone_id,))]


def project_for_drone_on(con, drone_id, day):
    """Project this airframe was on for YYYY-MM-DD, or None if the timeline has a gap."""
    if not drone_id or not day:
        return None
    row = con.execute(
        "SELECT project_id FROM drone_projects WHERE drone_id=? AND start_date<=?"
        " AND (end_date IS NULL OR end_date>=?) ORDER BY start_date DESC, id DESC LIMIT 1",
        (drone_id, day, day)).fetchone()
    return live_project(con, row["project_id"] if row else None)


def flight_calendar_day(takeoff_utc, log_start_utc, date_override):
    """Takeoff calendar date used to match the aircraft timeline. None if the clock is missing/1980."""
    if date_override:
        days = clock_shift_days(log_start_utc, date_override)
        shifted = shift_iso(takeoff_utc, days) if days else takeoff_utc
        dt = parse_iso_utc(shifted)
        return dt.date().isoformat() if dt else date_override
    raw = takeoff_utc or log_start_utc
    if clock_implausible(raw):
        return None
    dt = parse_iso_utc(takeoff_utc)
    return dt.date().isoformat() if dt else None


def parse_loose_date(raw):
    """YYYY-MM-DD from a fleet-list date, or None if it is not usable."""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    iso = parse_date_override(s[:10] if len(s) >= 10 and s[4] == "-" else "")
    if iso:
        return iso
    for fmt, size in (("%d-%m-%Y", 10), ("%d/%m/%Y", 10), ("%Y/%m/%d", 10), ("%d.%m.%Y", 10),
                      ("%Y-%m-%d", 10), ("%d-%m-%y", 8), ("%d/%m/%y", 8)):
        try:
            d = datetime.strptime(s[:size], fmt).date()
        except ValueError:
            continue
        if d.year < 100:
            d = d.replace(year=2000 + d.year)
        if CLOCK_MIN_YEAR <= d.year <= datetime.now(timezone.utc).year + 1:
            return d.isoformat()
    return None


def first_flight_day(con, drone_id):
    row = con.execute(
        "SELECT MIN(f.takeoff_utc) AS t FROM flights f JOIN aircraft a ON a.id = f.aircraft_id"
        " WHERE a.drone_id=? AND f.takeoff_utc IS NOT NULL AND f.takeoff_utc>=?",
        (drone_id, f"{CLOCK_MIN_YEAR:04d}-01-01")).fetchone()
    dt = parse_iso_utc(row["t"] if row else None)
    return dt.date().isoformat() if dt else None


def seed_drone_period(con, drone_id, project_id, start_date=None):
    """Add one open-ended period if this aircraft has no timeline yet."""
    project_id = live_project(con, project_id)
    if not drone_id or not project_id:
        return False
    if con.execute("SELECT 1 FROM drone_projects WHERE drone_id=?", (drone_id,)).fetchone():
        return False
    start = start_date or first_flight_day(con, drone_id) or datetime.now(timezone.utc).date().isoformat()
    con.execute(
        "INSERT INTO drone_projects (drone_id, project_id, start_date, end_date) VALUES (?,?,?,NULL)",
        (drone_id, project_id, start))
    con.execute(
        "UPDATE drones SET default_project_id=? WHERE id=? AND default_project_id IS NULL",
        (project_id, drone_id))
    return True


def seed_drone_projects_from_defaults(con):
    for d in con.execute("SELECT id, default_project_id, bought_on FROM drones"):
        seed_drone_period(con, d["id"], d["default_project_id"], parse_loose_date(d["bought_on"]))


def current_drone_project_id(periods):
    open_ = [p for p in periods if not p.get("end_date")]
    if len(open_) == 1:
        return open_[0]["project_id"]
    if periods:
        return max(periods, key=lambda p: (p.get("end_date") or p["start_date"], p["start_date"]))["project_id"]
    return None


def validate_drone_periods(periods):
    """periods is a list of {project_id, start_date, end_date}. Returns an error string or None."""
    if not periods:
        return None
    cleaned = []
    for p in periods:
        pid = parse_project_id(p.get("project_id"))
        if not pid:
            return "each period needs a project"
        start = parse_date_override(p.get("start_date"))
        if not start:
            return "each period needs a from date"
        end = parse_date_override(p.get("end_date")) if p.get("end_date") not in (None, "") else None
        if end is False:
            return "enter dates as YYYY-MM-DD"
        if end and end < start:
            return "until must be on or after from"
        cleaned.append({"project_id": pid, "start_date": start, "end_date": end})
    cleaned.sort(key=lambda p: (p["start_date"], p["end_date"] or "9999-12-31"))
    prev_end = None
    for p in cleaned:
        end = p["end_date"] or "9999-12-31"
        if prev_end and p["start_date"] <= prev_end:
            return "these dates overlap; one aircraft cannot be on two projects on the same day"
        prev_end = end
    return None


def replace_drone_periods(con, drone_id, periods):
    err = validate_drone_periods(periods)
    if err:
        return err
    cleaned = []
    for p in periods:
        start = parse_date_override(p.get("start_date"))
        end = parse_date_override(p.get("end_date")) if p.get("end_date") not in (None, "") else None
        cleaned.append({"project_id": int(p["project_id"]), "start_date": start, "end_date": end})
    con.execute("DELETE FROM drone_projects WHERE drone_id=?", (drone_id,))
    for p in cleaned:
        if not live_project(con, p["project_id"]):
            return "no such project"
        con.execute(
            "INSERT INTO drone_projects (drone_id, project_id, start_date, end_date) VALUES (?,?,?,?)",
            (drone_id, p["project_id"], p["start_date"], p["end_date"]))
    current = current_drone_project_id(cleaned)
    con.execute("UPDATE drones SET default_project_id=? WHERE id=?", (current, drone_id))
    return None


def pick_flight_project(con, row, resolve):
    """Aircraft timeline on the takeoff day. People are not used to guess the project."""
    if not row["drone_id"]:
        return None
    day = flight_calendar_day(row["takeoff_utc"], row["log_start_utc"], row["date_override"])
    return project_for_drone_on(con, row["drone_id"], day)


def import_drone_project_period(con, drone_id, project_id, start):
    periods = drone_periods(con, drone_id)
    before = [dict(p) for p in periods if p["start_date"] < start]
    after = [dict(p) for p in periods if p["start_date"] > start]
    previous_day = (datetime.strptime(start, "%Y-%m-%d").date() - timedelta(days=1)).isoformat()
    for p in before:
        if not p["end_date"] or p["end_date"] >= start:
            p["end_date"] = previous_day
    next_start = min((p["start_date"] for p in after), default=None)
    end = ((datetime.strptime(next_start, "%Y-%m-%d").date() - timedelta(days=1)).isoformat()
           if next_start else None)
    err = replace_drone_periods(con, drone_id, [
        *before, {"project_id": project_id, "start_date": start, "end_date": end}, *after])
    if err:
        raise historical.RecordError(err)
    rows = con.execute(
        "SELECT f.id, f.takeoff_utc, l.start_utc AS log_start_utc, l.date_override"
        " FROM flights f JOIN aircraft a ON a.id=f.aircraft_id JOIN logs l ON l.id=a.log_id"
        " WHERE a.drone_id=? AND (f.project_source='timeline'"
        " OR (f.project_source IS NULL AND f.project_id IS NULL))", (drone_id,)).fetchall()
    for row in rows:
        day = flight_calendar_day(row["takeoff_utc"], row["log_start_utc"], row["date_override"])
        if day and day >= start and (end is None or day <= end):
            con.execute("UPDATE flights SET project_id=?, project_source='timeline' WHERE id=?",
                        (project_id, row["id"]))


def assign_log_flights(con, log_id, kept=None):
    """Fill empty flight projects, restoring kept values from a re-parse first."""
    kept = kept or {}
    resolve = person_resolver(con)
    log = con.execute("SELECT uploaded_by, project_id FROM logs WHERE id=?", (log_id,)).fetchone()
    if log is None:
        return
    for f in con.execute(
            "SELECT f.id, f.idx, f.project_id, f.project_source, f.takeoff_utc, a.ac_id, a.drone_id, a.pic, a.gcs,"
            " l.uploaded_by, l.start_utc AS log_start_utc, l.date_override"
            " FROM flights f JOIN aircraft a ON a.id = f.aircraft_id JOIN logs l ON l.id = a.log_id"
            " WHERE a.log_id=?", (log_id,)):
        restore = kept.get((f["ac_id"], f["idx"])) or {}
        if restore:
            con.execute("UPDATE flights SET pic_user_id=?, pac_user_id=?, gcs_user_id=?, comment=?, hidden=? WHERE id=?",
                        (restore.get("pic_user_id"), restore.get("pac_user_id"), restore.get("gcs_user_id"),
                         restore.get("comment"), restore.get("hidden", 0), f["id"]))
        saved = restore.get("project_id")
        source = restore.get("project_source") if restore else f["project_source"]
        if source == "manual":
            con.execute("UPDATE flights SET project_id=?, project_source='manual' WHERE id=?",
                        (saved if restore else f["project_id"], f["id"]))
            continue
        if saved and live_project(con, saved):
            con.execute("UPDATE flights SET project_id=?, project_source=? WHERE id=?", (saved, source, f["id"]))
            continue
        if f["project_id"]:
            continue
        pid = pick_flight_project(con, f, resolve)
        if pid:
            con.execute("UPDATE flights SET project_id=?, project_source='timeline' WHERE id=?", (pid, f["id"]))
    if log["project_id"] is None:
        ids = [r["project_id"] for r in con.execute(
            "SELECT DISTINCT f.project_id FROM flights f JOIN aircraft a ON a.id = f.aircraft_id"
            " WHERE a.log_id=? AND f.project_id IS NOT NULL", (log_id,))]
        if len(ids) == 1:
            con.execute("UPDATE logs SET project_id=? WHERE id=? AND project_id IS NULL", (ids[0], log_id))


def assign_open_flights(con):
    for r in con.execute(
            "SELECT DISTINCT a.log_id FROM flights f JOIN aircraft a ON a.id = f.aircraft_id"
            " WHERE f.project_id IS NULL AND COALESCE(f.project_source,'')!='manual'"):
        assign_log_flights(con, r["log_id"])


def fill_drone_defaults(con, drone_id):
    """Fill unassigned flights from this aircraft's project timeline."""
    assign_open_flights(con)


# ---------------------------------------------------------------------------------------------------------------------
# Named places (shapes drawn on a map) for takeoff/landing locations
# ---------------------------------------------------------------------------------------------------------------------
def _valid_shape(sh):
    try:
        if sh["type"] == "circle":
            lat, lon = map(float, sh["center"])
            return {"type": "circle", "center": [lat, lon], "radius_m": float(sh["radius_m"])} if float(sh["radius_m"]) > 0 else None
        if sh["type"] == "polygon":
            pts = [[float(p[0]), float(p[1])] for p in sh["points"]]
            return {"type": "polygon", "points": pts} if len(pts) >= 3 else None
    except (KeyError, TypeError, ValueError, IndexError):
        pass
    return None


def _in_shape(sh, lat, lon):
    if sh["type"] == "circle":
        c = sh["center"]
        dy = (lat - c[0]) * 111320.0
        dx = (lon - c[1]) * 111320.0 * math.cos(math.radians(c[0]))
        return dx * dx + dy * dy <= sh["radius_m"] ** 2
    inside, pts = False, sh["points"]
    for i in range(len(pts)):
        (y1, x1), (y2, x2) = pts[i], pts[i - 1]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _shape_size(sh):
    if sh["type"] == "circle":
        return math.pi * sh["radius_m"] ** 2
    lats, lons = [p[0] for p in sh["points"]], [p[1] for p in sh["points"]]
    return (max(lats) - min(lats)) * (max(lons) - min(lons)) * 111320.0 ** 2


def place_resolver(con):
    """Return place(lat, lon) -> (name, indoor, id) of the smallest named place containing the point,
    else (None, False, None)."""
    places = sorted(((_shape_size(sh), r["name"], bool(r["indoor"]), r["id"], sh)
                     for r in con.execute("SELECT id, name, indoor, shape FROM places")
                     for sh in [json.loads(r["shape"])]), key=lambda t: t[0])

    def place(lat, lon):
        if lat is None or lon is None:
            return None, False, None
        return next(((name, indoor, pid) for _, name, indoor, pid, sh in places if _in_shape(sh, lat, lon)),
                    (None, False, None))

    return place


@app.get("/api/places")
def list_places():
    """Named places with the (real) flights the current user can see in them, plus the takeoff/landing positions
    of those flights rounded to ~100 m as a hint where to draw. Admins get every place; others only the places
    that appear on a flight they can see."""
    con = conn()
    admin = bool(g.user["is_admin"])
    stats, pts = {}, {}
    for f in logbook_rows():
        real = f.get("verdict") not in ("ground", "uncertain")
        for pid in {f.get("takeoff_place_id"), f.get("landing_place_id")} - {None}:
            s = stats.setdefault(pid, {"flights": 0, "seconds": 0.0, "last": None})
            if real:
                s["flights"] += f.get("flight_count", 1)
                s["seconds"] += f.get("duration_s") or 0
                s["last"] = max(s["last"] or "", f.get("takeoff_utc") or "") or None
        for lat, lon in ((f.get("takeoff_lat"), f.get("takeoff_lon")), (f.get("landing_lat"), f.get("landing_lon"))):
            if lat is not None and lon is not None:
                k = (round(lat, 3), round(lon, 3))
                pts[k] = pts.get(k, 0) + 1
    places = [dict(id=r["id"], name=r["name"], indoor=bool(r["indoor"]), shape=json.loads(r["shape"]),
                   **stats.get(r["id"], {"flights": 0, "seconds": 0.0, "last": None}))
              for r in con.execute("SELECT * FROM places ORDER BY name") if admin or r["id"] in stats]
    return jsonify(places=places, points=[{"lat": k[0], "lon": k[1], "count": n} for k, n in pts.items()],
                   admin=admin)


@app.post("/api/admin/places")
def admin_add_place():
    admin_required()
    body = request.get_json(silent=True) or {}
    name, shape = (body.get("name") or "").strip(), _valid_shape(body.get("shape"))
    if not name or len(name) > 80 or shape is None:
        return jsonify(error="a name (max 80 characters) and a valid shape are required"), 400
    con = conn()
    cur = con.execute("INSERT INTO places (name, indoor, shape) VALUES (?,?,?)",
                      (name, int(bool(body.get("indoor"))), json.dumps(shape)))
    con.commit()
    return jsonify(id=cur.lastrowid)


@app.put("/api/admin/places/<int:place_id>")
def admin_update_place(place_id):
    admin_required()
    body = request.get_json(silent=True) or {}
    con = conn()
    row = con.execute("SELECT * FROM places WHERE id=?", (place_id,)).fetchone()
    if row is None:
        abort(404)
    name = (body.get("name") or row["name"]).strip()
    shape = _valid_shape(body["shape"]) if "shape" in body else json.loads(row["shape"])
    if not name or len(name) > 80 or shape is None:
        return jsonify(error="invalid name or shape"), 400
    indoor = int(bool(body["indoor"])) if "indoor" in body else row["indoor"]
    con.execute("UPDATE places SET name=?, indoor=?, shape=? WHERE id=?", (name, indoor, json.dumps(shape), place_id))
    con.commit()
    return jsonify(ok=True)


@app.delete("/api/admin/places/<int:place_id>")
def admin_delete_place(place_id):
    admin_required()
    con = conn()
    if con.execute("SELECT 1 FROM flight_records WHERE place_id=?", (place_id,)).fetchone():
        return jsonify(error="This location has historical records. Reassign or remove those records first."), 409
    con.execute("DELETE FROM places WHERE id=?", (place_id,))
    con.commit()
    return jsonify(ok=True)


def admin_required():
    if not g.user["is_admin"]:
        abort(403)


@app.get("/locations")
def locations_page():
    return send_from_directory(PAGES_DIR, "locations.html")


@app.get("/people")
def people_page():
    return send_from_directory(PAGES_DIR, "people.html")


@app.get("/projects")
def projects_page():
    return send_from_directory(PAGES_DIR, "projects.html")


@app.get("/aircraft")
def aircraft_page():
    return send_from_directory(PAGES_DIR, "aircraft.html")


ACCESS_ROLES = {"view": "See logs", "add": "Add logs", "full": "See, add and delete"}


@app.get("/api/projects")
def list_projects():
    """Projects this user can open. Admins get every project."""
    con = conn()
    if g.user["is_admin"]:
        rows = con.execute("SELECT id, name, 'full' AS role FROM projects ORDER BY name").fetchall()
    else:
        rows = con.execute(
            "SELECT p.id, p.name, m.role FROM projects p JOIN project_members m ON m.project_id = p.id"
            " WHERE m.user_id=? ORDER BY p.name", (g.user["id"],)).fetchall()
    out = []
    for r in rows:
        n = con.execute("SELECT COUNT(*) AS n FROM logs WHERE project_id=?", (r["id"],)).fetchone()["n"]
        members = [dict(user_id=m["user_id"], username=m["username"],
                        display_name=m["display_name"] or m["username"], role=m["role"])
                   for m in con.execute(
                       "SELECT m.user_id, m.role, u.username, u.display_name FROM project_members m"
                       " JOIN users u ON u.id = m.user_id WHERE m.project_id=? ORDER BY u.username", (r["id"],))]
        out.append({"id": r["id"], "name": r["name"], "role": r["role"], "log_count": n,
                    "can_add": r["role"] in ("add", "full"), "can_delete": r["role"] == "full" or bool(g.user["is_admin"]),
                    "members": members})
    return jsonify(out)


@app.post("/api/admin/projects")
def admin_add_project():
    admin_required()
    name = ((request.get_json(silent=True) or {}).get("name") or "").strip()
    if not name or len(name) > 80:
        return jsonify(error="a project name is required (max 80 characters)"), 400
    con = conn()
    try:
        cur = con.execute("INSERT INTO projects (name, created_at) VALUES (?,?)", (name, now()))
        con.commit()
    except sqlite3.IntegrityError:
        return jsonify(error="a project with that name already exists"), 409
    return jsonify(id=cur.lastrowid)


def read_catalog_csv():
    upload = request.files.get("file")
    if upload and upload.filename:
        raw = upload.read(1_000_001)
        if len(raw) > 1_000_000:
            raise historical.RecordError("CSV must be at most 1 MB.")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
    else:
        body = request.get_json(silent=True)
        text = body.get("csv") if isinstance(body, dict) else None
        if not isinstance(text, str) or len(text.encode("utf-8")) > 1_000_000:
            raise historical.RecordError("Send a CSV file of at most 1 MB.")
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")), strict=True)
    if not reader.fieldnames:
        raise historical.RecordError("CSV has no header row.")
    headers = [key.strip().casefold() for key in reader.fieldnames]
    if any(not key for key in headers) or len(set(headers)) != len(headers):
        raise historical.RecordError("CSV headers must be non-empty and unique.")
    rows = []
    for line, raw in enumerate(reader, 2):
        if len(rows) >= 5000:
            raise historical.RecordError("Import at most 5000 CSV rows.")
        if None in raw or any(value is None for value in raw.values()):
            raise historical.RecordError(f"Row {line}: incorrect number of columns.")
        record = {key.strip().casefold(): value.strip() for key, value in raw.items()}
        if any(record.values()):
            rows.append((line, record))
    if not rows:
        raise historical.RecordError("CSV contains no records.")
    return headers, rows


@app.get("/api/admin/projects/template")
def project_csv_template():
    admin_required()
    return Response("project,person,role\nExample project,Example Pilot,add\n", mimetype="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="projects.csv"'})


@app.post("/api/admin/projects/import")
def admin_import_projects():
    admin_required()
    con = conn()
    try:
        headers, rows = read_catalog_csv()
        if "project" not in headers or "person" not in headers or set(headers) - {"project", "person", "role"}:
            raise historical.RecordError("CSV columns must be project,person,role (role is optional).")
        names = qualifications.crew_keys(con)
        active = {r["id"] for r in con.execute("SELECT id FROM users WHERE is_active=1")}
        projects = {}
        for line, row in rows:
            name, person = row["project"], row["person"]
            if not name or len(name) > 80:
                raise historical.RecordError(f"Row {line}: project name is required (max 80 characters).")
            project = projects.setdefault(name.casefold(), {"name": name, "members": {}})
            role = row.get("role") or "add"
            if role not in ACCESS_ROLES:
                raise historical.RecordError(f"Row {line}: role must be view, add or full.")
            if not person:
                if row.get("role"):
                    raise historical.RecordError(f"Row {line}: a role needs a person.")
                continue
            uid = names.get(person.casefold())
            if uid is None or uid not in active:
                raise historical.RecordError(f"Row {line}: unknown or ambiguous person '{person}'. Use a unique display name or alias.")
            if uid in project["members"] and project["members"][uid] != role:
                raise historical.RecordError(f"Row {line}: conflicting roles for '{person}'.")
            project["members"][uid] = role
        created = updated = 0
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            for project in projects.values():
                existing = con.execute("SELECT id FROM projects WHERE name=?", (project["name"],)).fetchone()
                if existing:
                    pid = existing["id"]
                    updated += 1
                else:
                    pid = con.execute("INSERT INTO projects(name,created_at) VALUES(?,?)",
                                      (project["name"], now())).lastrowid
                    created += 1
                con.execute("DELETE FROM project_members WHERE project_id=?", (pid,))
                con.executemany("INSERT INTO project_members(project_id,user_id,role) VALUES(?,?,?)",
                                [(pid, uid, role) for uid, role in project["members"].items()])
        return jsonify(created=created, updated=updated)
    except (historical.RecordError, csv.Error) as exc:
        return jsonify(error=str(exc)), 400


@app.put("/api/admin/projects/<int:project_id>")
def admin_update_project(project_id):
    admin_required()
    name = ((request.get_json(silent=True) or {}).get("name") or "").strip()
    if not name or len(name) > 80:
        return jsonify(error="a project name is required (max 80 characters)"), 400
    con = conn()
    row = con.execute("SELECT name FROM projects WHERE id=?", (project_id,)).fetchone()
    if row is None:
        return jsonify(error="no such project"), 404
    if name.casefold() == (row["name"] or "").casefold():
        return jsonify(ok=True)
    try:
        con.execute("UPDATE projects SET name=? WHERE id=?", (name, project_id))
        con.commit()
    except sqlite3.IntegrityError:
        con.rollback()
        return jsonify(error="a project with that name already exists"), 409
    return jsonify(ok=True)


@app.delete("/api/admin/projects/<int:project_id>")
def admin_delete_project(project_id):
    admin_required()
    con = conn()
    if not con.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
        return jsonify(error="no such project"), 404
    if con.execute("SELECT 1 FROM flight_records WHERE project_id=?", (project_id,)).fetchone():
        return jsonify(error="This project has historical records. Reassign or remove those records first."), 409
    con.execute("UPDATE logs SET project_id=NULL WHERE project_id=?", (project_id,))
    con.execute("UPDATE flights SET project_id=NULL WHERE project_id=?", (project_id,))
    con.execute("UPDATE drones SET default_project_id=NULL WHERE default_project_id=?", (project_id,))
    con.execute("UPDATE users SET default_project_id=NULL WHERE default_project_id=?", (project_id,))
    con.execute("DELETE FROM drone_projects WHERE project_id=?", (project_id,))
    con.execute("DELETE FROM project_members WHERE project_id=?", (project_id,))
    con.execute("DELETE FROM projects WHERE id=?", (project_id,))
    con.commit()
    return jsonify(ok=True)


@app.get("/api/admin/projects")
def admin_project_detail():
    admin_required()
    con = conn()
    projects = []
    for p in con.execute("SELECT * FROM projects ORDER BY name"):
        members = [dict(user_id=m["user_id"], username=m["username"], display_name=m["display_name"] or m["username"],
                        role=m["role"]) for m in con.execute(
            "SELECT m.user_id, m.role, u.username, u.display_name FROM project_members m"
            " JOIN users u ON u.id = m.user_id WHERE m.project_id=? ORDER BY u.username", (p["id"],))]
        n = con.execute("SELECT COUNT(*) AS n FROM logs WHERE project_id=?", (p["id"],)).fetchone()["n"]
        projects.append({"id": p["id"], "name": p["name"], "log_count": n, "members": members})
    return jsonify(projects=projects, roles=ACCESS_ROLES)


@app.put("/api/admin/projects/<int:project_id>/members")
def admin_set_member(project_id):
    admin_required()
    body = request.get_json(silent=True) or {}
    role = body.get("role")
    try:
        user_id = int(body.get("user_id"))
    except (TypeError, ValueError):
        return jsonify(error="choose a user"), 400
    con = conn()
    if not con.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
        return jsonify(error="no such project"), 404
    if not con.execute("SELECT 1 FROM users WHERE id=? AND is_active=1", (user_id,)).fetchone():
        return jsonify(error="no such user"), 404
    if role in ACCESS_ROLES:
        con.execute("INSERT INTO project_members (project_id, user_id, role) VALUES (?,?,?)"
                    " ON CONFLICT(project_id, user_id) DO UPDATE SET role=excluded.role",
                    (project_id, user_id, role))
    elif role in (None, ""):
        con.execute("DELETE FROM project_members WHERE project_id=? AND user_id=?", (project_id, user_id))
    else:
        return jsonify(error="choose an access level"), 400
    con.commit()
    return jsonify(ok=True)


def _group_unlinked_by_board(entries):
    """One Aircraft-page row per flight-controller id, even if airframe names differ."""
    leftover, by_board = [], {}
    for e in entries:
        bids = e.get("board_ids") or []
        if len(bids) != 1:
            leftover.append(e)
            continue
        by_board.setdefault(bids[0], []).append(e)
    out = leftover
    for bid, group in by_board.items():
        if len(group) == 1:
            e = dict(group[0])
            e.setdefault("names", [e["name"]])
            out.append(e)
            continue
        names, folders, cand, seen_c = [], [], [], set()
        count = 0
        for e in sorted(group, key=lambda x: (-x["count"], x["name"].casefold())):
            names.append(e["name"])
            count += e["count"]
            for folder in e["folders"]:
                if folder not in folders:
                    folders.append(folder)
            for c in e.get("candidates") or []:
                if c["id"] not in seen_c:
                    seen_c.add(c["id"])
                    cand.append(c)
        out.append({
            "name": names[0], "names": names, "count": count,
            "folders": folders, "board_ids": [bid], "candidates": cand,
        })
    out.sort(key=lambda e: (-e["count"], e["name"].casefold()))
    return out


def drone_payload(con, d):
    aliases = [r["alias"] for r in con.execute(
        "SELECT alias FROM drone_aliases WHERE drone_id=? ORDER BY alias", (d["id"],))]
    n = con.execute("SELECT COUNT(DISTINCT log_id) AS n FROM aircraft WHERE drone_id=?", (d["id"],)).fetchone()["n"]
    proj = con.execute("SELECT name FROM projects WHERE id=?", (d["default_project_id"],)).fetchone() if d["default_project_id"] else None
    periods = drone_periods(con, d["id"])
    return {"id": d["id"], "name": d["name"], "unique_id": d["unique_id"],
            "default_project_id": d["default_project_id"],
            "default_project_name": proj["name"] if proj else None,
            "projects": periods,
            "manager": d["manager"], "current_user": d["current_user"], "status": d["status"],
            "bought_on": d["bought_on"], "inventory_project": d["inventory_project"],
            "comments": d["comments"], "aliases": aliases, "log_count": n}


def fleet_payload(con, include_unlinked=False, project_ids=None):
    """Fleet list. Unlinked log names and the project menu are only filled when include_unlinked is true.
    project_ids, if given, keeps only aircraft whose default or timeline project is in that set."""
    drones, names = drone_name_map(con)
    if project_ids is None:
        rows = con.execute("SELECT * FROM drones ORDER BY name")
    elif not project_ids:
        rows = []
    else:
        q = ",".join("?" * len(project_ids))
        rows = con.execute(
            f"SELECT * FROM drones WHERE default_project_id IN ({q})"
            f" OR id IN (SELECT drone_id FROM drone_projects WHERE project_id IN ({q})) ORDER BY name",
            (*project_ids, *project_ids))
    fleet = [drone_payload(con, d) for d in rows]
    unlinked, projects = [], []
    if include_unlinked:
        seen = {}
        for a in con.execute(
                "SELECT a.name AS ac_name, l.name AS log_name FROM aircraft a "
                "JOIN logs l ON l.id = a.log_id WHERE a.name IS NOT NULL AND TRIM(a.name) != ''"):
            ac_name = (a["ac_name"] or "").strip()
            if not ac_name or resolve_drone(names, drones, ac_name) is not None:
                continue
            key = ac_name.casefold()
            e = seen.setdefault(key, {"name": ac_name, "count": 0, "folders": [], "board_ids": []})
            e["count"] += 1
            folder = log_folder_label(a["log_name"])
            if folder and folder not in e["folders"]:
                e["folders"].append(folder)
            for tok in identity_tokens(ac_name):
                if tok not in e["board_ids"]:
                    e["board_ids"].append(tok)
        unlinked = []
        for e in seen.values():
            cand, seen_ids = [], set()
            for folder in e["folders"]:
                for d in matching_drones_for_folder(con, folder):
                    if d["id"] not in seen_ids:
                        seen_ids.add(d["id"])
                        cand.append({"id": d["id"], "name": d["name"]})
            e["candidates"] = cand
            unlinked.append(e)
        unlinked = _group_unlinked_by_board(unlinked)
        projects = [dict(id=p["id"], name=p["name"]) for p in con.execute("SELECT id, name FROM projects ORDER BY name")]
    return dict(drones=fleet, unlinked=unlinked, projects=projects)


@app.get("/api/drones")
def list_drones():
    """Fleet aircraft. Non-admins only see aircraft whose default project they belong to."""
    con = conn()
    admin = bool(g.user["is_admin"])
    pids = None
    if not admin:
        pids = [r["project_id"] for r in con.execute(
            "SELECT project_id FROM project_members WHERE user_id=?", (g.user["id"],))]
    return jsonify(fleet_payload(con, include_unlinked=admin, project_ids=pids))


@app.get("/api/admin/drones")
def admin_drones():
    """Fleet aircraft, aliases, unlinked names from logs, and projects for the default-project menu."""
    admin_required()
    return jsonify(fleet_payload(conn(), include_unlinked=True))


@app.post("/api/admin/drones")
def admin_add_drone():
    admin_required()
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name or len(name) > 80:
        return jsonify(error="an aircraft name is required (max 80 characters)"), 400
    uid = parse_unique_id(body.get("unique_id"))
    if uid is False:
        return jsonify(error="a unique ID is required (max 40 characters)"), 400
    project_id = parse_project_id(body.get("default_project_id"))
    if project_id is False:
        return jsonify(error="no such project"), 400
    con = conn()
    if name_taken(con, name):
        return jsonify(error="that name is already used by an aircraft or alias"), 409
    if unique_id_taken(con, uid):
        return jsonify(error="that ID is already used by another aircraft"), 409
    if project_id is not None and not con.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
        return jsonify(error="no such project"), 404
    status = parse_status(body.get("status")) if "status" in body else None
    if status is False:
        return jsonify(error="status must be Ready, Maintenance, Development or Discard"), 400
    try:
        cur = con.execute(
            "INSERT INTO drones (name, unique_id, default_project_id, manager, current_user, status,"
            " bought_on, inventory_project, comments, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (name, uid, project_id, clip_text(body.get("manager"), 80), clip_text(body.get("current_user"), 80),
             status, clip_text(body.get("bought_on"), 20), clip_text(body.get("inventory_project"), 80),
             clip_text(body.get("comments"), 400), now()))
    except sqlite3.IntegrityError:
        return jsonify(error="that ID is already used by another aircraft"), 409
    relink_all_aircraft(con)
    seed_drone_period(con, cur.lastrowid, project_id, datetime.now(timezone.utc).date().isoformat())
    fill_drone_defaults(con, cur.lastrowid)
    con.commit()
    return jsonify(id=cur.lastrowid)


@app.put("/api/admin/drones/<int:drone_id>")
def admin_update_drone(drone_id):
    admin_required()
    body = request.get_json(silent=True) or {}
    con = conn()
    row = con.execute("SELECT * FROM drones WHERE id=?", (drone_id,)).fetchone()
    if row is None:
        return jsonify(error="no such aircraft"), 404
    name = row["name"]
    if "name" in body:
        name = (body.get("name") or "").strip()
        if not name or len(name) > 80:
            return jsonify(error="an aircraft name is required (max 80 characters)"), 400
        if name_taken(con, name, drone_id):
            return jsonify(error="that name is already used by an aircraft or alias"), 409
    uid = row["unique_id"]
    if "unique_id" in body:
        uid = parse_unique_id(body.get("unique_id"))
        if uid is False:
            return jsonify(error="a unique ID is required (max 40 characters)"), 400
        if unique_id_taken(con, uid, drone_id):
            return jsonify(error="that ID is already used by another aircraft"), 409
    project_id = row["default_project_id"]
    if "default_project_id" in body:
        project_id = parse_project_id(body.get("default_project_id"))
        if project_id is False:
            return jsonify(error="no such project"), 400
        if project_id is not None and not con.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            return jsonify(error="no such project"), 404
    status = row["status"]
    if "status" in body:
        status = parse_status(body.get("status"))
        if status is False:
            return jsonify(error="status must be Ready, Maintenance, Development or Discard"), 400
    manager = clip_text(body.get("manager"), 80) if "manager" in body else row["manager"]
    current_user = clip_text(body.get("current_user"), 80) if "current_user" in body else row["current_user"]
    bought_on = clip_text(body.get("bought_on"), 20) if "bought_on" in body else row["bought_on"]
    inventory_project = clip_text(body.get("inventory_project"), 80) if "inventory_project" in body else row["inventory_project"]
    comments = clip_text(body.get("comments"), 400) if "comments" in body else row["comments"]
    old_name = row["name"]
    try:
        if name.casefold() != (old_name or "").casefold():
            con.execute("DELETE FROM drone_aliases WHERE drone_id=? AND alias=?", (drone_id, name))
        con.execute(
            "UPDATE drones SET name=?, unique_id=?, default_project_id=?, manager=?, current_user=?,"
            " status=?, bought_on=?, inventory_project=?, comments=? WHERE id=?",
            (name, uid, project_id, manager, current_user, status, bought_on, inventory_project, comments, drone_id))
        if name.casefold() != (old_name or "").casefold() and old_name and not name_taken(con, old_name, drone_id):
            try:
                con.execute("INSERT INTO drone_aliases (drone_id, alias) VALUES (?,?)", (drone_id, old_name))
            except sqlite3.IntegrityError:
                pass
    except sqlite3.IntegrityError:
        con.rollback()
        return jsonify(error="that name or ID is already used by another aircraft"), 409
    relink_all_aircraft(con)
    if "default_project_id" in body:
        seed_drone_period(con, drone_id, project_id, parse_loose_date(bought_on) or first_flight_day(con, drone_id))
    fill_drone_defaults(con, drone_id)
    con.commit()
    return jsonify(ok=True)


@app.put("/api/admin/drones/<int:drone_id>/projects")
def admin_set_drone_projects(drone_id):
    """Replace this aircraft's project timeline. Empty until is the current project."""
    admin_required()
    body = request.get_json(silent=True) or {}
    periods = body.get("periods")
    if not isinstance(periods, list):
        return jsonify(error="send a list of periods"), 400
    con = conn()
    if not con.execute("SELECT 1 FROM drones WHERE id=?", (drone_id,)).fetchone():
        return jsonify(error="no such aircraft"), 404
    err = replace_drone_periods(con, drone_id, periods)
    if err:
        return jsonify(error=err), 400
    fill_drone_defaults(con, drone_id)
    con.commit()
    return jsonify(ok=True, projects=drone_periods(con, drone_id))


@app.post("/api/admin/drones/import")
def admin_import_drones():
    """Create or update fleet aircraft from the organisation CSV (AC Nice Name, Serial Unique, …)."""
    admin_required()
    con = conn()
    created = updated = skipped = 0
    try:
        headers, rows = read_catalog_csv()
        def col(*names):
            return next((name.casefold() for name in names if name.casefold() in headers), None)
        name_k = col("AC Nice Name", "name")
        uid_k = col("Serial Unique", "unique_id", "serial")
        if not name_k or not uid_k:
            raise historical.RecordError("CSV needs columns AC Nice Name and Serial Unique.")
        mgr_k = col("Manager (Post Holder)", "manager")
        user_k = col("Current User", "current_user")
        status_k = col("Status")
        bought_k = col("Date Bought", "bought_on")
        proj_k = col("Project", "inventory_project")
        effective_k = col("project_effective_date", "Project Effective Date")
        cmt_k = col("Comments", "comments")
        seen = set()
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            for line, rec in rows:
                name, uid = rec[name_k], rec[uid_k]
                if not name or not uid:
                    raise historical.RecordError(f"Row {line}: aircraft name and serial are required.")
                if len(name) > 80 or len(uid) > 40:
                    raise historical.RecordError(f"Row {line}: name must be at most 80 characters and serial at most 40.")
                if uid.casefold() in seen:
                    raise historical.RecordError(f"Row {line}: duplicate aircraft serial '{uid}'.")
                seen.add(uid.casefold())
                existing = find_import_drone(con, name, uid)
                if unique_id_taken(con, uid, existing["id"] if existing else None):
                    raise historical.RecordError(f"Row {line}: serial '{uid}' is already used.")
                project_label = rec.get(proj_k) or ""
                proj_id, proj_label = match_inventory_project(con, project_label)
                if project_label and not proj_id:
                    raise historical.RecordError(f"Row {line}: unknown or ambiguous project '{project_label}'. Import projects first.")
                effective = rec.get(effective_k) or ""
                if effective and (parse_date_override(effective) is False or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", effective)):
                    raise historical.RecordError(f"Row {line}: project_effective_date must be YYYY-MM-DD.")
                if existing and proj_id and proj_id != existing["default_project_id"] and not effective:
                    raise historical.RecordError(f"Row {line}: changing the project requires project_effective_date (YYYY-MM-DD).")
                if effective and not proj_id:
                    raise historical.RecordError(f"Row {line}: project_effective_date requires a project.")
                status = parse_status(rec.get(status_k) if status_k else None)
                if status is False:
                    status = clip_text(rec.get(status_k), 40)
                fields = {
                    "name": unique_import_name(con, name, uid, existing["id"] if existing else None), "unique_id": uid,
                    "default_project_id": proj_id if proj_id else (existing["default_project_id"] if existing else None),
                    "inventory_project": proj_label if project_label else (existing["inventory_project"] if existing else None),
                }
                for field, key, limit in (("manager", mgr_k, 80), ("current_user", user_k, 80),
                                          ("bought_on", bought_k, 20), ("comments", cmt_k, 400)):
                    fields[field] = clip_text(rec.get(key), limit) if key else (existing[field] if existing else None)
                fields["status"] = status if status_k else (existing["status"] if existing else None)
                if existing:
                    apply_drone_fields(con, existing["id"], fields, existing["name"])
                    drone_id = existing["id"]
                    updated += 1
                else:
                    cur = con.execute(
                        "INSERT INTO drones (name, unique_id, default_project_id, manager, current_user, status,"
                        " bought_on, inventory_project, comments, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (fields["name"], uid, fields["default_project_id"], fields["manager"],
                         fields["current_user"], fields["status"], fields["bought_on"], fields["inventory_project"],
                         fields["comments"], now()))
                    drone_id = cur.lastrowid
                    created += 1
                if effective:
                    import_drone_project_period(con, drone_id, proj_id, effective)
                else:
                    seed_drone_period(con, drone_id, fields["default_project_id"], parse_loose_date(fields["bought_on"]))
            relink_all_aircraft(con)
            assign_open_flights(con)
        return jsonify(created=created, updated=updated, skipped=skipped, errors=[])
    except (historical.RecordError, csv.Error, sqlite3.IntegrityError) as exc:
        return jsonify(error=str(exc)), 400


@app.get("/api/admin/drones/template")
def aircraft_csv_template():
    admin_required()
    return Response("AC Nice Name,Serial Unique,Project,project_effective_date\n"
                    "Example aircraft,EXAMPLE-001,Example project,2024-01-01\n", mimetype="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="aircraft.csv"'})


@app.delete("/api/admin/drones/<int:drone_id>")
def admin_delete_drone(drone_id):
    admin_required()
    con = conn()
    if not con.execute("SELECT 1 FROM drones WHERE id=?", (drone_id,)).fetchone():
        return jsonify(error="no such aircraft"), 404
    if con.execute("SELECT 1 FROM flight_records WHERE drone_id=?", (drone_id,)).fetchone():
        return jsonify(error="This aircraft has historical records. Reassign or remove those records first."), 409
    con.execute("UPDATE aircraft SET drone_id=NULL WHERE drone_id=?", (drone_id,))
    con.execute("DELETE FROM drones WHERE id=?", (drone_id,))
    con.commit()
    return jsonify(ok=True)


@app.post("/api/admin/drone-aliases")
def admin_add_drone_alias():
    admin_required()
    body = request.get_json(silent=True) or {}
    alias = (body.get("alias") or "").strip()
    if not alias or len(alias) > 80:
        return jsonify(error="alias must be 1-80 characters"), 400
    try:
        drone_id = int(body.get("drone_id"))
    except (TypeError, ValueError):
        return jsonify(error="choose an aircraft"), 400
    con = conn()
    if not con.execute("SELECT 1 FROM drones WHERE id=?", (drone_id,)).fetchone():
        return jsonify(error="no such aircraft"), 404
    if name_taken(con, alias, drone_id):
        return jsonify(error="that name is already used by an aircraft or alias"), 409
    try:
        con.execute("INSERT INTO drone_aliases (drone_id, alias) VALUES (?,?)", (drone_id, alias))
    except sqlite3.IntegrityError:
        return jsonify(error="that name is already used by an aircraft or alias"), 409
    remember_drone_log_identity(con, drone_id, alias)
    relink_all_aircraft(con)
    fill_drone_defaults(con, drone_id)
    con.commit()
    return jsonify(ok=True)


@app.delete("/api/admin/drone-aliases")
def admin_delete_drone_alias():
    admin_required()
    alias = ((request.get_json(silent=True) or {}).get("alias") or "").strip()
    con = conn()
    con.execute("DELETE FROM drone_aliases WHERE alias=?", (alias,))
    relink_all_aircraft(con)
    con.commit()
    return jsonify(ok=True)


@app.put("/api/logs/<int:log_id>/project")
def set_log_project(log_id):
    admin_required()
    con = conn()
    visible_log(con, log_id, "id")
    raw = (request.get_json(silent=True) or {}).get("project_id")
    project_id = int(raw) if isinstance(raw, int) or (isinstance(raw, str) and raw.isdigit()) else None
    if raw not in (None, "") and project_id is None:
        return jsonify(error="no such project"), 400
    if project_id is not None and not con.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
        return jsonify(error="no such project"), 404
    con.execute("UPDATE logs SET project_id=? WHERE id=?", (project_id, log_id))
    con.commit()
    return jsonify(ok=True)


@app.put("/api/logs/<int:log_id>/date")
def set_log_date(log_id):
    """Set or clear the calendar date used when the file clock is missing or 1980. Time of day is kept."""
    con = conn()
    row = visible_log(con, log_id)
    if not can_set_log_date(con, row):
        return jsonify(error="you cannot set the date of this log"), 403
    value = parse_date_override((request.get_json(silent=True) or {}).get("date_override"))
    if value is False:
        return jsonify(error="enter a date as YYYY-MM-DD"), 400
    con.execute("UPDATE logs SET date_override=? WHERE id=?", (value, log_id))
    con.commit()
    out = log_dict(con.execute("SELECT * FROM logs WHERE id=?", (log_id,)).fetchone())
    out["can_edit_date"] = True
    return jsonify(ok=True, date_override=value, date_ok=out["date_ok"], start_utc=out.get("start_utc"))


@app.put("/api/flights/<int:flight_id>/project")
def set_flight_project(flight_id):
    raw = (request.get_json(silent=True) or {}).get("project_id")
    project_id = parse_project_id(raw)
    if project_id is False:
        return jsonify(error="no such project"), 400
    con = conn()
    msg, code = apply_flight_project(con, flight_id, project_id)
    if msg == "missing":
        abort(404)
    if msg:
        return jsonify(error=msg), code
    con.commit()
    name = con.execute("SELECT name FROM projects WHERE id=?", (project_id,)).fetchone() if project_id else None
    return jsonify(ok=True, project_id=project_id, project_name=name["name"] if name else None)


@app.put("/api/flights/project")
def set_flights_project():
    """Set the project on several flights. null clears the project."""
    body = request.get_json(silent=True) or {}
    ids = body.get("flight_ids") or []
    try:
        ids = [int(x) for x in ids]
    except (TypeError, ValueError):
        return jsonify(error="choose flights"), 400
    if not ids:
        return jsonify(error="choose flights"), 400
    if "project_id" not in body:
        return jsonify(error="choose a project"), 400
    project_id = parse_project_id(body.get("project_id"))
    if project_id is False:
        return jsonify(error="no such project"), 400
    con = conn()
    for fid in ids:
        msg, code = apply_flight_project(con, fid, project_id)
        if msg == "missing":
            abort(404)
        if msg:
            return jsonify(error=msg), code
    con.commit()
    return jsonify(ok=True, updated=len(ids))


def parse_user_id(raw):
    if raw in (None, ""):
        return None
    if isinstance(raw, int) or (isinstance(raw, str) and str(raw).isdigit()):
        return int(raw)
    return False


def visible_editable_flight(con, flight_id):
    row = con.execute(
        "SELECT f.id, f.project_id, l.id AS log_id, l.project_id AS log_project_id, l.uploaded_by"
        " FROM flights f JOIN aircraft a ON a.id = f.aircraft_id JOIN logs l ON l.id = a.log_id WHERE f.id=?",
        (flight_id,)).fetchone()
    if row is None:
        return None, "missing"
    where, params = flight_own_clause()
    seen = con.execute(
        "SELECT f.id FROM flights f JOIN aircraft a ON a.id = f.aircraft_id JOIN logs l ON l.id = a.log_id"
        f" WHERE f.id=? AND {where}", (flight_id, *params)).fetchone()
    if seen is None:
        return None, "missing"
    if not can_edit_flight_project(con, row):
        return None, "forbidden"
    return row, None


def apply_flight_project(con, flight_id, project_id):
    row, err = visible_editable_flight(con, flight_id)
    if err == "missing":
        return "missing", 404
    if err == "forbidden":
        return "you cannot change the project of this flight", 403
    if project_id is not None and not live_project(con, project_id):
        return "no such project", 404
    if project_id is not None and not can_upload(con, project_id) and not g.user["is_admin"]:
        return "you cannot put flights in this project", 403
    con.execute("UPDATE flights SET project_id=?, project_source='manual' WHERE id=?", (project_id, flight_id))
    return None, None


def apply_flight_crew(con, flight_id, body):
    updates, params = [], []
    for role in ROLES:
        key = role + "_user_id"
        if key not in body:
            continue
        uid = parse_user_id(body.get(key))
        if uid is False:
            return "choose a person", 400
        if uid is not None and not con.execute("SELECT 1 FROM users WHERE id=? AND is_active=1", (uid,)).fetchone():
            return "no such user", 404
        updates.append(f"{role}_user_id=?")
        params.append(uid)
    if not updates:
        return "nothing to update", 400
    con.execute(f"UPDATE flights SET {', '.join(updates)} WHERE id=?", (*params, flight_id))
    return None, None


@app.put("/api/flights/crew")
def set_flights_crew():
    """Set PIC/PAC/GCS on several flights. Omitted fields are left as they are; null clears an override."""
    body = request.get_json(silent=True) or {}
    ids = body.get("flight_ids") or []
    try:
        ids = [int(x) for x in ids]
    except (TypeError, ValueError):
        return jsonify(error="choose flights"), 400
    if not ids:
        return jsonify(error="choose flights"), 400
    patch = {k: body[k] for k in ("pic_user_id", "pac_user_id", "gcs_user_id") if k in body}
    if not patch:
        return jsonify(error="choose a PIC, PAC or GCS"), 400
    con = conn()
    for fid in ids:
        row, err = visible_editable_flight(con, fid)
        if err == "missing":
            abort(404)
        if err == "forbidden":
            return jsonify(error="you cannot edit names on one of these flights"), 403
        msg, code = apply_flight_crew(con, fid, patch)
        if msg:
            return jsonify(error=msg), code
    con.commit()
    return jsonify(ok=True, updated=len(ids))


@app.put("/api/logbook/records")
def update_logbook_records():
    """Atomically edit mixed logbook sources, or hide/restore them without deleting data."""
    session_required()
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Send a JSON object."), 400
    keys = body.get("record_keys")
    if not isinstance(keys, list) or not keys or len(keys) > 5000:
        return jsonify(error="Select between 1 and 5000 source records."), 400
    targets = set()
    for key in keys:
        if not isinstance(key, str) or not re.fullmatch(r"(log|paper):[1-9][0-9]{0,18}", key):
            return jsonify(error="Invalid record key."), 400
        kind, identity = key.split(":")
        if int(identity) > 2**63 - 1:
            return jsonify(error="Invalid record key."), 400
        targets.add((kind, int(identity)))
    changes = {k: body[k] for k in ("project_id", "pic_user_id", "pac_user_id", "gcs_user_id", "hidden") if k in body}
    if not changes:
        return jsonify(error="Choose fields to change."), 400
    if ("hidden" in changes or any(kind == "paper" for kind, _ in targets)) and not g.user["is_admin"]:
        return jsonify(error="Administrator access is required."), 403
    if "hidden" in changes and type(changes["hidden"]) is not bool:
        return jsonify(error="Hidden must be true or false."), 400
    con = conn()
    try:
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            for key in ("pic_user_id", "pac_user_id", "gcs_user_id"):
                if key not in changes:
                    continue
                uid = parse_user_id(changes[key])
                if uid is False or (uid is not None and not 1 <= uid <= 2**63 - 1):
                    raise historical.RecordError("Choose an active person.")
                if uid is not None and not con.execute(
                        "SELECT 1 FROM users WHERE id=? AND is_active=1", (uid,)).fetchone():
                    raise historical.RecordError("Choose an active person.")
                changes[key] = uid
            if "project_id" in changes:
                pid = parse_project_id(changes["project_id"])
                if pid is False or (pid is not None and not 1 <= pid <= 2**63 - 1):
                    raise historical.RecordError("Choose an existing project.")
                if pid is not None and not live_project(con, pid):
                    raise historical.RecordError("Choose an existing project.")
                changes["project_id"] = pid
            for kind, identity in sorted(targets):
                if kind == "paper":
                    row = historical.visible(con, g.user, identity)
                    values = {key: row[key] for key in historical.FIELDS}
                    values.update({key: value for key, value in changes.items() if key != "hidden"})
                    if values["pic_user_id"] is None or values["project_id"] is None:
                        raise historical.RecordError("Paper records require a PIC and project; choose Keep or a person/project.")
                    if any(key != "hidden" for key in changes):
                        historical.store(con, values, g.user["id"], now(), identity)
                    if "hidden" in changes:
                        con.execute("UPDATE flight_records SET hidden=? WHERE id=?", (int(changes["hidden"]), identity))
                else:
                    _, err = visible_editable_flight(con, identity)
                    if err:
                        raise historical.RecordError("No such editable flight.", 404 if err == "missing" else 403)
                    if "project_id" in changes:
                        msg, code = apply_flight_project(con, identity, changes["project_id"])
                        if msg:
                            raise historical.RecordError(msg, code)
                    if any(key.endswith("_user_id") for key in changes):
                        msg, code = apply_flight_crew(con, identity, changes)
                        if msg:
                            raise historical.RecordError(msg, code)
                    if "hidden" in changes:
                        con.execute("UPDATE flights SET hidden=? WHERE id=?", (int(changes["hidden"]), identity))
        return jsonify(ok=True, updated=len(targets))
    except historical.RecordError as exc:
        return jsonify(error=str(exc)), exc.status


@app.put("/api/flights/<int:flight_id>/crew")
def set_flight_crew(flight_id):
    con = conn()
    row, err = visible_editable_flight(con, flight_id)
    if err == "missing":
        abort(404)
    if err == "forbidden":
        return jsonify(error="you cannot edit names on this flight"), 403
    msg, code = apply_flight_crew(con, flight_id, request.get_json(silent=True) or {})
    if msg:
        return jsonify(error=msg), code
    con.commit()
    return jsonify(ok=True)


COMMENT_MAX = 4000


@app.put("/api/flights/<int:flight_id>/comment")
def set_flight_comment(flight_id):
    con = conn()
    row, err = visible_editable_flight(con, flight_id)
    if err == "missing":
        abort(404)
    if err == "forbidden":
        return jsonify(error="you cannot comment on this flight"), 403
    comment = ((request.get_json(silent=True) or {}).get("comment") or "").strip() or None
    if comment and len(comment) > COMMENT_MAX:
        return jsonify(error="comment is too long (max 4000 characters)"), 400
    con.execute("UPDATE flights SET comment=? WHERE id=?", (comment, flight_id))
    con.commit()
    return jsonify(ok=True, comment=comment)


def issue_setup_token(con, user_id):
    """Invalidate any unused setup links for this user and return a new one-time token."""
    con.execute("UPDATE password_setup_tokens SET used_at=? WHERE user_id=? AND used_at IS NULL", (now(), user_id))
    token = secrets.token_urlsafe(32)
    created = datetime.now(timezone.utc)
    expires = created + timedelta(hours=SETUP_HOURS)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    con.execute("INSERT INTO password_setup_tokens (user_id, token_hash, created_at, expires_at) VALUES (?,?,?,?)",
                (user_id, hashlib.sha256(token.encode()).hexdigest(), created.strftime(fmt), expires.strftime(fmt)))
    return token, expires.strftime(fmt)


def clean_email(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        return None
    email = value.strip()
    if not email:
        return ""
    if len(email) > 120 or any(c.isspace() for c in email) or email.count("@") != 1:
        return None
    local, domain = email.split("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        return None
    return email


def setup_url(token):
    return request.host_url.rstrip("/") + "/setup/" + token


def lookup_setup(token):
    row = conn().execute(
        "SELECT t.id AS token_id, t.expires_at, t.used_at, u.id AS user_id, u.username, u.display_name, u.is_active, u.provider "
        "FROM password_setup_tokens t JOIN users u ON u.id = t.user_id WHERE t.token_hash=?",
        (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    if (row is None or row["used_at"] or not row["is_active"] or row["expires_at"] <= now()
            or row["provider"] not in ("local", "log")):
        return None
    return row


@app.post("/api/admin/users")
def admin_add_user():
    admin_required()
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    display_name = (body.get("display_name") or "").strip() or None
    email = clean_email(body.get("email"))
    if not username or len(username) > 40 or any(c.isspace() for c in username):
        return jsonify(error="username must be 1–40 characters with no spaces"), 400
    if display_name and len(display_name) > 80:
        return jsonify(error="display name must be at most 80 characters"), 400
    if email is None:
        return jsonify(error="enter a valid email address"), 400
    con = conn()
    default_project_id = parse_project_id(body.get("default_project_id"))
    if default_project_id is False:
        return jsonify(error="no such project"), 400
    if default_project_id is not None and not live_project(con, default_project_id):
        return jsonify(error="no such project"), 404
    if con.execute("SELECT 1 FROM users WHERE email=? COLLATE NOCASE", (email,)).fetchone():
        return jsonify(error="that email address is already used"), 409
    try:
        cur = con.execute(
            "INSERT INTO users (username, display_name, email, password_hash, is_admin, created_at, default_project_id)"
            " VALUES (?,?,?,?,?,?,?)",
            (username, display_name, email or None, None, int(bool(body.get("is_admin"))), now(), default_project_id))
        token, expires_at = issue_setup_token(con, cur.lastrowid)
        con.commit()
    except sqlite3.IntegrityError:
        return jsonify(error="that username already exists"), 409
    return jsonify(id=cur.lastrowid, setup_url=setup_url(token), expires_at=expires_at)


@app.delete("/api/admin/users/<int:user_id>/password")
def admin_remove_password(user_id):
    admin_required()
    con = conn()
    with db.write_lock, con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT id, provider, is_admin FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            return jsonify(error="no such user"), 404
        if row["provider"] != "local":
            return jsonify(error="This account does not use local password sign-in."), 400
        if row["is_admin"] and not con.execute(
                "SELECT 1 FROM users WHERE id!=? AND is_admin=1 AND is_active=1"
                " AND (provider='saml' OR (provider='local' AND password_hash IS NOT NULL)) LIMIT 1",
                (user_id,)).fetchone():
            return jsonify(error="Cannot remove the last administrator's login method."), 400
        con.execute("UPDATE users SET password_hash=NULL WHERE id=?", (user_id,))
        con.execute("UPDATE password_setup_tokens SET used_at=? WHERE user_id=? AND used_at IS NULL",
                    (now(), user_id))
    return jsonify(ok=True)


@app.post("/api/admin/users/<int:user_id>/setup-link")
def admin_new_setup_link(user_id):
    admin_required()
    con = conn()
    row = con.execute("SELECT id, is_active, provider FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None or not row["is_active"]:
        return jsonify(error="no such user"), 404
    if row["provider"] not in ("local", "log"):
        return jsonify(error="Passwords for this account are managed by its identity provider."), 403
    token, expires_at = issue_setup_token(con, user_id)
    con.commit()
    return jsonify(setup_url=setup_url(token), expires_at=expires_at)


@app.put("/api/admin/users/<int:user_id>/username")
@app.put("/api/admin/users/<int:user_id>/profile")
def admin_rename_user(user_id):
    admin_required()
    session_required()
    body = request.get_json(silent=True)
    profile = request.path.endswith("/profile")
    fields = {"username", "display_name", "email", "default_project_id"} if profile else {"username"}
    if not isinstance(body, dict) or set(body) != fields:
        return jsonify(error="send username, display_name, email and default_project_id" if profile else "send only a username"), 400
    display_name = None
    if profile:
        display_name = body["display_name"]
        if not isinstance(display_name, str):
            return jsonify(error="display name must be at most 80 characters without control characters"), 400
        display_name = display_name.strip()
        if len(display_name) > 80 or any(ord(c) < 32 or ord(c) == 127 for c in display_name):
            return jsonify(error="display name must be at most 80 characters without control characters"), 400
        display_name = display_name or None
    username = body["username"]
    if not isinstance(username, str):
        return jsonify(error="username must be 1-40 characters with no spaces"), 400
    username = username.strip()
    if not username or len(username) > 40 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in username):
        return jsonify(error="username must be 1-40 characters with no spaces or control characters"), 400
    con = conn()
    with db.write_lock, con:
        con.execute("BEGIN IMMEDIATE")
        user = con.execute("SELECT username,display_name FROM users WHERE id=?", (user_id,)).fetchone()
        if user is None:
            return jsonify(error="no such user"), 404
        if profile:
            email = clean_email(body["email"])
            if email is None:
                return jsonify(error="enter a valid email address"), 400
            if email and con.execute("SELECT 1 FROM users WHERE email=? COLLATE NOCASE AND id!=?",
                                     (email, user_id)).fetchone():
                return jsonify(error="that email address is already used"), 409
            project_id = parse_project_id(body["default_project_id"])
            if project_id is False:
                return jsonify(error="no such project"), 400
            if project_id is not None and not live_project(con, project_id):
                return jsonify(error="no such project"), 404
        if con.execute("SELECT 1 FROM users WHERE username=? AND id!=?", (username, user_id)).fetchone():
            return jsonify(error="that username already exists"), 409
        old_display = user["display_name"] if profile and display_name != user["display_name"] else None
        if old_display and any(
                r["display_name"].strip().casefold() == old_display.strip().casefold()
                for r in con.execute("SELECT display_name FROM users WHERE id!=? AND display_name IS NOT NULL",
                                     (user_id,))):
            old_display = None
        for name in filter(None, (display_name, old_display)):
            alias = con.execute("SELECT user_id FROM user_aliases WHERE alias=?", (name,)).fetchone()
            if alias and alias["user_id"] != user_id:
                return jsonify(error="a display name conflicts with another person's log alias; resolve the alias first"), 409
        if username != user["username"]:
            con.execute("UPDATE users SET username=? WHERE id=?", (username, user_id))
        if profile:
            if old_display and not con.execute("SELECT 1 FROM user_aliases WHERE alias=?", (old_display,)).fetchone():
                con.execute("INSERT INTO user_aliases(user_id,alias) VALUES (?,?)", (user_id, old_display))
            con.execute("UPDATE users SET display_name=? WHERE id=?", (display_name, user_id))
            con.execute("UPDATE users SET email=?, default_project_id=? WHERE id=?",
                        (email or None, project_id, user_id))
            assign_open_flights(con)
    return jsonify(ok=True)


@app.put("/api/admin/users/<int:user_id>")
def admin_update_user(user_id):
    admin_required()
    body = request.get_json(silent=True) or {}
    con = conn()
    if not con.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
        return jsonify(error="no such user"), 404
    if "email" in body:
        email = clean_email(body.get("email"))
        if email is None:
            return jsonify(error="enter a valid email address"), 400
        if con.execute("SELECT 1 FROM users WHERE email=? COLLATE NOCASE AND id!=?", (email, user_id)).fetchone():
            return jsonify(error="that email address is already used"), 409
        con.execute("UPDATE users SET email=? WHERE id=?", (email or None, user_id))
    if "default_project_id" in body:
        project_id = parse_project_id(body.get("default_project_id"))
        if project_id is False:
            return jsonify(error="no such project"), 400
        if project_id is not None and not live_project(con, project_id):
            return jsonify(error="no such project"), 404
        con.execute("UPDATE users SET default_project_id=? WHERE id=?", (project_id, user_id))
        assign_open_flights(con)
    if "email" not in body and "default_project_id" not in body:
        return jsonify(error="nothing to update"), 400
    con.commit()
    return jsonify(ok=True)


@app.get("/setup/<token>")
def setup_page(token):
    return send_from_directory(PAGES_DIR, "setup.html")


@app.get("/api/setup/<token>")
def setup_info(token):
    row = lookup_setup(token)
    if row is None:
        return jsonify(error="This link is no longer valid. Ask an admin for a new one."), 400
    return jsonify(username=row["username"], display_name=row["display_name"] or row["username"])


@app.post("/api/setup/<token>")
def setup_password(token):
    row = lookup_setup(token)
    if row is None:
        return jsonify(error="This link is no longer valid. Ask an admin for a new one."), 400
    password = (request.get_json(silent=True) or {}).get("password") or ""
    if not isinstance(password, str) or not 10 <= len(password) <= 1024:
        return jsonify(error="password must be 10–1024 characters"), 400
    con = conn()
    con.execute(
        "UPDATE users SET password_hash=?, provider=CASE WHEN provider='log' THEN 'local' ELSE provider END WHERE id=?",
        (generate_password_hash(password), row["user_id"]))
    con.execute("UPDATE password_setup_tokens SET used_at=? WHERE user_id=? AND used_at IS NULL", (now(), row["user_id"]))
    con.commit()
    return jsonify(ok=True, username=row["username"])


@app.delete("/api/admin/users/<int:user_id>")
def admin_delete_user(user_id):
    admin_required()
    if user_id == g.user["id"]:
        return jsonify(error="you cannot remove your own account"), 400
    con = conn()
    if con.execute("SELECT 1 FROM flight_records WHERE pic_user_id=? OR pac_user_id=? OR gcs_user_id=?",
                   (user_id, user_id, user_id)).fetchone():
        return jsonify(error="This person has historical records. Reassign or remove those records first."), 409
    row = con.execute("SELECT id, is_admin FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        return jsonify(error="no such user"), 404
    if row["is_admin"] and con.execute("SELECT COUNT(*) AS n FROM users WHERE is_admin=1 AND is_active=1 AND id!=?",
                                       (user_id,)).fetchone()["n"] < 1:
        return jsonify(error="the last admin account cannot be removed"), 400
    with db.write_lock, con:
        con.execute("BEGIN IMMEDIATE")
        if con.execute("SELECT 1 FROM qualification_submissions WHERE user_id=?", (user_id,)).fetchone():
            return jsonify(error="Remove this user's private qualification records and evidence before deleting the account."), 409
        for role in ROLES:
            con.execute(f"UPDATE flights SET {role}_user_id=NULL WHERE {role}_user_id=?", (user_id,))
        con.execute("DELETE FROM users WHERE id=?", (user_id,))
    return jsonify(ok=True)


@app.get("/api/admin/people")
def admin_people():
    """Users with their aliases, plus the names seen in logs that are not linked to any user yet."""
    admin_required()
    con = conn()
    users = []
    for u in con.execute("SELECT * FROM users ORDER BY username"):
        prov = u["provider"] or "local"
        users.append(dict(
            id=u["id"], username=u["username"], display_name=u["display_name"], email=u["email"],
            is_admin=bool(u["is_admin"]), has_password=bool(u["password_hash"]),
            provider=prov, pending=prov == "log",
            default_project_id=u["default_project_id"],
            aliases=[r["alias"] for r in con.execute(
                "SELECT alias FROM user_aliases WHERE user_id=? ORDER BY alias", (u["id"],))],
            qualifications=qualifications.qualifications_payload(con, g.user, u["id"])))
    resolve = person_resolver(con)
    unlinked = {}
    for a in con.execute("SELECT a.pic, a.pac, a.gcs FROM aircraft a JOIN logs l ON l.id = a.log_id"):
        for role in ROLES:
            name = (a[role] or "").strip()
            if name and resolve(role, name, None) is None:
                e = unlinked.setdefault(name.casefold(), {"name": name, "count": 0})
                e["count"] += 1
    projects = [dict(id=p["id"], name=p["name"]) for p in con.execute("SELECT id, name FROM projects ORDER BY name")]
    return jsonify(users=users, unlinked=sorted(unlinked.values(), key=lambda e: -e["count"]),
                   projects=projects)


@app.post("/api/admin/aliases")
def admin_add_alias():
    admin_required()
    body = request.get_json(silent=True) or {}
    alias = (body.get("alias") or "").strip()
    if not alias or len(alias) > 80:
        return jsonify(error="alias must be 1-80 characters"), 400
    con = conn()
    if not con.execute("SELECT 1 FROM users WHERE id=?", (body.get("user_id"),)).fetchone():
        return jsonify(error="no such user"), 404
    try:
        con.execute("INSERT INTO user_aliases (user_id, alias) VALUES (?,?)", (body["user_id"], alias))
        assign_open_flights(con)
        con.commit()
    except sqlite3.IntegrityError:
        return jsonify(error="alias is already linked to a user"), 409
    return jsonify(ok=True)


@app.delete("/api/admin/aliases")
def admin_delete_alias():
    admin_required()
    alias = ((request.get_json(silent=True) or {}).get("alias") or "").strip()
    con = conn()
    con.execute("DELETE FROM user_aliases WHERE alias=?", (alias,))
    con.commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------------------------------------------------
def current_version():
    if "PARSER_VERSION" not in app.config:
        app.config["PARSER_VERSION"] = parser_version()
    return app.config["PARSER_VERSION"]


def expected_parser_version(parser_name):
    if parser_name == mav_parse.PARSER_NAME:
        return mav_parse.PARSER_VERSION
    return current_version()


def outdated_sql():
    """Match logs whose stored parser version is not the one that would parse them today."""
    return ("((COALESCE(parser_name,'') = ? AND COALESCE(parser_version,'') != ?)"
            " OR (COALESCE(parser_name,'') != ? AND COALESCE(parser_version,'') != ?))",
            (mav_parse.PARSER_NAME, mav_parse.PARSER_VERSION,
             mav_parse.PARSER_NAME, current_version() or ""))


# File clocks before this year are treated as missing (Unix epoch 1970 / GPS epoch 1980).
CLOCK_MIN_YEAR = 1990
_UTC_KEYS = {
    "utc", "takeoff_utc", "landing_utc", "start_utc", "first_data_utc", "end_utc", "first_utc", "last_utc",
}


def parse_iso_utc(iso):
    """Parse an API/parser UTC string to a timezone-aware datetime, or None."""
    if not iso or not isinstance(iso, str):
        return None
    s = iso.strip()
    if s.endswith("Z"):
        s = s[:-1]
    if "." in s:
        try:
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc)
        except ValueError:
            s = s.split(".", 1)[0]
    try:
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def clock_implausible(start_utc):
    """True when the file has no calendar date, or one before 1990 (GPS/Unix epoch)."""
    dt = parse_iso_utc(start_utc)
    return dt is None or dt.year < CLOCK_MIN_YEAR


def parse_date_override(raw):
    """Return YYYY-MM-DD, None to clear, or False if the value is not a usable date."""
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        return False
    try:
        d = datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError:
        return False
    today = datetime.now(timezone.utc).date()
    if d.year < CLOCK_MIN_YEAR or d.year > today.year + 1:
        return False
    return d.isoformat()


def clock_shift_days(start_utc, date_override):
    """Days to add so the file clock's date becomes date_override. 0 if it cannot be shifted."""
    if not date_override:
        return 0
    dt = parse_iso_utc(start_utc)
    if dt is None:
        return 0
    try:
        target = datetime.strptime(date_override, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return 0
    start_date = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
    return (target - start_date).days


def shift_iso(iso, days):
    if not iso or not days:
        return iso
    dt = parse_iso_utc(iso)
    if dt is None:
        return iso
    out = dt + timedelta(days=days)
    frac = ""
    if isinstance(iso, str) and "." in iso:
        tail = iso.rsplit(".", 1)[-1].rstrip("Z")
        width = len(tail) if tail.isdigit() else 3
        frac = "." + f"{out.microsecond:06d}"[:width]
    return out.strftime("%Y-%m-%dT%H:%M:%S") + frac + "Z"


def apply_clock_to_obj(obj, days):
    """Shift utc / *_utc string fields in a dict or list. Skips upload/parse timestamps."""
    if not days or obj is None:
        return obj
    if isinstance(obj, list):
        for item in obj:
            apply_clock_to_obj(item, days)
        return obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _UTC_KEYS and isinstance(v, str):
                obj[k] = shift_iso(v, days)
            elif isinstance(v, (dict, list)):
                apply_clock_to_obj(v, days)
    return obj


def apply_row_clock(r):
    """Set date_ok and shift takeoff/landing times on a logbook/flight dict using that log's override."""
    raw_start = r.get("log_start_utc") if r.get("log_start_utc") is not None else r.get("start_utc")
    override = r.get("date_override")
    r["date_ok"] = bool(override) or not clock_implausible(raw_start)
    days = clock_shift_days(raw_start, override)
    if days:
        r["takeoff_utc"] = shift_iso(r.get("takeoff_utc"), days)
        r["landing_utc"] = shift_iso(r.get("landing_utc"), days)
        apply_clock_to_obj(r.get("takeoff"), days)
        apply_clock_to_obj(r.get("landing"), days)
    return r


def log_timing(row):
    timing = json.loads(row["timing"]) if row["timing"] else None
    if row["date_override"]:
        timing = dict(timing or {})
        timing["parsed_source"] = timing.get("source")
        timing["source"] = "manual"
        timing["effective_start_utc"] = shift_iso(
            row["start_utc"], clock_shift_days(row["start_utc"], row["date_override"]))
    return timing


def log_dict(row):
    d = dict(row)
    d["timing"] = log_timing(row)
    raw_start = d.get("start_utc")
    override = d.get("date_override")
    d["date_ok"] = bool(override) or not clock_implausible(raw_start)
    days = clock_shift_days(raw_start, override)
    if days:
        for k in ("start_utc", "first_data_utc", "end_utc"):
            d[k] = shift_iso(d.get(k), days)
    d["outdated"] = d["status"] == "done" and d["parser_version"] != expected_parser_version(d.get("parser_name"))
    d["has_log"] = d.pop("log_path") is not None
    d.pop("data_path", None)
    d.pop("paths_file", None)
    return d


def own_clause(alias=""):
    """Logs this user may see. Admins see every log. Others see their projects, and their own logs that are not in a project yet."""
    if g.user["is_admin"]:
        return "1=1", ()
    return (f"({alias}project_id IN (SELECT project_id FROM project_members WHERE user_id=?)"
            f" OR ({alias}project_id IS NULL AND {alias}uploaded_by=?))",
            (g.user["id"], g.user["id"]))


def project_arg_clause(alias=""):
    """Optional ?project= filter. 'none' is logs that are not in a project."""
    pid = request.args.get("project")
    if pid == "none":
        return f"{alias}project_id IS NULL", ()
    if pid and str(pid).isdigit():
        return f"{alias}project_id = ?", (int(pid),)
    return "1=1", ()


def list_clause(alias=""):
    where, params = own_clause(alias)
    extra, extra_params = project_arg_clause(alias)
    if extra != "1=1":
        return f"({where}) AND ({extra})", params + extra_params
    return where, params


def flight_project_expr():
    return "COALESCE(f.project_id, l.project_id)"


def flight_own_clause():
    """Flights this user may see, using the flight project when set and otherwise the log's project."""
    if g.user["is_admin"]:
        return "1=1", ()
    expr = flight_project_expr()
    return (f"({expr} IN (SELECT project_id FROM project_members WHERE user_id=?)"
            f" OR ({expr} IS NULL AND l.uploaded_by=?))",
            (g.user["id"], g.user["id"]))


def flight_list_clause():
    where, params = flight_own_clause()
    pid = request.args.get("project")
    expr = flight_project_expr()
    if pid == "none":
        return f"({where}) AND {expr} IS NULL", params
    if pid and str(pid).isdigit():
        return f"({where}) AND {expr} = ?", params + (int(pid),)
    return where, params


def role_on(con, project_id):
    if g.user["is_admin"]:
        return "full"
    if project_id is None:
        return None
    row = con.execute("SELECT role FROM project_members WHERE project_id=? AND user_id=?",
                      (project_id, g.user["id"])).fetchone()
    return row["role"] if row else None


def can_upload(con, project_id):
    if not con.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
        return False
    return role_on(con, project_id) in ("add", "full")


def can_upload_any(con):
    """True if this user may drop logs (project is assigned after parsing)."""
    if g.user["is_admin"]:
        return True
    return con.execute(
        "SELECT 1 FROM project_members WHERE user_id=? AND role IN ('add','full') LIMIT 1",
        (g.user["id"],)).fetchone() is not None


def can_delete_log(con, row):
    if g.user["is_admin"]:
        return True
    if row["project_id"] is None:
        return row["uploaded_by"] == g.user["id"]
    return role_on(con, row["project_id"]) == "full"


def can_edit_log_date(con, row):
    """Admin, full on the log's project, or the uploader when the log has no project."""
    return can_delete_log(con, row)


def can_set_log_date(con, log_row):
    """True if this user may type a calendar date for the log (same people who can edit a logbook row)."""
    if can_edit_log_date(con, log_row):
        return True
    for f in con.execute(
            "SELECT f.project_id, l.project_id AS log_project_id, l.uploaded_by"
            " FROM flights f JOIN aircraft a ON a.id = f.aircraft_id JOIN logs l ON l.id = a.log_id"
            " WHERE l.id=?", (log_row["id"],)):
        if can_edit_flight_project(con, f):
            return True
    return False


def effective_flight_project(row):
    return row["project_id"] if row["project_id"] is not None else row.get("log_project_id")


def can_edit_flight_project(con, row):
    if g.user["is_admin"]:
        return True
    pid = effective_flight_project(row)
    if pid is None:
        return row["uploaded_by"] == g.user["id"]
    return role_on(con, pid) == "full"


def visible_log(con, log_id, columns="*"):
    """Fetch a log row the current user may see, or 404 (also for other users' logs)."""
    where, params = own_clause()
    row = con.execute(f"SELECT {columns} FROM logs WHERE id=? AND {where}", (log_id, *params)).fetchone()
    if row is None:
        abort(404)
    return row


@app.get("/uploads")
def index():
    return send_from_directory(PAGES_DIR, "index.html")


@app.get("/")
@app.get("/logbook")
def logbook_page():
    return send_from_directory(PAGES_DIR, "logbook.html")


@app.get("/stats")
def stats_page():
    return send_from_directory(PAGES_DIR, "stats.html")


SAME_FLIGHT_SLACK_S = 60
TYPE_ORDER = {"onboard_sd": 0, "px4_ulog": 0, "ardupilot": 0, "gcs": 1, "mavlink_tlog": 1}


def _ts(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def _type_of(r):
    t = r.get("log_type")
    if t in TYPE_ORDER:
        return t
    return "onboard_sd" if (r.get("log_name") or "").endswith("_SD.data") else "gcs"  # logs parsed before types existed


def _same_aircraft(a, b):
    """The SD log only knows the airframe name, the GCS log the (often differently spelled) configuration name,
    so accept similar names, or the same ac_id when both takeoffs are close together."""
    if a["name"] == b["name"] or difflib.SequenceMatcher(None, a["name"], b["name"]).ratio() >= 0.8:
        return True
    if a["ac_id"] is None or a["ac_id"] != b["ac_id"] or None in (a["lat"], b["lat"]):
        return False
    return _km(a["lat"], a["lon"], b["lat"], b["lon"]) <= 2


def _km(lat1, lon1, lat2, lon2):
    dx = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    return 6371 * math.hypot(dx, math.radians(lat2 - lat1))


def merge_sources(flights):
    """Merge the same flight recorded by an onboard SD log and a ground station (GCS) log into one entry.

    Two flights are the same when they belong to the same aircraft and their takeoff-landing intervals overlap
    (with a small slack) and they come from different logs. The onboard log provides the flight data; the people and
    goal are taken from the first source that has them (GCS first, as the pre-flight messages are sent over the link).
    """
    groups = []
    for f in sorted(flights, key=lambda r: r["takeoff_utc"] or ""):
        f["log_type"] = _type_of(f)
        start = _ts(f["takeoff_utc"])
        end = _ts(f["landing_utc"]) or (start + (f["duration_s"] or 0) if start is not None else None)
        name = (f["aircraft"] or "").casefold()
        ident = {"name": name, "ac_id": f["ac_id"], "lat": f["takeoff_lat"], "lon": f["takeoff_lon"]}
        home = None
        if start is not None:
            for g in groups:
                linked_ids = {o["drone_id"] for o in g["flights"] if o["drone_id"] is not None}
                if (f["drone_id"] is not None and linked_ids and f["drone_id"] not in linked_ids):
                    continue
                if (_same_aircraft(g, ident) and g["end"] is not None and f["log_id"] not in g["logs"]
                        and start <= g["end"] + SAME_FLIGHT_SLACK_S and (end or start) >= g["start"] - SAME_FLIGHT_SLACK_S):
                    home = g
                    break
        if home is None:
            groups.append({**ident, "start": start, "end": end, "logs": {f["log_id"]}, "flights": [f]})
        else:
            home["flights"].append(f)
            home["logs"].add(f["log_id"])
            home["start"], home["end"] = min(home["start"], start), max(home["end"], end or start)

    out = []
    for g in groups:
        fl = sorted(g["flights"], key=lambda r: TYPE_ORDER[r["log_type"]])
        main = dict(fl[0])
        main["aircraft"] = next((o["aircraft"] for o in reversed(fl) if o["log_type"] == "gcs"), main["aircraft"])
        identity = next((o for o in fl if o["drone_id"] is not None), None)
        if identity is not None:
            for key in ("aircraft", "drone_id", "aircraft_uid"):
                main[key] = identity[key]
        if not main["landed"]:
            donor = next((o for o in fl[1:] if o["landed"]), None)
            if donor:
                for k in ("landing_utc", "landing_lat", "landing_lon", "landing_voltage_v", "landing",
                          "duration_s", "distance_m", "from_takeoff_m", "max_from_takeoff_m", "landed"):
                    main[k] = donor[k]
        for role in ROLES + ("goal",):
            main[role] = next((o[role] for o in sorted(fl, key=lambda r: -TYPE_ORDER[r["log_type"]]) if (o[role] or "").strip()), main[role])
        for role in ROLES:
            key = role + "_user_id"
            main[key] = next((o[key] for o in sorted(fl, key=lambda r: -TYPE_ORDER[r["log_type"]]) if o.get(key)), main.get(key))
        main["comment"] = next((o["comment"] for o in sorted(fl, key=lambda r: -TYPE_ORDER[r["log_type"]]) if (o.get("comment") or "").strip()), main.get("comment"))
        main["distance_m"] = next((o["distance_m"] for o in fl if o.get("distance_m") is not None), main.get("distance_m"))
        main["from_takeoff_m"] = next((o["from_takeoff_m"] for o in fl if o.get("from_takeoff_m") is not None), main.get("from_takeoff_m"))
        main["max_from_takeoff_m"] = next((o["max_from_takeoff_m"] for o in fl if o.get("max_from_takeoff_m") is not None), main.get("max_from_takeoff_m"))
        main["sources"] = [{"log_id": o["log_id"], "log_type": o["log_type"], "log_name": o["log_name"], "flight_id": o["flight_id"],
                            "has_log": bool(o["has_log"]), "takeoff_utc": o["takeoff_utc"], "duration_s": o["duration_s"],
                            "uploaded_by_name": o["uploaded_by_name"]} for o in fl]
        out.append(main)
    out.sort(key=lambda r: (r["takeoff_utc"] or "", r["flight_id"]), reverse=True)
    return out


@app.get("/flight/<int:flight_id>")
def flight_page(flight_id):
    return send_from_directory(PAGES_DIR, "flight.html")


@app.get("/api/logbook")
def logbook():
    """Visible telemetry flights and paper records (including daily totals), newest first."""
    hidden = request.args.get("hidden") == "1"
    if hidden:
        session_required()
        if not g.user["is_admin"]:
            abort(403)
    return jsonify(logbook_rows(hidden=hidden))


@app.get("/historical/new")
@app.get("/historical/<int:record_id>")
def historical_page(record_id=None):
    return send_from_directory(PAGES_DIR, "historical.html")


@app.get("/api/historical/options")
def historical_options():
    con = conn()
    projects = [dict(r) for r in con.execute("SELECT id,name FROM projects ORDER BY name")
                if historical.can_edit(con, g.user, r["id"])]
    # These are reference catalogs, like the existing crew menus; no flight details are exposed.
    return jsonify(projects=projects,
                   aircraft=[dict(r) for r in con.execute("SELECT id,name,unique_id FROM drones ORDER BY name")],
                   people=[dict(r) for r in con.execute(
                       "SELECT id,username,COALESCE(display_name,username) AS name FROM users ORDER BY username")],
                   places=[dict(r) for r in con.execute("SELECT id,name FROM places ORDER BY name")])


@app.get("/api/historical/template")
def historical_template():
    admin_required()
    return Response(",".join(historical.COLUMNS) + "\n", mimetype="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="historical-flights.csv"'})


@app.post("/api/historical")
def create_historical():
    # API keys may add paper records (scripted transcription of old logbooks); changing and deleting
    # them stays browser-only, like key and account management
    con = conn()
    try:
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            values = historical.validate(con, g.user, request.get_json(silent=True))
            record_id = historical.store(con, values, g.user["id"], now())
        return jsonify(id=record_id), 201
    except historical.RecordError as exc:
        return jsonify(error=str(exc)), exc.status


@app.route("/api/historical/<int:record_id>", methods=["GET", "PUT", "DELETE"])
def historical_record(record_id):
    con = conn()
    try:
        row = historical.visible(con, g.user, record_id)
        editable = historical.can_edit(con, g.user, row["project_id"])
        if request.method == "GET":
            record = dict(row)
            record["project_name"] = con.execute("SELECT name FROM projects WHERE id=?", (row["project_id"],)).fetchone()["name"]
            return jsonify(record=record, can_edit=editable)
        session_required()
        with db.write_lock:
            with con:
                con.execute("BEGIN IMMEDIATE")
                row = historical.visible(con, g.user, record_id)
                if not historical.can_edit(con, g.user, row["project_id"]):
                    raise historical.RecordError("Full project access is required to change this record.", 403)
                values = historical.validate(con, g.user, request.get_json(silent=True)) if request.method == "PUT" else None
                if request.method == "DELETE":
                    con.execute("DELETE FROM flight_records WHERE id=?", (record_id,))
                else:
                    historical.store(con, values, g.user["id"], now(), record_id)
            if request.method == "DELETE":
                attachments.prune(con, STORAGE_DIR)
        return jsonify(ok=True)
    except historical.RecordError as exc:
        return jsonify(error=str(exc)), exc.status


@app.post("/api/historical/import")
def import_historical():
    admin_required()  # also with an admin's API key, for scripted imports of old logbooks
    upload = request.files.get("file")
    if upload is None:
        return jsonify(error="Choose a CSV file."), 400
    raw = upload.stream.read(1_000_001)
    if len(raw) > 1_000_000:
        return jsonify(error="CSV must be at most 1 MB."), 400
    filename = (upload.filename or "paper-records.csv").replace("\\", "/").split("/")[-1]
    if not filename or len(filename) > 255 or any(ord(c) < 32 or ord(c) == 127 for c in filename):
        return jsonify(error="Supply a CSV filename of at most 255 characters without control characters."), 400
    con = conn()
    staged = final = None
    committed = False
    try:
        if request.form.get("preview") == "1":  # what an import would do, without writing anything
            records = historical.parse_csv(con, g.user, raw.decode("utf-8-sig"))
            return jsonify(preview=True, rows=[{k: r[k] for k in (
                "line", "action", "record_id", "hidden", "changes", "label")} for r in records])
        attachment_id = None
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            records = historical.parse_csv(con, g.user, raw.decode("utf-8-sig"))
            written = historical.save_import(con, records, g.user["id"], now())
            if written:  # the source CSV is linked to every record it created or changed
                staged, compressed, size, stored, sha = storage.save_attachment(
                    io.BytesIO(raw), STORAGE_DIR / ".incoming-attachments", 1_000_000)
                relative = f"attachments/{uuid.uuid4().hex}" + (".gz" if compressed else ".bin")
                final = STORAGE_DIR / relative
                final.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, final)
                attachment_id = con.execute(
                    "INSERT INTO attachments(filename,description,path,compressed,size_bytes,stored_bytes,sha256,"
                    " uploaded_by,uploaded_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (filename, "Original paper-record CSV import", relative, int(compressed), size, stored, sha,
                     g.user["id"], now())).lastrowid
                attachments.add_links(con, attachment_id, [dict(kind="paper", id=identity) for identity in written])
        committed = True
        by_action = {a: [r for r in records if r["action"] == a] for a in ("create", "update", "unchanged")}
        return jsonify(imported=len(by_action["create"]), updated=len(by_action["update"]),
                       unchanged=len(by_action["unchanged"]),
                       flights=sum(r["values"]["flight_count"] for r in by_action["create"]),
                       ids=[r["record_id"] for r in by_action["create"]],
                       updated_ids=[r["record_id"] for r in by_action["update"]],
                       unchanged_ids=[r["record_id"] for r in by_action["unchanged"]],
                       attachment_id=attachment_id), 201 if by_action["create"] else 200
    except historical.RecordError as exc:
        return jsonify(error=str(exc)), exc.status
    except (UnicodeError, csv.Error) as exc:
        return jsonify(error=f"Invalid UTF-8 CSV: {exc}"), 400
    except OSError:
        app.logger.exception("Could not store the original paper-record import CSV")
        return jsonify(error="Could not store the source CSV. No records were imported; retry or contact an administrator."), 503
    finally:
        if staged:
            staged.unlink(missing_ok=True)
        if final and not committed:
            final.unlink(missing_ok=True)


@app.get("/api/stats")
def stats():
    """Flight hours by aircraft, project, PIC and PAC.

    Visibility matches the logbook (see flight_own_clause): admins get every flight; everyone else only
    flights on projects they belong to, plus logs they uploaded that are not in a project yet. An SD log
    and a GCS log of the same flight are counted once. Hours are only from those visible flights.
    """
    keys = ("aircraft", "aircraft_uid", "drone_id", "project_id", "project_name",
            "duration_s", "distance_m", "from_takeoff_m", "max_from_takeoff_m",
            "takeoff_utc", "verdict", "indoor", "flight_count", "source_type",
            "pic", "pac", "pic_user", "pac_user")
    return jsonify(all=bool(g.user["is_admin"]),
                   flights=[{k: r.get(k) for k in keys} for r in logbook_rows()])


def valid_gps_location(lat, lon):
    return (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
            and math.isfinite(lat) and math.isfinite(lon)
            and -90 <= lat <= 90 and -180 <= lon <= 180 and (lat != 0 or lon != 0))


def endpoint_location_fallback(row):
    """Infer display coordinates only; leave logged event objects and altitudes unchanged."""
    known = {end: valid_gps_location(row.get(end + "_lat"), row.get(end + "_lon"))
             for end in ("takeoff", "landing")}
    for end, other in (("takeoff", "landing"), ("landing", "takeoff")):
        row[end + "_location_inferred"] = not known[end] and known[other]
        if not known[end]:
            for axis in ("lat", "lon"):
                row[end + "_" + axis] = row.get(other + "_" + axis) if known[other] else None
    return any(known.values())


def flight_has_gps_path(con, sources):
    for source in sources:
        row = con.execute(
            "SELECT f.path_idx, l.paths_file" + (", f.path_gz" if HAVE_PATH_GZ else "") +
            " FROM flights f JOIN aircraft a ON a.id=f.aircraft_id"
            " JOIN logs l ON l.id=a.log_id WHERE f.id=?", (source["flight_id"],)).fetchone()
        if row is not None:
            path = flight_path(row["paths_file"], row["path_idx"],
                               path_gz=row["path_gz"] if HAVE_PATH_GZ else None)
            if any(valid_gps_location((p.get("position") or {}).get("lat"),
                                      (p.get("position") or {}).get("lon")) for p in path):
                return True
    return False


def logbook_rows(hidden=False):
    where, params = flight_list_clause()
    con = conn()
    rows = con.execute(
        "SELECT f.id AS flight_id, f.idx, f.takeoff_utc, f.landing_utc, f.duration_s, f.distance_m,"
        " f.from_takeoff_m, f.max_from_takeoff_m, f.landed,"
        " f.takeoff_lat, f.takeoff_lon, f.landing_lat, f.landing_lon, f.project_id,"
        " f.pic_user_id, f.pac_user_id, f.gcs_user_id, f.comment,"
        " a.id AS aircraft_pk, a.ac_id, COALESCE(d.name, a.name) AS aircraft, a.name AS aircraft_logged,"
        " d.unique_id AS aircraft_uid, a.drone_id, a.pic, a.pac, a.gcs, a.goal,"
        " f.verdict, f.assessment_json, f.takeoff_voltage_v, f.landing_voltage_v, f.num_points, f.takeoff_json, f.landing_json,"
        " l.id AS log_id, l.name AS log_name, l.log_type, l.uploaded_by, l.uploaded_at, l.sha256, l.log_path IS NOT NULL AS has_log,"
        " l.project_id AS log_project_id, l.start_utc AS log_start_utc, l.date_override,"
        " (SELECT username FROM users u WHERE u.id = l.uploaded_by) AS uploaded_by_name"
        " FROM flights f JOIN aircraft a ON a.id = f.aircraft_id JOIN logs l ON l.id = a.log_id"
        " LEFT JOIN drones d ON d.id = a.drone_id"
        f" WHERE {where} AND l.status = 'done' AND f.hidden=?"
        " ORDER BY COALESCE(f.takeoff_utc, l.start_utc) DESC, f.idx", (*params, int(hidden))).fetchall()
    names = {p["id"]: p["name"] for p in con.execute("SELECT id, name FROM projects")}
    prepared = []
    for r in rows:
        d = dict(r)
        d["takeoff"] = json.loads(d.pop("takeoff_json") or "null")
        d["landing"] = json.loads(d.pop("landing_json") or "null")
        apply_row_clock(d)
        prepared.append(d)
    out = merge_sources(prepared)
    for r in out:
        r.update(record_key=f"log:{r['flight_id']}", source_type="telemetry", flight_count=1, hidden=hidden)
    place = place_resolver(con)
    source_rows = {r["flight_id"]: r for r in prepared}
    for r in out:
        for end in ("takeoff", "landing"):
            if not valid_gps_location(r[end + "_lat"], r[end + "_lon"]):
                donor = next((source_rows[s["flight_id"]] for s in r["sources"]
                              if valid_gps_location(source_rows[s["flight_id"]][end + "_lat"],
                                                    source_rows[s["flight_id"]][end + "_lon"])), None)
                if donor is not None:
                    for axis in ("lat", "lon"):
                        r[end + "_" + axis] = donor[end + "_" + axis]
        has_endpoint = endpoint_location_fallback(r)
        r["takeoff_place"], r["indoor"], r["takeoff_place_id"] = place(r["takeoff_lat"], r["takeoff_lon"])
        r["landing_place"], _, r["landing_place_id"] = place(r["landing_lat"], r["landing_lon"])
        r["assessment"] = json.loads(r.pop("assessment_json") or "null")
        r["optitrack"] = bool((r["assessment"] or {}).get("optitrack"))
        r["indoor_no_gps"] = not has_endpoint and not flight_has_gps_path(con, r["sources"])
        r["indoor"] = bool(r["indoor"] or r["optitrack"] or r["indoor_no_gps"])
        if r["optitrack"] and not r["takeoff_place"]:
            r["takeoff_place"] = "OptiTrack"
        pid = effective_flight_project(r)
        r["project_id"] = pid
        r["project_name"] = names.get(pid)
        r["can_edit_project"] = can_edit_flight_project(con, r)
        r["can_edit_crew"] = r["can_edit_project"]
        r["can_edit_comment"] = r["can_edit_project"]
        r["can_edit_date"] = r["can_edit_project"]
        r["date_ok"] = bool(r.get("date_override")) or not clock_implausible(r.get("log_start_utc"))
    out.extend(historical.rows(con, g.user, request.args.get("project"), hidden=hidden))
    out.sort(key=lambda r: (r.get("takeoff_utc") or ((r.get("date_override") or "") + "T00:00:00Z"),
                            r.get("flight_id") or r["manual_id"], r["source_type"]),
             reverse=True)
    return add_people(out, person_resolver(con))


@app.route("/api/flights/<int:flight_id>/weather", methods=["GET", "POST"])
def flight_weather(flight_id):
    entry = next((r for r in logbook_rows() if any(s["flight_id"] == flight_id for s in r["sources"])), None)
    if entry is None:
        abort(404)
    if entry.get("takeoff_location_inferred"):
        return jsonify(error="Weather needs logged takeoff coordinates; the displayed location is inferred from landing."), 400
    try:
        return jsonify(weather=weather.lookup(conn(), entry, fetch=request.method == "POST"))
    except weather.WeatherError as exc:
        return jsonify(error=str(exc)), exc.status


_GCS_RUNTIME_VERSION = re.compile(rb"logged with runtime paparazzi_version\s+(\S+)")
_GCS_PLAIN_VERSION = re.compile(rb"logged with paparazzi_version\s+(\S+)")
_GCS_BUILD_VERSION = re.compile(rb"logged with build paparazzi_version\s+(\S+)")
_PPRZ_VERSION_TAG = re.compile(r"^v(\d+)\.(\d+)(?:\.(\d+))?", re.I)


def gcs_paparazzi_version(rel_path):
    """Runtime Paparazzi version from the companion .log header comments, or None.

    GCS logs stamp `<!-- logged with runtime paparazzi_version ... -->` when recording starts.
    That is the ground station, not AUTOPILOT_VERSION from the aircraft; it is used only when
    the aircraft never sent a version in this log.
    """
    if not rel_path:
        return None
    path = STORAGE_DIR / rel_path
    if not path.is_file():
        return None
    try:
        opener = gzip.open if str(rel_path).endswith(".gz") else open
        with opener(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return None
    match = _GCS_RUNTIME_VERSION.search(head) or _GCS_PLAIN_VERSION.search(head) or _GCS_BUILD_VERSION.search(head)
    if not match:
        return None
    return match.group(1).decode("ascii", "replace").removesuffix("-->").strip()


def format_paparazzi_version_message(desc):
    """Match AUTOPILOT_VERSION's `MAJOR*10000+MINOR*100+PATCH desc` form when the tag is vMAJOR.MINOR."""
    match = _PPRZ_VERSION_TAG.match(desc or "")
    if not match:
        return desc
    code = int(match.group(1)) * 10000 + int(match.group(2)) * 100 + int(match.group(3) or 0)
    return f"{code} {desc}"


def with_gcs_autopilot_fallback(versions, log_rel):
    """Keep aircraft AUTOPILOT_VERSION when present; otherwise the GCS .log header."""
    if versions:
        return versions, False
    desc = gcs_paparazzi_version(log_rel)
    if not desc:
        return [], False
    return [format_paparazzi_version_message(desc)], True


@app.get("/api/flights/<int:flight_id>/detail")
def flight_detail(flight_id):
    """Everything for the flight page: the logbook entry (merged sources), this log's path, flight plan,
    setting changes and messages."""
    entry = next((r for r in logbook_rows() if any(s["flight_id"] == flight_id for s in r["sources"])), None)
    if entry is None:
        abort(404)
    con = conn()
    row = con.execute(
        "SELECT f.takeoff_time_s, f.landing_time_s, f.path_idx, l.paths_file, a.id AS aircraft_pk, a.name, a.ac_id,"
        " a.autopilot_versions, a.info_messages, a.preflight_checks, a.config_gz, l.id AS log_id, l.log_type,"
        " l.name AS log_name, l.log_path, l.start_utc AS log_start_utc, l.start_utc, l.timing, l.date_override"
        + (", f.path_gz" if HAVE_PATH_GZ else "") +
        " FROM flights f JOIN aircraft a ON a.id = f.aircraft_id JOIN logs l ON l.id = a.log_id WHERE f.id=?",
        (flight_id,)).fetchone()
    changes = [dict(r) for r in con.execute(
        "SELECT time_s, utc, idx, var, from_value, to_value, in_flight FROM setting_changes"
        " WHERE aircraft_id=? ORDER BY time_s", (row["aircraft_pk"],))]
    config = db.unpack(row["config_gz"]) or {}
    days = clock_shift_days(row["log_start_utc"], row["date_override"])
    path = flight_path(row["paths_file"], row["path_idx"], days, row["path_gz"] if HAVE_PATH_GZ else None)
    apply_clock_to_obj(changes, days)
    versions, from_gcs = with_gcs_autopilot_fallback(json.loads(row["autopilot_versions"] or "[]"), row["log_path"])
    return jsonify(
        entry=entry, flight_id=flight_id, log_id=row["log_id"], log_type=_type_of(dict(row)),
        timing=log_timing(row),
        takeoff_time_s=row["takeoff_time_s"], landing_time_s=row["landing_time_s"],
        path=path, flight_plan=config.get("flight_plan"),
        setting_changes=changes, info_messages=json.loads(row["info_messages"] or "[]"),
        preflight_checks=json.loads(row["preflight_checks"] or "[]"),
        autopilot_versions=versions, autopilot_versions_from_gcs=from_gcs)


# ---------------------------------------------------------------------------------------------------------------------
# Overview pages for one person, aircraft, project or location
# ---------------------------------------------------------------------------------------------------------------------
DETAIL_KINDS = ("people", "aircraft", "projects", "locations")
DETAIL_FLIGHT_KEYS = (
    "flight_id", "takeoff_utc", "landing_utc", "duration_s", "distance_m", "max_from_takeoff_m", "landed",
    "aircraft", "aircraft_uid", "drone_id", "project_id", "project_name", "takeoff_lat", "takeoff_lon",
    "takeoff_place", "takeoff_place_id", "landing_place", "landing_place_id", "indoor", "verdict", "date_ok",
    "pic", "pac", "gcs", "pic_user", "pac_user", "gcs_user", "goal", "comment",
    "takeoff_voltage_v", "landing_voltage_v", "manual_id", "source_type", "flight_count", "record_kind")


@app.get("/people/<int:item_id>")
@app.get("/aircraft/<int:item_id>")
@app.get("/projects/<int:item_id>")
@app.get("/locations/<int:item_id>")
def detail_page(item_id):
    return send_from_directory(PAGES_DIR, "detail.html")


def my_project_ids(con, user_id):
    return {r["project_id"] for r in con.execute("SELECT project_id FROM project_members WHERE user_id=?", (user_id,))}


def flight_matches(kind, item_id, f):
    if kind == "people":
        return any((f.get(role + "_user") or {}).get("id") == item_id for role in ROLES)
    if kind == "aircraft":
        return f.get("drone_id") == item_id
    if kind == "projects":
        return f.get("project_id") == item_id
    return item_id in (f.get("takeoff_place_id"), f.get("landing_place_id"))


def detail_person(con, uid, admin, mine):
    u = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if u is None:
        return None
    projects = [dict(id=r["id"], name=r["name"], role=r["role"]) for r in con.execute(
        "SELECT p.id, p.name, m.role FROM project_members m JOIN projects p ON p.id = m.project_id"
        " WHERE m.user_id=? ORDER BY p.name", (uid,)) if admin or r["id"] in mine]
    default = con.execute("SELECT id, name FROM projects WHERE id=?", (u["default_project_id"],)).fetchone()
    aliases = [r["alias"] for r in con.execute(
        "SELECT alias FROM user_aliases WHERE user_id=? ORDER BY alias", (uid,))]
    names, seen_names = [], set()
    for extra in (u["display_name"], *aliases):
        if extra and extra.casefold() not in seen_names:
            names.append(extra)
            seen_names.add(extra.casefold())
    out = dict(id=uid, name=u["display_name"] or u["username"], username=u["username"], is_admin=bool(u["is_admin"]),
               is_active=bool(u["is_active"]), provider=u["provider"] or "local",
               pending=(u["provider"] or "") == "log", projects=projects,
               default_project=dict(default) if default and (admin or default["id"] in mine) else None,
               aliases=aliases, names=names)
    if admin or uid == g.user["id"]:
        out.update(email=u["email"], created_at=u["created_at"], last_login=u["last_login"])
    if qualifications.can_see_quals(con, g.user, uid):
        out["qualifications"] = qualifications.current_payload(con, g.user, uid)
        out["names"] = names
    return out


def detail_aircraft(con, drone_id, admin, mine):
    d = con.execute("SELECT * FROM drones WHERE id=?", (drone_id,)).fetchone()
    if d is None:
        return None
    out = drone_payload(con, d)
    out.pop("log_count")
    if not admin and d["default_project_id"] not in mine:
        out["default_project_id"] = out["default_project_name"] = None
    out["logged_names"] = [r["name"] for r in con.execute(
        "SELECT DISTINCT name FROM aircraft WHERE drone_id=? AND name IS NOT NULL ORDER BY name", (drone_id,))]
    out["ac_ids"] = [r["ac_id"] for r in con.execute(
        "SELECT DISTINCT ac_id FROM aircraft WHERE drone_id=? AND ac_id IS NOT NULL ORDER BY ac_id", (drone_id,))]
    return out


def detail_project(con, pid):
    p = con.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if p is None:
        return None
    members = [dict(id=m["user_id"], name=m["display_name"] or m["username"], role=m["role"]) for m in con.execute(
        "SELECT m.user_id, m.role, u.username, u.display_name FROM project_members m JOIN users u ON u.id = m.user_id"
        " WHERE m.project_id=? ORDER BY COALESCE(u.display_name, u.username)", (pid,))]
    drones = [dict(r) for r in con.execute(
        "SELECT DISTINCT d.id, d.name, d.unique_id, d.status FROM drones d"
        " LEFT JOIN drone_projects dp ON dp.drone_id = d.id"
        " WHERE d.default_project_id=? OR dp.project_id=? ORDER BY d.name", (pid, pid))]
    return dict(id=pid, name=p["name"], created_at=p["created_at"], members=members, drones=drones,
                role=role_on(con, pid))


def detail_location(con, place_id):
    r = con.execute("SELECT * FROM places WHERE id=?", (place_id,)).fetchone()
    if r is None:
        return None
    return dict(id=place_id, name=r["name"], indoor=bool(r["indoor"]), shape=json.loads(r["shape"]))


def detail_logs(con, kind, item_id, log_ids):
    """Visible log files behind the flights, plus the files directly tied to this item."""
    where, params = own_clause("l.")
    extra = {"people": "l.uploaded_by = ?", "projects": "l.project_id = ?",
             "aircraft": "l.id IN (SELECT log_id FROM aircraft WHERE drone_id = ?)"}.get(kind)
    ids = sorted(log_ids)
    cond = f"l.id IN ({','.join('?' * len(ids))})" if ids else "0"
    args = list(ids)
    if extra:
        cond = f"({cond} OR {extra})"
        args.append(item_id)
    rows = con.execute(
        "SELECT l.*, p.name AS project_name,"
        " (SELECT COALESCE(display_name, username) FROM users u WHERE u.id = l.uploaded_by) AS uploaded_by_name,"
        " (SELECT group_concat(DISTINCT COALESCE(d.name, a.name, '?')) FROM aircraft a"
        " LEFT JOIN drones d ON d.id = a.drone_id WHERE a.log_id = l.id) AS aircraft_names,"
        " (SELECT COUNT(*) FROM flights f JOIN aircraft a ON a.id = f.aircraft_id WHERE a.log_id = l.id) AS num_flights"
        f" FROM logs l LEFT JOIN projects p ON p.id = l.project_id WHERE ({where}) AND {cond}"
        " ORDER BY COALESCE(l.start_utc, l.uploaded_at) DESC", (*params, *args)).fetchall()
    keep = ("id", "name", "log_type", "status", "size_bytes", "uploaded_at", "uploaded_by_name", "project_id",
            "project_name", "start_utc", "duration_s", "date_ok", "has_log", "aircraft_names", "num_flights")
    return [{k: d.get(k) for k in keep} for d in map(log_dict, rows)]


@app.get("/api/detail/<kind>/<int:item_id>")
def detail(kind, item_id):
    """Overview of one person, aircraft, project or location: its details, the flights and log files the
    current user may see (same rules as the logbook), from which the page builds the totals.

    Non-admins get 404 for items they have no access to: projects they are not in, and people, aircraft and
    locations that do not appear on any flight they can see (people sharing a project and aircraft whose default
    project they are in are also allowed)."""
    if kind not in DETAIL_KINDS:
        abort(404)
    con = conn()
    admin = bool(g.user["is_admin"])
    mine = my_project_ids(con, g.user["id"])
    if kind == "projects" and not admin and item_id not in mine:
        abort(404)
    item = {"people": lambda: detail_person(con, item_id, admin, mine),
            "aircraft": lambda: detail_aircraft(con, item_id, admin, mine),
            "projects": lambda: detail_project(con, item_id),
            "locations": lambda: detail_location(con, item_id)}[kind]()
    if item is None:
        abort(404)
    flights = [f for f in logbook_rows() if flight_matches(kind, item_id, f)]
    if not admin and not flights:
        allowed = (kind == "people" and (item_id == g.user["id"] or my_project_ids(con, item_id) & mine))
        if kind == "aircraft":
            drone_pids = {p["project_id"] for p in item.get("projects") or []}
            if item.get("default_project_id"):
                drone_pids.add(item["default_project_id"])
            allowed = bool(drone_pids & mine)
        if not allowed:
            abort(404)
    log_ids = {s["log_id"] for f in flights for s in f["sources"]}
    logs = detail_logs(con, kind, item_id, log_ids)
    out = []
    for f in flights:
        d = {k: f.get(k) for k in DETAIL_FLIGHT_KEYS}
        d["log_ids"] = sorted({s["log_id"] for s in f["sources"]})
        out.append(d)
    return jsonify(kind=kind, item=item, flights=out, logs=logs, all=admin)


@app.get("/api/status")
def status():
    con = conn()
    where, params = list_clause()
    counts = {r["status"]: r["n"] for r in
              con.execute(f"SELECT status, COUNT(*) n FROM logs WHERE {where} GROUP BY status", params)}
    od_sql, od_params = outdated_sql()
    outdated = con.execute(f"SELECT COUNT(*) FROM logs WHERE {where} AND status='done' AND {od_sql}",
                           (*params, *od_params)).fetchone()[0]
    date_unknown = con.execute(
        f"SELECT COUNT(*) FROM logs WHERE {where} AND status='done' AND date_override IS NULL"
        " AND (start_utc IS NULL OR start_utc < ?)", (*params, f"{CLOCK_MIN_YEAR:04d}-01-01")).fetchone()[0]
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if g.user["is_admin"]:
        ingest_waiting = con.execute(
            "SELECT COUNT(*) FROM ingest WHERE status IN ('accepted','hashing')").fetchone()[0]
        ingest_counts = {r["status"]: r["n"] for r in
                         con.execute("SELECT status, COUNT(*) n FROM ingest WHERE uploaded_at>=? GROUP BY status",
                                     (cutoff,))}
    else:
        ingest_waiting = con.execute(
            "SELECT COUNT(*) FROM ingest WHERE status IN ('accepted','hashing') AND uploaded_by=?",
            (g.user["id"],)).fetchone()[0]
        ingest_counts = {r["status"]: r["n"] for r in
                         con.execute("SELECT status, COUNT(*) n FROM ingest WHERE uploaded_by=? AND uploaded_at>=?"
                                     " GROUP BY status", (g.user["id"], cutoff))}
    backlog = pending_upload_work(con)
    return jsonify(parser_version=current_version(), path_rate_hz=PATH_RATE, queue=jobs.qsize(), counts=counts,
                   outdated=outdated, date_unknown=date_unknown, ingest=ingest_counts, ingest_waiting=ingest_waiting,
                   ingest_queue=ingest_jobs.qsize(), backlog=dict(backlog))


@app.get("/api/ingest/<int:ingest_id>")
def ingest_result(ingest_id):
    """Upload receipts remain accessible to their uploader after automatic project assignment."""
    row = conn().execute(
        "SELECT i.id,i.status,i.error,i.log_id,l.status AS log_status,l.error AS log_error"
        " FROM ingest i LEFT JOIN logs l ON l.id=i.log_id"
        " WHERE i.id=? AND (? OR i.uploaded_by=?)",
        (ingest_id, g.user["is_admin"], g.user["id"])).fetchone()
    if row is None:
        abort(404)
    return jsonify(dict(row))


@app.get("/api/logs")
def list_logs():
    where, params = list_clause("l.")
    con = conn()
    try:
        limit = min(max(int(request.args.get("limit", 100)), 1), 200)
    except (TypeError, ValueError):
        limit = 100
    try:
        offset = max(int(request.args.get("offset", 0)), 0)
    except (TypeError, ValueError):
        offset = 0
    total = con.execute("SELECT COUNT(*) FROM logs l WHERE " + where, params).fetchone()[0]
    rows = con.execute(
        "SELECT l.*, p.name AS project_name,"
        " (SELECT username FROM users u WHERE u.id = l.uploaded_by) AS uploaded_by_name,"
        " (SELECT group_concat(DISTINCT COALESCE(d.name, a.name, '?')) FROM aircraft a"
        " LEFT JOIN drones d ON d.id = a.drone_id WHERE a.log_id = l.id) AS aircraft_names,"
        " (SELECT SUM(a.num_flights) FROM aircraft a WHERE a.log_id = l.id) AS num_flights,"
        " (SELECT SUM(a.total_flight_time_s) FROM aircraft a WHERE a.log_id = l.id) AS flight_time_s"
        " FROM logs l LEFT JOIN projects p ON p.id = l.project_id WHERE " + where +
        " ORDER BY l.id DESC LIMIT ? OFFSET ?",
        (*params, limit, offset)).fetchall()
    return jsonify(logs=[{**log_dict(r), "can_delete": can_delete_log(con, r),
                          "can_edit_date": can_edit_log_date(con, r)} for r in rows],
                   total=total, limit=limit, offset=offset)


@app.get("/api/logs/<int:log_id>")
def get_log(log_id):
    con = conn()
    row = visible_log(con, log_id)
    out = log_dict(row)
    out["can_delete"] = can_delete_log(con, row)
    out["can_edit_date"] = can_set_log_date(con, row)
    days = clock_shift_days(row["start_utc"], row["date_override"])
    out["project_name"] = con.execute("SELECT name FROM projects WHERE id=?", (row["project_id"],)).fetchone()
    out["project_name"] = out["project_name"]["name"] if out["project_name"] else None
    resolve = person_resolver(con)
    out["aircraft"] = []
    for a in con.execute("SELECT * FROM aircraft WHERE log_id=? ORDER BY ac_id", (log_id,)):
        ad = {k: a[k] for k in a.keys() if k != "config_gz"}
        ad["uploaded_by"] = row["uploaded_by"]
        if a["drone_id"]:
            dn = con.execute("SELECT name, unique_id FROM drones WHERE id=?", (a["drone_id"],)).fetchone()
            ad["drone_name"] = dn["name"] if dn else None
            ad["drone_uid"] = dn["unique_id"] if dn else None
        else:
            ad["drone_name"] = None
            ad["drone_uid"] = None
        add_people([ad], resolve)
        for k in ("preflight_checks", "info_messages"):
            ad[k] = json.loads(ad[k]) if ad[k] else []
        ad["autopilot_versions"], ad["autopilot_versions_from_gcs"] = with_gcs_autopilot_fallback(
            json.loads(ad["autopilot_versions"]) if ad["autopilot_versions"] else [], row["log_path"])
        if days:
            ad["first_utc"] = shift_iso(ad.get("first_utc"), days)
            ad["last_utc"] = shift_iso(ad.get("last_utc"), days)
        flights = []
        for f in con.execute("SELECT * FROM flights WHERE aircraft_id=? ORDER BY idx", (a["id"],)):
            fd = {k: f[k] for k in f.keys() if k not in ("path_idx", "takeoff_json", "landing_json")}
            fd["takeoff"] = json.loads(f["takeoff_json"] or "null")
            fd["landing"] = json.loads(f["landing_json"] or "null")
            endpoint_location_fallback(fd)
            if days:
                fd["takeoff_utc"] = shift_iso(fd.get("takeoff_utc"), days)
                fd["landing_utc"] = shift_iso(fd.get("landing_utc"), days)
                apply_clock_to_obj(fd["takeoff"], days)
                apply_clock_to_obj(fd["landing"], days)
            pid = f["project_id"]
            pn = con.execute("SELECT name FROM projects WHERE id=?", (pid,)).fetchone() if pid else None
            fd["project_name"] = pn["name"] if pn else None
            flights.append(fd)
        ad["flights"] = flights
        ad["setting_changes"] = [dict(c) for c in con.execute(
            "SELECT time_s, utc, previous_report_s, idx, var, from_value, to_value, in_flight"
            " FROM setting_changes WHERE aircraft_id=? ORDER BY time_s", (a["id"],))]
        if days:
            apply_clock_to_obj(ad["setting_changes"], days)
        out["aircraft"].append(ad)
    return jsonify(out)


@app.get("/api/aircraft/<int:ac_pk>/config")
def get_config(ac_pk):
    where, params = own_clause("l.")
    row = conn().execute("SELECT a.config_gz FROM aircraft a JOIN logs l ON l.id = a.log_id"
                         f" WHERE a.id=? AND {where}", (ac_pk, *params)).fetchone()
    if row is None:
        abort(404)
    return jsonify(db.unpack(row["config_gz"]))


@app.get("/api/flights/<int:flight_id>/path")
def get_path(flight_id):
    where, params = own_clause("l.")
    row = conn().execute(
        "SELECT f.path_idx, l.paths_file, l.start_utc, l.date_override"
        + (", f.path_gz" if HAVE_PATH_GZ else "") +
        " FROM flights f JOIN aircraft a ON a.id = f.aircraft_id"
        f" JOIN logs l ON l.id = a.log_id WHERE f.id=? AND {where}", (flight_id, *params)).fetchone()
    if row is None:
        abort(404)
    return jsonify(flight_path(row["paths_file"], row["path_idx"], clock_shift_days(row["start_utc"], row["date_override"]),
                               row["path_gz"] if HAVE_PATH_GZ else None))


@app.get("/api/logs/<int:log_id>/download/<kind>")
def download(log_id, kind):
    row = visible_log(conn(), log_id, "data_path, log_path")
    rel = row["data_path"] if kind == "data" else row["log_path"] if kind == "log" else None
    if not rel:
        abort(404)
    if not rel.endswith(".gz"):
        return jsonify(error="Uncompressed legacy storage is unsupported; delete and re-upload this test log."), 409
    headers = {"Content-Disposition": f'attachment; filename="{safe_filename(Path(rel).name[:-3])}"',
               "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
    return Response(storage.chunks(STORAGE_DIR / rel, compressed=True),
                    mimetype="application/octet-stream", headers=headers)


class _Sink:
    """Unseekable write target that collects what zipfile writes so it can be streamed out."""

    def __init__(self):
        self.chunks = []

    def write(self, b):
        self.chunks.append(bytes(b))
        return len(b)

    def flush(self):
        pass

    def take(self):
        out, self.chunks = b"".join(self.chunks), []
        return out


def zip_stream(files):
    """Stream original log files into a compressed ZIP without building it on disk."""
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as z:
        for arc, path in files:
            with z.open(arc, "w", force_zip64=True) as dst:
                for chunk in storage.chunks(path, compressed=True):
                    dst.write(chunk)
                    yield sink.take()
    yield sink.take()


@app.get("/api/logs/<int:log_id>/download")
def download_both(log_id):
    """Zip with the .data and .log file of a log."""
    row = visible_log(conn(), log_id, "name, data_path, log_path")
    if any(not rel.endswith(".gz") for rel in (row["data_path"], row["log_path"]) if rel):
        return jsonify(error="Uncompressed legacy storage is unsupported; delete and re-upload this test log."), 409
    files = [(posixpath.basename(rel)[:-3], STORAGE_DIR / rel) for rel in (row["data_path"], row["log_path"]) if rel]
    if len(files) == 1:
        return download(log_id, "data")
    stem = posixpath.splitext(posixpath.basename(row["name"]))[0]
    headers = {"Content-Disposition": f'attachment; filename="{safe_filename(stem)}.zip"'}
    return Response(stream_with_context(zip_stream(files)), mimetype="application/zip", headers=headers)


# ---------------------------------------------------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------------------------------------------------
@app.post("/api/logs/<int:log_id>/reparse")
def reparse(log_id):
    con = conn()
    visible_log(con, log_id, "id")

    def queue_one():
        n = con.execute("UPDATE logs SET status='queued' WHERE id=? AND status IN ('done','error')", (log_id,)).rowcount
        con.commit()
        return n

    if db.retry_locked(queue_one, con):
        jobs.put(log_id)
        return jsonify(queued=[log_id])
    return jsonify(queued=[])


@app.post("/api/reparse-outdated")
def reparse_outdated():
    """Queue every log parsed by another parser version than the installed one, and failed ones."""
    admin_required()
    version = parser_version()
    app.config["PARSER_VERSION"] = version
    con = conn()
    where, params = list_clause()
    od_sql, od_params = outdated_sql()
    ids = [r["id"] for r in con.execute(
        f"SELECT id FROM logs WHERE {where} AND (status='error' OR (status='done' AND {od_sql}))",
        (*params, *od_params))]

    def queue_all():
        for i in ids:
            con.execute("UPDATE logs SET status='queued' WHERE id=?", (i,))
        con.commit()

    db.retry_locked(queue_all, con)
    for i in ids:
        jobs.put(i)
    return jsonify(queued=ids, parser_version=version)


@app.delete("/api/logs/<int:log_id>")
def delete_log(log_id):
    con = conn()
    row = visible_log(con, log_id, "sha256, status, project_id, uploaded_by")
    if not can_delete_log(con, row):
        return jsonify(error="you cannot delete logs in this project"), 403
    if row["status"] == "parsing":
        return jsonify(error="log is being parsed"), 409
    with db.write_lock:
        with con:
            con.execute("BEGIN IMMEDIATE")
            current = visible_log(con, log_id)
            if not can_delete_log(con, current):
                return jsonify(error="you cannot delete logs in this project"), 403
            if current["status"] == "parsing":
                return jsonify(error="log is being parsed"), 409
            con.execute("DELETE FROM logs WHERE id=?", (log_id,))
        attachments.prune(con, STORAGE_DIR)
    shutil.rmtree(STORAGE_DIR / row["sha256"][:2] / row["sha256"], ignore_errors=True)
    with contextlib.suppress(OSError):
        (STORAGE_DIR / row["sha256"][:2]).rmdir()  # only when empty
    return jsonify(deleted=log_id)


attachments.register(app, lambda: conn(), STORAGE_DIR, session_required, now, env_int("ATTACHMENT_MAX_BYTES"))
qualifications.register(app, lambda: conn(), STORAGE_DIR, session_required)


if __name__ == "__main__" and len(sys.argv) > 1:
    cli(sys.argv[1:])
    sys.exit(0)

start_workers()
if not conn().execute("SELECT 1 FROM users LIMIT 1").fetchone():
    print("No accounts yet. Create one with:  python3 app.py create-user <username> --admin", file=sys.stderr)

if __name__ == "__main__":
    host, port = config.bind_address()
    app.run(host=host, port=port, threaded=True)
