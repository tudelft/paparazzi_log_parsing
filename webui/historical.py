"""Source-independent flight records transcribed from paper, without synthetic telemetry or log files."""
import csv
import hashlib
import io
import json
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation

import qualifications

COLUMNS = ("aircraft", "pic", "project", "date", "duration_minutes", "flight_count",
           "kind", "takeoff_time", "location", "pac", "gcs", "goal", "comment", "reference")
FIELDS = ("drone_id", "pic_user_id", "project_id", "date", "duration_s", "flight_count",
          "kind", "takeoff_time", "place_id", "pac_user_id", "gcs_user_id", "goal", "comment", "reference")
COLUMN_OF = dict(zip(FIELDS, COLUMNS))  # stored field -> CSV column


class RecordError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def can_edit(con, user, project_id):
    return bool(user["is_admin"] or con.execute(
        "SELECT 1 FROM project_members WHERE user_id=? AND project_id=? AND role='full'",
        (user["id"], project_id)).fetchone())


def visible(con, user, record_id):
    row = con.execute("SELECT * FROM flight_records WHERE id=?", (record_id,)).fetchone()
    if row is None or (not user["is_admin"] and not con.execute(
            "SELECT 1 FROM project_members WHERE user_id=? AND project_id=?",
            (user["id"], row["project_id"])).fetchone()):
        raise RecordError("No such historical record.", 404)
    return row


def integer(value, label, minimum=1, maximum=2**63 - 1):
    text = str(value)
    if len(text) > 19 or not text.isascii() or not text.isdigit() or not minimum <= int(text) <= maximum:
        raise RecordError(f"{label} must be an integer between {minimum} and {maximum}.")
    return int(text)


def validate(con, user, body):
    if not isinstance(body, dict):
        raise RecordError("Send a JSON object.")
    out = {}
    for field, table, required in (
            ("project_id", "projects", True), ("drone_id", "drones", True),
            ("pic_user_id", "users", True), ("pac_user_id", "users", False),
            ("gcs_user_id", "users", False), ("place_id", "places", False)):
        value = body.get(field)
        if not required and value in (None, ""):
            out[field] = None
            continue
        value = integer(value, field)
        if not con.execute(f"SELECT 1 FROM {table} WHERE id=?", (value,)).fetchone():
            raise RecordError(f"Unknown {field}.")
        out[field] = value
    if not can_edit(con, user, out["project_id"]):
        raise RecordError("Full project access is required to enter historical records.", 403)
    try:
        day = date.fromisoformat(body.get("date", ""))
        if day.isoformat() != body["date"] or day > datetime.now(timezone.utc).date():
            raise ValueError
    except (TypeError, ValueError, KeyError) as exc:
        raise RecordError("Enter a valid date (YYYY-MM-DD), not in the future.") from exc
    out["date"] = day.isoformat()
    raw_time = body.get("takeoff_time")
    out["takeoff_time"] = None if raw_time in (None, "") else raw_time
    if out["takeoff_time"] is not None:
        try:
            parsed = time.fromisoformat(out["takeoff_time"])
            if parsed.tzinfo or len(out["takeoff_time"]) not in (5, 8):
                raise ValueError
            out["takeoff_time"] = parsed.strftime("%H:%M:%S")
        except (TypeError, ValueError) as exc:
            raise RecordError("Takeoff time must be HH:MM or HH:MM:SS in UTC, or left blank.") from exc
    try:
        minutes = Decimal(str(body.get("duration_minutes")))
        if not minutes.is_finite() or not 0 < minutes <= 1440:
            raise InvalidOperation
        seconds = float(minutes * 60)
        if seconds <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError) as exc:
        raise RecordError("Total flight time must be greater than 0 and at most 1440 minutes.") from exc
    out["duration_s"] = seconds
    out["flight_count"] = integer(body.get("flight_count", 1), "Flight count", maximum=1000)
    kind = body.get("kind")
    out["kind"] = ("daily" if out["flight_count"] > 1 else "flight") if kind in (None, "") else kind
    if out["kind"] not in ("flight", "daily") or (out["kind"] == "flight" and out["flight_count"] != 1):
        raise RecordError("An individual flight must have flight count 1; use daily for a summary.")
    if out["kind"] == "daily" and out["takeoff_time"]:
        raise RecordError("Leave takeoff time blank for a daily summary.")
    for key, limit in (("goal", 1000), ("comment", 4000), ("reference", 500)):
        value = body.get(key)
        if value is None:
            value = ""
        if not isinstance(value, str) or len(value) > limit:
            raise RecordError(f"{key} must be text of at most {limit} characters.")
        out[key] = value.strip() or None
    return out


