import os
from unittest.mock import patch

from .test_historical import AppTests


class ViewAsTests(AppTests):
    """LOGDB_VIEW_AS: an administrator's browser session stands in for another user.
    Users: 1 Admin, 2 Pilot and 3 Viewer (project 1), 4 Outsider (no project)."""

    def enable(self):
        settings = patch.dict(os.environ, LOGDB_VIEW_AS="1")
        settings.start()
        self.addCleanup(settings.stop)

    def test_off_by_default(self):
        self.sign_in(1)
        self.assertEqual(self.client.post("/api/view-as/2").status_code, 404)
        self.assertFalse(self.client.get("/api/detail/people/2").json["item"]["can_view_as"])

    def test_admin_views_and_acts_as_the_person_until_stopped(self):
        self.enable()
        self.sign_in(1)
        self.assertTrue(self.client.get("/api/detail/people/2").json["item"]["can_view_as"])
        self.assertFalse(self.client.get("/api/detail/people/1").json["item"]["can_view_as"])  # not themself
        with self.assertLogs(self.app.logger, "WARNING") as logged:
            self.assertEqual(self.client.post("/api/view-as/3").status_code, 200)
        self.assertIn("admin 1 (admin) started viewing as user 3 (viewer)", logged.output[0])
        me = self.client.get("/api/me").json
        self.assertEqual((me["user"]["id"], me["user"]["is_admin"]), (3, False))
        self.assertEqual(me["viewing_as_by"]["id"], 1)
        self.assertEqual(self.client.get("/api/detail/people/4").status_code, 404)  # the viewer's access, not the admin's
        # credentials stay out of reach
        self.assertEqual(self.client.post("/api/keys", json={"name": "k"}).status_code, 403)
        self.assertEqual(self.client.post("/api/account/password", json={}).status_code, 403)
        # switching to someone else from their page works while viewing
        self.assertEqual(self.client.post("/api/view-as/2").status_code, 200)
        self.assertEqual(self.client.get("/api/me").json["user"]["id"], 2)
        with self.assertLogs(self.app.logger, "WARNING"):
            self.assertEqual(self.client.post("/api/view-as/stop").status_code, 200)
        me = self.client.get("/api/me").json
        self.assertEqual((me["user"]["id"], me["viewing_as_by"]), (1, None))

    def test_only_admins_and_only_while_enabled(self):
        self.enable()
        self.sign_in(2)
        self.assertEqual(self.client.post("/api/view-as/3").status_code, 404)
        self.con.execute("UPDATE users SET is_active=0 WHERE id=4")
        self.con.commit()
        self.sign_in(1)
        self.assertEqual(self.client.post("/api/view-as/4").status_code, 404)  # cannot sign in
        self.client.post("/api/view-as/2")
        with patch.dict(os.environ, LOGDB_VIEW_AS="0"):  # switched off: back to the admin's own session
            self.assertEqual(self.client.get("/api/me").json["user"]["id"], 1)
        self.assertIsNone(self.client.get("/api/me").json["viewing_as_by"])


class ViewAsWelcomeTests(AppTests):
    enable = ViewAsTests.enable

    def test_dismissing_a_home_message_is_the_persons_own_step(self):
        self.enable()
        self.sign_in(1)
        self.client.put("/api/admin/welcome", json={"title": "Hi", "body": "Hello lab."})
        self.client.post("/api/view-as/3")
        self.assertEqual([m["id"] for m in self.client.get("/api/home").json["messages"]], ["welcome"])
        r = self.client.post("/api/home/messages/dismiss", json={"id": "welcome", "version": 1})
        self.assertEqual(r.status_code, 403)
        self.assertIsNone(self.con.execute("SELECT 1 FROM welcome_seen WHERE user_id=3").fetchone())
