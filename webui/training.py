"""In-app training questionnaires. Off unless LOGDB_TRAINING=1.

A pass is stored with the attempt count and can grant a qualification immediately, or queue it
for admin approval when the training has needs_approval set. Failed answers are not kept.
People may retry until they pass; after a pass they may retake when the linked qualification
has expired, a queued pass was rejected, or an administrator resets their progress.
Questionnaires are created in Edit mode or imported from a JSON/ZIP file kept outside the
repository — nothing is seeded at startup.
"""
import datetime
import io
import json
import random
import sqlite3
import uuid
import zipfile
from pathlib import Path

from flask import abort, g, jsonify, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

import db
import qualifications
from config import env_bool

KINDS = ("single", "multi", "ranking")
MAX_IMAGE_BYTES = 2 * 1024 * 1024
IMAGE_TYPES = {
    ".png": ("image/png", b"\x89PNG\r\n\x1a\n"),
    ".jpg": ("image/jpeg", b"\xff\xd8\xff"),
    ".jpeg": ("image/jpeg", b"\xff\xd8\xff"),
}


class TrainingError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def enabled():
    return env_bool("TRAINING")


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_date():
    return datetime.datetime.now(datetime.timezone.utc).date()


def require_enabled():
    if not enabled():
        abort(404)


def answer_correct(kind, choices, answer):
    """True when the submitted answer matches the question's key."""
    ordered = sorted(choices, key=lambda c: (c["sort_order"], c["id"]))
    if kind == "single":
        if not isinstance(answer, int):
            return False
        return any(c["id"] == answer and c["correct"] for c in choices)
    if kind == "multi":
        if not isinstance(answer, list) or any(type(x) is not int for x in answer):
            return False
        want = {c["id"] for c in choices if c["correct"]}
        return set(answer) == want
    if kind == "ranking":
        if not isinstance(answer, list) or any(type(x) is not int for x in answer):
            return False
        return answer == [c["id"] for c in ordered]
    return False


def score_answers(questions, choices_by_q, answers):
    earned = total = 0
    for q in questions:
        total += q["points"]
        raw = answers.get(q["id"], answers.get(str(q["id"])))
        if answer_correct(q["kind"], choices_by_q.get(q["id"], []), raw):
            earned += q["points"]
    return earned, total


def load_questions(con, training_id):
    questions = [dict(r) for r in con.execute(
        "SELECT id, prompt, kind, image_path, image_mime, points, sort_order FROM training_questions"
        " WHERE training_id=? ORDER BY sort_order, id", (training_id,))]
    choices = {}
    for q in questions:
        choices[q["id"]] = [dict(r) for r in con.execute(
            "SELECT id, label, correct, sort_order FROM training_choices WHERE question_id=? ORDER BY sort_order, id",
            (q["id"],))]
    return questions, choices


def briefer_list(con, training_id):
    return [{"id": r["id"], "name": r["name"]} for r in con.execute(
        "SELECT u.id, COALESCE(u.display_name,u.username) AS name FROM training_briefers b"
        " JOIN users u ON u.id=b.user_id WHERE b.training_id=? AND u.is_active=1 AND u.is_external=0"
        " ORDER BY name, u.username", (training_id,))]


def parse_briefers(con, ask, raw):
    if not ask:
        return []
    if not isinstance(raw, list) or not (1 <= len(raw) <= 20):
        raise TrainingError("Choose who can deliver this briefing.")
    ids = []
    for item in raw:
        if type(item) is not int or isinstance(item, bool) or item <= 0 or item in ids:
            raise TrainingError("Choose who can deliver this briefing.")
        ids.append(item)
    found = {r[0] for r in con.execute(
        "SELECT id FROM users WHERE is_active=1 AND is_external=0 AND id IN ("
        + ",".join("?" * len(ids)) + ")", ids)}
    if found != set(ids):
        raise TrainingError("Choose who can deliver this briefing.")
    return ids


def save_briefers(con, training_id, ids):
    con.execute("DELETE FROM training_briefers WHERE training_id=?", (training_id,))
    con.executemany("INSERT INTO training_briefers(training_id,user_id) VALUES (?,?)",
                    [(training_id, uid) for uid in ids])


def public_questions(questions, choices_by_q, draw=None):
    """Questions for a taker: no keys, choices shuffled (ranking too). With a draw (see new_draw) the questions,
    their order and the answer order are the attempt's, so they stay the same until it is submitted."""
    if draw:
        by_id = {q["id"]: q for q in questions}
        questions = [by_id[i] for i in draw["questions"]]
    out = []
    for q in questions:
        opts = [{"id": c["id"], "label": c["label"]} for c in choices_by_q.get(q["id"], [])]
        if draw:
            order = {cid: i for i, cid in enumerate(draw["choices"][str(q["id"])])}
            opts.sort(key=lambda o: order[o["id"]])
        else:
            random.shuffle(opts)
        out.append({"id": q["id"], "prompt": q["prompt"], "kind": q["kind"], "points": q["points"],
                    "image": bool(q["image_path"]), "choices": opts})
    return out


def new_draw(row, questions, choices_by_q):
    """One attempt: the questions asked (all, or question_count of them drawn at random), in the admin's order or
    shuffled, and a shuffled answer order per question."""
    ids = [q["id"] for q in questions]
    count = row["question_count"]
    if count and count < len(ids):
        keep = set(random.sample(ids, count))
        ids = [i for i in ids if i in keep]
    if row["shuffle_questions"]:
        random.shuffle(ids)
    order = {}
    for qid in ids:
        cids = [c["id"] for c in choices_by_q.get(qid, [])]
        random.shuffle(cids)
        order[str(qid)] = cids
    return {"questions": ids, "choices": order}


def valid_draw(raw, questions, choices_by_q):
    """The stored draw when it still matches the training's questions and answers, else None."""
    try:
        draw = json.loads(raw) if raw else None
        by_id = {q["id"]: q for q in questions}
        if not draw or not draw["questions"] or any(i not in by_id for i in draw["questions"]):
            return None
        for qid in draw["questions"]:
            if sorted(draw["choices"][str(qid)]) != sorted(c["id"] for c in choices_by_q.get(qid, [])):
                return None
        return draw
    except (TypeError, ValueError, KeyError):
        return None


