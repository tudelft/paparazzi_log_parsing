import http.client
import io
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import upload_logs as upload
import config
from test_historical import AppTests


class UploadScriptTests(unittest.TestCase):
    def test_small_vm_defaults_and_script_byte_threshold(self):
        with patch.dict(config.FILE_VALUES, {}, clear=True), patch.dict(config.os.environ, {}, clear=True):
            self.assertEqual(config.env_int("WORKERS"), 1)
            self.assertEqual(config.env_int("INGEST_WORKERS"), 1)
            self.assertEqual(config.env_int("BULK_MAX_PENDING"), 8)
            self.assertEqual(config.env_int("BULK_MAX_BYTES"), 16 << 30)
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "example.data"
            data.write_bytes(b"test")
            data.with_suffix(".log").write_bytes(b"xml")
            client = Mock()
            client._request.return_value = {"user": {"display_name": "Pilot"}}
            client.check.return_value = {}
            client.upload.return_value = {"results": [{"status": "accepted", "ingest_id": 1}]}
            args = ["upload_logs.py", directory, "--bulk", "--server", "http://fixture", "--key", "test"]
            with patch.object(upload, "Client", return_value=client), patch("sys.argv", args), \
                    patch.object(upload, "wait_capacity") as capacity, \
                    patch.object(upload, "reserve_capacity", return_value="a" * 32), patch.object(upload, "wait_result"):
                self.assertEqual(upload.main(), 0)
            self.assertEqual(capacity.call_args.args[2:4], (8, 16 << 30))

    def test_dry_run_does_not_reserve_upload_or_poll(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "example.data"
            data.write_bytes(b"test")
            data.with_suffix(".log").write_bytes(b"xml")
            client = Mock()
            client._request.return_value = {"user": {"display_name": "Pilot"}}
            client.check.return_value = {}
            args = ["upload_logs.py", directory, "--bulk", "--dry-run", "--server", "http://fixture", "--key", "test"]
            with patch.object(upload, "Client", return_value=client), patch("sys.argv", args):
                self.assertEqual(upload.main(), 0)
            client.upload.assert_not_called()
            client._request.assert_called_once_with("GET", "/api/me")

    def test_bulk_stops_submitting_after_upload_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            for index in range(2):
                data = Path(directory) / f"{index}.data"
                data.write_bytes(str(index).encode())
                data.with_suffix(".log").write_bytes(b"xml")
            client = Mock()
            client._request.return_value = {"user": {"display_name": "Pilot"}}
            client.check.return_value = {}
            client.upload.side_effect = upload.HTTPError(403, "denied")
            args = ["upload_logs.py", directory, "--bulk", "--server", "http://fixture", "--key", "test"]
            with patch.object(upload, "Client", return_value=client), patch("sys.argv", args), \
                    patch.object(upload, "wait_capacity"), patch.object(upload, "reserve_capacity", return_value="a" * 32):
                self.assertEqual(upload.main(), 1)
            self.assertEqual(client.upload.call_count, 1)

    def test_reservation_waits_for_other_users_and_reuses_token(self):
        client = Mock()
        calls = []

        def reserve(method, path, body, headers):
            request = json.loads(body)
            calls.append(request)
            if len(calls) == 1:
                raise upload.HTTPError(429, "budget full", "7")
            return {"token": request["token"]}

        client._request.side_effect = reserve
        with patch("upload_logs.time.sleep") as sleep:
            token = upload.reserve_capacity(client, 100, 5, 60)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(token, calls[0]["token"])
        sleep.assert_called_once_with(7)

    def test_capacity_checks_count_and_bytes_and_oversized_pair(self):
        client = Mock()
        client._request.side_effect = [
            {"backlog": {"count": 8, "bytes": 10}},
            {"backlog": {"count": 1, "bytes": 95}},
            {"backlog": {"count": 1, "bytes": 20}}]
        with patch("upload_logs.time.sleep") as sleep:
            upload.wait_capacity(client, 10, 8, 100, 5, 60)
        self.assertEqual(sleep.call_count, 2)
        client._request.side_effect = [{"backlog": {"count": 0, "bytes": 0}}]
        upload.wait_capacity(client, 200, 8, 100, 5, 60)

    def test_capacity_requires_new_api_and_times_out(self):
        client = Mock()
        client._request.return_value = {"queue": 1}
        with self.assertRaisesRegex(RuntimeError, "update the server"):
            upload.wait_capacity(client, 10, 8, 100, 5, 60)
        client._request.return_value = {"backlog": {"count": 8, "bytes": 10}}
        with patch("upload_logs.time.monotonic", side_effect=[0, 61]):
            with self.assertRaisesRegex(RuntimeError, "Timed out"):
                upload.wait_capacity(client, 10, 8, 100, 5, 60)

    def test_receipts_wait_through_ingest_and_parse(self):
        client = Mock()
        client._request.side_effect = [
            {"status": "accepted"}, {"status": "hashing"},
            {"status": "stored", "log_id": 1, "log_status": "queued"},
            {"status": "stored", "log_id": 1, "log_status": "parsing"},
            {"status": "stored", "log_id": 1, "log_status": "done"}]
        with patch("upload_logs.time.sleep") as sleep:
            upload.wait_result(client, 1, 5, 60)
        self.assertEqual(sleep.call_count, 4)
        client._request.side_effect = None
        for result, message in [
                ({"status": "rejected", "error": "bad pair"}, "bad pair"),
                ({"status": "duplicate", "error": "another uploader"}, "another uploader"),
                ({"status": "stored", "log_id": 1, "log_status": "error", "log_error": "bad protocol"}, "bad protocol"),
                ({"status": "stored", "log_id": 1, "log_status": None}, "deleted")]:
            client._request.return_value = result
            with self.assertRaisesRegex(RuntimeError, message):
                upload.wait_result(client, 1, 5, 60)

    def test_retries_replay_body_and_respect_retry_after(self):
        client = upload.Client("http://localhost", "key")
        seen = []

        def request(method, path, body, headers):
            seen.append(b"".join(body))
            if len(seen) == 1:
                raise upload.HTTPError(429, "busy", "7")
            return {"ok": True}

        with patch.object(client, "_request_once", side_effect=request), patch("upload_logs.time.sleep") as sleep:
            self.assertEqual(client._request("POST", "/", lambda: iter([b"original"])), {"ok": True})
        self.assertEqual(seen, [b"original", b"original"])
        sleep.assert_called_once_with(7)

    def test_permanent_errors_not_retried_and_transient_bounded(self):
        client = upload.Client("http://localhost", "key", retries=2)
        with patch.object(client, "_request_once", side_effect=upload.HTTPError(403, "denied")) as request:
            with self.assertRaises(upload.HTTPError):
                client._request("GET", "/")
            self.assertEqual(request.call_count, 1)
        with patch.object(client, "_request_once", side_effect=http.client.RemoteDisconnected()) as request:
            with patch("upload_logs.time.sleep"), self.assertRaises(http.client.RemoteDisconnected):
                client._request("GET", "/")
            self.assertEqual(request.call_count, 3)
        self.assertEqual(upload.retry_delay("nan", 3), 3)
        self.assertEqual(upload.retry_delay("invalid", 3), 3)


class UploadStatusTests(AppTests):
    def test_concurrent_importers_cannot_overbook(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "reservations.db")
            destination = sqlite3.connect(path)
            self.con.backup(destination)
            destination.close()
            local = threading.local()
            barrier = threading.Barrier(6)
            self.ns["conn"] = lambda: local.con

            def acquire(index):
                local.con = sqlite3.connect(path)
                local.con.row_factory = sqlite3.Row
                try:
                    client = self.app.test_client()
                    with client.session_transaction() as session:
                        session["uid"] = 1 if index % 2 else 2
                    barrier.wait(timeout=10)
                    response = client.post("/api/upload-reservations",
                                           json={"token": f"{index:032x}", "size_bytes": 100})
                    return response.status_code
                finally:
                    local.con.close()

            with patch.dict(self.ns["os"].environ, LOGDB_BULK_MAX_PENDING="2", LOGDB_BULK_MAX_BYTES="200"):
                with ThreadPoolExecutor(max_workers=6) as pool:
                    results = list(pool.map(acquire, range(6)))
            self.assertEqual(results.count(201), 2, results)
            self.assertEqual(results.count(429), 4, results)

    def test_script_bulk_roundtrip_uses_real_upload_and_receipt_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "example.data"
            log = data.with_suffix(".log")
            data.write_bytes(b"example telemetry\n")
            log.write_bytes(b"<configuration/>")
            stages = []

            def request(method, path, body=None, headers=None):
                raw = b"".join(body) if body is not None and not isinstance(body, bytes) else body
                response = self.client.open(path, method=method, data=raw, headers=headers or {})
                response.request.input_stream.close()
                if response.status_code >= 400:
                    raise upload.HTTPError(response.status_code, response.json, response.headers.get("Retry-After"))
                if path == "/api/upload":
                    iid = response.json["results"][0]["ingest_id"]
                    row = self.con.execute("SELECT * FROM ingest WHERE id=?", (iid,)).fetchone()
                    stage = self.ns["ingest_stage"](row["stage_dir"])
                    stages.append(stage)
                    self.assertEqual((stage / "log.data").read_bytes(), data.read_bytes())
                    cur = self.con.execute(
                        "INSERT INTO logs(name,sha256,data_path,status,uploaded_at,uploaded_by)"
                        " VALUES ('example','hash','example.gz','done','now',2)")
                    self.con.execute("UPDATE ingest SET status='stored',log_id=? WHERE id=?", (cur.lastrowid, iid))
                    self.con.commit()
                return response.json

            args = ["upload_logs.py", directory, "--bulk", "--server", "http://fixture", "--key", "test"]
            try:
                with patch.object(upload.Client, "_request_once", side_effect=request), patch("sys.argv", args):
                    self.assertEqual(upload.main(), 0)
                self.assertEqual(self.con.execute("SELECT COUNT(*) FROM bulk_reservations").fetchone()[0], 1)
            finally:
                for stage in stages:
                    self.ns["drop_stage"](stage)

    def reserve(self, token, size=100):
        return self.client.post("/api/upload-reservations", json={"token": token, "size_bytes": size})

    def test_reservations_share_global_budget_and_expire(self):
        with patch.dict(self.ns["os"].environ, LOGDB_BULK_MAX_PENDING="1", LOGDB_BULK_MAX_BYTES="200"):
            self.assertEqual(self.reserve("a" * 32).status_code, 201)
            self.assertEqual(self.reserve("a" * 32).status_code, 200)
            self.sign_in(1)
            self.assertEqual(self.reserve("b" * 32).status_code, 429)
            self.assertEqual(self.reserve("a" * 32).status_code, 409)
            self.con.execute("UPDATE bulk_reservations SET expires_at=0")
            self.con.commit()
            self.assertEqual(self.reserve("b" * 32, 300).status_code, 201)
            self.assertEqual(self.reserve("c" * 32, 1).status_code, 429)

    def test_reserved_upload_transfers_budget_and_retries_return_receipt(self):
        token = "a" * 32
        self.assertEqual(self.reserve(token, 7).status_code, 201)
        response = self.client.post("/api/upload", data={
            "names": ["example.data", "example.log"],
            "files": [(io.BytesIO(b"data"), "example.data"), (io.BytesIO(b"log"), "example.log")]},
            headers={"X-Bulk-Reservation": token})
        response.request.input_stream.close()
        self.assertEqual(response.status_code, 200, response.json)
        iid = response.json["results"][0]["ingest_id"]
        row = self.con.execute("SELECT * FROM ingest WHERE id=?", (iid,)).fetchone()
        stage = self.ns["ingest_stage"](row["stage_dir"])
        self.addCleanup(self.ns["drop_stage"], stage)
        self.assertEqual(row["size_bytes"], 7)
        self.assertEqual(self.con.execute("SELECT ingest_id FROM bulk_reservations").fetchone()[0], iid)
        self.assertEqual(self.client.get("/api/status").json["backlog"], {"count": 1, "bytes": 7})
        retry = self.client.post("/api/upload", headers={"X-Bulk-Reservation": token})
        self.assertEqual(retry.json["results"][0]["ingest_id"], iid)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM ingest").fetchone()[0], 1)
        self.sign_in(1)
        self.assertEqual(self.client.post("/api/upload", headers={"X-Bulk-Reservation": token}).status_code, 409)

    def test_failed_reserved_upload_cleans_and_releases(self):
        token = "a" * 32
        self.reserve(token)
        response = self.client.post("/api/upload", data={}, headers={"X-Bulk-Reservation": token})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM bulk_reservations").fetchone()[0], 0)

    def test_global_backlog_and_receipt_permissions(self):
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,status,size_bytes,uploaded_at,uploaded_by,project_id)"
            " VALUES(1,'private','hash','private.gz','parsing',200,'now',4,2)")
        self.con.execute(
            "INSERT INTO ingest(id,kind,name,stage_dir,data_name,status,size_bytes,uploaded_by,uploaded_at,log_id)"
            " VALUES(1,'pair','private','stage','private.data','stored',200,4,'now',1)")
        self.con.execute(
            "INSERT INTO ingest(id,kind,name,stage_dir,data_name,status,size_bytes,uploaded_by,uploaded_at)"
            " VALUES(2,'pair','pending','stage2','pending.data','accepted',300,2,'now')")
        self.con.commit()
        response = self.client.get("/api/status")
        self.assertEqual(response.json["backlog"], {"count": 2, "bytes": 500})
        self.assertEqual(self.client.get("/api/ingest/1").status_code, 404)
        self.assertEqual(self.client.get("/api/ingest/2").json["status"], "accepted")
        self.sign_in(4)
        self.assertEqual(self.client.get("/api/ingest/1").json["log_status"], "parsing")
        self.sign_in(1)
        self.assertEqual(self.client.get("/api/ingest/2").status_code, 200)


if __name__ == "__main__":
    unittest.main()
