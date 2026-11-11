"""Download and upload flight logs of a MAVLab flight log database from Python.

    import mavlab_logbook_client as ml
    ml.connect("logs.example.org")     # signs in through the browser when needed
    rows = ml.logbook()                # the flights you may see, newest first
    mats = ml.fetch([1234, 1240])      # flight numbers, as in /flight/1234; MATLAB exports, as pprz_fetch
    logs = ml.fetch_raw([1234, 1240])  # the original logs (.data with .log, .BFL, .ulg, ...)
    ml.upload("LOG00030.BFL", wait=True)

See connect() and Client for the details; the command line tool is ``mavlab-logbook-client``.
"""
from .client import AuthError, Client, Error, HTTPError, ProcessingFailed
from .settings import cache_dir, set_cache_dir

__version__ = "0.1.1"
__all__ = ["Client", "connect", "logbook", "fetch", "fetch_raw", "upload", "revoke_all", "cache_dir", "set_cache_dir",
           "Error", "AuthError", "HTTPError", "ProcessingFailed"]

_client = None


def connect(server, key=None, *, login=True, insecure=False):
    """Connect to the logbook server for logbook(), fetch(), fetch_raw(), upload() and revoke_all(), and return the
    Client.

    server: URL or host name, e.g. "logs.example.org" (https:// is added; http:// only for localhost).
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
        raise Error("not connected: call mavlab_logbook_client.connect() first")
    return _client


def logbook(all=False):
    """The logbook flights you may see, newest first (dicts with flight, date, aircraft, …); see Client.logbook."""
    return _connected().logbook(all=all)


def fetch(flights, scope="selected", force=False):
    """Local paths of the MATLAB exports (.mat) of flights by number, as MATLAB's pprz_fetch; see Client.fetch."""
    return _connected().fetch(flights, scope=scope, force=force)


def fetch_raw(flights, force=False):
    """Local paths of the original logs of flights by number, one per log; see Client.fetch_raw."""
    return _connected().fetch_raw(flights, force=force)


def upload(paths, name=None, wait=False, skip_existing=True):
    """Upload log files; see Client.upload."""
    return _connected().upload(paths, name=name, wait=wait, skip_existing=skip_existing)


def revoke_all():
    """Revoke your API keys from browser logins (all computers); see Client.revoke_all."""
    return _connected().revoke_all()
