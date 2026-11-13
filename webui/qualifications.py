"""Private qualification submissions, evidence and administrative approval."""
import datetime
import gzip
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
import zlib
from pathlib import Path

from flask import Response, g, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

import db
import licence_qr
import mail
import qualification_import
import storage

MAX_EVIDENCE_BYTES = 20 * 1024 * 1024
# EASA remote pilot certificate (NLD-RP-0123456789ab) or EASA licence (NL.66.12345, BE.FCL.123456.A)
CERTIFICATE_NUMBER = re.compile(
    rb"[A-Z]{3}-RP-[A-Za-z0-9]{12}|\b[A-Z]{2}\.(?:66|FCL|ATCO|147)\.[A-Z0-9]+(?:\.[A-Z0-9]+)*")
OCR_PAGES = 4  # certificates have the number on the first pages
OCR_SECONDS = 60  # per call of pdftoppm or tesseract
READ_SECONDS = 25  # total for reading one chosen file in the upload dialog; OCR stops when it runs out
OCR_TOO_LONG = "OCR took too long; fill in the fields."


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def user_keys(con, user_id=None):
    names = {}
    for user in con.execute("SELECT id,username,display_name FROM users" + (" WHERE id=?" if user_id else ""),
                            (user_id,) if user_id else ()):
        names.setdefault(user["username"].casefold(), user["id"])
        if user["display_name"]:
            names.setdefault(user["display_name"].casefold(), user["id"])
    for row in con.execute("SELECT user_id,alias FROM user_aliases" + (" WHERE user_id=?" if user_id else ""),
                           (user_id,) if user_id else ()):
        names[row["alias"].casefold()] = row["user_id"]
    return names


def crew_keys(con):
    """Explicit aliases override unique display names; login usernames are not implicit aliases."""
    names = {}
    for user in con.execute("SELECT id,display_name FROM users WHERE display_name IS NOT NULL"):
        name = user["display_name"].strip().casefold()
        if name:
            names[name] = None if name in names else user["id"]
    for row in con.execute("SELECT user_id,alias FROM user_aliases"):
        names[row["alias"].casefold()] = row["user_id"]
    return names


def can_see_quals(con, viewer, subject_id):
    return bool(viewer["is_admin"] or viewer["id"] == subject_id)


def directory_user_ids(con, viewer):
    if viewer["is_admin"]:
        return {r[0] for r in con.execute("SELECT id FROM users WHERE is_active=1")}
    return {viewer["id"]} | {r[0] for r in con.execute(
        "SELECT s.user_id FROM project_members m JOIN project_members s ON s.project_id=m.project_id"
        " WHERE m.user_id=?", (viewer["id"],))}


def qualifications_payload(con, viewer, user_id):
    if not can_see_quals(con, viewer, user_id):
        return None
    items = []
    for row in con.execute(
            "SELECT q.*,t.name AS category,t.requires_evidence,t.requires_start,t.requires_expiry,t.internal,"
            " t.requires_number,t.description AS type_description,"
            " COALESCE(u.display_name,u.username) AS reviewer FROM qualification_submissions q"
            " JOIN qualification_types t ON t.id=q.type_id LEFT JOIN users u ON u.id=q.reviewed_by"
            " WHERE q.user_id=? ORDER BY t.sort_order, t.name, q.id DESC", (user_id,)):
        item = {key: row[key] for key in (
            "id", "type_id", "category", "status", "valid_from", "valid_until", "submitted_at",
            "reviewed_at", "reviewer", "evidence_deleted_at", "filename", "note", "requires_evidence",
            "requires_start", "requires_expiry", "requires_number", "number")}
        item["internal"] = bool(row["internal"])
        item["has_evidence"] = bool(row["evidence_path"])
        item["viewable"] = item["has_evidence"] and storage.view_type(row["filename"] or "") is not None
        item["expired"] = bool(row["valid_until"] and row["valid_until"] < datetime.date.today().isoformat())
        link = con.execute(
            "SELECT training_id, user_id FROM training_progress WHERE submission_id=?",
            (row["id"],)).fetchone()
        if viewer["is_admin"] and row["evidence_path"] and not row["qr_json"]:
            item["qr"] = {"status": "pending"}
        if viewer["is_admin"] and row["qr_json"]:
            stored = json.loads(row["qr_json"])
            item["qr"] = dict(licence_qr.check(stored["codes"], row["number"], row["valid_from"], row["valid_until"]),
                              checked_at=stored.get("checked_at"))
        item["training_id"] = link["training_id"] if link else None
        item["training_user_id"] = link["user_id"] if link else None
        items.append(item)
    return dict(items=items, types=[dict(r) for r in con.execute(
        "SELECT * FROM qualification_types ORDER BY sort_order, name")], can_manage=bool(viewer["is_admin"]),
        max_evidence_bytes=MAX_EVIDENCE_BYTES)


def current_payload(con, viewer, user_id):
    if not can_see_quals(con, viewer, user_id):
        return None
    return dict(items=[
        dict(id=r["id"], has_evidence=bool(r["evidence_path"]),
             viewable=bool(r["evidence_path"]) and storage.view_type(r["filename"] or "") is not None,
             category=r["category"], valid_from=r["valid_from"], valid_until=r["valid_until"], status="approved",
             expired=bool(r["valid_until"] and r["valid_until"] < datetime.date.today().isoformat()),
             internal=bool(r["internal"]), number=r["number"])
        for r in con.execute(
            "SELECT q.id,q.evidence_path,q.filename,t.name AS category,q.valid_from,q.valid_until,t.internal,q.number"
            " FROM qualification_submissions q"
            " JOIN qualification_types t ON t.id=q.type_id WHERE q.user_id=? AND q.status='approved'"
            " ORDER BY t.sort_order, t.name", (user_id,))])


class QualificationError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def audit(con, subject_id, actor_id, action, details):
    con.execute(
        "INSERT INTO qualification_audit(subject_id,actor_id,action,details_json,created_at) VALUES (?,?,?,?,?)",
        (subject_id, actor_id, action, json.dumps(details), _utc_now()))


def expiry(raw, label="Valid until"):
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise QualificationError(f"{label} must be YYYY-MM-DD or empty.")
    try:
        if datetime.date.fromisoformat(raw).isoformat() != raw:
            raise ValueError
    except ValueError as exc:
        raise QualificationError(f"{label} must be a valid YYYY-MM-DD date.") from exc
    return raw


def licence_number(raw):
    """A licence or certificate number as typed: up to 40 letters, digits, spaces and . / - _; None when empty."""
    if raw in (None, ""):
        return None
    if not isinstance(raw, str) or not re.fullmatch(r"[A-Za-z0-9 ./_-]{1,40}", raw.strip()):
        raise QualificationError("Number must be at most 40 letters, digits, spaces or . / - _.")
    return raw.strip()


def pdf_number(path, compressed):
    """The certificate number in a PDF's text, or None. Best effort: the reviewer confirms it."""
    for text in pdf_texts(path, compressed):
        hit = CERTIFICATE_NUMBER.search(text)
        if hit:
            return hit.group().decode()
    return None


