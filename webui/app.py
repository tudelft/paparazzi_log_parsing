"""Web interface to upload Paparazzi logs (.data/.log files, zip archives, folders).

Uploaded logs are kept on disk, hashed (sha256 of the .data file) and parsed with the fast C++ parser
into a SQLite database. A log with a known hash is never stored or parsed twice. The version of the
parser that produced the stored result is recorded so outdated results can be re-parsed later.
"""
import csv
import hashlib
import difflib
import io
import json
import math
import os
import posixpath
import queue
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
from werkzeug.security import check_password_hash, generate_password_hash

import db
import saml

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("PPRZ_WEB_DATA", HERE / "data")).resolve()
STORAGE_DIR = DATA_DIR / "logs"
TMP_DIR = DATA_DIR / "tmp"
DB_PATH = str(DATA_DIR / "logs.db")
FASTREADER = os.environ.get("FASTREADER", str(HERE.parent / "cpp" / "build" / "paparazzi_log_parsing"))
PATH_RATE = float(os.environ.get("PPRZ_PATH_RATE", "5"))
WORKERS = int(os.environ.get("PPRZ_WORKERS", "2"))

PAGES_DIR = HERE / "static"
# Pages are served by explicit routes so nothing is reachable without logging in (except the login page)
app = Flask(__name__, static_folder=None)


