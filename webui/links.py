"""Links between records that describe the same flight (issue #35): paper records <-> telemetry flights
(flight_links), and manual join/split decisions between two telemetry recordings (source_links).

Records and logs are never merged or changed: a row in flight_links only says "same flight" (same=1) or
"not the same flight" (same=0). A telemetry flight is addressed by its log, ac_id and parser flight number, which
survive re-parsing (flight ids do not); the takeoff time stored with the link detects a re-parse that numbered the
flights differently, and such a stale link is ignored instead of attaching to another flight.
"""
from datetime import datetime, timezone

AUTO_WINDOW_S = 300        # clear matches: same aircraft, takeoff within 5 min
SUGGEST_WINDOW_S = 900     # possible duplicates shown for a person to decide
STALE_S = 60
NO_AC_ID = -1              # ac_id is part of the unique key, so NULL is stored as -1


def _ts(iso):
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(iso, fmt).replace(tzinfo=timezone.utc).timestamp()
        except (TypeError, ValueError):
            continue
    return None


def paper_ts(date, takeoff_time):
    return _ts(f"{date}T{takeoff_time}") if date and takeoff_time else None


def flight_key(con, flight_id):
    row = con.execute("SELECT a.log_id, COALESCE(a.ac_id, ?) AS ac_id, f.idx, f.takeoff_utc FROM flights f"
                      " JOIN aircraft a ON a.id=f.aircraft_id WHERE f.id=?", (NO_AC_ID, flight_id)).fetchone()
    return tuple(row) if row else None


def resolved(con):
    """Current link decisions as dicts with the present flight id; stale or orphaned links are left out."""
    out = []
    for r in con.execute(
            "SELECT k.*, f.id AS flight_id, f.takeoff_utc AS current_takeoff FROM flight_links k"
            " JOIN aircraft a ON a.log_id=k.log_id AND COALESCE(a.ac_id, ?)=k.ac_id"
            " JOIN logs l ON l.id=a.log_id"
            " JOIN flights f ON f.aircraft_id=a.id AND f.idx=k.idx"
            " WHERE k.origin!='auto' OR l.date_estimated=0 OR l.date_override IS NOT NULL", (NO_AC_ID,)):
        then, current = _ts(r["takeoff_utc"]), _ts(r["current_takeoff"])
        if then is not None and (current is None or abs(current - then) > STALE_S):
            continue
        out.append(dict(r))
    return out


def decide(con, record_id, flight_ids, same, origin, user_id, stamp):
    """Store a decision for each flight; a manual decision replaces any earlier one for the same pair."""
    for flight_id in flight_ids:
        key = flight_key(con, flight_id)
        if key is None:
            continue
        con.execute(
            "INSERT INTO flight_links(record_id, log_id, ac_id, idx, takeoff_utc, same, origin, created_by, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(record_id, log_id, ac_id, idx) DO UPDATE SET"
            " takeoff_utc=excluded.takeoff_utc, same=excluded.same, origin=excluded.origin,"
            " created_by=excluded.created_by, created_at=excluded.created_at",
            (record_id, *key, int(same), origin, user_id, stamp))


def _telemetry(con):
    """Flights that can take part in automatic matching: real flights with a fleet aircraft and a trusted clock."""
    rows = con.execute(
        "SELECT f.id, f.takeoff_utc, f.landing_utc, f.duration_s, a.drone_id FROM flights f"
        " JOIN aircraft a ON a.id=f.aircraft_id JOIN logs l ON l.id=a.log_id"
        " WHERE l.status='done' AND f.hidden=0 AND a.drone_id IS NOT NULL AND f.verdict!='ground'"
        " AND l.date_override IS NULL AND l.date_estimated=0 AND f.takeoff_utc>='2000'"
        " AND (a.simulation IS NULL OR json_extract(a.simulation, '$.detected') IS NOT 1)")
    out = []
    for r in rows:
        start = _ts(r["takeoff_utc"])
        if start is None:
            continue
        end = _ts(r["landing_utc"]) or start + (r["duration_s"] or 0)
        out.append(dict(id=r["id"], drone_id=r["drone_id"], start=start, end=max(end, start)))
    return out


def _same_time(a, b):
    """At least half of the shorter recording overlaps (1 s for rounding), as for merging (_same_type_overlap)."""
    return min(a["end"], b["end"]) - max(a["start"], b["start"]) >= 0.5 * min(a["end"] - a["start"], b["end"] - b["start"]) - 1


