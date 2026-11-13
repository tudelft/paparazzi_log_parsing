"""Email notifications. Off unless LOGDB_SMTP_HOST is set.

Mails are queued in `mail_outbox` inside the transaction that causes them (so a rolled-back change sends nothing)
and a background thread sends them. Each person chooses per kind in My account whether a mail comes at once, in one
daily summary, or not at all; account mails (sign-in links) always come at once. The address is read when the mail
is sent, so a changed address is used, and people without an address or with a disabled account get nothing.

Expiry reminders are checked once a day: 30 and 7 days before a current approved qualification ends (unless a newer
one of the same type already covers the time after it), and before the expected end date of a person profile.
"""
import datetime
import html
import re
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import db
from config import env, env_int

# kind: (label, help, modes offered, default mode, administrators only)
KINDS = {
    "account": ("Account", "Links to set or reset your password.", ("immediate",), "immediate", False),
    "expiry": ("Expiry reminders", "30 and 7 days before a qualification or your expected end date expires.",
               ("immediate", "daily", "off"), "immediate", False),
    "bookings": ("Bookings", "When you book, when someone else changes or removes your booking, and when a new "
                 "booking overlaps one of yours.", ("immediate", "daily", "off"), "immediate", False),
    "expiry_all": ("Others' expiry reminders", "A copy of everyone's expiry reminders (30 and 7 days before).",
                   ("off", "immediate", "daily"), "off", True),
    "admin": ("Administrator notices", "Qualifications waiting for approval and logs that failed to parse.",
              ("immediate", "daily", "off"), "immediate", True),
    "access_copy": ("Training access requests", "A copy of the access request mails sent after a training pass.",
                    ("off", "immediate", "daily"), "off", True),
}
MODE_LABELS = {"immediate": "Immediately", "daily": "Daily summary", "off": "Off"}
REMIND_DAYS = (30, 7)
MAX_ATTEMPTS = 5
POLL_SECONDS = 15
_wake = threading.Event()
_started = False


def enabled():
    return bool(env("SMTP_HOST"))


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def link(path):
    """Full address of a page when LOGDB_PUBLIC_URL is set, else the path."""
    base = (env("PUBLIC_URL") or "").rstrip("/")
    return base + path


def shared_admin_box():
    return env("MAIL_ADMIN_TO") or None


def kinds_for(user):
    """Kinds this person can choose; admin notices only when they do not go to the shared mailbox."""
    return [k for k, v in KINDS.items()
            if not v[4] or (user["is_admin"] and not (k == "admin" and shared_admin_box()))]


def preferences(con, user):
    chosen = {r["kind"]: r["mode"] for r in con.execute("SELECT kind, mode FROM mail_prefs WHERE user_id=?", (user["id"],))}
    return [{"kind": k, "label": KINDS[k][0], "help": KINDS[k][1], "modes": list(KINDS[k][2]),
             "mode": chosen.get(k) if chosen.get(k) in KINDS[k][2] else KINDS[k][3]} for k in kinds_for(user)]


def set_preferences(con, user, choices):
    """choices: {kind: mode}. Unknown kinds or modes raise ValueError."""
    allowed = kinds_for(user)
    for kind, mode in choices.items():
        if kind not in allowed or mode not in KINDS[kind][2]:
            raise ValueError(f"cannot set {kind} to {mode}")
    for kind, mode in choices.items():
        con.execute("INSERT INTO mail_prefs(user_id, kind, mode) VALUES (?,?,?)"
                    " ON CONFLICT(user_id, kind) DO UPDATE SET mode=excluded.mode", (user["id"], kind, mode))


def mode_for(con, user_id, kind):
    row = con.execute("SELECT mode FROM mail_prefs WHERE user_id=? AND kind=?", (user_id, kind)).fetchone()
    return row["mode"] if row and row["mode"] in KINDS[kind][2] else KINDS[kind][3]


