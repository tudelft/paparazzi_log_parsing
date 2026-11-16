import hashlib
import io
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from .test_historical import AppTests

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


class SafetyTests(AppTests):
    """Safety page: reports for everyone, safety board and safety promotion meetings for admins.
    Users: 1 Admin, 2 Pilot, 3 Viewer, 4 Outsider."""

    def setUp(self):
        super().setUp()
        settings = patch.dict(os.environ, LOGDB_SAFETY="1")
        settings.start()
        self.addCleanup(settings.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        old = self.app.config["SAFETY_DIR"]
        self.app.config["SAFETY_DIR"] = Path(folder.name) / "safety"
        self.addCleanup(self.app.config.__setitem__, "SAFETY_DIR", old)
        self.sign_in(2)

    def report(self, **fields):
        data = {"kind": "incident", "location": "Field", "occurred_at": "2026-11-10T14:05",
                "description": "Lost link during hover", **fields}
        return self.client.post("/api/safety/reports", data=data, content_type="multipart/form-data")

    def test_off_by_default(self):
        with patch.dict(os.environ, LOGDB_SAFETY="0"):
            self.assertEqual(self.client.get("/safety").status_code, 404)
            self.assertEqual(self.client.get("/api/safety").status_code, 404)
            self.assertFalse(self.client.get("/api/me").json["safety"])
        self.assertTrue(self.client.get("/api/me").json["safety"])
        r = self.client.get("/safety")  # the Operations Manual links this path without a query string
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"<title>Safety</title>", r.data)
        r.close()

    def test_report_is_private_to_reporter_and_admins(self):
        r = self.report(pic="Pilot", rpas="Quad 1", conditions="Gusty", flight_phase="Hover", leave_name_out="1",
                        sketch=(io.BytesIO(PNG), "sketch.png"))
        self.assertEqual(r.status_code, 201, r.json)
        rid = r.json["id"]
        mine = self.client.get("/api/safety/reports").json["reports"]
        self.assertEqual([(x["id"], x["status"], x["leave_name_out"], x["sketch"]) for x in mine],
                         [(rid, "new", True, "sketch.png")])
        self.assertNotIn("reporter", mine[0])  # admin-only fields
        self.assertNotIn("admin_note", mine[0])
        sketch = self.client.get(f"/api/safety/reports/{rid}/sketch")
        self.assertEqual((sketch.status_code, sketch.mimetype, sketch.data), (200, "image/png", PNG))
        sketch.close()
        self.sign_in(3)
        self.assertEqual(self.client.get("/api/safety/reports").json["reports"], [])
        self.assertEqual(self.client.get(f"/api/safety/reports/{rid}/sketch").status_code, 404)
        self.assertEqual(self.client.put(f"/api/admin/safety/reports/{rid}", json={"status": "closed"}).status_code, 403)
        self.sign_in(1)
        listed = self.client.get("/api/safety/reports").json["reports"]
        self.assertEqual((listed[0]["reporter"], listed[0]["rpas"]), ("Pilot", "Quad 1"))
        r = self.client.put(f"/api/admin/safety/reports/{rid}",
                            json={"status": "investigating", "reference": "https://repo.example/issue/1", "admin_note": "Call PIC"})
        self.assertEqual(r.status_code, 200, r.json)
        self.sign_in(2)
        self.assertEqual(self.client.get("/api/safety/reports").json["reports"][0]["status"], "investigating")

    def test_report_notice_can_leave_the_name_out(self):
        self.con.execute("UPDATE users SET email='admin@example.org', is_active=1 WHERE id=1")
        self.con.commit()
        with patch.dict(os.environ, {"LOGDB_SMTP_HOST": "relay.example.org"}):
            self.assertEqual(self.report(leave_name_out="1").status_code, 201)
            self.assertEqual(self.report().status_code, 201)
        bodies = [r["body"] for r in self.con.execute("SELECT body FROM mail_outbox WHERE user_id=1 ORDER BY id")]
        self.assertEqual(len(bodies), 2)
        self.assertIn("a reporter who asked to leave their name out", bodies[0])
        kinds = [r["kind"] for r in self.con.execute("SELECT kind FROM mail_outbox WHERE user_id=1")]
        self.assertEqual(kinds, ["safety", "safety"])  # its own kind: muting the other admin notices keeps these
        self.assertNotIn("Pilot", bodies[0])
        self.assertIn("by Pilot", bodies[1])

    def test_report_validation(self):
        for fields, error in (({"kind": "rumour"}, "Type must be one of"), ({"location": ""}, "Location is required"),
                              ({"description": " "}, "Description is required"),
                              ({"occurred_at": "yesterday"}, "Date and time must be"),
                              ({"occurred_at": "2999-01-01T10:00"}, "Date and time cannot be in the future"),
                              ({"location": "Field\nBcc: x@example.org"}, "Location cannot contain control characters"),
                              ({"pic": "Pilot\u2028Two"}, "Pilot in command cannot contain"),
                              ({"sketch": (io.BytesIO(b"not an image"), "sketch.png")}, "matching file content"),
                              ({"sketch": (io.BytesIO(PNG), "sketch.exe")}, "PDF, JPEG or PNG")):
            r = self.report(**fields)
            self.assertEqual(r.status_code, 400, fields)
            self.assertIn(error, r.json["error"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM safety_reports").fetchone()[0], 0)
        self.assertEqual(list(Path(self.app.config["SAFETY_DIR"]).glob("*")) if Path(self.app.config["SAFETY_DIR"]).exists()
                         else [], [])

    def test_settings_and_meetings_are_for_admins(self):
        self.assertNotIn("board_link", self.client.get("/api/safety").json)
        self.assertEqual(self.client.get("/api/admin/safety/meetings").status_code, 403)
        self.assertEqual(self.client.put("/api/admin/safety/settings", json={"reporting_text": "x"}).status_code, 403)
        self.sign_in(1)
        r = self.client.put("/api/admin/safety/settings", json={"board_link": "ftp://nope"})
        self.assertEqual(r.status_code, 400)
        r = self.client.put("/api/admin/safety/settings", json={"reporting_text": "Call the safety manager.",
                                                                "board_link": "https://docs.example/safety-board"})
        self.assertEqual(r.status_code, 200, r.json)
        d = self.client.get("/api/safety").json
        self.assertEqual((d["reporting_text"], d["board_link"], d["promotion_text"]),
                         ("Call the safety manager.", "https://docs.example/safety-board", ""))
        r = self.client.post("/api/admin/safety/meetings", json={
            "kind": "board", "held_on": "2026-12-02", "start_time": "10:00", "title": "Yearly review",
            "attendees": [{"user_id": 2}, {"name": "Guest Expert"}, {"user_id": 2}, {"name": "guest expert"}]})
        self.assertEqual(r.status_code, 201, r.json)
        mid = r.json["id"]
        listing = self.client.get("/api/admin/safety/meetings").json
        self.assertEqual([(m["id"], m["kind"], m["start_time"]) for m in listing["meetings"]], [(mid, "board", "10:00")])
        self.assertEqual(listing["meetings"][0]["attendees"],
                         [{"user_id": 2, "name": "Pilot"}, {"user_id": None, "name": "Guest Expert"}])
        self.assertIn({"id": 3, "name": "Viewer"}, listing["people"])
        r = self.client.put(f"/api/admin/safety/meetings/{mid}", json={"kind": "promotion", "attendees": [{"user_id": 3}]})
        self.assertEqual(r.status_code, 200, r.json)
        m = self.client.get("/api/admin/safety/meetings").json["meetings"][0]
        self.assertEqual((m["kind"], m["title"], [a["name"] for a in m["attendees"]]), ("promotion", "Yearly review", ["Viewer"]))
        for body in ({"kind": "party", "held_on": "2026-12-02"}, {"kind": "board", "held_on": "soon"},
                     {"kind": "board", "held_on": "2026-12-02", "attendees": [{"user_id": 99}]}):
            self.assertEqual(self.client.post("/api/admin/safety/meetings", json=body).status_code, 400, body)
        self.assertEqual(self.client.delete(f"/api/admin/safety/meetings/{mid}").status_code, 200)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM safety_meeting_attendees").fetchone()[0], 0)
        self.assertEqual(self.client.delete(f"/api/admin/safety/meetings/{mid}").status_code, 404)

    def test_sketch_with_a_non_latin_1_name(self):
        name = "Screenshot 2026-11-14 at 10.00.00\u202fAM \u2019wing\u2019 \U0001F681.png"
        r = self.report(sketch=(io.BytesIO(PNG), name))
        self.assertEqual(r.status_code, 201, r.json)
        rid = r.json["id"]
        self.assertEqual(self.con.execute("SELECT sketch_name FROM safety_reports").fetchone()[0], name)
        # a quote breaks a plain header too (the test client cannot upload one, browsers send it as %22)
        self.con.execute("UPDATE safety_reports SET sketch_name=?", (name.replace("wing", '"wing"'),))
        self.con.commit()
        r = self.client.get(f"/api/safety/reports/{rid}/sketch")
        self.assertEqual(r.status_code, 200)
        header = r.headers["Content-Disposition"]
        header.encode("latin-1")  # gunicorn sends headers as latin-1: this must not raise
        self.assertTrue(header.startswith("inline; filename="), header)
        self.assertIn("filename*=UTF-8''Screenshot%202026-11-14%20at%2010.00.00%E2%80%AFAM", header)
        self.assertNotIn("\n", header)
        r.close()

    def test_reporter_name_survives_the_account(self):
        rid = self.report().json["id"]
        self.con.execute("UPDATE users SET display_name='Pilot Renamed' WHERE id=2")
        self.con.commit()
        self.sign_in(1)
        report = self.client.get("/api/safety/reports").json["reports"][0]
        self.assertEqual((report["reporter"], report["reporter_deleted"]), ("Pilot Renamed", False))  # current name
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.execute("DELETE FROM users WHERE id=2")
        self.con.commit()
        report = self.client.get("/api/safety/reports").json["reports"][0]
        self.assertEqual((report["id"], report["reporter"], report["reporter_deleted"]), (rid, "Pilot", True))

    def test_status_change_mails_the_reporter_and_counts_new_reports(self):
        self.con.execute("UPDATE users SET email='pilot@example.org', is_active=1 WHERE id=2")
        self.con.commit()
        rid = self.report().json["id"]
        self.report()
        self.sign_in(1)
        self.assertEqual(self.client.get("/api/me").json["safety_new"], 2)
        with patch.dict(os.environ, {"LOGDB_SMTP_HOST": "relay.example.org"}):
            self.client.put(f"/api/admin/safety/reports/{rid}", json={"admin_note": "only a note"})  # no status change
            self.client.put(f"/api/admin/safety/reports/{rid}", json={"status": "investigating"})
        mails = [(r["kind"], r["subject"]) for r in self.con.execute("SELECT kind, subject FROM mail_outbox WHERE user_id=2")]
        self.assertEqual(mails, [("safety_status", "Your safety report: investigating")])
        self.assertEqual(self.client.get("/api/me").json["safety_new"], 1)
        self.sign_in(2)
        self.assertEqual(self.client.get("/api/me").json["safety_new"], 0)  # administrators only
        kinds = {k["kind"] for k in self.client.get("/api/account/email").json["kinds"]}
        self.assertIn("safety_status", kinds)
        self.assertNotIn("safety", kinds)  # new reports go to administrators

    def test_config_check_warns_when_safety_is_off(self):
        import config
        with patch.dict(os.environ, LOGDB_SAFETY="0"), patch("builtins.print") as printed:
            config.check()
        lines = " ".join(str(c.args[0]) for c in printed.call_args_list if c.args)
        self.assertIn("LOGDB_SAFETY is off: /safety, linked from the Operations Manual, returns 404", lines)

    def test_api_keys_cannot_use_safety(self):
        token = "logdb_" + "b" * 30
        self.con.execute("INSERT INTO api_keys(user_id,name,key_hash,prefix,created_at) VALUES (1,'test',?,?,'now')",
                         (hashlib.sha256(token.encode()).hexdigest(), "test"))
        self.con.commit()
        client = self.app.test_client()
        for url in ("/api/safety", "/api/safety/reports", "/api/admin/safety/meetings"):
            self.assertEqual(client.get(url, headers={"Authorization": "Bearer " + token}).status_code, 403, url)
