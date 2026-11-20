"""Welcome message: an administrator writes it in the site (/welcome); every person who signs in sees it once, after
the profile and before the page they asked for. Only the mechanism is in this repository: the text is stored in the
site's database, so each installation writes its own.

The message has a version. Saving a changed text keeps it (a fixed typo is not shown again), unless the administrator
asks to show it again to everyone; then the version goes up and everybody sees it once more. An empty text means no
welcome message.
"""
from flask import g, jsonify, request, send_from_directory

import db

MAX_TITLE = 120
MAX_BODY = 10000


class WelcomeError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def message(con):
    """The current welcome message as a dict, or None when there is none (no row or an empty text)."""
    row = con.execute("SELECT title, body, version, updated_at FROM welcome_message WHERE id=1").fetchone()
    return dict(row) if row and row["body"].strip() else None


def needs_welcome(con, user):
    """True when this signed-in person has not seen the current welcome message yet (external users never do,
    like the profile)."""
    if user["is_external"]:
        return False
    msg = message(con)
    if msg is None:
        return False
    seen = con.execute("SELECT version FROM welcome_seen WHERE user_id=?", (user["id"],)).fetchone()
    return seen is None or seen["version"] < msg["version"]


def clip(raw, max_len, label):
    text = raw if isinstance(raw, str) else "" if raw is None else None
    if text is None:
        raise WelcomeError(f"{label} must be text.")
    text = text.replace("\r\n", "\n").strip()
    if len(text) > max_len:
        raise WelcomeError(f"{label} is at most {max_len} characters.")
    return text


def save(con, body, user_id, now):
    """Store the message from an administrator's form; returns the stored message (None when emptied)."""
    title = clip(body.get("title"), MAX_TITLE, "The title")
    text = clip(body.get("body"), MAX_BODY, "The message")
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


def register(app, connection, require_session, now, pages_dir):
    @app.errorhandler(WelcomeError)
    def welcome_error(exc):
        return jsonify(error=str(exc)), exc.status

    @app.get("/welcome")
    def welcome_page():
        return send_from_directory(pages_dir, "welcome.html")

    @app.get("/api/welcome")
    def get_welcome():
        require_session()
        con = connection()
        out = {"message": message(con), "can_edit": bool(g.user["is_admin"])}
        if g.user["is_admin"]:
            out["seen"] = con.execute(
                "SELECT COUNT(*) FROM welcome_seen s JOIN welcome_message m ON m.id=1"
                " WHERE s.version >= m.version").fetchone()[0]
        return jsonify(out)

    @app.post("/api/welcome/seen")
    def welcome_seen():
        require_session()
        con = connection()
        msg = message(con)
        if msg is None:
            return jsonify(ok=True)
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("INSERT INTO welcome_seen (user_id, version, seen_at) VALUES (?, ?, ?)"
                        " ON CONFLICT(user_id) DO UPDATE SET version=MAX(version, excluded.version),"
                        " seen_at=excluded.seen_at", (g.user["id"], msg["version"], now()))
        return jsonify(ok=True)

    @app.put("/api/admin/welcome")
    def save_welcome():
        require_session()
        if not g.user["is_admin"]:
            raise WelcomeError("Administrator access required.", 403)
        return jsonify(message=save(connection(), request.get_json(silent=True) or {}, g.user["id"], now()))
