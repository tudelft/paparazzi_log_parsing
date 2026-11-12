"""Read the QR code on licence evidence (EASA remote pilot certificates and similar) to check what was entered.

The QR code is what the issuing authority puts on a certificate to verify it, so it is a better source than the
printed text (some templates hide a sample number in the text). This module only reads the code: the pages of a
PDF are rendered with pdftoppm (poppler-utils) and the codes decoded with zbarimg (zbar-tools); images are decoded
directly. What a code holds differs per authority, so the result keeps the raw text and picks out what is
recognisable: a certificate number, dates and links. Nothing here contacts an outside service; a link is only
shown to the reviewer, who decides to open it.
"""
import datetime
import gzip
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

PAGES = 2          # certificates carry the code on the first page(s)
DPI = 300          # small codes need a sharp render
SECONDS = 60       # per call of pdftoppm or zbarimg
MAX_TEXT = 2000    # per code, as stored
NUMBER = re.compile(r"[A-Z]{3}-RP-[A-Za-z0-9]{12}|\b[A-Z]{2}\.(?:66|FCL|ATCO|147)\.[A-Z0-9]+(?:\.[A-Z0-9]+)*")
URL = re.compile(r"https?://[^\s\"'<>]+", re.I)
DATES = (  # (pattern, order of year/month/day groups)
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), (1, 2, 3)),
    (re.compile(r"\b(\d{2})[./-](\d{2})[./-](\d{4})\b"), (3, 2, 1)),
    (re.compile(r"\b(\d{4})(\d{2})(\d{2})\b"), (1, 2, 3)),
)


def available(pdf=True):
    return bool(shutil.which("zbarimg") and (not pdf or shutil.which("pdftoppm")))


def _zbar(image):
    """The QR codes in one image, as text."""
    try:
        out = subprocess.run(["zbarimg", "--xml", "-q", "-Sdisable", "-Sqrcode.enable", str(image)],
                             capture_output=True, timeout=SECONDS)
    except (OSError, subprocess.SubprocessError):
        return []
    try:
        root = ET.fromstring(out.stdout.decode("utf-8", "replace") or "<barcodes/>")
    except ET.ParseError:
        return []
    codes = []
    for symbol in root.iter():
        if symbol.tag.endswith("symbol"):
            data = next((d.text for d in symbol if d.tag.endswith("data")), None)
            if data:
                codes.append(data.strip()[:MAX_TEXT])
    return codes


def read_codes(path, compressed, mime):
    """The distinct QR texts on the first pages of a PDF or in an image; [] when none (or the tools are missing)."""
    if not available(pdf=mime == "application/pdf"):
        return []
    with tempfile.TemporaryDirectory() as tmp:
        suffix = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg"}.get(mime)
        if suffix is None:
            return []
        src = Path(tmp) / ("evidence" + suffix)
        try:
            with (gzip.open(path, "rb") if compressed else open(path, "rb")) as source, open(src, "wb") as out:
                shutil.copyfileobj(source, out)
        except OSError:
            return []
        images = [src]
        if suffix == ".pdf":
            try:
                subprocess.run(["pdftoppm", "-r", str(DPI), "-l", str(PAGES), "-png", str(src), str(Path(tmp) / "p")],
                               capture_output=True, timeout=SECONDS)
            except (OSError, subprocess.SubprocessError):
                return []
            images = sorted(Path(tmp).glob("p*.png"))
        codes = []
        for image in images:
            codes += [c for c in _zbar(image) if c not in codes]
        return codes


def _date(groups, order):
    try:
        y, m, d = (int(groups[i - 1]) for i in order)
        return datetime.date(y, m, d).isoformat()
    except ValueError:
        return None


def facts(codes):
    """What can be recognised in the codes: number, dates (ISO, in order of appearance) and links."""
    text = "\n".join(codes)
    hit = NUMBER.search(text)
    dates = []
    for pattern, order in DATES:
        for m in pattern.finditer(text):
            day = _date(m.groups(), order)
            if day and day not in dates:
                dates.append(day)
    return {"number": hit.group() if hit else None, "dates": dates, "links": URL.findall(text)[:3]}


def _same_number(a, b):
    norm = lambda s: re.sub(r"[\s]", "", s or "").upper()  # noqa: E731
    return norm(a) == norm(b)


def check(codes, number, valid_from, valid_until):
    """The QR result for a submission: the codes, what was recognised and the differences with what was entered.
    status: 'none' (no code found), 'match' (a number in the code equals the entered one and every entered date
    appears in it), 'differs' (a recognised number or date disagrees) or 'read' (codes found, nothing to compare)."""
    found = facts(codes)
    issues = []
    if found["number"] and number and not _same_number(found["number"], number):
        issues.append(f"number in the code is {found['number']}")
    compared = bool(found["number"] and number)
    for label, value in (("valid from", valid_from), ("valid until", valid_until)):
        if value and found["dates"]:
            compared = True
            if value not in found["dates"]:
                issues.append(f"{label} {value} is not in the code (dates in it: {', '.join(found['dates'])})")
    status = "none" if not codes else "differs" if issues else "match" if compared else "read"
    return dict(found, codes=codes, issues=issues, status=status,
                checked_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
