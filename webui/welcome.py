"""Welcome message: an administrator writes it in the site (/welcome); everyone sees it at the top of their Home page
until they dismiss it. Pages are never redirected for it. Only the mechanism is in this repository: the text is stored
in the site's database, so each installation writes its own.

Admins also write notices: a title and text like the welcome message, for everyone or only for some profile roles,
optionally shown until a date (inclusive), and optionally not closable (no Dismiss: it stays until its date or until
an admin deletes it). Changing a notice's title or text raises its version, so it shows again to who dismissed it.

Home shows one list of messages (home_messages): notices that cannot be closed first, then the welcome message, then
the other notices, newest first. Dismissing is per message id ("welcome", "notice-<id>") and version.

The message has a version. Saving a changed text keeps it (a fixed typo is not shown again), unless the administrator
asks to show it again to everyone; then the version goes up and everybody sees it once more. An empty text means no
welcome message.

The text uses the same syntax as a training's access mail: tags such as {name} and {role} filled in per person,
blocks per profile role ({if role: A, B} ... {elif role: C} ... {else} ... {end}, each on its own line) and a little
markup (**bold**, lines starting with "- " as bullets, two leading spaces indent, https:// addresses as links);
site paths such as /training also become links here.
"""
import datetime
import html
import re

from flask import g, jsonify, redirect, request, send_from_directory

import db
import mail
import training

MAX_TITLE = 120
MAX_BODY = 10000
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class WelcomeError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def message(con):
    """The current welcome message as a dict, or None when there is none (no row or an empty text)."""
    row = con.execute("SELECT title, body, version, updated_at FROM welcome_message WHERE id=1").fetchone()
    return dict(row) if row and row["body"].strip() else None


TAGS = ("{name}", "{email}", "{username}", "{department}", "{role}", "{supervisor}", "{end_date}")
SITE_PATH = re.compile(r"(?<![\w/:.])/[a-z][\w\-/]*(?:\?[^\s<>\"]*[^\s<>\".,;:!?)\]'])?", re.I)
LINK = re.compile(mail.URL.pattern + "|" + SITE_PATH.pattern, re.I)
TRAILING = ".,;:!?)]'"


def check(text):
    """Raise WelcomeError when the role blocks do not pair up."""
    try:
        training.role_blocks(text, "the welcome message")
    except training.TrainingError as exc:
        raise WelcomeError(str(exc)) from None


def fill(con, text, user_id):
    """The text for one person: the part of each role block that matches their profile role, then the tags."""
    person = training.person_details(con, user_id) or {}
    values = {tag: str(person.get(tag[1:-1]) or "-") for tag in TAGS}
    return training.fill_access(text, {**{k: k for k in training.PLACEHOLDERS}, **values})  # other tags stay as typed


def inline(text):
    def link(m):
        url = m[0].rstrip(TRAILING)
        rest = m[0][len(url):]
        attrs = ' target="_blank" rel="noopener"' if url.startswith("http") else ""
        return f'<a href="{html.escape(url, quote=True)}"{attrs}>{html.escape(url)}</a>{html.escape(rest)}'
    out, pos = [], 0
    for m in LINK.finditer(text):
        out.append(html.escape(text[pos:m.start()], quote=False))
        out.append(link(m))
        pos = m.end()
    out.append(html.escape(text[pos:], quote=False))
    return mail.BOLD.sub(r"<strong>\1</strong>", "".join(out))


def render(text):
    """The filled-in text as HTML, with the access mail's markup; everything typed is escaped."""
    out = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        block = block.strip("\n").rstrip()
        if not block.strip():
            continue
        run, bullets = [], []

        def flush():
            if run:
                out.append("<p>" + "<br>".join(run) + "</p>")
                run.clear()
            if bullets:
                out.append("<ul>" + "".join(f"<li>{b}</li>" for b in bullets) + "</ul>")
                bullets.clear()

        for line in block.split("\n"):
            if line.lstrip().startswith("- "):
                if run:
                    flush()
                bullets.append(inline(line.lstrip()[2:].strip()))
            elif line.startswith(("  ", "\t")) and line.strip():
                flush()
                out.append(f'<div class="indent">{inline(line.strip())}</div>')
            else:
                if bullets:
                    flush()
                run.append(inline(line.strip()))
        flush()
    return "".join(out)