def auto_link(con, stamp):
    """Link paper records to telemetry where the match is clear both ways. Records or flights with any stored
    decision are left alone, so a later upload, re-parse or import never replaces a link or undoes an unlink.
    Returns the number of records linked."""
    papers = []
    for r in con.execute("SELECT id, drone_id, date, takeoff_time FROM flight_records"
                         " WHERE hidden=0 AND kind='flight' AND takeoff_time IS NOT NULL"):
        start = paper_ts(r["date"], r["takeoff_time"])
        if start is not None:
            papers.append(dict(id=r["id"], drone_id=r["drone_id"], start=start))
    flights = _telemetry(con)
    decided = resolved(con)
    decided_records = {d["record_id"] for d in decided}
    decided_flights = {d["flight_id"] for d in decided}
    by_drone = {}
    for f in flights:
        by_drone.setdefault(f["drone_id"], []).append(f)
    papers_by_drone = {}
    for p in papers:
        papers_by_drone.setdefault(p["drone_id"], []).append(p)
    linked = 0
    for p in papers:
        if p["id"] in decided_records:
            continue
        near = [f for f in by_drone.get(p["drone_id"], []) if abs(f["start"] - p["start"]) <= AUTO_WINDOW_S]
        # Several recordings of one flight (SD and GCS) cover the same time; anything else is ambiguous.
        if not near or not all(_same_time(a, b) for a in near for b in near):
            continue
        if any(f["id"] in decided_flights for f in near):
            continue
        rivals = [q for q in papers_by_drone.get(p["drone_id"], [])
                  if any(abs(f["start"] - q["start"]) <= AUTO_WINDOW_S for f in near)]
        if len(rivals) != 1:
            continue
        decide(con, p["id"], [f["id"] for f in near], True, "auto", None, stamp)
        decided_flights.update(f["id"] for f in near)
        linked += 1
    return linked


def combine(con, rows):
    """Fold paper records into the telemetry entries they are linked to. Rows are logbook rows the current user can
    see; links to rows they cannot see are not applied. A linked entry counts as its telemetry (flight count and
    logged time); the paper record adds its reference and comment, and fields where it disagrees become conflicts.
    Unlinked paper records get possible duplicates for a person to decide."""
    decisions = resolved(con)
    telemetry = [r for r in rows if r["source_type"] == "telemetry"]
    papers = {r["manual_id"]: r for r in rows if r["source_type"] == "paper"}
    entry_of = {s["flight_id"]: i for i, r in enumerate(telemetry) for s in r["sources"]}
    vetoed = {(d["record_id"], entry_of.get(d["flight_id"])) for d in decisions if not d["same"]}
    root = {}

    def find(x):
        root.setdefault(x, x)
        while root[x] != x:
            root[x] = root[root[x]]
            x = root[x]
        return x

    origin = {}
    for d in decisions:
        if d["same"] and d["record_id"] in papers and d["flight_id"] in entry_of:
            entry = entry_of[d["flight_id"]]
            root[find(("p", d["record_id"]))] = find(("t", entry))
            origin[(d["record_id"], entry)] = d["origin"]
    groups = {}
    for x in list(root):
        groups.setdefault(find(x), []).append(x)
    absorbed, out = set(), []
    replaced = {}
    for members in groups.values():
        entries = sorted(i for kind, i in members if kind == "t")
        records = sorted(i for kind, i in members if kind == "p")
        if not entries or not records:
            continue
        parts = [telemetry[i] for i in entries]
        main = dict(max(parts, key=lambda r: r["duration_s"] or 0))
        main["flight_count"] = sum(r["flight_count"] for r in parts)
        main["duration_s"] = sum(r["duration_s"] or 0 for r in parts)
        main["sources"] = [s for r in parts for s in r["sources"]]
        main["linked_records"] = [dict(
            id=p, reference=papers[p]["reference"],
            origin="manual" if any(origin.get((p, i)) == "manual" for i in entries) else "auto",
            flight_ids=[s["flight_id"] for i in entries if (p, i) in origin for s in telemetry[i]["sources"]],
        ) for p in records]
        main["reference"] = ", ".join(papers[p]["reference"] for p in records if papers[p]["reference"]) or None
        if not (main.get("comment") or "").strip():
            main["comment"] = "\n".join(papers[p]["comment"] for p in records if (papers[p]["comment"] or "").strip()) or None
        main["conflicts"] = conflicts(main, [papers[p] for p in records])
        for i in entries:
            replaced[i] = main if i == entries[0] else None
        absorbed.update(records)
    for i, r in enumerate(telemetry):
        if i in replaced:
            if replaced[i] is not None:
                out.append(replaced[i])
        else:
            out.append(r)
    for record_id, p in papers.items():
        if record_id in absorbed:
            continue
        start = _ts(p["takeoff_utc"]) if len(p["takeoff_utc"] or "") > 10 else None
        p["possible_duplicates"] = [] if start is None else [
            dict(flight_ids=[s["flight_id"] for s in r["sources"]], takeoff_utc=r["takeoff_utc"],
                 duration_s=r["duration_s"], aircraft=r["aircraft"])
            for i, r in enumerate(telemetry)
            if r["drone_id"] == p["drone_id"] and (record_id, i) not in vetoed and _ts(r["takeoff_utc"]) is not None
            and abs(_ts(r["takeoff_utc"]) - start) <= SUGGEST_WINDOW_S]
        out.append(p)
    return out


