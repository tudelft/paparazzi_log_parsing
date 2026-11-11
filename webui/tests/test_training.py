import datetime
import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import training
from .test_historical import AppTests
from .test_qualifications import PNG


class TrainingTests(AppTests):
    def setUp(self):
        super().setUp()
        self.sign_in(1)
        settings = patch.dict(os.environ, LOGDB_TRAINING="1")
        settings.start()
        self.addCleanup(settings.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        old = self.app.config.get("TRAINING_IMAGE_DIR")
        self.app.config["TRAINING_IMAGE_DIR"] = Path(folder.name)
        self.addCleanup(self.app.config.__setitem__, "TRAINING_IMAGE_DIR", old)
        created = self.client.post("/api/qualification-types", json={
            "name": "Briefing", "requires_evidence": False, "requires_start": False, "requires_expiry": True})
        self.assertEqual(created.status_code, 200, created.json)
        self.type_id = created.json["id"]

    def add_training(self, **body):
        payload = {"name": "Safety", "type_id": self.type_id, "valid_days": 365, **body}
        r = self.client.post("/api/admin/trainings", json=payload)
        self.assertEqual(r.status_code, 201, r.json)
        return r.json["id"]

    def add_question(self, tid, **body):
        payload = {"prompt": "Pick the safe option", "kind": "single", "points": 1,
                   "choices": [{"label": "Safe", "correct": True}, {"label": "Unsafe", "correct": False}], **body}
        r = self.client.post(f"/api/admin/trainings/{tid}/questions", json=payload)
        self.assertEqual(r.status_code, 201, r.json)
        return r.json["id"]

    def sample_payload(self, **overrides):
        data = {
            "name": "Imported briefing",
            "description": "Mock questions only.",
            "pass_score": 1,
            "valid_days": 30,
            "ask_briefed_by": False,
            "questions": [{
                "prompt": "Which is safe?",
                "kind": "single",
                "points": 1,
                "image": "plot.png",
                "choices": [{"label": "Safe", "correct": True}, {"label": "Unsafe"}],
            }],
        }
        data.update(overrides)
        return data

    def import_file(self, payload, images=None, preview=False, replace=False, type_id=None, as_zip=True):
        data = {"file": None}
        if as_zip:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("training.json", json.dumps(payload))
                for name, blob in (images or {"plot.png": PNG}).items():
                    zf.writestr(name, blob)
            buf.seek(0)
            data["file"] = (buf, "training.zip")
        else:
            data["file"] = (io.BytesIO(json.dumps(payload).encode("utf-8")), "training.json")
        if preview:
            data["preview"] = "1"
        if replace:
            data["replace"] = "1"
        if type_id is not None:
            data["type_id"] = str(type_id)
        return self.client.post("/api/admin/trainings/import", data=data,
                                content_type="multipart/form-data")

    def test_hidden_when_disabled(self):
        with patch.dict(os.environ, LOGDB_TRAINING="0"):
            self.assertEqual(self.client.get("/training").status_code, 404)
            self.assertEqual(self.client.get("/api/trainings").status_code, 404)
            self.assertFalse(self.client.get("/api/me").json["training"])

    def test_me_flag_and_pass_grants_qualification(self):
        self.assertTrue(self.client.get("/api/me").json["training"])
        self.assertEqual(self.client.get("/training").status_code, 200)
        tid = self.add_training()
        qid = self.add_question(tid)
        quiz = self.client.get(f"/api/trainings/{tid}")
        self.assertEqual(quiz.status_code, 200)
        self.assertTrue(all("correct" in c for c in quiz.json["questions"][0]["choices"]))
        self.sign_in(2)
        taker = self.client.get(f"/api/trainings/{tid}")
        self.assertEqual(taker.status_code, 200)
        self.assertFalse(any("correct" in c for q in taker.json["questions"] for c in q["choices"]))
        safe = next(c["id"] for c in quiz.json["questions"][0]["choices"] if c["correct"])
        fail = self.client.post(f"/api/trainings/{tid}/submit", json={"answers": {str(qid): safe + 1}})
        self.assertEqual(fail.status_code, 200, fail.json)
        self.assertFalse(fail.json["passed"])
        self.assertEqual(fail.json["attempts"], 1)
        progress = self.con.execute("SELECT attempts, passed_at, answers_json FROM training_progress"
                                    " WHERE user_id=2 AND training_id=?", (tid,)).fetchone()
        self.assertEqual(progress["attempts"], 1)
        self.assertIsNone(progress["passed_at"])
        self.assertIsNone(progress["answers_json"])
        passed = self.client.post(f"/api/trainings/{tid}/submit", json={"answers": {str(qid): safe}})
        self.assertEqual(passed.status_code, 200, passed.json)
        self.assertTrue(passed.json["passed"])
        self.assertEqual(passed.json["attempts"], 2)
        self.assertEqual(self.client.post(f"/api/trainings/{tid}/submit",
                                          json={"answers": {str(qid): safe}}).status_code, 409)
        row = self.con.execute("SELECT status, valid_from, valid_until FROM qualification_submissions"
                               " WHERE id=?", (passed.json["qualification_id"],)).fetchone()
        today = datetime.datetime.now(datetime.timezone.utc).date()
        self.assertEqual(row["status"], "approved")
        self.assertEqual(row["valid_from"], today.isoformat())
        self.assertEqual(row["valid_until"], (today + datetime.timedelta(days=365)).isoformat())
        listed = self.client.get("/api/trainings").json["trainings"]
        self.assertEqual(listed[0]["attempts"], 2)
        self.assertTrue(listed[0]["passed_at"])

    def test_ranking_and_multi_and_images(self):
        tid = self.add_training(type_id=None, valid_days=None)
        multi = self.add_question(tid, prompt="Select both", kind="multi",
                                  choices=[{"label": "A", "correct": True}, {"label": "B", "correct": True},
                                           {"label": "C", "correct": False}])
        ranking = self.add_question(tid, prompt="Order these", kind="ranking",
                                    choices=[{"label": "First"}, {"label": "Second"}, {"label": "Third"}])
        image = self.client.post(f"/api/admin/trainings/{tid}/questions/{multi}/image",
                                 data={"image": (io.BytesIO(PNG), "plot.png")})
        self.assertEqual(image.status_code, 200, image.json)
        self.sign_in(1)
        admin_q = {q["id"]: q for q in self.client.get(f"/api/trainings/{tid}").json["questions"]}
        order = [c["id"] for c in admin_q[ranking]["choices"]]
        correct_multi = [c["id"] for c in admin_q[multi]["choices"] if c["correct"]]
        self.sign_in(2)
        quiz = self.client.get(f"/api/trainings/{tid}").json
        self.assertEqual(len(quiz["questions"]), 2)
        wrong = self.client.post(f"/api/trainings/{tid}/submit", json={"answers": {
            str(multi): correct_multi, str(ranking): list(reversed(order))}})
        self.assertFalse(wrong.json["passed"])
        ok = self.client.post(f"/api/trainings/{tid}/submit", json={"answers": {
            str(multi): correct_multi, str(ranking): order}})
        self.assertTrue(ok.json["passed"], ok.json)
        self.assertIsNone(ok.json["qualification_id"])
        pic = self.client.get(f"/api/trainings/questions/{multi}/image")
        self.assertEqual(pic.status_code, 200)
        self.assertEqual(pic.data[:8], PNG[:8])

    def test_inactive_hidden_and_api_keys_blocked(self):
        tid = self.add_training()
        self.add_question(tid)
        self.assertEqual(self.client.put(f"/api/admin/trainings/{tid}", json={
            "name": "Safety", "type_id": self.type_id, "valid_days": 365, "active": False}).status_code, 200)
        self.sign_in(2)
        self.assertEqual(self.client.get("/api/trainings").json["trainings"], [])
        self.assertEqual(self.client.get(f"/api/trainings/{tid}").status_code, 404)
        self.sign_in(1)
        self.con.execute("INSERT INTO api_keys(user_id,name,key_hash,prefix,created_at) VALUES (1,'test',?,'test','now')",
                         (hashlib.sha256(b"logdb_test-key").hexdigest(),))
        self.con.commit()
        headers = {"Authorization": "Bearer logdb_test-key"}
        self.assertEqual(self.client.get("/api/trainings", headers=headers).status_code, 403)
        self.assertEqual(self.client.post("/api/admin/trainings", headers=headers,
                                         json={"name": "Other"}).status_code, 403)
        self.assertEqual(self.client.post("/api/admin/trainings/import", headers=headers,
                                          data={"file": (io.BytesIO(b"{}"), "t.json")}).status_code, 403)

    def test_briefed_by_stored_on_pass_only(self):
        self.assertEqual(self.client.post("/api/admin/trainings", json={
            "name": "Needs briefers", "ask_briefed_by": True, "briefers": []}).status_code, 400)
        tid = self.add_training(ask_briefed_by=True, briefers=[1, 3])
        qid = self.add_question(tid)
        quiz = self.client.get(f"/api/trainings/{tid}").json
        self.assertEqual({b["id"] for b in quiz["briefers"]}, {1, 3})
        safe = next(c["id"] for c in quiz["questions"][0]["choices"] if c["correct"])
        self.sign_in(2)
        missing = self.client.post(f"/api/trainings/{tid}/submit", json={"answers": {str(qid): safe}})
        self.assertEqual(missing.status_code, 400, missing.json)
        self.assertEqual(self.client.post(f"/api/trainings/{tid}/submit",
                                          json={"answers": {str(qid): safe}, "briefed_by": 2}).status_code, 400)
        fail = self.client.post(f"/api/trainings/{tid}/submit",
                                json={"answers": {str(qid): safe + 1}, "briefed_by": 1})
        self.assertFalse(fail.json["passed"])
        self.assertIsNone(self.con.execute("SELECT briefed_by FROM training_progress"
                                           " WHERE user_id=2 AND training_id=?", (tid,)).fetchone()["briefed_by"])
        passed = self.client.post(f"/api/trainings/{tid}/submit",
                                  json={"answers": {str(qid): safe}, "briefed_by": 1})
        self.assertTrue(passed.json["passed"], passed.json)
        self.assertEqual(self.con.execute("SELECT briefed_by FROM training_progress"
                                          " WHERE user_id=2 AND training_id=?", (tid,)).fetchone()["briefed_by"], 1)
        self.assertEqual(self.client.get(f"/api/trainings/{tid}").json["briefed_by_name"], "Admin")
        self.sign_in(1)
        results = self.client.get(f"/api/admin/trainings/{tid}/results").json["results"]
        self.assertEqual(results[0]["briefed_by_name"], "Admin")

    def test_no_bundled_catalog(self):
        self.assertFalse((Path(training.__file__).parent / "trainings").exists())
        self.assertFalse(hasattr(training, "seed_catalog"))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM trainings").fetchone()[0], 0)

    def test_import_preview_then_import_and_reject_duplicate(self):
        payload = self.sample_payload()
        preview = self.import_file(payload, preview=True, type_id=self.type_id)
        self.assertEqual(preview.status_code, 200, preview.json)
        self.assertTrue(preview.json["preview"])
        self.assertEqual(preview.json["action"], "create")
        self.assertEqual(preview.json["question_count"], 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM trainings").fetchone()[0], 0)
        created = self.import_file(payload, type_id=self.type_id)
        self.assertEqual(created.status_code, 201, created.json)
        tid = created.json["id"]
        quiz = self.client.get(f"/api/trainings/{tid}").json
        self.assertEqual(len(quiz["questions"]), 1)
        self.assertTrue(quiz["questions"][0]["image"])
        duplicate = self.import_file(payload, type_id=self.type_id)
        self.assertEqual(duplicate.status_code, 409, duplicate.json)
        admin_q = self.client.get(f"/api/trainings/{tid}").json["questions"][0]
        qid = admin_q["id"]
        safe = next(c["id"] for c in admin_q["choices"] if c["correct"])
        self.sign_in(2)
        self.assertTrue(self.client.post(f"/api/trainings/{tid}/submit",
                                         json={"answers": {str(qid): safe}}).json["passed"])
        self.sign_in(1)
        replaced = self.import_file(self.sample_payload(description="Replaced"), type_id=self.type_id, replace=True)
        self.assertEqual(replaced.status_code, 200, replaced.json)
        self.assertEqual(replaced.json["action"], "replace")
        self.assertEqual(replaced.json["id"], tid)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM trainings").fetchone()[0], 1)
        self.assertEqual(self.client.get(f"/api/trainings/{tid}").json["description"], "Replaced")
        progress = self.con.execute("SELECT passed_at FROM training_progress WHERE user_id=2 AND training_id=?",
                                    (tid,)).fetchone()
        self.assertIsNotNone(progress["passed_at"])

    def test_failed_import_removes_written_images(self):
        question = self.sample_payload()["questions"][0]
        payload = self.sample_payload(questions=[question, {**question, "image": "two.png"}])
        real, calls = training.check_image_bytes, []

        def fail_on_last_write(name, data):
            calls.append(name)
            if len(calls) == 4:  # two checks while parsing, then the second write
                raise training.TrainingError("disk full")
            return real(name, data)

        with patch.object(training, "check_image_bytes", fail_on_last_write):
            r = self.import_file(payload, images={"plot.png": PNG, "two.png": PNG}, type_id=self.type_id)
        self.assertEqual(r.status_code, 400, r.json)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM trainings").fetchone()[0], 0)
        self.assertEqual(list(Path(self.app.config["TRAINING_IMAGE_DIR"]).iterdir()), [])

    def test_deleting_a_question_cannot_leave_pass_score_unreachable(self):
        tid = self.add_training(pass_score=2)
        self.add_question(tid)
        last = self.add_question(tid)
        r = self.client.delete(f"/api/admin/trainings/{tid}/questions/{last}")
        self.assertEqual(r.status_code, 400, r.json)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM training_questions WHERE training_id=?",
                                          (tid,)).fetchone()[0], 2)

    def test_import_rejects_bad_questions_and_evidence_types(self):
        bad = self.import_file(self.sample_payload(questions=[{
            "prompt": "Broken", "kind": "single",
            "choices": [{"label": "A", "correct": True}, {"label": "B", "correct": True}]}]))
        self.assertEqual(bad.status_code, 400)
        self.assertIn("exactly one correct", bad.json["error"])
        evidence = self.client.post("/api/qualification-types", json={
            "name": "Licence", "requires_evidence": True, "requires_start": False, "requires_expiry": False})
        self.assertEqual(evidence.status_code, 200, evidence.json)
        types = self.client.get("/api/trainings").json["types"]
        self.assertTrue(all(t["id"] != evidence.json["id"] for t in types))
        blocked = self.import_file(self.sample_payload(valid_days=None), type_id=evidence.json["id"])
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("evidence", blocked.json["error"].lower())
        named = self.import_file({**self.sample_payload(), "qualification": "Briefing"})
        self.assertEqual(named.status_code, 400)
        self.assertIn("qualification", named.json["error"].lower())
        too_high = self.import_file(self.sample_payload(pass_score=99))
        self.assertEqual(too_high.status_code, 400)
        self.assertIn("pass score", too_high.json["error"].lower())

    def test_export_round_trip(self):
        tid = self.add_training()
        qid = self.add_question(tid)
        self.client.post(f"/api/admin/trainings/{tid}/questions/{qid}/image",
                         data={"image": (io.BytesIO(PNG), "plot.png")})
        exported = self.client.get(f"/api/admin/trainings/{tid}/export")
        self.assertEqual(exported.status_code, 200)
        self.assertIn("zip", exported.content_type)
        with zipfile.ZipFile(io.BytesIO(exported.data)) as zf:
            json_name = next(n for n in zf.namelist() if n.endswith(".json"))
            payload = json.loads(zf.read(json_name))
            images = {n: zf.read(n) for n in zf.namelist() if n != json_name}
        payload["name"] = "Round trip"
        again = self.import_file(payload, images=images, type_id=self.type_id)
        self.assertEqual(again.status_code, 201, again.json)

    def test_retake_after_expiry_and_admin_reset(self):
        tid = self.add_training(valid_days=1)
        qid = self.add_question(tid)
        quiz = self.client.get(f"/api/trainings/{tid}").json
        safe = next(c["id"] for c in quiz["questions"][0]["choices"] if c["correct"])
        self.sign_in(2)
        passed = self.client.post(f"/api/trainings/{tid}/submit", json={"answers": {str(qid): safe}})
        self.assertTrue(passed.json["passed"], passed.json)
        self.assertEqual(self.client.post(f"/api/trainings/{tid}/submit",
                                          json={"answers": {str(qid): safe}}).status_code, 409)
        yesterday = (datetime.datetime.now(datetime.timezone.utc).date()
                     - datetime.timedelta(days=1)).isoformat()
        self.con.execute("UPDATE qualification_submissions SET valid_until=? WHERE id=?",
                         (yesterday, passed.json["qualification_id"]))
        self.con.commit()
        again = self.client.get(f"/api/trainings/{tid}").json
        self.assertIsNone(again["passed_at"])
        self.assertTrue(again["expired_pass"])
        self.assertEqual(len(again["questions"]), 1)
        renewed = self.client.post(f"/api/trainings/{tid}/submit", json={"answers": {str(qid): safe}})
        self.assertTrue(renewed.json["passed"], renewed.json)
        self.sign_in(1)
        reset = self.client.delete(f"/api/admin/trainings/{tid}/progress/2")
        self.assertEqual(reset.status_code, 200, reset.json)
        status = self.con.execute("SELECT status FROM qualification_submissions WHERE id=?",
                                  (renewed.json["qualification_id"],)).fetchone()["status"]
        self.assertEqual(status, "withdrawn")
        audit = self.con.execute("SELECT subject_id, actor_id, action FROM qualification_audit"
                                 " WHERE action='training-reset' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(audit["subject_id"], 2)
        self.assertEqual(audit["actor_id"], 1)
        self.sign_in(2)
        self.assertIsNone(self.client.get(f"/api/trainings/{tid}").json["passed_at"])
        self.assertTrue(self.client.post(f"/api/trainings/{tid}/submit",
                                         json={"answers": {str(qid): safe}}).json["passed"])

    def test_expiry_type_needs_valid_days(self):
        r = self.client.post("/api/admin/trainings", json={"name": "No days", "type_id": self.type_id})
        self.assertEqual(r.status_code, 400)
        self.assertIn("validity days", r.json["error"].lower())

    def test_pass_needs_approval_then_reject_allows_retake(self):
        self.assertEqual(self.client.post("/api/admin/trainings", json={
            "name": "Needs review", "needs_approval": True}).status_code, 400)
        tid = self.add_training(needs_approval=True, ask_briefed_by=True, briefers=[1])
        qid = self.add_question(tid)
        quiz = self.client.get(f"/api/trainings/{tid}").json
        safe = next(c["id"] for c in quiz["questions"][0]["choices"] if c["correct"])
        self.sign_in(2)
        passed = self.client.post(f"/api/trainings/{tid}/submit",
                                  json={"answers": {str(qid): safe}, "briefed_by": 1})
        self.assertEqual(passed.status_code, 200, passed.json)
        self.assertTrue(passed.json["passed"])
        self.assertTrue(passed.json["awaiting_approval"])
        sid = passed.json["qualification_id"]
        row = self.con.execute("SELECT status, filename FROM qualification_submissions WHERE id=?",
                               (sid,)).fetchone()
        self.assertEqual(row["status"], "pending")
        self.assertIn("Training passed", row["filename"])
        self.assertIn("briefed by", row["filename"])
        detail = self.client.get(f"/api/trainings/{tid}").json
        self.assertTrue(detail["awaiting_approval"])
        self.assertIsNone(detail["passed_at"])
        self.assertEqual(detail["questions"], [])
        self.assertEqual(self.client.post(f"/api/trainings/{tid}/submit",
                                          json={"answers": {str(qid): safe}, "briefed_by": 1}).status_code, 409)
        self.sign_in(1)
        pending = self.client.get("/api/admin/qualification-pending").json["submissions"]
        self.assertTrue(any(s["id"] == sid and s["evidence_label"] for s in pending))
        reject = self.client.post(f"/api/qualification-submissions/{sid}/review", json={"action": "reject"})
        self.assertEqual(reject.status_code, 200, reject.json)
        self.sign_in(2)
        again = self.client.get(f"/api/trainings/{tid}").json
        self.assertFalse(again["awaiting_approval"])
        self.assertEqual(len(again["questions"]), 1)
        approved = self.client.post(f"/api/trainings/{tid}/submit",
                                    json={"answers": {str(qid): safe}, "briefed_by": 1})
        self.assertTrue(approved.json["passed"], approved.json)
        self.assertTrue(approved.json["awaiting_approval"])
        self.sign_in(1)
        ok = self.client.post(
            f"/api/qualification-submissions/{approved.json['qualification_id']}/review",
            json={"action": "approve"})
        self.assertEqual(ok.status_code, 200, ok.json)
        self.sign_in(2)
        done = self.client.get(f"/api/trainings/{tid}").json
        self.assertTrue(done["passed_at"])
        self.assertFalse(done["awaiting_approval"])


class ScoreTests(unittest.TestCase):
    def test_answer_correct_helpers(self):
        choices = [{"id": 1, "label": "A", "correct": 1, "sort_order": 0},
                   {"id": 2, "label": "B", "correct": 0, "sort_order": 1},
                   {"id": 3, "label": "C", "correct": 1, "sort_order": 2}]
        self.assertTrue(training.answer_correct("single", choices, 1))
        self.assertFalse(training.answer_correct("single", choices, 2))
        self.assertTrue(training.answer_correct("multi", choices, [1, 3]))
        self.assertFalse(training.answer_correct("multi", choices, [1]))
        self.assertTrue(training.answer_correct("ranking", choices, [1, 2, 3]))
        self.assertFalse(training.answer_correct("ranking", choices, [3, 2, 1]))
        earned, total = training.score_answers(
            [{"id": 10, "kind": "single", "points": 2}], {10: choices}, {10: 1})
        self.assertEqual((earned, total), (2, 2))
