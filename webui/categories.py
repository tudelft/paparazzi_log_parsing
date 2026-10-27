"""The operation category each flight was flown under, and the authorisation for it.

Categories: national rules before EU 2019/947 (ROC / ROC-light, "national"), "open", "specific", "luc" and
"indoor". An authorisation (catalog managed by admins: a ROC, an operational authorisation, a LUC) has one
category and a validity period; choosing one on a flight also sets its category.

The category of a logbook entry is, in this order:
1. set on the flight or on the paper record ("flight");
2. indoor flight, e.g. in a hall ("indoor");
3. the default of its project ("project");
4. the default of its takeoff place, e.g. a site that needs an authorisation ("place");
5. the date ("date"): every flight before EU_RULES_FROM was under national rules. For our own operations, the
   catalog decides: a national authorisation valid that day (the newest if several) gives national rules; on a day
   no authorisation of ours was valid, the flight can only have been OPEN; while an EU authorisation was valid it
   can be OPEN or SPECIFIC, so it stays unknown. Exemptions (such as a one-off BVLOS exemption) only apply where
   they are set (1-4) and are left out of the date rule;
6. otherwise unknown.
Only 1 is stored per flight, so defaults follow when a project, place or the catalog changes. Our own entries that
need a look are marked with a check: SPECIFIC without an authorisation, or a date outside the authorisation's
validity."""

import sqlite3
from datetime import date

import operators

CATEGORIES = {"national": "National rules (ROC / ROC-light)", "open": "OPEN", "specific": "SPECIFIC",
              "luc": "LUC", "indoor": "Indoor"}
AUTHORISED = ("national", "specific", "luc")  # categories an authorisation can have
SOURCES = ("flight", "indoor", "project", "place", "date")
EU_RULES_FROM = "2020-12-31"  # Regulation (EU) 2019/947 applies; before it only national rules existed
TABLES = ("flights", "flight_records", "projects", "places")


class CategoryError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def catalog(con):
    return [dict(r) for r in con.execute(
        "SELECT id, name, category, operator_id, valid_from, valid_until, link, note, exemption FROM authorisations"
        " ORDER BY COALESCE(valid_from, ''), name COLLATE NOCASE")]


def _day(value, key):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) != 10:
        raise CategoryError(f"{key} must be a date (YYYY-MM-DD).")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise CategoryError(f"{key} must be a date (YYYY-MM-DD).") from None


def validate(con, body, authorisation_id=None):
    """Clean the fields of an authorisation; fields left out of a change keep their value."""
    if not isinstance(body, dict):
        raise CategoryError("Send a JSON object.")
    if authorisation_id is not None:
        current = con.execute("SELECT name, category, operator_id, valid_from, valid_until, link, note, exemption"
                              " FROM authorisations WHERE id=?", (authorisation_id,)).fetchone()
        body = {**dict(current), **body}
    out = {}
    for key, limit in (("name", 80), ("link", 500), ("note", 400)):
        value = body.get(key)
        if value is None:
            value = ""
        if not isinstance(value, str) or len(value.strip()) > limit:
            raise CategoryError(f"{key} must be text of at most {limit} characters.")
        out[key] = value.strip() or None
    if not out["name"]:
        raise CategoryError("An authorisation needs a name, such as its number.")
    if body.get("category") not in AUTHORISED:
        raise CategoryError("category must be one of " + ", ".join(AUTHORISED) + ".")
    out["category"] = body["category"]
    exemption = body.get("exemption") or False
    if exemption not in (True, False, 0, 1) or type(exemption) not in (bool, int):
        raise CategoryError("exemption must be true or false.")
    out["exemption"] = int(exemption)
    out["valid_from"] = _day(body.get("valid_from"), "valid_from")
    out["valid_until"] = _day(body.get("valid_until"), "valid_until")
    if out["valid_from"] and out["valid_until"] and out["valid_from"] > out["valid_until"]:
        raise CategoryError("valid_until is before valid_from.")
    out["operator_id"] = operators.parse_id(con, body.get("operator_id"))
    if out["operator_id"] is False:
        raise CategoryError("No such operator.")
    clash = con.execute("SELECT id FROM authorisations WHERE name=? COLLATE NOCASE AND id!=?",
                        (out["name"], authorisation_id or 0)).fetchone()
    if clash:
        raise CategoryError("An authorisation with that name already exists.", 409)
    return out


def save(con, values, stamp, authorisation_id=None):
    keys = ("name", "category", "operator_id", "valid_from", "valid_until", "link", "note", "exemption")
    try:
        if authorisation_id is None:
            return con.execute(f"INSERT INTO authorisations ({', '.join(keys)}, created_at) VALUES ({'?,' * len(keys)}?)",
                               (*[values[k] for k in keys], stamp)).lastrowid
        con.execute(f"UPDATE authorisations SET {', '.join(k + '=?' for k in keys)} WHERE id=?",
                    (*[values[k] for k in keys], authorisation_id))
        # a flight, project or place that names it keeps a category that matches it
        for table in TABLES:
            con.execute(f"UPDATE {table} SET category=? WHERE authorisation_id=?", (values["category"], authorisation_id))
        return authorisation_id
    except sqlite3.IntegrityError:  # a concurrent change took the name
        con.rollback()
        raise CategoryError("An authorisation with that name already exists.", 409) from None


