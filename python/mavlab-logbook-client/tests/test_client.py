"""Tests of mavlab_logbook_client against a small local stub of the server API (no real server, logs or keys)."""
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
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mavlab_logbook_client as ml  # noqa: E402
from mavlab_logbook_client import settings  # noqa: E402

DATA, LOG = b"paparazzi data" * 100, b"<protocol/>"
SHA = hashlib.sha256(DATA).hexdigest()
OTHER = b"LOG00030" * 50  # flight 20, a Betaflight log on its own
LOG_OF = {7: 1, 8: 1, 20: 2}  # flight number -> log
MAT = b"\x89HDF\r\n" * 10  # an export


def job(name, ready, version=1):
    """An export job as the server reports it; its id changes with the content (version)."""
    jid = hashlib.sha256(f"{name}|{version}".encode()).hexdigest()[:32]
    out = {"id": jid, "status": "ready" if ready else "missing", "progress": 0, "error": None,
           "filename": name + ".mat"}
    if ready:
        out["download"] = f"/api/matlab-exports/{jid}/download"
    return out


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
        elif self.path == "/api/logbook?compact=1":
            rows = [
                {"flight_id": 8, "takeoff_utc": "2026-11-05T09:59:46.627Z", "date_ok": True, "duration_s": 90,
                 "aircraft": "Hex 1", "pic_user": {"id": 2, "display_name": "Pilot"}, "pic": "pilot",
                 "project_name": "P", "goal": "test", "verdict": "flight", "log_id": 2,
                 "simulation": {"detected": False}},
                {"flight_id": 7, "takeoff_utc": "1970-01-01T00:02:00Z", "date_ok": False, "aircraft": "Quad",
                 "pic_user": None, "pic": "Guest", "verdict": "ground", "log_id": 1,
                 "simulation": {"detected": True}},
                {"flight_id": None, "historical_id": 3, "aircraft": "Paper", "verdict": "flight"}]
            self.reply(200, gzip.compress(json.dumps(rows).encode()), {"Content-Encoding": "gzip"})
        elif self.path.startswith("/api/flights/") and self.path.endswith("/files"):
            s["files_calls"] += 1
            if s.get("fail"):
                self.reply(s["fail"], {"error": "failing"})
                return
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
        elif self.path.startswith("/api/matlab-exports/"):
            jid = self.path.split("/")[3]
            if self.path.endswith("/download"):
                s["downloads"].append(jid)
                self.reply(200, MAT)
            else:
                s["status_polls"] += 1
                self.reply(200, job(s["jobs"][jid], True, s["version"]))
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
        elif s.get("fail") and "/matlab" in self.path:
            self.reply(s["fail"], {"error": "failing"})
        elif self.path == "/api/flights/matlab":
            body = self.body()
            exports = []
            for log in dict.fromkeys(LOG_OF[f] for f in body["flight_ids"]):
                wanted = [f for f in body["flight_ids"] if LOG_OF[f] == log]
                name = f"log{log}_{body['scope']}" + ("_" + "_".join(map(str, wanted))
                                                       if body["scope"] == "selected" else "")
                exports.append(dict(self.submit(name, body["check"]), log_id=log, flight_ids=wanted,
                                    scope=body["scope"]))
            self.reply(200 if body["check"] else 202, {"exports": exports})
        elif self.path.startswith("/api/flights/") and self.path.endswith("/matlab"):
            flight = int(self.path.split("/")[3])
            self.reply(200, dict(self.submit(f"flight{flight}", self.body()["check"]), flight_id=flight))
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


    def submit(self, name, check):
        """An export is ready once it was asked for without check."""
        s = self.state
        out = job(name, name in s["built"], s["version"])
        s["jobs"][out["id"]] = name
        if not check and name not in s["built"]:
            s["built"].add(name)
            out["status"] = "queued"
        return out


class ClientTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)
        env = patch.dict(os.environ, {"MAVLAB_LOGBOOK_CONFIG": str(self.dir / "config"),
                                      "MAVLAB_LOGBOOK_CACHE": str(self.dir / "cache")})
        env.start()
        self.addCleanup(env.stop)
        for name in ("LOGDB_SERVER", "LOGDB_API_KEY"):
            os.environ.pop(name, None)
        Stub.state = dict(key="logdb_old", auth=[], files_calls=0, token_polls=0, polls=0,
                          uploads=[], known=set(), jobs={}, built=set(), version=1, downloads=[], status_polls=0)
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
            ml.fetch_raw(7)  # not connected yet
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
        self.assertEqual(Stub.state["revoked"], {})
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

    def test_keys_never_travel_over_plain_http(self):
        for url in ("http://logs.example.org", "http://10.0.0.1:8000"):
            with self.assertRaisesRegex(ValueError, "unencrypted"):
                ml.Client(url, quiet=True)
        for url in ("http://localhost:8000", "http://127.0.0.1", "http://[::1]:8000"):
            self.assertEqual(settings.normal_server(url + "/"), url)

    def test_fetch_downloads_exports_per_log_or_flight_and_reuses_them(self):
        client = self.client(key="logdb_old")
        folder = self.dir / "cache" / "mat" / settings.server_tag(self.url)
        with patch("time.sleep"):
            first = client.fetch([8, 7], scope="selected")
            self.assertEqual(len(first), 1)
            self.assertTrue(first[0].name.startswith("log_1_selected_"))
            self.assertEqual(first[0].read_bytes(), MAT)
            self.assertEqual(client.fetch([8, 7]), first)  # cached: no download
            self.assertEqual(len(Stub.state["downloads"]), 1)
            self.assertEqual((folder / "flight_logs.txt").read_text(), "8 1\n7 1\n")
            per_flight = client.fetch([7, 8, 7], scope="flight")
            self.assertEqual([p.name.split("_")[1] for p in per_flight], ["7", "8"])
            # a changed export (new parser or logbook data) replaces the old whole-log file
            whole = client.fetch(7, scope="log")
            Stub.state["version"] = 2
            again = client.fetch(7, scope="log")
            self.assertNotEqual(whole, again)
            self.assertFalse(whole[0].exists())
            Stub.state["fail"] = 503  # a failing server: the cached copies, with a warning
            with self.assertWarnsRegex(UserWarning, "HTTP 503"):
                self.assertEqual(client.fetch([7, 8], scope="log"), again)
            with self.assertWarnsRegex(UserWarning, "cached copy"):
                self.assertEqual(client.fetch(7, scope="flight"), per_flight[:1])
            with self.assertRaises(ml.HTTPError):
                client.fetch(20)  # never fetched: nothing to fall back on
            Stub.state["fail"] = 409  # not a server failure: raised
            with self.assertRaises(ml.HTTPError):
                client.fetch(7)
        with self.assertRaises(ValueError):
            client.fetch(7, scope="nope")

    def test_fetch_raw_caches_by_sha256_and_works_offline(self):
        client = self.client(key="logdb_old")
        path, = client.fetch_raw([7, "7"])
        Stub.state["fail"] = 503  # a failing server: the cached copy, with a warning
        with self.assertWarnsRegex(UserWarning, "HTTP 503"):
            self.assertEqual(client.fetch_raw(7), [path])
        Stub.state["fail"] = 404  # not a server failure: raised
        with self.assertRaises(ml.HTTPError):
            client.fetch_raw(7)
        del Stub.state["fail"]
        self.assertEqual(path, self.dir / "cache" / "logs" / SHA / "a.data")
        self.assertEqual(path.read_bytes(), DATA)
        self.assertEqual(path.with_suffix(".log").read_bytes(), LOG)
        self.server.shutdown()
        self.server.server_close()  # connections are refused now: offline
        with self.assertWarnsRegex(UserWarning, "using the cached copy"):
            self.assertEqual(self.client(key="logdb_old").fetch_raw(7), [path])
        with self.assertRaises(ValueError):
            client.fetch_raw([True])

    def test_logbook_lists_real_flights_unless_all(self):
        client = self.client(key="logdb_old")
        row, = client.logbook()
        self.assertEqual(row, {"flight": 8, "date": datetime(2026, 11, 5, 9, 59, 46, tzinfo=timezone.utc),
                               "date_ok": True, "aircraft": "Hex 1", "duration_min": 1.5, "pic": "Pilot",
                               "project": "P", "goal": "test", "verdict": "flight", "simulation": False, "log": 2})
        ground = client.logbook(all=True)[1]  # a clock that was not set (1970) is not a date
        self.assertEqual((ground["flight"], ground["date"], ground["date_ok"], ground["simulation"], ground["pic"],
                          ground["duration_min"]), (7, None, False, True, "Guest", None))
        self.assertEqual(len(client.logbook(all=True)), 2)  # the paper record has no flight number

    def test_corrupt_download_is_not_kept(self):
        Stub.state["corrupt"] = True
        with self.assertRaises(ml.Error):
            self.client(key="logdb_old").fetch_raw(7)
        self.assertEqual(list((self.dir / "cache" / "logs" / SHA).iterdir()), [])

    def test_fetch_raw_returns_one_whole_log_per_log(self):
        client = self.client(key="logdb_old")
        data, other = client.fetch_raw([8, 20, 7])
        self.assertEqual(data, self.dir / "cache" / "logs" / SHA / "a.data")
        self.assertEqual(other.name, "LOG00030.BFL")
        self.assertEqual(other.read_bytes(), OTHER)
        with self.assertRaises(ml.HTTPError):
            client.fetch_raw([7, 99])

    def test_upload_pairs_skips_and_waits(self):
        data, bfl = self.dir / "f.data", self.dir / "LOG1.BFL"
        data.write_bytes(DATA)
        data.with_suffix(".log").write_bytes(LOG)
        bfl.write_bytes(b"blackbox")
        client = self.client(key="logdb_old")
        with patch("time.sleep"):
            receipts = client.upload([data.with_suffix(".log"), data], wait=True)
        self.assertEqual(len(receipts), 1)
        self.assertEqual((receipts[0]["status"], receipts[0]["log_id"], receipts[0]["flight_ids"]),
                         ("done", 3, [11, 12]))
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
                self.assertEqual(settings.config_dir(), Path("/h/Library/Application Support/mavlab-logbook-client"))
                self.assertEqual(settings.default_cache_dir(), Path("/h/Library/Caches/mavlab-logbook-client"))
            with patch.object(settings.sys, "platform", "linux"):
                self.assertEqual(settings.default_cache_dir(), Path("/h/.cache/mavlab-logbook-client"))
        ml.set_cache_dir(self.dir / "elsewhere")
        with patch.dict(os.environ):
            del os.environ["MAVLAB_LOGBOOK_CACHE"]
            self.assertEqual(ml.cache_dir(), (self.dir / "elsewhere").resolve())


if __name__ == "__main__":
    unittest.main()