def pdf_texts(path, compressed, readable=False):
    """The text of a PDF as byte strings to search, best first; [] when it is not a readable PDF.
    readable: only the text strings (no raw streams such as metadata), separated by spaces.
    Reads plain and Flate-compressed streams; text in embedded subset fonts (glyph codes such as
    <0038004300...>Tj, as in the RDW certificates) is decoded with the fonts' ToUnicode maps."""
    try:
        with (gzip.open(path, "rb") if compressed else open(path, "rb")) as source:
            raw = source.read(MAX_EVIDENCE_BYTES + 1)
    except OSError:
        return []
    if not raw.startswith(b"%PDF-"):
        return []
    chunks = [raw]
    for match in re.finditer(rb"stream\r?\n", raw):
        end = raw.find(b"endstream", match.end())
        if end < 0:
            continue
        try:
            chunks.append(zlib.decompressobj().decompress(raw[match.end():end], 4 * MAX_EVIDENCE_BYTES))
        except zlib.error:
            continue
    texts = []
    # Text is often split into several strings, e.g. [(NLD-RP-)-12(0123456789ab)]TJ: join the literals (for a
    # number without a space; readable text keeps the strings apart so a label and a date stay separate words).
    glue = b" " if readable else b""
    for chunk in chunks:
        joined = glue.join(s[1:-1] for s in re.findall(rb"\((?:\\.|[^\\)])*\)", chunk))
        texts += [joined] if readable else [chunk, joined]
    content = [c for c in chunks if b"BT" in c and b"ET" in c]
    for cmap in filter(None, (to_unicode(c) for c in chunks if b"beginbf" in c)):
        # Decode every glyph string with each font's map: the one the number is set in yields it.
        texts += [glue.decode().join(decode_glyphs(s, cmap) for s in re.findall(rb"<([0-9A-Fa-f\s]+)>", block)).encode()
                  for chunk in content for block in re.findall(rb"BT(.*?)ET", chunk, re.S)]
    return texts


def ocr_available():
    return bool(shutil.which("pdftoppm") and shutil.which("tesseract"))


def ocr_number(path, compressed):
    """The number read by OCR from a scanned PDF (pages turned upright first), or None. Needs the pdftoppm
    (poppler-utils) and tesseract (tesseract-ocr) commands; takes a few seconds per page."""
    return ocr_read(path, compressed)[0]


def ocr_read(path, compressed, deadline=None):
    """(number or None, the recognised text of the pages read, complete) of a scanned PDF; (None, b"", True)
    without OCR. With a deadline (time.monotonic()), every call gets at most the time left; when it runs out the
    pages read so far are returned with complete False."""
    if not ocr_available():
        return None, b"", True

    def seconds():
        if deadline is None:
            return OCR_SECONDS
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError
        return min(OCR_SECONDS, left)
    import numpy  # installed with h5py
    pages = []
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "evidence.pdf"
        with (gzip.open(path, "rb") if compressed else open(path, "rb")) as source, open(pdf, "wb") as out:
            shutil.copyfileobj(source, out)
        try:
            subprocess.run(["pdftoppm", "-r", "200", "-gray", "-l", str(OCR_PAGES), str(pdf), str(Path(tmp) / "p")],
                           check=True, capture_output=True, timeout=seconds())
        except TimeoutError:
            return None, b"", False
        except subprocess.TimeoutExpired:
            return None, b"", deadline is None or time.monotonic() < deadline
        except (OSError, subprocess.SubprocessError):
            return None, b"", True
        for page in sorted(Path(tmp).glob("p-*.pgm")):
            try:
                osd = subprocess.run(["tesseract", str(page), "-", "--psm", "0"], capture_output=True,
                                     timeout=seconds(), text=True).stdout
                turn = re.search(r"Rotate: (\d+)", osd)
                turned = int(turn.group(1)) % 360 if turn else 0
                if turned:
                    rotate_pgm(page, turned, numpy)
                text = ocr_text(page, seconds())
                hit = CERTIFICATE_NUMBER.search(text)
                if hit is None and turned != 180:
                    # orientation detection needs much text; a short or upside-down page gets one more try
                    rotate_pgm(page, 180, numpy)
                    turned_text = ocr_text(page, seconds())
                    hit = CERTIFICATE_NUMBER.search(turned_text)
                    if hit:
                        text = turned_text
            except TimeoutError:
                return None, b"\n".join(pages), False
            except subprocess.TimeoutExpired:
                if deadline is not None and time.monotonic() >= deadline:
                    return None, b"\n".join(pages), False
                continue
            except (OSError, subprocess.SubprocessError, ValueError):
                continue
            pages.append(text)
            if hit:
                return hit.group().decode(), b"\n".join(pages), True
    return None, b"\n".join(pages), True


def ocr_text(page, seconds=OCR_SECONDS):
    return subprocess.run(["tesseract", str(page), "-", "--psm", "3"], capture_output=True,
                          timeout=seconds).stdout


def rotate_pgm(page, degrees, numpy):
    """Turn a binary 8-bit PGM (as pdftoppm -gray writes it) clockwise by 90, 180 or 270 degrees."""
    raw = page.read_bytes()
    head = re.match(rb"P5\s+(\d+)\s+(\d+)\s+255\s", raw)
    if not head:
        raise ValueError("not an 8-bit PGM")
    width, height = int(head.group(1)), int(head.group(2))
    image = numpy.frombuffer(raw, numpy.uint8, width * height, head.end()).reshape(height, width)
    image = numpy.rot90(image, -(degrees // 90))
    page.write_bytes(b"P5\n%d %d\n255\n" % (image.shape[1], image.shape[0]) + image.tobytes())


def to_unicode(cmap):
    """Glyph code (bytes) to text from a ToUnicode CMap stream (bfchar and bfrange entries)."""
    out = {}
    hexes = lambda h: bytes.fromhex(h.decode())
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", cmap, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
            out[hexes(src)] = hexes(dst).decode("utf-16-be", "replace")
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", cmap, re.S):
        for lo, hi, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]*>|\[[^\]]*\])", block):
            start, stop, width = int(lo, 16), int(hi, 16), len(lo) // 2
            if stop - start > 65535:
                continue
            if dst.startswith(b"["):
                targets = [hexes(d).decode("utf-16-be", "replace") for d in re.findall(rb"<([0-9A-Fa-f]*)>", dst)]
            else:
                first = hexes(dst[1:-1])
                base = int.from_bytes(first, "big") if first else 0
                targets = [(base + k).to_bytes(max(len(first), 2), "big").decode("utf-16-be", "replace")
                           for k in range(stop - start + 1)]
            for k, text in enumerate(targets[:stop - start + 1]):
                out[(start + k).to_bytes(width, "big")] = text
    return out


def decode_glyphs(hexstring, cmap):
    digits = re.sub(rb"\s", b"", hexstring).decode()
    data = bytes.fromhex(digits + "0" * (len(digits) % 2))  # an odd final digit is padded with 0
    width = len(next(iter(cmap)))
    return "".join(cmap.get(data[i:i + width], "?") for i in range(0, len(data) - width + 1, width))