def fingerprint(values):
    return hashlib.sha256(json.dumps(values, sort_keys=True, allow_nan=False).encode()).hexdigest()


def store(con, values, user_id, stamp, record_id=None):
    digest = fingerprint(values)
    duplicate = con.execute("SELECT id FROM flight_records WHERE fingerprint=? AND id!=?",
                            (digest, record_id or 0)).fetchone()
    if duplicate:
        raise RecordError(f"Duplicate historical record (record {duplicate['id']}).", 409)
    if record_id is None:
        return con.execute(
            f"INSERT INTO flight_records ({','.join(FIELDS)},fingerprint,created_by,created_at,updated_at)"
            f" VALUES ({','.join('?' for _ in range(len(FIELDS) + 4))})",
            (*[values[f] for f in FIELDS], digest, user_id, stamp, stamp)).lastrowid
    con.execute(f"UPDATE flight_records SET {','.join(f'{f}=?' for f in FIELDS)},fingerprint=?,updated_at=? WHERE id=?",
                (*[values[f] for f in FIELDS], digest, stamp, record_id))
    return record_id


def match(con, values):
    """Return (action, existing row, values) for a CSV row: unchanged, update or create.

    A row describes an existing record when it is identical, has the same reference (such as a flight number
    F00001), or, without a reference match, is an individual flight with the same aircraft, date and takeoff time
    and no different reference. A reference shared by several records (such as a book page) only matches those
    with the same date and takeoff time. Daily summaries and flights without takeoff time are only recognised by
    reference or when identical, because several of them can share an aircraft and day. A row without reference
    keeps the reference of the record it updates."""
    row = con.execute("SELECT * FROM flight_records WHERE fingerprint=?", (fingerprint(values),)).fetchone()
    if row is not None:
        return "unchanged", row, values
    found = []
    if values["reference"]:
        found = con.execute("SELECT * FROM flight_records WHERE reference=? ORDER BY id", (values["reference"],)).fetchall()
        if len(found) > 1:
            found = [r for r in found if r["date"] == values["date"] and r["takeoff_time"] == values["takeoff_time"]]
    if not found and values["kind"] == "flight" and values["takeoff_time"]:
        found = [r for r in con.execute(
            "SELECT * FROM flight_records WHERE kind='flight' AND drone_id=? AND date=? AND takeoff_time=? ORDER BY id",
            (values["drone_id"], values["date"], values["takeoff_time"]))
            if not (r["reference"] and values["reference"])]
    if len(found) > 1:
        raise RecordError("Matches paper records " + ", ".join(str(r["id"]) for r in found) +
                          ". Give each flight a unique reference to choose one.", 409)
    if not found:
        return "create", None, values
    row = found[0]
    if not values["reference"] and row["reference"]:
        values = {**values, "reference": row["reference"]}
        if fingerprint(values) == row["fingerprint"]:
            return "unchanged", row, values
    return "update", row, values


