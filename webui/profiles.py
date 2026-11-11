"""Person profiles: role, department, supervisors, expected end date, student/employee number and GitHub name.

Off unless LOGDB_PERSON_PROFILE=1. When on, everyone who signs in in the browser fills in their profile before
using the site, and again once the expected end date has passed (API keys are never stopped). The person and
admins see the whole profile; supervisors see their students' profiles without the number. Admins edit the
role and department lists (People, Edit mode). The role list is filled in once when the tables are created;
the department list starts empty (site-specific: admins add or import it), and people can also type one.
"""
import csv
import datetime
import io
import json
import re
import threading
import time
import urllib.error
import urllib.request

from flask import abort, g, jsonify, request, send_from_directory

from config import env, env_bool

ROLES = (  # name, can supervise, needs a supervisor
    ("Bachelor Student", 0, 1), ("Master Student", 0, 1), ("PhD Student", 1, 1), ("PostDoc", 1, 0),
    ("Teaching Assistant", 0, 1), ("Staff", 1, 0), ("Extern", 0, 0))
GITHUB_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_github_cache, _github_lock = {}, threading.Lock()


class ProfileError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def enabled():
    return env_bool("PERSON_PROFILE")


def seed(con):
    con.executemany("INSERT OR IGNORE INTO profile_roles (name, can_supervise, needs_supervisor, sort_order)"
                    " VALUES (?,?,?,?)", [(n, s, need, i) for i, (n, s, need) in enumerate(ROLES, 1)])


def today():
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def options(con):
    roles = [dict(r, can_supervise=bool(r["can_supervise"]), needs_supervisor=bool(r["needs_supervisor"]))
             for r in con.execute("SELECT id, name, can_supervise, needs_supervisor FROM profile_roles"
                                  " ORDER BY sort_order, name")]
    departments = [dict(r) for r in con.execute(
        "SELECT id, name, grp FROM profile_departments ORDER BY sort_order, name")]
    supervisors = [dict(r) for r in con.execute(
        "SELECT u.id, COALESCE(u.display_name, u.username) AS name, r.name AS role FROM users u"
        " JOIN user_profiles p ON p.user_id=u.id JOIN profile_roles r ON r.id=p.role_id"
        " WHERE r.can_supervise=1 AND u.is_active=1 AND u.is_external=0 ORDER BY name")]
    return dict(roles=roles, departments=departments, supervisors=supervisors)


def profile(con, user_id, with_number=True):
    """The profile of a person (None when there is none), with role and department names and supervisors."""
    row = con.execute(
        "SELECT p.*, r.name AS role, r.needs_supervisor, d.name AS department, d.grp AS department_group"
        " FROM user_profiles p LEFT JOIN profile_roles r ON r.id=p.role_id"
        " LEFT JOIN profile_departments d ON d.id=p.department_id WHERE p.user_id=?", (user_id,)).fetchone()
    if row is None:
        return None
    sups = [dict(id=r["supervisor_id"], name=r["name"] or r["external_name"], external=r["supervisor_id"] is None)
            for r in con.execute(
                "SELECT s.supervisor_id, s.external_name, COALESCE(u.display_name, u.username) AS name"
                " FROM user_supervisors s LEFT JOIN users u ON u.id=s.supervisor_id WHERE s.user_id=? ORDER BY s.id",
                (user_id,))]
    out = dict(role_id=row["role_id"], role=row["role"], department_id=row["department_id"],
               department=row["department"] or row["department_text"], department_group=row["department_group"],
               department_text=row["department_text"], end_date=row["end_date"], indefinite=bool(row["indefinite"]),
               github=row["github"], updated_at=row["updated_at"], to_confirm=bool(row["to_confirm"]),
               supervisors=sups)
    if with_number:
        out["number"] = row["number"]
    return out


def projects(con, user_id, viewer):
    """The projects a person is a member of, limited to those the viewer may open (admins: all)."""
    sql = ("SELECT p.id, p.name FROM project_members m JOIN projects p ON p.id=m.project_id WHERE m.user_id=?")
    args = [user_id]
    if not viewer["is_admin"]:
        sql += " AND p.id IN (SELECT project_id FROM project_members WHERE user_id=?)"
        args.append(viewer["id"])
    return [dict(r) for r in con.execute(sql + " ORDER BY p.name", args)]


