import datetime
import os
from unittest.mock import patch

from .test_historical import AppTests


class BookingTests(AppTests):
    """Users: 1 Admin, 2 Pilot and 3 Viewer hold the briefing; 4 Outsider does not (fixtures in AppTests)."""

    def setUp(self):
        super().setUp()
        settings = patch.dict(os.environ, LOGDB_BOOKINGS="1")
        settings.start()
        self.addCleanup(settings.stop)
        self.con.execute("INSERT INTO qualification_types(id,name,requires_evidence) VALUES (7,'Arena safety briefing',0)")
        self.con.execute("INSERT INTO trainings(id,name,type_id) VALUES (3,'Briefing',7)")
        for uid in (1, 2, 3):
            self.con.execute("INSERT INTO qualification_submissions(user_id,type_id,status,submitted_at)"
                             " VALUES (?,7,'approved','now')", (uid,))
        self.con.commit()
        self.sign_in(1)
        self.arena = self.add_item(name="Indoor arena", type_id=7, qualification_for="viewing", flags=[
            {"label": "Demo", "blocks_overlap": True, "colour": "#d29922"},
            {"label": "Others allowed", "blocks_overlap": False, "colour": "#2da44e"}])
        self.room = self.add_item(name="Meeting room")
        flags = {f["label"]: f["id"] for f in self.item(self.arena)["flags"]}
        self.demo, self.shared = flags["Demo"], flags["Others allowed"]
        self.sign_in(2)

    def add_item(self, **body):
        r = self.client.post("/api/admin/booking-things", json=body)
        self.assertEqual(r.status_code, 201, r.json)
        return r.json["id"]

    def week(self):
        return self.client.get("/api/bookings?from=2030-01-07&to=2030-01-13")

    def item(self, item_id):
        return next(t for t in self.week().json["things"] if t["id"] == item_id)

    def book(self, item=None, start="2030-01-07T10:00", end="2030-01-07T12:00", flags=()):
        return self.client.post("/api/bookings", json={"thing_id": item or self.arena, "description": "Hover test",
                                                       "start": start, "end": end, "flags": list(flags)})

    def test_viewing_mode_hides_the_item_and_links_the_training(self):
        self.assertEqual(self.book(self.room, flags=()).status_code, 201)
        self.assertEqual(self.book(flags=[self.shared]).status_code, 201)
        self.sign_in(4)
        r = self.week()
        arena = next(t for t in r.json["things"] if t["id"] == self.arena)
        self.assertFalse(arena["can_view"])
        self.assertEqual(arena["training_id"], 3)
        self.assertEqual([b["thing_id"] for b in r.json["bookings"]], [self.room])
        r = self.book(start="2030-01-08T10:00", end="2030-01-08T11:00")
        self.assertEqual(r.status_code, 403)
        self.assertEqual((r.json["thing_id"], r.json["training_id"]), (self.arena, 3))

    def test_booking_mode_shows_the_agenda_but_needs_the_qualification_to_book(self):
        self.sign_in(1)
        self.client.put(f"/api/admin/booking-things/{self.arena}", json={
            "name": "Indoor arena", "type_id": 7, "qualification_for": "booking", "flags": self.item(self.arena)["flags"]})
        self.sign_in(2)
        self.assertEqual(self.book(flags=[self.shared]).status_code, 201)
        self.sign_in(4)
        arena = self.item(self.arena)
        self.assertTrue(arena["can_view"])
        self.assertFalse(arena["can_book"])
        self.assertEqual(len(self.week().json["bookings"]), 1)
        self.assertEqual(self.book(start="2030-01-08T10:00", end="2030-01-08T11:00").status_code, 403)
        self.assertEqual(self.book(self.room).status_code, 201)

    def test_expired_qualification_does_not_count(self):
        past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        self.con.execute("UPDATE qualification_submissions SET valid_until=? WHERE user_id=2", (past,))
        self.assertFalse(self.item(self.arena)["can_view"])

    def test_flags_decide_overlap(self):
        self.assertEqual(self.book(flags=[self.shared]).status_code, 201)
        self.sign_in(3)
        self.assertEqual(self.book(start="2030-01-07T11:00", end="2030-01-07T13:00", flags=[self.shared]).status_code, 201)
        self.assertEqual(self.book(start="2030-01-07T11:00").status_code, 409)  # not shared
        self.assertEqual(self.book(start="2030-01-07T11:00", flags=[self.shared, self.demo]).status_code, 409)
        self.assertEqual(self.book(start="2030-01-07T13:00", end="2030-01-07T14:00", flags=[self.demo]).status_code, 201)
        r = self.book(start="2030-01-07T13:30", end="2030-01-07T15:00", flags=[self.shared])
        self.assertEqual(r.status_code, 409)
        self.assertIn("Demo", r.json["error"])

    def test_item_without_flags_never_overlaps_and_items_do_not_conflict(self):
        self.assertEqual(self.book(self.room).status_code, 201)
        self.assertEqual(self.book(self.room, start="2030-01-07T11:00").status_code, 409)
        self.assertEqual(self.book(self.room, start="2030-01-07T12:00", end="2030-01-07T13:00").status_code, 201)
        self.assertEqual(self.book(flags=[]).status_code, 201)  # same time, other item

    def test_unknown_flag_and_bad_times_are_rejected(self):
        self.assertEqual(self.book(self.room, flags=[self.demo]).status_code, 400)
        self.assertEqual(self.book(start="2030-01-07T12:00", end="2030-01-07T10:00").status_code, 400)

    def test_only_booker_and_admin_can_change(self):
        bid = self.book(flags=[self.shared]).json["id"]
        self.sign_in(3)
        body = {"description": "Mine now", "start": "2030-01-07T10:00", "end": "2030-01-07T11:00"}
        self.assertEqual(self.client.put(f"/api/bookings/{bid}", json=body).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/bookings/{bid}").status_code, 403)
        self.assertFalse(self.week().json["bookings"][0]["can_edit"])
        self.sign_in(1)
        self.assertEqual(self.client.put(f"/api/bookings/{bid}", json=body).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/bookings/{bid}").status_code, 200)

    def test_admin_manages_items(self):
        self.assertEqual(self.client.post("/api/admin/booking-things", json={"name": "Hall"}).status_code, 403)
        self.book(self.room)
        self.sign_in(1)
        self.assertEqual(self.client.post("/api/admin/booking-things", json={"name": "meeting ROOM"}).status_code, 409)
        self.assertEqual(self.client.post("/api/admin/booking-things", json={"name": "Hall", "colour": "red"}).status_code, 400)
        self.assertEqual(self.client.delete(f"/api/admin/booking-things/{self.room}").status_code, 409)
        flags = [f for f in self.item(self.arena)["flags"] if f["label"] == "Demo"]
        r = self.client.put(f"/api/admin/booking-things/{self.arena}", json={
            "name": "Indoor arena", "type_id": 7, "qualification_for": "viewing", "active": False, "flags": flags})
        self.assertEqual(r.status_code, 200, r.json)
        arena = self.item(self.arena)
        self.assertEqual(([f["label"] for f in arena["flags"]], arena["active"]), (["Demo"], False))
        self.sign_in(2)
        self.assertNotIn(self.arena, [t["id"] for t in self.week().json["things"]])

    def test_hidden_when_not_enabled(self):
        with patch.dict(os.environ, LOGDB_BOOKINGS="0"):
            self.assertEqual(self.week().status_code, 404)
            self.assertFalse(self.client.get("/api/me").json["bookings"])
