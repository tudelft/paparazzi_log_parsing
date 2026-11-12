import os
from unittest.mock import patch

import mail
from .test_historical import AppTests

MAIL_ENV = {"LOGDB_SMTP_HOST": "relay.example.org", "LOGDB_MAIL_FROM": "logbook@example.org",
            "LOGDB_PUBLIC_URL": "https://logs.example.org"}


class FakeSMTP:
    def __init__(self):
        self.sent = []

    def send_message(self, msg):
        self.sent.append(msg)

    def quit(self):
        pass


class MailTests(AppTests):
    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, MAIL_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.con.execute("UPDATE users SET email=lower(username)||'@example.org'")
        self.con.commit()
        self.smtp = FakeSMTP()

    def send(self, digests=False):
        mail.send_due(self.con, lambda: self.smtp, digests=digests)
        sent, self.smtp.sent = self.smtp.sent, []
        return sent

    def test_off_without_relay(self):
        with patch.dict(os.environ, {"LOGDB_SMTP_HOST": ""}):
            self.assertFalse(mail.notify(self.con, 2, "bookings", "s", "t"))
            self.assertFalse(self.client.get("/api/account/email").json["enabled"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM mail_outbox").fetchone()[0], 0)

    def test_preferences_immediate_daily_off(self):
        self.sign_in(2)
        kinds = {k["kind"]: k["mode"] for k in self.client.get("/api/account/email").json["kinds"]}
        self.assertEqual(kinds, {"account": "immediate", "expiry": "immediate", "bookings": "immediate"})  # no admin kind
        self.assertEqual(self.client.put("/api/account/email", json={"modes": {"admin": "off"}}).status_code, 400)
        self.assertEqual(self.client.put("/api/account/email", json={"modes": {"account": "off"}}).status_code, 400)
        mail.notify(self.con, 2, "bookings", "Booked: arena", "You booked the arena.", "/agenda")
        self.con.commit()
        msg, = self.send()
        self.assertEqual((msg["To"], msg["Subject"]), ("Pilot <pic@example.org>", "Booked: arena"))
        body = msg.get_content()
        self.assertIn("Hello Pilot,", body)
        self.assertIn("https://logs.example.org/agenda", body)
        self.assertIn("https://logs.example.org/account#email", body)
        self.assertIn("-- \nFlight logbook\nChoose which emails", body)
        with patch.dict(os.environ, {"LOGDB_MAIL_FOOTER": "MAVLab\\nFaculty of Aerospace Engineering",
                                     "LOGDB_MAIL_FROM_NAME": "MAVLab logbook"}):
            mail.notify(self.con, 2, "bookings", "Booked: bench", "You booked the bench.")
            self.con.commit()
            msg, = self.send()
            self.assertEqual(msg["From"], "MAVLab logbook <logbook@example.org>")
            self.assertIn("-- \nMAVLab\nFaculty of Aerospace Engineering\nChoose which emails", msg.get_content())
        self.assertEqual(self.client.put("/api/account/email", json={"modes": {"bookings": "daily"}}).status_code, 200)
        for n in (1, 2):
            mail.notify(self.con, 2, "bookings", f"Booked {n}", f"Booking {n}.")
        self.con.commit()
        self.assertEqual(self.send(), [])            # waits for the daily summary
        msg, = self.send(digests=True)
        self.assertEqual(msg["Subject"], "Flight logbook: 2 notices")
        self.assertIn("* Booked 2", msg.get_content())
        self.client.put("/api/account/email", json={"modes": {"bookings": "off"}})
        self.assertFalse(mail.notify(self.con, 2, "bookings", "s", "t"))

    def test_relay_failure_is_retried(self):
        mail.notify(self.con, 2, "bookings", "s", "t")
        self.con.commit()
        mail.send_due(self.con, lambda: (_ for _ in ()).throw(OSError("relay down")))
        row = self.con.execute("SELECT sent_at, attempts, error FROM mail_outbox").fetchone()
        self.assertEqual((row[0], row[1], row[2]), (None, 1, "relay down"))
        self.assertEqual(len(self.send()), 1)

    def test_setup_link_is_emailed_when_asked(self):
        self.sign_in(1)
        r = self.client.post("/api/admin/users", json={"username": "newbie", "email": "newbie@example.org",
                                                       "send_email": True}).json
        self.assertEqual(r["mailed_to"], "newbie@example.org")
        msg, = self.send()
        self.assertIn(r["setup_url"], msg.get_content())
        self.assertTrue(r["setup_url"].startswith("https://logs.example.org/setup/"))
        self.assertIsNone(self.client.post(f"/api/admin/users/{r['id']}/setup-link", json={}).json["mailed_to"])
        self.assertEqual(self.send(), [])

    def test_expiry_reminders_30_and_7_days_once(self):
        import datetime
        today = datetime.date(2026, 11, 1)
        self.con.execute("INSERT INTO qualification_types(id,name,requires_evidence) VALUES (7,'A2 certificate',0)")
        self.con.execute("INSERT INTO qualification_submissions(id,user_id,type_id,status,submitted_at,valid_until)"
                         " VALUES (1,2,7,'approved','now','2026-11-25')")
        self.con.commit()
        self.sign_in(1)
        self.assertEqual(self.client.put("/api/account/email", json={"modes": {"expiry_all": "immediate"}}).status_code, 200)
        mail.queue_reminders(self.con, today)
        mail.queue_reminders(self.con, today + datetime.timedelta(days=1))   # same step: not again
        sent = sorted((m["To"], m["Subject"]) for m in self.send())
        self.assertEqual(sent, [("Admin <admin@example.org>", "Pilot: A2 certificate expires on 2026-11-25"),
                                ("Pilot <pic@example.org>", "Your A2 certificate expires on 2026-11-25")])
        self.client.put("/api/account/email", json={"modes": {"expiry_all": "off"}})
        mail.queue_reminders(self.con, datetime.date(2026, 11, 18))         # 7 days before; admin copy off
        self.assertEqual([m["To"] for m in self.send()], ["Pilot <pic@example.org>"])
        self.con.execute("UPDATE qualification_submissions SET valid_until='2028-11-25'")  # renewed: no reminders
        mail.queue_reminders(self.con, datetime.date(2026, 11, 20))
        self.assertEqual(self.send(), [])

    def test_bookings_and_admin_notices(self):
        self.con.execute("INSERT INTO booking_things(id,name,colour) VALUES (1,'Arena','#2da44e')")
        self.con.execute("INSERT INTO booking_flags(id,thing_id,label) VALUES (1,1,'Shared')")
        self.con.commit()
        with patch.dict(os.environ, {"LOGDB_BOOKINGS": "1"}):
            self.sign_in(2)
            first = self.client.post("/api/bookings", json={"thing_id": 1, "description": "Hover tests", "start": "2026-11-16T10:00",
                                                            "end": "2026-11-16T12:00", "flags": [1]})
            self.assertEqual(first.status_code, 201, first.json)
            msg, = self.send()
            self.assertEqual(msg["Subject"], "Booked: Arena, Mon 16 Nov 2026 10:00–12:00")
            self.sign_in(3)
            self.client.post("/api/bookings", json={"thing_id": 1, "description": "Swarm", "start": "2026-11-16T11:00",
                                                    "end": "2026-11-16T13:00", "flags": [1]})
            to = sorted((m["To"], m["Subject"]) for m in self.send())
            self.assertEqual(to, [("Pilot <pic@example.org>", "Arena: Viewer booked during your booking"),
                                  ("Viewer <viewer@example.org>", "Booked: Arena, Mon 16 Nov 2026 11:00–13:00")])
            self.sign_in(1)
            self.assertEqual(self.client.delete(f"/api/bookings/{first.json['id']}").status_code, 200)
            msg, = self.send()
            self.assertEqual((msg["To"], msg["Subject"]), ("Pilot <pic@example.org>", "Removed: your booking of Arena"))
        mail.notify_admins(self.con, "Log 5 failed to parse", "bad file", "/logs#log5")
        self.con.commit()
        self.assertEqual([m["To"] for m in self.send()], ["Admin <admin@example.org>"])
        with patch.dict(os.environ, {"LOGDB_MAIL_ADMIN_TO": "team@example.org"}):
            mail.notify_admins(self.con, "Log 6 failed to parse", "bad file", key="parse-error:6")
            mail.notify_admins(self.con, "Log 6 failed to parse", "bad file", key="parse-error:6")
            self.con.commit()
            msg, = self.send()
            self.assertEqual(msg["To"], "team@example.org")
            self.assertNotIn("Hello", msg.get_content())
            self.sign_in(1)
            self.assertNotIn("admin", [k["kind"] for k in self.client.get("/api/account/email").json["kinds"]])

    def test_admin_mail_log(self):
        mail.notify(self.con, 2, "bookings", "Booked: arena", "You booked the arena.")
        mail.notify(self.con, 3, "bookings", "Booked: bench", "You booked the bench.")
        self.con.commit()
        mail.send_due(self.con, lambda: (_ for _ in ()).throw(OSError("relay down")))
        self.con.execute("UPDATE mail_outbox SET attempts=5 WHERE user_id=3")
        mail.notify(self.con, 2, "account", "Flight logbook: set a new password", "https://x/setup/secret")
        self.con.commit()
        self.send()
        self.assertEqual(self.client.get("/api/admin/mail").status_code, 403)
        self.sign_in(1)
        log = self.client.get("/api/admin/mail").json
        self.assertEqual([(m["to"], m["status"]) for m in log["mails"]],
                         [("Pilot <pic@example.org>", "sent"), ("Viewer <viewer@example.org>", "failed"),
                          ("Pilot <pic@example.org>", "sent")])
        self.assertEqual(self.client.get("/api/admin/mail?status=failed").json["total"], 1)
        self.con.execute("UPDATE mail_outbox SET body='' WHERE kind='account' AND sent_at IS NOT NULL")
        self.con.commit()
        body = self.client.get(f"/api/admin/mail/{log['mails'][0]['id']}").json["body"]
        self.assertNotIn("secret", body)