def needed_points(row, asked, questions):
    """Points needed to pass. The pass score is set against all questions; when an attempt asks only some of them,
    it is scaled to the points of those (rounded up). Without a pass score every asked point is needed."""
    asked_total = sum(q["points"] for q in asked)
    if row["pass_score"] is None:
        return asked_total
    all_total = sum(q["points"] for q in questions)
    if asked_total == all_total or not all_total:
        return row["pass_score"]
    return min(asked_total, max(1, -(-row["pass_score"] * asked_total // all_total)))


def parse_answers(body, questions):
    if not isinstance(body, dict) or not isinstance(body.get("answers"), dict):
        raise TrainingError("Send answers as an object keyed by question id.")
    raw = body["answers"]
    answers = {}
    known = {q["id"] for q in questions}
    for key, value in raw.items():
        if not str(key).isdigit() or int(key) not in known:
            raise TrainingError("Unknown question in answers.")
        qid = int(key)
        if isinstance(value, bool) or isinstance(value, float):
            raise TrainingError("Each answer must be a choice id or a list of choice ids.")
        if isinstance(value, int):
            answers[qid] = value
        elif isinstance(value, list):
            if any(type(x) is not int or isinstance(x, bool) or x <= 0 for x in value):
                raise TrainingError("Each answer must be a choice id or a list of choice ids.")
            answers[qid] = value
        else:
            raise TrainingError("Each answer must be a choice id or a list of choice ids.")
    return answers


def clip_text(raw, max_len, label):
    if not isinstance(raw, str):
        raise TrainingError(f"{label} must be text.")
    text = raw.strip()
    if not text or len(text) > max_len:
        raise TrainingError(f"{label} must be 1-{max_len} characters.")
    return text


def optional_int(raw, label, minimum=1):
    if raw in (None, ""):
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < minimum:
        raise TrainingError(f"{label} must be an integer ≥ {minimum}, or empty.")
    return raw


def parse_question_body(body, index=None):
    """Validate one question dict (edit API or import). Returns prompt, kind, points, choices."""
    where = f"Question {index}: " if index is not None else ""
    if not isinstance(body, dict):
        raise TrainingError(f"{where}each question must be an object.")
    try:
        prompt = clip_text(body.get("prompt"), 2000, "Question")
    except TrainingError as exc:
        raise TrainingError(f"{where}{exc}") from exc
    kind = body.get("kind")
    if kind not in KINDS:
        raise TrainingError(f"{where}kind must be single, multi or ranking.")
    points = body.get("points", 1)
    if type(points) is not int or points < 1 or points > 100:
        raise TrainingError(f"{where}points must be 1-100.")
    raw_choices = body.get("choices")
    if not isinstance(raw_choices, list) or len(raw_choices) < 2 or len(raw_choices) > 20:
        raise TrainingError(f"{where}needs 2-20 choices.")
    choices = []
    for i, item in enumerate(raw_choices):
        if not isinstance(item, dict):
            raise TrainingError(f"{where}each choice needs a label.")
        try:
            label = clip_text(item.get("label"), 400, "Choice")
        except TrainingError as exc:
            raise TrainingError(f"{where}{exc}") from exc
        correct = bool(item.get("correct")) if kind != "ranking" else False
        choices.append((label, correct, i))
    if kind == "single" and sum(c[1] for c in choices) != 1:
        raise TrainingError(f"{where}a single-choice question needs exactly one correct answer.")
    if kind == "multi" and sum(c[1] for c in choices) < 1:
        raise TrainingError(f"{where}a multiple-choice question needs at least one correct answer.")
    image_name = body.get("image")
    if image_name in (None, ""):
        image_name = None
    elif not isinstance(image_name, str) or not image_name.strip():
        raise TrainingError(f"{where}image must be a file name inside the ZIP.")
    else:
        image_name = image_name.strip().replace("\\", "/")
        if image_name.startswith("/") or ".." in Path(image_name).parts:
            raise TrainingError(f"{where}image path is not allowed.")
    return prompt, kind, points, choices, image_name


MAX_JSON_BYTES = 1 * 1024 * 1024


def check_image_bytes(name, data):
    suffix = Path(name).suffix.lower()
    if suffix not in IMAGE_TYPES:
        raise TrainingError(f"Question images must be JPEG or PNG ({name}).")
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise TrainingError(f"Question images must be non-empty and at most 2 MiB ({name}).")
    mime, magic = IMAGE_TYPES[suffix]
    if not data.startswith(magic):
        raise TrainingError(f"Question images must be JPEG or PNG with matching file content ({name}).")
    return mime, suffix


def check_pass_score(pass_score, total_points):
    if pass_score is not None and total_points > 0 and pass_score > total_points:
        raise TrainingError(f"Pass score ({pass_score}) is higher than total points ({total_points}).")


def validity_window(kind, valid_days):
    start = utc_date().isoformat()
    until = (utc_date() + datetime.timedelta(days=valid_days)).isoformat() if valid_days else None
    if kind["requires_expiry"] and until is None:
        raise TrainingError("This training must set validity days because the qualification expires.")
    return start, until


def pass_note(training_name, earned, needed, briefed_by_name=None, when=None):
    """Short label stored on the qualification submission for a training pass."""
    day = (when or utc_now())[:10]
    text = f'Training "{training_name}" passed {earned}/{needed} on {day}'
    if briefed_by_name:
        text += f" · briefed by {briefed_by_name}"
    return text[:300]


def grant_qualification(con, user_id, kind, valid_days, note=None):
    """Insert an approved qualification for a training pass. Returns the submission id or None."""
    if not kind:
        return None
    start, until = validity_window(kind, valid_days)
    con.execute("UPDATE qualification_submissions SET status='withdrawn'"
                " WHERE user_id=? AND type_id=? AND status='pending'", (user_id, kind["id"]))
    con.execute("UPDATE qualification_submissions SET status='superseded'"
                " WHERE user_id=? AND type_id=? AND status='approved'", (user_id, kind["id"]))
    now = utc_now()
    sid = con.execute(
        "INSERT INTO qualification_submissions(user_id,type_id,valid_from,valid_until,status,submitted_by,"
        " submitted_at,reviewed_by,reviewed_at,note) VALUES (?,?,?,?,'approved',?,?,?,?,?)",
        (user_id, kind["id"], start, until, user_id, now, user_id, now, note)).lastrowid
    qualifications.audit(con, user_id, user_id, "training-passed",
                         dict(id=sid, category=kind["name"], valid_from=start, valid_until=until, note=note))
    return sid


def queue_qualification(con, user_id, kind, valid_days, note):
    """Insert a pending qualification for a training pass that needs approval."""
    if not kind:
        raise TrainingError("A pass that needs approval must link to a qualification.")
    start, until = validity_window(kind, valid_days)
    con.execute("UPDATE qualification_submissions SET status='withdrawn'"
                " WHERE user_id=? AND type_id=? AND status='pending'", (user_id, kind["id"]))
    now = utc_now()
    sid = con.execute(
        "INSERT INTO qualification_submissions(user_id,type_id,valid_from,valid_until,status,submitted_by,"
        " submitted_at,note) VALUES (?,?,?,?,'pending',?,?,?)",
        (user_id, kind["id"], start, until, user_id, now, note[:300] if note else None)).lastrowid
    qualifications.audit(con, user_id, user_id, "training-pass-queued",
                         dict(id=sid, category=kind["name"], valid_from=start, valid_until=until, note=note))
    return sid


def clear_progress_for_submission(con, submission_id):
    """Clear a queued training pass so the person can retake after reject or withdraw."""
    if not submission_id:
        return
    con.execute(
        "UPDATE training_progress SET passed_at=NULL, answers_json=NULL, submission_id=NULL, briefed_by=NULL"
        " WHERE submission_id=?", (submission_id,))


def submission_status(con, progress):
    if not progress or not progress["submission_id"]:
        return None
    row = con.execute("SELECT status, valid_until FROM qualification_submissions WHERE id=?",
                      (progress["submission_id"],)).fetchone()
    return dict(row) if row else None


def pass_is_current(con, user_id, training_row, progress):
    """True when the person still holds a valid approved pass (blocks retake)."""
    if not progress or not progress["passed_at"]:
        return False
    today = utc_date().isoformat()
    sub = submission_status(con, progress)
    if progress["submission_id"]:
        if sub is None or sub["status"] != "approved":
            return False
        if sub["valid_until"] and sub["valid_until"] < today:
            return False
        return True
    if training_row["type_id"]:
        current = con.execute(
            "SELECT 1 FROM qualification_submissions WHERE user_id=? AND type_id=? AND status='approved'"
            " AND (valid_until IS NULL OR valid_until>=?)",
            (user_id, training_row["type_id"], today)).fetchone()
        return current is not None
    return True


def pass_awaiting_approval(con, progress):
    """True when a pass is waiting in the qualification approval queue."""
    if not progress or not progress["passed_at"]:
        return False
    sub = submission_status(con, progress)
    return bool(sub and sub["status"] == "pending")


def register(app, connection, root, require_session):
    app.config["TRAINING_IMAGE_DIR"] = Path(root) / "training-images"

    def con():
        return connection()

    def admin():
        require_session()
        require_enabled()
        if not g.user["is_admin"]:
            raise TrainingError("Administrator access required.", 403)

    def signed_in():
        require_session()
        require_enabled()

    def image_dir():
        folder = Path(app.config["TRAINING_IMAGE_DIR"])
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        return folder

    def training_row(cid, training_id, active_only=False):
        sql = "SELECT * FROM trainings WHERE id=?"
        params = [training_id]
        if active_only:
            sql += " AND active=1"
        row = cid.execute(sql, params).fetchone()
        if row is None:
            raise TrainingError("No such training.", 404)
        return row

    def linkable_types(cid):
        return [{"id": r["id"], "name": r["name"], "requires_expiry": bool(r["requires_expiry"])}
                for r in cid.execute(
                    "SELECT id, name, requires_expiry FROM qualification_types"
                    " WHERE active=1 AND requires_evidence=0 ORDER BY sort_order, name")]

    def resolve_type(cid, type_id, valid_days):
        if not type_id:
            return None
        kind = cid.execute(
            "SELECT id, name, requires_expiry, requires_evidence, active FROM qualification_types WHERE id=?",
            (type_id,)).fetchone()
        if kind is None:
            raise TrainingError("No such qualification type.", 404)
        if kind["requires_evidence"]:
            raise TrainingError("Trainings cannot link to a type that requires evidence.")
        if kind["requires_expiry"] and valid_days is None:
            raise TrainingError("This qualification expires, so set validity days.")
        return kind

    def payload_list(cid, user):
        mine = {r["training_id"]: dict(r) for r in cid.execute(
            "SELECT training_id, attempts, passed_at, submission_id FROM training_progress WHERE user_id=?",
            (user["id"],))}
        items = []
        for row in cid.execute("SELECT t.*, qt.name AS type_name FROM trainings t"
                               " LEFT JOIN qualification_types qt ON qt.id=t.type_id ORDER BY t.sort_order, t.name"):
            if not row["active"] and not user["is_admin"]:
                continue
            progress = mine.get(row["id"]) or {}
            prog = progress or None
            current = pass_is_current(cid, user["id"], row, prog)
            awaiting = pass_awaiting_approval(cid, prog)
            items.append({
                "id": row["id"], "name": row["name"], "description": row["description"],
                "type_id": row["type_id"], "type_name": row["type_name"],
                "pass_score": row["pass_score"], "valid_days": row["valid_days"],
                "ask_briefed_by": bool(row["ask_briefed_by"]),
                "needs_approval": bool(row["needs_approval"]),
                "shuffle_questions": bool(row["shuffle_questions"]), "question_count": row["question_count"],
                "active": bool(row["active"]),
                "passed_at": progress.get("passed_at") if current else None,
                "awaiting_approval": awaiting,
                "expired_pass": bool(progress.get("passed_at") and not current and not awaiting),
                "attempts": progress.get("attempts") or 0,
            })
        return items

    def delete_training_images(cid, training_id):
        for q in cid.execute("SELECT image_path FROM training_questions WHERE training_id=?", (training_id,)):
            if q["image_path"]:
                (image_dir() / q["image_path"]).unlink(missing_ok=True)

    def insert_questions(cid, training_id, parsed_questions, image_blobs, written):
        """Insert questions; new image files are appended to written so a failed import can remove them."""
        for i, (prompt, kind, points, choices, image_name) in enumerate(parsed_questions):
            qid = cid.execute(
                "INSERT INTO training_questions(training_id,prompt,kind,points,sort_order) VALUES (?,?,?,?,?)",
                (training_id, prompt, kind, points, i)).lastrowid
            if image_name:
                data = image_blobs[image_name]
                mime, suffix = check_image_bytes(image_name, data)
                stored = uuid.uuid4().hex + suffix
                (image_dir() / stored).write_bytes(data)
                written.append(stored)
                cid.execute("UPDATE training_questions SET image_path=?, image_mime=? WHERE id=?",
                            (stored, mime, qid))
            cid.executemany(
                "INSERT INTO training_choices(question_id,label,correct,sort_order) VALUES (?,?,?,?)",
                [(qid, label, int(correct), order) for label, correct, order in choices])

    def read_zip_json(zf):
        names = [n for n in zf.namelist() if not n.endswith("/") and not Path(n).name.startswith(".")]
        json_names = [n for n in names if n.lower().endswith(".json")]
        if len(json_names) != 1:
            raise TrainingError("A ZIP must contain exactly one JSON questionnaire file.")
        info = zf.getinfo(json_names[0])
        if info.file_size > MAX_JSON_BYTES:
            raise TrainingError("The questionnaire JSON must be at most 1 MiB.")
        try:
            data = json.loads(zf.read(json_names[0]).decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise TrainingError("The questionnaire JSON must be UTF-8.") from exc
        except json.JSONDecodeError as exc:
            raise TrainingError("The questionnaire JSON is not valid.") from exc
        return data, names, json_names[0]

    def read_import_payload():
        uploaded = request.files.get("file")
        if uploaded is None or not uploaded.filename:
            raise TrainingError("Choose a JSON or ZIP file.")
        filename = secure_filename(uploaded.filename)[:160] or "training.json"
        raw = uploaded.read()
        if not raw:
            raise TrainingError("The import file is empty.")
        images = {}
        suffix = Path(filename).suffix.lower()
        if suffix == ".zip" or raw[:2] == b"PK":
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                    data, names, json_name = read_zip_json(zf)
                # Image bytes are filled after parse_import knows which names are referenced.
                return data, {"_raw": raw, "_names": names, "_json_name": json_name}
            except zipfile.BadZipFile as exc:
                raise TrainingError("The ZIP file is not valid.") from exc
        if len(raw) > MAX_JSON_BYTES:
            raise TrainingError("The questionnaire JSON must be at most 1 MiB.")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TrainingError("The import file must be UTF-8 JSON or a ZIP containing one JSON file.") from exc
        return data, images

    def load_referenced_images(images, needed):
        """Read only referenced image entries; check ZipInfo.file_size before loading."""
        if "_raw" not in images:
            return images
        raw = images["_raw"]
        names = images["_names"]
        json_name = images["_json_name"]
        out = {}
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            by_rel = {n.replace("\\", "/"): n for n in names if n != json_name}
            by_base = {}
            for rel, original in by_rel.items():
                by_base.setdefault(Path(rel).name, original)
            for wanted in needed:
                original = by_rel.get(wanted) or by_base.get(Path(wanted).name)
                if original is None:
                    continue
                info = zf.getinfo(original)
                if info.file_size > MAX_IMAGE_BYTES:
                    raise TrainingError(f"Question images must be non-empty and at most 2 MiB ({wanted}).")
                if info.file_size <= 0:
                    raise TrainingError(f"Question images must be non-empty and at most 2 MiB ({wanted}).")
                entry = zf.read(original)
                rel = original.replace("\\", "/")
                out[rel] = entry
                out[Path(rel).name] = entry
                out[wanted] = entry
        return out

    def parse_import(cid, data, images, type_id):
        if not isinstance(data, dict):
            raise TrainingError("The questionnaire JSON must be an object.")
        unknown = set(data) - {"name", "description", "pass_score", "valid_days", "ask_briefed_by",
                               "needs_approval", "questions", "sort_order", "qualification"}
        if "qualification" in data:
            raise TrainingError("Do not put a qualification name in the file; pick an existing type when importing.")
        if unknown:
            raise TrainingError("Unknown fields: " + ", ".join(sorted(unknown)) + ".")
        name = clip_text(data.get("name"), 80, "Name")
        description = (data.get("description") or "").strip()
        if len(description) > 2000:
            raise TrainingError("Description must be at most 2000 characters.")
        pass_score = optional_int(data.get("pass_score"), "Pass score")
        valid_days = optional_int(data.get("valid_days"), "Validity days")
        if "ask_briefed_by" in data and type(data.get("ask_briefed_by")) is not bool:
            raise TrainingError("Ask who briefed must be true or false.")
        ask_briefed_by = bool(data.get("ask_briefed_by", False))
        if "needs_approval" in data and type(data.get("needs_approval")) is not bool:
            raise TrainingError("A pass needs approval must be true or false.")
        needs_approval = bool(data.get("needs_approval", False))
        if needs_approval and not type_id:
            raise TrainingError("A pass that needs approval must link to a qualification.")
        resolve_type(cid, type_id, valid_days)
        raw_questions = data.get("questions")
        if not isinstance(raw_questions, list):
            raise TrainingError("Questions must be a list.")
        if len(raw_questions) > 200:
            raise TrainingError("A training can have at most 200 questions.")
        parsed = []
        needed_images = set()
        for i, item in enumerate(raw_questions, start=1):
            prompt, kind, points, choices, image_name = parse_question_body(item, index=i)
            if image_name:
                needed_images.add(image_name)
            parsed.append((prompt, kind, points, choices, image_name))
        check_pass_score(pass_score, sum(p[2] for p in parsed))
        images = load_referenced_images(images, needed_images)
        missing = sorted(n for n in needed_images if n not in images and Path(n).name not in images)
        if missing:
            raise TrainingError("Missing image file(s): " + ", ".join(missing) + ".")
        for name_key in needed_images:
            blob = images.get(name_key) or images.get(Path(name_key).name)
            check_image_bytes(name_key, blob)
        existing = cid.execute("SELECT id FROM trainings WHERE name=?", (name,)).fetchone()
        return {
            "name": name, "description": description, "pass_score": pass_score, "valid_days": valid_days,
            "ask_briefed_by": ask_briefed_by, "needs_approval": needs_approval, "type_id": type_id,
            "questions": parsed, "existing_id": existing["id"] if existing else None,
            "question_count": len(parsed),
            "image_count": len(needed_images),
            "images": images,
        }

    @app.get("/training")
    def training_page():
        require_enabled()
        return send_from_directory(Path(__file__).parent / "static", "training.html")

    @app.get("/api/trainings")
    def list_trainings():
        signed_in()
        cid = con()
        out = {"trainings": payload_list(cid, g.user)}
        if g.user["is_admin"]:
            out["types"] = linkable_types(cid)
        return jsonify(out)

    @app.get("/api/trainings/<int:training_id>")
    def get_training(training_id):
        signed_in()
        cid = con()
        row = training_row(cid, training_id, active_only=not g.user["is_admin"])
        questions, choices = load_questions(cid, training_id)
        progress = cid.execute("SELECT attempts, passed_at, briefed_by, submission_id, answers_json FROM training_progress"
                               " WHERE user_id=? AND training_id=?",
                               (g.user["id"], training_id)).fetchone()
        current = pass_is_current(cid, g.user["id"], row, progress)
        awaiting = pass_awaiting_approval(cid, progress)
        briefer = None
        if progress and progress["briefed_by"] and (current or awaiting):
            briefer = cid.execute("SELECT COALESCE(display_name, username) AS name FROM users WHERE id=?",
                                  (progress["briefed_by"],)).fetchone()
        pass_score_text = None
        if awaiting and progress and progress["answers_json"]:
            try:
                stored = json.loads(progress["answers_json"])
                asked = [q for q in questions if str(q["id"]) in stored] or questions
                earned, total = score_answers(asked, choices, stored)
                needed = needed_points(row, asked, questions)
                pass_score_text = f"{earned}/{needed}"
            except (TypeError, ValueError, json.JSONDecodeError):
                pass_score_text = None
        out = {
            "id": row["id"], "name": row["name"], "description": row["description"],
            "type_id": row["type_id"], "pass_score": row["pass_score"], "valid_days": row["valid_days"],
            "ask_briefed_by": bool(row["ask_briefed_by"]),
            "needs_approval": bool(row["needs_approval"]),
            "shuffle_questions": bool(row["shuffle_questions"]), "question_count": row["question_count"],
            "active": bool(row["active"]),
            "passed_at": progress["passed_at"] if progress and current else None,
            "submitted_at": progress["passed_at"] if progress and awaiting else None,
            "awaiting_approval": awaiting,
            "pass_score_text": pass_score_text,
            "expired_pass": bool(progress and progress["passed_at"] and not current and not awaiting),
            "attempts": progress["attempts"] if progress else 0,
            "briefed_by": progress["briefed_by"] if progress and (current or awaiting) else None,
            "briefed_by_name": briefer["name"] if briefer else None,
            "briefers": briefer_list(cid, training_id),
            "total_points": sum(q["points"] for q in questions),
            "type_name": cid.execute("SELECT name FROM qualification_types WHERE id=?",
                                     (row["type_id"],)).fetchone()["name"] if row["type_id"] else None,
        }
        if g.user["is_admin"]:
            out["questions"] = [{
                "id": q["id"], "prompt": q["prompt"], "kind": q["kind"], "points": q["points"],
                "image": bool(q["image_path"]),
                "choices": [{"id": c["id"], "label": c["label"], "correct": bool(c["correct"]),
                             "sort_order": c["sort_order"]} for c in choices.get(q["id"], [])],
            } for q in questions]
        elif (out["passed_at"] or awaiting) and request.args.get("practice") == "1" and questions:
            # practice after a pass: a fresh draw each time, not stored; the result changes nothing
            draw = new_draw(row, questions, choices)
            out["questions"], out["practice"] = public_questions(questions, choices, draw), True
            asked = [q for q in questions if q["id"] in set(draw["questions"])]
            out["needed"], out["asked_points"] = needed_points(row, asked, questions), sum(q["points"] for q in asked)
        elif out["passed_at"] or awaiting:
            out["questions"] = []
        elif questions:
            draw = current_draw(cid, training_id, row, questions, choices)
            out["questions"] = public_questions(questions, choices, draw)
            asked = [q for q in questions if q["id"] in set(draw["questions"])]
            out["needed"], out["asked_points"] = needed_points(row, asked, questions), sum(q["points"] for q in asked)
        else:
            out["questions"] = []
        return jsonify(out)

    def current_draw(cid, training_id, row, questions, choices):
        """This person's attempt (kept in training_progress.draw_json); a new one when none matches."""
        stored = cid.execute("SELECT draw_json FROM training_progress WHERE user_id=? AND training_id=?",
                             (g.user["id"], training_id)).fetchone()
        draw = valid_draw(stored["draw_json"] if stored else None, questions, choices)
        if draw is None:
            draw = new_draw(row, questions, choices)
            with db.write_lock, cid:
                cid.execute("BEGIN IMMEDIATE")
                cid.execute("INSERT INTO training_progress(user_id,training_id,attempts,draw_json) VALUES (?,?,0,?)"
                            " ON CONFLICT(user_id,training_id) DO UPDATE SET draw_json=excluded.draw_json",
                            (g.user["id"], training_id, json.dumps(draw)))
        return draw

    @app.get("/api/trainings/questions/<int:question_id>/image")
    def question_image(question_id):
        signed_in()
        row = con().execute("SELECT image_path, image_mime FROM training_questions WHERE id=?",
                            (question_id,)).fetchone()
        if not row or not row["image_path"]:
            abort(404)
        path = image_dir() / row["image_path"]
        if not path.is_file():
            abort(404)
        return send_file(path, mimetype=row["image_mime"] or "application/octet-stream")

    @app.post("/api/trainings/<int:training_id>/submit")
    def submit_training(training_id):
        signed_in()
        cid = con()
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            row = training_row(cid, training_id, active_only=True)
            progress = cid.execute("SELECT * FROM training_progress WHERE user_id=? AND training_id=?",
                                   (g.user["id"], training_id)).fetchone()
            body = request.get_json(silent=True) or {}
            if body.get("practice") is True:
                return practice_result(cid, row, training_id, body)
            if pass_is_current(cid, g.user["id"], row, progress):
                raise TrainingError("You have already passed this training.", 409)
            if pass_awaiting_approval(cid, progress):
                raise TrainingError("Your pass is waiting for approval.", 409)
            questions, choices = load_questions(cid, training_id)
            if not questions:
                raise TrainingError("This training has no questions yet.")
            draw = valid_draw(progress["draw_json"] if progress else None, questions, choices)
            if draw is None and (row["question_count"] or row["shuffle_questions"]) and not g.user["is_admin"]:
                raise TrainingError("The questions of this training changed; open it again to start a new attempt.", 409)
            asked = [q for q in questions if q["id"] in set(draw["questions"])] if draw else questions
            answers = parse_answers(body, asked)
            briefed_by = None
            briefer_name = None
            if row["ask_briefed_by"]:
                raw = body.get("briefed_by")
                if type(raw) is not int or isinstance(raw, bool) or raw <= 0:
                    raise TrainingError("Choose who delivered the briefing.")
                briefer = cid.execute(
                    "SELECT u.id, COALESCE(u.display_name,u.username) AS name FROM training_briefers b"
                    " JOIN users u ON u.id=b.user_id WHERE b.training_id=? AND b.user_id=?",
                    (training_id, raw)).fetchone()
                if briefer is None:
                    raise TrainingError("Choose who delivered the briefing.")
                briefed_by = raw
                briefer_name = briefer["name"]
            earned, total = score_answers(asked, choices, answers)
            needed = needed_points(row, asked, questions)
            passed = earned >= needed and total > 0
            attempts = (progress["attempts"] if progress else 0) + 1
            submission_id = None
            passed_at = None
            stored_answers = None
            awaiting = False
            if passed:
                passed_at = utc_now()
                stored_answers = json.dumps(answers)
                kind = cid.execute("SELECT * FROM qualification_types WHERE id=?", (row["type_id"],)).fetchone() if row["type_id"] else None
                if row["type_id"] and kind is None:
                    raise TrainingError("The linked qualification type is missing.")
                if kind and not kind["active"]:
                    raise TrainingError("The linked qualification type is disabled.")
                if kind and kind["requires_evidence"]:
                    raise TrainingError("Trainings cannot link to a type that requires evidence.")
                note = pass_note(row["name"], earned, needed, briefer_name, passed_at)
                if row["needs_approval"]:
                    submission_id = queue_qualification(cid, g.user["id"], kind, row["valid_days"], note)
                    awaiting = True
                else:
                    submission_id = grant_qualification(cid, g.user["id"], kind, row["valid_days"], note)
                cid.execute(
                    "INSERT INTO training_progress(user_id,training_id,attempts,passed_at,answers_json,submission_id,briefed_by)"
                    " VALUES (?,?,?,?,?,?,?) ON CONFLICT(user_id,training_id) DO UPDATE SET"
                    " attempts=excluded.attempts, passed_at=excluded.passed_at,"
                    " answers_json=excluded.answers_json, submission_id=excluded.submission_id,"
                    " briefed_by=excluded.briefed_by, draw_json=NULL",
                    (g.user["id"], training_id, attempts, passed_at, stored_answers, submission_id, briefed_by))
            else:
                cid.execute(
                    "INSERT INTO training_progress(user_id,training_id,attempts) VALUES (?,?,?)"
                    " ON CONFLICT(user_id,training_id) DO UPDATE SET attempts=excluded.attempts, draw_json=NULL",
                    (g.user["id"], training_id, attempts))
        return jsonify(passed=passed, score=earned, total=total, needed=needed, attempts=attempts,
                       passed_at=passed_at, qualification_id=submission_id, awaiting_approval=awaiting)

    def practice_result(cid, row, training_id, body):
        """Score a practice attempt (after a pass): the answered questions are the ones drawn; nothing is stored."""
        questions, choices = load_questions(cid, training_id)
        answers = parse_answers(body, questions)
        expected = min(row["question_count"] or len(questions), len(questions))
        if len(answers) != expected:
            raise TrainingError(f"Answer all {expected} questions.")
        asked = [q for q in questions if q["id"] in answers]
        earned, total = score_answers(asked, choices, answers)
        needed = needed_points(row, asked, questions)
        return jsonify(practice=True, passed=earned >= needed and total > 0, score=earned, total=total, needed=needed)

    def training_fields(cid, body, training_id=None):
        name = clip_text(body.get("name"), 80, "Name")
        description = (body.get("description") or "").strip()
        if len(description) > 2000:
            raise TrainingError("Description must be at most 2000 characters.")
        type_id = optional_int(body.get("type_id"), "Qualification type")
        pass_score = optional_int(body.get("pass_score"), "Pass score")
        valid_days = optional_int(body.get("valid_days"), "Validity days")
        if type(body.get("ask_briefed_by", False)) is not bool:
            raise TrainingError("Ask who briefed must be true or false.")
        ask_briefed_by = body.get("ask_briefed_by", False)
        if type(body.get("needs_approval", False)) is not bool:
            raise TrainingError("A pass needs approval must be true or false.")
        needs_approval = body.get("needs_approval", False)
        if needs_approval and not type_id:
            raise TrainingError("A pass that needs approval must link to a qualification.")
        briefers = parse_briefers(cid, ask_briefed_by, body.get("briefers"))
        resolve_type(cid, type_id, valid_days)
        if training_id is not None:
            total = cid.execute("SELECT COALESCE(SUM(points),0) FROM training_questions WHERE training_id=?",
                                (training_id,)).fetchone()[0]
            check_pass_score(pass_score, total)
        if type(body.get("shuffle_questions", False)) is not bool:
            raise TrainingError("Shuffle questions must be true or false.")
        draw = (int(body.get("shuffle_questions", False)), optional_int(body.get("question_count"), "Questions per attempt"))
        return name, description, type_id, pass_score, valid_days, ask_briefed_by, needs_approval, briefers, draw

    @app.post("/api/admin/trainings")
    def add_training():
        admin()
        cid = con()
        name, description, type_id, pass_score, valid_days, ask_briefed_by, needs_approval, briefers, draw = \
            training_fields(cid, request.get_json(silent=True) or {})
        try:
            with db.write_lock, cid:
                cid.execute("BEGIN IMMEDIATE")
                nxt = cid.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM trainings").fetchone()[0]
                tid = cid.execute(
                    "INSERT INTO trainings(name,description,type_id,pass_score,valid_days,ask_briefed_by,"
                    "needs_approval,sort_order,shuffle_questions,question_count) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (name, description, type_id, pass_score, valid_days, int(ask_briefed_by),
                     int(needs_approval), nxt, *draw)).lastrowid
                save_briefers(cid, tid, briefers)
        except sqlite3.IntegrityError as exc:
            raise TrainingError("A training with that name already exists.", 409) from exc
        return jsonify(ok=True, id=tid), 201

    @app.put("/api/admin/trainings/<int:training_id>")
    def edit_training(training_id):
        admin()
        body = request.get_json(silent=True) or {}
        if type(body.get("active", True)) is not bool:
            raise TrainingError("Active must be true or false.")
        cid = con()
        name, description, type_id, pass_score, valid_days, ask_briefed_by, needs_approval, briefers, draw = \
            training_fields(cid, body, training_id=training_id)
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            training_row(cid, training_id)
            try:
                cid.execute("UPDATE trainings SET name=?,description=?,type_id=?,pass_score=?,valid_days=?,"
                            "ask_briefed_by=?,needs_approval=?,active=?,shuffle_questions=?,question_count=? WHERE id=?",
                            (name, description, type_id, pass_score, valid_days, int(ask_briefed_by),
                             int(needs_approval), body.get("active", True), *draw, training_id))
                cid.execute("UPDATE training_progress SET draw_json=NULL WHERE training_id=?", (training_id,))
                save_briefers(cid, training_id, briefers)
            except sqlite3.IntegrityError as exc:
                raise TrainingError("A training with that name already exists.", 409) from exc
        return jsonify(ok=True)

    @app.delete("/api/admin/trainings/<int:training_id>")
    def delete_training(training_id):
        admin()
        cid = con()
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            training_row(cid, training_id)
            delete_training_images(cid, training_id)
            cid.execute("DELETE FROM trainings WHERE id=?", (training_id,))
        return jsonify(ok=True)

    @app.post("/api/admin/trainings/import")
    def import_training():
        """Import one questionnaire from JSON or a ZIP (JSON + JPEG/PNG images).
        With form field preview=1 nothing is written. An existing name is rejected unless replace=1."""
        admin()
        cid = con()
        preview = request.form.get("preview") == "1"
        replace = request.form.get("replace") == "1"
        type_raw = (request.form.get("type_id") or "").strip()
        type_id = None
        if type_raw:
            if not type_raw.isdigit():
                raise TrainingError("Qualification type must be an id.")
            type_id = int(type_raw)
        data, images = read_import_payload()
        parsed = parse_import(cid, data, images, type_id)
        action = "create"
        if parsed["existing_id"] is not None:
            if not replace:
                raise TrainingError(
                    f"A training named '{parsed['name']}' already exists. Re-check with Replace existing enabled,"
                    " or rename the questionnaire.", 409)
            action = "replace"
        result = {
            "preview": preview, "action": action, "name": parsed["name"],
            "description": parsed["description"], "pass_score": parsed["pass_score"],
            "valid_days": parsed["valid_days"], "ask_briefed_by": parsed["ask_briefed_by"],
            "needs_approval": parsed["needs_approval"],
            "type_id": type_id, "question_count": parsed["question_count"],
            "image_count": parsed["image_count"], "existing_id": parsed["existing_id"],
        }
        if type_id:
            kind = cid.execute("SELECT name FROM qualification_types WHERE id=?", (type_id,)).fetchone()
            result["type_name"] = kind["name"] if kind else None
        else:
            result["type_name"] = None
        warnings = []
        if parsed["ask_briefed_by"]:
            warnings.append("Who can deliver the briefing must be set in Edit mode after import.")
        if action == "replace":
            warnings.append("Questions and images are replaced in place; existing pass records are kept.")
        if warnings:
            result["warning"] = " ".join(warnings)
        if preview:
            return jsonify(result)
        image_blobs = parsed["images"]
        old_paths, written = [], []
        try:
            tid = write_import(cid, action, parsed, type_id, image_blobs, old_paths, written)
        except Exception:
            for path in written:
                (image_dir() / path).unlink(missing_ok=True)
            raise
        for path in old_paths:
            (image_dir() / path).unlink(missing_ok=True)
        result["id"] = tid
        return jsonify(result), (200 if action == "replace" else 201)

    def write_import(cid, action, parsed, type_id, image_blobs, old_paths, written):
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            if action == "replace":
                tid = parsed["existing_id"]
                old_paths[:] = [r["image_path"] for r in cid.execute(
                    "SELECT image_path FROM training_questions WHERE training_id=? AND image_path IS NOT NULL",
                    (tid,))]
                cid.execute(
                    "UPDATE trainings SET description=?,type_id=?,pass_score=?,valid_days=?,ask_briefed_by=?,"
                    "needs_approval=? WHERE id=?",
                    (parsed["description"], type_id, parsed["pass_score"], parsed["valid_days"],
                     int(parsed["ask_briefed_by"]), int(parsed["needs_approval"]), tid))
                if not parsed["ask_briefed_by"]:
                    cid.execute("DELETE FROM training_briefers WHERE training_id=?", (tid,))
                cid.execute("DELETE FROM training_questions WHERE training_id=?", (tid,))
            else:
                nxt = cid.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM trainings").fetchone()[0]
                try:
                    tid = cid.execute(
                        "INSERT INTO trainings(name,description,type_id,pass_score,valid_days,ask_briefed_by,"
                        "needs_approval,sort_order) VALUES (?,?,?,?,?,?,?,?)",
                        (parsed["name"], parsed["description"], type_id, parsed["pass_score"],
                         parsed["valid_days"], int(parsed["ask_briefed_by"]),
                         int(parsed["needs_approval"]), nxt)).lastrowid
                except sqlite3.IntegrityError as exc:
                    raise TrainingError("A training with that name already exists.", 409) from exc
            insert_questions(cid, tid, parsed["questions"], image_blobs, written)
        return tid

    @app.get("/api/admin/trainings/<int:training_id>/export")
    def export_training(training_id):
        admin()
        cid = con()
        row = training_row(cid, training_id)
        questions, choices = load_questions(cid, training_id)
        payload = {
            "name": row["name"],
            "description": row["description"] or "",
            "pass_score": row["pass_score"],
            "valid_days": row["valid_days"],
            "ask_briefed_by": bool(row["ask_briefed_by"]),
            "needs_approval": bool(row["needs_approval"]),
            "questions": [],
        }
        files = {}
        for q in questions:
            item = {
                "prompt": q["prompt"], "kind": q["kind"], "points": q["points"],
                "choices": [{"label": c["label"], **({"correct": True} if c["correct"] and q["kind"] != "ranking" else {})}
                            for c in choices.get(q["id"], [])],
            }
            if q["image_path"]:
                src = image_dir() / q["image_path"]
                if src.is_file():
                    suffix = Path(q["image_path"]).suffix.lower() or ".png"
                    name = f"question-{q['id']}{suffix}"
                    item["image"] = name
                    files[name] = src.read_bytes()
            payload["questions"].append(item)
        safe = secure_filename(row["name"])[:60] or "training"
        if not files:
            return send_file(io.BytesIO(json.dumps(payload, indent=2).encode("utf-8")),
                             mimetype="application/json",
                             as_attachment=True, download_name=f"{safe}.json")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{safe}.json", json.dumps(payload, indent=2))
            for name, data in files.items():
                zf.writestr(name, data)
        buf.seek(0)
        return send_file(buf, mimetype="application/zip", as_attachment=True,
                         download_name=f"{safe}.zip")

    @app.post("/api/admin/trainings/<int:training_id>/questions")
    def add_question(training_id):
        admin()
        return save_question(con(), training_id, None)

    @app.put("/api/admin/trainings/<int:training_id>/questions/<int:question_id>")
    def edit_question(training_id, question_id):
        admin()
        return save_question(con(), training_id, question_id)

    @app.delete("/api/admin/trainings/<int:training_id>/questions/<int:question_id>")
    def delete_question(training_id, question_id):
        admin()
        cid = con()
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            row = cid.execute("SELECT * FROM training_questions WHERE id=? AND training_id=?",
                              (question_id, training_id)).fetchone()
            if row is None:
                raise TrainingError("No such question.", 404)
            cid.execute("DELETE FROM training_questions WHERE id=?", (question_id,))
            cid.execute("UPDATE training_progress SET draw_json=NULL WHERE training_id=?", (training_id,))
            total = cid.execute("SELECT COALESCE(SUM(points),0) FROM training_questions WHERE training_id=?",
                                (training_id,)).fetchone()[0]
            pass_score = cid.execute("SELECT pass_score FROM trainings WHERE id=?", (training_id,)).fetchone()[0]
            check_pass_score(pass_score, total)
        if row["image_path"]:
            (image_dir() / row["image_path"]).unlink(missing_ok=True)
        return jsonify(ok=True)

    @app.put("/api/admin/trainings/<int:training_id>/question-order")
    def order_questions(training_id):
        """Set the order of a training's questions: {"question_ids": [...]} with every question once."""
        admin()
        ids = (request.get_json(silent=True) or {}).get("question_ids")
        if not isinstance(ids, list) or any(type(i) is not int for i in ids):
            raise TrainingError("question_ids must be a list of question ids.")
        cid = con()
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            training_row(cid, training_id)
            existing = {r[0] for r in cid.execute("SELECT id FROM training_questions WHERE training_id=?", (training_id,))}
            if len(ids) != len(existing) or set(ids) != existing:
                raise TrainingError("Give every question of this training exactly once.")
            cid.executemany("UPDATE training_questions SET sort_order=? WHERE id=?",
                            [(i, qid) for i, qid in enumerate(ids, 1)])
            cid.execute("UPDATE training_progress SET draw_json=NULL WHERE training_id=?", (training_id,))
        return jsonify(ok=True)

    def save_question(cid, training_id, question_id):
        body = request.get_json(silent=True) or {}
        prompt, kind, points, choices, _image = parse_question_body(body)
        created = question_id is None
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            row = training_row(cid, training_id)
            if created:
                nxt = cid.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM training_questions WHERE training_id=?",
                                  (training_id,)).fetchone()[0]
                question_id = cid.execute(
                    "INSERT INTO training_questions(training_id,prompt,kind,points,sort_order) VALUES (?,?,?,?,?)",
                    (training_id, prompt, kind, points, nxt)).lastrowid
            else:
                existing = cid.execute("SELECT id FROM training_questions WHERE id=? AND training_id=?",
                                       (question_id, training_id)).fetchone()
                if existing is None:
                    raise TrainingError("No such question.", 404)
                cid.execute("UPDATE training_questions SET prompt=?,kind=?,points=? WHERE id=?",
                            (prompt, kind, points, question_id))
                cid.execute("DELETE FROM training_choices WHERE question_id=?", (question_id,))
            cid.executemany(
                "INSERT INTO training_choices(question_id,label,correct,sort_order) VALUES (?,?,?,?)",
                [(question_id, label, int(correct), order) for label, correct, order in choices])
            cid.execute("UPDATE training_progress SET draw_json=NULL WHERE training_id=?", (training_id,))
            if not created:  # adding a question only raises the total, so a quiz can be built one question at a time
                total = cid.execute("SELECT COALESCE(SUM(points),0) FROM training_questions WHERE training_id=?",
                                    (training_id,)).fetchone()[0]
                check_pass_score(row["pass_score"], total)
        return jsonify(ok=True, id=question_id), (201 if created else 200)

    @app.post("/api/admin/trainings/<int:training_id>/questions/<int:question_id>/image")
    def upload_question_image(training_id, question_id):
        admin()
        uploaded = request.files.get("image")
        if uploaded is None or not uploaded.filename:
            raise TrainingError("Choose a JPEG or PNG image.")
        filename = secure_filename(uploaded.filename)[:160]
        suffix = Path(filename).suffix.lower()
        if suffix not in IMAGE_TYPES:
            raise TrainingError("Question images must be JPEG or PNG.")
        data = uploaded.read(MAX_IMAGE_BYTES + 1)
        mime, _ = check_image_bytes(filename, data)
        cid = con()
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            row = cid.execute("SELECT * FROM training_questions WHERE id=? AND training_id=?",
                              (question_id, training_id)).fetchone()
            if row is None:
                raise TrainingError("No such question.", 404)
            name = uuid.uuid4().hex + suffix
            path = image_dir() / name
            path.write_bytes(data)
            if row["image_path"]:
                (image_dir() / row["image_path"]).unlink(missing_ok=True)
            cid.execute("UPDATE training_questions SET image_path=?, image_mime=? WHERE id=?",
                        (name, mime, question_id))
        return jsonify(ok=True)

    @app.get("/api/admin/trainings/<int:training_id>/results")
    def training_results(training_id):
        admin()
        cid = con()
        training_row(cid, training_id)
        rows = [dict(r) for r in cid.execute(
            "SELECT p.user_id, COALESCE(u.display_name,u.username) AS person, p.attempts, p.passed_at,"
            " p.briefed_by, COALESCE(b.display_name,b.username) AS briefed_by_name, p.submission_id,"
            " p.answers_json"
            " FROM training_progress p JOIN users u ON u.id=p.user_id"
            " LEFT JOIN users b ON b.id=p.briefed_by"
            " WHERE p.training_id=? AND p.passed_at IS NOT NULL ORDER BY p.passed_at", (training_id,))]
        today = utc_date().isoformat()
        for row in rows:
            current = True
            if row["submission_id"]:
                sub = cid.execute("SELECT status, valid_until FROM qualification_submissions WHERE id=?",
                                  (row["submission_id"],)).fetchone()
                current = bool(sub and sub["status"] == "approved"
                               and (not sub["valid_until"] or sub["valid_until"] >= today))
            row["current"] = current
            try:
                row["answers"] = json.loads(row["answers_json"]) if row["answers_json"] else None
            except (TypeError, ValueError, json.JSONDecodeError):
                row["answers"] = None
            del row["answers_json"]
            del row["submission_id"]
        return jsonify(results=rows)

    @app.delete("/api/admin/trainings/<int:training_id>/progress/<int:user_id>")
    def reset_progress(training_id, user_id):
        """Clear a person's pass so they can retake. Withdraws the linked training-granted qualification."""
        admin()
        cid = con()
        with db.write_lock, cid:
            cid.execute("BEGIN IMMEDIATE")
            training_row(cid, training_id)
            progress = cid.execute("SELECT * FROM training_progress WHERE user_id=? AND training_id=?",
                                   (user_id, training_id)).fetchone()
            if progress is None:
                raise TrainingError("No progress for that person.", 404)
            if progress["submission_id"]:
                sub = cid.execute("SELECT * FROM qualification_submissions WHERE id=?",
                                  (progress["submission_id"],)).fetchone()
                if sub and sub["status"] == "approved":
                    cid.execute("UPDATE qualification_submissions SET status='withdrawn' WHERE id=?",
                                (progress["submission_id"],))
                    qualifications.audit(cid, user_id, g.user["id"], "training-reset",
                                         dict(id=progress["submission_id"], training_id=training_id))
            cid.execute("DELETE FROM training_progress WHERE user_id=? AND training_id=?",
                        (user_id, training_id))
        return jsonify(ok=True)

    @app.errorhandler(TrainingError)
    def training_error(exc):
        return jsonify(error=str(exc)), exc.status
