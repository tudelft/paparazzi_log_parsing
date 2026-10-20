"""Server settings.

Every setting is named LOGDB_<NAME>. It is read from the environment first, then from the config file:
$LOGDB_CONFIG, else /etc/flight-logs/logdb.env, else logdb.env next to this file (KEY=value lines, # comments).

    python3 config.py check      # show the effective settings and check them
    python3 config.py example    # print a commented config file with all settings
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATHS = [Path("/etc/flight-logs/logdb.env"), HERE / "logdb.env"]

# name: (default, description); a default of None means "not set" (the description says what happens then)
SETTINGS = {
    "Server": {
        "PUBLIC_URL": (None, "Address users open, e.g. https://logs.example.org. Used for the SAML URLs; "
                             "when empty they follow the request"),
        "BIND": ("127.0.0.1:8000", "Address:port gunicorn (and `python3 app.py`) listens on"),
        "THREADS": ("8", "Gunicorn threads (there is always exactly one gunicorn process)"),
        "PROXIES": ("0", "Number of reverse proxies (nginx/Caddy) in front of the app. Set to 1 behind a proxy so "
                         "client addresses and https are seen correctly; keep 0 when clients connect directly"),
    },
    "Storage and parsing": {
        "WEB_DATA": (str(HERE / "data"), "Local directory with the database, session key and SAML keys. Keep it on "
                                         "a local disk: SQLite must not run on a network share"),
        "LOG_DIR": (None, "Directory for all uploaded logs and their flight paths; may be a network share "
                          "(default: WEB_DATA/logs)"),
        "BACKUP_DIR": (None, "Where `app.py backup` writes a consistent copy of the database and keys "
                             "(overwritten each time; may be a network share)"),
        "FASTREADER": (str(HERE.parent / "cpp" / "build" / "paparazzi_log_parsing"), "Parser binary"),
        "WORKERS": (None, "Logs parsed in parallel (default: 2-4 depending on the CPU count)"),
        "INGEST_WORKERS": ("2", "Background threads that hash, deduplicate and unzip uploads"),
        "PATH_RATE": ("5", "Flight path points stored per second (0 disables the map path)"),
    },
    "Login": {
        "COOKIE_SECURE": ("0", "1 when the site is served over https (cookies are then only sent over https)"),
        "SESSION_HOURS": ("12", "How long a login lasts"),
        "SECRET_KEY": (None, "Session signing key (default: generated once in WEB_DATA/secret_key)"),
    },
    "TU Delft SAML login": {
        "SAML_ENABLED": ("1", "0 hides the 'Sign in with TU Delft' button"),
        "SAML_METADATA_URL": ("https://login.tudelft.nl/sso/saml2/idp/metadata.php", "Identity provider metadata"),
        "SAML_ENTITY_ID": (None, "This service's SAML entity ID (default: <PUBLIC_URL>/saml/metadata)"),
    },
    "Pilot licences": {
        "OPERATIONS_TOKEN": (None, "GitHub token with read access to the Operations repo, so the app can show "
                                   "certificate PDFs without everyone needing GitHub"),
        "OPERATIONS_REPO": ("tudelft/Operations", "owner/name of the Operations repository"),
        "OPERATIONS_REF": ("EU_OPS", "Branch that holds the Pilots folder"),
        "OPERATIONS_PILOTS_PATH": ("Outdoor_Flight/Pilots", "Folder of per-pilot certificate PDFs"),
    },
}
ALL = {name: v for group in SETTINGS.values() for name, v in group.items()}
CLIENT_ONLY = {"SERVER", "API_KEY", "CONFIG"}  # upload_logs.py and the config file path, not server settings


def _config_path():
    if os.environ.get("LOGDB_CONFIG"):
        return Path(os.environ["LOGDB_CONFIG"])
    return next((p for p in CONFIG_PATHS if p.is_file()), None)


def _read_file(path):
    values = {}
    if path is None:
        return values
    try:
        lines = path.read_text().splitlines()
    except OSError as e:
        sys.exit(f"cannot read config file {path}: {e}")
    for n, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key.startswith("LOGDB_"):
            sys.exit(f"{path}:{n}: expected LOGDB_<NAME>=value")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key[6:]] = value
    return values


CONFIG_FILE = _config_path()
FILE_VALUES = _read_file(CONFIG_FILE)


def source(name):
    if "LOGDB_" + name in os.environ:
        return "environment"
    if name in FILE_VALUES:
        return str(CONFIG_FILE)
    return "default"


def env(name, default=None):
    """The setting as a string; empty values count as unset."""
    if name not in ALL:
        raise KeyError(f"unknown setting LOGDB_{name}")
    value = os.environ.get("LOGDB_" + name)
    if value is None:
        value = FILE_VALUES.get(name)
    if value is None or value == "":
        value = ALL[name][0] if default is None else default
    return value


def env_int(name, default=None):
    try:
        return int(env(name, default))
    except (TypeError, ValueError):
        sys.exit(f"LOGDB_{name} must be a whole number, not {env(name, default)!r}")


def env_float(name, default=None):
    try:
        return float(env(name, default))
    except (TypeError, ValueError):
        sys.exit(f"LOGDB_{name} must be a number, not {env(name, default)!r}")


def env_bool(name):
    return str(env(name)).strip().lower() in ("1", "true", "yes", "on")


def env_path(name):
    """A path setting; relative paths are relative to this directory."""
    return (HERE / os.path.expanduser(env(name))).resolve()


def log_dir():
    return env_path("LOG_DIR") if env("LOG_DIR") else env_path("WEB_DATA") / "logs"


def _writable(path):
    probe = path if path.exists() else next((p for p in path.parents if p.exists()), None)
    return probe is not None and os.access(probe, os.W_OK)


def bind_address():
    host, _, port = env("BIND").rpartition(":")
    if not host or not port.isdigit():
        sys.exit(f"LOGDB_BIND must look like 127.0.0.1:8000, not {env('BIND')!r}")
    return host.strip("[]"), int(port)


# Values for a typical server behind nginx/Caddy with https; everything else stays at its default
SERVER_EXAMPLE = {"PUBLIC_URL": "https://logs.example.org", "PROXIES": "1", "COOKIE_SECURE": "1",
                  "WEB_DATA": "/var/lib/flight-logs", "LOG_DIR": "/mnt/flight-logs/logs",
                  "BACKUP_DIR": "/mnt/flight-logs/backup"}
PORTABLE_DEFAULTS = {"WEB_DATA": "data", "FASTREADER": "../cpp/build/paparazzi_log_parsing"}  # relative to webui/


def example():
    out = ["# Flight log database settings: copy to /etc/flight-logs/logdb.env and edit.",
           "# Commented-out settings use the default shown. Check with: python3 app.py check-config", ""]
    for group, items in SETTINGS.items():
        out.append(f"# --- {group} ---")
        for name, (default, desc) in items.items():
            out.append(f"# {desc}")
            if name in SERVER_EXAMPLE:
                out.append(f"LOGDB_{name}={SERVER_EXAMPLE[name]}")
            else:
                out.append(f"#LOGDB_{name}={PORTABLE_DEFAULTS.get(name, default or '')}")
        out.append("")
    return "\n".join(out)


def check():
    """Print the effective settings and problems. Returns the number of errors."""
    import shutil
    import subprocess

    print(f"Config file: {CONFIG_FILE or 'none (looked for $LOGDB_CONFIG, ' + ', '.join(map(str, CONFIG_PATHS)) + ')'}")
    for group, items in SETTINGS.items():
        print(f"\n{group}")
        for name in items:
            value = str(env_path(name)) if name in PORTABLE_DEFAULTS else env(name)
            shown = "(hidden)" if name in ("SECRET_KEY", "OPERATIONS_TOKEN") and value else (
                value if value is not None else "(not set)")
            print(f"  LOGDB_{name:<18} {shown:<52} {source(name)}")

    errors, warnings = [], []
    known = set(ALL) | CLIENT_ONLY
    for key in FILE_VALUES:
        if key not in known:
            errors.append(f"unknown setting LOGDB_{key} in {CONFIG_FILE} (typo?)")
    for key in os.environ:
        if key.startswith("LOGDB_") and key[6:] not in known:
            warnings.append(f"unknown environment variable {key}")
    for name in ("THREADS", "PROXIES", "INGEST_WORKERS", "SESSION_HOURS") + (("WORKERS",) if env("WORKERS") else ()):
        v = env(name)
        if not v.isdigit():
            errors.append(f"LOGDB_{name} must be a whole number, not {v!r}")
    try:
        float(env("PATH_RATE"))
    except ValueError:
        errors.append(f"LOGDB_PATH_RATE must be a number, not {env('PATH_RATE')!r}")
    host, _, port = env("BIND").rpartition(":")
    if not host or not port.isdigit():
        errors.append(f"LOGDB_BIND must look like 127.0.0.1:8000, not {env('BIND')!r}")

    parser = str(env_path("FASTREADER"))
    if not (os.path.isfile(parser) and os.access(parser, os.X_OK)):
        errors.append(f"parser not found or not executable: {parser} (build it in ../cpp)")
    else:
        try:
            version = subprocess.run([parser, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
            print(f"\nParser: {version or 'no version output'}")
        except (OSError, subprocess.SubprocessError) as e:
            errors.append(f"parser does not run: {e}")

    data = env_path("WEB_DATA")
    if not _writable(data):
        errors.append(f"LOGDB_WEB_DATA {data} is not writable by this user")
    elif data.exists():
        free = shutil.disk_usage(data).free / 1e9
        print(f"Data directory: {data} ({free:.0f} GB free)")
        if free < 2:
            warnings.append(f"only {free:.1f} GB free in {data}")
    logs = log_dir()
    if not _writable(logs):
        errors.append(f"log directory {logs} is not writable by this user (mounted?)")
    elif logs.exists():
        free = shutil.disk_usage(logs).free / 1e9
        print(f"Log directory: {logs} ({free:.0f} GB free)")
        if free < 5:
            warnings.append(f"only {free:.1f} GB free in {logs}")
    if env("BACKUP_DIR"):
        if not _writable(env_path("BACKUP_DIR")):
            errors.append(f"LOGDB_BACKUP_DIR {env_path('BACKUP_DIR')} is not writable by this user (mounted?)")
    else:
        warnings.append("LOGDB_BACKUP_DIR is not set: the database is not backed up by `app.py backup`")

    url = env("PUBLIC_URL") or ""
    secure = env_bool("COOKIE_SECURE")
    if url and not url.startswith(("http://", "https://")):
        errors.append("LOGDB_PUBLIC_URL must start with http:// or https://")
    if url.startswith("https://") and not secure:
        warnings.append("LOGDB_PUBLIC_URL is https but LOGDB_COOKIE_SECURE=0; set it to 1")
    if url.startswith("http://") and secure:
        errors.append("LOGDB_COOKIE_SECURE=1 but LOGDB_PUBLIC_URL is http: logins would not work")
    behind_proxy = env("PROXIES").isdigit() and int(env("PROXIES")) > 0
    if behind_proxy and not host.startswith(("127.", "localhost", "[::1]")):
        warnings.append("LOGDB_PROXIES > 0 while gunicorn listens on a public address: clients could fake their "
                        "address; bind to 127.0.0.1 or set LOGDB_PROXIES=0")
    if not behind_proxy and host.startswith(("127.", "localhost", "[::1]")) and url:
        warnings.append("gunicorn listens on localhost only; if a proxy is in front, set LOGDB_PROXIES=1")
    if env_bool("SAML_ENABLED") and not url:
        warnings.append("TU Delft login is enabled: set LOGDB_PUBLIC_URL so the SAML metadata has the right URLs")

    print()
    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"ERROR: {e}")
    if not warnings and not errors:
        print("Settings look fine.")
    return len(errors)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "example":
        print(example())
    elif cmd == "check":
        sys.exit(1 if check() else 0)
    else:
        sys.exit(__doc__)
