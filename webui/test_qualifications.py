import hashlib
import io
import json
import tempfile
from pathlib import Path

import qualifications
from test_historical import AppTests

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
                             json={"name": name, "requires_evidence": required})
        self.assertEqual(r.status_code, 200, r.json)
        return r.json["id"]

    def submit(self, uid=2, content=PDF, filename="evidence.pdf", tid=None, until="2028-06-30"):
        data = {"type_id": str(tid or self.tid), "valid_until": until}
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
        self.assertIn("sandbox", downloaded.headers["Content-Security-Policy"])
        self.assertEqual(self.client.delete(file_url).status_code, 403)
        for uid in (3, 4, 5):
            self.sign_in(uid)
            self.assertEqual(self.listing().status_code, 404)
            self.assertEqual(self.client.get(file_url).status_code, 404)
            self.assertEqual(self.client.get("/api/users/2/qualification-audit").status_code, 404)
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
