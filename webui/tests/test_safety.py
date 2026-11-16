import hashlib
import io
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from .test_historical import AppTests

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


class SafetyTests(AppTests):
    """Safety page (/safety): danger, incident and accident reports for everyone.
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

    def test_report_is_stored_and_the_sketch_private(self):
        r = self.report(pic="Pilot", rpas="Quad 1", conditions="Gusty", flight_phase="Hover", leave_name_out="1",
                        sketch=(io.BytesIO(PNG), "sketch.png"))
        self.assertEqual(r.status_code, 201, r.json)
        rid = r.json["id"]
        row = self.con.execute("SELECT * FROM safety_reports WHERE id=?", (rid,)).fetchone()
        self.assertEqual((row["kind"], row["pic"], row["rpas"], row["leave_name_out"], row["reporter_id"],
                          row["reporter_name"], row["sketch_name"]),
                         ("incident", "Pilot", "Quad 1", 1, 2, "Pilot", "sketch.png"))
        self.assertEqual(self.client.get("/api/safety/reports").status_code, 405)  # no list of reports
        sketch = self.client.get(f"/api/safety/reports/{rid}/sketch")  # the reporter
        self.assertEqual((sketch.status_code, sketch.mimetype, sketch.data), (200, "image/png", PNG))
        sketch.close()
        self.sign_in(3)
        self.assertEqual(self.client.get(f"/api/safety/reports/{rid}/sketch").status_code, 404)
        self.sign_in(1)  # administrators open it from the link in their mail
        sketch = self.client.get(f"/api/safety/reports/{rid}/sketch")
        self.assertEqual(sketch.status_code, 200)
        sketch.close()

    def test_admins_get_the_whole_report_by_mail(self):
        self.con.execute("UPDATE users SET email='admin@example.org', is_active=1 WHERE id=1")
        self.con.commit()
        with patch.dict(os.environ, {"LOGDB_SMTP_HOST": "relay.example.org"}):
            self.assertEqual(self.report(leave_name_out="1", rpas="Quad 1", sketch=(io.BytesIO(PNG), "s.png")).status_code, 201)
            self.assertEqual(self.report(description="Line one\nLine two").status_code, 201)
        mails = list(self.con.execute("SELECT kind, subject, body FROM mail_outbox WHERE user_id=1 ORDER BY id"))
        self.assertEqual([m["kind"] for m in mails], ["safety", "safety"])  # own kind: muting other notices keeps these
        first, second = mails[0]["body"], mails[1]["body"]
        self.assertEqual(mails[0]["subject"], "Safety report: incident at Field")
        self.assertIn("filed by a reporter who asked to leave their name out", first)
        self.assertNotIn("Pilot", first)
        for line in ("Type: incident", "Date and time: 2026-11-10 14:05", "Location: Field", "Aircraft: Quad 1",
                     "Description:\nLost link during hover", "Sketch or photo: s.png", "/api/safety/reports/"):
            self.assertIn(line, first)
        self.assertIn("filed by Pilot", second)
        self.assertIn("Line one\nLine two", second)
        self.assertNotIn("Sketch", second)

    def test_report_validation(self):
        for fields, error in (({"kind": "rumour"}, "Type must be one of"),
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

    def test_reporting_text_is_set_by_admins(self):
        self.assertEqual(self.client.put("/api/admin/safety/settings", json={"reporting_text": "x"}).status_code, 403)
        self.assertEqual(self.client.get("/api/admin/safety/meetings").status_code, 404)  # board and promotion: removed
        self.sign_in(1)
        import safety
        self.assertEqual(self.client.get("/api/safety").json["reporting_text"], safety.DEFAULT_REPORTING_TEXT)
        r = self.client.put("/api/admin/safety/settings", json={"reporting_text": "Call the safety manager.\nThen the board."})
        self.assertEqual(r.status_code, 200, r.json)
        d = self.client.get("/api/safety").json
        self.assertEqual(d["reporting_text"], "Call the safety manager.\nThen the board.")
        self.assertEqual(set(d), {"reporting_text", "is_admin", "kinds", "max_sketch_bytes", "me"})
        self.assertEqual(self.client.put("/api/admin/safety/settings", json={"reporting_text": ""}).status_code, 200)
        self.assertEqual(self.client.get("/api/safety").json["reporting_text"], safety.DEFAULT_REPORTING_TEXT)

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
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.execute("DELETE FROM users WHERE id=2")
        self.con.commit()
        row = self.con.execute("SELECT reporter_id, reporter_name FROM safety_reports WHERE id=?", (rid,)).fetchone()
        self.assertEqual(tuple(row), (None, "Pilot"))

    def test_config_check_warns_when_safety_is_off(self):
        import config
        with patch.dict(os.environ, LOGDB_SAFETY="0"), patch("builtins.print") as printed:
            config.check()
        lines = " ".join(str(c.args[0]) for c in printed.call_args_list if c.args)
        self.assertIn("LOGDB_SAFETY is off: /safety, linked from the Operations Manual, returns 404", lines)

    def test_config_check_warns_when_reports_reach_nobody(self):
        import config

        def warnings(**settings):
            with patch.dict(os.environ, {"LOGDB_SAFETY": "1", "LOGDB_SMTP_HOST": "", "LOGDB_MAIL_FROM": "",
                                         "LOGDB_MAIL_ADMIN_TO": "", **settings}), patch("builtins.print") as printed:
                config.check()
            return " ".join(str(c.args[0]) for c in printed.call_args_list
                            if c.args and str(c.args[0]).startswith("warning:"))

        self.assertIn("LOGDB_SMTP_HOST is not set: safety reports are stored but nobody is told", warnings())
        mail = {"LOGDB_SMTP_HOST": "relay.example.org", "LOGDB_MAIL_FROM": "logbook@example.org"}
        self.assertIn("LOGDB_SAFETY is on without LOGDB_MAIL_ADMIN_TO", warnings(**mail))
        lines = warnings(**mail, LOGDB_MAIL_ADMIN_TO="safety@example.org")
        self.assertNotIn("safety reports", lines)
        self.assertNotIn("LOGDB_MAIL_ADMIN_TO", lines)
        self.assertNotIn("safety reports", warnings(LOGDB_SAFETY="0"))

    def test_api_keys_cannot_use_safety(self):
        token = "logdb_" + "b" * 30
        self.con.execute("INSERT INTO api_keys(user_id,name,key_hash,prefix,created_at) VALUES (1,'test',?,?,'now')",
                         (hashlib.sha256(token.encode()).hexdigest(), "test"))
        self.con.commit()
        client = self.app.test_client()
        for url in ("/api/safety", "/api/safety/reports/1/sketch"):
            self.assertEqual(client.get(url, headers={"Authorization": "Bearer " + token}).status_code, 403, url)

    def test_only_type_date_and_description_are_required(self):
        self.con.execute("UPDATE users SET email='admin@example.org', is_active=1 WHERE id=1")
        self.con.commit()
        with patch.dict(os.environ, {"LOGDB_SMTP_HOST": "relay.example.org"}):
            r = self.report(location="")
        self.assertEqual(r.status_code, 201, r.json)
        row = self.con.execute("SELECT pic, rpas, location, conditions, flight_phase FROM safety_reports").fetchone()
        self.assertEqual(tuple(row), ("", "", "", "", ""))
        subject, body = self.con.execute("SELECT subject, body FROM mail_outbox WHERE kind='safety'").fetchone()
        self.assertEqual(subject, "Safety report: incident")
        self.assertNotIn("Location:", body)