def parse_csv(con, user, text):
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")), strict=True)
    required = {"aircraft", "pic", "project", "date", "duration_minutes", "flight_count"}
    if (not reader.fieldnames or not required.issubset(reader.fieldnames)
            or len(set(reader.fieldnames)) != len(reader.fieldnames)
            or set(reader.fieldnames) - set(COLUMNS)):
        raise RecordError("CSV headers must include " + ", ".join(sorted(required)) +
                          ". Download the template for supported columns.")
    catalogs = {
        "aircraft": list(con.execute("SELECT id, name FROM drones")),
        "project": list(con.execute("SELECT id, name FROM projects")),
        "location": list(con.execute("SELECT id, name FROM places")),
    }
    crew = qualifications.crew_keys(con)
    records = []
    seen = set()
    targets = {}  # existing record id -> CSV line that updates it
    for row in reader:
        if len(records) >= 1000:
            raise RecordError("Import at most 1000 records at once.")
        if None in row or any(v is None for v in row.values()):
            raise RecordError(f"CSV line {reader.line_num}: incorrect number of columns.")
        body = {k: v.strip() for k, v in row.items()}
        try:
            for key, catalog, target, required_field in (
                    ("aircraft", "aircraft", "drone_id", True), ("project", "project", "project_id", True),
                    ("pic", "person", "pic_user_id", True), ("pac", "person", "pac_user_id", False),
                    ("gcs", "person", "gcs_user_id", False), ("location", "location", "place_id", False)):
                value = body.get(key, "")
                if not value and not required_field:
                    continue
                if catalog == "person":
                    uid = crew.get(value.casefold())
                    if uid is None:
                        raise RecordError(f"Unknown or ambiguous {key}: {value!r}. "
                                          "Use a unique display name or ask an admin to add a crew alias.")
                    body[target] = uid
                    continue
                matches = [r["id"] for r in catalogs[catalog] if r["name"].casefold() == value.casefold()]
                if len(matches) != 1:
                    raise RecordError(f"Unknown or ambiguous {key}: {value!r}. Use an existing name.")
                body[target] = matches[0]
            action, existing, values = match(con, validate(con, user, body))
            digest = fingerprint(values)
            if digest in seen:
                raise RecordError("Same record as an earlier line.", 409)
            seen.add(digest)
            if existing is not None:
                if existing["id"] in targets:
                    raise RecordError(f"Same paper record {existing['id']} as CSV line {targets[existing['id']]}.", 409)
                targets[existing["id"]] = reader.line_num
            records.append(dict(
                line=reader.line_num, action=action, values=values,
                record_id=existing["id"] if existing is not None else None,
                hidden=bool(existing is not None and existing["hidden"]),
                changes=[COLUMN_OF[f] for f in FIELDS if existing is not None and values[f] != existing[f]],
                label=dict(aircraft=body["aircraft"], date=values["date"], takeoff_time=values["takeoff_time"],
                           reference=values["reference"])))
        except RecordError as exc:
            raise RecordError(f"CSV line {reader.line_num}: {exc}", exc.status) from exc
    if not records:
        raise RecordError("The CSV contains no records.")
    return records


def save_import(con, records, user_id, stamp):
    """Create or update the records of a parsed CSV; returns the ids that were written."""
    written = []
    for r in records:
        if r["action"] == "unchanged":
            continue
        r["record_id"] = store(con, r["values"], user_id, stamp, r["record_id"])
        written.append(r["record_id"])
    return written


def rows(con, user, project=None, hidden=False):
    where, params = "r.hidden=?", [int(hidden)]
    if not user["is_admin"]:
        where += " AND r.project_id IN (SELECT project_id FROM project_members WHERE user_id=?)"
        params.append(user["id"])
    if project == "none":
        return []
    if project and str(project).isdigit():
        where += " AND r.project_id=?"
        params.append(int(project))
    records = con.execute(
        "SELECT r.*, d.name AS aircraft, d.unique_id AS aircraft_uid, p.name AS project_name,"
        " l.name AS place_name, l.indoor FROM flight_records r"
        " JOIN drones d ON d.id=r.drone_id JOIN projects p ON p.id=r.project_id"
        f" LEFT JOIN places l ON l.id=r.place_id WHERE {where}", params)
    out = []
    for row in records:
        r = dict(row)
        editable = can_edit(con, user, r["project_id"])
        takeoff = r["date"] + ("T" + r["takeoff_time"] + ".000Z" if r["takeoff_time"] else "")
        out.append(dict(
            record_key=f"paper:{r['id']}", manual_id=r["id"], source_type="paper", flight_count=r["flight_count"],
            record_kind=r["kind"], reference=r["reference"], flight_id=None, log_id=None, sources=[],
            aircraft=r["aircraft"], aircraft_uid=r["aircraft_uid"], drone_id=r["drone_id"],
            project_id=r["project_id"], project_name=r["project_name"], takeoff_utc=takeoff, landing_utc=None,
            duration_s=r["duration_s"], distance_m=None, from_takeoff_m=None, max_from_takeoff_m=None,
            takeoff_lat=None, takeoff_lon=None, landing_lat=None, landing_lon=None,
            takeoff_place=r["place_name"], takeoff_place_id=r["place_id"], landing_place=None, landing_place_id=None,
            indoor=bool(r["indoor"]), verdict="flight", optitrack=False, date_ok=True, date_override=None,
            goal=r["goal"], comment=r["comment"], pic=None, pac=None, gcs=None,
            pic_user_id=r["pic_user_id"], pac_user_id=r["pac_user_id"], gcs_user_id=r["gcs_user_id"],
            hidden=bool(r["hidden"]), can_edit_record=editable,
            can_edit_crew=bool(user["is_admin"]), can_edit_project=bool(user["is_admin"]), can_edit_date=False,
            can_edit_comment=False, takeoff_voltage_v=None, landing_voltage_v=None,
        ))
    return out