def notify(con, user_id, kind, subject, text, path=None, key=None):
    """Queue a mail to one person in the caller's transaction. key makes it once-only (e.g. a reminder).
    Returns True when queued."""
    if not enabled() or user_id is None:
        return False
    user = con.execute("SELECT id, email, is_active, is_external, is_admin FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None or not user["email"] or not user["is_active"] or user["is_external"]:
        return False
    if KINDS[kind][4] and not user["is_admin"]:
        return False
    mode = mode_for(con, user_id, kind)
    if mode == "off":
        return False
    if key and con.execute("INSERT OR IGNORE INTO mail_sent_keys(key, created_at) VALUES (?,?)",
                           (f"{user_id}:{key}", now())).rowcount == 0:
        return False
    body = text + (f"\n\n{link(path)}" if path else "")
    con.execute("INSERT INTO mail_outbox(user_id, kind, subject, body, digest, created_at) VALUES (?,?,?,?,?,?)",
                (user_id, kind, subject, body, int(mode == "daily"), now()))
    _wake.set()
    return True


def notify_admins(con, subject, text, path=None, key=None, skip=None):
    """To the shared mailbox (LOGDB_MAIL_ADMIN_TO) when set, else to each administrator as they chose."""
    if shared_admin_box():
        if not enabled() or (key and con.execute("INSERT OR IGNORE INTO mail_sent_keys(key, created_at) VALUES (?,?)",
                                                 (f"shared:{key}", now())).rowcount == 0):
            return 0
        con.execute("INSERT INTO mail_outbox(user_id, to_addr, kind, subject, body, created_at) VALUES (NULL,?,?,?,?,?)",
                    (shared_admin_box(), "admin", subject, text + (f"\n\n{link(path)}" if path else ""), now()))
        _wake.set()
        return 1
    return sum(notify(con, r["id"], "admin", subject, text, path, key)
               for r in con.execute("SELECT id FROM users WHERE is_admin=1 AND is_active=1") if r["id"] != skip)


def queue_access(con, to_addr, subject, body, cc_addr=None, reply_to=None):
    """Queue a training access request to fixed addresses (not an account), and a copy to administrators who
    asked for one ("access_copy"). Returns True when queued."""
    if not enabled() or not to_addr:
        return False
    con.execute("INSERT INTO mail_outbox(user_id, to_addr, cc_addr, reply_to, kind, subject, body, created_at)"
                " VALUES (NULL,?,?,?,'access',?,?,?)", (to_addr, cc_addr, reply_to, subject, body, now()))
    for admin in con.execute("SELECT id FROM users WHERE is_admin=1 AND is_active=1").fetchall():
        notify(con, admin["id"], "access_copy", subject, f"Sent to {to_addr}" + (f", copy to {cc_addr}" if cc_addr else "")
               + f":\n\n{body}")
    _wake.set()
    return True


# --- composing -------------------------------------------------------------------------------------------------------
def signature():
    """LOGDB_MAIL_FOOTER, with \\n for line breaks; the sender name when unset."""
    return (env("MAIL_FOOTER") or env("MAIL_FROM_NAME")).replace("\\n", "\n").strip()


ACCESS_NOTE = "Access request, sent by the flight logbook after a training was passed."


def footer(shared=False):
    end = (ACCESS_NOTE if shared == "access" else "Administrator notice, sent to the shared mailbox." if shared
           else "Change which emails you get: " + link("/account?tab=email"))
    return f"\n\n-- \n{signature()}\n{end}"


URL = re.compile(r"https?://[^\s<>\"]+")


def button_label(url):
    return "Set your password" if "/setup/" in url else "Open in the flight logbook"


def html_body(name, body, shared):
    """The same mail as simple HTML with inline styles (what mail programs support): a header with the sender name,
    the text, a button for a link on its own line and a small footer with the email settings link."""
    paragraphs = []
    for block in (b.strip() for b in body.split("\n\n")):
        if not block:
            continue
        if URL.fullmatch(block):
            url = html.escape(block, quote=True)
            paragraphs.append(f'<p style="margin:20px 0"><a href="{url}" style="display:inline-block;background:#1f6feb;'
                              f'color:#ffffff;text-decoration:none;padding:9px 16px;border-radius:6px;font-weight:600">'
                              f'{html.escape(button_label(block))}</a></p>')
            continue
        text = URL.sub(lambda m: f'<a href="{m[0]}" style="color:#1f6feb">{m[0]}</a>', html.escape(block, quote=False))
        paragraphs.append('<p style="margin:0 0 14px">' + text.replace("\n", "<br>") + "</p>")
    sign = html.escape(signature()).replace("\n", "<br>")
    end = (html.escape(ACCESS_NOTE) if shared == "access" else "Administrator notice, sent to the shared mailbox." if shared else
           f'<a href="{html.escape(link("/account?tab=email"), quote=True)}" style="color:#6b7785">Change which emails '
           "you get</a>")
    hello = f'<p style="margin:0 0 14px">Hello {html.escape(name)},</p>' if name else ""
    return f"""<!doctype html><html><body style="margin:0;background:#f5f6f8">
<div style="background:#f5f6f8;padding:24px 12px;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif">
<div style="max-width:580px;margin:0 auto;background:#ffffff;border:1px solid #dfe3e8;border-radius:8px;overflow:hidden">
<div style="background:#16202c;color:#ffffff;padding:14px 22px;font-size:16px;font-weight:600">{html.escape(env("MAIL_FROM_NAME"))}</div>
<div style="padding:22px;color:#1d2733;font-size:15px;line-height:1.5">{hello}{''.join(paragraphs)}</div>
<div style="padding:14px 22px;border-top:1px solid #dfe3e8;color:#6b7785;font-size:12px;line-height:1.5">{sign}<br>{end}</div>
</div></div></body></html>"""


def compose(name, subject, body, shared=False):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((env("MAIL_FROM_NAME"), env("MAIL_FROM")))
    msg["Message-ID"] = make_msgid(domain=env("MAIL_FROM").partition("@")[2] or None)
    msg.set_content((f"Hello {name},\n\n" if name else "") + f"{body}{footer(shared)}\n")
    msg.add_alternative(html_body(name, body, shared), subtype="html")
    return msg


def digest_subject(rows):
    return rows[0]["subject"] if len(rows) == 1 else f"Flight logbook: {len(rows)} notices"


def digest_body(rows):
    if len(rows) == 1:
        return rows[0]["body"]
    return "Here is what happened since the last summary:\n\n" + "\n\n".join(
        f"* {r['subject']}\n  " + r["body"].replace("\n", "\n  ") for r in rows)


# --- sending ---------------------------------------------------------------------------------------------------------
def smtp():
    host, port = env("SMTP_HOST"), env_int("SMTP_PORT")
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        if env("SMTP_STARTTLS") == "1":
            server.starttls(context=ssl.create_default_context())
    if env("SMTP_USER"):
        server.login(env("SMTP_USER"), env("SMTP_PASSWORD") or "")
    return server


def _send_batches(con, batches, connect):
    """batches: [(user row, subject, body, outbox ids)]. Marks each outbox row sent or failed."""
    if not batches:
        return 0
    sent = 0
    server = None
    try:
        server = connect()
        for user, subject, body, ids in batches:
            marks = ",".join("?" * len(ids))
            try:
                if isinstance(user, str):  # the shared admin mailbox
                    msg = compose(None, subject, body, shared=True)
                    msg["To"] = user
                elif isinstance(user, dict):  # a training access request to fixed addresses
                    msg = compose(None, subject, body, shared="access")
                    msg["To"] = user["to"]
                    if user["cc"]:
                        msg["Cc"] = user["cc"]
                    if user["reply_to"]:
                        msg["Reply-To"] = user["reply_to"]
                elif not user or not user["email"] or not user["is_active"]:
                    raise ValueError("no address")
                else:
                    msg = compose(user["display_name"] or user["username"], subject, body)
                    msg["To"] = formataddr((user["display_name"] or "", user["email"]))
                server.send_message(msg)
                con.execute(f"UPDATE mail_outbox SET sent_at=?, error=NULL WHERE id IN ({marks})", (now(), *ids))
                sent += 1
            except (smtplib.SMTPException, OSError, ValueError) as exc:
                give_up = isinstance(exc, ValueError) or isinstance(exc, smtplib.SMTPRecipientsRefused)
                con.execute(f"UPDATE mail_outbox SET attempts=attempts+?, error=? WHERE id IN ({marks})",
                            (MAX_ATTEMPTS if give_up else 1, str(exc)[:500], *ids))
            con.commit()
    except (smtplib.SMTPException, OSError) as exc:  # relay unreachable: try again later
        con.rollback()
        ids = [i for *_, batch in batches for i in batch]
        con.execute(f"UPDATE mail_outbox SET attempts=attempts+1, error=? WHERE id IN ({','.join('?' * len(ids))})"
                    " AND sent_at IS NULL", (str(exc)[:500], *ids))
        con.commit()
    finally:
        if server is not None:
            try:
                server.quit()
            except (smtplib.SMTPException, OSError):
                pass
    return sent


def _users(con, ids):
    return {r["id"]: r for r in con.execute(
        f"SELECT id, username, display_name, email, is_active FROM users WHERE id IN ({','.join('?' * len(ids))})",
        list(ids))} if ids else {}


def recipient(row, users):
    if row["kind"] == "access":
        return {"to": row["to_addr"], "cc": row["cc_addr"], "reply_to": row["reply_to"]}
    return row["to_addr"] if row["user_id"] is None else users.get(row["user_id"])


def send_due(con, connect=smtp, digests=False):
    """Send the queued immediate mails, and with digests=True one summary per person of the daily ones."""
    rows = con.execute("SELECT * FROM mail_outbox WHERE sent_at IS NULL AND attempts<? AND digest IN (0, ?)"
                       " ORDER BY id", (MAX_ATTEMPTS, int(digests))).fetchall()
    users = _users(con, {r["user_id"] for r in rows if r["user_id"] is not None})
    batches = [(recipient(r, users), r["subject"], r["body"], [r["id"]]) for r in rows if not r["digest"]]
    grouped = {}
    for r in rows:
        if r["digest"]:
            grouped.setdefault(r["user_id"], []).append(r)
    batches += [(users.get(uid), digest_subject(rs), digest_body(rs), [r["id"] for r in rs]) for uid, rs in grouped.items()]
    return _send_batches(con, batches, connect)


def remind(con, user_id, subject, text, path, key, what):
    """The reminder to the person, and a copy to administrators who asked for everyone's ("expiry_all")."""
    notify(con, user_id, "expiry", subject, text, path, key=key)
    person = con.execute("SELECT COALESCE(display_name, username) FROM users WHERE id=?", (user_id,)).fetchone()
    for admin in con.execute("SELECT id FROM users WHERE is_admin=1 AND is_active=1 AND id!=?", (user_id,)).fetchall():
        notify(con, admin["id"], "expiry_all", f"{person[0]}: {what}", f"Reminder sent to {person[0]}:\n\n{text}",
               f"/people/{user_id}", key=f"copy:{user_id}:{key}")


def queue_reminders(con, today=None):
    """Queue the expiry reminders due today (each once per qualification/end date and number of days)."""
    today = today or datetime.date.today()
    horizon = (today + datetime.timedelta(days=max(REMIND_DAYS))).isoformat()
    for q in con.execute(
            "SELECT q.id, q.user_id, q.type_id, q.valid_until, t.name FROM qualification_submissions q"
            " JOIN qualification_types t ON t.id=q.type_id WHERE q.status='approved' AND q.valid_until IS NOT NULL"
            " AND q.valid_until>=? AND q.valid_until<=? AND NOT EXISTS (SELECT 1 FROM qualification_submissions n"
            " WHERE n.user_id=q.user_id AND n.type_id=q.type_id AND n.status IN ('approved','pending') AND n.id!=q.id"
            " AND (n.valid_until IS NULL OR n.valid_until>q.valid_until))",
            (today.isoformat(), horizon)).fetchall():
        days = (datetime.date.fromisoformat(q["valid_until"]) - today).days
        step = min(d for d in REMIND_DAYS if days <= d)
        remind(con, q["user_id"], f"Your {q['name']} expires on {q['valid_until']}",
               f"Your {q['name']} is valid until {q['valid_until']} ({days} day{'s' * (days != 1)} from now). "
               "Upload the renewed one in My account so you can keep flying and booking.",
               "/account?tab=qualifications", f"expiry:q{q['id']}:{q['valid_until']}:{step}",
               f"{q['name']} expires on {q['valid_until']}")
    if env("PERSON_PROFILE") == "1":
        for p in con.execute("SELECT user_id, end_date FROM user_profiles WHERE indefinite=0 AND end_date>=?"
                             " AND end_date<=?", (today.isoformat(), horizon)).fetchall():
            days = (datetime.date.fromisoformat(p["end_date"]) - today).days
            step = min(d for d in REMIND_DAYS if days <= d)
            remind(con, p["user_id"], f"Your expected end date is {p['end_date']}",
                   f"Your profile says you leave on {p['end_date']} ({days} day{'s' * (days != 1)} from now). "
                   "If you stay longer, give a new end date in your profile.",
                   "/profile", f"expiry:end:{p['end_date']}:{step}", f"expected end date {p['end_date']}")
    con.commit()


def _loop(connect_db):
    last_day = None
    while True:
        _wake.wait(POLL_SECONDS)
        _wake.clear()
        time.sleep(1)  # let the request that queued the mail commit first
        try:
            con = connect_db()
            local = datetime.datetime.now()
            daily = local.hour >= env_int("MAIL_DAILY_HOUR") and local.date() != last_day
            if daily:
                queue_reminders(con)
            send_due(con, digests=daily)
            if daily:
                last_day = local.date()
            con.execute("UPDATE mail_outbox SET body='' WHERE kind='account' AND sent_at IS NOT NULL")  # one-time links
            con.execute("DELETE FROM mail_outbox WHERE created_at<?", (
                (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ"),))
            con.commit()
        except Exception:  # never let a mail problem stop the thread
            import logging
            logging.getLogger(__name__).exception("sending mail failed")
            try:
                con.rollback()
            except Exception:
                pass


def start(connect_db):
    global _started
    if enabled() and not _started:
        _started = True
        threading.Thread(target=_loop, args=(connect_db,), daemon=True, name="mail").start()


def send_test(user):
    """Send one test mail now (admins, from My account); raises on failure."""
    server = smtp()
    try:
        msg = compose(user["display_name"] or user["username"], "Flight logbook: test email",
                      "This is a test email from the flight logbook. Sending email works.")
        msg["To"] = formataddr((user["display_name"] or "", user["email"]))
        server.send_message(msg)
    finally:
        server.quit()
