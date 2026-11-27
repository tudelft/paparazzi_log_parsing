"""Who may choose an operator, a category or an authorisation for flights: in Plan flight (a planned slot), in Edit
flight and on paper records.

Each operator, authorisation and category may list qualification types (choice_qualifications); then only holders of
a current approved qualification of one of them may choose it, and nothing listed means everyone. An authorisation
also needs the rule of its category. Our own operator, OPEN and "nothing set" (the defaults) are free for everyone, and
administrators may always choose. A choice must be allowed for the person who makes it and for the flight's PIC."""

import bookings
import categories

KINDS = ("operator", "authorisation", "category")
FREE_CATEGORIES = ("open",)


class ChoiceError(ValueError):
    def __init__(self, message, status=403):
        super().__init__(message)
        self.status = status


def rules(con):
    """{(kind, ref): [type ids]} of every rule."""
    out = {}
    for r in con.execute("SELECT kind, ref, type_id FROM choice_qualifications ORDER BY kind, ref, type_id"):
        out.setdefault((r["kind"], r["ref"]), []).append(r["type_id"])
    return out


def types_of(con, kind, ref):
    return [r[0] for r in con.execute("SELECT type_id FROM choice_qualifications WHERE kind=? AND ref=? ORDER BY type_id",
                                      (kind, str(ref)))]


def parse_types(con, raw):
    """Qualification type ids from a request (a list); raises ChoiceError."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 50:
        raise ChoiceError("qualification_types must be a list of qualification type ids.", 400)
    out = []
    for value in raw:
        if type(value) is not int or not con.execute("SELECT 1 FROM qualification_types WHERE id=?", (value,)).fetchone():
            raise ChoiceError("Choose existing qualification types.", 400)
        if value not in out:
            out.append(value)
    return out


def set_types(con, kind, ref, type_ids):
    con.execute("DELETE FROM choice_qualifications WHERE kind=? AND ref=?", (kind, str(ref)))
    con.executemany("INSERT INTO choice_qualifications (kind, ref, type_id) VALUES (?,?,?)",
                    [(kind, str(ref), t) for t in type_ids])


def forget(con, kind, ref):
    con.execute("DELETE FROM choice_qualifications WHERE kind=? AND ref=?", (kind, str(ref)))


def needs(con, operator_id=None, category=None, authorisation_id=None):
    """The qualification-type lists a choice needs (each list: one of these types); [] when it is free."""
    lists = []
    if operator_id is not None and not con.execute("SELECT 1 FROM operators WHERE id=? AND own=1", (operator_id,)).fetchone():
        lists.append(types_of(con, "operator", operator_id))
    if authorisation_id is not None:
        lists.append(types_of(con, "authorisation", authorisation_id))
        row = con.execute("SELECT category FROM authorisations WHERE id=?", (authorisation_id,)).fetchone()
        category = row["category"] if row else category
    if category is not None and category not in FREE_CATEGORIES:
        lists.append(types_of(con, "category", category))
    return [types for types in lists if types]


def holds(con, user_id, types):
    return any(bookings.has_qualification(con, user_id, t) for t in types)


def person(con, user_id):
    return con.execute("SELECT id, is_admin, COALESCE(display_name, username) AS name FROM users WHERE id=?",
                       (user_id,)).fetchone()


def check(con, chooser, pic_id, operator_id=None, category=None, authorisation_id=None):
    """Raise ChoiceError unless the chooser (a users row) and the PIC (id or None) may make this choice; an
    administrator choosing needs nothing."""
    if chooser["is_admin"]:
        return
    lists = needs(con, operator_id, category, authorisation_id)
    if not lists:
        return
    people = [(chooser["id"], "You")]
    if pic_id is not None and pic_id != chooser["id"]:
        pic = person(con, pic_id)
        if pic is not None:
            people.append((pic_id, f"The PIC ({pic['name']})"))
    names = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM qualification_types")}
    for uid, who in people:
        for types in lists:
            if not holds(con, uid, types):
                what = " or ".join(names.get(t, str(t)) for t in types)
                raise ChoiceError(f"{who} need{'s' if who != 'You' else ''} a current {what} qualification for this"
                                  " operator or category.")


def allowed(con, user):
    """What this person may choose (for the selects): {"operators": {id: bool}, "authorisations": {id: bool},
    "categories": {key: bool}}."""
    admin = bool(user["is_admin"])
    ok = lambda **choice: admin or all(holds(con, user["id"], t) for t in needs(con, **choice))  # noqa: E731
    return {"operators": {r[0]: ok(operator_id=r[0]) for r in con.execute("SELECT id FROM operators")},
            "authorisations": {r[0]: ok(authorisation_id=r[0]) for r in con.execute("SELECT id FROM authorisations")},
            "categories": {key: ok(category=key) for key in categories.CATEGORIES}}
