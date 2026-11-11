"""Tests of mavlab_logs against a small local stub of the server API (no real server, logs or keys)."""
import gzip
import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mavlab_logs as ml  # noqa: E402
from mavlab_logs import settings  # noqa: E402

DATA, LOG = b"paparazzi data" * 100, b"<protocol/>"
SHA = hashlib.sha256(DATA).hexdigest()
OTHER = b"LOG00030" * 50  # flight 20, a Betaflight log on its own
LOG_OF = {7: 1, 8: 1, 20: 2}  # flight number -> log


class Stub(BaseHTTPRequestHandler):
    state = None

    def log_message(self, *args):
        pass

    def reply(self, status, body, headers=None):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def body(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        return json.loads(raw) if self.headers.get("Content-Type") == "application/json" else raw

    def authorized(self):
        s = self.state
        s["auth"].append(self.headers.get("Authorization"))
        if self.headers.get("Authorization") != f"Bearer {s['key']}":
            self.reply(401, {"error": "invalid API key"})
            return False
        return True

    def do_GET(self):
        s = self.state
        if not self.authorized():
            return
        if self.path == "/api/me":
            self.reply(200, {"user": {"id": 2, "display_name": "Pilot"}})
        elif self.path.startswith("/api/flights/") and self.path.endswith("/files"):
            s["files_calls"] += 1
            flight = int(self.path.split("/")[3])
            if LOG_OF.get(flight) == 1:
                self.reply(200, {"flight_id": flight, "log_id": 1, "sha256": SHA, "files": [
                    {"kind": "data", "name": "a.data", "url": "/api/logs/1/download/data"},
                    {"kind": "log", "name": "a.log", "url": "/api/logs/1/download/log"}]})
            elif flight in LOG_OF:
                self.reply(200, {"flight_id": flight, "log_id": 2, "sha256": hashlib.sha256(OTHER).hexdigest(),
                                 "files": [{"kind": "data", "name": "LOG00030.BFL",
                                            "url": "/api/logs/2/download/data"}]})
            else:
                self.reply(404, {"error": f"no flight {flight}"})
        elif self.path == "/api/logs/2/download/data":
            self.reply(200, OTHER)
        elif self.path.startswith("/api/logs/1/download/"):
            raw = DATA if self.path.endswith("data") else LOG
            if s.get("corrupt"):
                raw = b"x" + raw
            if "gzip" in self.headers.get("Accept-Encoding", ""):
                self.reply(200, gzip.compress(raw), {"Content-Encoding": "gzip"})
            else:
                self.reply(200, raw)
        elif self.path == "/api/ingest/9":
            s["polls"] += 1
            done = s["polls"] > 1
            self.reply(200, {"status": "stored" if done else "accepted", "log_id": 3 if done else None,
                             "log_status": "done" if done else None, "flight_ids": [11, 12] if done else None})
        else:
            self.reply(404, {"error": "not found"})

    def do_POST(self):
        s = self.state
        if self.path == "/api/cli-login":
            s["client"] = self.body()["client"]
            self.reply(200, {"device_code": "secret", "user_code": "BCDF-GHJK", "interval": 0, "expires_in": 5,
                             "verification_uri_complete": "http://stub/cli-login?code=BCDF-GHJK"})
        elif self.path == "/api/cli-login/token":
            s["token_polls"] += 1
            if s["token_polls"] == 1:
                self.reply(400, {"error": "authorization_pending"})
            else:
                s["key"] = "logdb_new"
                self.reply(200, {"access_token": "logdb_new", "expires_at": "2099-01-01T00:00:00Z",
                                 "user": {"display_name": "Pilot"}})
        elif not self.authorized():
            return
        elif self.path == "/api/keys/revoke-all":
            s["revoked"] = self.body()
            self.reply(200, {"revoked": 1})
        elif self.path == "/api/logs/check":
            self.reply(200, {"known": {d: {"has_log": True, "mine": True} for d in self.body()["sha256"]
                                       if d in s["known"]}})
        elif self.path == "/api/upload":
            raw = self.body()
            s["uploads"].append([part.split(b"\r\n")[0].decode() for part in raw.split(b'name="names"\r\n\r\n')[1:]])
            self.reply(200, {"results": [{"name": s["uploads"][-1][0], "status": "accepted", "ingest_id": 9}]})
        else:
            self.reply(404, {"error": "not found"})


class ClientTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)
        env = patch.dict(os.environ, {"MAVLAB_LOGS_CONFIG": str(self.dir / "config"),
                                      "MAVLAB_LOGS_CACHE": str(self.dir / "cache")})
        env.start()
        self.addCleanup(env.stop)
        for name in ("LOGDB_SERVER", "LOGDB_API_KEY"):
            os.environ.pop(name, None)
        Stub.state = dict(key="logdb_old", auth=[], files_calls=0, token_polls=0, polls=0,
                          uploads=[], known=set())
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        connected = patch.object(ml, "_client", None)
        connected.start()
        self.addCleanup(connected.stop)
        browser = patch("webbrowser.open")
        self.browser = browser.start()
        self.addCleanup(browser.stop)

    def client(self, **kw):
        return ml.Client(self.url, quiet=True, retries=0, **kw)

    def test_connect_signs_in_through_the_browser_and_redoes_a_refused_login(self):
        with self.assertRaises(ml.Error):
            ml.fetch(7)  # not connected yet
        with redirect_stderr(io.StringIO()) as err, redirect_stdout(io.StringIO()) as out:
            client = ml.connect(self.url)
        self.assertEqual(client.key_source, "browser login")
        self.assertIn("BCDF-GHJK", err.getvalue())
        self.assertIn("as Pilot", out.getvalue())
        self.browser.assert_called_once_with("http://stub/cli-login?code=BCDF-GHJK")
        self.assertTrue(Stub.state["client"].startswith("Python on "))
        stored = settings.entry(self.url)
        self.assertEqual((stored["key"], stored["expires_at"]), ("logdb_new", "2099-01-01T00:00:00Z"))
        if os.name != "nt":
            self.assertEqual((self.dir / "config" / "config.json").stat().st_mode & 0o777, 0o600)
        # the next connection reuses the stored key; a revoked key starts a new login
        Stub.state.update(key="logdb_other", token_polls=0)
        client = self.client()
        self.assertEqual(client.key_source, "browser login")
        self.assertEqual(client.me()["display_name"], "Pilot")
        self.assertEqual(Stub.state["auth"][-2:], ["Bearer logdb_new", "Bearer logdb_new"])
        # an expired stored key is not even tried
        settings.store(self.url, "logdb_new", "2000-01-01T00:00:00Z")
        self.assertIsNone(self.client().key)
        settings.store(self.url, "logdb_new", "2099-01-01T00:00:00Z")
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            ml.connect(self.url)
            self.assertEqual(ml.revoke_all(), 1)
        self.assertEqual(Stub.state["revoked"], {"include_manual": False})
        self.assertIsNone(settings.entry(self.url))

    def test_keys_from_the_environment_or_arguments_are_never_replaced(self):
        self.assertEqual(self.client().server, self.url)
        self.assertEqual(ml.Client("logs.example.org", quiet=True).server, "https://logs.example.org")
        with patch.dict(os.environ, LOGDB_API_KEY="logdb_old", LOGDB_SERVER="https://elsewhere.example"):
            self.assertIsNone(self.client().key)  # a key for another server is not sent here
        with patch.dict(os.environ, LOGDB_API_KEY="logdb_old"):
            self.assertEqual(self.client().me()["id"], 2)
        with patch.dict(os.environ, LOGDB_API_KEY="logdb_wrong", LOGDB_SERVER=self.url):
            with redirect_stderr(io.StringIO()) as err:
                client = ml.Client(self.url, retries=0)
            self.assertIn("Using LOGDB_API_KEY from the environment", err.getvalue())
            with self.assertRaisesRegex(ml.AuthError, "environment variable LOGDB_API_KEY"):
                client.me()
        with self.assertRaises(ml.AuthError):
            self.client(key="logdb_wrong").me()
        with self.assertRaises(ml.AuthError):
            self.client(login=False).me()
        self.assertEqual(Stub.state["token_polls"], 0)

    def test_fetch_caches_by_sha256_and_works_offline(self):
        client = self.client(key="logdb_old")
        path, = client.fetch([7, "7"])
        self.assertEqual(path, self.dir / "cache" / "logs" / SHA / "a.data")
        self.assertEqual(path.read_bytes(), DATA)
        self.assertEqual(path.with_suffix(".log").read_bytes(), LOG)
        self.server.shutdown()
        self.server.server_close()  # connections are refused now: offline
        with redirect_stderr(io.StringIO()):
            self.assertEqual(self.client(key="logdb_old").fetch(7), [path])
        with self.assertRaises(ValueError):
            client.fetch([True])

    def test_corrupt_download_is_not_kept(self):
        Stub.state["corrupt"] = True
        with self.assertRaises(ml.Error):
            self.client(key="logdb_old").fetch(7)
        self.assertEqual(list((self.dir / "cache" / "logs" / SHA).iterdir()), [])

    def test_scope_returns_whole_logs_per_log_or_per_number(self):
        client = self.client(key="logdb_old")
        data, other = self.dir / "cache" / "logs" / SHA / "a.data", None
        for scope in ("selected", "all", "log"):  # binary logs are never cut: the same files
            paths = client.fetch([8, 20, 7], scope=scope)
            self.assertEqual(paths[0], data)
            other = other or paths[1]
            self.assertEqual(paths, [data, other])
        self.assertEqual(other.name, "LOG00030.BFL")
        self.assertEqual(other.read_bytes(), OTHER)
        self.assertEqual(client.fetch([8, 20, 7, 8], scope="flight"), [data, other, data])
        with self.assertRaises(ValueError):
            client.fetch(7, scope="nope")
        with self.assertRaises(ml.HTTPError):
            client.fetch([7, 99])

    def test_upload_pairs_skips_and_waits(self):
        data, bfl = self.dir / "f.data", self.dir / "LOG1.BFL"
        data.write_bytes(DATA)
        data.with_suffix(".log").write_bytes(LOG)
        bfl.write_bytes(b"blackbox")
        client = self.client(key="logdb_old")
        with patch("time.sleep"):
            receipts = client.upload([data.with_suffix(".log"), data], wait=True)
        self.assertEqual(len(receipts), 1)
        self.assertEqual((receipts[0]["status"], receipts[0]["log_id"], receipts[0]["flight_ids"]), ("done", 3, [11, 12]))
        self.assertEqual(Stub.state["uploads"], [["f.data", "f.log"]])
        client.upload(bfl, name="Hex 1/LOG1.BFL")
        self.assertEqual(Stub.state["uploads"][-1], ["Hex 1/LOG1.BFL"])
        Stub.state["known"].add(SHA)
        self.assertEqual(client.upload(data)[0]["status"], "skipped")
        data.with_suffix(".log").unlink()
        with self.assertRaises(FileNotFoundError):
            client.upload(data)

    def test_cache_folders_follow_the_platform(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(settings.os.path, "expanduser", lambda p: "/h"):
            with patch.object(settings.sys, "platform", "darwin"):
                self.assertEqual(settings.config_dir(), Path("/h/Library/Application Support/mavlab-logs"))
                self.assertEqual(settings.default_cache_dir(), Path("/h/Library/Caches/mavlab-logs"))
            with patch.object(settings.sys, "platform", "linux"):
                self.assertEqual(settings.default_cache_dir(), Path("/h/.cache/mavlab-logs"))
        ml.set_cache_dir(self.dir / "elsewhere")
        with patch.dict(os.environ):
            del os.environ["MAVLAB_LOGS_CACHE"]
            self.assertEqual(ml.cache_dir(), (self.dir / "elsewhere").resolve())


if __name__ == "__main__":
    unittest.main()
