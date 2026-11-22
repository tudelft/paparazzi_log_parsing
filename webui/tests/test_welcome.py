import os
from unittest.mock import patch

from .test_historical import AppTests


class WelcomeTests(AppTests):
    """The welcome message (welcome.py): written by admins, shown at the top of the Home page (/api/home messages)
    until dismissed; no page is ever redirected for it."""

    def write(self, body, title="Welcome", **extra):
        self.sign_in(1)
        return self.client.put("/api/admin/welcome", json={"title": title, "body": body, **extra})

    def messages(self):
        return self.client.get("/api/home").json["messages"]

    def dismiss(self, version, message_id="welcome"):
        return self.client.post("/api/home/messages/dismiss", json={"id": message_id, "version": version})

    def test_on_home_until_dismissed_and_never_a_redirect(self):
        self.sign_in(3)
        self.assertEqual(self.messages(), [])  # no message
        self.assertEqual(self.write("Hello lab.\n\nRead /training first.").status_code, 200)
        self.sign_in(3)
        for path in ("/", "/home", "/documents", "/people", "/aircraft", "/training", "/safety", "/logbook?x=1"):
            self.assertNotEqual(self.client.get(path).status_code, 302, path)  # Operations Manual paths too
        msg, = self.messages()
        self.assertEqual((msg["id"], msg["kind"], msg["version"], msg["title"], msg["dismissible"]),
                         ("welcome", "welcome", 1, "Welcome", True))
        self.assertEqual(msg["html"], '<p>Hello lab.</p><p>Read <a href="/training">/training</a> first.</p>')
        self.assertEqual(self.client.get("/welcome").status_code, 302)  # the editor: admins only, others go Home
        self.assertNotIn("text", self.client.get("/api/welcome").json)  # the raw text: admins only
        self.assertNotIn("seen", self.client.get("/api/welcome").json)  # admins only
        for bad in ({"id": "welcome"}, {"id": "welcome", "version": "1"}, {"version": 1}):
            self.assertEqual(self.client.post("/api/home/messages/dismiss", json=bad).status_code, 400, bad)
        self.assertEqual(self.dismiss(1, "other").status_code, 404)
        self.assertEqual(self.dismiss(1).status_code, 200)
        self.assertEqual(self.messages(), [])
        self.sign_in(1)
        self.assertEqual(self.client.get("/welcome").status_code, 200)

    def test_edits_keep_the_version_unless_shown_again(self):
        self.write("Helo lab.")
        self.sign_in(3)
        self.dismiss(1)
        self.write("Hello lab.")  # a typo fixed: not shown again
        self.sign_in(3)
        self.assertEqual(self.messages(), [])
        self.assertEqual(self.client.get("/api/welcome").json["message"]["version"], 1)
        self.write("News for everyone.", show_again=True)
        self.sign_in(3)
        self.assertEqual([m["version"] for m in self.messages()], [2])
        self.assertEqual(self.dismiss(99).status_code, 200)  # never beyond the current version
        self.sign_in(1)
        self.assertEqual(self.client.get("/api/welcome").json["seen"], 1)
        self.con.execute("UPDATE users SET is_active=0 WHERE id=3")  # only active accounts count
        self.con.commit()
        self.assertEqual(self.client.get("/api/welcome").json["seen"], 0)
        self.write("Newer.", show_again=True)
        self.sign_in(2)
        self.assertEqual([m["version"] for m in self.messages()], [3])
        self.write("")  # emptied: no welcome message
        self.sign_in(2)
        self.assertEqual(self.messages(), [])
        self.assertIsNone(self.client.get("/api/welcome").json["message"])

    def test_api_keys_cannot_dismiss(self):
        self.write("Hello lab.")
        original = self.ns["user_for_key"]
        self.addCleanup(self.ns.__setitem__, "user_for_key", original)
        self.ns["user_for_key"] = lambda token: self.con.execute("SELECT *, 0 AS key_read_only FROM users WHERE id=3"
                                                                 ).fetchone()
        self.client.post("/logout")
        r = self.client.post("/api/home/messages/dismiss", json={"id": "welcome", "version": 1},
                             headers={"Authorization": "Bearer test-only"})
        self.assertEqual(r.status_code, 403)

    def test_tags_roles_and_markup_like_the_access_mail(self):
        settings = patch.dict(os.environ, LOGDB_PERSON_PROFILE="1")
        settings.start()
        self.addCleanup(settings.stop)
        self.ns["profiles"].seed(self.con)
        role = {r["name"]: r["id"] for r in self.con.execute("SELECT id, name FROM profile_roles")}
        self.con.execute("INSERT INTO user_profiles (user_id, role_id, indefinite) VALUES (3, ?, 1)", (role["Staff"],))
        self.con.commit()
        text = ("Hello **{name}** ({role}), {training} <b>\n{if role: Staff}\n- staff line\n  indented\n{else}\n"
                "student line\n{end}\nSee https://example.org.")
        self.assertEqual(self.write(text).status_code, 200)
        self.sign_in(3)
        self.assertEqual(self.client.get("/api/home").json["messages"][0]["html"],
                         "<p>Hello <strong>Viewer</strong> (Staff), {training} &lt;b&gt;</p><ul><li>staff line</li></ul>"
                         '<div class="indent">indented</div><p>See <a href="https://example.org" target="_blank"'
                         ' rel="noopener">https://example.org</a>.</p>')
        self.sign_in(1)
        self.assertEqual(self.client.get("/api/welcome").json["text"], text)
        self.assertIn("{end}", self.write("{if role: Staff}\nx").json["error"])

    def test_only_admins_write_it(self):
        self.sign_in(3)
        self.assertEqual(self.client.put("/api/admin/welcome", json={"body": "x"}).status_code, 403)
        self.assertIn("at most", self.write("x" * 10001).json["error"])
        self.assertIn("true or false", self.write("x", show_again="yes").json["error"])


