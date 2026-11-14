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
        r = self.report(pic="Pilot", rpas="Quad 1", conditions="Gusty", flight_phase="Hover", confidential="1",
                        sketch=(io.BytesIO(PNG), "sketch.png"))
        self.assertEqual(r.status_code, 201, r.json)
        rid = r.json["id"]
        mine = self.client.get("/api/safety/reports").json["reports"]
        self.assertEqual([(x["id"], x["status"], x["confidential"], x["sketch"]) for x in mine],
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

    def test_report_notice_leaves_out_a_confidential_reporter(self):
        self.con.execute("UPDATE users SET email='admin@example.org', is_active=1 WHERE id=1")
        self.con.commit()
        with patch.dict(os.environ, {"LOGDB_SMTP_HOST": "relay.example.org"}):
            self.assertEqual(self.report(confidential="1").status_code, 201)
            self.assertEqual(self.report().status_code, 201)
        bodies = [r["body"] for r in self.con.execute("SELECT body FROM mail_outbox WHERE user_id=1 ORDER BY id")]
        self.assertEqual(len(bodies), 2)
        self.assertIn("a confidential reporter", bodies[0])
        self.assertNotIn("Pilot", bodies[0])
        self.assertIn("by Pilot", bodies[1])

    def test_report_validation(self):
        for fields, error in (({"kind": "rumour"}, "Type must be one of"), ({"location": ""}, "Location is required"),
                              ({"description": " "}, "Description is required"),
                              ({"occurred_at": "yesterday"}, "Date and time must be"),
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

    def test_api_keys_cannot_use_safety(self):
        token = "logdb_" + "b" * 30
        self.con.execute("INSERT INTO api_keys(user_id,name,key_hash,prefix,created_at) VALUES (1,'test',?,?,'now')",
                         (hashlib.sha256(token.encode()).hexdigest(), "test"))
        self.con.commit()
        client = self.app.test_client()
        for url in ("/api/safety", "/api/safety/reports", "/api/admin/safety/meetings"):
            self.assertEqual(client.get(url, headers={"Authorization": "Bearer " + token}).status_code, 403, url)
