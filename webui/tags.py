"""Tags of logbook entries (#368): one list for all projects; anyone who may edit a flight or paper record tags it,
and typing a new name creates the tag. Administrators rename, merge and delete tags and mark one hidden by default:
entries with such a tag are left out of the Logbook, totals and detail pages until Flight filters asks for the tag.

A flight or paper record keeps its tags as a JSON list of tag ids (flights.tags, flight_records.tags), so a re-parse
keeps them like the other edits of a flight; an entry of the logbook has the tags of all its sources.
"""
import json

from flask import abort, g, jsonify, request

NAME_MAX = 40
MAX_PER_ENTRY = 20


class TagError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def clean_name(value):
    """A tag name: 1 to NAME_MAX characters, single spaces, no commas (they separate tags in a URL)."""
    if not isinstance(value, str):
        raise TagError("A tag is text.")
    name = " ".join(value.split())
    if not 1 <= len(name) <= NAME_MAX or "," in name:
        raise TagError(f"A tag has 1 to {NAME_MAX} characters and no commas.")
    return name


def ids_of(raw):
    """The tag ids stored on a flight or paper record."""
    try:
        value = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [i for i in value if isinstance(i, int)] if isinstance(value, list) else []


def dump(ids):
    ids = sorted(set(ids))
    return json.dumps(ids) if ids else None


def resolve(con, names, user_id, stamp):
    """The ids of tag names, creating the ones that do not exist yet (names match without case)."""
    if not isinstance(names, list) or len(names) > MAX_PER_ENTRY:
        raise TagError(f"Send a list of at most {MAX_PER_ENTRY} tags.")
    out = []
    for name in map(clean_name, names):
        row = con.execute("SELECT id FROM tags WHERE name=? COLLATE NOCASE", (name,)).fetchone()
        out.append(row["id"] if row else con.execute(
            "INSERT INTO tags(name, created_by, created_at) VALUES (?,?,?)", (name, user_id, stamp)).lastrowid)
    return out