def usage(con, authorisation_id):
    return {label: con.execute(f"SELECT COUNT(*) FROM {table} WHERE authorisation_id=?", (authorisation_id,)).fetchone()[0]
            for label, table in (("flights", "flights"), ("paper records", "flight_records"),
                                 ("projects", "projects"), ("places", "places"))}


def parse(con, body):
    """(category, authorisation_id) from a request with "category" and/or "authorisation_id". An authorisation sets
    the category; both empty clear it (back to the defaults). Raises CategoryError."""
    raw_id = body.get("authorisation_id")
    category = body.get("category") or None
    if raw_id in (None, ""):
        if category is not None and category not in CATEGORIES:
            raise CategoryError("category must be one of " + ", ".join(CATEGORIES) + ", or empty.")
        return category, None
    if type(raw_id) is bool or not (isinstance(raw_id, int) or (isinstance(raw_id, str) and raw_id.isascii()
                                                               and raw_id.isdigit())) or not 1 <= int(raw_id) <= 2**63 - 1:
        raise CategoryError("Choose an existing authorisation.")
    row = con.execute("SELECT category FROM authorisations WHERE id=?", (int(raw_id),)).fetchone()
    if row is None:
        raise CategoryError("Choose an existing authorisation.")
    if category is not None and category != row["category"]:
        raise CategoryError("That authorisation is for another category.")
    return row["category"], int(raw_id)


def lookup(rows, value):
    """Authorisation id by name (CSV import; rows from catalog), or None if unknown or ambiguous."""
    key = value.strip().casefold()
    found = {r["id"] for r in rows if r["name"].casefold() == key}
    return found.pop() if len(found) == 1 else None


def category_of(value):
    """Category key from a CSV cell: a key or a label, case-insensitive; None if unknown."""
    key = value.strip().casefold()
    return next((k for k, label in CATEGORIES.items() if key in (k, label.casefold())), None)


def _day_of(r):
    day = (r.get("takeoff_utc") or r.get("date_override") or "") if r.get("date_ok") else (r.get("date_override") or "")
    return day[:10] or None


def apply(con, rows, flight_categories):
    """Add category, category_label, authorisation_id, authorisation_name, category_source and category_check to
    logbook rows (after operators.apply). flight_categories maps flight ids to (category, authorisation_id) set on
    that flight; paper rows and the paper records folded into an entry carry their own."""
    auths = {a["id"]: a for a in catalog(con)}
    own = {r[0] for r in con.execute("SELECT id FROM operators WHERE own=1")}
    ours = [a for a in auths.values() if (a["operator_id"] is None or a["operator_id"] in own) and not a["exemption"]]
    valid = lambda a, day: (a["valid_from"] or "") <= day <= (a["valid_until"] or "9999")  # noqa: E731
    pair = "SELECT id, category, authorisation_id FROM {} WHERE category IS NOT NULL"
    projects, places, papers = ({r[0]: (r[1], r[2]) for r in con.execute(pair.format(t))}
                                for t in ("projects", "places", "flight_records"))
    for r in rows:
        if r["source_type"] == "paper":
            explicit = [papers.get(r["manual_id"])]
        else:
            explicit = ([flight_categories.get(s["flight_id"]) for s in r["sources"]]
                        + [papers.get(p["id"]) for p in r.get("linked_records") or []])
        day = _day_of(r)
        dated = None
        if day and r.get("operator_own", True) and ours:
            covering = sorted((a for a in ours if valid(a, day)), key=lambda a: a["valid_from"] or "")
            if covering and covering[-1]["category"] == "national":
                dated = ("national", covering[-1]["id"])
            elif not covering and day >= EU_RULES_FROM:
                dated = ("open", None)
        if dated is None and day and day < EU_RULES_FROM:
            dated = ("national", None)
        candidates = ([("flight", c) for c in explicit] + [("indoor", ("indoor", None) if r.get("indoor") else None),
                                                           ("project", projects.get(r.get("project_id"))),
                                                           ("place", places.get(r.get("takeoff_place_id"))),
                                                           ("date", dated)])
        source, (category, auth_id) = next(((s, c) for s, c in candidates if c), (None, (None, None)))
        auth = auths.get(auth_id)
        r["category"] = category
        r["category_label"] = CATEGORIES.get(category, "Unknown")
        r["category_source"] = source
        r["authorisation_id"] = auth["id"] if auth else None
        r["authorisation_name"] = auth["name"] if auth else None
        check = None
        if r.get("operator_own", True):
            if category == "specific" and not auth:
                check = "SPECIFIC without an authorisation"
            elif auth and day and not valid(auth, day):
                check = f"Flown outside the validity of {auth['name']}"
        r["category_check"] = check
    return rows
