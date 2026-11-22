import datetime

from . import test_bookings
from .test_historical import AppTests


def ago(days):
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


class ApprovalHistoryTests(AppTests):
    """The Decided view of the approval queue. Users (BookingApprovalTests): 1 Admin, 2 Pilot (books),
    3 Viewer (approver of the meeting room), 4 Outsider."""
    # BookingApprovalTests' fixtures (not bound at module level, so its tests do not run here again)
    setUp, MAIL = test_bookings.BookingApprovalTests.setUp, test_bookings.BookingApprovalTests.MAIL
    book, add_item = test_bookings.BookingTests.book, test_bookings.BookingTests.add_item
    item, week = test_bookings.BookingTests.item, test_bookings.BookingTests.week

    def submission(self, user_id, status, submitted, reviewed, reviewer, note=None, review_note=None, type_id=7):
        sid = self.con.execute(
            "INSERT INTO qualification_submissions(user_id,type_id,status,submitted_at,reviewed_by,reviewed_at,note,"
            "review_note) VALUES (?,?,?,?,?,?,?,?)",
            (user_id, type_id, status, submitted, reviewer, reviewed, note, review_note)).lastrowid
        self.con.commit()
        return sid

    def history(self, **args):
        r = self.client.get("/api/admin/approval-history", query_string=args)
        self.assertEqual(r.status_code, 200, r.json)
        return r.json

    def seed(self):
        self.con.execute("INSERT INTO qualification_types(id,name,requires_evidence) VALUES (8,'Medical class 2',0)")
        self.approved = self.submission(2, "approved", ago(3), ago(2), 1, type_id=8)
        self.rejected = self.submission(4, "rejected", ago(5), ago(4), 1, review_note="The scan is unreadable",
                                        type_id=8)
        self.withdrawn = self.submission(3, "withdrawn", ago(6), ago(6), 3, type_id=8)  # by the person
        stamp = ago(1)
        self.automatic = self.submission(4, "approved", stamp, stamp, 4,
                                         note='Training "Briefing" passed 9/10 on 2026-11-19 · briefed by Admin')
        self.imported = self.submission(3, "approved", stamp, stamp, 1, type_id=8)
        self.same_second = self.submission(4, "approved", stamp, stamp, 1, type_id=8)  # added and approved at once
        import qualifications
        qualifications.audit(self.con, 4, 4, "training-passed", {"id": self.automatic})
        qualifications.audit(self.con, 3, 1, "approved", {"id": self.imported, "source": "csv-import"})
        self.con.commit()
        self.old = self.submission(2, "superseded", ago(400), ago(400), 1, type_id=8)
        self.sign_in(2)
        pending = self.book(self.room).json["id"]
        self.con.execute("UPDATE bookings SET created_at=? WHERE id=?", (ago(1), pending))  # asked earlier
        self.con.commit()
        self.sign_in(3)  # the approver decides one, and books one (approved at once)
        self.assertEqual(self.client.post(f"/api/bookings/{pending}/review", json={"action": "approve"}).status_code,
                         200)
        self.own = self.book(self.room, start="2030-01-08T10:00", end="2030-01-08T11:00").json["id"]
        self.sign_in(2)
        self.refused = self.book(self.room, start="2030-01-09T10:00", end="2030-01-09T11:00").json["id"]
        self.con.execute("UPDATE bookings SET created_at=? WHERE id=?", (ago(1), self.refused))
        self.con.commit()
        self.sign_in(1)
        self.assertEqual(self.client.post(f"/api/bookings/{self.refused}/review",
                                          json={"action": "reject", "reason": "Room is closed"}).status_code, 200)
        r = self.book(self.arena)
        self.assertEqual(r.status_code, 201, r.json)
        self.plain = r.json["id"]  # an agenda without approval: not in the history
        self.pending = pending

    def test_admin_only(self):
        self.sign_in(2)
        self.assertEqual(self.client.get("/api/admin/approval-history").status_code, 403)
        self.sign_in(1)
        for bad in ({"kind": "x"}, {"period": "week"}, {"page": "0"}, {"page": "x"}):
            self.assertEqual(self.client.get("/api/admin/approval-history", query_string=bad).status_code, 400, bad)

    def test_decided_items_newest_first_with_automatic_and_withdrawn(self):
        self.seed()
        d = self.history()
        keys = [i["key"] for i in d["items"]]
        self.assertEqual(d["total"], 9)  # 6 qualifications and 3 bookings; not the plain agenda's booking, nor the record from 400 days ago
        self.assertNotIn(f"b{self.plain}", keys)
        self.assertNotIn(f"q{self.old}", keys)
        decided = [i["decided_at"] for i in d["items"]]
        self.assertEqual(decided, sorted(decided, reverse=True))
        by_key = {i["key"]: i for i in d["items"]}
        auto = by_key[f"q{self.automatic}"]
        self.assertEqual((auto["kind"], auto["what"], auto["automatic"], auto["by"], auto["score"], auto["briefed_by"]),
                         ("training", "Briefing", True, None, "9 / 10", "Admin"))
        self.assertEqual(by_key[f"q{self.withdrawn}"]["reason"], "By the person")
        imported, same = by_key[f"q{self.imported}"], by_key[f"q{self.same_second}"]
        self.assertEqual((imported["automatic"], imported["by"], imported["reason"]), (True, "Admin", "Imported"))
        self.assertEqual((same["automatic"], same["by"], same["reason"]), (False, "Admin", None))
        self.assertEqual((by_key[f"q{self.rejected}"]["outcome"], by_key[f"q{self.rejected}"]["rejection"]),
                         ("rejected", "The scan is unreadable"))
        self.assertTrue(by_key[f"b{self.pending}"]["by_approver"])
        own = by_key[f"b{self.own}"]
        self.assertEqual((own["automatic"], own["by"], own["reason"]),
                         (True, "Viewer", "Automatic: booked by an approver"))
        self.assertEqual(by_key[f"b{self.refused}"]["rejection"], "Room is closed")

    def test_filters_search_period_and_pages(self):
        self.seed()
        self.assertEqual({i["kind"] for i in self.history(kind="booking")["items"]}, {"booking"})
        self.assertEqual([i["key"] for i in self.history(kind="training")["items"]], [f"q{self.automatic}"])
        self.assertEqual([i["key"] for i in self.history(q="UNREADABLE")["items"]], [f"q{self.rejected}"])
        self.assertEqual([i["key"] for i in self.history(q="room is")["items"]], [f"b{self.refused}"])
        self.assertEqual(self.history(period="all")["total"], 10)  # with the old one
        import approval_history
        size = approval_history.PAGE_SIZE
        for n in range(size):
            self.submission(2, "rejected", ago(10), ago(10), 1, type_id=8)
        first, second = self.history(), self.history(page=2)
        self.assertEqual((first["total"], len(first["items"]), len(second["items"])), (9 + size, size, 9))
        self.assertFalse({i["key"] for i in first["items"]} & {i["key"] for i in second["items"]})
