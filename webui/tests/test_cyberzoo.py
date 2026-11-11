import datetime
import os
from unittest.mock import patch

from .test_historical import AppTests


class CyberzooTests(AppTests):
    def setUp(self):
        super().setUp()
        settings = patch.dict(os.environ, LOGDB_CYBERZOO_QUALIFICATION="Cyberzoo Safety Briefing")
        settings.start()
        self.addCleanup(settings.stop)
        self.con.execute("INSERT INTO qualification_types(id,name,requires_evidence) VALUES (7,'Cyberzoo Safety Briefing',0)")
        self.con.execute("INSERT INTO trainings(id,name,type_id) VALUES (3,'CSB',7)")
        for uid in (2, 3):  # Pilot and Viewer hold the briefing; Outsider (4) does not
            self.con.execute("INSERT INTO qualification_submissions(user_id,type_id,status,submitted_at)"
                             " VALUES (?,7,'approved','now')", (uid,))
        self.con.commit()

    def book(self, start="2030-01-07T10:00", end="2030-01-07T12:00", **changes):
        return self.client.post("/api/cyberzoo/bookings", json={
            "description": "Hover test", "start": start, "end": end, **changes})

    def week(self):
        return self.client.get("/api/cyberzoo/bookings?from=2030-01-07&to=2030-01-13")

    def test_without_briefing_agenda_is_locked_with_training_link(self):
        self.sign_in(4)
        r = self.week()
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json["training_id"], 3)
        self.assertEqual(self.book().status_code, 403)

    def test_expired_briefing_does_not_count(self):
        past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        self.con.execute("UPDATE qualification_submissions SET valid_until=? WHERE user_id=2", (past,))
        self.assertEqual(self.week().status_code, 403)

    def test_overlap_only_between_bookings_that_allow_others(self):
        self.assertEqual(self.book().status_code, 201)
        self.sign_in(3)
        self.assertEqual(self.book(start="2030-01-07T11:00", end="2030-01-07T13:00").status_code, 201)
        self.assertEqual(self.book(start="2030-01-07T11:00", others_allowed=False).status_code, 409)
        self.assertEqual(self.book(start="2030-01-07T11:00", demo=True).status_code, 409)
        self.assertEqual(self.book(start="2030-01-07T12:00", end="2030-01-07T14:00", demo=True).status_code, 409)
        self.assertEqual(self.book(start="2030-01-07T13:00", end="2030-01-07T14:00", demo=True).status_code, 201)
        r = self.week()
        self.assertEqual(r.status_code, 200)
        self.assertEqual([b["demo"] for b in r.json["bookings"]], [False, False, True])

    def test_only_booker_and_admin_can_change(self):
        bid = self.book().json["id"]
        self.sign_in(3)
        body = {"description": "Mine now", "start": "2030-01-07T10:00", "end": "2030-01-07T11:00"}
        self.assertEqual(self.client.put(f"/api/cyberzoo/bookings/{bid}", json=body).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/cyberzoo/bookings/{bid}").status_code, 403)
        self.assertFalse(self.week().json["bookings"][0]["can_edit"])
        self.sign_in(1)  # admin without the briefing: may view and change, not book
        self.assertEqual(self.week().status_code, 200)
        self.assertEqual(self.book(start="2030-01-08T10:00", end="2030-01-08T11:00").status_code, 403)
        self.assertEqual(self.client.put(f"/api/cyberzoo/bookings/{bid}", json=body).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/cyberzoo/bookings/{bid}").status_code, 200)

    def test_rejects_end_before_start(self):
        r = self.book(start="2030-01-07T12:00", end="2030-01-07T10:00")
        self.assertEqual(r.status_code, 400)

    def test_hidden_when_not_configured(self):
        with patch.dict(os.environ, LOGDB_CYBERZOO_QUALIFICATION=""):
            self.assertEqual(self.week().status_code, 404)
            self.assertFalse(self.client.get("/api/me").json["cyberzoo"])