def integer(raw):
    if isinstance(raw, bool) or not isinstance(raw, (str, int)) or not str(raw).isascii() or not str(raw).isdigit():
        raise QualificationError("Choose a valid qualification type.")
    if len(str(raw)) > 10 or int(raw) <= 0:
        raise QualificationError("Choose a valid qualification type.")
    return int(raw)


def private_subject(con, user, uid):
    if not can_see_quals(con, user, uid) or not con.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
        raise QualificationError("No such person.", 404)


def visible_submission(con, user, sid):
    row = con.execute(
        "SELECT q.*,t.name AS category,t.requires_evidence,t.requires_expiry,t.requires_start,t.requires_number,t.active"
        " FROM qualification_submissions q"
        " JOIN qualification_types t ON t.id=q.type_id WHERE q.id=?", (sid,)).fetchone()
    if row is None or not can_see_quals(con, user, row["user_id"]):
        raise QualificationError("No such qualification.", 404)
    return row


def evidence_format(path, compressed, filename):
    with (gzip.open(path, "rb") if compressed else open(path, "rb")) as source:
        head = source.read(16)
    formats = {".pdf": ("application/pdf", head.startswith(b"%PDF-")),
               ".jpg": ("image/jpeg", head.startswith(b"\xff\xd8\xff")),
               ".jpeg": ("image/jpeg", head.startswith(b"\xff\xd8\xff")),
               ".png": ("image/png", head.startswith(b"\x89PNG\r\n\x1a\n"))}
    mime, matches = formats.get(Path(filename).suffix.lower(), ("", False))
    if not matches:
        raise QualificationError("Evidence must be a PDF, JPEG or PNG with matching file content.")
    return mime


def read_qr(con, row, root, actor_id=None):
    """Read the QR codes of one submission's evidence, store them and fill an empty number from them.
    Returns the check (licence_qr.check) or None without evidence. Reads the file outside the write lock."""
    if not row["evidence_path"]:
        return None
    codes = licence_qr.read_codes(Path(root) / row["evidence_path"], bool(row["compressed"]), row["mime"])
    found = licence_qr.facts(codes)
    with db.write_lock, con:
        con.execute("BEGIN IMMEDIATE")
        con.execute("UPDATE qualification_submissions SET qr_json=? WHERE id=? AND evidence_path=?",
                    (json.dumps({"codes": codes, "checked_at": _utc_now()}), row["id"], row["evidence_path"]))
        wants_number = con.execute("SELECT requires_number FROM qualification_types WHERE id=?",
                                   (row["type_id"],)).fetchone()
        filled = None
        if found["number"] and not row["number"] and wants_number and wants_number[0] and con.execute(
                "UPDATE qualification_submissions SET number=? WHERE id=? AND COALESCE(number,'')=''",
                (found["number"], row["id"])).rowcount:
            filled = found["number"]
            audit(con, row["user_id"], actor_id, "number-changed",
                  dict(id=row["id"], number=filled, old_number=None, source="qr"))
    # compared with what was entered: a number just filled from the code says nothing about the code
    return dict(licence_qr.check(codes, row["number"], row["valid_from"], row["valid_until"]), filled=filled)


MONTHS = {name: n for n, names in enumerate((
    "jan january januari janvier januar", "feb february februari fevrier février februar",
    "mar march maart mars märz marz", "apr april avril", "may mei mai", "jun june juni juin",
    "jul july juli juillet", "aug august augustus aout août", "sep sept september septembre",
    "oct october oktober okt octobre", "nov november novembre", "dec december decembre décembre dezember dez"),
    1) for name in names.split()}
TEXT_DATES = (  # (pattern, order of year/month/day groups); month names are looked up in MONTHS
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), (1, 2, 3)),
    (re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b"), (3, 2, 1)),
    (re.compile(r"\b(\d{1,2})\.?\s+([A-Za-zéû]{3,9})\.?\s+(\d{4})\b"), (3, 2, 1)),
)
# Labels before a date (closest one wins). Dates of birth are never a validity date.
DATE_LABELS = (
    ("birth", re.compile(r"birth|geboren|geboorte|naissance|geburt", re.I)),
    ("valid_until", re.compile(r"valid\s*(?:until|till|to)|expir|geldig\s*tot|vervaldatum|verloopdatum"
                               r"|date\s*of\s*expiry|valable\s*jusqu|gültig\s*bis|g.ltig\s*bis", re.I)),
    ("valid_from", re.compile(r"valid\s*from|date\s*of\s*issue|issue\s*date|issued|afgifte|uitgegeven|geldig\s*vanaf"
                              r"|date\s*de\s*d.livrance|ausstellungsdatum", re.I)),
)
LABEL_WINDOW = 80  # characters before a date searched for its label


