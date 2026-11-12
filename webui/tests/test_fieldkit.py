import datetime
import tempfile
from pathlib import Path

from . import test_qualifications as tq
from .test_historical import AppTests

PDF = tq.PDF


class FieldKitTests(AppTests):
    """GET /api/fieldkit: the signed-in pilot's highest valid drone licence and RT licence."""
    create_type, submit, review = tq.QualificationTests.create_type, tq.QualificationTests.submit, tq.QualificationTests.review

    def setUp(self):
        super().setUp()
        self.sign_in(1)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        old = self.app.config["QUALIFICATION_EVIDENCE_DIR"]
        self.app.config["QUALIFICATION_EVIDENCE_DIR"] = Path(folder.name)
        self.addCleanup(self.app.config.__setitem__, "QUALIFICATION_EVIDENCE_DIR", old)
        self.tid = self.create_type("A1/A3", True)

    def approved(self, tid, until, content=PDF):
        r = self.submit(uid=1, tid=tid, until=until, content=content)
        self.assertEqual(r.status_code, 201, r.json)
        self.assertEqual(self.review(r.json["id"]).status_code, 200)
        return r.json["id"]

    def kit(self):
        r = self.client.get("/api/fieldkit")
        self.assertEqual(r.status_code, 200, r.json)
        return r.json["pilot"]

    def test_page_and_menu(self):
        r = self.client.get("/fieldkit")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"<title>Field kit</title>", r.data)
        r.close()

    def test_highest_valid_licence_and_rt(self):
        future = (datetime.date.today() + datetime.timedelta(days=365)).isoformat()
        past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        self.assertIsNone(self.kit()["licence"])
        a1a3 = self.approved(self.tid, future)
        self.assertEqual(self.kit()["licence"]["id"], a1a3)
        a2 = self.approved(self.create_type("A2", True), future)
        self.assertEqual(self.kit()["licence"]["id"], a2)
        sts = self.create_type("STS", True)
        self.approved(sts, past)  # expired: not shown
        self.assertEqual(self.kit()["licence"]["type"], "A2")
        sts_id = self.approved(sts, future)
        kit = self.kit()
        self.assertEqual((kit["licence"]["id"], kit["licence"]["type"]), (sts_id, "STS"))
        self.assertEqual(kit["licence"]["view"], f"/api/qualification-submissions/{sts_id}/evidence/view")
        self.assertIsNone(kit["rt"])
        rt = self.approved(self.create_type("RT", True), "")
        self.assertEqual(self.kit()["rt"]["id"], rt)

    def test_pending_or_without_evidence_is_not_shown(self):
        future = (datetime.date.today() + datetime.timedelta(days=365)).isoformat()
        r = self.submit(uid=1, tid=self.tid, until=future)
        self.assertEqual(r.status_code, 201, r.json)  # pending, not approved
        self.assertIsNone(self.kit()["licence"])
        self.approved(self.create_type("RT", False), "", content=None)  # no document to show
        self.assertIsNone(self.kit()["rt"])

    def test_only_own_documents(self):
        future = (datetime.date.today() + datetime.timedelta(days=365)).isoformat()
        r = self.submit(uid=2, tid=self.tid, until=future)
        self.review(r.json["id"])
        self.assertIsNone(self.kit()["licence"])