def needs_welcome(con, user):
    """True when this person has not dismissed the current welcome message yet."""
    msg = message(con)
    if msg is None:
        return False
    seen = con.execute("SELECT version FROM welcome_seen WHERE user_id=?", (user["id"],)).fetchone()
    return seen is None or seen["version"] < msg["version"]


def today():
    return datetime.date.today().isoformat()


def person_role(con, user_id):
    """The person's profile role id (None without one, or when person profiles are off)."""
    import profiles
    if not profiles.enabled():
        return None
    row = con.execute("SELECT role_id FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
    return row["role_id"] if row else None


def notices_for(con, user_id):
    """The notices this person is meant to see today (audience and until date), newest first, with the version
    they dismissed (None when not)."""
    role = person_role(con, user_id)
    return con.execute(
        "SELECT n.*, d.version AS dismissed FROM notices n"
        " LEFT JOIN notice_dismissals d ON d.notice_id=n.id AND d.user_id=?"
        " WHERE (n.until IS NULL OR n.until >= ?)"
        " AND (n.audience='all' OR EXISTS (SELECT 1 FROM notice_roles r WHERE r.notice_id=n.id AND r.role_id=?))"
        " ORDER BY COALESCE(n.updated_at, n.created_at) DESC, n.id DESC", (user_id, today(), role)).fetchall()


def home_messages(con, user):
    """The messages at the top of the person's Home page: dicts with id, kind, version, title, html (built and
    escaped here, filled in for this person), dismissible and until. Notices that cannot be closed come first, then
    the welcome message, then the other notices (newest first); dismissed ones are left out."""
    def item(mid, kind, version, title, body, dismissible, until=None):
        return dict(id=mid, kind=kind, version=version, title=title, html=render(fill(con, body, user["id"])),
                    dismissible=dismissible, until=until)
    fixed, closable = [], []
    for n in notices_for(con, user["id"]):
        if n["closable"] and n["dismissed"] is not None and n["dismissed"] >= n["version"]:
            continue
        (closable if n["closable"] else fixed).append(
            item(f"notice-{n['id']}", "notice", n["version"], n["title"], n["body"], bool(n["closable"]), n["until"]))
    msg = message(con)
    welcome = [item("welcome", "welcome", msg["version"], msg["title"] or "Welcome", msg["body"], True)] \
        if msg and needs_welcome(con, user) else []
    return fixed + welcome + closable


def dismiss(con, user_id, message_id, version, now):
    """Record that the person dismissed this message up to this version (never beyond the current one)."""
    if message_id.startswith("notice-") and message_id[7:].isdigit():
        n = next((r for r in notices_for(con, user_id) if r["id"] == int(message_id[7:])), None)
        if n is None:
            raise WelcomeError("Unknown message.", 404)
        if not n["closable"]:
            raise WelcomeError("This notice cannot be closed.", 409)
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("INSERT INTO notice_dismissals (notice_id, user_id, version, dismissed_at) VALUES (?, ?, ?, ?)"
                        " ON CONFLICT(notice_id, user_id) DO UPDATE SET version=MAX(version, excluded.version),"
                        " dismissed_at=excluded.dismissed_at", (n["id"], user_id, min(version, n["version"]), now))
        return
    if message_id != "welcome":
        raise WelcomeError("Unknown message.", 404)
    msg = message(con)
    if msg is None:
        return
    version = min(version, msg["version"])
    with db.write_lock, con:
        con.execute("BEGIN IMMEDIATE")
        con.execute("INSERT INTO welcome_seen (user_id, version, seen_at) VALUES (?, ?, ?)"
                    " ON CONFLICT(user_id) DO UPDATE SET version=MAX(version, excluded.version),"
                    " seen_at=excluded.seen_at", (user_id, version, now))


def clip(raw, max_len, label):
    text = raw if isinstance(raw, str) else "" if raw is None else None
    if text is None:
        raise WelcomeError(f"{label} must be text.")
    text = text.replace("\r\n", "\n").strip("\n").rstrip()
    if len(text) > max_len:
        raise WelcomeError(f"{label} is at most {max_len} characters.")
    return text


def save(con, body, user_id, now):
    """Store the message from an administrator's form; returns the stored message (None when emptied)."""
    title = " ".join(clip(body.get("title"), MAX_TITLE, "The title").split())
    text = clip(body.get("body"), MAX_BODY, "The message")
    check(text)
    again = body.get("show_again", False)
    if type(again) is not bool:
        raise WelcomeError("show_again must be true or false.")
    with db.write_lock, con:
        con.execute("BEGIN IMMEDIATE")
        old = con.execute("SELECT body, version FROM welcome_message WHERE id=1").fetchone()
        version = old["version"] if old else 0
        # a first message, or one written after it was emptied, is new to everybody
        if text and (again or not old or not old["body"].strip()):
            version += 1
        con.execute("INSERT INTO welcome_message (id, title, body, version, updated_at, updated_by)"
                    " VALUES (1, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,"
                    " body=excluded.body, version=excluded.version, updated_at=excluded.updated_at,"
                    " updated_by=excluded.updated_by", (title, text, version, now, user_id))
    return message(con)


def notice_fields(con, body):
    """(title, text, audience, role ids, until, closable) from an admin's form, checked."""
    if not isinstance(body, dict):
        raise WelcomeError("Send a JSON object.")
    title = " ".join(clip(body.get("title"), MAX_TITLE, "The title").split())
    text = clip(body.get("body"), MAX_BODY, "The text")
    if not title or not text:
        raise WelcomeError("Give the notice a title and a text.")
    check(text)
    audience = body.get("audience", "all")
    if audience not in ("all", "roles"):
        raise WelcomeError("audience must be all or roles.")
    role_ids = body.get("role_ids") or []
    if not isinstance(role_ids, list) or any(type(r) is not int for r in role_ids):
        raise WelcomeError("role_ids must be a list of role ids.")
    role_ids = sorted(set(role_ids)) if audience == "roles" else []
    if audience == "roles":
        known = {r[0] for r in con.execute("SELECT id FROM profile_roles")}
        if not role_ids or not set(role_ids) <= known:
            raise WelcomeError("Choose one or more roles from the list.")
    until = body.get("until") or None
    if until is not None:
        try:
            if not isinstance(until, str) or not DATE.match(until):
                raise ValueError
            datetime.date.fromisoformat(until)
        except ValueError:
            raise WelcomeError("Show until must be a date (YYYY-MM-DD) or empty.") from None
    closable = body.get("closable", True)
    if type(closable) is not bool:
        raise WelcomeError("closable must be true or false.")
    return title, text, audience, role_ids, until, closable


def notice_json(con, row):
    """A notice for the admins' list, with its roles and how many active accounts dismissed this version."""
    roles = con.execute("SELECT r.id, r.name FROM notice_roles nr JOIN profile_roles r ON r.id=nr.role_id"
                        " WHERE nr.notice_id=? ORDER BY r.sort_order, r.name", (row["id"],)).fetchall()
    dismissed = con.execute(
        "SELECT COUNT(*) FROM notice_dismissals d JOIN users u ON u.id=d.user_id AND u.is_active=1"
        " WHERE d.notice_id=? AND d.version >= ?", (row["id"], row["version"])).fetchone()[0]
    names = {r[0]: r[1] for r in con.execute("SELECT id, COALESCE(display_name, username) FROM users WHERE id IN (?, ?)",
                                             (row["created_by"] or 0, row["updated_by"] or 0))}
    return dict(id=row["id"], title=row["title"], body=row["body"], version=row["version"], audience=row["audience"],
                role_ids=[r["id"] for r in roles], roles=[r["name"] for r in roles], until=row["until"],
                closable=bool(row["closable"]), expired=bool(row["until"] and row["until"] < today()),
                dismissed=dismissed, created_at=row["created_at"], created_by=names.get(row["created_by"]),
                updated_at=row["updated_at"], updated_by=names.get(row["updated_by"]))


def register(app, connection, require_session, now, pages_dir):
    @app.errorhandler(WelcomeError)
    def welcome_error(exc):
        return jsonify(error=str(exc)), exc.status

    @app.get("/welcome")
    def welcome_page():
        if not g.user["is_admin"]:  # the message itself is on the Home page; this page is its editor
            return redirect("/home")
        return send_from_directory(pages_dir, "welcome.html")

    @app.get("/api/welcome")
    def get_welcome():
        require_session()
        con = connection()
        msg = message(con)
        out = {"message": None, "can_edit": bool(g.user["is_admin"])}
        if msg:
            out["message"] = {"title": msg["title"], "version": msg["version"], "updated_at": msg["updated_at"],
                              "html": render(fill(con, msg["body"], g.user["id"]))}
        if g.user["is_admin"]:
            out["text"] = msg["body"] if msg else ""
            out["tags"] = TAGS
            out["seen"] = con.execute(
                "SELECT COUNT(*) FROM welcome_seen s JOIN welcome_message m ON m.id=1"
                " JOIN users u ON u.id=s.user_id AND u.is_active=1 WHERE s.version >= m.version").fetchone()[0]
        return jsonify(out)

    @app.post("/api/home/messages/dismiss")
    def dismiss_home_message():
        """Dismiss one Home message ({id, version}); blocked while viewing as someone (app.VIEW_AS_BLOCKED)."""
        require_session()
        body = request.get_json(silent=True) or {}
        if not isinstance(body.get("id"), str) or type(body.get("version")) is not int or body["version"] < 1:
            raise WelcomeError("Send the message's id and version.")
        dismiss(connection(), g.user["id"], body["id"], body["version"], now())
        return jsonify(ok=True)

    def admin():
        require_session()
        if not g.user["is_admin"]:
            raise WelcomeError("Administrator access required.", 403)
        return connection()

    @app.get("/api/admin/notices")
    def list_notices():
        con = admin()
        rows = con.execute("SELECT * FROM notices ORDER BY COALESCE(updated_at, created_at) DESC, id DESC").fetchall()
        import profiles
        roles = [dict(id=r["id"], name=r["name"]) for r in con.execute(
            "SELECT id, name FROM profile_roles ORDER BY sort_order, name")]
        return jsonify(notices=[notice_json(con, r) for r in rows], roles=roles, profiles=profiles.enabled())

    @app.post("/api/admin/notices")
    def add_notice():
        con = admin()
        title, text, audience, role_ids, until, closable = notice_fields(con, request.get_json(silent=True))
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            nid = con.execute("INSERT INTO notices (title, body, audience, until, closable, created_at, created_by)"
                              " VALUES (?, ?, ?, ?, ?, ?, ?)",
                              (title, text, audience, until, int(closable), now(), g.user["id"])).lastrowid
            con.executemany("INSERT INTO notice_roles (notice_id, role_id) VALUES (?, ?)", [(nid, r) for r in role_ids])
        return jsonify(notice=notice_json(con, con.execute("SELECT * FROM notices WHERE id=?", (nid,)).fetchone())), 201

    @app.put("/api/admin/notices/<int:notice_id>")
    def edit_notice(notice_id):
        con = admin()
        title, text, audience, role_ids, until, closable = notice_fields(con, request.get_json(silent=True))
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            old = con.execute("SELECT title, body FROM notices WHERE id=?", (notice_id,)).fetchone()
            if old is None:
                raise WelcomeError("No such notice.", 404)
            changed = int((old["title"], old["body"]) != (title, text))  # a new text shows again to everyone
            con.execute("UPDATE notices SET title=?, body=?, audience=?, until=?, closable=?, version=version+?,"
                        " updated_at=?, updated_by=? WHERE id=?",
                        (title, text, audience, until, int(closable), changed, now(), g.user["id"], notice_id))
            con.execute("DELETE FROM notice_roles WHERE notice_id=?", (notice_id,))
            con.executemany("INSERT INTO notice_roles (notice_id, role_id) VALUES (?, ?)",
                            [(notice_id, r) for r in role_ids])
        return jsonify(notice=notice_json(con, con.execute("SELECT * FROM notices WHERE id=?",
                                                           (notice_id,)).fetchone()))

    @app.delete("/api/admin/notices/<int:notice_id>")
    def delete_notice(notice_id):
        con = admin()
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            if not con.execute("DELETE FROM notices WHERE id=?", (notice_id,)).rowcount:
                raise WelcomeError("No such notice.", 404)
        return jsonify(ok=True)

    @app.put("/api/admin/welcome")
    def save_welcome():
        require_session()
        if not g.user["is_admin"]:
            raise WelcomeError("Administrator access required.", 403)
        return jsonify(message=save(connection(), request.get_json(silent=True) or {}, g.user["id"], now()))
