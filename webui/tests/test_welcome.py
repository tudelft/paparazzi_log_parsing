import os
from unittest.mock import patch

from .test_historical import AppTests


class WelcomeTests(AppTests):
    """The welcome message (welcome.py): written by admins, shown once after the profile, before the page."""

    def write(self, body, title="Welcome", **extra):
        self.sign_in(1)
        return self.client.put("/api/admin/welcome", json={"title": title, "body": body, **extra})

    def test_shown_once_after_the_profile(self):
        self.sign_in(3)
        self.assertEqual(self.client.get("/logbook").status_code, 200)  # no message: straight to the page
        self.assertEqual(self.write("Hello lab.\n\nRead /training first.").status_code, 200)
        self.sign_in(3)
        page = self.client.get("/logbook?x=1")
        self.assertEqual(page.status_code, 302)
        self.assertEqual(page.headers["Location"], "/welcome?next=%2Flogbook%3Fx%3D1")
        self.assertEqual(self.client.get("/welcome").status_code, 200)
        self.assertEqual(self.client.get("/api/me").status_code, 200)  # the API keeps working
        self.assertEqual(self.client.get("/api/welcome").json["message"]["body"], "Hello lab.\n\nRead /training first.")
        self.assertNotIn("seen", self.client.get("/api/welcome").json)  # admins only
        self.assertEqual(self.client.post("/api/welcome/seen").status_code, 200)
        self.assertEqual(self.client.get("/logbook").status_code, 200)

    def test_profile_comes_first(self):
        settings = patch.dict(os.environ, LOGDB_PERSON_PROFILE="1")
        settings.start()
        self.addCleanup(settings.stop)
        self.ns["profiles"].seed(self.con)
        self.con.commit()
        self.write("Hello lab.")
        self.sign_in(3)
        self.assertTrue(self.client.get("/logbook").headers["Location"].startswith("/profile?"))
        self.assertEqual(self.client.get("/welcome").status_code, 200)  # not sent back to the profile

    def test_edits_keep_the_version_unless_shown_again(self):
        self.write("Helo lab.")
        self.sign_in(3)
        self.client.post("/api/welcome/seen")
        self.write("Hello lab.")  # a typo fixed: not shown again
        self.sign_in(3)
        self.assertEqual(self.client.get("/logbook").status_code, 200)
        self.assertEqual(self.client.get("/api/welcome").json["message"]["version"], 1)
        self.write("News for everyone.", show_again=True)
        self.sign_in(3)
        self.assertEqual(self.client.get("/logbook").status_code, 302)
        self.client.post("/api/welcome/seen")
        self.sign_in(1)
        self.assertEqual(self.client.get("/api/welcome").json["seen"], 1)
        self.write("")  # emptied: no welcome message
        self.sign_in(4)
        self.assertEqual(self.client.get("/logbook").status_code, 200)
        self.assertIsNone(self.client.get("/api/welcome").json["message"])

    def test_only_admins_write_it(self):
        self.sign_in(3)
        self.assertEqual(self.client.put("/api/admin/welcome", json={"body": "x"}).status_code, 403)
        self.assertIn("at most", self.write("x" * 10001).json["error"])
        self.assertIn("true or false", self.write("x", show_again="yes").json["error"])
