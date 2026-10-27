import hashlib
import io
import json
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import qualifications
import db
from .test_historical import AppTests

PDF = b"%PDF-1.4\nTest evidence\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"test"
JPEG = b"\xff\xd8\xff\xe0" + b"test"


class QualificationTests(AppTests):
    def setUp(self):
        super().setUp()
        self.sign_in(1)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        old = self.app.config["QUALIFICATION_EVIDENCE_DIR"]
        self.app.config["QUALIFICATION_EVIDENCE_DIR"] = self.root
        self.addCleanup(self.app.config.__setitem__, "QUALIFICATION_EVIDENCE_DIR", old)
        self.tid = self.create_type("A1/A3", True)

    def create_type(self, name, required):
        r = self.client.post("/api/qualification-types",
                             json={"name": name, "requires_evidence": required,
                                   "requires_start": False, "requires_expiry": False})
        self.assertEqual(r.status_code, 200, r.json)
        return r.json["id"]

    def submit(self, uid=2, content=PDF, filename="evidence.pdf", tid=None, until="2028-06-30", start=""):
        data = {"type_id": str(tid or self.tid), "valid_until": until, "valid_from": start}
        if content is not None:
            data["evidence"] = (io.BytesIO(content), filename)
        r = self.client.post(f"/api/users/{uid}/qualifications", data=data)
        r.request.input_stream.close()
        return r

    def review(self, sid, action="approve"):
        return self.client.post(f"/api/qualification-submissions/{sid}/review", json={"action": action})

    def row(self, sid):
        return self.con.execute("SELECT * FROM qualification_submissions WHERE id=?", (sid,)).fetchone()

    def listing(self, uid=2):
        return self.client.get(f"/api/users/{uid}/qualifications")

    def test_admin_uploads_for_other_users_and_approves_self(self):
        r = self.submit()
        self.assertEqual(r.status_code, 201, r.json)
        sid = r.json["id"]
        self.assertEqual(self.row(sid)["submitted_by"], 1)
        self.assertEqual(self.row(sid)["user_id"], 2)
        self.assertEqual(self.row(sid)["status"], "pending")
        self.assertEqual(self.review(sid).status_code, 200)
        self.assertEqual(self.row(sid)["reviewed_by"], 1)
        own = self.submit(uid=1).json["id"]
        self.assertEqual(self.review(own).status_code, 200)
        self.assertEqual(self.row(own)["status"], "approved")

    def test_people_directory_keeps_approved_licences_and_evidence_private(self):
        sid = self.submit().json["id"]
        self.assertEqual(self.review(sid).status_code, 200)
        evidence = f"/api/qualification-submissions/{sid}/evidence"
        for uid, allowed in ((1, True), (2, True), (3, False)):
            self.sign_in(uid)
            people = self.client.get("/api/people-directory").json["users"]
            pilot = next(u for u in people if u["id"] == 2)
            if allowed:
                licence = pilot["qualifications"]["items"][0]
                self.assertEqual((licence["id"], licence["category"], licence["status"]),
                                 (sid, "A1/A3", "approved"))
                self.assertTrue(licence["has_evidence"])
                self.assertTrue(licence["viewable"])
                self.assertEqual(self.client.get(evidence).status_code, 200)
                self.assertEqual(self.client.get(evidence + "/view").status_code, 200)
            else:
                self.assertIsNone(pilot["qualifications"])
                self.assertEqual(self.client.get(evidence).status_code, 404)
                self.assertEqual(self.client.get(evidence + "/view").status_code, 404)

    def test_self_service_and_all_surfaces_are_private(self):
        self.sign_in(2)
        r = self.submit()
        self.assertEqual(r.status_code, 201)
        sid = r.json["id"]
        self.assertEqual(self.review(sid).status_code, 403)
        self.assertEqual(self.submit(uid=3).status_code, 404)
        self.assertEqual(self.row(sid)["submitted_by"], 2)
        self.assertEqual(self.listing().status_code, 200)
        file_url = f"/api/qualification-submissions/{sid}/evidence"
        downloaded = self.client.get(file_url)
        self.assertEqual(downloaded.data, PDF)
        self.assertIn("attachment;", downloaded.headers["Content-Disposition"])
        self.assertIn("no-store", downloaded.headers["Cache-Control"])
        self.assertEqual(downloaded.headers["X-Content-Type-Options"], "nosniff")
        viewed = self.client.get(file_url + "/view")
        self.assertEqual((viewed.data, viewed.mimetype), (PDF, "application/pdf"))
        self.assertIn("inline;", viewed.headers["Content-Disposition"])
        self.assertEqual(viewed.headers["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("Content-Security-Policy", viewed.headers)  # browsers' PDF viewers break under a sandbox
        self.assertIn("sandbox", downloaded.headers["Content-Security-Policy"])
        self.assertEqual(self.client.delete(file_url).status_code, 403)
        for uid in (3, 4, 5):
            self.sign_in(uid)
            self.assertEqual(self.listing().status_code, 404)
            self.assertEqual(self.client.get(file_url).status_code, 404)
            self.assertEqual(self.client.get("/api/users/2/qualification-audit").status_code, 403)
            self.assertEqual(self.review(sid, "withdraw").status_code, 404)
            detail = self.client.get("/api/detail/people/2")
            if detail.status_code == 200:
                self.assertIsNone(detail.json["item"].get("qualifications"))
            directory = self.client.get("/api/people-directory").json
            for person in directory["users"]:
                if person["id"] == 2:
                    self.assertIsNone(person["qualifications"])
        self.sign_in(1)
        self.assertEqual(self.client.get(file_url).data, PDF)
        self.assertEqual(self.listing().json["items"][0]["status"], "pending")
        self.assertEqual(self.submit(uid=999).status_code, 404)

    def test_type_configuration_and_optional_evidence_still_needs_approval(self):
        tid = self.create_type("Internal qualification", False)
        self.sign_in(2)
        r = self.submit(tid=tid, content=None, until="")
        self.assertEqual(r.status_code, 201, r.json)
        sid = r.json["id"]
        self.assertEqual(self.row(sid)["status"], "pending")
        self.assertIsNone(self.row(sid)["valid_until"])
        self.assertEqual(self.review(sid).status_code, 403)
        self.sign_in(1)
        self.assertEqual(self.client.put(f"/api/qualification-types/{tid}", json={
            "name": "Internal qualification", "requires_evidence": True, "active": True}).status_code, 200)
        self.assertEqual(self.review(sid).status_code, 400)
        self.client.put(f"/api/qualification-types/{tid}", json={
            "name": "Internal qualification", "requires_evidence": False, "active": True})
        self.assertEqual(self.review(sid).status_code, 200)
        self.assertEqual(self.create_type("RT licence", True) > 0, True)
        self.client.put(f"/api/qualification-types/{tid}", json={
            "name": "Internal qualification", "requires_evidence": False, "active": False})
        self.assertEqual(self.submit(tid=tid, content=None).status_code, 400)
        self.assertEqual(self.row(sid)["status"], "approved")
        self.sign_in(2)
        self.assertEqual(self.client.post("/api/qualification-types", json={
            "name": "Unapproved", "requires_evidence": False}).status_code, 403)
        self.assertEqual(self.client.put(f"/api/qualification-types/{tid}", json={
            "name": "Changed", "requires_evidence": False}).status_code, 403)

    def test_renewal_preserves_approval_and_retains_previous_evidence(self):
        old = self.submit().json["id"]
        self.review(old)
        self.sign_in(2)
        new = self.submit(until="2030-01-01").json["id"]
        self.assertEqual(self.row(old)["status"], "approved")
        self.assertEqual(self.row(new)["status"], "pending")
        self.assertEqual(self.submit().status_code, 409)
        self.sign_in(1)
        self.review(new)
        self.assertEqual(self.row(old)["status"], "superseded")
        self.assertEqual(self.row(new)["status"], "approved")
        self.sign_in(2)
        self.assertEqual(self.client.get(f"/api/qualification-submissions/{old}/evidence").data, PDF)
        rejected = self.submit().json["id"]
        self.sign_in(1)
        self.review(rejected, "reject")
        self.assertEqual(self.row(new)["status"], "approved")
        self.assertEqual(self.row(rejected)["status"], "rejected")
        self.sign_in(2)
        self.assertEqual(self.client.get(f"/api/qualification-submissions/{rejected}/evidence").data, PDF)
        withdrawn = self.submit().json["id"]
        self.assertEqual(self.review(withdrawn, "withdraw").status_code, 200)
        self.assertEqual(self.review(withdrawn, "withdraw").status_code, 409)
        self.assertEqual(self.row(withdrawn)["status"], "withdrawn")
        self.assertEqual(self.client.get(f"/api/qualification-submissions/{withdrawn}/evidence").data, PDF)

    def test_file_formats_mime_size_and_failed_upload_cleanup(self):
        for data, filename in ((PDF, "one.pdf"), (PNG, "two.png"), (JPEG, "three.jpeg")):
            sid = self.submit(content=data, filename=filename).json["id"]
            self.assertEqual(self.client.get(f"/api/qualification-submissions/{sid}/evidence").data, data)
            self.review(sid, "reject")
        count = len(list(self.root.iterdir()))
        for data, filename in ((b"<script>bad</script>", "fake.pdf"), (PDF, "bad.html"),
                               (b"<svg/>", "bad.svg"), (b"", "empty.pdf"), (PNG, "wrong.pdf")):
            self.assertEqual(self.submit(content=data, filename=filename).status_code, 400)
            self.assertEqual(len(list(self.root.iterdir())), count)
        self.assertEqual(self.submit(content=None).status_code, 400)
        self.assertEqual(self.submit(until="2028-02-30").status_code, 400)
        exact = b"%PDF-" + b"x" * (qualifications.MAX_EVIDENCE_BYTES - 5)
        sid = self.submit(content=exact).json["id"]
        self.assertEqual(self.row(sid)["size_bytes"], qualifications.MAX_EVIDENCE_BYTES)
        self.assertEqual(self.client.get(f"/api/qualification-submissions/{sid}/evidence").data, exact)
        self.review(sid, "reject")
        count = len(list(self.root.iterdir()))
        self.assertEqual(self.submit(content=exact + b"x").status_code, 400)
        self.assertEqual(len(list(self.root.iterdir())), count)
        self.assertTrue(all(not p.name.startswith(".") for p in self.root.iterdir()))

    def test_deletion_is_admin_only_audited_and_blocks_orphaning_on_user_removal(self):
        sid = self.submit().json["id"]
        path = self.root / self.row(sid)["evidence_path"]
        self.assertTrue(path.is_file())
        self.assertEqual(self.client.delete("/api/admin/users/2").status_code, 409)
        self.sign_in(2)
        url = f"/api/qualification-submissions/{sid}"
        self.assertEqual(self.client.delete(url).status_code, 403)
        self.assertEqual(self.client.delete(url + "/evidence").status_code, 403)
        self.sign_in(1)
        self.assertEqual(self.client.delete(url + "/evidence").status_code, 200)
        self.assertFalse(path.exists())
        self.assertIsNotNone(self.row(sid)["evidence_deleted_at"])
        self.assertEqual(self.review(sid).status_code, 400)
        self.assertEqual(self.client.get(url + "/evidence").status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 200)
        events = self.client.get("/api/users/2/qualification-audit").json["events"]
        self.assertEqual([e["action"] for e in events], ["deleted", "evidence-deleted", "submitted"])
        self.assertEqual(json.loads(events[-1]["details_json"])["category"], "A1/A3")
        self.assertNotIn("evidence.pdf", json.dumps(events))
        self.assertEqual(self.client.delete("/api/admin/users/2").status_code, 200)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_admin_corrects_dates_of_any_submission_with_an_audit_entry(self):
        sid = self.submit(start="2024-01-01").json["id"]
        self.assertEqual(self.review(sid).status_code, 200)
        url = f"/api/qualification-submissions/{sid}"
        self.sign_in(2)
        self.assertEqual(self.client.put(url, json={"valid_until": "2025-01-01"}).status_code, 403)
        self.sign_in(1)
        for body in ({}, {"status": "pending"}, {"valid_until": "2025-13-01"}, {"valid_until": "2023-12-31"}):
            self.assertEqual(self.client.put(url, json=body).status_code, 400, body)
        self.assertEqual(self.client.put(url, json={"valid_until": "2025-01-01"}).status_code, 200)
        row = self.row(sid)
        self.assertEqual((row["valid_from"], row["valid_until"], row["status"]), ("2024-01-01", "2025-01-01", "approved"))
        self.assertTrue(next(i for i in self.listing().json["items"] if i["id"] == sid)["expired"])
        event = self.client.get("/api/users/2/qualification-audit").json["events"][0]
        self.assertEqual(event["action"], "dates-changed")
        self.assertEqual({k: v for k, v in json.loads(event["details_json"]).items() if "valid" in k},
                         dict(valid_from="2024-01-01", valid_until="2025-01-01",
                              old_valid_from="2024-01-01", old_valid_until="2028-06-30"))
        tid = self.create_type("Expiry-controlled", False)
        self.client.put(f"/api/qualification-types/{tid}",
                        json=dict(name="Expiry-controlled", requires_evidence=False, requires_expiry=True))
        other = self.submit(tid=tid, content=None).json["id"]
        self.assertEqual(self.client.put(f"/api/qualification-submissions/{other}",
                                         json={"valid_until": ""}).status_code, 400)

    def test_api_keys_can_read_but_cannot_modify_qualifications(self):
        self.con.execute("INSERT INTO api_keys(user_id,name,key_hash,prefix,created_at) VALUES (1,'test',?,'test','now')",
                         (hashlib.sha256(b"logdb_test-key").hexdigest(),))
        self.con.commit()
        headers = {"Authorization": "Bearer logdb_test-key"}
        self.assertEqual(self.client.post("/api/qualification-types", headers=headers,
                                         json={"name": "New", "requires_evidence": False}).status_code, 403)
        sid = self.submit().json["id"]
        self.assertEqual(self.client.get(f"/api/qualification-submissions/{sid}/evidence", headers=headers).data, PDF)
        self.assertEqual(self.client.post(f"/api/qualification-submissions/{sid}/review", headers=headers,
                                         json={"action": "approve"}).status_code, 403)
        self.assertEqual(self.client.post("/api/users/2/qualifications", headers=headers,
                                         data={"type_id": self.tid}).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/qualification-submissions/{sid}", headers=headers).status_code, 403)
        self.assertEqual(self.client.put(f"/api/qualification-submissions/{sid}", headers=headers,
                                         json={"valid_until": "2030-01-01"}).status_code, 403)

    def test_validation_type_duplicates_and_no_unintended_mutations(self):
        for body in ([], {}, {"name": 1, "requires_evidence": True},
                     {"name": "New", "requires_evidence": "false"}, {"name": "", "requires_evidence": True},
                     {"name": "New", "requires_evidence": True, "notes": "not allowed"}):
            self.assertEqual(self.client.post("/api/qualification-types", json=body).status_code, 400)
        self.assertEqual(self.client.post("/api/qualification-types", json={
            "name": "a1/a3", "requires_evidence": True}).status_code, 409)
        for tid in ("0", "99999999999999999", "not a type"):
            self.assertEqual(self.client.post("/api/users/2/qualifications", data={"type_id": tid}).status_code, 400)
        sid = self.submit().json["id"]
        self.assertEqual(self.client.post(f"/api/qualification-submissions/{sid}/review",
                                         json={"action": []}).status_code, 400)
        self.assertEqual(self.client.post(f"/api/qualification-submissions/{sid}/review",
                                         json={"action": "approve", "notes": "private"}).status_code, 400)
        self.assertEqual(self.row(sid)["status"], "pending")
        self.assertIn("no-store", self.listing().headers["Cache-Control"])
        self.assertEqual(self.client.get("/api/users/2/qualification-audit").status_code, 200)

    def test_missing_evidence_is_an_explicit_failure_and_prevents_approval(self):
        sid = self.submit().json["id"]
        (self.root / self.row(sid)["evidence_path"]).unlink()
        with self.assertLogs(self.app.logger, level="ERROR"):
            response = self.client.get(f"/api/qualification-submissions/{sid}/evidence")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.review(sid).status_code, 400)
        self.assertEqual(self.row(sid)["status"], "pending")

    def test_pilot_page_exposes_only_current_approved_summaries(self):
        old = self.submit(until="2020-01-01").json["id"]
        self.review(old)
        new = self.submit(until="2030-01-01").json["id"]
        self.review(new)
        expired_type = self.create_type("Expired licence", False)
        expired = self.submit(tid=expired_type, content=None, until="2020-01-01").json["id"]
        self.review(expired)
        self.submit(until="2031-01-01")
        expected = [
            dict(id=new, has_evidence=True, viewable=True, category="A1/A3", valid_from=None, valid_until="2030-01-01", status="approved", expired=False, internal=False),
            dict(id=expired, has_evidence=False, viewable=False, category="Expired licence", valid_from=None, valid_until="2020-01-01", status="approved", expired=True, internal=False)]
        for uid in (1, 2):
            self.sign_in(uid)
            data = self.client.get("/api/detail/people/2").json["item"]["qualifications"]
            self.assertEqual(data, {"items": expected})
            self.assertEqual(len(self.listing().json["items"]), 4)
            self.assertEqual(self.client.get(f"/api/qualification-submissions/{new}/evidence").data, PDF)
        self.sign_in(1)
        self.client.delete(f"/api/qualification-submissions/{new}/evidence")
        items = self.client.get("/api/detail/people/2").json["item"]["qualifications"]["items"]
        self.assertFalse(items[0]["has_evidence"])
        self.assertNotIn("evidence_path", items[0])
        self.sign_in(3)
        self.assertIsNone(self.client.get("/api/detail/people/2").json["item"].get("qualifications"))

    def test_people_admin_type_settings_and_pending_queue(self):
        sid = self.submit().json["id"]
        types = self.client.get("/api/admin/qualification-types")
        self.assertEqual(types.status_code, 200)
        self.assertEqual(types.json["types"][0]["id"], self.tid)
        self.assertIn("no-store", types.headers["Cache-Control"])
        queue = self.client.get("/api/admin/qualification-pending")
        self.assertEqual(queue.status_code, 200)
        self.assertEqual(queue.json["submissions"][0]["id"], sid)
        self.assertEqual(queue.json["submissions"][0]["user_id"], 2)
        self.assertEqual(queue.json["submissions"][0]["person"], "Pilot")
        self.assertNotIn("evidence_path", queue.json["submissions"][0])
        self.assertIn("no-store", queue.headers["Cache-Control"])
        for uid in (2, 3, 4):
            self.sign_in(uid)
            self.assertEqual(self.client.get("/api/admin/qualification-pending").status_code, 403)
            self.assertEqual(self.client.get("/api/admin/qualification-types").status_code, 403)
        self.sign_in(1)
        self.review(sid)
        self.assertEqual(self.client.get("/api/admin/qualification-pending").json["submissions"], [])

    def test_qualification_history_is_admin_only_even_for_record_owner(self):
        self.submit()
        for uid in (2, 3, 4, 5):
            self.sign_in(uid)
            response = self.client.get("/api/users/2/qualification-audit")
            self.assertEqual(response.status_code, 403)
            self.assertNotIn("events", response.json)
            self.assertIn("no-store", response.headers["Cache-Control"])
        self.sign_in(1)
        response = self.client.get("/api/users/2/qualification-audit")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["events"][0]["action"], "submitted")

    def test_per_type_expiry_requirement_submission_and_approval(self):
        tid = self.create_type("Expiry-controlled", False)
        pending = self.submit(tid=tid, content=None, until="").json["id"]
        settings = dict(name="Expiry-controlled", requires_evidence=False, requires_expiry=True)
        self.assertEqual(self.client.put(f"/api/qualification-types/{tid}", json=settings).status_code, 200)
        self.assertEqual(self.review(pending).status_code, 400)
        self.assertEqual(self.row(pending)["status"], "pending")
        self.review(pending, "withdraw")
        for uid in (1, 2):
            self.sign_in(uid)
            response = self.submit(tid=tid, content=None, until="")
            self.assertEqual(response.status_code, 400)
            self.assertIn("expiry date", response.json["error"])
            self.assertEqual(self.submit(tid=tid, content=None, until="invalid").status_code, 400)
            self.assertTrue(next(t for t in self.listing().json["types"] if t["id"] == tid)["requires_expiry"])
        sid = self.submit(tid=tid, content=None, until="2030-01-01").json["id"]
        self.sign_in(1)
        self.assertEqual(self.review(sid).status_code, 200)
        self.assertEqual(self.submit(content=None, until="").status_code, 400)  # Evidence is independently required.
        self.assertEqual(self.submit(until="").status_code, 201)  # Other types still allow no expiry.
        for value in ("true", 1, None, []):
            self.assertEqual(self.client.put(f"/api/qualification-types/{tid}",
                                            json={**settings, "requires_expiry": value}).status_code, 400)
        self.client.put(f"/api/qualification-types/{tid}",
                        json=dict(name="Expiry-controlled", requires_evidence=False))
        self.assertEqual(self.con.execute("SELECT requires_expiry FROM qualification_types WHERE id=?", (tid,)).fetchone()[0], 1)
        self.client.put(f"/api/qualification-types/{tid}", json={**settings, "requires_expiry": False})
        self.assertEqual(self.submit(tid=tid, content=None, until="").status_code, 201)

    def test_expiry_requirement_creation_and_existing_database_upgrade(self):
        response = self.client.post("/api/qualification-types", json=dict(
            name="New expiry type", requires_evidence=False, requires_expiry=True))
        self.assertEqual(response.status_code, 200)
        tid = response.json["id"]
        self.assertEqual(self.submit(tid=tid, content=None, until="").status_code, 400)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "legacy.sqlite"
            con = sqlite3.connect(path)
            con.row_factory = sqlite3.Row
            self.addCleanup(con.close)
            con.execute("CREATE TABLE qualification_types (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        " name TEXT NOT NULL UNIQUE COLLATE NOCASE, requires_evidence INTEGER NOT NULL,"
                        " active INTEGER NOT NULL DEFAULT 1)")
            con.execute("INSERT INTO qualification_types(name,requires_evidence) VALUES ('Legacy',1)")
            con.commit()
            with patch.object(db, "connect", return_value=con):
                for _ in range(2):
                    db.init(path)
                    row = con.execute("SELECT * FROM qualification_types").fetchone()
                    self.assertEqual(row["name"], "Legacy")
                    self.assertEqual(row["requires_expiry"], 0)
                    self.assertEqual(row["requires_start"], 0)
                    self.assertEqual(row["internal"], 0)
                    self.assertEqual(row["sort_order"], 1)
                    self.assertIn("valid_from", {r["name"] for r in con.execute(
                        "PRAGMA table_info(qualification_submissions)")})
            con.close()

    def test_validity_dates_defaults_optional_requirements_and_history(self):
        response = self.client.post("/api/qualification-types", json={
            "name": "Validity dates", "requires_evidence": False})
        self.assertEqual(response.status_code, 200)
        tid = response.json["id"]
        kind = next(t for t in self.listing().json["types"] if t["id"] == tid)
        self.assertTrue(kind["requires_start"])
        self.assertTrue(kind["requires_expiry"])
        self.assertEqual(self.submit(tid=tid, content=None, until="2020-01-01").status_code, 400)
        self.assertEqual(self.submit(tid=tid, content=None, start="2019-01-01", until="").status_code, 400)
        for start in ("invalid", "2020-02-30", "2021-01-01"):
            self.assertEqual(self.submit(tid=tid, content=None, start=start, until="2020-01-01").status_code, 400)
        response = self.submit(tid=tid, content=None, start="2019-01-01", until="2020-01-01")
        self.assertEqual(response.status_code, 201, response.json)
        sid = response.json["id"]
        self.assertEqual(self.review(sid).status_code, 200)
        item = next(i for i in self.listing().json["items"] if i["id"] == sid)
        self.assertEqual(item["valid_from"], "2019-01-01")
        self.assertTrue(item["expired"])
        current = self.client.get("/api/detail/people/2").json["item"]["qualifications"]["items"][0]
        self.assertEqual(current["valid_from"], "2019-01-01")
        events = self.client.get("/api/users/2/qualification-audit").json["events"]
        self.assertEqual(json.loads(events[0]["details_json"])["valid_from"], "2019-01-01")
        settings = dict(name="Validity dates", requires_evidence=False, requires_start=False, requires_expiry=False)
        self.assertEqual(self.client.put(f"/api/qualification-types/{tid}", json=settings).status_code, 200)
        pending = self.submit(tid=tid, content=None, until="").json["id"]
        self.assertEqual(self.client.put(f"/api/qualification-types/{tid}",
                                        json={**settings, "requires_start": True}).status_code, 200)
        self.assertEqual(self.review(pending).status_code, 400)
        self.assertEqual(self.row(sid)["status"], "approved")
        for invalid in (1, "true", None):
            self.assertEqual(self.client.put(f"/api/qualification-types/{tid}", json={
                **settings, "requires_start": invalid}).status_code, 400)

    def test_type_internal_flag_order_and_bulk_save(self):
        second = self.create_type("Rotorcraft", True)
        self.assertFalse(next(t for t in self.listing().json["types"] if t["id"] == self.tid)["internal"])
        self.assertEqual(self.client.put(f"/api/qualification-types/{second}", json={
            "name": "Rotorcraft", "requires_evidence": True, "internal": True}).status_code, 200)
        self.assertTrue(next(t for t in self.listing().json["types"] if t["id"] == second)["internal"])
        response = self.client.put("/api/admin/qualification-types", json={"types": [
            dict(id=second, name="Rotorcraft", requires_evidence=True, requires_start=False,
                 requires_expiry=True, internal=True, active=True),
            dict(id=self.tid, name="A1/A3", requires_evidence=True, requires_start=False,
                 requires_expiry=False, internal=False, active=True),
            dict(name="Crew check", requires_evidence=False, requires_start=False,
                 requires_expiry=False, internal=True, active=True)]})
        self.assertEqual(response.status_code, 200, response.json)
        names = [t["name"] for t in self.client.get("/api/admin/qualification-types").json["types"]]
        self.assertEqual(names[:3], ["Rotorcraft", "A1/A3", "Crew check"])
        created = next(t for t in self.listing().json["types"] if t["name"] == "Crew check")
        self.assertTrue(created["internal"])
        self.assertFalse(created["requires_start"])
        for uid in (2, 3):
            self.sign_in(uid)
            self.assertEqual(self.client.put("/api/admin/qualification-types", json={"types": []}).status_code, 403)
        self.sign_in(1)
        self.assertEqual(self.client.put("/api/admin/qualification-types", json={"types": "no"}).status_code, 400)
        for invalid in (1, "true", None):
            self.assertEqual(self.client.put(f"/api/qualification-types/{second}", json={
                "name": "Rotorcraft", "requires_evidence": True, "internal": invalid}).status_code, 400)

    def test_saved_type_order_reaches_people_overviews_and_account(self):
        second = self.create_type("Rotorcraft", False)
        third = self.create_type("Crew check", False)
        for tid in (second, self.tid, third):
            sid = self.submit(tid=tid).json["id"]
            self.assertEqual(self.review(sid).status_code, 200)
        types = self.client.get("/api/admin/qualification-types").json["types"]
        by_id = {t["id"]: dict(id=t["id"], name=t["name"], requires_evidence=bool(t["requires_evidence"]),
                              requires_start=bool(t["requires_start"]), requires_expiry=bool(t["requires_expiry"]),
                              internal=bool(t["internal"]), active=bool(t["active"])) for t in types}
        order = [self.tid, third, second]
        response = self.client.put("/api/admin/qualification-types", json={
            "types": [by_id[tid] for tid in order]})
        self.assertEqual(response.status_code, 200, response.json)
        expected = ["A1/A3", "Crew check", "Rotorcraft"]

        def names(payload):
            return [item["category"] for item in payload["items"] if item["status"] == "approved"]

        for endpoint in ("/api/admin/people", "/api/people-directory"):
            payload = self.client.get(endpoint).json
            person = next(u for u in payload["users"] if u["id"] == 2)
            self.assertEqual(names(person["qualifications"]), expected)
        self.assertEqual(names(self.listing().json), expected)
        self.assertEqual(names(self.client.get("/api/detail/people/2").json["item"]["qualifications"]), expected)
        self.sign_in(2)
        person = next(u for u in self.client.get("/api/people-directory").json["users"] if u["id"] == 2)
        self.assertEqual(names(person["qualifications"]), expected)
        self.assertEqual(names(self.listing().json), expected)
        self.sign_in(3)
        person = next(u for u in self.client.get("/api/people-directory").json["users"] if u["id"] == 2)
        self.assertIsNone(person["qualifications"])

    def test_management_page_assets_and_read_only_pilot_page(self):
        with self.client.get("/qualifications.js") as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn("no-store", response.headers["Cache-Control"])
            script = response.get_data(as_text=True)
            self.assertIn("data-open-upload", script)
            self.assertIn("info-tip", script)
            self.assertNotIn("not recorded", script)
        with self.client.get("/people") as response:
            people = response.get_data(as_text=True)
        with self.client.get("/account") as response:
            account = response.get_data(as_text=True)
        with self.client.get("/people/2") as response:
            pilot = response.get_data(as_text=True)
        self.assertIn('id="peopleSearch"', people)
        self.assertIn('id="personTabs"', people)
        self.assertIn('id="typeSettings"', people)
        self.assertIn('saveQualificationTypes', people)
        self.assertIn('Internal', people)
        self.assertNotIn('saveQualificationType(', people)
        self.assertNotIn('Save type', people)
        self.assertIn('id="approvalQueue"', people)
        self.assertNotIn("1. Choose a person", people)
        self.assertNotIn("2. Selected person", people)
        self.assertIn('class="section-heading"', people)
        self.assertIn("<summary>Help</summary>", people)
        self.assertIn('id="myQualifications"', account)
        self.assertIn('src="/qualifications.js"', account)
        self.assertNotIn('submitQualification', pilot)
        self.assertNotIn('qualificationHistory', pilot)
        self.assertNotIn('saveQualificationType', pilot)
        self.assertIn("Import ZIP", people)
        self.assertIn("previewQualImport", people)
        self.assertIn("data-view", script)
        self.assertIn("Archive (", script)
        self.assertIn("/api/admin/qualifications/import", people)


class QualificationImportTests(AppTests):
    def setUp(self):
        super().setUp()
        self.sign_in(1)
        evidence = tempfile.TemporaryDirectory()
        staging = tempfile.TemporaryDirectory()
        self.addCleanup(evidence.cleanup)
        self.addCleanup(staging.cleanup)
        self.root = Path(evidence.name)
        self.stage = Path(staging.name)
        old_ev = self.app.config["QUALIFICATION_EVIDENCE_DIR"]
        old_imp = self.app.config["QUALIFICATION_IMPORT_DIR"]
        self.app.config["QUALIFICATION_EVIDENCE_DIR"] = self.root
        self.app.config["QUALIFICATION_IMPORT_DIR"] = self.stage
        self.addCleanup(self.app.config.__setitem__, "QUALIFICATION_EVIDENCE_DIR", old_ev)
        self.addCleanup(self.app.config.__setitem__, "QUALIFICATION_IMPORT_DIR", old_imp)
        self.tid = self.client.post("/api/qualification-types", json={
            "name": "A2", "requires_evidence": True, "requires_start": False, "requires_expiry": True}).json["id"]

    def zip_payload(self, person="Pilot", type_name="A2", until="2026-12-01", pdf=PDF,
                    filename="Pilot/A2_until-2026-12-01.pdf"):
        csv_text = "person,type,valid_from,valid_until,file\n" f"{person},{type_name},,{until},{filename}\n"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("manifest.csv", csv_text)
            archive.writestr(filename, pdf)
        buf.seek(0)
        return buf

    def preview(self, payload, name="qualifications.zip"):
        response = self.client.post("/api/admin/qualifications/import",
                                    data={"file": (payload, name)})
        payload.close()
        return response

    def test_preview_and_commit_approves_with_evidence_and_dates(self):
        preview = self.preview(self.zip_payload())
        self.assertEqual(preview.status_code, 200, preview.json)
        self.assertTrue(preview.json["can_commit"])
        self.assertEqual(preview.json["ok"], 1)
        self.assertEqual(preview.json["rows"][0]["person_name"], "Pilot")
        self.assertEqual(preview.json["rows"][0]["valid_until"], "2026-12-01")
        commit = self.client.post("/api/admin/qualifications/import/commit",
                                  json={"token": preview.json["token"]})
        self.assertEqual(commit.status_code, 201, commit.json)
        sid = commit.json["ids"][0]
        row = self.con.execute("SELECT * FROM qualification_submissions WHERE id=?", (sid,)).fetchone()
        self.assertEqual(row["status"], "approved")
        self.assertEqual(row["user_id"], 2)
        self.assertEqual(row["valid_until"], "2026-12-01")
        self.assertEqual(row["submitted_by"], 1)
        self.assertEqual(row["reviewed_by"], 1)
        downloaded = self.client.get(f"/api/qualification-submissions/{sid}/evidence")
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.data, PDF)

    def archive_zip(self, *periods):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("manifest.csv", "person,type,valid_from,valid_until,file\n" + "".join(
                f"Pilot,A2,,{until},Pilot/A2_until-{until}.pdf\n" for until in periods))
            for until in periods:
                archive.writestr(f"Pilot/A2_until-{until}.pdf", PDF)
        buf.seek(0)
        return buf

    def import_zip(self, payload):
        preview = self.preview(payload)
        self.assertEqual(preview.status_code, 200, preview.json)
        commit = self.client.post("/api/admin/qualifications/import/commit", json={"token": preview.json["token"]})
        return preview.json, commit

    def statuses(self):
        return [tuple(r) for r in self.con.execute(
            "SELECT valid_until,status FROM qualification_submissions WHERE user_id=2 ORDER BY valid_until")]

    def test_older_periods_are_archived_and_reimport_is_skipped(self):
        preview, commit = self.import_zip(self.archive_zip("2019-06-01", "2027-03-01", "2022-06-01"))
        self.assertEqual([r["outcome"] for r in preview["rows"]], ["archive", "current", "archive"])
        self.assertEqual((commit.status_code, commit.json["current"], commit.json["archived"]), (201, 1, 2))
        self.assertEqual(self.statuses(), [("2019-06-01", "superseded"), ("2022-06-01", "superseded"),
                                           ("2027-03-01", "approved")])
        preview, commit = self.import_zip(self.archive_zip("2019-06-01", "2027-03-01", "2017-01-01"))
        self.assertEqual([r["outcome"] for r in preview["rows"]], ["skip", "skip", "archive"])
        self.assertEqual((commit.status_code, commit.json["archived"], commit.json["skipped"]), (201, 1, 2))
        self.assertEqual(self.statuses()[-1], ("2027-03-01", "approved"))
        self.assertEqual(len(self.statuses()), 4)
        repeat = self.preview(self.archive_zip("2019-06-01"))
        self.assertFalse(repeat.json["can_commit"])
        preview, commit = self.import_zip(self.archive_zip("2029-01-01"))
        self.assertEqual(preview["rows"][0]["outcome"], "current")
        self.assertIn("Archive", preview["rows"][0]["warning"])
        self.assertEqual(self.statuses()[-2:], [("2027-03-01", "superseded"), ("2029-01-01", "approved")])
        detail = self.client.get("/api/detail/people/2").json["item"]["qualifications"]["items"]
        self.assertEqual([i["valid_until"] for i in detail], ["2029-01-01"])

    def test_unknown_types_are_created_on_commit(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("manifest.csv", "person,type,valid_from,valid_until,file\n"
                             "Pilot,CTR,,2027-01-01,Pilot/CTR.pdf\nViewer,ctr,,,\nPilot,GVC,,2028-01-01,Pilot/GVC.pdf\n")
            archive.writestr("Pilot/CTR.pdf", PDF)
            archive.writestr("Pilot/GVC.pdf", PDF)
        buf.seek(0)
        preview = self.preview(buf)
        self.assertTrue(preview.json["can_commit"], preview.json)
        self.assertEqual(preview.json["new_types"], 2)
        self.assertFalse(self.con.execute("SELECT 1 FROM qualification_types WHERE name='CTR'").fetchone())
        commit = self.client.post("/api/admin/qualifications/import/commit", json={"token": preview.json["token"]})
        self.assertEqual((commit.status_code, commit.json["new_types"], commit.json["current"]), (201, 2, 3))
        types = {r["name"]: tuple(r)[1:] for r in self.con.execute(
            "SELECT name,requires_evidence,requires_expiry,requires_start,internal,active FROM qualification_types")}
        self.assertEqual(types["CTR"], (0, 0, 0, 0, 1))
        self.assertEqual(types["GVC"], (1, 1, 0, 0, 1))
        self.assertEqual(self.preview(self.zip_payload(type_name="ctr")).json["rows"][0]["new_type"], None)

    def test_rows_with_only_a_name_add_people(self):
        csv_text = b"person,type,valid_from,valid_until,file\nExample Member,,,,\nPilot,,,,\nNobody,,,2027-01-01,\n"
        blocked = self.preview(io.BytesIO(csv_text), "manifest.csv").json
        self.assertEqual([r["outcome"] for r in blocked["rows"]][:2], ["person", "skip"])
        self.assertIn("Type is required", blocked["rows"][2]["error"])
        preview = self.preview(io.BytesIO(csv_text.rsplit(b"Nobody", 1)[0]), "manifest.csv").json
        self.assertTrue(preview["can_commit"], preview)
        commit = self.client.post("/api/admin/qualifications/import/commit", json={"token": preview["token"]})
        self.assertEqual((commit.status_code, commit.json["new_people"], commit.json["imported"]), (201, 1, 0))
        uid = self.con.execute("SELECT id FROM users WHERE display_name='Example Member'").fetchone()[0]
        self.assertFalse(self.con.execute("SELECT 1 FROM qualification_submissions WHERE user_id=?", (uid,)).fetchone())
        again = self.preview(io.BytesIO(b"person,type,valid_from,valid_until,file\nExample Member,,,,\n"), "manifest.csv").json
        self.assertEqual((again["rows"][0]["outcome"], again["can_commit"]), ("skip", False))

    def test_new_names_become_users_and_ambiguous_or_csv_only_rows_are_blocked(self):
        before = self.con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        preview = self.preview(self.zip_payload(person="Example Newcomer"))
        self.assertTrue(preview.json["can_commit"])
        self.assertEqual((preview.json["new_people"], preview.json["rows"][0]["outcome"]), (1, "current"))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM users").fetchone()[0], before)
        commit = self.client.post("/api/admin/qualifications/import/commit", json={"token": preview.json["token"]})
        self.assertEqual((commit.status_code, commit.json["new_people"]), (201, 1))
        user = self.con.execute("SELECT * FROM users WHERE display_name='Example Newcomer'").fetchone()
        self.assertEqual((user["username"], user["password_hash"], user["email"], user["is_admin"]),
                         ("ExampleNewcomer", None, None, 0))
        self.assertEqual(self.con.execute("SELECT status FROM qualification_submissions WHERE user_id=?",
                                          (user["id"],)).fetchone()[0], "approved")
        self.assertEqual(self.preview(self.zip_payload(person="Example Newcomer")).json["rows"][0]["outcome"], "skip")
        self.con.execute("UPDATE users SET display_name='Pilot' WHERE id=3")
        self.con.commit()
        ambiguous = self.preview(self.zip_payload(person="Pilot"))
        self.assertFalse(ambiguous.json["can_commit"])
        self.assertIn("Ambiguous person", ambiguous.json["rows"][0]["error"])
        csv_only = io.BytesIO(b"person,type,valid_from,valid_until,file\nAdmin,A2,,2026-12-01,Admin/A2.pdf\n")
        missing = self.preview(csv_only, "manifest.csv")
        self.assertEqual(missing.status_code, 200)
        self.assertFalse(missing.json["can_commit"])
        self.assertIn("ZIP", missing.json["rows"][0]["error"])

    def test_import_is_admin_session_only(self):
        payload = self.zip_payload()
        self.sign_in(2)
        self.assertEqual(self.preview(payload).status_code, 403)
        self.con.execute("INSERT INTO api_keys(user_id,name,key_hash,prefix,created_at) VALUES (1,'test',?,'test','now')",
                         (hashlib.sha256(b"logdb_test-key").hexdigest(),))
        self.con.commit()
        self.sign_in(1)
        headers = {"Authorization": "Bearer logdb_test-key"}
        zipped = self.zip_payload()
        self.assertEqual(self.client.post("/api/admin/qualifications/import",
                                          data={"file": (zipped, "qualifications.zip")},
                                          headers=headers).status_code, 403)
        zipped.close()
