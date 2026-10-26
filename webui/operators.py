"""The UAS operator (operator registration) each flight was flown under.

Most flights are flown under our own registration, but some are part of another organisation's operation: a
partner's authorisation, a test centre, a foreign authority. Operator reporting must count only our own flights;
pilot experience counts all of them.

The operator of a logbook entry is, in this order:
1. set on the flight or on the paper record ("flight");
2. the operator of its project ("project");
3. the operator of its takeoff place, e.g. a test centre that runs its own operation ("place");
4. the operator its PIC flies for, e.g. crew of a partner ("pic");
5. our own operator ("default").
Only 1 is stored per flight; the others follow when a project, place or person changes, so existing flights need no
migration: setting the operator of the external crew fills in their flights. When the PIC is external crew without
an operator and nothing more specific applies, the entry is marked to be checked."""

import sqlite3

SOURCES = ("flight", "project", "place", "pic", "default")


class OperatorError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def catalog(con):
    return [dict(r) for r in con.execute(
        "SELECT id, name, registration, country, own, note FROM operators ORDER BY own DESC, name COLLATE NOCASE")]


def validate(con, body, operator_id=None):
    """Clean the fields of an operator; operator_id is the one being changed (fields left out keep their value)."""
    if not isinstance(body, dict):
        raise OperatorError("Send a JSON object.")
    if operator_id is not None:
        current = con.execute("SELECT name, registration, country, own, note FROM operators WHERE id=?",
                              (operator_id,)).fetchone()
        body = {**dict(current), "own": bool(current["own"]), **body}
    out = {}
    for key, limit in (("name", 80), ("registration", 40), ("country", 40), ("note", 400)):
        value = body.get(key)
        if value is None:
            value = ""
        if not isinstance(value, str) or len(value.strip()) > limit:
            raise OperatorError(f"{key} must be text of at most {limit} characters.")
        out[key] = value.strip() or None
    if not out["name"]:
        raise OperatorError("An operator needs a name.")
    if type(body.get("own", False)) is not bool:
        raise OperatorError("own must be true or false.")
    out["own"] = int(body.get("own", False))
    clash = con.execute("SELECT id FROM operators WHERE name=? COLLATE NOCASE AND id!=?",
                        (out["name"], operator_id or 0)).fetchone()
    if clash:
        raise OperatorError("An operator with that name already exists.", 409)
    return out


def save(con, values, stamp, operator_id=None):
    try:
        return _save(con, values, stamp, operator_id)
    except sqlite3.IntegrityError:  # a concurrent change took the name or the own mark
        con.rollback()
        raise OperatorError("An operator with that name already exists.", 409) from None


def _save(con, values, stamp, operator_id):
    if values["own"]:  # only one operator is our own
        con.execute("UPDATE operators SET own=0 WHERE own=1 AND id!=?", (operator_id or 0,))
    if operator_id is None:
        return con.execute(
            "INSERT INTO operators (name, registration, country, own, note, created_at) VALUES (?,?,?,?,?,?)",
            (values["name"], values["registration"], values["country"], values["own"], values["note"], stamp)).lastrowid
    con.execute("UPDATE operators SET name=?, registration=?, country=?, own=?, note=? WHERE id=?",
                (values["name"], values["registration"], values["country"], values["own"], values["note"], operator_id))
    return operator_id


def usage(con, operator_id):
    """How many flights, paper records, projects, places and people name this operator."""
    return {label: con.execute(f"SELECT COUNT(*) FROM {table} WHERE operator_id=?", (operator_id,)).fetchone()[0]
            for label, table in (("flights", "flights"), ("paper records", "flight_records"),
                                 ("projects", "projects"), ("places", "places"), ("people", "users"))}


def parse_id(con, raw):
    """An operator id from a request: None clears it, False when it is not a known operator."""
    if raw in (None, ""):
        return None
    if type(raw) is bool or not (isinstance(raw, int) or (isinstance(raw, str) and raw.isascii() and raw.isdigit())):
        return False
    if not 1 <= int(raw) <= 2**63 - 1 or not con.execute("SELECT 1 FROM operators WHERE id=?", (int(raw),)).fetchone():
        return False
    return int(raw)


def lookup(rows, value):
    """Operator id by name or registration number (CSV import; rows from catalog), or None if unknown or ambiguous."""
    key = value.strip().casefold()
    found = {r["id"] for r in rows if key in (r["name"].casefold(), (r["registration"] or "").strip().casefold())}
    return found.pop() if len(found) == 1 else None


def apply(con, rows, flight_operators):
    """Add operator_id, operator_name, operator_registration, operator_own, operator_source and operator_check to
    logbook rows. flight_operators maps flight ids to the operator set on that flight (recordings of one entry
    are edited together); paper rows and the paper records folded into an entry carry their own."""
    operators = {o["id"]: o for o in catalog(con)}
    own = next((o for o in operators.values() if o["own"]), None)
    projects = {r[0]: r[1] for r in con.execute("SELECT id, operator_id FROM projects WHERE operator_id IS NOT NULL")}
    places = {r[0]: r[1] for r in con.execute("SELECT id, operator_id FROM places WHERE operator_id IS NOT NULL")}
    papers = {r[0]: r[1] for r in con.execute("SELECT id, operator_id FROM flight_records WHERE operator_id IS NOT NULL")}
    crew = {r[0]: r[1] for r in con.execute("SELECT id, operator_id FROM users WHERE operator_id IS NOT NULL")}
    external = {r[0] for r in con.execute("SELECT id FROM users WHERE is_external=1 AND operator_id IS NULL")}
    for r in rows:
        if r["source_type"] == "paper":
            explicit = [papers.get(r["manual_id"])]
        else:
            explicit = ([flight_operators.get(s["flight_id"]) for s in r["sources"]]
                        + [papers.get(p["id"]) for p in r.get("linked_records") or []])
        pic = (r.get("pic_user") or {}).get("id")
        candidates = ([("flight", i) for i in explicit] + [("project", projects.get(r.get("project_id"))),
                                                           ("place", places.get(r.get("takeoff_place_id"))),
                                                           ("pic", crew.get(pic))])
        source, operator = next(((s, operators[i]) for s, i in candidates if i in operators), ("default", own))
        r["operator_id"] = operator["id"] if operator else None
        r["operator_name"] = operator["name"] if operator else None
        r["operator_registration"] = operator["registration"] if operator else None
        r["operator_own"] = bool(operator["own"]) if operator else True  # nothing set up yet: all flights are ours
        r["operator_source"] = source
        r["operator_check"] = source == "default" and pic in external
    return rows