class NoticeTests(AppTests):
    """Admins' notices on the Home page (welcome.py): audience by profile role, show-until date, can't be closed.
    Users: 1 Admin, 2 Pilot, 3 Viewer, 4 Outsider."""

    def setUp(self):
        super().setUp()
        settings = patch.dict(os.environ, LOGDB_PERSON_PROFILE="1")
        settings.start()
        self.addCleanup(settings.stop)
        self.ns["profiles"].seed(self.con)
        self.role = {r["name"]: r["id"] for r in self.con.execute("SELECT id, name FROM profile_roles")}
        for uid, role in ((2, "Staff"), (3, "Master Student")):  # 4 has no profile: no role
            self.con.execute("INSERT INTO user_profiles (user_id, role_id, indefinite, number, department_text)"
                             " VALUES (?, ?, 1, '1', 'Lab')", (uid, self.role[role]))
        self.con.commit()

    def add(self, **body):
        self.sign_in(1)
        return self.client.post("/api/admin/notices", json={"title": "Hangar closed", "body": "Use **door B**.", **body})

    def ids(self, uid):
        self.sign_in(uid)
        return [m["id"] for m in self.client.get("/api/home").json["messages"]]

    def test_admins_only_and_validation(self):
        self.sign_in(2)
        self.assertEqual(self.client.post("/api/admin/notices", json={"title": "x", "body": "y"}).status_code, 403)
        self.assertEqual(self.client.get("/api/admin/notices").status_code, 403)
        for bad in ({"title": ""}, {"body": ""}, {"audience": "some"}, {"audience": "roles"},
                    {"audience": "roles", "role_ids": [999]}, {"until": "31-12-2030"}, {"until": "2030-02-30"},
                    {"closable": "no"}, {"body": "{if role: Staff}\nx"}, {"title": "x" * 121}):
            self.assertEqual(self.add(**bad).status_code, 400, bad)
        r = self.add(until="2099-01-01")
        self.assertEqual(r.status_code, 201, r.json)
        nid = r.json["notice"]["id"]
        listed = self.client.get("/api/admin/notices").json
        self.assertEqual([n["title"] for n in listed["notices"]], ["Hangar closed"])
        self.assertTrue(listed["profiles"])
        self.assertIn("Staff", [x["name"] for x in listed["roles"]])
        self.assertEqual(self.client.put(f"/api/admin/notices/{nid + 1}", json={"title": "a", "body": "b"}).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/admin/notices/{nid}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/admin/notices/{nid}").status_code, 404)
        self.assertEqual(self.ids(3), [])

    def test_audience_by_role(self):
        self.add(title="For all")
        self.add(title="Staff only", audience="roles", role_ids=[self.role["Staff"]])
        titles = lambda uid: [m["title"] for m in (self.sign_in(uid), self.client.get("/api/home").json["messages"])[1]]  # noqa: E731
        self.assertEqual(sorted(titles(2)), ["For all", "Staff only"])
        self.assertEqual(titles(3), ["For all"])
        self.assertEqual(titles(4), ["For all"])  # no role: only notices for everyone
        with patch.dict(os.environ, LOGDB_PERSON_PROFILE="0"):  # profiles off: only notices for everyone
            self.assertEqual(titles(2), ["For all"])

    def test_until_date_and_cannot_be_closed(self):
        past = self.add(title="Old", until="2000-01-01").json["notice"]
        self.assertTrue(past["expired"])
        fixed = self.add(title="Fixed", closable=False).json["notice"]
        self.add(title="Closable")
        self.sign_in(3)
        messages = self.client.get("/api/home").json["messages"]
        self.assertEqual([(m["title"], m["dismissible"]) for m in messages], [("Fixed", False), ("Closable", True)])
        self.assertEqual(messages[0]["html"], "<p>Use <strong>door B</strong>.</p>")
        r = self.client.post("/api/home/messages/dismiss", json={"id": f"notice-{fixed['id']}", "version": 1})
        self.assertEqual(r.status_code, 409)
        r = self.client.post("/api/home/messages/dismiss", json={"id": f"notice-{past['id']}", "version": 1})
        self.assertEqual(r.status_code, 404)  # ended: no longer shown, nothing to dismiss

    def test_dismiss_and_version(self):
        nid = self.add().json["notice"]["id"]
        self.sign_in(3)
        self.assertEqual(self.client.post("/api/home/messages/dismiss",
                                          json={"id": f"notice-{nid}", "version": 1}).status_code, 200)
        self.assertEqual(self.ids(3), [])
        self.sign_in(1)
        self.assertEqual(self.client.get("/api/admin/notices").json["notices"][0]["dismissed"], 1)
        same = {"title": "Hangar closed", "body": "Use **door B**.", "until": "2099-12-31"}
        self.assertEqual(self.client.put(f"/api/admin/notices/{nid}", json=same).json["notice"]["version"], 1)
        self.assertEqual(self.ids(3), [])  # only the date changed: stays dismissed
        self.sign_in(1)
        changed = self.client.put(f"/api/admin/notices/{nid}", json={**same, "body": "Use door C."}).json["notice"]
        self.assertEqual((changed["version"], changed["dismissed"]), (2, 0))
        self.assertEqual(self.ids(3), [f"notice-{nid}"])  # a new text: shown again

    def test_view_as_cannot_dismiss(self):
        nid = self.add().json["notice"]["id"]
        with patch.dict(os.environ, LOGDB_VIEW_AS="1"):
            self.sign_in(1)
            self.client.post("/api/view-as/3")
            r = self.client.post("/api/home/messages/dismiss", json={"id": f"notice-{nid}", "version": 1})
        self.assertEqual(r.status_code, 403)
        self.assertIsNone(self.con.execute("SELECT 1 FROM notice_dismissals").fetchone())
