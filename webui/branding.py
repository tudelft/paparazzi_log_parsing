"""Site name, logos and footer text from the LOGDB_SITE_* / LOGDB_FOOTER_* settings (see config.py)."""
import re
from pathlib import Path

from config import env

LOGO_DIR = Path(__file__).resolve().parent / "static" / "logos"
LOGO_NAME = re.compile(r"[A-Za-z0-9_-]+")


def site_name():
    return env("SITE_NAME").strip() or "Flight logbook"


def logo(name, href=None):
    """{name, night, href} for static/logos/NAME.svg, or None when there is no such logo. `night` is the variant for
    dark backgrounds (NAME-white.svg when present)."""
    name = (name or "").strip()
    if not LOGO_NAME.fullmatch(name) or not (LOGO_DIR / f"{name}.svg").is_file():
        return None
    night = f"{name}-white" if (LOGO_DIR / f"{name}-white.svg").is_file() else name
    return {"name": name, "night": night, "href": href}


def footer_logo_entries():
    """(name, link) pairs of LOGDB_FOOTER_LOGOS."""
    out = []
    for item in (env("FOOTER_LOGOS") or "").split(","):
        name, _, href = item.strip().partition("=")
        if name.strip():
            out.append((name.strip(), href.strip() or None))
    return out


def site():
    """What the pages show; theme.js receives it as window.SITE."""
    footer_logos = []
    for name, href in footer_logo_entries():
        entry = logo(name, href if href and re.match(r"https?://", href) else None)
        if entry:
            footer_logos.append(entry)
    return {"name": site_name(), "logo": logo(env("SITE_LOGO")),
            "footer_text": (env("FOOTER_TEXT") or "").strip() or site_name(), "footer_logos": footer_logos}


def problems():
    """Configuration errors for `app.py check-config`."""
    errors = []
    names = [("SITE_LOGO", env("SITE_LOGO"), None)] if env("SITE_LOGO") else []
    names += [("FOOTER_LOGOS", name, href) for name, href in footer_logo_entries()]
    for setting, name, href in names:
        if not logo(name):
            errors.append(f"LOGDB_{setting}: no logo {name!r} (expected {LOGO_DIR / (name + '.svg')})")
        if href and not re.match(r"https?://", href):
            errors.append(f"LOGDB_{setting}: link of {name!r} must start with http:// or https://")
    return errors
