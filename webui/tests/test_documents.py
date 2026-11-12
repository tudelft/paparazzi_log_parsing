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

    def test_inactive_qualification_limit_is_kept(self):
        tid = self.create_type("Old permit", False)
        did = self.upload(type_id=tid).json["id"]
        self.con.execute("UPDATE qualification_types SET active=0 WHERE id=?", (tid,))
        self.con.commit()
        types = {t["id"]: t["active"] for t in self.listing()["types"]}
        self.assertEqual(types[tid], 0)  # still offered to the edit dialog
        r = self.client.put(f"/api/admin/documents/{did}", json={"title": "Renamed", "type_id": tid})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(self.listing()["documents"][0]["type_id"], tid)
        self.sign_in(2)
        self.assertEqual(self.listing()["documents"], [])

    def test_replace_file_keeps_settings(self):
        tid = self.create_type("Outdoor pilot", False)
        did = self.upload(type_id=tid, description="2026", sort_order=3).json["id"]
        folder = Path(self.app.config["DOCUMENTS_DIR"])
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        old = set(folder.iterdir())
        new = PDF.replace(b"%PDF", b"%PDF ", 1)
        r = self.client.post(f"/api/admin/documents/{did}/file", data={"file": (io.BytesIO(new), "insurance-2027.pdf")},
                             content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200, r.json)
        d = self.listing()["documents"][0]
        self.assertEqual((d["title"], d["description"], d["type_id"], d["sort_order"], d["filename"]),
                         ("Insurance", "2026", tid, 3, "insurance-2027.pdf"))
        self.assertEqual(self.client.get(f"/api/documents/{did}/download").data, new)
        self.assertTrue(old.isdisjoint(folder.iterdir()))  # the old file is removed
        self.sign_in(2)
        r = self.client.post(f"/api/admin/documents/{did}/file", data={"file": (io.BytesIO(new), "x.pdf")},
                             content_type="multipart/form-data")
        self.assertEqual(r.status_code, 403)

    def test_admin_only_and_profile_roles(self):
        self.ns["profiles"].seed(self.con)
        role = {r["name"]: r["id"] for r in self.con.execute("SELECT id, name FROM profile_roles")}
        staff, other = role["Staff"], next(i for n, i in role.items() if n != "Staff")
        self.con.execute("INSERT INTO user_profiles (user_id, role_id, indefinite) VALUES (2, ?, 1), (3, ?, 1)",
                         (staff, other))
        self.con.commit()
        admins = self.upload(title="Admins", admin_only=1).json["id"]
        r = self.client.post("/api/admin/documents", data={"title": "Staff", "file": (io.BytesIO(PDF), "s.pdf"),
                                                           "role_ids": [str(staff)]}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 201, r.json)
        staff_doc = r.json["id"]
        self.assertEqual({d["id"] for d in self.listing()["documents"]}, {admins, staff_doc})
        seen = lambda uid: (self.sign_in(uid), {d["id"] for d in self.listing()["documents"]})[1]
        with patch.dict(os.environ, LOGDB_PERSON_PROFILE="1"):
            self.assertEqual(seen(2), {staff_doc})
            self.assertEqual(seen(3), set())
        self.assertEqual(seen(2), set())  # profiles off: fails closed
        # every limit must be met: a role and a qualification
        self.sign_in(1)
        tid = self.create_type("Outdoor pilot", False)
        self.client.put(f"/api/admin/documents/{staff_doc}", json={"title": "Staff", "type_id": tid, "role_ids": [staff]})
        with patch.dict(os.environ, LOGDB_PERSON_PROFILE="1"):
            self.assertEqual(seen(2), set())
            self.sign_in(1)
            self.review(self.submit(uid=2, tid=tid, until="", content=None).json["id"])
            self.assertEqual(seen(2), {staff_doc})
            # the role removed from the profile lists: only admins see it
            self.con.execute("DELETE FROM profile_roles WHERE id=?", (staff,))
            self.con.commit()
            self.assertEqual(seen(2), set())
        self.sign_in(1)
        d = next(d for d in self.listing()["documents"] if d["id"] == staff_doc)
        self.assertEqual((d["roles_limited"], d["role_ids"]), (True, []))

    def test_storage_counts_documents(self):
        import storage_usage
        self.upload()
        totals = storage_usage._walk(Path(self.app.config["DOCUMENTS_DIR"]).parent)
        self.assertEqual(list(totals), ["documents"])