def text_dates(text):
    """The dates in a text as [(ISO date, label or None)], label 'valid_from', 'valid_until' or 'birth'."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    found = []
    for pattern, order in TEXT_DATES:
        for m in pattern.finditer(text):
            parts = [m.group(i) for i in order]
            month = parts[1] if parts[1].isdigit() else MONTHS.get(parts[1].casefold())
            try:
                day = datetime.date(int(parts[0]), int(month), int(parts[2]))
            except (TypeError, ValueError):
                continue
            if not 1980 <= day.year <= 2100:
                continue
            before, label, at = text[max(0, m.start() - LABEL_WINDOW):m.start()], None, -1
            for name, words in DATE_LABELS:
                for hit in words.finditer(before):
                    if hit.start() > at:
                        label, at = name, hit.start()
            found.append((m.start(), day.isoformat(), label))
    out = []
    for _, day, label in sorted(found):
        if (day, label) not in out:
            out.append((day, label))
    return out


def years_apart(start, until):
    """Whole years (1 to 10) from start to until, also when until is the day before the anniversary."""
    a, b = datetime.date.fromisoformat(start), datetime.date.fromisoformat(until)
    for years in range(1, 11):
        try:
            anniversary = a.replace(year=a.year + years)
        except ValueError:  # 29 February
            anniversary = datetime.date(a.year + years, 3, 1)
        if b in (anniversary, anniversary - datetime.timedelta(days=1)):
            return years
    return None


def validity(dates):
    """(valid_from, valid_until) from [(date, label)]: labelled dates first; otherwise the pair of dates a whole
    number of years apart (EASA certificates run 5 years). None where the document does not say it clearly."""
    start = next((d for d, label in dates if label == "valid_from"), None)
    until = max((d for d, label in dates if label == "valid_until"), default=None)
    if start and until and start > until:
        start = None
    if start is None or until is None:
        days = sorted({d for d, label in dates if label != "birth"})
        pairs = [(a, b) for a in days for b in days if a < b and years_apart(a, b)
                 and (start is None or a == start) and (until is None or b == until)]
        if pairs:
            start, until = max(pairs, key=lambda p: p[1])
    return start, until


def same_value(field, a, b):
    if field == "number":
        return re.sub(r"\s", "", a or "").upper() == re.sub(r"\s", "", b or "").upper()
    return a == b


def read_evidence(path, compressed, mime, entered, wanted=("number", "valid_from", "valid_until"), ocr=True,
                  seconds=READ_SECONDS):
    """Read number and validity dates from evidence: the QR code, the PDF text and, when something wanted is
    still missing and ocr is set, OCR within what is left of seconds. Best effort: the reviewer confirms what was read.
    Returns dict(found={field: {value, source}}, dates=[{date, label, source}], codes (None: QR not read),
    qr (licence_qr.check against the entered values), issues: where the document differs from what was entered,
    notes: e.g. that OCR ran out of time)."""
    deadline = time.monotonic() + seconds
    found, dates, sources, notes = {}, [], {}, []
    codes = licence_qr.read_codes(path, compressed, mime) if licence_qr.available(mime == "application/pdf") else None

    def take(source, number, text_found):
        if number and "number" not in found:
            found["number"] = dict(value=number, source=source)
        for day, label in text_found:
            if not any(x["date"] == day and x["label"] == label for x in dates):
                dates.append(dict(date=day, label=label, source=source))
        start, until = validity(text_found)
        for field, value in (("valid_from", start), ("valid_until", until)):
            if value and field not in found:
                found[field] = dict(value=value, source=source)
        sources[source] = dict(number=number, valid_from=start, valid_until=until)

    if codes:
        facts = licence_qr.facts(codes)
        qr_dates = text_dates("\n".join(codes))
        qr_dates += [(d, None) for d in facts["dates"] if d not in {x[0] for x in qr_dates}]
        take("qr", facts["number"], qr_dates)
    if mime == "application/pdf":
        take("text", pdf_number(path, compressed), text_dates(b"\n".join(pdf_texts(path, compressed, readable=True))))
        if ocr and any(field not in found for field in wanted):
            number, text, complete = ocr_read(path, compressed, deadline)
            if number or text:
                take("ocr", number, text_dates(text))
            if not complete:
                notes.append(OCR_TOO_LONG)
    issues = []
    labels = dict(number="Number", valid_from="Valid from", valid_until="Valid until")
    for field, label in labels.items():
        typed = entered.get(field)
        for source, values in sources.items():
            if source != "qr" and typed and values[field] and not same_value(field, typed, values[field]):
                issues.append(f"{label} in the document ({source.upper() if source == 'ocr' else source}) is "
                              f"{values[field]}, entered {typed}")
    qr = None
    if codes is not None:
        qr = licence_qr.check(codes, entered.get("number"), entered.get("valid_from"), entered.get("valid_until"))
        issues += [f"QR code: {issue}" for issue in qr["issues"]]
    return dict(found=found, dates=dates, codes=codes, qr=qr, issues=issues, notes=notes)


TYPE_DESCRIPTION_MAX = 300


def type_description(body):
    """The type's description from a request body; None when it was not sent (keep the stored one)."""
    if "description" not in body or body["description"] is None:
        return None
    if not isinstance(body["description"], str) or len(body["description"].strip()) > TYPE_DESCRIPTION_MAX:
        raise QualificationError(f"The description can be at most {TYPE_DESCRIPTION_MAX} characters.")
    return " ".join(body["description"].split())


