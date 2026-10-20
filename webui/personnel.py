"""MAVLab pilot qualifications (Teams CSV) and certificate PDFs (Operations GitHub)."""
import csv
import datetime
import io
import json
import os
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from config import env, env_path

LICENSE_RE = re.compile(r"([A-Z]{3}-RP-[A-Za-z0-9]+)", re.I)
DATE_RE = re.compile(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})")
TRUE = {"1", "true", "yes", "on"}


def licenses_dir():
    return env_path("WEB_DATA") / "licenses"


def operations_token():
    return env("OPERATIONS_TOKEN") or None


def operations_repo():
    return env("OPERATIONS_REPO") or "tudelft/Operations"


def operations_ref():
    return env("OPERATIONS_REF") or "EU_OPS"


def operations_pilots_path():
    return (env("OPERATIONS_PILOTS_PATH") or "Outdoor_Flight/Pilots").strip("/")


def parse_day(raw):
    """Teams/CSV dates are day-month-year. Returns YYYY-MM-DD or None."""
    s = (raw or "").strip()
    if not s:
        return None
    m = DATE_RE.search(s)
    if not m:
        return None
    d, mo, y = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if y < 100:
        y += 2000
    try:
        return datetime.date(y, mo, d).isoformat()
    except ValueError:
        try:
            return datetime.date(y, d, mo).isoformat()
        except ValueError:
            return None


def parse_license_cell(raw):
    s = (raw or "").strip()
    if not s:
        return None, None
    m = LICENSE_RE.search(s)
    number = m.group(1).upper() if m else (s.split()[0] if s else None)
    return number, parse_day(s)


def as_bool(raw):
    return str(raw or "").strip().lower() in TRUE


def _norm(s):
    return (s or "").strip()


def user_keys(con, user_id=None):
    """username, display name and aliases, lowercased -> user id. Explicit aliases win."""
    names = {}
    rows = con.execute("SELECT id, username, display_name FROM users" +
                       (" WHERE id=?" if user_id else ""),
                       ((user_id,) if user_id else ()))
    for u in rows:
        names.setdefault(u["username"].casefold(), u["id"])
        if u["display_name"]:
            names.setdefault(u["display_name"].casefold(), u["id"])
    q = "SELECT user_id, alias FROM user_aliases"
    args = ()
    if user_id:
        q += " WHERE user_id=?"
        args = (user_id,)
    for r in con.execute(q, args):
        names[r["alias"].casefold()] = r["user_id"]
    return names


def personnel_match_keys(row):
    keys = []
    for k in ("name", "tud_user", "github_folder"):
        v = (row[k] or "").strip()
        if v:
            keys.append(v.casefold())
    return keys


def linked_user_id(row, names):
    for k in personnel_match_keys(row):
        if k in names:
            return names[k]
    return None


def can_see_quals(con, viewer, subject_id):
    if viewer["is_admin"] or viewer["id"] == subject_id:
        return True
    mine = {r["project_id"] for r in con.execute(
        "SELECT project_id FROM project_members WHERE user_id=?", (viewer["id"],))}
    theirs = {r["project_id"] for r in con.execute(
        "SELECT project_id FROM project_members WHERE user_id=?", (subject_id,))}
    return bool(mine & theirs)


def directory_user_ids(con, viewer):
    """People the viewer may open on the People page (admin: everyone)."""
    if viewer["is_admin"]:
        return {r["id"] for r in con.execute("SELECT id FROM users WHERE is_active=1")}
    mine = {r["project_id"] for r in con.execute(
        "SELECT project_id FROM project_members WHERE user_id=?", (viewer["id"],))}
    ids = {viewer["id"]}
    if mine:
        ids.update(r["user_id"] for r in con.execute(
            f"SELECT DISTINCT user_id FROM project_members WHERE project_id IN ({','.join('?' * len(mine))})",
            tuple(mine)))
    return ids


def expired(day):
    if not day:
        return False
    try:
        return datetime.date.fromisoformat(day) < datetime.date.today()
    except ValueError:
        return False


