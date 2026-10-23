import gzip
import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import attachments
import db
import storage
from . import test_historical


class AttachmentTests(test_historical.AppTests):
    def tearDown(self):
        for row in self.con.execute("SELECT path FROM attachments"):
            (self.ns["STORAGE_DIR"] / row["path"]).unlink(missing_ok=True)
        super().tearDown()

    def upload_file(self, targets, content=b"wind speed,temperature\n1,20\n" * 1000, name="wind.csv", **extra):
        metadata = dict(filename=name, description="Wind tunnel", targets=targets, **extra)
        return self.client.post("/api/attachments", data=content, content_type="application/octet-stream",
                                headers={"X-Attachment-Metadata": quote(json.dumps(metadata))})

    def paper(self, **changes):
        return dict(kind="paper", id=self.create(**changes).json["id"])

    def log(self, project=1, name="test.data"):
        cur = self.con.execute(
            "INSERT INTO logs(name,sha256,data_path,log_path,status,uploaded_at,uploaded_by,project_id)"
            " VALUES (?,?,'test.data.gz','test.log.gz','done','now',2,?)", (name, os.urandom(16).hex(), project))
        log_id = cur.lastrowid
        aircraft = self.con.execute("INSERT INTO aircraft(log_id,ac_id,name) VALUES (?,7,'Drone')", (log_id,)).lastrowid
        fid = self.con.execute(
            "INSERT INTO flights(aircraft_id,idx,takeoff_time_s,takeoff_utc,project_id) VALUES (?,1,12,'2010-01-01',?)",
            (aircraft, project)).lastrowid
        self.con.commit()
        return dict(kind="log", id=log_id), dict(kind="flight", id=fid)

    def test_compressed_shared_file_roundtrip_and_last_unlink(self):
        paper = self.paper()
        log, flight = self.log()
        data = b"wind,temperature\n1.2,20\n" * 10000
        response = self.upload_file([paper, log, flight], data, "測定.csv")
        self.assertEqual(response.status_code, 201, response.json)
        aid = response.json["id"]
        row = self.con.execute("SELECT * FROM attachments").fetchone()
        self.assertTrue(row["compressed"])
        self.assertLess(row["stored_bytes"], len(data) * 0.1)
        self.assertEqual(row["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 1)
        downloaded = self.client.get(f"/api/attachments/{aid}/download")
        self.assertEqual(downloaded.data, data)
        self.assertIn("filename*=UTF-8", downloaded.headers["Content-Disposition"])
        self.assertEqual(downloaded.headers["X-Content-Type-Options"], "nosniff")
        listing = self.client.get(f'/api/attachments?kind=flight&id={flight["id"]}').json
        self.assertEqual(len(listing["files"]), 1)  # Direct and inherited links do not duplicate a file.
        path = self.ns["STORAGE_DIR"] / row["path"]
        for link in listing["files"][0]["links"]:
            self.assertEqual(self.client.delete(f'/api/attachments/{aid}/links/{link["link_id"]}').status_code, 200)
        self.assertFalse(path.exists())
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 0)
        self.assertEqual(self.client.get(f"/api/attachments/{aid}/download").status_code, 404)

    def test_incompressible_and_range_download(self):
        paper = self.paper()
        data = os.urandom(1 << 16)
        response = self.upload_file([paper], data, "video.mp4")
        self.assertEqual(response.status_code, 201)
        row = self.con.execute("SELECT * FROM attachments").fetchone()
        self.assertFalse(row["compressed"])
        self.assertEqual(row["stored_bytes"], len(data))
        aid = response.json["id"]
        response = self.client.get(f"/api/attachments/{aid}/download", headers={"Range": "bytes=10-19"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, data[10:20])
        response.close()

    def test_permissions_sharing_and_hidden_links(self):
        paper = self.paper()
        response = self.upload_file([paper])
        aid = response.json["id"]
        self.sign_in(1)
        private = self.paper(project_id=2, date="2011-01-01")
        self.assertEqual(self.client.post(f"/api/attachments/{aid}/links", json={"targets": [private]}).status_code, 200)
        self.sign_in(3)
        listing = self.client.get(f'/api/attachments?kind=paper&id={paper["id"]}').json["files"][0]
        self.assertEqual(len(listing["links"]), 1)
        self.assertFalse(listing["can_share"])
        self.assertEqual(self.client.get(f"/api/attachments/{aid}/download").status_code, 200)
        self.assertEqual(self.upload_file([paper]).status_code, 403)
        self.assertEqual(self.client.delete(f'/api/attachments/{aid}/links/{listing["links"][0]["link_id"]}').status_code, 403)
        self.sign_in(4)
        self.assertEqual(self.client.get(f"/api/attachments/{aid}/download").status_code, 404)
        self.assertEqual(self.client.get(f'/api/attachments?kind=paper&id={paper["id"]}').status_code, 404)
        self.sign_in(2)
        # A user who cannot manage all existing links cannot share the file further.
        another = self.paper(date="2012-01-01")
        self.assertEqual(self.client.post(f"/api/attachments/{aid}/links", json={"targets": [another]}).status_code, 404)
        listing = self.client.get(f'/api/attachments?kind=paper&id={paper["id"]}').json["files"][0]
        self.assertEqual(self.client.delete(f'/api/attachments/{aid}/links/{listing["links"][0]["link_id"]}').status_code, 200)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 1)
        self.sign_in(1)
        self.assertEqual(self.client.delete(f'/api/historical/{private["id"]}').status_code, 200)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 0)

    def test_validation_stream_limits_and_no_partial_files(self):
        paper = self.paper()
        for targets, name in (([], "x"), ([dict(kind=[], id=1)], "x"), ([paper], "../x"), ([paper], "a\nb")):
            self.assertEqual(self.upload_file(targets, name=name).status_code, 400)
        self.assertEqual(self.upload_file([paper], b"").status_code, 400)
        self.assertEqual(self.client.post("/api/attachments", data=b"x").status_code, 400)
        self.app.config["ATTACHMENT_MAX_BYTES"] = 1024
        self.addCleanup(self.app.config.__setitem__, "ATTACHMENT_MAX_BYTES", 10 << 30)
        response = self.upload_file([paper], b"x" * 1025)
        self.assertEqual(response.status_code, 413, response.json)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 0)
        self.assertEqual(list((self.ns["STORAGE_DIR"] / ".incoming-attachments").glob("*")), [])

    def test_reparse_preserves_matching_and_falls_back_to_log(self):
        log, flight = self.log()
        aid = self.upload_file([flight]).json["id"]
        saved = attachments.snapshot(self.con, log["id"])
        doc = dict(aircraft=[dict(ac_id=7, name="Drone", flights=[dict(flight=1, takeoff=dict(time_s=12))])])
        db.store_parsed(self.con, log["id"], doc, "paths.gz")
        attachments.restore(self.con, log["id"], saved)
        link = self.con.execute("SELECT * FROM attachment_links WHERE attachment_id=?", (aid,)).fetchone()
        self.assertIsNotNone(link["flight_id"])
        saved = attachments.snapshot(self.con, log["id"])
        db.store_parsed(self.con, log["id"], dict(aircraft=[]), "paths.gz")
        attachments.restore(self.con, log["id"], saved)
        link = self.con.execute("SELECT * FROM attachment_links WHERE attachment_id=?", (aid,)).fetchone()
        self.assertEqual(link["log_id"], log["id"])
        self.assertIsNone(link["flight_id"])
        self.con.commit()
        self.assertEqual(self.client.delete(f'/api/logs/{log["id"]}').status_code, 200)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 0)

    def test_log_inheritance_does_not_bypass_visibility(self):
        log, flight = self.log(project=2)
        self.con.execute("UPDATE flights SET project_id=1 WHERE id=?", (flight["id"],))
        self.con.commit()
        self.sign_in(1)
        self.upload_file([log])
        self.sign_in(2)
        response = self.client.get(f'/api/attachments?kind=flight&id={flight["id"]}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["files"], [])

    def test_search_and_api_keys(self):
        paper = self.paper()
        self.assertEqual(self.client.get("/api/attachments/targets?q=Drone").json["targets"][0]["id"], paper["id"])
        self.assertEqual(self.client.get(f'/api/attachments/targets?q={paper["id"]}').json["targets"][0]["id"], paper["id"])
        self.sign_in(3)
        self.assertEqual(self.client.get("/api/attachments/targets?q=Drone").json["targets"], [])
        self.sign_in(2)
        token = "logdb_" + "a" * 30
        self.con.execute("INSERT INTO api_keys(user_id,name,key_hash,prefix,created_at) VALUES (2,'test',?,?, 'now')",
                         (hashlib.sha256(token.encode()).hexdigest(), "test"))
        self.con.commit()
        key = {"Authorization": "Bearer " + token}
        # A key uploads to records its user may edit and links the same file to more of them
        other = self.paper(date="2010-01-02")
        metadata = quote(json.dumps(dict(filename="ctr.pdf", targets=[paper])))
        response = self.client.post("/api/attachments", data=b"%PDF", content_type="application/octet-stream",
                                    headers={**key, "X-Attachment-Metadata": metadata})
        self.assertEqual(response.status_code, 201, response.json)
        aid = response.json["id"]
        response = self.client.post(f"/api/attachments/{aid}/links", json=dict(targets=[other]), headers=key)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachment_links WHERE attachment_id=?",
                                          (aid,)).fetchone()[0], 2)
        # ... but not to records of a project without edit rights
        self.sign_in(1)
        private = self.paper(project_id=2)
        self.sign_in(2)
        metadata = quote(json.dumps(dict(filename="ctr.pdf", targets=[private])))
        response = self.client.post("/api/attachments", data=b"%PDF", content_type="application/octet-stream",
                                    headers={**key, "X-Attachment-Metadata": metadata})
        self.assertIn(response.status_code, (403, 404))
        response = self.client.post(f"/api/attachments/{aid}/links", json=dict(targets=[private]), headers=key)
        self.assertIn(response.status_code, (403, 404))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 1)
        # Unlinking (and so deleting) still needs a browser session
        link = self.con.execute("SELECT id FROM attachment_links WHERE attachment_id=?", (aid,)).fetchone()["id"]
        self.assertEqual(self.client.delete(f"/api/attachments/{aid}/links/{link}", headers=key).status_code, 403)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachment_links").fetchone()[0], 2)

    def test_upload_rechecks_permissions_and_cleans_staging(self):
        paper = self.paper()
        original = storage.save_attachment

        def revoke(*args, **kwargs):
            saved = original(*args, **kwargs)
            self.con.execute("UPDATE project_members SET role='view' WHERE user_id=2")
            self.con.commit()
            return saved

        with patch("storage.save_attachment", side_effect=revoke):
            self.assertEqual(self.upload_file([paper]).status_code, 403)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 0)
        root = self.ns["STORAGE_DIR"]
        self.assertEqual(list((root / ".incoming-attachments").glob("*")), [])
        self.assertEqual(list((root / "attachments").glob("*")), [])

    def test_ingest_compresses_and_adds_renamed_companion(self):
        stage = self.ns["STAGE_DIR"] / "pair-test"
        stage.mkdir(parents=True, exist_ok=True)
        raw = b'12.0 7 INFO_MSG "[PFC] goal: wind tunnel"\n' * 100
        (stage / "original.data").write_bytes(raw)
        iid = self.ns["insert_ingest"](self.con, "pair", "original.data", "pair-test",
                                      "original.data", None, len(raw), 2)
        row = self.con.execute("SELECT * FROM ingest WHERE id=?", (iid,)).fetchone()
        self.ns["ingest_pair"](self.con, row)
        log = self.con.execute("SELECT * FROM logs").fetchone()
        self.assertTrue(log["data_path"].endswith(".data.gz"))
        self.assertIsNone(log["log_path"])
        stage.mkdir(parents=True, exist_ok=True)
        (stage / "renamed.data").write_bytes(raw)
        xml = b'<configuration><conf><aircraft ac_id="7" name="Drone"/></conf></configuration>'
        (stage / "renamed.log").write_bytes(xml)
        iid = self.ns["insert_ingest"](self.con, "pair", "renamed.data", "pair-test",
                                      "renamed.data", "renamed.log", len(raw), 2)
        row = self.con.execute("SELECT * FROM ingest WHERE id=?", (iid,)).fetchone()
        self.ns["ingest_pair"](self.con, row)
        log = self.con.execute("SELECT * FROM logs").fetchone()
        self.assertTrue(log["log_path"].endswith("original.log.gz"))
        root = self.ns["STORAGE_DIR"]
        self.addCleanup((root / log["data_path"]).unlink, missing_ok=True)
        self.addCleanup((root / log["log_path"]).unlink, missing_ok=True)
        self.assertEqual(gzip.decompress((root / log["log_path"]).read_bytes()), xml)
        self.assertFalse(stage.exists())
        if not Path(self.ns["FASTREADER"]).is_file():
            self.skipTest("Parser binary not built; ingest roundtrip above passed.")
        self.ns["parse_log"](log["id"])
        parsed = self.con.execute("SELECT status,error,paths_file FROM logs").fetchone()
        self.assertEqual(parsed["status"], "done", parsed["error"])
        self.addCleanup((root / parsed["paths_file"]).unlink, missing_ok=True)

    def test_compressed_logs_download_zip_and_parser_input(self):
        log, _ = self.log()
        root = self.ns["STORAGE_DIR"]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.data"
            companion = Path(directory) / "sample.log"
            raw = b"12.0 7 GPS 1 2 3\n" * 1000
            xml = b"<protocol>test</protocol>"
            source.write_bytes(raw)
            companion.write_bytes(xml)
            sha = hashlib.sha256(raw).hexdigest()
            data_rel, log_rel = self.ns["store_files"](sha, "sample.data", source, companion)
            self.addCleanup((root / data_rel).unlink, missing_ok=True)
            self.addCleanup((root / log_rel).unlink, missing_ok=True)
            self.assertEqual(gzip.decompress((root / data_rel).read_bytes()), raw)
            self.con.execute("UPDATE logs SET data_path=?,log_path=?,sha256=? WHERE id=?",
                             (data_rel, log_rel, sha, log["id"]))
            self.con.commit()
            response = self.client.get(f'/api/logs/{log["id"]}/download/data')
            self.assertEqual(response.data, raw)
            self.assertIn('filename="sample.data"', response.headers["Content-Disposition"])
            response = self.client.get(f'/api/logs/{log["id"]}/download')
            with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                self.assertEqual(archive.namelist(), ["sample.data", "sample.log"])
                self.assertEqual(archive.read("sample.data"), raw)
                self.assertEqual(archive.read("sample.log"), xml)
                self.assertEqual(archive.getinfo("sample.data").compress_type, zipfile.ZIP_DEFLATED)

            def parser(cmd, stdout, stderr):
                parsed = Path(cmd[-1])
                self.assertEqual(parsed.read_bytes(), raw)
                self.assertEqual(parsed.with_suffix(".log").read_bytes(), xml)
                stdout.write(b'{"aircraft":[]}')
                return type("Result", (), dict(returncode=0))()

            with patch("subprocess.run", side_effect=parser):
                self.ns["parse_log"](log["id"])
            row = self.con.execute("SELECT status,error,paths_file FROM logs WHERE id=?", (log["id"],)).fetchone()
            self.assertEqual(row["status"], "done", row["error"])
            self.addCleanup((root / row["paths_file"]).unlink, missing_ok=True)
            self.assertEqual(list(self.ns["TMP_DIR"].glob("parse-*")), [])


