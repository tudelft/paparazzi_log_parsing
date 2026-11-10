"""Download and upload flight logs of a MAVLab flight log database from Python.

    import mavlab_logs as ml
    ml.connect("logs.example.org")     # signs in through the browser when needed
    paths = ml.download([1234, 1240])  # flight numbers, as in /flight/1234
    ml.upload("LOG00030.BFL", wait=True)

See connect() and Client for the details; the command line tool is ``mavlab-logs``.
"""
from .client import AuthError, Client, Error, HTTPError, ProcessingFailed
from .settings import cache_dir, set_cache_dir

__version__ = "0.1.0"
__all__ = ["Client", "connect", "download", "upload", "revoke_all", "cache_dir", "set_cache_dir",
           "Error", "AuthError", "HTTPError", "ProcessingFailed"]

_client = None


def connect(server, key=None, *, login=True, insecure=False):
    """Connect to the logbook server for download(), upload() and revoke_all(), and return the Client.

    server: URL or host name, e.g. "logs.example.org" (https:// is added).
    key: API key from the account page; default LOGDB_API_KEY, else the key of the last browser login to this
    server, else a new browser login (sign in, compare the code, press Allow; the key lasts a week).
    Values taken from the environment are announced. login=False raises AuthError instead of opening the browser.
    """
    global _client
    client = Client(server, key, login=login, insecure=insecure)
    try:
        user = client.me()
    except OSError as exc:
        raise Error(f"cannot reach {client._origin()}: {exc}") from exc
    print(f"Connected to {client.server} as {user.get('display_name', '?')}.", flush=True)
    _client = client
    return client


def _connected():
    if _client is None:
        raise Error("not connected: call mavlab_logs.connect() first")
    return _client


def download(flights, mat=False, force=False):
    """Local paths of flights by number; see Client.download."""
    return _connected().download(flights, mat=mat, force=force)


def upload(paths, name=None, wait=False, skip_existing=True):
    """Upload log files; see Client.upload."""
    return _connected().upload(paths, name=name, wait=wait, skip_existing=skip_existing)


def revoke_all(include_manual=False):
    """Revoke your API keys from browser logins (all computers); include_manual=True also those made on the
    account page. See Client.revoke_all."""
    return _connected().revoke_all(include_manual)
