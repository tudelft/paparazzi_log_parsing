import datetime
import io
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from . import test_qualifications as tq
from .test_historical import AppTests

PDF = tq.PDF


class DocumentTests(AppTests):
    """Documents page: admin text, groups and shared documents (optionally only for holders of a qualification),
    and the person's own current qualifications. Users: 1 Admin, 2 Pilot, 3 Viewer, 4 Outsider."""
    create_type, submit, review = tq.QualificationTests.create_type, tq.QualificationTests.submit, tq.QualificationTests.review

    def setUp(self):
        super().setUp()
        settings = patch.dict(os.environ, LOGDB_DOCUMENTS="1")
        settings.start()
        self.addCleanup(settings.stop)
        for key, name in (("QUALIFICATION_EVIDENCE_DIR", "evidence"), ("DOCUMENTS_DIR", "documents")):
            folder = tempfile.TemporaryDirectory()
            self.addCleanup(folder.cleanup)
            old = self.app.config[key]
            self.app.config[key] = Path(folder.name) / name
            self.addCleanup(self.app.config.__setitem__, key, old)
        self.sign_in(1)

    def upload(self, title="Insurance", content=PDF, filename="insurance.pdf", **fields):
        data = {"title": title, "file": (io.BytesIO(content), filename), **{k: str(v) for k, v in fields.items()}}
        return self.client.post("/api/admin/documents", data=data, content_type="multipart/form-data")

    def listing(self):
        r = self.client.get("/api/documents")
        self.assertEqual(r.status_code, 200, r.json)
        return r.json

    def test_off_by_default(self):
        with patch.dict(os.environ, LOGDB_DOCUMENTS="0"):
            self.assertEqual(self.client.get("/api/documents").status_code, 404)
            self.assertEqual(self.client.get("/documents").status_code, 404)
            self.assertFalse(self.client.get("/api/me").json["documents"])
        self.assertTrue(self.client.get("/api/me").json["documents"])
        r = self.client.get("/documents")
        self.assertIn(b"<title>Documents</title>", r.data)
        r.close()

    def test_intro_groups_and_documents(self):
        self.assertEqual(self.client.put("/api/admin/documents/intro", json={"intro": "Show these to the police."})
                         .status_code, 200)
        r = self.client.post("/api/admin/document-groups", json={"name": "Company", "description": "Insurance etc."})
        self.assertEqual(r.status_code, 201, r.json)
        gid = r.json["id"]
        r = self.upload(group_id=gid, description="Current certificate")
        self.assertEqual(r.status_code, 201, r.json)
        did = r.json["id"]
        self.sign_in(2)
        d = self.listing()
        self.assertEqual(d["intro"], "Show these to the police.")
        self.assertFalse(d["can_edit"])
        self.assertNotIn("types", d)
        self.assertEqual([(x["id"], x["group_id"], x["description"]) for x in d["documents"]],
                         [(did, gid, "Current certificate")])
        r = self.client.get(f"/api/documents/{did}/view")
        self.assertEqual((r.status_code, r.mimetype, r.data), (200, "application/pdf", PDF))
        r = self.client.get(f"/api/documents/{did}/download")
        self.assertIn("attachment", r.headers["Content-Disposition"])
        self.assertEqual(r.data, PDF)
        # non-admins cannot change anything
        self.assertEqual(self.client.put("/api/admin/documents/intro", json={"intro": "x"}).status_code, 403)
        self.assertEqual(self.upload().status_code, 403)
        self.assertEqual(self.client.delete(f"/api/admin/documents/{did}").status_code, 403)
        # a group with documents cannot be deleted; an empty one can
        self.sign_in(1)
        self.assertEqual(self.client.delete(f"/api/admin/document-groups/{gid}").status_code, 409)
        self.assertEqual(self.client.put(f"/api/admin/documents/{did}", json={"title": "Insurance 2027"}).status_code, 200)
        self.assertEqual(self.listing()["documents"][0]["group_id"], None)
        self.assertEqual(self.client.delete(f"/api/admin/document-groups/{gid}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/admin/documents/{did}").status_code, 200)
        self.assertEqual(self.listing()["documents"], [])
        self.assertEqual(list(Path(self.app.config["DOCUMENTS_DIR"]).iterdir()), [])

    def test_document_visible_only_with_qualification(self):
        tid = self.create_type("Outdoor pilot", False)
        did = self.upload(type_id=tid).json["id"]
        future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        r = self.submit(uid=2, tid=tid, until=future, content=None)
        self.assertEqual(r.status_code, 201, r.json)
        self.review(r.json["id"])
        self.assertEqual(len(self.listing()["documents"]), 1)  # admins see every document
        self.sign_in(2)
        self.assertEqual([x["id"] for x in self.listing()["documents"]], [did])
        self.sign_in(4)
        self.assertEqual(self.listing()["documents"], [])
        self.assertEqual(self.client.get(f"/api/documents/{did}/download").status_code, 404)

    def test_own_qualifications(self):
        future = (datetime.date.today() + datetime.timedelta(days=365)).isoformat()
        past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        a1a3, rt, old = self.create_type("A1/A3", True), self.create_type("RT", False), self.create_type("A2", True)
        for tid, until, content in ((a1a3, future, PDF), (rt, "", None), (old, past, PDF)):
            r = self.submit(uid=1, tid=tid, until=until, content=content)
            self.assertEqual(r.status_code, 201, r.json)
            self.review(r.json["id"])
        r = self.submit(uid=2, tid=a1a3, until=future)  # someone else's
        self.review(r.json["id"])
        own = {q["type"]: q for q in self.listing()["qualifications"]}
        self.assertEqual(set(own), {"A1/A3", "RT"})  # expired A2 and other people's are left out
        self.assertTrue(own["A1/A3"]["view"].endswith("/evidence/view"))
        self.assertNotIn("download", own["RT"])