def qualifications_payload(row, docs, include_docs=True):
    if row is None:
        return None
    out = {
        "id": row["id"],
        "name": row["name"],
        "tud_user": row["tud_user"],
        "kind": row["kind"],
        "fi": bool(row["fi"]),
        "ctr": bool(row["ctr"]),
        "a1a3": row["a1a3"],
        "a1a3_until": row["a1a3_until"],
        "a1a3_expired": expired(row["a1a3_until"]),
        "a2": row["a2"],
        "a2_until": row["a2_until"],
        "a2_expired": expired(row["a2_until"]),
        "cyberzoo": row["cyberzoo"],
        "erp": row["erp"],
    }
    if include_docs:
        out["docs"] = [dict(id=d["id"], title=d["title"], filename=d["filename"],
                            license=d["license"], validity=d["validity"],
                            expired=expired(d["validity"]))
                       for d in docs]
    return out


def personnel_for_user(con, user_id):
    names = user_keys(con, user_id)
    if not names:
        return None, []
    matches = [row for row in con.execute("SELECT * FROM personnel ORDER BY name")
               if linked_user_id(row, names) == user_id]
    if not matches:
        return None, []
    primary = next((r for r in matches if r["a1a3"] or r["a2"] or r["tud_user"]), matches[0])
    ids = [r["id"] for r in matches]
    docs = con.execute(
        f"SELECT * FROM personnel_docs WHERE personnel_id IN ({','.join('?' * len(ids))}) ORDER BY title",
        ids).fetchall()
    return primary, docs


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def username_from_name(name):
    slug = re.sub(r"[^A-Za-z0-9]+", "", name or "")
    return (slug[:40] or "pilot")


def unique_username(con, base):
    name = (base or "pilot")[:40] or "pilot"
    n = 2
    while con.execute("SELECT 1 FROM users WHERE username=?", (name,)).fetchone():
        suffix = str(n)
        name = (base[:40 - len(suffix)] + suffix)
        n += 1
    return name


def add_alias(con, user_id, alias):
    alias = (alias or "").strip()
    if not alias:
        return False
    try:
        con.execute("INSERT INTO user_aliases (user_id, alias) VALUES (?,?)", (user_id, alias))
        return True
    except sqlite3.IntegrityError:
        return False


def has_remote_pilot_licence(row):
    return bool((row["a1a3"] or "").strip() or (row["a2"] or "").strip())


def looks_like_remote_pilot_pdf(filename):
    u = (filename or "").upper().replace("-", "_").replace(" ", "_")
    return any(tok in u for tok in ("A1", "A2", "A3", "STS", "_RP_", "-RP-"))


def ensure_users_for_personnel(con):
    """Create a sign-in account for every remote pilot on the licence list who does not already match a user.

    Names from Teams and GitHub are stored as aliases, so a later TU Delft login can attach to this
    account instead of creating a second one. Existing users (matched by username, display name or
    alias) are reused. People with no A1/A3 or A2 are skipped."""
    created = 0
    for row in con.execute("SELECT * FROM personnel ORDER BY name"):
        if not has_remote_pilot_licence(row):
            continue
        uid = linked_user_id(row, user_keys(con))
        if uid is None:
            username = unique_username(con, username_from_name(row["name"]))
            cur = con.execute(
                "INSERT INTO users (username, display_name, email, password_hash, is_admin, provider, created_at)"
                " VALUES (?,?,NULL,NULL,0,'local',?)",
                (username, row["name"], _utc_now()))
            uid = cur.lastrowid
            created += 1
        user = con.execute("SELECT username, display_name FROM users WHERE id=?", (uid,)).fetchone()
        skip = {user["username"].casefold()}
        if user["display_name"]:
            skip.add(user["display_name"].casefold())
        for extra in (row["name"], row["tud_user"], row["github_folder"]):
            if extra and extra.casefold() not in skip:
                add_alias(con, uid, extra)
                skip.add(extra.casefold())
    return created


_SKIP_LOG_NAMES = {"unknown", "n/a", "na", "none", "tbd", "-", "--", "?", "??"}


def looks_like_person_name(name):
    name = (name or "").strip()
    if len(name) < 2 or name.casefold() in _SKIP_LOG_NAMES:
        return False
    return any(c.isalpha() for c in name)


def ensure_users_for_log_names(con):
    """Create a pending account for every PIC/PAC/GCS name in the logs that is not linked yet."""
    keys = user_keys(con)
    created = 0
    seen = set()
    for a in con.execute("SELECT pic, pac, gcs FROM aircraft"):
        for role in ("pic", "pac", "gcs"):
            name = (a[role] or "").strip()
            if not looks_like_person_name(name):
                continue
            fold = name.casefold()
            if fold in seen or fold in keys:
                continue
            seen.add(fold)
            username = unique_username(con, username_from_name(name))
            cur = con.execute(
                "INSERT INTO users (username, display_name, email, password_hash, is_admin, provider, created_at)"
                " VALUES (?,?,NULL,NULL,0,'log',?)",
                (username, name[:80], _utc_now()))
            uid = cur.lastrowid
            add_alias(con, uid, name)
            keys[fold] = uid
            keys[username.casefold()] = uid
            created += 1
    return created