def conflicts(main, papers):
    """Fields where a linked paper record disagrees with the telemetry; the telemetry value is shown."""
    out = []
    for p in papers:
        label = p["reference"] or f"paper record {p['manual_id']}"
        if p["drone_id"] != main["drone_id"]:
            out.append(dict(field="aircraft", record=label, paper=p["aircraft"], log=main["aircraft"]))
        if p["project_id"] != main["project_id"]:
            out.append(dict(field="project", record=label, paper=p["project_name"], log=main.get("project_name")))
        if p["takeoff_place_id"] is not None and main.get("takeoff_place_id") is not None \
                and p["takeoff_place_id"] != main["takeoff_place_id"]:
            out.append(dict(field="place", record=label, paper=p["takeoff_place"], log=main["takeoff_place"]))
        for role in ("pic", "pac", "gcs"):
            mine, theirs = (p.get(role + "_user") or {}), (main.get(role + "_user") or {})
            if mine.get("id") and theirs.get("id") and mine["id"] != theirs["id"]:
                out.append(dict(field=role.upper(), record=label, paper=mine.get("display_name"),
                                log=theirs.get("display_name")))
    return out


def _flights_by_key(con):
    return {(r["log_id"], r["ac_id"], r["idx"]): (r["id"], r["takeoff_utc"]) for r in con.execute(
        "SELECT f.id, f.idx, f.takeoff_utc, a.log_id, COALESCE(a.ac_id, ?) AS ac_id FROM flights f"
        " JOIN aircraft a ON a.id=f.aircraft_id", (NO_AC_ID,))}


def source_decisions(con):
    """Manual join (True) / split (False) decisions between two recordings, keyed by frozenset of current flight ids.
    Stale decisions (a re-parse moved either flight) are left out."""
    flights, out = _flights_by_key(con), {}
    for r in con.execute("SELECT * FROM source_links ORDER BY created_at, id"):
        ids = []
        for side in ("a", "b"):
            current = flights.get((r[side + "_log_id"], r[side + "_ac_id"], r[side + "_idx"]))
            then = _ts(r[side + "_takeoff_utc"])
            if current is None or (then is not None and (_ts(current[1]) is None or abs(_ts(current[1]) - then) > STALE_S)):
                break
            ids.append(current[0])
        else:
            out[frozenset(ids)] = bool(r["same"])
    return out


def decide_sources(con, first, others, same, user_id, stamp):
    """Store that flight `first` is (same=True) or is not (same=False) the same flight as each of `others`."""
    a = flight_key(con, first)
    for other in others:
        b = flight_key(con, other)
        if a is None or b is None or a[:3] == b[:3]:
            continue
        x, y = sorted((a, b), key=lambda k: k[:3])
        con.execute(
            "INSERT INTO source_links(a_log_id, a_ac_id, a_idx, a_takeoff_utc, b_log_id, b_ac_id, b_idx, b_takeoff_utc,"
            " same, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(a_log_id, a_ac_id, a_idx, b_log_id, b_ac_id, b_idx) DO UPDATE SET"
            " a_takeoff_utc=excluded.a_takeoff_utc, b_takeoff_utc=excluded.b_takeoff_utc, same=excluded.same,"
            " created_by=excluded.created_by, created_at=excluded.created_at",
            (*x, *y, int(same), user_id, stamp))