def load_secret_key():
    """Session signing key, generated once and kept in the data directory."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    key_file = DATA_DIR / "secret_key"
    if not key_file.exists():
        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    return key_file.read_text().strip()


app.secret_key = os.environ.get("PPRZ_SECRET_KEY") or load_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("PPRZ_COOKIE_SECURE", "0") == "1",  # set to 1 when served over https
    PERMANENT_SESSION_LIFETIME=int(os.environ.get("PPRZ_SESSION_HOURS", "12")) * 3600,
    MAX_CONTENT_LENGTH=None,
)
jobs: "queue.Queue[int]" = queue.Queue()


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


# ---------------------------------------------------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------------------------------------------------
def parse_log(log_id):
    con = conn()
    row = con.execute("SELECT * FROM logs WHERE id=?", (log_id,)).fetchone()
    if row is None:
        return
    con.execute("UPDATE logs SET status='parsing', error=NULL WHERE id=?", (log_id,))
    con.commit()
    out_path = None
    try:
        data_path = STORAGE_DIR / row["data_path"]
        cmd = [FASTREADER, "-r", str(PATH_RATE), "--sha256", row["sha256"], str(data_path)]
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        fd, out_path = tempfile.mkstemp(dir=TMP_DIR, suffix=".json")
        t0 = time.time()
        with os.fdopen(fd, "wb") as out:
            proc = subprocess.run(cmd, stdout=out, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode(errors="replace").strip() or f"parser exit code {proc.returncode}")
        seconds = time.time() - t0
        with open(out_path, "rb") as f:
            doc = json.load(f)
        con.execute("BEGIN")
        kept = snapshot_flight_edits(con, log_id)
        db.store_parsed(con, log_id, doc)
        link_log_aircraft(con, log_id)
        assign_log_flights(con, log_id, kept)
        con.execute("UPDATE logs SET status='done', error=NULL, parsed_at=?, parse_seconds=? WHERE id=?",
                    (now(), round(seconds, 3), log_id))
        con.commit()
    except Exception as e:  # noqa: BLE001 - any failure is reported on the log row
        con.rollback()
        con.execute("UPDATE logs SET status='error', error=? WHERE id=?", (str(e)[:2000], log_id))
        con.commit()
    finally:
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
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    db.init(DB_PATH)
    con = conn()
    assign_open_flights(con)
    con.execute("UPDATE logs SET status='queued' WHERE status IN ('queued', 'parsing')")
    con.commit()
    for r in con.execute("SELECT id FROM logs WHERE status='queued' ORDER BY id"):
        jobs.put(r["id"])
    for _ in range(max(1, WORKERS)):
        threading.Thread(target=worker, daemon=True).start()


# ---------------------------------------------------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------------------------------------------------
def collect_files(stage, uploads):
    """Return ({(directory, stem): {'.data': path, '.log': path}}, ignored) for plain files and zip members."""
    found = {}

    def add(rel, path):
        d, base = posixpath.split(rel)
        stem, ext = posixpath.splitext(base)
        if ext.lower() in (".data", ".log"):
            found.setdefault((d, stem), {})[ext.lower()] = path
        return ext.lower() in (".data", ".log")

    ignored = []
    for rel, path in uploads:
        if rel.lower().endswith(".zip"):
            zrel = rel[:-4]
            try:
                with zipfile.ZipFile(path) as z:
                    for info in z.infolist():
                        member = clean_relpath(info.filename)
                        if info.is_dir() or not member.lower().endswith((".data", ".log")):
                            continue
                        dest = stage / uuid.uuid4().hex
                        with z.open(info) as src, open(dest, "wb") as dst:
                            shutil.copyfileobj(src, dst, 8 << 20)
                        add(posixpath.join(zrel, member), str(dest))
            except zipfile.BadZipFile:
                ignored.append({"name": rel, "status": "ignored", "reason": "not a valid zip file"})
        elif not add(rel, path):
            ignored.append({"name": rel, "status": "ignored", "reason": "not a .data, .log or .zip file"})
    return found, ignored


def safe_filename(name):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name) or "log"


def store_files(sha, name, data_src, log_src):
    """Move a staged log into permanent storage, returns the relative paths."""
    folder = Path(sha[:2]) / sha
    (STORAGE_DIR / folder).mkdir(parents=True, exist_ok=True)
    base = safe_filename(posixpath.basename(name))
    data_rel = str(folder / base)
    log_rel = None
    shutil.move(data_src, STORAGE_DIR / data_rel)
    if log_src:
        log_rel = str(folder / (posixpath.splitext(base)[0] + ".log"))
        shutil.move(log_src, STORAGE_DIR / log_rel)
    return data_rel, log_rel


@app.post("/api/upload")
def upload():
    con = conn()
    if not can_upload_any(con):
        return jsonify(error="you cannot add logs"), 403
    # Project is not chosen at upload time. After parsing it is filled from the
    # aircraft default, then the uploader / GCS / PIC, and can be edited on this page.
    project_id = None
    files = request.files.getlist("files")
    names = request.form.getlist("names")
    if not files:
        return jsonify(error="no files"), 400
    stage = TMP_DIR / uuid.uuid4().hex
    stage.mkdir(parents=True, exist_ok=True)
    try:
        uploads = []
        for i, f in enumerate(files):
            rel = clean_relpath(names[i] if i < len(names) else f.filename or f"file{i}")
            if not rel:
                continue
            dest = stage / uuid.uuid4().hex
            f.save(dest)
            uploads.append((rel, str(dest)))
        found, results = collect_files(stage, uploads)
        for (d, stem), parts in sorted(found.items()):
            name = posixpath.join(d, stem + ".data")
            if ".data" not in parts:
                results.append({"name": posixpath.join(d, stem + ".log"), "status": "rejected",
                                "reason": "no matching .data file"})
                continue
            if ".log" not in parts:
                results.append({"name": name, "status": "rejected", "reason": "no matching .log file"})
                continue
            size = os.path.getsize(parts[".data"])
            if size == 0:
                results.append({"name": name, "status": "ignored", "reason": "empty .data file"})
                continue
            sha = sha256_file(parts[".data"])
            row = con.execute("SELECT id, log_path, uploaded_by FROM logs WHERE sha256=?", (sha,)).fetchone()
            if row is not None and not g.user["is_admin"] and row["uploaded_by"] != g.user["id"]:
                results.append({"name": name, "status": "duplicate", "reason": "already uploaded by another user"})
                continue
            if row is not None:
                if row["log_path"] is None and ".log" in parts:
                    log_rel = str(Path(sha[:2]) / sha / (safe_filename(stem) + ".log"))
                    shutil.move(parts[".log"], STORAGE_DIR / log_rel)
                    con.execute("UPDATE logs SET log_path=?, status='queued' WHERE id=?", (log_rel, row["id"]))
                    con.commit()
                    jobs.put(row["id"])
                    results.append({"name": name, "status": "duplicate", "id": row["id"],
                                    "reason": "already uploaded, missing .log added and re-queued"})
                else:
                    results.append({"name": name, "status": "duplicate", "id": row["id"],
                                    "reason": "already uploaded"})
                continue
            data_rel, log_rel = store_files(sha, name, parts[".data"], parts.get(".log"))
            try:
                cur = con.execute(
                    "INSERT INTO logs (name, sha256, size_bytes, data_path, log_path, status, uploaded_at, uploaded_by, project_id)"
                    " VALUES (?,?,?,?,?,'queued',?,?,?)",
                    (name, sha, size, data_rel, log_rel, now(), g.user["id"], project_id))
                con.commit()
            except sqlite3.IntegrityError:
                results.append({"name": name, "status": "duplicate", "reason": "already uploaded"})
                continue
            jobs.put(cur.lastrowid)
            results.append({"name": name, "status": "queued", "id": cur.lastrowid, "has_log": log_rel is not None})
    finally:
        shutil.rmtree(stage, ignore_errors=True)
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
            "is_admin": bool(u["is_admin"])}


@app.after_request
def no_cache_pages(resp):
    if resp.mimetype == "text/html" or request.path in ("/theme.css", "/theme.js"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/theme.css")
def theme_css():
    return send_from_directory(PAGES_DIR, "theme.css")


@app.get("/theme.js")
def theme_js():
    return send_from_directory(PAGES_DIR, "theme.js")


@app.before_request
def require_login():
    if request.endpoint in PUBLIC_ENDPOINTS:
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
            con.execute("UPDATE users SET subject=?, provider=COALESCE(NULLIF(provider,''),'saml') WHERE id=?",
                        (subject, row["id"]))
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


@app.get("/api/me")
def me():
    return jsonify(user=user_public(g.user))


@app.get("/api/people")
def list_people():
    """Active accounts for PIC/PAC/GCS menus in the logbook."""
    rows = conn().execute(
        "SELECT id, username, display_name FROM users WHERE is_active=1 ORDER BY COALESCE(display_name, username), username")
    return jsonify([{"id": r["id"], "username": r["username"],
                     "display_name": r["display_name"] or r["username"]} for r in rows])


# ---- command line user management:  python3 app.py create-user|set-password|list-users|deactivate-user ----
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
    al = sub.add_parser("add-alias", help="link a PIC/PAC/GCS name from the logs to a user")
    al.add_argument("username")
    al.add_argument("alias")
    ra = sub.add_parser("remove-alias")
    ra.add_argument("alias")
    d = sub.add_parser("deactivate-user", help="block an account (its uploads are kept)")
    d.add_argument("username")
    ac = sub.add_parser("activate-user")
    ac.add_argument("username")
    a = p.parse_args(argv)

    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
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
    case-insensitive on explicit aliases, then username, then display name. Without any name in the log only the
    PIC falls back to the uploader (source 'uploader'); PAC and GCS stay empty."""
    users = {u["id"]: dict(id=u["id"], username=u["username"], display_name=u["display_name"] or u["username"])
             for u in con.execute("SELECT id, username, display_name FROM users")}
    names = {}
    for u in users.values():
        names[u["username"].casefold()] = u["id"]
    for u in users.values():
        if u["display_name"]:
            names.setdefault(u["display_name"].casefold(), u["id"])
    for r in con.execute("SELECT user_id, alias FROM user_aliases"):
        names[r["alias"].casefold()] = r["user_id"]  # explicit aliases win

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
    return drones.get(did) if did else None


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
    for a in con.execute("SELECT id, name FROM aircraft WHERE log_id=?", (log_id,)):
        d = resolve_drone(names, drones, a["name"])
        con.execute("UPDATE aircraft SET drone_id=? WHERE id=?", (d["id"] if d else None, a["id"]))


