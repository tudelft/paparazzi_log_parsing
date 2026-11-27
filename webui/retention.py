"""Flight records are kept for LOGDB_RETENTION_YEARS (#313).

Regulation (EU) 2019/947, Annex, UAS.SPEC.050(1)(g): records of the operations are kept for at least 3 years. A log
with a real flight (not a ground run or a simulation) or a paper record younger than that cannot be deleted; hide its
entries instead. An administrator may still delete one with a reason (a mistaken upload, a privacy request): the
reason, who and when, and a snapshot of the record are kept in deleted_records, which cannot be changed or emptied.
Logs without real flights stay deletable.
"""
import json
from datetime import date, datetime, timezone

REASON_MIN, REASON_MAX = 5, 1000


class RetentionError(ValueError):
    def __init__(self, message, status, kept_until=None):
        super().__init__(message)
        self.status = status
        self.kept_until = kept_until


def kept_until(day, years):
    """The last day a record of `day` (YYYY-MM-DD) must be kept, or None when the period is off or the day unknown."""
    if years <= 0 or not day:
        return None
    try:
        d = date.fromisoformat(day[:10])
    except ValueError:
        return None
    try:
        until = d.replace(year=d.year + years)
    except ValueError:  # 29 February
        until = d.replace(year=d.year + years, day=28)
    return until.isoformat()


def still_kept(until):
    return bool(until) and until >= datetime.now(timezone.utc).date().isoformat()


def while_kept(until):
    """`until` while the record is still kept, else None (what the pages show)."""
    return until if still_kept(until) else None


def check_delete(until, user, body):
    """The reason for deleting a record kept until `until`, or None when it may simply be deleted; RetentionError when
    it may not: only an administrator, with a reason."""
    if not still_kept(until):
        return None
    if not user["is_admin"]:
        raise RetentionError(f"Flight records are kept until {until}: hide the entry instead, or ask an administrator.",
                             409, until)
    reason = (body or {}).get("reason") if isinstance(body, dict) else None
    reason = reason.strip() if isinstance(reason, str) else ""
    if not REASON_MIN <= len(reason) <= REASON_MAX:
        raise RetentionError(f"This record is kept until {until}: give the reason for deleting it "
                             f"({REASON_MIN} to {REASON_MAX} characters).", 400, until)
    return reason


def record_deletion(con, kind, record_id, label, snapshot, reason, user, stamp):
    con.execute(
        "INSERT INTO deleted_records(kind, record_id, label, snapshot_json, reason, deleted_by, deleted_by_name,"
        " deleted_at) VALUES (?,?,?,?,?,?,?,?)",
        (kind, record_id, label, json.dumps(snapshot, default=str, sort_keys=True), reason, user["id"],
         user["display_name"] or user["username"], stamp))


def deleted(con):
    return [dict(r, snapshot=json.loads(r["snapshot_json"])) for r in con.execute(
        "SELECT id, kind, record_id, label, snapshot_json, reason, deleted_by_name, deleted_at FROM deleted_records"
        " ORDER BY id DESC")]