def known_ids(con, ids):
    if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise TagError("Send a list of tag ids.")
    found = {r["id"] for r in con.execute(f"SELECT id FROM tags WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else set()
    if found != set(ids):
        raise TagError("Unknown tag.", 404)
    return ids


def change(con, table, row_id, add=(), remove=(), replace=None):
    """Set (replace) or add and remove tags of one flight or paper record."""
    row = con.execute(f"SELECT tags FROM {table} WHERE id=?", (row_id,)).fetchone()
    current = set(ids_of(row["tags"])) if row else set()
    new = set(replace) if replace is not None else (current | set(add)) - set(remove)
    if len(new) > MAX_PER_ENTRY:
        raise TagError(f"An entry has at most {MAX_PER_ENTRY} tags.")
    if new != current:
        con.execute(f"UPDATE {table} SET tags=? WHERE id=?", (dump(new), row_id))


def catalog(con):
    """Every tag with the number of flights and paper records that have it."""
    counts = {}
    for table in ("flights", "flight_records"):
        for row in con.execute(f"SELECT tags FROM {table} WHERE tags IS NOT NULL"):
            for i in ids_of(row["tags"]):
                counts[i] = counts.get(i, 0) + 1
    return [dict(id=r["id"], name=r["name"], hidden=bool(r["hidden"]), entries=counts.get(r["id"], 0))
            for r in con.execute("SELECT id, name, hidden FROM tags ORDER BY name COLLATE NOCASE")]


def names(con):
    """id -> (name, hidden), for the logbook read model."""
    return {r["id"]: (r["name"], bool(r["hidden"])) for r in con.execute("SELECT id, name, hidden FROM tags")}


def apply(rows, source_tags, paper_tags, by_id):
    """Give every logbook entry tags (names, sorted) and tags_hidden (its tags that are hidden by default): the tags of
    all its logged sources and paper records."""
    for r in rows:
        if r["source_type"] == "paper":
            ids = set(paper_tags.get(r["manual_id"], ()))
        else:
            ids = {i for s in r["sources"] for i in source_tags.get(s["flight_id"], ())}
            ids |= {i for p in r.get("linked_records") or [] for i in paper_tags.get(p["id"], ())}
        known = [by_id[i] for i in ids if i in by_id]
        r["tags"] = sorted((name for name, _ in known), key=str.casefold)
        r["tags_hidden"] = sorted((name for name, hidden in known if hidden), key=str.casefold)


def of_logs(con, log_ids):
    """{log id: (tag names, names of those hidden by default)}, both sorted: the tags given with its uploads
    (logs.upload_tags) and those of its flights (#396)."""
    if not log_ids:
        return {}
    by_id = names(con)
    found = {}
    marks = ",".join("?" * len(log_ids))
    for r in con.execute(f"SELECT id, upload_tags FROM logs WHERE id IN ({marks})", list(log_ids)):
        found.setdefault(r["id"], set()).update(ids_of(r["upload_tags"]))
    for r in con.execute(f"SELECT a.log_id, f.tags FROM flights f JOIN aircraft a ON a.id=f.aircraft_id"
                         f" WHERE a.log_id IN ({marks}) AND f.tags IS NOT NULL", list(log_ids)):
        found.setdefault(r["log_id"], set()).update(ids_of(r["tags"]))
    out = {}
    for log_id, tag_ids in found.items():
        known = [by_id[i] for i in tag_ids if i in by_id]
        out[log_id] = (sorted((n for n, _ in known), key=str.casefold), sorted((n for n, h in known if h), key=str.casefold))
    return out


def _rewrite(con, old_id, new_id=None):
    """Replace (merge) or drop (delete) a tag id on every flight, paper record and log (its upload tags)."""
    for table, column in (("flights", "tags"), ("flight_records", "tags"), ("logs", "upload_tags")):
        for row in con.execute(f"SELECT id, {column} AS tags FROM {table} WHERE {column} IS NOT NULL").fetchall():
            ids = ids_of(row["tags"])
            if old_id in ids:
                ids = [i for i in ids if i != old_id] + ([new_id] if new_id is not None else [])
                con.execute(f"UPDATE {table} SET {column}=? WHERE id=?", (dump(ids), row["id"]))


def register(app, connection, write_lock, require_admin):
    @app.get("/api/tags")
    def list_tags():
        return jsonify(tags=catalog(connection()), can_manage=bool(g.user["is_admin"]))

    @app.route("/api/admin/tags/<int:tag_id>", methods=["PUT", "DELETE"])
    def admin_tag(tag_id):
        """Rename a tag or set Hidden by default (PUT {name, hidden}); DELETE removes it from every entry."""
        require_admin()
        con = connection()
        try:
            with write_lock, con:
                con.execute("BEGIN IMMEDIATE")
                if not con.execute("SELECT 1 FROM tags WHERE id=?", (tag_id,)).fetchone():
                    abort(404)
                if request.method == "DELETE":
                    _rewrite(con, tag_id)
                    con.execute("DELETE FROM tags WHERE id=?", (tag_id,))
                    return jsonify(ok=True)
                body = request.get_json(silent=True) or {}
                if "name" in body:
                    name = clean_name(body["name"])
                    other = con.execute("SELECT id FROM tags WHERE name=? COLLATE NOCASE AND id!=?", (name, tag_id)).fetchone()
                    if other:
                        raise TagError("Another tag has that name: merge them instead.", 409)
                    con.execute("UPDATE tags SET name=? WHERE id=?", (name, tag_id))
                if "hidden" in body:
                    if type(body["hidden"]) is not bool:
                        raise TagError("hidden must be true or false.")
                    con.execute("UPDATE tags SET hidden=? WHERE id=?", (int(body["hidden"]), tag_id))
            return jsonify(ok=True)
        except TagError as exc:
            return jsonify(error=str(exc)), exc.status

    @app.post("/api/admin/tags/<int:tag_id>/merge")
    def admin_merge_tag(tag_id):
        """Merge this tag into another ({"into": id}): its entries get the other tag, and this one is removed."""
        require_admin()
        con = connection()
        into = (request.get_json(silent=True) or {}).get("into")
        try:
            with write_lock, con:
                con.execute("BEGIN IMMEDIATE")
                if into == tag_id:
                    raise TagError("Choose another tag to merge into.")
                known_ids(con, [tag_id, into] if isinstance(into, int) else [tag_id, -1])
                _rewrite(con, tag_id, into)
                con.execute("DELETE FROM tags WHERE id=?", (tag_id,))
            return jsonify(ok=True)
        except TagError as exc:
            return jsonify(error=str(exc)), exc.status
