"""Web interface to upload Paparazzi logs (.data/.log files, zip archives, folders).

Uploaded logs are kept on disk, hashed (sha256 of the .data file) and parsed with the fast C++ parser
into a SQLite database. A log with a known hash is never stored or parsed twice. The version of the
parser that produced the stored result is recorded so outdated results can be re-parsed later.
"""
import hashlib
import difflib
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
from datetime import datetime, timezone
from pathlib import Path

from flask import (Flask, Response, abort, g, jsonify, redirect, request, send_from_directory, session,
                   stream_with_context)
from werkzeug.security import check_password_hash, generate_password_hash

import db

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
        db.store_parsed(con, log_id, doc)
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
        con = conn()
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
                    "INSERT INTO logs (name, sha256, size_bytes, data_path, log_path, status, uploaded_at, uploaded_by)"
                    " VALUES (?,?,?,?,?,'queued',?,?)", (name, sha, size, data_rel, log_rel, now(), g.user["id"]))
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
# Local accounts for now. The users table already has provider/subject columns, so an external login (OpenID
# Connect with the TU Delft SSO) can be added later by creating/looking up users on (provider, subject).
PUBLIC_ENDPOINTS = {"login_page", "login"}
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
    if resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-cache"
    return resp


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


def add_people(rows, resolve):
    """Attach pic_user/pac_user/gcs_user to dict rows that carry pic/pac/gcs and uploaded_by."""
    for r in rows:
        for role in ROLES:
            r[role + "_user"] = resolve(role, r.get(role), r.get("uploaded_by"))
    return rows


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


@app.get("/api/admin/people")
def admin_people():
    """Users with their aliases, plus the names seen in logs that are not linked to any user yet."""
    admin_required()
    con = conn()
    users = [dict(id=u["id"], username=u["username"], display_name=u["display_name"], is_admin=bool(u["is_admin"]),
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
    return jsonify(users=users, unlinked=sorted(unlinked.values(), key=lambda e: -e["count"]))


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
    """SQL filter and params restricting to the current user's logs (admins see everything)."""
    if g.user["is_admin"]:
        return "1=1", ()
    return f"{alias}uploaded_by = ?", (g.user["id"],)


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


def logbook_rows():
    where, params = own_clause("l.")
    rows = conn().execute(
        "SELECT f.id AS flight_id, f.idx, f.takeoff_utc, f.landing_utc, f.duration_s, f.landed,"
        " f.takeoff_lat, f.takeoff_lon, f.landing_lat, f.landing_lon,"
        " a.id AS aircraft_pk, a.ac_id, a.name AS aircraft, a.pic, a.pac, a.gcs, a.goal,"
        " f.verdict, f.assessment_json, f.takeoff_voltage_v, f.landing_voltage_v, f.num_points, f.takeoff_json, f.landing_json,"
        " l.id AS log_id, l.name AS log_name, l.log_type, l.uploaded_by, l.uploaded_at, l.sha256, l.log_path IS NOT NULL AS has_log,"
        " (SELECT username FROM users u WHERE u.id = l.uploaded_by) AS uploaded_by_name"
        " FROM flights f JOIN aircraft a ON a.id = f.aircraft_id JOIN logs l ON l.id = a.log_id"
        f" WHERE {where} AND l.status = 'done'"
        " ORDER BY COALESCE(f.takeoff_utc, l.start_utc) DESC, f.idx", params).fetchall()
    out = merge_sources([dict(r) for r in rows])
    place = place_resolver(conn())
    for r in out:
        r["takeoff_place"], r["indoor"] = place(r["takeoff_lat"], r["takeoff_lon"])
        r["landing_place"] = place(r["landing_lat"], r["landing_lon"])[0]
        r["assessment"] = json.loads(r.pop("assessment_json") or "null")
        r["takeoff"] = json.loads(r.pop("takeoff_json") or "null")
        r["landing"] = json.loads(r.pop("landing_json") or "null")
    return add_people(out, person_resolver(conn()))


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
    where, params = own_clause()
    counts = {r["status"]: r["n"] for r in
              con.execute(f"SELECT status, COUNT(*) n FROM logs WHERE {where} GROUP BY status", params)}
    outdated = con.execute(f"SELECT COUNT(*) FROM logs WHERE {where} AND status='done'"
                           " AND COALESCE(parser_version,'') != ?", (*params, current_version() or "")).fetchone()[0]
    return jsonify(parser_version=current_version(), path_rate_hz=PATH_RATE, queue=jobs.qsize(), counts=counts,
                   outdated=outdated)


@app.get("/api/logs")
def list_logs():
    where, params = own_clause("l.")
    rows = conn().execute(
        "SELECT l.*, (SELECT username FROM users u WHERE u.id = l.uploaded_by) AS uploaded_by_name,"
        " (SELECT group_concat(DISTINCT COALESCE(a.name, '?')) FROM aircraft a WHERE a.log_id = l.id) AS aircraft_names,"
        " (SELECT SUM(a.num_flights) FROM aircraft a WHERE a.log_id = l.id) AS num_flights,"
        " (SELECT SUM(a.total_flight_time_s) FROM aircraft a WHERE a.log_id = l.id) AS flight_time_s"
        " FROM logs l WHERE " + where + " ORDER BY l.id DESC", params).fetchall()
    return jsonify([log_dict(r) for r in rows])


@app.get("/api/logs/<int:log_id>")
def get_log(log_id):
    con = conn()
    row = visible_log(con, log_id)
    out = log_dict(row)
    resolve = person_resolver(con)
    out["aircraft"] = []
    for a in con.execute("SELECT * FROM aircraft WHERE log_id=? ORDER BY ac_id", (log_id,)):
        ad = {k: a[k] for k in a.keys() if k != "config_gz"}
        ad["uploaded_by"] = row["uploaded_by"]
        add_people([ad], resolve)
        for k in ("autopilot_versions", "preflight_checks", "info_messages"):
            ad[k] = json.loads(ad[k]) if ad[k] else []
        flights = []
        for f in con.execute("SELECT * FROM flights WHERE aircraft_id=? ORDER BY idx", (a["id"],)):
            fd = {k: f[k] for k in f.keys() if k not in ("path_gz", "takeoff_json", "landing_json")}
            fd["takeoff"] = json.loads(f["takeoff_json"] or "null")
            fd["landing"] = json.loads(f["landing_json"] or "null")
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
    where, params = own_clause()
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
    row = visible_log(con, log_id, "sha256, status")
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