def unlinked_personnel(con):
    names = user_keys(con)
    out = []
    for row in con.execute("SELECT * FROM personnel ORDER BY name"):
        if linked_user_id(row, names) is None:
            extras = [row["tud_user"]] if row["tud_user"] and row["tud_user"].casefold() != row["name"].casefold() else []
            out.append({"name": row["name"], "tud_user": row["tud_user"], "kind": row["kind"],
                        "also": extras, "id": row["id"]})
    return out


def _colmap(header):
    aliases = {
        "name": ("name", "column1", "person", "pilot"),
        "kind": ("type", "status", "kind"),
        "tud_user": ("tud user", "user", "tud_user"),
        "fi": ("fi",),
        "ctr": ("ctr",),
        "a1a3": ("a1/a3", "a1a3", "a1-a3"),
        "a2": ("a2",),
        "cyberzoo": ("cyberzoo quiz", "cyberzoo"),
        "erp": ("erp",),
    }
    folded = {((h or "").strip().casefold()): i for i, h in enumerate(header)}
    out = {}
    for key, opts in aliases.items():
        for o in opts:
            if o in folded:
                out[key] = folded[o]
                break
    return out


def import_personnel_csv(con, text):
    """Replace Teams fields from a Personnel CSV. Certificate files are left as they are."""
    sample = text.lstrip("\ufeff")
    reader = csv.reader(io.StringIO(sample))
    try:
        header = next(reader)
    except StopIteration:
        return {"created": 0, "updated": 0, "skipped": 0}
    cols = _colmap(header)
    if "name" not in cols:
        raise ValueError("CSV needs a Name column")
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    created = updated = skipped = 0
    seen = set()
    for rec in reader:
        if not rec or all(not (c or "").strip() for c in rec):
            continue

        def cell(key):
            i = cols.get(key)
            return rec[i].strip() if i is not None and i < len(rec) else ""

        name = cell("name")
        if not name or name.casefold() in ("name", "column1"):
            skipped += 1
            continue
        a1, a1_until = parse_license_cell(cell("a1a3"))
        a2, a2_until = parse_license_cell(cell("a2"))
        fields = {
            "name": name[:80],
            "tud_user": (cell("tud_user") or None),
            "kind": (cell("kind") or None),
            "fi": int(as_bool(cell("fi"))),
            "ctr": int(as_bool(cell("ctr"))),
            "a1a3": a1,
            "a1a3_until": a1_until,
            "a2": a2,
            "a2_until": a2_until,
            "cyberzoo": parse_day(cell("cyberzoo")),
            "erp": parse_day(cell("erp")),
            "updated_at": now,
        }
        if fields["tud_user"]:
            fields["tud_user"] = fields["tud_user"][:80]
        if fields["kind"]:
            fields["kind"] = fields["kind"][:40]
        seen.add(name.casefold())
        row = con.execute("SELECT id FROM personnel WHERE name=?", (name,)).fetchone()
        if row:
            con.execute(
                "UPDATE personnel SET tud_user=?, kind=?, fi=?, ctr=?, a1a3=?, a1a3_until=?, a2=?, a2_until=?,"
                " cyberzoo=?, erp=?, updated_at=? WHERE id=?",
                (fields["tud_user"], fields["kind"], fields["fi"], fields["ctr"], fields["a1a3"],
                 fields["a1a3_until"], fields["a2"], fields["a2_until"], fields["cyberzoo"], fields["erp"],
                 now, row["id"]))
            updated += 1
        else:
            con.execute(
                "INSERT INTO personnel (name, tud_user, kind, fi, ctr, a1a3, a1a3_until, a2, a2_until,"
                " cyberzoo, erp, github_folder, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (fields["name"], fields["tud_user"], fields["kind"], fields["fi"], fields["ctr"],
                 fields["a1a3"], fields["a1a3_until"], fields["a2"], fields["a2_until"],
                 fields["cyberzoo"], fields["erp"], None, now))
            created += 1
    users_created = ensure_users_for_personnel(con)
    return {"created": created, "updated": updated, "skipped": skipped, "users_created": users_created}