def relink_all_aircraft(con):
    drones, names = drone_name_map(con)
    for a in con.execute("SELECT id, name FROM aircraft"):
        d = resolve_drone(names, drones, a["name"])
        con.execute("UPDATE aircraft SET drone_id=? WHERE id=?", (d["id"] if d else None, a["id"]))


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


def snapshot_flight_edits(con, log_id):
    """Keep edited flight project and crew across a re-parse (flights are deleted with the aircraft rows)."""
    return {(r["ac_id"], r["idx"]): dict(r) for r in con.execute(
        "SELECT a.ac_id, f.idx, f.project_id, f.pic_user_id, f.pac_user_id, f.gcs_user_id, f.comment"
        " FROM flights f JOIN aircraft a ON a.id = f.aircraft_id WHERE a.log_id=?", (log_id,))}


def pick_flight_project(con, row, resolve):
    """Aircraft default, then uploader, then GCS operator, then PIC."""
    if row["drone_id"]:
        d = con.execute("SELECT default_project_id FROM drones WHERE id=?", (row["drone_id"],)).fetchone()
        pid = live_project(con, d["default_project_id"] if d else None)
        if pid:
            return pid
    gcs = resolve("gcs", row["gcs"], None)
    pic = resolve("gcs", row["pic"], None)  # name only; do not fall back to the uploader
    for uid in (row["uploaded_by"], gcs["id"] if gcs else None, pic["id"] if pic else None):
        pid = user_main_project(con, uid)
        if pid:
            return pid
    return None


def assign_log_flights(con, log_id, kept=None):
    """Fill empty flight projects, restoring kept values from a re-parse first."""
    kept = kept or {}
    resolve = person_resolver(con)
    log = con.execute("SELECT uploaded_by, project_id FROM logs WHERE id=?", (log_id,)).fetchone()
    if log is None:
        return
    for f in con.execute(
            "SELECT f.id, f.idx, f.project_id, a.ac_id, a.drone_id, a.pic, a.gcs, l.uploaded_by"
            " FROM flights f JOIN aircraft a ON a.id = f.aircraft_id JOIN logs l ON l.id = a.log_id"
            " WHERE a.log_id=?", (log_id,)):
        restore = kept.get((f["ac_id"], f["idx"])) or {}
        if restore:
            con.execute("UPDATE flights SET pic_user_id=?, pac_user_id=?, gcs_user_id=?, comment=? WHERE id=?",
                        (restore.get("pic_user_id"), restore.get("pac_user_id"), restore.get("gcs_user_id"),
                         restore.get("comment"), f["id"]))
        saved = restore.get("project_id")
        if saved and live_project(con, saved):
            con.execute("UPDATE flights SET project_id=? WHERE id=?", (saved, f["id"]))
            continue
        if f["project_id"]:
            continue
        pid = pick_flight_project(con, f, resolve)
        if pid:
            con.execute("UPDATE flights SET project_id=? WHERE id=?", (pid, f["id"]))
    if log["project_id"] is None:
        ids = [r["project_id"] for r in con.execute(
            "SELECT DISTINCT f.project_id FROM flights f JOIN aircraft a ON a.id = f.aircraft_id"
            " WHERE a.log_id=? AND f.project_id IS NOT NULL", (log_id,))]
        if len(ids) == 1:
            con.execute("UPDATE logs SET project_id=? WHERE id=? AND project_id IS NULL", (ids[0], log_id))


def assign_open_flights(con):
    for r in con.execute(
            "SELECT DISTINCT a.log_id FROM flights f JOIN aircraft a ON a.id = f.aircraft_id"
            " WHERE f.project_id IS NULL"):
        assign_log_flights(con, r["log_id"])


def fill_drone_defaults(con, drone_id):
    """Give unassigned logs and flights linked to this drone its default project."""
    assign_open_flights(con)
    d = con.execute("SELECT default_project_id FROM drones WHERE id=?", (drone_id,)).fetchone()
    if not d or not live_project(con, d["default_project_id"]):
        return
    con.execute(
        "UPDATE logs SET project_id=? WHERE project_id IS NULL AND id IN"
        " (SELECT log_id FROM aircraft WHERE drone_id=?)",
        (d["default_project_id"], drone_id),
    )


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
    """Return place(lat, lon) -> (name, indoor) of the smallest named place containing the point, else (None, False)."""
    places = sorted(((_shape_size(sh), r["name"], bool(r["indoor"]), sh)
                     for r in con.execute("SELECT name, indoor, shape FROM places")
                     for sh in [json.loads(r["shape"])]), key=lambda t: t[0])

    def place(lat, lon):
        if lat is None or lon is None:
            return None, False
        return next(((name, indoor) for _, name, indoor, sh in places if _in_shape(sh, lat, lon)), (None, False))

    return place


