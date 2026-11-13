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

import mail
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
        row = self.con.execute("SELECT status, valid_from, valid_until, note, filename FROM qualification_submissions"
                               " WHERE id=?", (passed.json["qualification_id"],)).fetchone()
        today = datetime.datetime.now(datetime.timezone.utc).date()
        self.assertEqual(row["status"], "approved")
        self.assertEqual(row["valid_from"], today.isoformat())
        self.assertEqual(row["valid_until"], (today + datetime.timedelta(days=365)).isoformat())
        self.assertIsNone(row["filename"])
        self.assertIn('Training "Safety" passed', row["note"])
        self.assertIn(today.isoformat(), row["note"])
        quals = self.client.get("/api/users/2/qualifications").json["items"]
        match = next(q for q in quals if q["id"] == passed.json["qualification_id"])
        self.assertEqual(match["training_id"], tid)
        self.assertEqual(match["training_user_id"], 2)
        self.assertIn('Training "Safety" passed', match["note"])
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

    def correct_answers(self, tid, questions):
        """Right answers for the questions a taker was given, from the admin view of the training."""
        with self.client.session_transaction() as session:
            uid = session["uid"]
        self.sign_in(1)
        keys = {q["id"]: q for q in self.client.get(f"/api/trainings/{tid}").json["questions"]}
        self.sign_in(uid)
        return {str(q["id"]): next(c["id"] for c in keys[q["id"]]["choices"] if c["correct"]) for q in questions}

    def test_subset_and_shuffle_stay_fixed_during_an_attempt(self):
        tid = self.add_training(pass_score=8)  # 8 of 10 points
        for i in range(10):
            self.add_question(tid, prompt=f"Q{i}")
        settings = self.client.get(f"/api/trainings/{tid}").json
        body = {k: settings[k] for k in ("name", "type_id", "valid_days", "pass_score")}
        self.assertEqual(self.client.put(f"/api/admin/trainings/{tid}", json={
            **body, "question_count": 5, "shuffle_questions": True}).status_code, 200)
        self.sign_in(2)
        first = self.client.get(f"/api/trainings/{tid}").json
        self.assertEqual(len(first["questions"]), 5)
        self.assertEqual((first["needed"], first["asked_points"]), (4, 5))  # 8/10 scaled to the 5 points drawn
        again = self.client.get(f"/api/trainings/{tid}").json  # a reload keeps the attempt
        self.assertEqual([(q["id"], [c["id"] for c in q["choices"]]) for q in again["questions"]],
                         [(q["id"], [c["id"] for c in q["choices"]]) for q in first["questions"]])
        right = self.correct_answers(tid, first["questions"])
        other = next(str(q["id"]) for q in self.client.get(f"/api/trainings/{tid}").json["questions"])
        not_drawn = next(i for i in range(1, 100) if str(i) not in right and self.con.execute(
            "SELECT 1 FROM training_questions WHERE id=? AND training_id=?", (i, tid)).fetchone())
        self.assertEqual(self.client.post(f"/api/trainings/{tid}/submit",
                                          json={"answers": {str(not_drawn): 1}}).status_code, 400)
        wrong = dict(right)
        wrong[other] = wrong[other] + 1  # one wrong of five: 4 >= 4 still passes
        passed = self.client.post(f"/api/trainings/{tid}/submit", json={"answers": wrong}).json
        self.assertEqual((passed["passed"], passed["score"], passed["total"], passed["needed"]), (True, 4, 5, 4))
        self.assertIsNone(self.con.execute("SELECT draw_json FROM training_progress WHERE user_id=2").fetchone()[0])

    def test_practice_after_a_pass_changes_nothing(self):
        tid = self.add_training()
        self.add_question(tid)
        self.sign_in(2)
        quiz = self.client.get(f"/api/trainings/{tid}").json
        right = self.correct_answers(tid, quiz["questions"])
        self.assertTrue(self.client.post(f"/api/trainings/{tid}/submit", json={"answers": right}).json["passed"])
        self.assertEqual(self.client.get(f"/api/trainings/{tid}").json["questions"], [])
        practice = self.client.get(f"/api/trainings/{tid}?practice=1").json
        self.assertTrue(practice["practice"])
        self.assertEqual(len(practice["questions"]), 1)
        before = dict(self.con.execute("SELECT * FROM training_progress WHERE user_id=2").fetchone())
        wrong = {k: v + 1 for k, v in right.items()}
        result = self.client.post(f"/api/trainings/{tid}/submit", json={"answers": wrong, "practice": True}).json
        self.assertEqual((result["practice"], result["passed"], result["score"]), (True, False, 0))
        self.assertEqual(dict(self.con.execute("SELECT * FROM training_progress WHERE user_id=2").fetchone()), before)

    def test_question_order(self):
        tid = self.add_training()
        a, b, c = (self.add_question(tid, prompt=p) for p in ("A", "B", "C"))
        url = f"/api/admin/trainings/{tid}/question-order"
        self.assertEqual(self.client.put(url, json={"question_ids": [c, a, b]}).status_code, 200)
        self.assertEqual([q["prompt"] for q in self.client.get(f"/api/trainings/{tid}").json["questions"]], ["C", "A", "B"])
        for bad in ([c, a], [c, a, a], [c, a, b, 999], ["x"]):  # every question exactly once
            self.assertEqual(self.client.put(url, json={"question_ids": bad}).status_code, 400)
        self.sign_in(2)
        self.assertEqual(self.client.put(url, json={"question_ids": [a, b, c]}).status_code, 403)

    def test_deleting_a_question_cannot_leave_pass_score_unreachable(self):
        tid = self.add_training(pass_score=2)
        self.add_question(tid)
        last = self.add_question(tid)
        r = self.client.delete(f"/api/admin/trainings/{tid}/questions/{last}")
        self.assertEqual(r.status_code, 400, r.json)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM training_questions WHERE training_id=?",
                                          (tid,)).fetchone()[0], 2)

    def test_reading_material_must_be_confirmed_before_the_test(self):
        pdf = b"%PDF-1.4 mock reading"
        payload = self.sample_payload(reading="Read the rules.", reading_file="rules.pdf")
        preview = self.import_file(payload, images={"plot.png": PNG, "rules.pdf": pdf}, preview=True)
        self.assertEqual((preview.json["reading_chars"], preview.json["reading_file"]), (15, "rules.pdf"))
        tid = self.import_file(payload, images={"plot.png": PNG, "rules.pdf": pdf}).json["id"]
        admin_q = self.client.get(f"/api/trainings/{tid}").json["questions"][0]
        right = {str(admin_q["id"]): next(c["id"] for c in admin_q["choices"] if c["correct"])}
        self.sign_in(2)
        quiz = self.client.get(f"/api/trainings/{tid}").json
        self.assertEqual((quiz["reading"], quiz["reading_file"], quiz["must_read"]), ("Read the rules.", True, True))
        self.assertEqual(self.client.get(f"/api/trainings/{tid}/reading-file").data, pdf)
        self.assertEqual(self.client.post(f"/api/trainings/{tid}/submit", json={"answers": right}).status_code, 409)
        self.assertEqual(self.client.post(f"/api/trainings/{tid}/read").status_code, 200)
        self.assertFalse(self.client.get(f"/api/trainings/{tid}").json["must_read"])
        self.assertTrue(self.client.post(f"/api/trainings/{tid}/submit", json={"answers": right}).json["passed"])
        self.sign_in(1)
        self.assertEqual(self.client.delete(f"/api/admin/trainings/{tid}/progress/2").status_code, 200)
        exported = zipfile.ZipFile(io.BytesIO(self.client.get(f"/api/admin/trainings/{tid}/export").data))
        manifest = json.loads(exported.read(next(n for n in exported.namelist() if n.endswith(".json"))))
        self.assertEqual((manifest["reading"], exported.read(manifest["reading_file"])), ("Read the rules.", pdf))
        self.sign_in(2)
        self.assertTrue(self.client.get(f"/api/trainings/{tid}").json["must_read"])  # a retake reads again
        self.sign_in(1)
        bad = self.import_file(self.sample_payload(name="Other", reading_file="rules.pdf"),
                               images={"plot.png": PNG, "rules.pdf": b"not a pdf"})
        self.assertEqual(bad.status_code, 400, bad.json)
        missing = self.import_file(self.sample_payload(name="Other", reading_file="gone.pdf"))
        self.assertEqual(missing.status_code, 400, missing.json)
        self.assertFalse(self.client.get(f"/api/trainings/{self.add_training(name='Plain')}").json["must_read"])

    def test_reading_pdf_upload_replace_remove_and_admin_on_inactive_training(self):
        tid = self.add_training(reading="Rules.")
        qid = self.add_question(tid)
        url = f"/api/admin/trainings/{tid}/reading-file"
        upload = lambda blob, name="rules.pdf": self.client.post(url, data={"file": (io.BytesIO(blob), name)},
                                                                  content_type="multipart/form-data")
        self.assertEqual(upload(b"not a pdf").status_code, 400)
        self.assertEqual(upload(b"%PDF-1.4 a", "rules.txt").status_code, 400)
        self.assertEqual(upload(b"%PDF-1.4 first").json["reading_name"], "rules.pdf")
        first = self.con.execute("SELECT reading_path FROM trainings WHERE id=?", (tid,)).fetchone()[0]
        self.assertEqual(upload(b"%PDF-1.4 second", "rules-2026.pdf").status_code, 200)
        self.assertFalse((self.app.config["TRAINING_IMAGE_DIR"] / first).exists())  # old file removed
        self.assertEqual(self.client.get(f"/api/trainings/{tid}/reading-file").data, b"%PDF-1.4 second")
        self.sign_in(2)
        self.assertEqual(upload(b"%PDF-1.4 x").status_code, 403)
        self.sign_in(1)
        self.assertEqual(self.client.delete(url).status_code, 200)
        self.assertEqual(self.client.get(f"/api/trainings/{tid}/reading-file").status_code, 404)
        self.assertEqual(os.listdir(self.app.config["TRAINING_IMAGE_DIR"]), [])
        # an admin can try an inactive training end to end
        self.con.execute("UPDATE trainings SET active=0 WHERE id=?", (tid,))
        self.con.commit()
        right = next(c["id"] for c in self.client.get(f"/api/trainings/{tid}").json["questions"][0]["choices"] if c["correct"])
        self.assertEqual(self.client.post(f"/api/trainings/{tid}/read").status_code, 200)
        self.assertTrue(self.client.post(f"/api/trainings/{tid}/submit", json={"answers": {str(qid): right}}).json["passed"])
        self.sign_in(2)
        self.assertEqual(self.client.post(f"/api/trainings/{tid}/read").status_code, 404)

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
        row = self.con.execute("SELECT status, filename, note FROM qualification_submissions WHERE id=?",
                               (sid,)).fetchone()
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["filename"])
        self.assertIn('Training "Safety"', row["note"])
        self.assertIn("passed", row["note"])
        self.assertIn("briefed by", row["note"])
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

    def access_setup(self, **body):
        mail_env = patch.dict(os.environ, {"LOGDB_SMTP_HOST": "relay.example.org", "LOGDB_MAIL_FROM": "logbook@example.org"})
        mail_env.start()
        self.addCleanup(mail_env.stop)
        self.con.execute("UPDATE users SET email=lower(username)||'@example.org'")
        self.con.commit()
        self.assertEqual(self.client.post("/api/admin/trainings", json={
            "name": "Bad", "access_mail_to": "not-an-address", "access_mail_text": "x"}).status_code, 400)
        self.assertEqual(self.client.post("/api/admin/trainings", json={
            "name": "Bad", "access_mail_to": "desk@example.org"}).status_code, 400)
        tid = self.add_training(access_mail_to="desk@example.org; keys@example.org",
                                access_mail_text="{name} ({email}) passed {training} on {date}.", **body)
        qid = self.add_question(tid)
        quiz = self.client.get(f"/api/trainings/{tid}").json
        self.assertEqual(quiz["access_mail_to"], "desk@example.org, keys@example.org")
        safe = next(c["id"] for c in quiz["questions"][0]["choices"] if c["correct"])
        return tid, {"answers": {str(qid): safe}}

    def access_mails(self):
        return self.con.execute("SELECT * FROM mail_outbox WHERE kind='access'").fetchall()

    def test_access_mail_only_after_no(self):
        tid, answers = self.access_setup(access_question="Do you already have access to the CyberZoo?",
                                         access_mail_cc=True)
        self.sign_in(2)
        self.assertEqual(self.client.get(f"/api/trainings/{tid}").json["access_question"],
                         "Do you already have access to the CyberZoo?")
        self.assertEqual(self.client.post(f"/api/trainings/{tid}/submit", json=answers).status_code, 400)
        r = self.client.post(f"/api/trainings/{tid}/submit", json={**answers, "access_answer": "yes"})
        self.assertTrue(r.json["passed"], r.json)
        self.assertEqual(self.access_mails(), [])
        self.sign_in(1)
        self.assertEqual(self.client.delete(f"/api/admin/trainings/{tid}/progress/2").status_code, 200)
        self.sign_in(2)
        r = self.client.post(f"/api/trainings/{tid}/submit", json={**answers, "access_answer": "no"})
        self.assertTrue(r.json["passed"], r.json)
        row, = self.access_mails()
        self.assertEqual((row["to_addr"], row["cc_addr"], row["reply_to"]),
                         ("desk@example.org, keys@example.org", "pic@example.org", "pic@example.org"))
        self.assertTrue(row["subject"].startswith("Access request: "), row["subject"])
        self.assertIn("(pic@example.org) passed Safety on ", row["body"])
        sent = []
        smtp = type("FakeSMTP", (), {"send_message": lambda _, m: sent.append(m), "quit": lambda _: None})()
        mail.send_due(self.con, lambda: smtp)
        msg, = sent
        self.assertEqual((msg["To"], msg["Cc"], msg["Reply-To"]),
                         ("desk@example.org, keys@example.org", "pic@example.org", "pic@example.org"))
        self.assertIn(mail.ACCESS_NOTE, msg.get_body(("plain",)).get_content())

    def test_access_mail_waits_for_approval(self):
        tid, answers = self.access_setup(needs_approval=True)
        self.sign_in(2)
        self.assertIsNone(self.client.get(f"/api/trainings/{tid}").json["access_question"])
        r = self.client.post(f"/api/trainings/{tid}/submit", json=answers)
        self.assertTrue(r.json["awaiting_approval"], r.json)
        self.assertEqual(self.access_mails(), [])
        self.sign_in(1)
        self.assertEqual(self.client.post(f"/api/qualification-submissions/{r.json['qualification_id']}/review",
                                          json={"action": "approve"}).status_code, 200)
        row, = self.access_mails()
        self.assertIsNone(row["cc_addr"])
        self.assertEqual(row["reply_to"], "pic@example.org")


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