def supervised_ids(con, supervisor_id):
    return [r[0] for r in con.execute("SELECT DISTINCT s.user_id FROM user_supervisors s JOIN users u ON u.id=s.user_id"
                                      " WHERE s.supervisor_id=? AND u.is_active=1", (supervisor_id,))]


def visible_people(con, viewer):
    """The people whose profile the viewer may see in lists: admins everyone who can sign in; others themselves
    and the people they supervise."""
    if viewer["is_admin"]:
        ids = [r[0] for r in con.execute("SELECT id FROM users WHERE is_active=1 AND is_external=0")]
    else:
        ids = [viewer["id"], *supervised_ids(con, viewer["id"])]
    out = []
    for uid in dict.fromkeys(ids):
        u = con.execute("SELECT id, username, COALESCE(display_name, username) AS name FROM users WHERE id=?",
                        (uid,)).fetchone()
        if u is None:
            continue
        own = uid == viewer["id"]
        out.append(dict(id=u["id"], name=u["name"], username=u["username"], self=own,
                        profile=profile(con, uid, with_number=bool(viewer["is_admin"]) or own),
                        missing=missing(con, uid), projects=projects(con, uid, viewer)))
    return sorted(out, key=lambda r: r["name"].casefold())


def missing(con, user_id):
    """What still has to be filled in (empty when the profile is complete and current)."""
    p = profile(con, user_id)
    if p is None:
        return ["profile"]
    out = []
    if not p["role_id"]:
        out.append("role")
    if not p["department"]:
        out.append("department")
    if not p["indefinite"] and not (p["end_date"] and p["end_date"] >= today()):
        out.append("end_date")
    if not (p["number"] or "").strip():
        out.append("number")
    needs = con.execute("SELECT needs_supervisor FROM profile_roles WHERE id=?", (p["role_id"],)).fetchone()
    if needs and needs["needs_supervisor"] and not p["supervisors"]:
        out.append("supervisors")
    return out


def needs_profile(con, user):
    """True when this signed-in browser user must (re)fill the profile before using the site."""
    if not enabled() or user["is_external"]:
        return False
    if missing(con, user["id"]):
        return True
    row = con.execute("SELECT to_confirm FROM user_profiles WHERE user_id=?", (user["id"],)).fetchone()
    return bool(row and row["to_confirm"])  # filled in by an admin or an import: the person checks it once


