"""Where the stored login and the log cache live; shared with the MATLAB tools (matlab/+pprz/config.m).

config.json in the config folder holds an optional cache folder and the key of the last browser login per server:
``{"version": 1, "cache": "", "servers": [{"server", "key", "expires_at", "user"}]}``.
"""
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

APP = "mavlab-logbook-client"


def _home():
    return Path(os.path.expanduser("~"))


def config_dir():
    """Linux ~/.config/mavlab-logbook-client, macOS ~/Library/Application Support/mavlab-logbook-client,
    Windows %APPDATA%\\mavlab-logbook-client;
    MAVLAB_LOGBOOK_CONFIG chooses another folder."""
    if os.environ.get("MAVLAB_LOGBOOK_CONFIG"):
        return Path(os.environ["MAVLAB_LOGBOOK_CONFIG"])
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or _home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = _home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or _home() / ".config")
    return base / APP


def default_cache_dir():
    """Linux ~/.cache/mavlab-logbook-client, macOS ~/Library/Caches/mavlab-logbook-client,
    Windows %LOCALAPPDATA%\\mavlab-logbook-client\\Cache."""
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or _home() / "AppData" / "Local") / APP / "Cache"
    if sys.platform == "darwin":
        return _home() / "Library" / "Caches" / APP
    return Path(os.environ.get("XDG_CACHE_HOME") or _home() / ".cache") / APP


def cache_dir():
    """The cache folder: MAVLAB_LOGBOOK_CACHE, else the one set with set_cache_dir(), else the default of this OS."""
    return Path(os.environ.get("MAVLAB_LOGBOOK_CACHE") or load().get("cache") or default_cache_dir())


LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def normal_server(url):
    """https://host without a trailing /; a bare host name gets https://. http:// only for this computer, so that
    API keys never travel unencrypted."""
    url = (url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "https://" + url
    if not re.match(r"^https?://[^/\s]+", url):
        raise ValueError(f"the server must start with https://, e.g. https://logs.example.org: {url!r}")
    if url.startswith("http://") and urlsplit(url).hostname not in LOCAL_HOSTS:
        raise ValueError(f"refusing {url}: http:// would send the API key unencrypted; use https:// "
                         "(http:// only for localhost)")
    return url


def server_tag(server):
    """Folder name for the server-specific part of the cache (flight numbers and exports differ per server)."""
    return re.sub(r"[^A-Za-z0-9.-]+", "_", normal_server(server).split("://", 1)[1]).strip("_")


def load():
    path = config_dir() / "config.json"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        cfg = {}
    except ValueError as exc:
        raise RuntimeError(f"{path} is not valid JSON; fix or delete it") from exc
    if not isinstance(cfg, dict):
        raise RuntimeError(f"{path} does not hold a JSON object; fix or delete it")
    servers = cfg.get("servers")
    if isinstance(servers, dict):  # one entry, as MATLAB's jsonencode writes a 1x1 struct
        servers = [servers]
    cfg["servers"] = [s for s in servers or [] if isinstance(s, dict) and s.get("server")]
    return cfg


def save(cfg):
    """Write config.json atomically, readable by this user only (it holds API keys)."""
    folder = config_dir()
    folder.mkdir(parents=True, exist_ok=True)
    cfg = dict(cfg, version=1)
    fd, temp = tempfile.mkstemp(dir=folder, prefix=".config-", suffix=".tmp")
    try:
        if os.name != "nt":
            os.chmod(temp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(cfg, handle, indent=1)
        os.replace(temp, folder / "config.json")
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def entry(server):
    """The stored entry for a server, or None."""
    server = normal_server(server)
    return next((s for s in load()["servers"] if s["server"] == server), None)


def expired(item, margin_s=60):
    stamp = (item or {}).get("expires_at") or ""
    if not stamp:
        return False
    when = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (when - datetime.now(timezone.utc)).total_seconds() < margin_s


def store(server, key, expires_at="", user=""):
    """Remember the key of a browser login for a server."""
    server = normal_server(server)
    cfg = load()
    cfg["servers"] = [s for s in cfg["servers"] if s["server"] != server]
    cfg["servers"].append(dict(server=server, key=key, expires_at=expires_at or "", user=user or ""))
    save(cfg)


def forget(server):
    server = normal_server(server)
    cfg = load()
    cfg["servers"] = [s for s in cfg["servers"] if s["server"] != server]
    save(cfg)


def set_cache_dir(folder):
    """Keep downloads in this folder (also MATLAB's pprz_cache_dir); None or "" returns to the default of this OS."""
    cfg = load()
    cfg["cache"] = str(Path(folder).expanduser().resolve()) if folder else ""
    save(cfg)