@app.get("/api/admin/places")
def admin_places():
    admin_required()
    con = conn()
    places = [dict(id=r["id"], name=r["name"], indoor=bool(r["indoor"]), shape=json.loads(r["shape"]))
              for r in con.execute("SELECT * FROM places ORDER BY name")]
    # takeoff/landing positions, rounded to ~100 m, as a hint where to draw
    pts = {}
    for r in con.execute("SELECT takeoff_lat a, takeoff_lon b FROM flights UNION ALL SELECT landing_lat, landing_lon FROM flights"):
        if r["a"] is not None:
            k = (round(r["a"], 3), round(r["b"], 3))
            pts[k] = pts.get(k, 0) + 1
    return jsonify(places=places, points=[{"lat": k[0], "lon": k[1], "count": n} for k, n in pts.items()])


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
    con.execute("DELETE FROM places WHERE id=?", (place_id,))
    con.commit()
    return jsonify(ok=True)


def admin_required():
    if not g.user["is_admin"]:
        abort(403)


@app.get("/admin")
def admin_page():
    admin_required()
    return send_from_directory(PAGES_DIR, "admin.html")


@app.get("/projects")
def projects_page():
    admin_required()
    return send_from_directory(PAGES_DIR, "projects.html")


@app.get("/aircraft")
def aircraft_page():
    admin_required()
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
        out.append({"id": r["id"], "name": r["name"], "role": r["role"], "log_count": n,
                    "can_add": r["role"] in ("add", "full"), "can_delete": r["role"] == "full" or bool(g.user["is_admin"])})
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


@app.delete("/api/admin/projects/<int:project_id>")
def admin_delete_project(project_id):
    admin_required()
    con = conn()
    if not con.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
        return jsonify(error="no such project"), 404
    con.execute("UPDATE logs SET project_id=NULL WHERE project_id=?", (project_id,))
    con.execute("UPDATE flights SET project_id=NULL WHERE project_id=?", (project_id,))
    con.execute("UPDATE drones SET default_project_id=NULL WHERE default_project_id=?", (project_id,))
    con.execute("UPDATE users SET default_project_id=NULL WHERE default_project_id=?", (project_id,))
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


def drone_payload(con, d):
    aliases = [r["alias"] for r in con.execute(
        "SELECT alias FROM drone_aliases WHERE drone_id=? ORDER BY alias", (d["id"],))]
    n = con.execute("SELECT COUNT(DISTINCT log_id) AS n FROM aircraft WHERE drone_id=?", (d["id"],)).fetchone()["n"]
    proj = con.execute("SELECT name FROM projects WHERE id=?", (d["default_project_id"],)).fetchone() if d["default_project_id"] else None
    return {"id": d["id"], "name": d["name"], "unique_id": d["unique_id"],
            "default_project_id": d["default_project_id"],
            "default_project_name": proj["name"] if proj else None,
            "manager": d["manager"], "current_user": d["current_user"], "status": d["status"],
            "bought_on": d["bought_on"], "inventory_project": d["inventory_project"],
            "comments": d["comments"], "aliases": aliases, "log_count": n}


@app.get("/api/admin/drones")
def admin_drones():
    """Fleet aircraft, aliases, unlinked names from logs, and projects for the default-project menu."""
    admin_required()
    con = conn()
    drones, names = drone_name_map(con)
    fleet = [drone_payload(con, d) for d in con.execute("SELECT * FROM drones ORDER BY name")]
    unlinked = {}
    for a in con.execute("SELECT name FROM aircraft WHERE name IS NOT NULL AND TRIM(name) != ''"):
        if resolve_drone(names, drones, a["name"]) is None:
            key = a["name"].strip().casefold()
            e = unlinked.setdefault(key, {"name": a["name"].strip(), "count": 0})
            e["count"] += 1
    projects = [dict(id=p["id"], name=p["name"]) for p in con.execute("SELECT id, name FROM projects ORDER BY name")]
    return jsonify(drones=fleet, unlinked=sorted(unlinked.values(), key=lambda e: (-e["count"], e["name"].casefold())),
                   projects=projects)


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
    try:
        con.execute(
            "UPDATE drones SET name=?, unique_id=?, default_project_id=?, manager=?, current_user=?,"
            " status=?, bought_on=?, inventory_project=?, comments=? WHERE id=?",
            (name, uid, project_id, manager, current_user, status, bought_on, inventory_project, comments, drone_id))
    except sqlite3.IntegrityError:
        return jsonify(error="that ID is already used by another aircraft"), 409
    relink_all_aircraft(con)
    fill_drone_defaults(con, drone_id)
    con.commit()
    return jsonify(ok=True)