def github_status(name):
    """'found', 'missing' or 'unknown' (GitHub not reachable), from GitHub's public users API, cached an hour."""
    key = name.lower()
    with _github_lock:
        hit = _github_cache.get(key)
        if hit and time.time() - hit[1] < 3600:
            return hit[0]
    req = urllib.request.Request(f"https://api.github.com/users/{name}",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "flight-logbook"})
    token = env("FIRMWARE_GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            status = "found" if json.load(r).get("login") else "missing"
    except urllib.error.HTTPError as exc:
        status = "missing" if exc.code == 404 else "unknown"
    except (OSError, ValueError):
        status = "unknown"
    if status != "unknown":
        with _github_lock:
            _github_cache[key] = (status, time.time())
    return status


def _text(body, key, limit):
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or len(value.strip()) > limit:
        raise ProfileError(f"{key.replace('_', ' ')} must be text of at most {limit} characters.")
    return value.strip() or None


def save(con, user_id, body, stamp, by_admin=False):
    """Validate and store a person's own profile (the whole form)."""
    if not isinstance(body, dict):
        raise ProfileError("Send a JSON object.")
    role_id = body.get("role_id")
    if type(role_id) is not int or not con.execute("SELECT 1 FROM profile_roles WHERE id=?", (role_id,)).fetchone():
        raise ProfileError("Choose what you are.")
    department_id = body.get("department_id")
    department_text = _text(body, "department_text", 120)
    if department_id is not None:
        if type(department_id) is not int or not con.execute(
                "SELECT 1 FROM profile_departments WHERE id=?", (department_id,)).fetchone():
            raise ProfileError("Unknown department.")
        department_text = None
    elif not department_text:
        raise ProfileError("Choose a department or type it.")
    indefinite = body.get("indefinite", False)
    if type(indefinite) is not bool:
        raise ProfileError("indefinite must be true or false.")
    end_date = None if indefinite else _text(body, "end_date", 10)
    if not indefinite:
        try:
            if not end_date or not DATE.match(end_date):
                raise ValueError
            datetime.date.fromisoformat(end_date)
        except ValueError:
            raise ProfileError("Give the expected end date (or tick indefinite).") from None
        if end_date < today():
            raise ProfileError("The expected end date has passed; give a new one.")
    number = _text(body, "number", 40)
    if not number:
        raise ProfileError("Give your student or employee number.")
    github = _text(body, "github", 39)
    if github and not GITHUB_NAME.match(github):
        raise ProfileError("That is not a valid GitHub username.")
    sups = body.get("supervisors") or []
    if not isinstance(sups, list) or len(sups) > 10:
        raise ProfileError("supervisors must be a list (at most 10).")
    allowed = {s["id"] for s in options(con)["supervisors"]}
    rows = []
    for s in sups:
        if isinstance(s, dict) and type(s.get("id")) is int:
            if s["id"] == user_id or s["id"] not in allowed:
                raise ProfileError("Choose supervisors from the list, or add them as external.")
            rows.append((s["id"], None))
        elif isinstance(s, dict) and isinstance(s.get("name"), str) and s["name"].strip():
            if len(s["name"].strip()) > 120:
                raise ProfileError("An external supervisor's name is at most 120 characters.")
            rows.append((None, s["name"].strip()))
        else:
            raise ProfileError("Each supervisor is {id} from the list or {name} for an external one.")
    rows = list(dict.fromkeys(rows))
    needs = con.execute("SELECT needs_supervisor FROM profile_roles WHERE id=?", (role_id,)).fetchone()[0]
    if needs and not rows:
        raise ProfileError("Name at least one supervisor.")
    con.execute(
        "INSERT INTO user_profiles (user_id, role_id, department_id, department_text, end_date, indefinite, number,"
        " github, updated_at, to_confirm) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET"
        " role_id=excluded.role_id, department_id=excluded.department_id, department_text=excluded.department_text,"
        " end_date=excluded.end_date, indefinite=excluded.indefinite, number=excluded.number, github=excluded.github,"
        + (" to_confirm=to_confirm" if by_admin else " updated_at=excluded.updated_at, to_confirm=0"),
        # an admin's change keeps "last confirmed" and, for a new profile, asks the person to check it
        (user_id, role_id, department_id, department_text, end_date, int(indefinite), number, github,
         None if by_admin else stamp, int(by_admin)))
    con.execute("DELETE FROM user_supervisors WHERE user_id=?", (user_id,))
    con.executemany("INSERT INTO user_supervisors (user_id, supervisor_id, external_name) VALUES (?,?,?)",
                    [(user_id, sid, name) for sid, name in rows])


def save_options(con, body):
    """Replace the role and department lists (admins). Profiles keep working: a removed entry becomes empty there."""
    if not isinstance(body, dict):
        raise ProfileError("Send a JSON object.")
    for key, fields in (("roles", ("name", "can_supervise", "needs_supervisor")), ("departments", ("name", "grp"))):
        items = body.get(key)
        if items is None:
            continue
        if not isinstance(items, list) or (key == "roles" and not items) or len(items) > 200:
            raise ProfileError("roles must be a non-empty list." if key == "roles" else "departments must be a list.")
        table = "profile_roles" if key == "roles" else "profile_departments"
        seen, keep = set(), []
        for i, item in enumerate(items, 1):
            name = (item.get("name") or "").strip() if isinstance(item, dict) else ""
            if not name or len(name) > 120 or name.casefold() in seen:
                raise ProfileError(f"{key}: give every entry a unique name (at most 120 characters).")
            seen.add(name.casefold())
            values = [name] + [int(bool(item.get(f))) if f != "grp" else ((item.get("grp") or "").strip() or None)
                               for f in fields[1:]] + [i]
            if type(item.get("id")) is int and con.execute(f"SELECT 1 FROM {table} WHERE id=?", (item["id"],)).fetchone():
                con.execute(f"UPDATE {table} SET {', '.join(f'{f}=?' for f in fields)}, sort_order=? WHERE id=?",
                            (*values, item["id"]))
                keep.append(item["id"])
            else:
                keep.append(con.execute(f"INSERT INTO {table} ({', '.join(fields)}, sort_order) VALUES"
                                        f" ({', '.join('?' * (len(fields) + 1))})", values).lastrowid)
        con.execute(f"DELETE FROM {table} WHERE id NOT IN ({','.join('?' * len(keep))})", keep)


IMPORT_COLUMNS = ("username", "role", "department", "supervisors", "end_date", "number", "github")


def import_profiles(con, text, apply=False):
    """Fill in profiles from CSV text (admins). A row names a person by username (or email) and fills only the fields
    that are still empty; existing values stay. Rows may leave fields empty. Imported profiles are marked to_confirm,
    so the person checks them at their next sign-in. Returns one result per row; nothing is written while any row
    has an error, or without apply."""
    if not isinstance(text, str) or not text.strip():
        raise ProfileError("Choose a CSV file.")
    if len(text) > 2_000_000:
        raise ProfileError("The file is too large.")
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")), skipinitialspace=True)
    headers = {(h or "").strip().lower(): h for h in reader.fieldnames or []}
    if "username" not in headers:
        raise ProfileError("The CSV needs a username column (" + ", ".join(IMPORT_COLUMNS) + ").")
    unknown = sorted(set(headers) - set(IMPORT_COLUMNS))
    if unknown:
        raise ProfileError("Unknown columns: " + ", ".join(unknown) + ".")
    roles = {r["name"].casefold(): r["id"] for r in con.execute("SELECT id, name FROM profile_roles")}
    needs = {r["id"]: r["needs_supervisor"] for r in con.execute("SELECT id, needs_supervisor FROM profile_roles")}
    depts = {r["name"].casefold(): r["id"] for r in con.execute("SELECT id, name FROM profile_departments")}
    supervisors = {}
    for s in options(con)["supervisors"]:
        supervisors.setdefault(s["name"].casefold(), s["id"])
    for r in con.execute("SELECT u.id, u.username FROM users u WHERE u.id IN (SELECT user_id FROM user_profiles p"
                         " JOIN profile_roles r ON r.id=p.role_id WHERE r.can_supervise=1)"):
        supervisors.setdefault(r["username"].casefold(), r["id"])
    rows, seen = [], set()
    for line, raw in enumerate(reader, 2):
        cell = {k: (raw.get(h) or "").strip() for k, h in headers.items()}
        if not any(cell.values()):
            continue
        out = {"line": line, "username": cell["username"], "changes": [], "error": None}
        rows.append(out)
        user = con.execute("SELECT id, COALESCE(display_name, username) AS name FROM users WHERE is_external=0 AND"
                           " (username=? COLLATE NOCASE OR email=? COLLATE NOCASE)",
                           (cell["username"], cell["username"])).fetchone() if cell["username"] else None
        if user is None:
            out["error"] = "unknown username" if cell["username"] else "no username"
            continue
        if user["id"] in seen:
            out["error"] = "this person is in the file twice"
            continue
        seen.add(user["id"])
        out["name"], out["user_id"] = user["name"], user["id"]
        old = con.execute("SELECT * FROM user_profiles WHERE user_id=?", (user["id"],)).fetchone()
        new = dict(old) if old else {"role_id": None, "department_id": None, "department_text": None, "end_date": None,
                                      "indefinite": 0, "number": None, "github": None}
        try:
            if cell.get("role") and not new["role_id"]:
                if cell["role"].casefold() not in roles:
                    raise ProfileError(f"unknown role '{cell['role']}'")
                new["role_id"] = roles[cell["role"].casefold()]
                out["changes"].append("role")
            if cell.get("department") and not new["department_id"] and not new["department_text"]:
                if cell["department"].casefold() in depts:
                    new["department_id"] = depts[cell["department"].casefold()]
                else:
                    new["department_text"] = cell["department"][:120]
                out["changes"].append("department")
            if cell.get("end_date") and not new["end_date"] and not new["indefinite"]:
                if cell["end_date"].casefold() == "indefinite":
                    new["indefinite"] = 1
                else:
                    try:
                        if not DATE.match(cell["end_date"]):
                            raise ValueError
                        datetime.date.fromisoformat(cell["end_date"])
                    except ValueError:
                        raise ProfileError(f"end_date '{cell['end_date']}' is not YYYY-MM-DD or indefinite") from None
                    new["end_date"] = cell["end_date"]
                out["changes"].append("end date")
            if cell.get("number") and not (new["number"] or "").strip():
                new["number"] = cell["number"][:40]
                out["changes"].append("number")
            if cell.get("github") and not new["github"]:
                if not GITHUB_NAME.match(cell["github"]):
                    raise ProfileError(f"'{cell['github']}' is not a valid GitHub username")
                new["github"] = cell["github"]
                out["changes"].append("GitHub")
            sups = []
            has_sups = con.execute("SELECT 1 FROM user_supervisors WHERE user_id=?", (user["id"],)).fetchone()
            if cell.get("supervisors") and not has_sups:
                for name in [n.strip() for n in cell["supervisors"].split(";") if n.strip()][:10]:
                    sid = supervisors.get(name.casefold())
                    if sid == user["id"]:
                        raise ProfileError("a person cannot supervise themselves")
                    sups.append((sid, None) if sid else (None, name[:120]))
                sups = list(dict.fromkeys(sups))
                if sups:
                    out["changes"].append("supervisors")
        except ProfileError as exc:
            out["error"] = str(exc)
            continue
        out["status"] = "unchanged" if not out["changes"] else "update" if old else "new"
        out["sups"], out["new"] = sups, new
        out["still_missing"] = [m for m in (
            "role" if not new["role_id"] else "", "department" if not (new["department_id"] or new["department_text"]) else "",
            "end date" if not (new["end_date"] or new["indefinite"]) else "", "number" if not (new["number"] or "").strip() else "",
            "supervisors" if new["role_id"] and needs.get(new["role_id"]) and not (has_sups or sups) else "") if m]
    errors = sum(1 for r in rows if r["error"])
    if apply and not errors:
        for r in rows:
            if r["status"] == "unchanged":
                continue
            n = r["new"]
            con.execute(
                "INSERT INTO user_profiles (user_id, role_id, department_id, department_text, end_date, indefinite,"
                " number, github, to_confirm) VALUES (?,?,?,?,?,?,?,?,1) ON CONFLICT(user_id) DO UPDATE SET"
                " role_id=excluded.role_id, department_id=excluded.department_id, department_text=excluded.department_text,"
                " end_date=excluded.end_date, indefinite=excluded.indefinite, number=excluded.number,"
                " github=excluded.github, to_confirm=1",
                (r["user_id"], n["role_id"], n["department_id"], n["department_text"], n["end_date"], n["indefinite"],
                 n["number"], n["github"]))
            con.executemany("INSERT INTO user_supervisors (user_id, supervisor_id, external_name) VALUES (?,?,?)",
                            [(r["user_id"], sid, name) for sid, name in r["sups"]])
    for r in rows:
        r.pop("new", None)
        r.pop("sups", None)
    return {"rows": rows, "errors": errors, "applied": bool(apply and not errors),
            "changed": sum(1 for r in rows if r.get("status") in ("new", "update"))}


def person_name(con, user_id):
    row = con.execute("SELECT COALESCE(display_name, username) FROM users WHERE id=? AND is_external=0",
                      (user_id,)).fetchone()
    return row[0] if row else None


def students_of(con, user_id):
    """The people this user supervises, with their profiles without the number."""
    return [dict(id=r["user_id"], name=r["name"], profile=profile(con, r["user_id"], with_number=False))
            for r in con.execute(
                "SELECT DISTINCT s.user_id, COALESCE(u.display_name, u.username) AS name FROM user_supervisors s"
                " JOIN users u ON u.id=s.user_id WHERE s.supervisor_id=? AND u.is_active=1 ORDER BY name",
                (user_id,))]


def register(app, connection, require_session, now, pages_dir):
    def con():
        return connection()

    @app.get("/profile")
    def profile_page():
        if not enabled():
            abort(404)
        return send_from_directory(pages_dir, "profile.html")

    def signed_in():
        require_session()
        if not enabled():
            abort(404)

    @app.errorhandler(ProfileError)
    def profile_error(exc):
        return jsonify(error=str(exc)), exc.status

    @app.get("/api/profile")
    def own_profile():
        signed_in()
        c = con()
        return jsonify(profile=profile(c, g.user["id"]), missing=missing(c, g.user["id"]), options=options(c),
                       students=students(c, g.user["id"]))

    @app.put("/api/profile")
    def save_own_profile():
        signed_in()
        c = con()
        save(c, g.user["id"], request.get_json(silent=True), now())
        c.commit()
        return jsonify(profile=profile(c, g.user["id"]), missing=missing(c, g.user["id"]))

    @app.get("/api/profile/github")
    def check_github():
        signed_in()
        name = (request.args.get("name") or "").strip()
        if not GITHUB_NAME.match(name):
            return jsonify(status="invalid")
        return jsonify(status=github_status(name))

    def students(c, user_id):
        return students_of(c, user_id)

    @app.get("/api/users/<int:user_id>/profile")
    def person_profile(user_id):
        """A person's profile: admins see all of it, their supervisors all but the number."""
        if not enabled():
            abort(404)
        c = con()
        admin = bool(g.user["is_admin"])
        supervisor = c.execute("SELECT 1 FROM user_supervisors WHERE user_id=? AND supervisor_id=?",
                               (user_id, g.user["id"])).fetchone() is not None
        if not (admin or supervisor or user_id == g.user["id"]):
            abort(404)
        own = user_id == g.user["id"]
        students = []
        if admin or own:  # the people this person supervises, as their supervisor sees them
            students = [dict(id=s["id"], name=s["name"], profile=s["profile"], projects=projects(c, s["id"], g.user))
                        for s in students_of(c, user_id)]
        out = {"profile": profile(c, user_id, with_number=admin or own), "self": own, "can_edit": admin or own,
               "missing": missing(c, user_id) if admin or own else None,
               "projects": projects(c, user_id, g.user), "students": students}
        if admin:  # what the edit form needs
            out["options"] = options(c)
            out["name"] = person_name(c, user_id)
        return jsonify(out)

    @app.put("/api/users/<int:user_id>/profile")
    def save_person_profile(user_id):
        """Admins edit someone's profile, with the same rules as the person's own form."""
        signed_in()
        if not g.user["is_admin"]:
            abort(403)
        c = con()
        if person_name(c, user_id) is None:
            abort(404)
        save(c, user_id, request.get_json(silent=True), now(), by_admin=True)
        c.commit()
        return jsonify(profile=profile(c, user_id), missing=missing(c, user_id))

    @app.get("/api/profiles")
    def visible_profiles():
        """Profiles for the People Profiles and Supervision tabs: admins everyone, others themselves and the
        people they supervise (without numbers)."""
        signed_in()
        c = con()
        return jsonify(people=visible_people(c, g.user), admin=bool(g.user["is_admin"]))

    @app.post("/api/admin/profiles/import")
    def import_profile_csv():
        """Preview ({"csv": text}) or apply ({"csv": text, "apply": true}) a profile import."""
        signed_in()
        if not g.user["is_admin"]:
            abort(403)
        body = request.get_json(silent=True) or {}
        c = con()
        result = import_profiles(c, body.get("csv"), apply=body.get("apply") is True)
        if result["applied"]:
            c.commit()
        return jsonify(result)

    @app.put("/api/admin/profile-options")
    def save_profile_options():
        signed_in()
        if not g.user["is_admin"]:
            abort(403)
        c = con()
        save_options(c, request.get_json(silent=True))
        c.commit()
        return jsonify(options(c))
