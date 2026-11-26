"""Decided approvals for the administrators' history (Approval queue, Decided): qualification submissions and training
passes (approved, rejected, withdrawn; also passes granted without approval and imported certificates), bookings
of agendas that need approval (approved or rejected; also bookings an administrator or approver made, approved at
once) and training access extensions (approved, rejected or withdrawn; also those sent without approval). Built from what the records already keep: who decided, when and the rejection reason."""
import datetime
import re

KINDS = ("training", "qualification", "booking", "extension", "operation")
PERIODS = ("90d", "year", "all")
PAGE_SIZE = 50
QUERY_MAX = 100
# The note a training pass leaves on its qualification submission (training.pass_note)
TRAINING_NOTE = re.compile(r'^Training "(.+)" passed (\d+)/(\d+) on [\d-]+(?: · briefed by (.+))?$')


def since(period, today=None):
    """The earliest decision time (ISO, UTC) of a period, or None for all."""
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    if period == "90d":
        return (today - datetime.timedelta(days=90)).isoformat()
    if period == "year":
        return f"{today.year}-01-01"
    return None


def qualification_items(con, start):
    rows = con.execute(
        "SELECT q.id,q.user_id,q.type_id,q.status,q.submitted_at,q.reviewed_by,q.reviewed_at,q.review_note,"
        " q.valid_from,q.valid_until,q.number,q.evidence_path,q.filename,q.mime,q.note,t.name AS category,"
        " COALESCE(u.display_name,u.username) AS person,COALESCE(r.display_name,r.username) AS reviewer,"
        " r.is_admin AS reviewer_admin FROM qualification_submissions q"
        " JOIN users u ON u.id=q.user_id JOIN qualification_types t ON t.id=q.type_id"
        " LEFT JOIN users r ON r.id=q.reviewed_by"
        " WHERE q.reviewed_at IS NOT NULL AND q.status IN ('approved','superseded','rejected','withdrawn')"
        " AND q.reviewed_at>=?", (start or "",)).fetchall()
    trainings = {r["name"]: r["id"] for r in con.execute("SELECT id,name FROM trainings")} if rows else {}
    # what the audit log says was approved without the queue: a pass of a training that needs no approval, an import
    granted, imported = set(), set()
    if rows:
        for a in con.execute("SELECT action,json_extract(details_json,'$.id') AS id,"
                             " json_extract(details_json,'$.source') AS source FROM qualification_audit"
                             " WHERE action IN ('training-passed','approved','archived') AND created_at>=?",
                             (start or "",)):
            if a["action"] == "training-passed":
                granted.add(a["id"])
            elif a["source"] == "csv-import":
                imported.add(a["id"])
    out = []
    for r in rows:
        note = TRAINING_NOTE.match(r["note"] or "")
        approved = r["status"] in ("approved", "superseded")
        automatic = approved and (r["id"] in granted or r["id"] in imported)
        by, reason = r["reviewer"], r["review_note"]
        if automatic and r["id"] in granted:
            by, reason = None, "Automatic: the training needs no approval"
        elif automatic:
            reason = "Imported"
        elif r["status"] == "withdrawn" and r["reviewed_by"] == r["user_id"]:
            reason = "By the person"
        answer = None
        if note:
            link = con.execute("SELECT access_answer FROM training_progress WHERE submission_id=?", (r["id"],)).fetchone()
            answer = link["access_answer"] if link else None
        out.append(dict(
            key=f"q{r['id']}", kind="training" if note else "qualification", id=r["id"], user_id=r["user_id"],
            person=r["person"], what=note[1] if note else r["category"], category=r["category"],
            outcome="approved" if approved else r["status"], replaced=r["status"] == "superseded",
            automatic=automatic, decided_at=r["reviewed_at"], submitted_at=r["submitted_at"], by=by,
            by_approver=False, reason=reason, rejection=r["review_note"] if r["status"] == "rejected" else None,
            number=r["number"], valid_from=r["valid_from"], valid_until=r["valid_until"],
            has_evidence=bool(r["evidence_path"]), evidence_name=r["filename"] if r["evidence_path"] else None,
            evidence_mime=r["mime"] if r["evidence_path"] else None, evidence_label=r["note"] or None,
            training_id=trainings.get(note[1]) if note else None, score=f"{note[2]} / {note[3]}" if note else None,
            briefed_by=note[4] if note else None, access_answer=answer))
    return out


