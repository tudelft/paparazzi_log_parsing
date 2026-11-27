import datetime
import os
from unittest.mock import patch

from .test_historical import AppTests


class BookingTests(AppTests):
    """Users: 1 Admin, 2 Pilot and 3 Viewer hold the briefing; 4 Outsider does not (fixtures in AppTests)."""

    def setUp(self):
        AppTests.setUp(self)  # also BookingApprovalTests' setup
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

    def test_home_coming_up_shows_everyones_bookings_on_items_one_can_view(self):
        start = (datetime.datetime.now() + datetime.timedelta(days=1)).replace(hour=10, minute=0)
        span = dict(start=start.strftime("%Y-%m-%dT%H:%M"), end=(start + datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"))
        self.assertEqual(self.book(**span).status_code, 201)  # the pilot, on the arena
        mine = self.client.get("/api/home").json["bookings"]
        self.assertEqual([(b["thing"], b["mine"]) for b in mine["upcoming"]], [("Indoor arena", True)])
        self.sign_in(3)  # holds the briefing: sees the pilot's booking, not as their own
        home = self.client.get("/api/home").json["bookings"]
        self.assertEqual(home["upcoming"], [])
        self.assertEqual([(b["thing"], b["person"], b["mine"]) for b in home["upcoming_all"]],
                         [("Indoor arena", "Pilot", False)])
        self.sign_in(4)  # may not view the arena: nothing of it
        self.assertEqual(self.client.get("/api/home").json["bookings"]["upcoming_all"], [])

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

    def test_only_a_blocking_option_prevents_overlap(self):
        self.assertEqual(self.book(flags=[self.shared]).status_code, 201)  # 10:00-12:00
        self.sign_in(3)
        self.assertEqual(self.book(start="2030-01-07T11:00", end="2030-01-07T13:00").status_code, 201)  # no option needed
        r = self.book(start="2030-01-07T11:00", flags=[self.shared, self.demo])  # blocking: not while others book
        self.assertEqual(r.status_code, 409)
        self.assertIn("yours is marked Demo", r.json["error"])
        self.assertEqual(self.book(start="2030-01-07T13:00", end="2030-01-07T14:00", flags=[self.demo]).status_code, 201)
        r = self.book(start="2030-01-07T13:30", end="2030-01-07T15:00", flags=[self.shared])  # theirs blocks
        self.assertEqual(r.status_code, 409)
        self.assertIn("marked Demo", r.json["error"])

    def test_an_exclusive_item_overlaps_only_with_a_shared_option(self):
        self.con.execute("UPDATE booking_things SET exclusive=1 WHERE id=?", (self.arena,))
        self.con.commit()
        self.assertTrue(self.item(self.arena)["exclusive"])
        self.assertEqual(self.book(flags=[self.shared]).status_code, 201)
        self.sign_in(3)
        r = self.book(start="2030-01-07T11:00")
        self.assertEqual(r.status_code, 409)
        self.assertIn("only when both tick the same option: Others allowed", r.json["error"])
        self.assertEqual(self.book(start="2030-01-07T11:00", flags=[self.shared]).status_code, 201)

    def test_items_without_options_overlap_and_items_do_not_conflict(self):
        self.assertEqual(self.book(self.room).status_code, 201)
        self.assertEqual(self.book(self.room, start="2030-01-07T11:00").status_code, 201)
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

    def test_admin_books_for_someone_else_and_hands_over(self):
        body = {"thing_id": self.room, "description": "Demo", "start": "2030-01-07T10:00", "end": "2030-01-07T11:00"}
        self.assertEqual(self.client.post("/api/bookings", json={**body, "user_id": 3}).status_code, 403)  # not an admin
        self.assertNotIn("people", self.week().json)
        self.sign_in(1)
        self.assertIn(3, [p["id"] for p in self.week().json["people"]])
        self.assertEqual(self.client.post("/api/bookings", json={**body, "user_id": 999}).status_code, 400)
        bid = self.client.post("/api/bookings", json={**body, "user_id": 3}).json["id"]
        self.assertEqual(self.week().json["bookings"][0]["user_id"], 3)
        moved = {**body, "start": "2030-01-08T14:00", "end": "2030-01-08T15:00", "user_id": 2}
        self.assertEqual(self.client.put(f"/api/bookings/{bid}", json=moved).status_code, 200)
        b = self.week().json["bookings"][0]
        self.assertEqual((b["user_id"], b["start"]), (2, "2030-01-08T14:00"))
        self.sign_in(2)  # the new owner can change it; without user_id it stays theirs
        self.assertEqual(self.client.put(f"/api/bookings/{bid}", json=body).status_code, 200)
        self.assertEqual(self.week().json["bookings"][0]["user_id"], 2)

    def test_admin_manages_items(self):
        self.assertEqual(self.client.post("/api/admin/booking-things", json={"name": "Hall"}).status_code, 403)
        self.book(self.room)
        self.sign_in(1)
        self.assertEqual(self.client.post("/api/admin/booking-things", json={"name": "meeting ROOM"}).status_code, 409)
        self.assertEqual(self.client.post("/api/admin/booking-things", json={"name": "Hall", "colour": "red"}).status_code, 400)
        # every item has a colour: without one, the first of the palette no item uses yet
        hall = self.client.post("/api/admin/booking-things", json={"name": "Hall"}).json["id"]
        used = {t["colour"] for t in self.client.get("/api/bookings?from=2030-01-07&to=2030-01-07").json["things"] if t["id"] != hall}
        self.assertNotIn(self.item(hall)["colour"], used)
        self.assertRegex(self.item(hall)["colour"], r"^#[0-9a-f]{6}$")
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

    def test_calendar_feed_by_private_link(self):
        day = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
        self.assertEqual(self.book(start=f"{day}T10:00", end=f"{day}T12:00", flags=[self.shared]).status_code, 201)
        self.assertEqual(self.book(self.room, start=f"{day}T13:00", end=f"{day}T14:00").status_code, 201)
        self.assertIsNone(self.client.get("/api/bookings/calendar").json["token"])
        token = self.client.post("/api/bookings/calendar").json["token"]
        with self.client.session_transaction() as session:
            session.clear()  # calendar apps cannot sign in: the token is the key
        feed = self.client.get(f"/calendar/{token}/all.ics")
        self.assertEqual((feed.status_code, feed.mimetype), (200, "text/calendar"))
        text = feed.get_data(as_text=True)
        self.assertEqual(text.count("BEGIN:VEVENT"), 2)
        self.assertIn("DTSTART:" + day.replace("-", "") + "T100000", text)
        self.assertIn("SUMMARY:Indoor arena: Hover test", text)
        self.assertIn("Others allowed", text)
        one = self.client.get(f"/calendar/{token}/{self.room}.ics").get_data(as_text=True)
        self.assertEqual((one.count("BEGIN:VEVENT"), "X-WR-CALNAME:Meeting room" in one), (1, True))
        self.assertEqual(self.client.get("/calendar/not-a-real-token-at-all-xx/all.ics").status_code, 404)
        # someone without the qualification does not see an item that needs it for viewing
        self.sign_in(4)
        other = self.client.post("/api/bookings/calendar").json["token"]
        self.assertEqual(self.client.get(f"/calendar/{other}/all.ics").get_data(as_text=True).count("BEGIN:VEVENT"), 1)
        # replacing the link stops the old one
        self.sign_in(2)
        self.client.post("/api/bookings/calendar")
        self.assertEqual(self.client.get(f"/calendar/{token}/all.ics").status_code, 404)

    def test_admin_orders_the_tabs(self):
        url = "/api/admin/booking-things-order"
        self.assertEqual(self.client.put(url, json={"ids": [self.room, self.arena]}).status_code, 403)
        self.sign_in(1)
        self.assertEqual(self.client.put(url, json={"ids": [self.room]}).status_code, 400)
        self.assertEqual(self.client.put(url, json={"ids": [self.room, self.arena]}).status_code, 200)
        self.assertEqual([t["id"] for t in self.week().json["things"]], [self.room, self.arena])


class BookingApprovalTests(AppTests):
    """The meeting room needs approval; 3 Viewer approves it. 1 Admin, 2 Pilot (books), 4 Outsider."""
    MAIL = {"LOGDB_SMTP_HOST": "relay.example.org", "LOGDB_MAIL_FROM": "logbook@example.org",
            "LOGDB_PUBLIC_URL": "https://logs.example.org"}
    add_item, week, item, book = BookingTests.add_item, BookingTests.week, BookingTests.item, BookingTests.book

    def setUp(self):
        BookingTests.setUp(self)
        settings = patch.dict(os.environ, self.MAIL)
        settings.start()
        self.addCleanup(settings.stop)
        self.con.execute("UPDATE users SET email=lower(username)||'@example.org'")
        self.con.commit()
        self.sign_in(1)
        r = self.client.put(f"/api/admin/booking-things/{self.room}",
                            json={"name": "Meeting room", "needs_approval": True, "approver_ids": [3]})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(self.client.put(f"/api/admin/booking-things/{self.room}", json={
            "name": "Meeting room", "needs_approval": True, "approver_ids": [999]}).status_code, 400)
        self.sign_in(2)

    def mails(self, subject):
        return sorted(r[0] for r in self.con.execute(
            "SELECT u.username FROM mail_outbox m JOIN users u ON u.id=m.user_id WHERE m.subject LIKE ?",
            (subject + "%",)))

    def status(self, booking_id):
        return self.con.execute("SELECT status FROM bookings WHERE id=?", (booking_id,)).fetchone()[0]

    def test_who_needs_approval(self):
        r = self.book(self.room)
        self.assertEqual((r.status_code, r.json["status"]), (201, "pending"))
        self.assertEqual(self.mails("Booking to approve"), ["admin", "viewer"])  # admins and the approver
        item = self.item(self.room)
        self.assertTrue(item["needs_approval"])
        self.assertFalse(item["can_approve"])
        self.assertNotIn("approver_ids", item)
        self.sign_in(3)  # the approver books without approval
        self.assertEqual(self.book(self.room, start="2030-01-08T10:00", end="2030-01-08T11:00").json["status"],
                         "approved")
        self.assertTrue(self.item(self.room)["can_approve"])
        self.sign_in(1)
        self.assertEqual(self.book(self.room, start="2030-01-09T10:00", end="2030-01-09T11:00").json["status"],
                         "approved")
        self.assertEqual(self.item(self.room)["approver_ids"], [3])
        self.sign_in(2)  # items without approval are unchanged
        self.assertEqual(self.book(self.arena, flags=[self.shared]).json["status"], "approved")

    def test_pending_holds_the_slot_and_reject_frees_it(self):
        exclusive = self.con.execute("INSERT INTO booking_flags(thing_id, label, blocks_overlap, sort_order)"
                                     " VALUES (?, 'No others', 1, 0)", (self.room,)).lastrowid
        self.con.commit()
        bid = self.book(self.room, flags=[exclusive]).json["id"]
        self.sign_in(4)
        r = self.book(self.room, start="2030-01-07T11:00", end="2030-01-07T13:00")
        self.assertEqual(r.status_code, 409)
        self.assertIn("asked to book", r.json["error"])
        theirs = [b for b in self.week().json["bookings"] if b["id"] == bid][0]
        self.assertEqual((theirs["status"], theirs["can_decide"]), ("pending", False))
        self.sign_in(3)
        self.assertTrue([b for b in self.week().json["bookings"] if b["id"] == bid][0]["can_decide"])
        url = f"/api/bookings/{bid}/review"
        self.assertEqual(self.client.post(url, json={"action": "reject", "reason": "x" * 501}).status_code, 400)
        self.assertEqual(self.client.post(url, json={"action": "reject", "reason": "Lab closed"}).status_code, 200)
        self.assertEqual(self.client.post(url, json={"action": "approve"}).status_code, 409)  # only pending
        self.assertEqual(self.mails("Not approved"), ["pic"])
        body = self.con.execute("SELECT body FROM mail_outbox WHERE subject LIKE 'Not approved%'").fetchone()[0]
        self.assertIn("Reason: Lab closed", body)
        self.assertNotIn(bid, [b["id"] for b in self.week().json["bookings"]])  # gone for others
        self.sign_in(4)
        self.assertEqual(self.book(self.room, start="2030-01-07T11:00", end="2030-01-07T13:00").status_code, 201)
        self.sign_in(2)
        mine = [b for b in self.week().json["bookings"] if b["id"] == bid][0]  # the booker still sees it
        self.assertEqual((mine["status"], mine["review_note"]), ("rejected", "Lab closed"))

    def test_approver_scope_and_approve(self):
        pending = self.book(self.room).json["id"]
        self.sign_in(1)  # the arena needs approval too, without approvers: only administrators
        self.client.put(f"/api/admin/booking-things/{self.arena}", json={
            "name": "Indoor arena", "type_id": 7, "qualification_for": "viewing", "needs_approval": True,
            "flags": self.item(self.arena)["flags"]})
        self.sign_in(2)
        other = self.book(self.arena, start="2030-01-08T10:00", end="2030-01-08T11:00").json["id"]
        self.sign_in(3)
        self.assertEqual(self.client.post(f"/api/bookings/{other}/review", json={"action": "approve"}).status_code, 403)
        self.assertEqual(self.client.get("/api/admin/booking-pending").status_code, 403)
        self.assertEqual(self.client.post(f"/api/bookings/{pending}/review", json={"action": "approve"}).status_code,
                         200)
        self.assertEqual(self.status(pending), "approved")
        self.assertEqual(self.mails("Approved"), ["pic"])
        self.sign_in(2)
        self.assertEqual(self.client.post(f"/api/bookings/{other}/review", json={"action": "approve"}).status_code, 403)
        self.sign_in(1)
        rows = self.client.get("/api/admin/booking-pending").json["bookings"]
        self.assertEqual([(b["id"], b["thing"], b["person"]) for b in rows], [(other, "Indoor arena", "Pilot")])
        self.assertEqual(self.client.post(f"/api/bookings/{other}/review", json={"action": "approve"}).status_code, 200)
        self.assertEqual(self.client.get("/api/admin/booking-pending").json["bookings"], [])

    def test_moving_needs_approval_again(self):
        bid = self.book(self.room).json["id"]
        self.sign_in(3)
        self.client.post(f"/api/bookings/{bid}/review", json={"action": "approve"})
        self.sign_in(2)
        body = {"description": "Hover test", "start": "2030-01-07T10:00", "end": "2030-01-07T12:00", "flags": []}
        self.assertEqual(self.client.put(f"/api/bookings/{bid}", json={**body, "description": "Renamed"}).json["status"],
                         "approved")  # same time
        self.con.execute("DELETE FROM mail_outbox")
        self.con.commit()
        r = self.client.put(f"/api/bookings/{bid}", json={**body, "start": "2030-01-07T13:00", "end": "2030-01-07T14:00"})
        self.assertEqual(r.json["status"], "pending")
        self.assertEqual(self.mails("Booking to approve"), ["admin", "viewer"])
        self.sign_in(1)  # an administrator moving it keeps the status; switching approval off books what waits
        r = self.client.put(f"/api/bookings/{bid}", json={**body, "start": "2030-01-07T15:00", "end": "2030-01-07T16:00"})
        self.assertEqual(r.json["status"], "pending")
        self.client.put(f"/api/admin/booking-things/{self.room}", json={"name": "Meeting room"})
        self.assertEqual(self.status(bid), "approved")

    def test_rejected_booking_asked_again(self):
        bid = self.book(self.room).json["id"]
        self.sign_in(1)
        self.client.post(f"/api/bookings/{bid}/review", json={"action": "reject"})
        self.sign_in(2)
        body = {"description": "Hover test", "start": "2030-01-07T10:00", "end": "2030-01-07T12:00", "flags": []}
        self.assertEqual(self.client.put(f"/api/bookings/{bid}", json=body).json["status"], "pending")
        self.assertIsNone(self.con.execute("SELECT review_note FROM bookings WHERE id=?", (bid,)).fetchone()[0])

    def test_home_counts_bookings_to_approve(self):
        self.book(self.room)
        self.assertEqual(self.client.get("/api/home").json["bookings"]["to_approve"]["count"], 0)
        for uid in (1, 3):  # an administrator, and the room's approver
            self.sign_in(uid)
            ask = self.client.get("/api/home").json["bookings"]["to_approve"]
            self.assertEqual((ask["count"], ask["thing_id"], ask["week"]), (1, self.room, "2030-01-07"))
