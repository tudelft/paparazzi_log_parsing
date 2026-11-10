"""Download and upload flight logs of a MAVLab flight log database from Python.

    import mavlab_logs as ml
    paths = ml.download([1234, 1240], server="https://logs.example.org")  # opens the browser to sign in once
    ml.upload("LOG00030.BFL", wait=True)                                   # the same server from now on

See Client for the details; the command line tool is ``mavlab-logs``.
"""
from .client import AuthError, Client, Error, HTTPError, ProcessingFailed
from .settings import cache_dir, set_cache_dir

__version__ = "0.1.0"
__all__ = ["Client", "login", "logout", "download", "upload", "cache_dir", "set_cache_dir",
           "Error", "AuthError", "HTTPError", "ProcessingFailed"]


def login(server=None, key=None, open_browser=True):
    """Sign in through the browser (or store an API key from the account page) and return the user."""
    client = Client(server, login=False)
    return client.use_key(key) if key else client.login(open_browser)


def logout(server=None):
    """Revoke and forget the stored key of the server."""
    Client(server, login=False).logout()


def download(flights, server=None, mat=False, force=False):
    """Local paths of flights by number; see Client.download."""
    return Client(server).download(flights, mat=mat, force=force)


def upload(paths, server=None, name=None, wait=False, skip_existing=True):
    """Upload log files; see Client.upload."""
    return Client(server).upload(paths, name=name, wait=wait, skip_existing=skip_existing)