class StorageTests(unittest.TestCase):
    def test_local_compression_transfers_only_finished_gzip_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "local"
            work.mkdir()
            source = work / "sample.data"
            raw = b"example telemetry\n" * 10000
            source.write_bytes(raw)
            destination = root / "shared" / "sample.data.gz"
            real_replace = storage.os.replace

            def publish(temporary, final):
                self.assertEqual(Path(temporary).parent, destination.parent)
                self.assertEqual(gzip.decompress(Path(temporary).read_bytes()), raw)
                self.assertFalse(destination.exists())
                real_replace(temporary, final)

            with patch.object(storage.os, "replace", side_effect=publish):
                storage.gzip_file(source, destination, work_dir=work)
            self.assertEqual(gzip.decompress(destination.read_bytes()), raw)
            self.assertLess(destination.stat().st_size, len(raw) // 10)
            self.assertEqual(list(work.iterdir()), [source])
            self.assertEqual(list(destination.parent.iterdir()), [destination])
            destination.unlink()
            with patch.object(storage.os, "replace", side_effect=OSError("share disconnected")):
                with self.assertRaisesRegex(OSError, "disconnected"):
                    storage.gzip_file(source, destination, work_dir=work)
            self.assertEqual(list(work.iterdir()), [source])
            self.assertEqual(list(destination.parent.iterdir()), [])

    def test_local_disk_headroom_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(storage.shutil, "disk_usage", return_value=type("Usage", (), {"free": 100})()):
                with self.assertRaisesRegex(OSError, "disk space"):
                    storage.ensure_space(directory, required=51, minimum_free=50)
                storage.ensure_space(directory, required=50, minimum_free=50)
                with self.assertRaisesRegex(OSError, "disk space"):
                    storage.copy_local(io.BytesIO(b"data"), Path(directory) / "stage", minimum_free=100)
            self.assertEqual((Path(directory) / "stage").stat().st_size, 0)

    def test_stream_limit_without_content_length_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "exceeds"):
                storage.save_attachment(io.BytesIO(b"x" * 2000), directory, 1000)
            self.assertEqual(list(Path(directory).iterdir()), [])
