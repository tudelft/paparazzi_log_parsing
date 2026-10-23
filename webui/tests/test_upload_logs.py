import http.client
import errno
import gzip
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
from .test_historical import AppTests


class UploadScriptTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        script = patch.object(upload, "__file__", str(Path(folder.name) / "upload_logs.py"))
        script.start()
        self.addCleanup(script.stop)

    def test_resume_survives_ctrl_c_and_checks_pending_without_rehashing(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "flight.data"
            data.write_bytes(b"test")
            data.with_suffix(".log").write_bytes(b"xml")
            client = Mock()
            client._request.return_value = {"user": {"id": 1, "display_name": "Pilot"}}
            client.check.return_value = {}
            client.upload.return_value = {"results": [{"status": "accepted", "ingest_id": 7}]}
            args = ["upload_logs.py", directory, "--bulk", "--server", "http://fixture", "--key", "test"]
            with patch.object(upload, "Client", return_value=client), patch("sys.argv", args), \
                    patch.object(upload, "wait_capacity"), patch.object(upload, "reserve_capacity"), \
                    patch.object(upload, "wait_result", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    upload.main()
            client.check.return_value = {upload.sha256_file(data): {"has_log": True}}
            with patch.object(upload, "Client", return_value=client), patch("sys.argv", args), \
                    patch.object(upload, "sha256_file", side_effect=AssertionError("must not rehash")), \
                    patch.object(upload, "wait_result") as result:
                self.assertEqual(upload.main(), 0)
                result.assert_called_once_with(client, 7, 5, 3600)
            self.assertEqual(client.upload.call_count, 1)

    def test_resume_invalidates_changed_companion_and_separates_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "flight.data"
            data.write_bytes(b"test")
            log = data.with_suffix(".log")
            log.write_bytes(b"xml")
            pair = ("flight.data", str(data), str(log))
            resume = upload.Resume(directory, "http://fixture", 1)
            digest = resume.digest(pair)
            loaded = upload.Resume(directory, "http://fixture", 1)
            with patch.object(upload, "sha256_file", side_effect=AssertionError("cached")):
                self.assertEqual(loaded.digest(pair), digest)
            log.write_bytes(b"changed XML")
            with patch.object(upload, "sha256_file", return_value="new") as hashed:
                self.assertEqual(loaded.digest(pair), "new")
                hashed.assert_called_once()
            self.assertNotEqual(loaded.path, upload.Resume(directory, "http://other", 1).path)
            self.assertNotEqual(loaded.path, upload.Resume(directory, "http://fixture", 2).path)

    def test_resumed_terminal_failure_does_not_block_remaining_uploads(self):
        with tempfile.TemporaryDirectory() as directory:
            pairs = []
            for name in ("bad", "good"):
                data = Path(directory) / (name + ".data")
                data.write_bytes(name.encode())
                log = data.with_suffix(".log")
                log.write_bytes(b"xml")
                pairs.append((data.name, str(data), str(log)))
            resume = upload.Resume(directory, "http://fixture", 1)
            resume.digest(pairs[0])
            resume.accepted(pairs[0], 1379)
            client = Mock()
            client._request.return_value = {"user": {"id": 1, "display_name": "Pilot"}}
            client.check.return_value = {}
            client.upload.return_value = {"results": [{"status": "accepted", "ingest_id": 1380}]}
            args = ["upload_logs.py", directory, "--bulk", "--server", "http://fixture", "--key", "test"]
            output, errors = io.StringIO(), io.StringIO()
            with patch.object(upload, "Client", return_value=client), patch("sys.argv", args), \
                    patch.object(upload, "wait_capacity"), patch.object(upload, "reserve_capacity"), \
                    patch.object(upload, "wait_result", side_effect=[upload.ProcessingFailed("empty log file"), None]), \
                    patch("sys.stdout", output), patch("sys.stderr", errors):
                self.assertEqual(upload.main(), 1)
            client.upload.assert_called_once()
            self.assertEqual(client.upload.call_args.args[0][0][0], "good.data")
            self.assertIn("empty log file", errors.getvalue())
            self.assertIn("2/2 (100.0%)", output.getvalue())
            self.assertIn("1 failure(s)", output.getvalue())
            saved = upload.Resume(directory, "http://fixture", 1)
            self.assertTrue(all("ingest_id" not in e for e in saved.entries.values()))
            self.assertIn("digest", saved.entry(pairs[0]))

    def test_resumed_timeout_retains_pending_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "flight.data"
            data.write_bytes(b"test")
            log = data.with_suffix(".log")
            log.write_bytes(b"xml")
            pair = (data.name, str(data), str(log))
            resume = upload.Resume(directory, "http://fixture", 1)
            resume.accepted(pair, 7)
            client = Mock()
            client._request.return_value = {"user": {"id": 1, "display_name": "Pilot"}}
            args = ["upload_logs.py", directory, "--bulk", "--server", "http://fixture", "--key", "test"]
            with patch.object(upload, "Client", return_value=client), patch("sys.argv", args), \
                    patch.object(upload, "wait_result", side_effect=RuntimeError("Timed out")), \
                    patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()):
                self.assertEqual(upload.main(), 1)
            self.assertEqual(upload.Resume(directory, "http://fixture", 1).entry(pair)["ingest_id"], 7)
            client.upload.assert_not_called()

    def test_new_terminal_failure_clears_receipt_in_fifo_and_final_drain(self):
        for max_pending in (1, 8):
            with self.subTest(max_pending=max_pending), tempfile.TemporaryDirectory() as directory:
                data = Path(directory) / "flight.data"
                data.write_bytes(b"test")
                log = data.with_suffix(".log")
                log.write_bytes(b"xml")
                client = Mock()
                client._request.return_value = {"user": {"id": 1, "display_name": "Pilot"}}
                client.check.return_value = {}
                client.upload.return_value = {"results": [{"status": "accepted", "ingest_id": 7}]}
                args = ["upload_logs.py", directory, "--bulk", "--server", "http://fixture", "--key", "test",
                        "--max-pending", str(max_pending)]
                with patch.object(upload, "Client", return_value=client), patch("sys.argv", args), \
                        patch.object(upload, "wait_capacity"), patch.object(upload, "reserve_capacity"), \
                        patch.object(upload, "wait_result", side_effect=upload.ProcessingFailed("empty log file")), \
                        patch("sys.stdout", io.StringIO()):
                    self.assertEqual(upload.main(), 1)
                entry = upload.Resume(directory, "http://fixture", 1).entry((data.name, str(data), str(log)))
                self.assertNotIn("ingest_id", entry)
                self.assertIn("digest", entry)

    def test_progress_counts_skipped_accepted_processed_and_failed(self):
        progress = upload.Progress(10, bulk=True)
        progress.checked = 8
        progress.skipped = 3
        progress.accepted = 4
        progress.processed = 2
        progress.failed = 1
        output = io.StringIO()
        with patch("sys.stdout", output):
            progress.report()
        self.assertEqual(output.getvalue(), "Progress: 6/10 (60.0%) | 4 remaining | 8 checked | "
                         "3 skipped | 4 accepted, 2 processed | 1 failed\n")

    def test_progress_acceptance_only_dry_run_and_empty(self):
        for bulk, dry_run, total, expected in (
                (False, False, 4, "3/4 (75.0%) | 1 remaining"),
                (True, True, 4, "3/4 (75.0%) | 1 remaining"),
                (False, False, 0, "0/0 (100.0%) | 0 remaining")):
            with self.subTest(bulk=bulk, dry_run=dry_run, total=total):
                progress = upload.Progress(total, bulk, dry_run)
                if total:
                    progress.skipped = 1
                    progress.accepted = progress.planned = 2
                output = io.StringIO()
                with patch("sys.stdout", output):
                    progress.report()
                self.assertIn(expected, output.getvalue())

    def test_scan_errors_are_not_silently_ignored(self):
        def walk(root, onerror):
            onerror(PermissionError("cannot list folder"))
            return iter(())

        with patch.object(upload.os, "walk", side_effect=walk):
            with self.assertRaisesRegex(PermissionError, "cannot list folder"):
                list(upload.find_pairs("fixture"))

    def test_bulk_progress_tracks_processing_results_and_remaining(self):
        for processing_error in (False, True):
            with self.subTest(processing_error=processing_error), tempfile.TemporaryDirectory() as directory:
                data = Path(directory) / "flight.data"
                data.write_bytes(b"test")
                data.with_suffix(".log").write_bytes(b"xml")
                client = Mock()
                client._request.return_value = {"user": {"display_name": "Pilot"}}
                client.check.return_value = {}
                client.upload.return_value = {"results": [{"status": "accepted", "ingest_id": 7}]}
                output = io.StringIO()
                args = ["upload_logs.py", directory, "--bulk", "--server", "http://fixture", "--key", "test"]
                error = RuntimeError("parse failed") if processing_error else None
                with patch.object(upload, "Client", return_value=client), patch("sys.argv", args), \
                        patch.object(upload, "wait_capacity"), patch.object(upload, "reserve_capacity"), \
                        patch.object(upload, "wait_result", side_effect=error), patch("sys.stdout", output):
                    self.assertEqual(upload.main(), int(processing_error))
                self.assertIn("Progress: 0/1 (0.0%) | 1 remaining | 1 checked | 0 skipped | "
                              "1 accepted, 0 processed | 0 failed", output.getvalue())
                expected = "1 accepted, 0 processed | 1 failed" if processing_error else "1 accepted, 1 processed | 0 failed"
                self.assertIn("Progress: 1/1 (100.0%) | 0 remaining | 1 checked | 0 skipped | " + expected,
                              output.getvalue())

    def test_upload_starts_before_later_batches_are_hashed_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            for index, content in enumerate((b"first", b"second", b"first", b"known")):
                data = Path(directory) / f"{index}.data"
                data.write_bytes(content)
                data.with_suffix(".log").write_bytes(b"xml")
            events = []
            output = io.StringIO()
            real_hash = upload.sha256_file
            known_hash = real_hash(Path(directory) / "3.data")
            client = Mock()
            client._request.return_value = {"user": {"display_name": "Pilot"}}
            client.check.side_effect = lambda hashes: {known_hash: {"has_log": True}} if known_hash in hashes else {}

            def hashed(path):
                events.append(("hash", Path(path).name))
                return real_hash(path)

            def uploaded(files, **kwargs):
                events.append(("upload", files[0][0]))
                return {"results": [{"status": "accepted", "ingest_id": len(events)}]}

            client.upload.side_effect = uploaded
            args = ["upload_logs.py", directory, "--server", "http://fixture", "--key", "test",
                    "--hash-batch-files", "1"]
            with patch.object(upload, "Client", return_value=client), patch("sys.argv", args), \
                    patch.object(upload, "sha256_file", side_effect=hashed), patch("sys.stdout", output):
                self.assertEqual(upload.main(), 0)
            self.assertEqual(events, [("hash", "0.data"), ("upload", "0.data"),
                                      ("hash", "1.data"), ("upload", "1.data"),
                                      ("hash", "2.data"), ("hash", "3.data")])
            self.assertEqual(client.check.call_count, 4)
            self.assertIn("Found 4 pairs.", output.getvalue())
            self.assertIn("Progress: 4/4 (100.0%) | 0 remaining | 4 checked | 2 skipped | 2 accepted | 0 failed",
                          output.getvalue())

    def test_hash_batches_enforce_count_bytes_and_oversized_files(self):
        pairs = [(str(i), str(i), "log") for i in range(5)]
        sizes = {"0": 4, "1": 6, "2": 11, "3": 3, "4": 3}
        with patch.object(upload.os.path, "getsize", side_effect=sizes.get), \
                patch.object(upload, "sha256_file", side_effect=lambda path: path):
            batches = list(upload.hash_batches(iter(pairs), 2, 10))
        self.assertEqual([[p[0][0] for p in batch] for batch in batches], [["0", "1"], ["2"], ["3", "4"]])
        with patch.object(upload.os.path, "getsize", return_value=6), \
                patch.object(upload, "sha256_file", side_effect=lambda path: path):
            batches = list(upload.hash_batches(iter(pairs[:2]), 32, 10))
        self.assertEqual([len(batch) for batch in batches], [1, 1])
        self.assertEqual(list(upload.hash_batches(iter([]), 2, 10)), [])

    def test_dry_run_batches_deduplicate_and_include_missing_companion(self):
        with tempfile.TemporaryDirectory() as directory:
            for i in range(2):
                data = Path(directory) / f"{i}.data"
                data.write_bytes(b"same")
                data.with_suffix(".log").write_bytes(b"xml")
            client = Mock()
            client._request.return_value = {"user": {"display_name": "Pilot"}}
            digest = upload.sha256_file(data)
            client.check.return_value = {digest: {"has_log": False}}
            args = ["upload_logs.py", directory, "--bulk", "--dry-run",
                    "--server", "http://fixture", "--key", "test", "--hash-batch-files", "1"]
            output = io.StringIO()
            with patch.object(upload, "Client", return_value=client), patch("sys.argv", args), \
                    patch("sys.stdout", output):
                self.assertEqual(upload.main(), 0)
            self.assertEqual(output.getvalue().count("would upload"), 1)
            self.assertEqual(client.check.call_count, 2)
            self.assertIn("Progress: 2/2 (100.0%) | 0 remaining | 2 checked | 1 skipped | 1 planned | 0 failed",
                          output.getvalue())
            client.upload.assert_not_called()
            client._request.assert_called_once_with("GET", "/api/me")

    def test_later_hash_failure_drains_accepted_bulk_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            for i in range(2):
                data = Path(directory) / f"{i}.data"
                data.write_bytes(b"test")
                data.with_suffix(".log").write_bytes(b"xml")
            client = Mock()
            client._request.return_value = {"user": {"display_name": "Pilot"}}
            client.check.return_value = {}
            client.upload.return_value = {"results": [{"status": "accepted", "ingest_id": 7}]}
            args = ["upload_logs.py", directory, "--bulk", "--server", "http://fixture", "--key", "test",
                    "--hash-batch-files", "1"]
            output = io.StringIO()
            with patch.object(upload, "Client", return_value=client), patch("sys.argv", args), \
                    patch.object(upload, "sha256_file", side_effect=["digest", OSError("read failed")]), \
                    patch.object(upload, "wait_capacity"), \
                    patch.object(upload, "reserve_capacity", return_value="a" * 32), \
                    patch.object(upload, "wait_result") as result, patch("sys.stdout", output):
                self.assertEqual(upload.main(), 1)
            self.assertEqual(client.upload.call_count, 1)
            result.assert_called_once_with(client, 7, 5, 3600)
            self.assertIn("Progress: 1/2 (50.0%) | 1 remaining", output.getvalue())
            self.assertIn("1 discovery/hash/check errors", output.getvalue())
            self.assertNotIn("(100.0%)", output.getvalue())

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
            with self.assertRaisesRegex(upload.ProcessingFailed, message):
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
    def test_upload_stages_locally_and_can_resume_legacy_share_staging(self):
        response = self.client.post("/api/upload", data={
            "names": ["example.data", "example.log"],
            "files": [(io.BytesIO(b"telemetry"), "example.data"), (io.BytesIO(b"xml"), "example.log")]})
        response.request.input_stream.close()
        self.assertEqual(response.status_code, 200, response.json)
        iid = response.json["results"][0]["ingest_id"]
        row = self.con.execute("SELECT * FROM ingest WHERE id=?", (iid,)).fetchone()
        stage = self.ns["ingest_stage"](row["stage_dir"])
        self.addCleanup(self.ns["drop_stage"], stage)
        self.assertTrue(stage.is_relative_to(self.ns["LOCAL_STAGE_DIR"]))
        self.assertFalse(stage.is_relative_to(self.ns["STORAGE_DIR"]))
        self.assertEqual(self.ns["ingest_stage"]("legacy/pair"), self.ns["STAGE_DIR"] / "legacy/pair")
        self.assertEqual((stage / "log.data").read_bytes(), b"telemetry")
        self.ns["run_ingest"](iid)
        log = self.con.execute("SELECT * FROM logs").fetchone()
        self.assertIsNotNone(log)
        self.assertEqual(gzip.decompress((self.ns["STORAGE_DIR"] / log["data_path"]).read_bytes()), b"telemetry")
        self.assertEqual(gzip.decompress((self.ns["STORAGE_DIR"] / log["log_path"]).read_bytes()), b"xml")
        self.assertFalse(stage.exists())

    def test_failed_ingestion_releases_local_raw_staging(self):
        relative = self.ns["new_stage_rel"]()
        stage = self.ns["ingest_stage"](relative)
        stage.mkdir(parents=True)
        (stage / "log.data").write_bytes(b"test data")
        iid = self.ns["insert_ingest"](self.con, "file", "example.data", relative,
                                      "log.data", None, 9, 2)
        with patch.object(self.ns["storage"], "gzip_file", side_effect=OSError("share disconnected")):
            with self.assertRaisesRegex(OSError, "disconnected"):
                self.ns["run_ingest"](iid)
        self.assertFalse(stage.exists())

    def test_low_space_refuses_reservation_and_cleans_rejected_upload(self):
        no_space = OSError(errno.ENOSPC, "Insufficient local temporary disk space")
        with patch.object(self.ns["storage"], "ensure_space", side_effect=no_space):
            response = self.reserve("c" * 32)
            self.assertEqual(response.status_code, 429, response.json)
            self.assertIn("disk space", response.json["error"])
        self.assertEqual(self.reserve("a" * 32, 7).status_code, 201)
        before = set(self.ns["LOCAL_STAGE_DIR"].glob("*"))
        with patch.object(self.ns["storage"], "ensure_space", side_effect=no_space):
            response = self.client.post("/api/upload", data={
                "names": ["example.data", "example.log"],
                "files": [(io.BytesIO(b"data"), "example.data"), (io.BytesIO(b"log"), "example.log")]},
                headers={"X-Bulk-Reservation": "a" * 32})
            response.request.input_stream.close()
        self.assertEqual(response.status_code, 503, response.json)
        self.assertEqual(set(self.ns["LOCAL_STAGE_DIR"].glob("*")), before)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM ingest").fetchone()[0], 0)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM bulk_reservations").fetchone()[0], 0)

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