@app.post("/api/admin/drones/import")
def admin_import_drones():
    """Create or update fleet aircraft from the organisation CSV (AC Nice Name, Serial Unique, …)."""
    admin_required()
    uploaded = request.files.get("file")
    if uploaded and uploaded.filename:
        raw = uploaded.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
    else:
        text = (request.get_json(silent=True) or {}).get("csv") or ""
    if not text.strip():
        return jsonify(error="choose a CSV file"), 400
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return jsonify(error="the CSV has no header row"), 400
    def col(*names):
        keys = {k: k for k in reader.fieldnames}
        fold = {k.strip().casefold(): k for k in reader.fieldnames}
        for n in names:
            if n in keys:
                return n
            if n.casefold() in fold:
                return fold[n.casefold()]
        return None
    name_k = col("AC Nice Name", "name")
    uid_k = col("Serial Unique", "unique_id", "serial")
    if not name_k or not uid_k:
        return jsonify(error="CSV needs columns AC Nice Name and Serial Unique"), 400
    mgr_k = col("Manager (Post Holder)", "manager")
    user_k = col("Current User", "current_user")
    status_k = col("Status")
    bought_k = col("Date Bought", "bought_on")
    proj_k = col("Project", "inventory_project")
    cmt_k = col("Comments", "comments")
    con = conn()
    created = updated = skipped = 0
    errors = []
    for i, rec in enumerate(reader, start=2):
        name = (rec.get(name_k) or "").strip()
        uid = (rec.get(uid_k) or "").strip()
        if not name or not uid:
            skipped += 1
            continue
        if len(name) > 80:
            name = name[:80]
        if len(uid) > 40:
            errors.append(f"row {i}: serial {uid} is too long")
            continue
        status = parse_status(rec.get(status_k) if status_k else None)
        if status is False:
            status = clip_text(rec.get(status_k), 40)
        proj_id, proj_label = match_inventory_project(con, rec.get(proj_k) if proj_k else None)
        existing = find_import_drone(con, name, uid)
        if existing and unique_id_taken(con, uid, existing["id"]):
            errors.append(f"row {i}: serial {uid} is already used")
            continue
        if not existing and unique_id_taken(con, uid):
            errors.append(f"row {i}: serial {uid} is already used")
            continue
        name = unique_import_name(con, name, uid, existing["id"] if existing else None)
        fields = {
            "name": name, "unique_id": uid,
            "default_project_id": existing["default_project_id"] if existing and existing["default_project_id"] else proj_id,
            "manager": clip_text(rec.get(mgr_k) if mgr_k else None, 80),
            "current_user": clip_text(rec.get(user_k) if user_k else None, 80),
            "status": status, "bought_on": clip_text(rec.get(bought_k) if bought_k else None, 20),
            "inventory_project": proj_label, "comments": clip_text(rec.get(cmt_k) if cmt_k else None, 400),
        }
        try:
            if existing:
                apply_drone_fields(con, existing["id"], fields, existing["name"])
                updated += 1
            else:
                con.execute(
                    "INSERT INTO drones (name, unique_id, default_project_id, manager, current_user, status,"
                    " bought_on, inventory_project, comments, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (fields["name"], fields["unique_id"], fields["default_project_id"], fields["manager"],
                     fields["current_user"], fields["status"], fields["bought_on"], fields["inventory_project"],
                     fields["comments"], now()))
                created += 1
        except sqlite3.IntegrityError as exc:
            errors.append(f"row {i}: {exc}")
    relink_all_aircraft(con)
    assign_open_flights(con)
    con.commit()
    return jsonify(created=created, updated=updated, skipped=skipped, errors=errors)


@app.delete("/api/admin/drones/<int:drone_id>")
def admin_delete_drone(drone_id):
    admin_required()
    con = conn()
    if not con.execute("SELECT 1 FROM drones WHERE id=?", (drone_id,)).fetchone():
        return jsonify(error="no such aircraft"), 404
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