def register(app, connection, root, require_session):
    reading, reading_lock = set(), threading.Lock()  # people whose chosen file is being read (one process, threads)
    app.config["QUALIFICATION_EVIDENCE_DIR"] = Path(root) / "qualification-evidence"
    app.config.setdefault("QUALIFICATION_IMPORT_DIR", Path(root).parent / "tmp" / "qual-import")

    @app.get("/qualifications.js")
    def qualifications_script():
        return send_from_directory(Path(__file__).parent / "static", "qualifications.js")

    @app.get("/api/admin/qualification-types")
    def list_qualification_types():
        admin()
        return jsonify(types=[dict(r) for r in connection().execute(
            "SELECT * FROM qualification_types ORDER BY sort_order, name")])

    @app.put("/api/admin/qualification-types")
    def save_qualification_types():
        admin()
        body = request.get_json(silent=True)
        allowed = {"id", "name", "requires_evidence", "requires_expiry", "requires_start", "requires_number",
                   "internal", "active", "description"}
        if not isinstance(body, dict) or set(body) - {"types"} or not isinstance(body.get("types"), list):
            raise QualificationError("Invalid qualification request.")
        parsed = []
        seen_names, seen_ids = set(), set()
        for raw in body["types"]:
            if not isinstance(raw, dict) or set(raw) - allowed:
                raise QualificationError("Invalid qualification request.")
            name = raw.get("name")
            if isinstance(name, str) and not name.strip() and raw.get("id") is None:
                continue
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                raise QualificationError("Type name must be 1-80 characters.")
            name = name.strip()
            if name.casefold() in seen_names:
                raise QualificationError("A type with that name already exists.", 409)
            seen_names.add(name.casefold())
            tid = raw.get("id")
            if tid is not None:
                if type(tid) is not int or isinstance(tid, bool) or tid <= 0:
                    raise QualificationError("Choose a valid qualification type.")
                if tid in seen_ids:
                    raise QualificationError("Invalid qualification request.")
                seen_ids.add(tid)
            if (type(raw.get("requires_evidence")) is not bool
                    or type(raw.get("requires_expiry", False)) is not bool
                    or type(raw.get("requires_start", False)) is not bool
                    or type(raw.get("requires_number", False)) is not bool
                    or type(raw.get("internal", False)) is not bool
                    or type(raw.get("active", True)) is not bool):
                raise QualificationError(
                    "Evidence requirement, date requirements, internal and active must be true or false.")
            parsed.append(dict(
                id=tid, name=name, requires_evidence=raw["requires_evidence"], description=type_description(raw),
                requires_expiry=raw.get("requires_expiry", True), requires_start=raw.get("requires_start", True),
                requires_number=raw.get("requires_number", False),
                internal=raw.get("internal", False), active=raw.get("active", True)))
        con = connection()
        try:
            with db.write_lock, con:
                con.execute("BEGIN IMMEDIATE")
                existing = {r["id"]: r for r in con.execute("SELECT * FROM qualification_types")}
                for item in parsed:
                    if item["id"] is not None and item["id"] not in existing:
                        raise QualificationError("No such qualification type.", 404)
                for item in parsed:
                    if item["description"] is None:  # not sent: keep it
                        item["description"] = existing[item["id"]]["description"] if item["id"] else ""
                for order, item in enumerate(parsed, 1):
                    if item["id"] is None:
                        item["id"] = con.execute(
                            "INSERT INTO qualification_types(name,requires_evidence,requires_expiry,requires_start,"
                            "requires_number,internal,sort_order,active,description) VALUES (?,?,?,?,?,?,?,?,?)",
                            (item["name"], item["requires_evidence"], item["requires_expiry"], item["requires_start"],
                             item["requires_number"], item["internal"], order, item["active"],
                             item["description"])).lastrowid
                    else:
                        con.execute(
                            "UPDATE qualification_types SET name=?,requires_evidence=?,requires_expiry=?,"
                            "requires_start=?,requires_number=?,internal=?,sort_order=?,active=?,description=? WHERE id=?",
                            (item["name"], item["requires_evidence"], item["requires_expiry"], item["requires_start"],
                             item["requires_number"], item["internal"], order, item["active"], item["description"],
                             item["id"]))
                leftover = [rid for rid in existing if rid not in {item["id"] for item in parsed}]
                leftover.sort(key=lambda rid: (existing[rid]["sort_order"], existing[rid]["name"].casefold()))
                for offset, rid in enumerate(leftover, len(parsed) + 1):
                    con.execute("UPDATE qualification_types SET sort_order=? WHERE id=?", (offset, rid))
                audit(con, None, g.user["id"], "types-updated", dict(types=[
                    {key: item[key] for key in (
                        "id", "name", "requires_evidence", "requires_expiry", "requires_start", "requires_number",
                        "internal", "active", "description")}
                    for item in parsed]))
        except sqlite3.IntegrityError as exc:
            if "qualification_types.name" not in str(exc):
                raise
            raise QualificationError("A type with that name already exists.", 409) from exc
        return jsonify(ok=True, ids=[item["id"] for item in parsed])

    @app.get("/api/admin/qualification-pending")
    def qualification_pending():
        admin()
        rows = connection().execute(
            "SELECT q.id,q.user_id,q.type_id,t.name AS category,t.requires_start,t.requires_expiry,"
            " q.valid_from,q.valid_until,q.submitted_at,q.note,q.evidence_path,"
            " COALESCE(u.display_name,u.username) AS person FROM qualification_submissions q"
            " JOIN users u ON u.id=q.user_id JOIN qualification_types t ON t.id=q.type_id"
            " WHERE q.status='pending' ORDER BY q.id").fetchall()
        out = []
        for r in rows:
            link = connection().execute(
                "SELECT training_id FROM training_progress WHERE submission_id=?",
                (r["id"],)).fetchone()
            out.append({
                "id": r["id"], "user_id": r["user_id"], "type_id": r["type_id"],
                "category": r["category"], "requires_start": r["requires_start"],
                "requires_expiry": r["requires_expiry"], "valid_from": r["valid_from"],
                "valid_until": r["valid_until"], "submitted_at": r["submitted_at"],
                "person": r["person"], "has_evidence": bool(r["evidence_path"]),
                "evidence_label": r["note"] or None,
                "training_id": link["training_id"] if link else None,
            })
        return jsonify(submissions=out)

    @app.get("/api/admin/qualifications/import/template")
    def qualification_import_template():
        admin()
        return Response(qualification_import.TEMPLATE, mimetype="text/csv", headers={
            "Content-Disposition": 'attachment; filename="qualification-import.csv"',
            "Cache-Control": "private, no-store"})

    @app.post("/api/admin/qualifications/import")
    def qualification_import_preview():
        admin()
        request.max_content_length = qualification_import.MAX_UPLOAD_BYTES + 1024 * 1024
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            raise QualificationError("Choose a CSV or a ZIP that contains manifest.csv.")
        raw = upload.stream.read(qualification_import.MAX_UPLOAD_BYTES + 1)
        if len(raw) > qualification_import.MAX_UPLOAD_BYTES:
            raise QualificationError("Import must be at most 200 MiB.")
        return jsonify(qualification_import.store_preview(app, connection(), upload.filename, raw))

    @app.post("/api/admin/qualifications/import/commit")
    def qualification_import_commit():
        admin()
        body = json_body(("token",))
        token = body.get("token")
        if not isinstance(token, str) or not token:
            raise QualificationError("Upload the CSV or ZIP again to preview it.")
        return jsonify(qualification_import.commit(app, connection(), token)), 201

    @app.errorhandler(QualificationError)
    def qualification_error(exc):
        return jsonify(error=str(exc)), exc.status

    def admin():
        require_session()
        if not g.user["is_admin"]:
            raise QualificationError("Administrator access required.", 403)

    def evidence_root():
        return Path(app.config["QUALIFICATION_EVIDENCE_DIR"])

    def json_body(allowed):
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) - set(allowed):
            raise QualificationError("Invalid qualification request.")
        return body

    @app.post("/api/qualification-types")
    @app.put("/api/qualification-types/<int:type_id>")
    def save_qualification_type(type_id=None):
        admin()
        body = json_body(("name", "requires_evidence", "requires_expiry", "requires_start", "requires_number",
                          "internal", "active", "description"))
        description = type_description(body)
        name = body.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise QualificationError("Type name must be 1-80 characters.")
        if (type(body.get("requires_evidence")) is not bool
                or type(body.get("requires_expiry", False)) is not bool
                or type(body.get("requires_start", False)) is not bool
                or type(body.get("requires_number", False)) is not bool
                or type(body.get("internal", False)) is not bool
                or type(body.get("active", True)) is not bool):
            raise QualificationError("Evidence requirement, date requirements, internal and active must be true or false.")
        con = connection()
        try:
            with db.write_lock, con:
                con.execute("BEGIN IMMEDIATE")
                if type_id is not None:
                    existing = con.execute("SELECT * FROM qualification_types WHERE id=?", (type_id,)).fetchone()
                    if existing is None:
                        raise QualificationError("No such qualification type.", 404)
                    requires_expiry = body.get("requires_expiry", bool(existing["requires_expiry"]))
                    requires_start = body.get("requires_start", bool(existing["requires_start"]))
                    internal = body.get("internal", bool(existing["internal"]))
                    requires_number = body.get("requires_number", bool(existing["requires_number"]))
                    if description is None:
                        description = existing["description"]
                    con.execute("UPDATE qualification_types SET name=?,requires_evidence=?,requires_expiry=?,"
                                "requires_start=?,requires_number=?,internal=?,active=?,description=? WHERE id=?",
                                (name.strip(), body["requires_evidence"], requires_expiry, requires_start,
                                 requires_number, internal, body.get("active", True), description, type_id))
                else:
                    requires_expiry = body.get("requires_expiry", True)
                    requires_start = body.get("requires_start", True)
                    internal = body.get("internal", False)
                    requires_number = body.get("requires_number", False)
                    nxt = con.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM qualification_types").fetchone()[0]
                    type_id = con.execute(
                        "INSERT INTO qualification_types(name,requires_evidence,requires_expiry,requires_start,"
                        "requires_number,internal,sort_order,active,description) VALUES (?,?,?,?,?,?,?,?,?)",
                        (name.strip(), body["requires_evidence"], requires_expiry, requires_start, requires_number,
                         internal, nxt, body.get("active", True), description or "")).lastrowid
                audit(con, None, g.user["id"], "type-updated",
                      dict(type_id=type_id, name=name.strip(), requires_evidence=body["requires_evidence"],
                           requires_expiry=requires_expiry, requires_start=requires_start,
                           requires_number=requires_number, internal=internal,
                           active=body.get("active", True), description=description))
        except sqlite3.IntegrityError as exc:
            if "qualification_types.name" not in str(exc):
                raise
            raise QualificationError("A type with that name already exists.", 409) from exc
        return jsonify(ok=True, id=type_id)

    @app.get("/api/users/<int:user_id>/qualifications")
    def list_qualifications(user_id):
        con = connection()
        private_subject(con, g.user, user_id)
        return jsonify(qualifications_payload(con, g.user, user_id))

    @app.post("/api/users/<int:user_id>/qualifications")
    def submit_qualification(user_id):
        require_session()
        con = connection()
        private_subject(con, g.user, user_id)
        request.max_content_length = MAX_EVIDENCE_BYTES + 1024 * 1024
        if set(request.form) - {"type_id", "valid_from", "valid_until", "number"} or set(request.files) - {"evidence"}:
            raise QualificationError("Send only a type, validity dates, a number and optional evidence.")
        if len(request.files.getlist("evidence")) > 1:
            raise QualificationError("Attach one PDF, JPEG or PNG per submission.")
        tid, until = integer(request.form.get("type_id")), expiry(request.form.get("valid_until"))
        start = expiry(request.form.get("valid_from"), "Valid from")
        number = licence_number(request.form.get("number"))
        if start and until and start > until:
            raise QualificationError("Valid from must be on or before Valid until.")
        uploaded = request.files.get("evidence")
        staged = destination = None
        committed = False
        filename = mime = None
        compressed, size = False, 0
        read, filled = None, {}
        try:
            if uploaded and uploaded.filename:
                filename = secure_filename(uploaded.filename)[:160]
                if not filename:
                    raise QualificationError("Choose an evidence filename.")
                folder = evidence_root()
                folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                try:
                    staged, compressed, size, _, _ = storage.save_attachment(uploaded.stream, folder, MAX_EVIDENCE_BYTES)
                except ValueError as exc:
                    raise QualificationError("Evidence must be non-empty and at most 20 MiB.") from exc
                mime = evidence_format(staged, compressed, filename)
                kind = con.execute("SELECT * FROM qualification_types WHERE id=?", (tid,)).fetchone()
                # Empty fields are read from the evidence (the reviewer confirms them); typed values are kept.
                wanted = [field for field, key in (("number", "requires_number"), ("valid_from", "requires_start"),
                                                   ("valid_until", "requires_expiry")) if kind is not None and kind[key]]
                if number is None and "number" not in wanted and mime == "application/pdf":
                    number = pdf_number(staged, compressed)  # as before: from the text only
                entered = dict(number=number, valid_from=start, valid_until=until)
                if any(entered[field] is None for field in wanted):
                    # no OCR here: the dialog already read the chosen file and filled its OCR results into the form
                    read = read_evidence(staged, compressed, mime, entered, wanted, ocr=False)
                    for field in wanted:
                        if entered[field] is None and field in read["found"]:
                            filled[field] = read["found"][field]["source"]
                            entered[field] = read["found"][field]["value"]
                    if entered["valid_from"] and entered["valid_until"] and entered["valid_from"] > entered["valid_until"]:
                        for field in ("valid_from", "valid_until"):
                            if filled.pop(field, None):
                                entered[field] = None
                    number, start, until = entered["number"], entered["valid_from"], entered["valid_until"]
            with db.write_lock, con:
                con.execute("BEGIN IMMEDIATE")
                private_subject(con, g.user, user_id)
                kind = con.execute("SELECT * FROM qualification_types WHERE id=? AND active=1", (tid,)).fetchone()
                if kind is None:
                    raise QualificationError("Choose an active qualification type.")
                if kind["requires_evidence"] and staged is None:
                    raise QualificationError("This qualification requires evidence.")
                if kind["requires_expiry"] and until is None:
                    raise QualificationError("This qualification requires an expiry date.")
                if kind["requires_start"] and start is None:
                    raise QualificationError("This qualification requires a Valid from date.")
                if con.execute("SELECT 1 FROM qualification_submissions WHERE user_id=? AND type_id=? AND status='pending'",
                               (user_id, tid)).fetchone():
                    raise QualificationError("There is already a pending submission for this type. Withdraw it first.", 409)
                if staged:
                    destination = evidence_root() / (uuid.uuid4().hex + (".gz" if compressed else ".bin"))
                    os.replace(staged, destination)
                sid = con.execute(
                    "INSERT INTO qualification_submissions(user_id,type_id,valid_from,valid_until,number,status,submitted_by,"
                    " submitted_at,evidence_path,filename,mime,compressed,size_bytes) VALUES (?,?,?,?,?,'pending',?,?,?,?,?,?,?)",
                    (user_id, tid, start, until, number, g.user["id"], _utc_now(),
                     destination.name if destination else None, filename, mime, int(compressed), size)).lastrowid
                if read and read["codes"] is not None:
                    con.execute("UPDATE qualification_submissions SET qr_json=? WHERE id=?",
                                (json.dumps({"codes": read["codes"], "checked_at": _utc_now()}), sid))
                details = dict(id=sid, category=kind["name"], valid_from=start, valid_until=until, number=number,
                               has_evidence=bool(destination))
                if filled:
                    details["read_from"] = filled  # field: 'qr', 'text' or 'ocr'
                if read and read["issues"]:
                    details["differs"] = read["issues"]
                audit(con, user_id, g.user["id"], "submitted", details)
                if not g.user["is_admin"]:
                    person = con.execute("SELECT COALESCE(display_name, username) FROM users WHERE id=?",
                                         (user_id,)).fetchone()[0]
                    mail.notify_admins(con, f"Qualification to approve: {kind['name']} of {person}",
                                       f"{person} submitted {kind['name']}"
                                       + (f", valid until {until}" if until else "") + ". It waits for approval.",
                                       "/people?tab=approvals")
            committed = True
            if destination and not (read and read["codes"] is not None) \
                    and app.config.get("QUALIFICATION_QR_BACKGROUND", True) \
                    and licence_qr.available(pdf=mime == "application/pdf"):
                actor = g.user["id"]

                def read_later():  # a few seconds per PDF: not while the person waits
                    try:
                        con2 = connection()
                        row = con2.execute("SELECT * FROM qualification_submissions WHERE id=?", (sid,)).fetchone()
                        if row:
                            read_qr(con2, row, evidence_root(), actor)
                    except Exception:  # noqa: BLE001 - the reviewer can still run Check QR codes
                        logging.getLogger(__name__).exception("reading the QR code of submission %s failed", sid)
                threading.Thread(target=read_later, daemon=True, name="licence-qr").start()
            return jsonify(ok=True, id=sid, number=number, valid_from=start, valid_until=until, read_from=filled,
                           issues=read["issues"] if read else []), 201
        finally:
            if staged:
                staged.unlink(missing_ok=True)
            if destination and not committed:
                destination.unlink(missing_ok=True)

    @app.post("/api/users/<int:user_id>/qualifications/read")
    def read_qualification_evidence(user_id):
        """What the chosen evidence says, before submitting: nothing is stored and the file is deleted again."""
        require_session()
        con = connection()
        private_subject(con, g.user, user_id)
        request.max_content_length = MAX_EVIDENCE_BYTES + 1024 * 1024
        if set(request.form) - {"type_id", "valid_from", "valid_until", "number"} or set(request.files) != {"evidence"} \
                or len(request.files.getlist("evidence")) > 1:
            raise QualificationError("Send one evidence file and optionally the entered values.")
        entered = dict(valid_from=expiry(request.form.get("valid_from"), "Valid from"),
                       valid_until=expiry(request.form.get("valid_until")),
                       number=licence_number(request.form.get("number")))
        uploaded = request.files["evidence"]
        filename = secure_filename(uploaded.filename or "")[:160]
        if not filename:
            raise QualificationError("Choose an evidence filename.")
        folder = evidence_root()
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        with reading_lock:  # one read at a time per person: each can take up to READ_SECONDS
            if g.user["id"] in reading:
                raise QualificationError("The previous file is still being read; try again when it is done.", 429)
            reading.add(g.user["id"])
        staged = None
        try:
            try:
                staged, compressed, _, _, _ = storage.save_attachment(uploaded.stream, folder, MAX_EVIDENCE_BYTES)
            except ValueError as exc:
                raise QualificationError("Evidence must be non-empty and at most 20 MiB.") from exc
            read = read_evidence(staged, compressed, evidence_format(staged, compressed, filename), entered,
                                 seconds=app.config.get("QUALIFICATION_READ_SECONDS", READ_SECONDS))
        finally:
            with reading_lock:
                reading.discard(g.user["id"])
            if staged:
                staged.unlink(missing_ok=True)
        qr = read["qr"]
        if qr is not None and not g.user["is_admin"]:
            qr = {key: qr[key] for key in ("status", "authority", "issues")}
        return jsonify(found=read["found"], dates=read["dates"], issues=read["issues"], notes=read["notes"], qr=qr)

    @app.post("/api/qualification-submissions/<int:sid>/review")
    def review_qualification(sid):
        require_session()
        body = json_body(("action",))
        action = body.get("action")
        if action not in ("approve", "reject", "withdraw"):
            raise QualificationError("Choose approve, reject or withdraw.")
        con = connection()
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            row = visible_submission(con, g.user, sid)
            if action != "withdraw" and not g.user["is_admin"]:
                raise QualificationError("Administrator approval required.", 403)
            if row["status"] != "pending":
                raise QualificationError("Only pending submissions can be reviewed or withdrawn.", 409)
            if action == "approve":
                if not row["active"]:
                    raise QualificationError("This qualification type is disabled.")
                if row["requires_expiry"] and not row["valid_until"]:
                    raise QualificationError("An expiry date is required before approval. Withdraw and resubmit with an expiry date.")
                if row["requires_start"] and not row["valid_from"]:
                    raise QualificationError("A Valid from date is required before approval. Withdraw and resubmit with a Valid from date.")
                if row["requires_number"] and not row["number"]:
                    raise QualificationError("A number is required before approval. Add it with Edit.")
                if row["requires_evidence"] and (
                        not row["evidence_path"] or not (evidence_root() / row["evidence_path"]).is_file()):
                    raise QualificationError("Evidence must be available before approval.")
                con.execute("UPDATE qualification_submissions SET status='superseded'"
                            " WHERE user_id=? AND type_id=? AND status='approved'", (row["user_id"], row["type_id"]))
            state = {"approve": "approved", "reject": "rejected", "withdraw": "withdrawn"}[action]
            con.execute("UPDATE qualification_submissions SET status=?,reviewed_by=?,reviewed_at=? WHERE id=?",
                        (state, g.user["id"], _utc_now(), sid))
            if action in ("reject", "withdraw"):
                from training import clear_progress_for_submission
                clear_progress_for_submission(con, sid)
            else:
                from training import queue_access_after_approval
                queue_access_after_approval(con, sid)
            audit(con, row["user_id"], g.user["id"], state,
                  dict(id=sid, category=row["category"], valid_from=row["valid_from"], valid_until=row["valid_until"]))
        return jsonify(ok=True)

    @app.put("/api/qualification-submissions/<int:sid>")
    def edit_qualification_dates(sid):
        """Admins correct the validity dates or the number of a submission in any status."""
        admin()
        body = json_body(("valid_from", "valid_until", "number"))
        if not body:
            raise QualificationError("Send valid_from, valid_until and/or number.")
        con = connection()
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            row = visible_submission(con, g.user, sid)
            start = expiry(body["valid_from"], "Valid from") if "valid_from" in body else row["valid_from"]
            until = expiry(body["valid_until"]) if "valid_until" in body else row["valid_until"]
            if start and until and start > until:
                raise QualificationError("Valid from must be on or before Valid until.")
            if row["requires_expiry"] and until is None:
                raise QualificationError("This qualification requires an expiry date.")
            if row["requires_start"] and start is None:
                raise QualificationError("This qualification requires a Valid from date.")
            number = licence_number(body["number"]) if "number" in body else row["number"]
            if row["requires_number"] and row["status"] == "approved" and number is None:
                raise QualificationError("This qualification requires a number.")
            if (start, until) != (row["valid_from"], row["valid_until"]):
                con.execute("UPDATE qualification_submissions SET valid_from=?,valid_until=? WHERE id=?",
                            (start, until, sid))
                audit(con, row["user_id"], g.user["id"], "dates-changed",
                      dict(id=sid, category=row["category"], valid_from=start, valid_until=until,
                           old_valid_from=row["valid_from"], old_valid_until=row["valid_until"]))
            if number != row["number"]:
                con.execute("UPDATE qualification_submissions SET number=? WHERE id=?", (number, sid))
                audit(con, row["user_id"], g.user["id"], "number-changed",
                      dict(id=sid, category=row["category"], number=number, old_number=row["number"]))
        return jsonify(ok=True)

    @app.get("/api/qualification-submissions/<int:sid>/evidence")
    def download_qualification_evidence(sid):
        return send_evidence(sid, inline=False)

    @app.get("/api/qualification-submissions/<int:sid>/evidence/view")
    def view_qualification_evidence(sid):
        return send_evidence(sid, inline=True)

    def send_evidence(sid, inline):
        row = visible_submission(connection(), g.user, sid)
        if not row["evidence_path"]:
            raise QualificationError("No evidence file is available.", 404)
        path = evidence_root() / row["evidence_path"]
        if not path.is_file():
            app.logger.error("Missing private evidence for qualification submission %s", sid)
            raise QualificationError("Evidence is unavailable. Contact an administrator.", 503)
        # Inline only for the same safe types as flight attachments; PDF viewers break under a sandbox.
        mimetype = storage.view_type(row["filename"] or "") if inline else row["mime"]
        if not mimetype:
            raise QualificationError("This file type cannot be viewed in the browser; download it instead.", 415)
        headers = {"Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{row["filename"]}"',
                   "Content-Length": str(row["size_bytes"]), "Cache-Control": "private, no-store",
                   "X-Content-Type-Options": "nosniff"}
        if not (inline and mimetype == "application/pdf"):
            headers["Content-Security-Policy"] = "sandbox; default-src 'none'" + ("; img-src 'self'" if inline else "")
        return Response(storage.chunks(path, bool(row["compressed"])), mimetype=mimetype, headers=headers)

    @app.delete("/api/qualification-submissions/<int:sid>/evidence")
    @app.delete("/api/qualification-submissions/<int:sid>")
    def delete_qualification(sid):
        admin()
        con = connection()
        evidence_only = request.path.endswith("/evidence")
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            row = visible_submission(con, g.user, sid)
            if row["evidence_path"]:
                (evidence_root() / row["evidence_path"]).unlink(missing_ok=True)
            if evidence_only:
                con.execute("UPDATE qualification_submissions SET evidence_path=NULL,filename=NULL,mime=NULL,"
                            " qr_json=NULL,evidence_deleted_at=? WHERE id=?", (_utc_now(), sid))
            else:
                con.execute("DELETE FROM qualification_submissions WHERE id=?", (sid,))
            audit(con, row["user_id"], g.user["id"], "evidence-deleted" if evidence_only else "deleted",
                  dict(id=sid, category=row["category"], valid_from=row["valid_from"],
                       valid_until=row["valid_until"], status=row["status"]))
        return jsonify(ok=True)

    @app.post("/api/admin/qualifications/fill-numbers")
    def fill_qualification_numbers():
        """Fill the empty number of approved qualifications of types that require one, from their PDF evidence."""
        admin()
        con = connection()
        rows = con.execute(
            "SELECT q.id,q.user_id,q.evidence_path,q.compressed,q.mime,t.name AS category,"
            " COALESCE(u.display_name,u.username) AS person FROM qualification_submissions q"
            " JOIN qualification_types t ON t.id=q.type_id JOIN users u ON u.id=q.user_id"
            " WHERE q.status='approved' AND t.requires_number=1 AND COALESCE(q.number,'')=''"
            " ORDER BY person, t.sort_order").fetchall()
        found, unread, later = [], [], []
        deadline = time.monotonic() + 90  # OCR takes seconds per scan: stop in time and let the admin run it again
        for row in rows:  # read the PDFs before taking the write lock
            path = evidence_root() / row["evidence_path"] if row["evidence_path"] else None
            pdf = path is not None and row["mime"] == "application/pdf"
            number = pdf_number(path, bool(row["compressed"])) if pdf else None
            if number is None and pdf and ocr_available():
                if time.monotonic() > deadline:
                    later.append(dict(id=row["id"], user_id=row["user_id"], person=row["person"],
                                      category=row["category"]))
                    continue
                number = ocr_number(path, bool(row["compressed"]))
            item = dict(id=row["id"], user_id=row["user_id"], person=row["person"], category=row["category"])
            if number:
                found.append(dict(item, number=number))
            else:
                unread.append(item)
        filled = []
        with db.write_lock, con:
            con.execute("BEGIN IMMEDIATE")
            for item in found:
                if con.execute("UPDATE qualification_submissions SET number=? WHERE id=? AND COALESCE(number,'')=''",
                               (item["number"], item["id"])).rowcount:
                    audit(con, item["user_id"], g.user["id"], "number-changed",
                          dict(id=item["id"], category=item["category"], number=item["number"], old_number=None,
                               source="evidence"))
                    filled.append(item)
        return jsonify(filled=filled, unread=unread, later=later, ocr=ocr_available())

    @app.post("/api/admin/qualifications/check-qr")
    def check_qualification_qr():
        """Read the QR codes of approved and pending licences with evidence: those not read yet, or with
        {"before": "<UTC time>"} also those last read before then (the dialog's Read all: one run, read once each).
        Reads for about {"seconds": n} (default 20, at most 90) and returns the results with how many are left, so the
        dialog calls it again until none are left and shows the progress."""
        admin()
        if not licence_qr.available():
            raise QualificationError("QR codes cannot be read on this server: install zbar-tools and poppler-utils.", 503)
        body = request.get_json(silent=True) or {}
        before = body.get("before") if isinstance(body.get("before"), str) else None
        seconds = body.get("seconds", 20)
        seconds = min(90, max(0, seconds)) if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else 20
        con = connection()
        rows = con.execute(
            "SELECT q.*,t.name AS category,COALESCE(u.display_name,u.username) AS person"
            " FROM qualification_submissions q JOIN qualification_types t ON t.id=q.type_id JOIN users u ON u.id=q.user_id"
            " WHERE q.status IN ('approved','pending') AND q.evidence_path IS NOT NULL"
            " AND (q.qr_json IS NULL OR (? IS NOT NULL AND COALESCE(json_extract(q.qr_json,'$.checked_at'),'') < ?))"
            " ORDER BY person, t.sort_order", (before, before)).fetchall()
        if seconds == 0:  # only count, so the dialog can show the progress from the start
            return jsonify(results=[], later=len(rows), total=len(rows))
        results, later = [], 0
        deadline = time.monotonic() + seconds
        for row in rows:
            if time.monotonic() > deadline:
                later += 1
                continue
            try:
                result = read_qr(con, row, evidence_root(), g.user["id"])
            except Exception:  # noqa: BLE001 - one unreadable file must not stop the others
                logging.getLogger(__name__).exception("reading the QR code of submission %s failed", row["id"])
                con.execute("UPDATE qualification_submissions SET qr_json=? WHERE id=?",
                            (json.dumps({"codes": [], "checked_at": _utc_now(), "error": True}), row["id"]))
                con.commit()
                result = dict(status="none", number=None, issues=["could not be read"], authority=None, filled=None)
            results.append(dict(id=row["id"], user_id=row["user_id"], person=row["person"], category=row["category"],
                                status=result["status"], number=result["number"], issues=result["issues"],
                                authority=(result["authority"] or {}).get("name"),
                                filled=result["filled"]))
        return jsonify(results=results, later=later, total=len(rows))

    @app.get("/api/pilots")
    def list_pilots():
        """Active people with their approved, unexpired qualifications, for documents such as the Operations
        Manual. Admins only (a read-only API key of an admin account is enough). No contact details."""
        if not g.user["is_admin"]:
            raise QualificationError("Administrator access required.", 403)
        wanted = {name.strip().casefold() for name in (request.args.get("qualification") or "").split(",") if name.strip()}
        only_active = request.args.get("active", "1") not in ("0", "false")
        today = datetime.date.today().isoformat()
        people = {}
        for row in connection().execute(
                "SELECT u.id,COALESCE(u.display_name,u.username) AS name,u.is_active,t.name AS type,q.number,"
                " q.valid_from,q.valid_until,t.internal FROM qualification_submissions q"
                " JOIN users u ON u.id=q.user_id JOIN qualification_types t ON t.id=q.type_id"
                " WHERE q.status='approved' AND (q.valid_until IS NULL OR q.valid_until>=?)"
                " AND (q.valid_from IS NULL OR q.valid_from<=?) ORDER BY name, t.sort_order, t.name", (today, today)):
            if only_active and not row["is_active"]:
                continue
            person = people.setdefault(row["id"], dict(id=row["id"], name=row["name"], qualifications=[]))
            person["qualifications"].append(dict(type=row["type"], number=row["number"], valid_from=row["valid_from"],
                                                 valid_until=row["valid_until"], internal=bool(row["internal"])))
        pilots = [p for p in people.values()
                  if not wanted or wanted & {q["type"].casefold() for q in p["qualifications"]}]
        return jsonify(pilots=pilots, date=today)

    @app.get("/api/users/<int:user_id>/qualification-audit")
    def qualification_history(user_id):
        admin()
        con = connection()
        private_subject(con, g.user, user_id)
        rows = con.execute(
            "SELECT a.action,a.created_at,a.details_json,COALESCE(u.display_name,u.username) AS actor"
            " FROM qualification_audit a LEFT JOIN users u ON u.id=a.actor_id"
            " WHERE a.subject_id=? ORDER BY a.id DESC LIMIT 100", (user_id,)).fetchall()
        return jsonify(events=[dict(r) for r in rows])