def _github(url, raw=False, timeout=30):
    token = operations_token()
    if not token:
        raise RuntimeError("LOGDB_OPERATIONS_TOKEN is not set")
    headers = {
        "Authorization": "Bearer " + token,
        "User-Agent": "flight-log-database",
        "X-GitHub-Api-Version": "2022-11-28",
        "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            return data if raw else json.loads(data.decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"GitHub {e.code} for {url}: {body}") from e


def github_contents(path=""):
    repo = operations_repo()
    ref = operations_ref()
    base = operations_pilots_path()
    rel = "/".join(p for p in (base, path) if p)
    url = f"https://api.github.com/repos/{repo}/contents/{urllib.parse.quote(rel, safe='/')}?ref={urllib.parse.quote(ref)}"
    return _github(url, raw=False)


def github_file_bytes(path):
    repo = operations_repo()
    ref = operations_ref()
    url = (f"https://api.github.com/repos/{repo}/contents/{urllib.parse.quote(path, safe='/')}"
           f"?ref={urllib.parse.quote(ref)}")
    return _github(url, raw=True, timeout=60)


def sync_github_docs(con):
    """Index PDF certificates from the Operations pilots folder. Does not download the files yet."""
    listing = github_contents("")
    if not isinstance(listing, list):
        raise RuntimeError("GitHub pilots path is not a folder")
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    people = 0
    docs = 0
    for entry in listing:
        if entry.get("type") != "dir" or entry.get("name") in ("archive", "internal_qualifications"):
            continue
        folder = entry["name"]
        files = github_contents(folder)
        if not isinstance(files, list):
            continue
        pdfs = [f for f in files if f.get("type") == "file" and (f.get("name") or "").lower().endswith(".pdf")]
        row = con.execute("SELECT id FROM personnel WHERE name=? OR github_folder=?",
                          (folder, folder)).fetchone()
        if row is None and not any(looks_like_remote_pilot_pdf(f.get("name")) for f in pdfs):
            continue
        people += 1
        if row:
            pid = row["id"]
            con.execute("UPDATE personnel SET github_folder=?, updated_at=? WHERE id=?",
                        (folder, now, pid))
        else:
            cur = con.execute(
                "INSERT INTO personnel (name, github_folder, fi, ctr, updated_at) VALUES (?,?,0,0,?)",
                (folder, folder, now))
            pid = cur.lastrowid
        keep = []
        for f in files:
            if f.get("type") != "file" or not (f.get("name") or "").lower().endswith(".pdf"):
                continue
            title = Path(f["name"]).stem
            gh_path = f.get("path") or f"{operations_pilots_path()}/{folder}/{f['name']}"
            keep.append(gh_path)
            existing = con.execute("SELECT id FROM personnel_docs WHERE github_path=?", (gh_path,)).fetchone()
            if existing:
                con.execute(
                    "UPDATE personnel_docs SET personnel_id=?, title=?, filename=? WHERE id=?",
                    (pid, title[:120], f["name"][:120], existing["id"]))
            else:
                con.execute(
                    "INSERT INTO personnel_docs (personnel_id, title, filename, license, validity, github_path)"
                    " VALUES (?,?,?,?,?,?)",
                    (pid, title[:120], f["name"][:120], None, None, gh_path))
            docs += 1
        if keep:
            con.execute(
                f"DELETE FROM personnel_docs WHERE personnel_id=? AND github_path NOT IN ({','.join('?' * len(keep))})",
                (pid, *keep))
        else:
            con.execute("DELETE FROM personnel_docs WHERE personnel_id=?", (pid,))
    users_created = ensure_users_for_personnel(con)
    return {"people": people, "docs": docs, "users_created": users_created}


def cached_pdf_path(doc_id):
    return licenses_dir() / str(doc_id) / "file.pdf"


def load_pdf(con, doc_id):
    """Return (bytes, filename) for a certificate, fetching from GitHub once and caching locally."""
    row = con.execute("SELECT * FROM personnel_docs WHERE id=?", (doc_id,)).fetchone()
    if row is None:
        return None
    dest = cached_pdf_path(doc_id)
    if dest.is_file() and dest.stat().st_size > 0:
        return dest.read_bytes(), row["filename"]
    if not row["github_path"]:
        raise RuntimeError("no GitHub path for this certificate")
    data = github_file_bytes(row["github_path"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    con.execute("UPDATE personnel_docs SET cached_path=?, size_bytes=? WHERE id=?",
                (str(dest), len(data), doc_id))
    con.commit()
    return data, row["filename"]