@app.put("/api/flights/<int:flight_id>/project")
def set_flight_project(flight_id):
    con = conn()
    row = con.execute(
        "SELECT f.id, f.project_id, l.id AS log_id, l.project_id AS log_project_id, l.uploaded_by"
        " FROM flights f JOIN aircraft a ON a.id = f.aircraft_id JOIN logs l ON l.id = a.log_id WHERE f.id=?",
        (flight_id,)).fetchone()
    if row is None:
        abort(404)
    where, params = flight_own_clause()
    seen = con.execute(
        "SELECT f.id FROM flights f JOIN aircraft a ON a.id = f.aircraft_id JOIN logs l ON l.id = a.log_id"
        f" WHERE f.id=? AND {where}", (flight_id, *params)).fetchone()
    if seen is None:
        abort(404)
    if not can_edit_flight_project(con, row):
        return jsonify(error="you cannot change the project of this flight"), 403
    raw = (request.get_json(silent=True) or {}).get("project_id")
    project_id = parse_project_id(raw)
    if project_id is False:
        return jsonify(error="no such project"), 400
    if project_id is not None and not live_project(con, project_id):
        return jsonify(error="no such project"), 404
    if project_id is not None and not can_upload(con, project_id) and not g.user["is_admin"]:
        return jsonify(error="you cannot put flights in this project"), 403
    con.execute("UPDATE flights SET project_id=? WHERE id=?", (project_id, flight_id))
    con.commit()
    name = con.execute("SELECT name FROM projects WHERE id=?", (project_id,)).fetchone() if project_id else None
    return jsonify(ok=True, project_id=project_id, project_name=name["name"] if name else None)


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
    email = (value or "").strip()
    if not email or len(email) > 120 or any(c.isspace() for c in email) or email.count("@") != 1:
        return None
    local, domain = email.split("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        return None
    return email


def setup_url(token):
    return request.host_url.rstrip("/") + "/setup/" + token


def lookup_setup(token):
    row = conn().execute(
        "SELECT t.id AS token_id, t.expires_at, t.used_at, u.id AS user_id, u.username, u.display_name, u.is_active "
        "FROM password_setup_tokens t JOIN users u ON u.id = t.user_id WHERE t.token_hash=?",
        (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    if row is None or row["used_at"] or not row["is_active"] or row["expires_at"] <= now():
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
            (username, display_name, email, None, int(bool(body.get("is_admin"))), now(), default_project_id))
        token, expires_at = issue_setup_token(con, cur.lastrowid)
        con.commit()
    except sqlite3.IntegrityError:
        return jsonify(error="that username already exists"), 409
    return jsonify(id=cur.lastrowid, setup_url=setup_url(token), expires_at=expires_at)


@app.post("/api/admin/users/<int:user_id>/setup-link")
def admin_new_setup_link(user_id):
    admin_required()
    con = conn()
    row = con.execute("SELECT id, is_active FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None or not row["is_active"]:
        return jsonify(error="no such user"), 404
    token, expires_at = issue_setup_token(con, user_id)
    con.commit()
    return jsonify(setup_url=setup_url(token), expires_at=expires_at)


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
        con.execute("UPDATE users SET email=? WHERE id=?", (email, user_id))
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
    if len(password) < 10:
        return jsonify(error="password must be at least 10 characters"), 400
    con = conn()
    con.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(password), row["user_id"]))
    con.execute("UPDATE password_setup_tokens SET used_at=? WHERE user_id=? AND used_at IS NULL", (now(), row["user_id"]))
    con.commit()
    return jsonify(ok=True, username=row["username"])


@app.delete("/api/admin/users/<int:user_id>")
def admin_delete_user(user_id):
    admin_required()
    if user_id == g.user["id"]:
        return jsonify(error="you cannot remove your own account"), 400
    con = conn()
    row = con.execute("SELECT id, is_admin FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        return jsonify(error="no such user"), 404
    if row["is_admin"] and con.execute("SELECT COUNT(*) AS n FROM users WHERE is_admin=1 AND is_active=1 AND id!=?",
                                       (user_id,)).fetchone()["n"] < 1:
        return jsonify(error="the last admin account cannot be removed"), 400
    for role in ROLES:
        con.execute(f"UPDATE flights SET {role}_user_id=NULL WHERE {role}_user_id=?", (user_id,))
    con.execute("DELETE FROM users WHERE id=?", (user_id,))
    con.commit()
    return jsonify(ok=True)


@app.get("/api/admin/people")
def admin_people():
    """Users with their aliases, plus the names seen in logs that are not linked to any user yet."""
    admin_required()
    con = conn()
    users = [dict(id=u["id"], username=u["username"], display_name=u["display_name"], email=u["email"],
                  is_admin=bool(u["is_admin"]), has_password=bool(u["password_hash"]),
                  provider=u["provider"] or "local",
                  default_project_id=u["default_project_id"],
                  aliases=[r["alias"] for r in con.execute(
                      "SELECT alias FROM user_aliases WHERE user_id=? ORDER BY alias", (u["id"],))])
             for u in con.execute("SELECT * FROM users ORDER BY username")]
    resolve = person_resolver(con)
    unlinked = {}
    for a in con.execute("SELECT a.pic, a.pac, a.gcs FROM aircraft a JOIN logs l ON l.id = a.log_id"):
        for role in ROLES:
            name = (a[role] or "").strip()
            if name and resolve(role, name, None) is None:
                e = unlinked.setdefault(name.casefold(), {"name": name, "count": 0})
                e["count"] += 1
    projects = [dict(id=p["id"], name=p["name"]) for p in con.execute("SELECT id, name FROM projects ORDER BY name")]
    return jsonify(users=users, unlinked=sorted(unlinked.values(), key=lambda e: -e["count"]), projects=projects)


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


def log_dict(row):
    d = dict(row)
    d["outdated"] = d["status"] == "done" and d["parser_version"] != current_version()
    d["has_log"] = d.pop("log_path") is not None
    d.pop("data_path", None)
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


@app.get("/")
def index():
    return send_from_directory(PAGES_DIR, "index.html")


@app.get("/logbook")
def logbook_page():
    return send_from_directory(PAGES_DIR, "logbook.html")


@app.get("/stats")
def stats_page():
    return send_from_directory(PAGES_DIR, "stats.html")


SAME_FLIGHT_SLACK_S = 60
TYPE_ORDER = {"onboard_sd": 0, "gcs": 1}  # which source supplies the flight data; people/goal prefer the GCS


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
        if not main["landed"]:
            donor = next((o for o in fl[1:] if o["landed"]), None)
            if donor:
                for k in ("landing_utc", "landing_lat", "landing_lon", "landing_voltage_v", "landing_json", "duration_s", "landed"):
                    main[k] = donor[k]
        for role in ROLES + ("goal",):
            main[role] = next((o[role] for o in sorted(fl, key=lambda r: -TYPE_ORDER[r["log_type"]]) if (o[role] or "").strip()), main[role])
        for role in ROLES:
            key = role + "_user_id"
            main[key] = next((o[key] for o in sorted(fl, key=lambda r: -TYPE_ORDER[r["log_type"]]) if o.get(key)), main.get(key))
        main["comment"] = next((o["comment"] for o in sorted(fl, key=lambda r: -TYPE_ORDER[r["log_type"]]) if (o.get("comment") or "").strip()), main.get("comment"))
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
    """One row per takeoff/landing over all visible logs, newest first."""
    return jsonify(logbook_rows())


@app.get("/api/stats")
def stats():
    """Flight hours by aircraft and by project.

    Visibility matches the logbook (see flight_own_clause): admins get every flight; everyone else only
    flights on projects they belong to, plus logs they uploaded that are not in a project yet. An SD log
    and a GCS log of the same flight are counted once. Aircraft hours are only from those visible flights,
    not the aircraft's lifetime across other projects.
    """
    keys = ("aircraft", "aircraft_uid", "drone_id", "project_id", "project_name",
            "duration_s", "takeoff_utc", "verdict", "indoor")
    return jsonify(all=bool(g.user["is_admin"]),
                   flights=[{k: r.get(k) for k in keys} for r in logbook_rows()])


def logbook_rows():
    where, params = flight_list_clause()
    con = conn()
    rows = con.execute(
        "SELECT f.id AS flight_id, f.idx, f.takeoff_utc, f.landing_utc, f.duration_s, f.landed,"
        " f.takeoff_lat, f.takeoff_lon, f.landing_lat, f.landing_lon, f.project_id,"
        " f.pic_user_id, f.pac_user_id, f.gcs_user_id, f.comment,"
        " a.id AS aircraft_pk, a.ac_id, COALESCE(d.name, a.name) AS aircraft, a.name AS aircraft_logged,"
        " d.unique_id AS aircraft_uid, a.drone_id, a.pic, a.pac, a.gcs, a.goal,"
        " f.verdict, f.assessment_json, f.takeoff_voltage_v, f.landing_voltage_v, f.num_points, f.takeoff_json, f.landing_json,"
        " l.id AS log_id, l.name AS log_name, l.log_type, l.uploaded_by, l.uploaded_at, l.sha256, l.log_path IS NOT NULL AS has_log,"
        " l.project_id AS log_project_id,"
        " (SELECT username FROM users u WHERE u.id = l.uploaded_by) AS uploaded_by_name"
        " FROM flights f JOIN aircraft a ON a.id = f.aircraft_id JOIN logs l ON l.id = a.log_id"
        " LEFT JOIN drones d ON d.id = a.drone_id"
        f" WHERE {where} AND l.status = 'done'"
        " ORDER BY COALESCE(f.takeoff_utc, l.start_utc) DESC, f.idx", params).fetchall()
    names = {p["id"]: p["name"] for p in con.execute("SELECT id, name FROM projects")}
    out = merge_sources([dict(r) for r in rows])
    place = place_resolver(con)
    for r in out:
        r["takeoff_place"], r["indoor"] = place(r["takeoff_lat"], r["takeoff_lon"])
        r["landing_place"] = place(r["landing_lat"], r["landing_lon"])[0]
        r["assessment"] = json.loads(r.pop("assessment_json") or "null")
        r["takeoff"] = json.loads(r.pop("takeoff_json") or "null")
        r["landing"] = json.loads(r.pop("landing_json") or "null")
        pid = effective_flight_project(r)
        r["project_id"] = pid
        r["project_name"] = names.get(pid)
        r["can_edit_project"] = can_edit_flight_project(con, r)
        r["can_edit_crew"] = r["can_edit_project"]
        r["can_edit_comment"] = r["can_edit_project"]
    return add_people(out, person_resolver(con))


@app.get("/api/flights/<int:flight_id>/detail")
def flight_detail(flight_id):
    """Everything for the flight page: the logbook entry (merged sources), this log's path, flight plan,
    setting changes and messages."""
    entry = next((r for r in logbook_rows() if any(s["flight_id"] == flight_id for s in r["sources"])), None)
    if entry is None:
        abort(404)
    con = conn()
    row = con.execute(
        "SELECT f.takeoff_time_s, f.landing_time_s, f.path_gz, a.id AS aircraft_pk, a.name, a.ac_id, a.autopilot_versions,"
        " a.info_messages, a.preflight_checks, a.config_gz, l.id AS log_id, l.log_type, l.name AS log_name"
        " FROM flights f JOIN aircraft a ON a.id = f.aircraft_id JOIN logs l ON l.id = a.log_id WHERE f.id=?",
        (flight_id,)).fetchone()
    changes = [dict(r) for r in con.execute(
        "SELECT time_s, utc, idx, var, from_value, to_value, in_flight FROM setting_changes"
        " WHERE aircraft_id=? ORDER BY time_s", (row["aircraft_pk"],))]
    config = db.unpack(row["config_gz"]) or {}
    return jsonify(
        entry=entry, flight_id=flight_id, log_id=row["log_id"], log_type=_type_of(dict(row)),
        takeoff_time_s=row["takeoff_time_s"], landing_time_s=row["landing_time_s"],
        path=db.unpack(row["path_gz"]) or [], flight_plan=config.get("flight_plan"),
        setting_changes=changes, info_messages=json.loads(row["info_messages"] or "[]"),
        preflight_checks=json.loads(row["preflight_checks"] or "[]"),
        autopilot_versions=json.loads(row["autopilot_versions"] or "[]"))


@app.get("/api/status")
def status():
    con = conn()
    where, params = list_clause()
    counts = {r["status"]: r["n"] for r in
              con.execute(f"SELECT status, COUNT(*) n FROM logs WHERE {where} GROUP BY status", params)}
    outdated = con.execute(f"SELECT COUNT(*) FROM logs WHERE {where} AND status='done'"
                           " AND COALESCE(parser_version,'') != ?", (*params, current_version() or "")).fetchone()[0]
    return jsonify(parser_version=current_version(), path_rate_hz=PATH_RATE, queue=jobs.qsize(), counts=counts,
                   outdated=outdated)


@app.get("/api/logs")
def list_logs():
    where, params = list_clause("l.")
    con = conn()
    rows = con.execute(
        "SELECT l.*, p.name AS project_name,"
        " (SELECT username FROM users u WHERE u.id = l.uploaded_by) AS uploaded_by_name,"
        " (SELECT group_concat(DISTINCT COALESCE(d.name, a.name, '?')) FROM aircraft a"
        " LEFT JOIN drones d ON d.id = a.drone_id WHERE a.log_id = l.id) AS aircraft_names,"
        " (SELECT SUM(a.num_flights) FROM aircraft a WHERE a.log_id = l.id) AS num_flights,"
        " (SELECT SUM(a.total_flight_time_s) FROM aircraft a WHERE a.log_id = l.id) AS flight_time_s"
        " FROM logs l LEFT JOIN projects p ON p.id = l.project_id WHERE " + where + " ORDER BY l.id DESC",
        params).fetchall()
    return jsonify([{**log_dict(r), "can_delete": can_delete_log(con, r)} for r in rows])


@app.get("/api/logs/<int:log_id>")
def get_log(log_id):
    con = conn()
    row = visible_log(con, log_id)
    out = log_dict(row)
    out["can_delete"] = can_delete_log(con, row)
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
        for k in ("autopilot_versions", "preflight_checks", "info_messages"):
            ad[k] = json.loads(ad[k]) if ad[k] else []
        flights = []
        for f in con.execute("SELECT * FROM flights WHERE aircraft_id=? ORDER BY idx", (a["id"],)):
            fd = {k: f[k] for k in f.keys() if k not in ("path_gz", "takeoff_json", "landing_json")}
            fd["takeoff"] = json.loads(f["takeoff_json"] or "null")
            fd["landing"] = json.loads(f["landing_json"] or "null")
            pid = f["project_id"]
            pn = con.execute("SELECT name FROM projects WHERE id=?", (pid,)).fetchone() if pid else None
            fd["project_name"] = pn["name"] if pn else None
            flights.append(fd)
        ad["flights"] = flights
        ad["setting_changes"] = [dict(c) for c in con.execute(
            "SELECT time_s, utc, previous_report_s, idx, var, from_value, to_value, in_flight"
            " FROM setting_changes WHERE aircraft_id=? ORDER BY time_s", (a["id"],))]
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
    row = conn().execute("SELECT f.path_gz FROM flights f JOIN aircraft a ON a.id = f.aircraft_id"
                         f" JOIN logs l ON l.id = a.log_id WHERE f.id=? AND {where}", (flight_id, *params)).fetchone()
    if row is None:
        abort(404)
    return jsonify(db.unpack(row["path_gz"]) or [])


@app.get("/api/logs/<int:log_id>/download/<kind>")
def download(log_id, kind):
    row = visible_log(conn(), log_id, "data_path, log_path")
    rel = row["data_path"] if kind == "data" else row["log_path"] if kind == "log" else None
    if not rel:
        abort(404)
    return send_from_directory(STORAGE_DIR, rel, as_attachment=True)


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
    """Yield an uncompressed (stored) zip of [(archive_name, path)] without building it on disk."""
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
        for arc, path in files:
            with open(path, "rb") as src, z.open(zipfile.ZipInfo.from_file(path, arc), "w", force_zip64=True) as dst:
                while chunk := src.read(4 << 20):
                    dst.write(chunk)
                    yield sink.take()
    yield sink.take()


@app.get("/api/logs/<int:log_id>/download")
def download_both(log_id):
    """Zip with the .data and .log file of a log."""
    row = visible_log(conn(), log_id, "name, data_path, log_path")
    files = [(posixpath.basename(rel), STORAGE_DIR / rel) for rel in (row["data_path"], row["log_path"]) if rel]
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
    if con.execute("UPDATE logs SET status='queued' WHERE id=? AND status IN ('done','error')", (log_id,)).rowcount:
        con.commit()
        jobs.put(log_id)
        return jsonify(queued=[log_id])
    return jsonify(queued=[])


@app.post("/api/reparse-outdated")
def reparse_outdated():
    """Queue every log parsed by another parser version than the installed one, and failed ones."""
    version = parser_version()
    app.config["PARSER_VERSION"] = version
    con = conn()
    where, params = list_clause()
    ids = [r["id"] for r in con.execute(
        f"SELECT id FROM logs WHERE {where} AND (status='error' OR (status='done' AND COALESCE(parser_version,'') != ?))",
        (*params, version or ""))]
    for i in ids:
        con.execute("UPDATE logs SET status='queued' WHERE id=?", (i,))
    con.commit()
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
    con.execute("DELETE FROM logs WHERE id=?", (log_id,))
    con.commit()
    shutil.rmtree(STORAGE_DIR / row["sha256"][:2] / row["sha256"], ignore_errors=True)
    return jsonify(deleted=log_id)


if __name__ == "__main__" and len(sys.argv) > 1:
    cli(sys.argv[1:])
    sys.exit(0)

start_workers()
if not conn().execute("SELECT 1 FROM users LIMIT 1").fetchone():
    print("No accounts yet. Create one with:  python3 app.py create-user <username> --admin", file=sys.stderr)

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")), threaded=True)
