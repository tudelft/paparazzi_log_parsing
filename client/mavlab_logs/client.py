"""HTTP client for the flight log database: browser login, cached downloads by flight number, and uploads."""
import hashlib
import http.client
import json
import math
import os
import socket
import ssl
import sys
import tempfile
import time
import uuid
import webbrowser
import zlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

from . import settings

CHUNK = 1 << 20
TRANSIENT = {408, 429, 500, 502, 503, 504}


class Error(RuntimeError):
    """Any failure reported by this package."""


class HTTPError(Error):
    def __init__(self, status, message, retry_after=None, code=None):
        super().__init__(f"HTTP {status}: {message}")
        self.status, self.retry_after, self.code = status, retry_after, code


class AuthError(Error):
    """Not signed in, an invalid or expired key, or a login that was denied or timed out."""


class ProcessingFailed(Error):
    """The server could not store or parse an uploaded log."""


def _retry_delay(header, fallback):
    if header:
        try:
            seconds = float(header)
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                return fallback
        if math.isfinite(seconds):
            return max(0, seconds)
    return fallback


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(CHUNK):
            h.update(block)
    return h.hexdigest()


def _flight_ids(flights):
    if isinstance(flights, (int, str)):
        flights = [flights]
    ids = []
    for f in flights:
        if isinstance(f, bool) or not (isinstance(f, int) or (isinstance(f, str) and f.strip().isdigit())):
            raise ValueError(f"a flight number is a positive integer (the number in /flight/<number>): {f!r}")
        ids.append(int(f))
    return ids