def booking_items(con, start):
    rows = con.execute(
        "SELECT b.*,t.name AS thing,COALESCE(u.display_name,u.username) AS person,"
        " COALESCE(d.display_name,d.username) AS decider,d.is_admin AS decider_admin,"
        " EXISTS(SELECT 1 FROM booking_approvers a WHERE a.thing_id=b.thing_id AND a.user_id=b.decided_by) AS approver"
        " FROM bookings b JOIN booking_things t ON t.id=b.thing_id JOIN users u ON u.id=b.user_id"
        " LEFT JOIN users d ON d.id=b.decided_by"
        " WHERE b.status IN ('approved','rejected') AND b.decided_at IS NOT NULL AND b.decided_at>=?",
        (start or "",)).fetchall()
    out = []
    for r in rows:
        flags = [f["label"] for f in con.execute(
            "SELECT f.label FROM booking_flag_values v JOIN booking_flags f ON f.id=v.flag_id"
            " WHERE v.booking_id=? ORDER BY f.sort_order,f.id", (r["id"],))]
        # approved the moment it was made (by an administrator or approver), or when approval was switched off
        automatic = r["status"] == "approved" and (r["decided_by"] is None or r["decided_at"] == r["created_at"])
        reason = r["review_note"]
        if r["decided_by"] is None:
            reason = "Automatic: approval was switched off"
        elif automatic:
            reason = "Automatic: booked by an " + ("administrator" if r["decider_admin"] else "approver")
        out.append(dict(
            key=f"b{r['id']}", kind="booking", id=r["id"], user_id=r["user_id"], person=r["person"],
            what=r["thing"], thing_id=r["thing_id"], outcome=r["status"], replaced=False, automatic=automatic,
            decided_at=r["decided_at"], submitted_at=r["created_at"], by=r["decider"],
            by_approver=bool(r["approver"]) and not r["decider_admin"] and not automatic, reason=reason,
            rejection=r["review_note"] if r["status"] == "rejected" else None, description=r["description"],
            start=r["starts_at"], end=r["ends_at"], flags=flags))
    return out


def extension_items(con, start):
    """Access extension requests of trainings: decided by an administrator (approval), withdrawn while waiting, or
    sent without approval (direct, or after a new pass of the test)."""
    rows = con.execute(
        "SELECT e.*,t.name AS training,COALESCE(u.display_name,u.username) AS person,"
        " COALESCE(d.display_name,d.username) AS decider FROM training_extensions e"
        " JOIN trainings t ON t.id=e.training_id JOIN users u ON u.id=e.user_id LEFT JOIN users d ON d.id=e.decided_by"
        " WHERE e.decided_at IS NOT NULL AND e.decided_at>=? AND (e.status IN ('sent','rejected')"
        " OR (e.status='withdrawn' AND e.mode='approval'))", (start or "",)).fetchall()
    out = []
    for r in rows:
        automatic = r["status"] == "sent" and r["mode"] != "approval"
        by, reason = r["decider"], r["review_note"]
        if automatic:
            by, reason = None, ("Automatic: passed the training again" if r["mode"] == "retake"
                                else "Automatic: the training needs no approval")
        elif r["status"] == "withdrawn":
            reason = "By the person" if r["decided_by"] == r["user_id"] else "Pass reset"
        out.append(dict(
            key=f"e{r['id']}", kind="extension", id=r["id"], user_id=r["user_id"], person=r["person"],
            what=r["training"], training_id=r["training_id"], outcome="approved" if r["status"] == "sent" else r["status"],
            replaced=False, automatic=automatic, decided_at=r["decided_at"], submitted_at=r["created_at"],
            by=None if automatic else by, by_approver=False, reason=reason,
            rejection=r["review_note"] if r["status"] == "rejected" else None, end_date=r["end_date"],
            previous_end=r["previous_end"], note=r["note"] or None))
    return out


def operation_items(con, start):
    """Teams of outdoor operations that waited for approval and were approved or rejected (decided_by set)."""
    rows = con.execute(
        "SELECT o.*, b.starts_at, b.ends_at, p.name AS place, COALESCE(u.display_name,u.username) AS person,"
        " COALESCE(d.display_name,d.username) AS decider, pr.name AS project FROM operation_teams o"
        " JOIN bookings b ON b.id=o.booking_id JOIN operation_slots s ON s.booking_id=b.id"
        " JOIN places p ON p.id=s.place_id JOIN users u ON u.id=o.pic_id LEFT JOIN users d ON d.id=o.decided_by"
        " LEFT JOIN projects pr ON pr.id=o.project_id"
        " WHERE o.decided_by IS NOT NULL AND o.status IN ('approved','rejected') AND o.decided_at>=?",
        (start or "",)).fetchall()
    return [dict(key=f"o{r['id']}", kind="operation", id=r["id"], user_id=r["pic_id"], person=r["person"],
                 what=r["place"], booking_id=r["booking_id"], outcome=r["status"], replaced=False, automatic=False,
                 decided_at=r["decided_at"], submitted_at=r["created_at"], by=r["decider"], by_approver=False,
                 reason=r["review_note"], rejection=r["review_note"] if r["status"] == "rejected" else None,
                 start=r["starts_at"], end=r["ends_at"], project=r["project"], description=r["goal"] or None)
            for r in rows]


def matches(item, query):
    fields = (item["person"], item["what"], item.get("category"), item["reason"], item["by"], item.get("number"),
              item.get("description"), item.get("briefed_by"), item.get("note"))
    return any(query in value.casefold() for value in fields if value)


def history(con, kind="", query="", period="90d", page=1, bookings=True):
    """(items of the page, total) of the decided approvals, newest decision first."""
    start = since(period)
    items = (qualification_items(con, start) + (booking_items(con, start) if bookings else [])
             + extension_items(con, start) + (operation_items(con, start) if bookings else []))
    query = query.strip().casefold()[:QUERY_MAX]
    items = [i for i in items if (not kind or i["kind"] == kind) and (not query or matches(i, query))]
    items.sort(key=lambda i: (i["decided_at"], i["key"]), reverse=True)
    first = (page - 1) * PAGE_SIZE
    return items[first:first + PAGE_SIZE], len(items)