def _write_atomic(target, write):
    """Call write(handle) on a temporary file next to target, then move it into place."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=target.parent, prefix="." + target.name[:40] + "-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as handle:
            write(handle)
        os.replace(temp, target)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


class Client:
    """Connection to one logbook server.

    server: URL, else LOGDB_SERVER, else the default stored by the last login.
    key: API key, else LOGDB_API_KEY (used only with LOGDB_SERVER's server), else the stored login for the server.
    login: sign in through the browser when there is no valid key (False raises AuthError instead).
    """

    def __init__(self, server=None, key=None, *, login=True, insecure=False, timeout=600, retries=3, quiet=False):
        env_server = os.environ.get("LOGDB_SERVER")
        server = server or env_server or settings.load().get("default")
        if not server:
            raise ValueError("no server: pass server='https://…', set LOGDB_SERVER, or log in once with a server")
        self.server = settings.normal_server(server)
        self.key, self.key_source = key, "argument" if key else None
        env_key = os.environ.get("LOGDB_API_KEY")
        if not self.key and env_key and (not env_server or settings.normal_server(env_server) == self.server):
            self.key, self.key_source = env_key, "environment"
        if not self.key:
            stored = settings.entry(self.server)
            if stored and stored.get("key") and not settings.expired(stored):
                self.key, self.key_source = stored["key"], "stored"
        self.allow_login, self.quiet = login, quiet
        self.timeout, self.retries = timeout, retries
        u = urlsplit(self.server)
        self.https = u.scheme == "https"
        self.host, self.port = u.hostname, u.port or (443 if self.https else 80)
        self.prefix = u.path.rstrip("/")
        self.ctx = ssl._create_unverified_context() if insecure else None

    def _say(self, text):
        if not self.quiet:
            print(text, file=sys.stderr, flush=True)

    # --- HTTP ----------------------------------------------------------------------------------------------------
    def _conn(self, timeout=None):
        if self.https:
            return http.client.HTTPSConnection(self.host, self.port, timeout=timeout or self.timeout,
                                               context=self.ctx)
        return http.client.HTTPConnection(self.host, self.port, timeout=timeout or self.timeout)

    def _send(self, method, path, body=None, headers=None, auth=True, sink=None):
        """One request. Returns the decoded JSON, or with sink(response) the sink's result for a 2xx response."""
        h = dict(headers or {})
        if auth:
            h["Authorization"] = f"Bearer {self.key}"
        c = self._conn()
        try:
            c.request(method, self.prefix + path, body=body() if callable(body) else body, headers=h)
            r = c.getresponse()
            if sink is not None and r.status < 400:
                return sink(r)
            raw = r.read()
        finally:
            c.close()
        try:
            data = json.loads(raw)
        except ValueError:
            if r.status < 400:
                raise Error(f"expected JSON from {path}, received HTTP {r.status}") from None
            data = {"error": raw[:200].decode("utf-8", "replace")}
        if r.status >= 400:
            message = data.get("error_description") or data.get("error") if isinstance(data, dict) else data
            raise HTTPError(r.status, message, r.getheader("Retry-After"),
                            data.get("error") if isinstance(data, dict) else None)
        if not isinstance(data, dict):
            raise Error(f"invalid JSON response from {path}")
        return data

    def _request(self, method, path, body=None, headers=None, auth=True, sink=None, retries=None):
        retries = self.retries if retries is None else retries
        relogged = False
        attempt = 0
        while True:
            if auth and not self.key:
                self._need_login()
                relogged = True
            try:
                return self._send(method, path, body, headers, auth, sink)
            except (OSError, http.client.HTTPException, HTTPError) as exc:
                if isinstance(exc, HTTPError) and exc.status == 401 and auth:
                    if relogged or self.key_source in ("argument", "environment") or not self.allow_login:
                        raise AuthError(f"the server refused the API key ({self.key_source}): {exc}") from exc
                    self.key = None  # an expired or revoked stored login: sign in again, once
                    continue
                if isinstance(exc, ssl.SSLCertVerificationError):
                    raise
                if isinstance(exc, HTTPError) and exc.status not in TRANSIENT:
                    raise
                if attempt >= retries:
                    raise
                delay = _retry_delay(exc.retry_after if isinstance(exc, HTTPError) else None, min(60, 2 ** attempt))
                attempt += 1
                self._say(f"Temporary request failure: {exc}; retrying in {delay:g} s.")
                time.sleep(delay)

    def _json(self, method, path, payload=None, **kw):
        body = None if payload is None else json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"} if payload is not None else None
        return self._request(method, path, body, headers, **kw)

    # --- login ---------------------------------------------------------------------------------------------------
    def _need_login(self):
        if not self.allow_login:
            raise AuthError(f"not signed in to {self.server}: call login() or pass an API key")
        self.login()

    def login(self, open_browser=True, client=None):
        """Sign in through the browser (RFC 8628 device flow) and store the key, valid for a week.

        Prints a link and a code; the browser opens the link, where you sign in, compare the code and allow it.
        Returns the user (dict with display_name, …)."""
        client = client or f"Python on {socket.gethostname()}"
        start = self._json("POST", "/api/cli-login", {"client": client}, auth=False)
        url = start["verification_uri_complete"]
        self._say(f"To sign in to {self.server}, open\n  {url}\nand check that it shows the code {start['user_code']}.")
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:  # no browser on this machine: the link above still works elsewhere
                pass
        interval = max(1, int(start.get("interval", 5)))
        deadline = time.monotonic() + int(start.get("expires_in", 600))
        while time.monotonic() < deadline:
            time.sleep(interval)
            try:
                token = self._json("POST", "/api/cli-login/token", {"device_code": start["device_code"]}, auth=False)
            except HTTPError as exc:
                if exc.code == "authorization_pending":
                    continue
                if exc.code == "slow_down":
                    interval += 5
                    continue
                raise AuthError(f"login failed: {exc}") from exc
            self.key, self.key_source = token["access_token"], "stored"
            user = token.get("user") or {}
            settings.store(self.server, self.key, token.get("expires_at", ""), user.get("display_name", ""))
            self._say(f"Signed in as {user.get('display_name', '?')} until {token.get('expires_at', '?')}.")
            return user
        raise AuthError("login timed out: the code was not confirmed in the browser in time")

    def use_key(self, key):
        """Check an API key from the account page and store it for this server (it does not expire by itself)."""
        self.key, self.key_source = key, "argument"
        user = self.me()
        settings.store(self.server, key, "", user.get("display_name", ""))
        self.key_source = "stored"
        return user

    def logout(self):
        """Revoke the stored key on the server (when it still works) and forget it on this computer."""
        stored = settings.entry(self.server)
        if stored and stored.get("key"):
            self.key = stored["key"]
            try:
                self._send("DELETE", "/api/key")
            except (OSError, http.client.HTTPException, HTTPError):
                pass  # already expired/revoked, or offline: forgetting it locally is what remains
            settings.forget(self.server)
        self.key, self.key_source = None, None

    def me(self):
        """The signed-in user (display_name, username, …)."""
        return self._request("GET", "/api/me")["user"]

    # --- downloads -----------------------------------------------------------------------------------------------
    def _download(self, path, target, sha256=None):
        """Stream path into target (decompressing a gzip transfer), checking the SHA-256 when given."""
        def write(handle):
            def sink(response):
                gz = response.getheader("Content-Encoding", "").lower() == "gzip"
                inflate = zlib.decompressobj(31) if gz else None
                digest = hashlib.sha256()
                while block := response.read(CHUNK):
                    if inflate:
                        block = inflate.decompress(block)
                    digest.update(block)
                    handle.write(block)
                if inflate:
                    tail = inflate.flush()
                    digest.update(tail)
                    handle.write(tail)
                return digest.hexdigest()
            handle.seek(0)
            handle.truncate()
            got = self._request("GET", path, headers={"Accept-Encoding": "gzip"}, sink=sink)
            if sha256 and got != sha256:
                raise Error(f"{target.name}: downloaded file does not match the server's SHA-256")
        _write_atomic(target, write)

    def _index(self, flight_id):
        return settings.cache_dir() / "flights" / settings.server_tag(self.server) / f"{flight_id}.json"

    def download(self, flights, mat=False, force=False):
        """Download flights by number (the number in /flight/<number>) and return their local paths, in order.

        By default the original log the flight was recorded in: the path of its data file (.data, .BFL, .ulg, …),
        with a Paparazzi .log next to it. mat=True returns the flight's MATLAB export (.mat) instead, as pprz_fetch.
        Files are cached (see cache_dir()) and reused; a log is identified by its SHA-256, so it never goes stale.
        force=True downloads again. Without a connection, the copies cached before are returned."""
        ids = _flight_ids(flights)
        return [self._mat(i, force) if mat else self._raw(i, force) for i in ids]

    def _raw(self, flight_id, force):
        index = self._index(flight_id)
        try:
            info = self._request("GET", f"/api/flights/{flight_id}/files")
        except (OSError, http.client.HTTPException) as exc:
            try:
                info = json.loads(index.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise exc from None
            folder = settings.cache_dir() / "logs" / info["sha256"]
            if all((folder / f["name"]).is_file() for f in info["files"]):
                self._say(f"Flight {flight_id}: offline, using the cached copy ({exc})")
                return folder / info["files"][0]["name"]
            raise
        folder = settings.cache_dir() / "logs" / info["sha256"]
        for f in info["files"]:
            target = folder / Path(f["name"]).name
            if force or not target.is_file():
                self._say(f"Flight {flight_id}: downloading {target.name}")
                self._download(f["url"], target, info["sha256"] if f["kind"] == "data" else None)
        _write_atomic(index, lambda h: h.write(json.dumps(info).encode()))
        return folder / Path(info["files"][0]["name"]).name

    def _mat(self, flight_id, force):
        folder = settings.cache_dir() / "mat" / settings.server_tag(self.server)
        old = sorted(folder.glob(f"flight_{flight_id}_*.mat"), key=lambda p: p.stat().st_mtime)
        path = f"/api/flights/{flight_id}/matlab"
        try:
            job = self._json("POST", path, {"check": True})
        except (OSError, http.client.HTTPException) as exc:
            if not old:
                raise
            self._say(f"Flight {flight_id}: offline, using the cached copy ({exc})")
            return old[-1]
        target = folder / f"flight_{flight_id}_{job['id'][:16]}.mat"
        if target.is_file() and not force:
            return target
        if job["status"] != "ready":
            job = self._json("POST", path, {"check": False})
        last = None
        while job["status"] != "ready":
            if job["status"] in ("failed", "missing"):
                raise Error(f"Flight {flight_id}: the export failed: {job.get('error')}")
            pct = round(100 * float(job.get("progress") or 0))
            if pct != last:
                self._say(f"Flight {flight_id}: preparing the export on the server, {pct}%")
                last = pct
            time.sleep(1)
            job = self._request("GET", f"/api/matlab-exports/{job['id']}")
        self._say(f"Flight {flight_id}: downloading {job['filename']}")
        self._download(job["download"], target)
        for stale in old:
            if stale != target:
                stale.unlink(missing_ok=True)
        return target

    # --- uploads -------------------------------------------------------------------------------------------------
    def _check(self, digests):
        return self._json("POST", "/api/logs/check", {"sha256": digests})["known"]

    def _post_files(self, files):
        """files: list of (stored name, path), streamed as multipart/form-data."""
        boundary = uuid.uuid4().hex
        parts = []
        for name, _ in files:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="names"\r\n\r\n{name}\r\n'.encode())
        for name, path in files:
            filename = os.path.basename(path).replace('"', "_")
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{filename}"'
                         "\r\nContent-Type: application/octet-stream\r\n\r\n".encode())
            parts.append(path)
            parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        length = sum(len(p) if isinstance(p, bytes) else os.path.getsize(p) for p in parts)

        def body():
            for p in parts:
                if isinstance(p, bytes):
                    yield p
                else:
                    with open(p, "rb") as f:
                        while block := f.read(CHUNK):
                            yield block

        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(length)}
        return self._request("POST", "/api/upload", body, headers)

    def _wait(self, ingest_id, poll, max_wait):
        started = time.monotonic()
        last = None
        while True:
            result = self._request("GET", f"/api/ingest/{ingest_id}")
            state, log_state = result.get("status"), result.get("log_status")
            if state in ("error", "rejected"):
                raise ProcessingFailed(result.get("error") or f"upload {state}")
            if state in ("stored", "duplicate"):
                if not result.get("log_id"):
                    raise ProcessingFailed(result.get("error") or "the upload has no stored log")
                if log_state == "done":
                    return result
                if log_state == "error":
                    raise ProcessingFailed("parsing failed: " + (result.get("log_error") or "unknown error"))
                if log_state is None:
                    raise ProcessingFailed("the stored log was deleted before it was parsed")
            if time.monotonic() - started >= max_wait:
                raise Error("timed out waiting for processing; the server may still be working")
            if (state, log_state) != last:
                self._say(f"Upload {ingest_id}: {state}" + (f", parser {log_state}" if log_state else ""))
                last = (state, log_state)
            time.sleep(poll)

    def upload(self, paths, name=None, wait=False, skip_existing=True, poll=5, max_wait=3600):
        """Upload log files and return one receipt (dict) per log, in order.

        A Paparazzi .data (or its .log) is sent together with the file of the same name next to it; other formats
        (.BFL, .ulg, .bin, .tlog, …) on their own. name is the stored name of a single file (default: its file
        name); a folder in it, such as "Hex 1/LOG00030.BFL", can link the log to that fleet aircraft. A log that
        you already uploaded is skipped (status "skipped") unless skip_existing=False. With wait=True the call
        returns once the server has parsed each log (status "done", with log_id and flight_ids for download())
        and raises ProcessingFailed if storing or parsing failed."""
        if isinstance(paths, (str, os.PathLike)):
            paths = [paths]
        paths = [Path(p) for p in paths]
        if name is not None and len(paths) != 1:
            raise ValueError("name can only be given for a single file")
        groups, seen = [], set()
        for path in paths:
            if not path.is_file():
                raise FileNotFoundError(path)
            stored = (name or path.name).replace(os.sep, "/")
            files, paired = [(stored, path)], False
            ext = path.suffix.lower()
            if ext in (".data", ".log"):
                other = ".log" if ext == ".data" else ".data"
                companion = next((c for c in (path.with_suffix(other), path.with_suffix(other.upper())) if c.is_file()),
                                 None)
                if companion:
                    pair = {ext: path, other: companion}
                    stem = stored.rsplit(".", 1)[0]
                    files, paired = [(stem + ".data", pair[".data"]), (stem + ".log", pair[".log"])], True
                elif ext == ".data":
                    raise FileNotFoundError(f"no matching .log next to {path}")
                # else: a .log on its own (an ArduPilot text log)
            key = os.path.realpath(files[0][1])
            if key not in seen:
                seen.add(key)
                groups.append((files, paired))
        receipts = []
        for files, paired in groups:
            label = files[0][0]
            if skip_existing:
                digest = sha256_file(files[0][1])
                known = self._check([digest]).get(digest)
                if known and known.get("mine", True) and (known.get("has_log") or not paired):
                    self._say(f"{label}: already on the server")
                    receipts.append({"name": label, "status": "skipped", "reason": "already uploaded"})
                    continue
            self._say(f"{label}: uploading")
            results = self._post_files(files).get("results")
            if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
                raise Error("the server returned an invalid upload receipt")
            receipt = results[0]
            if receipt.get("status") != "accepted" or type(receipt.get("ingest_id")) is not int:
                raise Error(f"{label}: " + (receipt.get("reason") or f"upload not accepted: {receipt}"))
            if wait:
                result = self._wait(receipt["ingest_id"], poll, max_wait)
                receipt.update(status="done", log_id=result["log_id"], flight_ids=result.get("flight_ids", []))
            receipts.append(receipt)
        return receipts
