import os
from unittest.mock import patch

from .test_historical import AppTests


class ProfileTests(AppTests):
    """Person profiles (LOGDB_PERSON_PROFILE): asked at sign-in, visible to the person, admins and supervisors."""

    def setUp(self):
        super().setUp()
        settings = patch.dict(os.environ, LOGDB_PERSON_PROFILE="1")
        settings.start()
        self.addCleanup(settings.stop)
        self.profiles = self.ns["profiles"]
        self.profiles.seed(self.con)
        self.con.commit()
        self.role = {r["name"]: r["id"] for r in self.con.execute("SELECT id, name FROM profile_roles")}
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM profile_departments").fetchone()[0], 0)  # site-specific
        self.dept = self.con.execute("INSERT INTO profile_departments (name, grp) VALUES ('Flight Lab', 'Robotics')").lastrowid

    def form(self, **changes):
        return {"role_id": self.role["Staff"], "department_id": self.dept, "end_date": None, "indefinite": True,
                "number": "123", "github": "", "supervisors": [], **changes}

    def save(self, uid, **changes):
        self.sign_in(uid)
        return self.client.put("/api/profile", json=self.form(**changes))

    def test_pages_wait_for_a_complete_profile_but_the_api_does_not(self):
        self.sign_in(3)
        page = self.client.get("/logbook?x=1")
        self.assertEqual(page.status_code, 302)
        self.assertEqual(page.headers["Location"], "/profile?next=%2Flogbook%3Fx%3D1")
        self.assertEqual(self.client.get("/api/me").status_code, 200)
        self.assertEqual(self.client.get("/theme.css").status_code, 200)
        self.assertEqual(self.save(3).status_code, 200)
        self.assertEqual(self.client.get("/logbook").status_code, 200)
        # asked again once the expected end date has passed
        self.con.execute("UPDATE user_profiles SET indefinite=0, end_date='2000-01-01' WHERE user_id=3")
        self.con.commit()
        self.assertEqual(self.client.get("/logbook").status_code, 302)
        with patch.dict(os.environ, LOGDB_PERSON_PROFILE="0"):
            self.assertEqual(self.client.get("/logbook").status_code, 200)
            self.assertEqual(self.client.get("/api/profile").status_code, 404)

    def test_validation(self):
        self.assertIn("supervisor", self.save(2, role_id=self.role["PhD Student"]).json["error"])
        self.assertIn("end date", self.save(2, indefinite=False, end_date="2000-01-01").json["error"])
        self.assertIn("number", self.save(2, number=" ").json["error"])
        self.assertIn("GitHub", self.save(2, github="-bad-").json["error"])
        self.assertIn("department", self.save(2, department_id=None).json["error"])
        self.assertEqual(self.save(2, department_id=None, department_text="Elsewhere").status_code, 200)
        # only people whose role can supervise are offered (and accepted) as supervisors
        self.save(5, role_id=self.role["Extern"])
        refused = self.save(2, role_id=self.role["PhD Student"], supervisors=[{"id": 5}])
        self.assertEqual(refused.status_code, 400)

    def test_supervisors_see_their_students_without_the_number(self):
        self.save(5)  # Staff: can supervise
        saved = self.save(2, role_id=self.role["Master Student"], indefinite=False, end_date="2099-01-01",
                          supervisors=[{"id": 5}, {"name": "Dr. Partner"}])
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual(saved.json["missing"], [])
        self.assertEqual([s["name"] for s in saved.json["profile"]["supervisors"]], ["Uploader", "Dr. Partner"])
        self.sign_in(5)
        seen = self.client.get("/api/users/2/profile").json["profile"]
        self.assertEqual(seen["role"], "Master Student")
        self.assertNotIn("number", seen)
        self.assertEqual([s["id"] for s in self.client.get("/api/profile").json["students"]], [2])
        self.sign_in(3)
        self.assertEqual(self.client.get("/api/users/2/profile").status_code, 404)
        self.sign_in(1)
        self.assertEqual(self.client.get("/api/users/2/profile").json["profile"]["number"], "123")

    def test_admins_edit_the_lists(self):
        self.save(2)
        options = self.client.get("/api/profile").json["options"]
        self.assertEqual(self.client.put("/api/admin/profile-options", json={"roles": []}).status_code, 403)
        self.sign_in(1)
        roles = [r for r in options["roles"] if r["name"] != "Staff"] + [{"name": "Visitor"}]
        saved = self.client.put("/api/admin/profile-options", json={"roles": roles})
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual([r["name"] for r in saved.json["roles"]][-1], "Visitor")
        self.assertEqual([d["name"] for d in saved.json["departments"]], ["Flight Lab"])
        emptied = self.client.put("/api/admin/profile-options", json={"departments": []})
        self.assertEqual(emptied.status_code, 200, emptied.json)
        self.assertEqual(emptied.json["departments"], [])
        self.assertEqual(self.client.put("/api/admin/profile-options", json={"roles": []}).status_code, 400)
        # a removed role empties that field, so the person is asked again
        self.assertEqual(self.profiles.missing(self.con, 2), ["role", "department"])
        duplicate = self.client.put("/api/admin/profile-options", json={"roles": [{"name": "A"}, {"name": "a"}]})
        self.assertEqual(duplicate.status_code, 400)

    def test_github_check(self):
        self.sign_in(2)
        self.assertEqual(self.client.get("/api/profile/github?name=-x").json["status"], "invalid")
        with patch.object(self.profiles, "github_status", return_value="found") as check:
            self.assertEqual(self.client.get("/api/profile/github?name=octocat").json["status"], "found")
        check.assert_called_once_with("octocat")

    def test_migration_seeds_the_lists_once(self):
        db = self.ns["db"]
        self.con.execute("DROP TABLE user_supervisors")
        self.con.execute("DROP TABLE user_profiles")
        self.con.execute("DROP TABLE profile_roles")
        self.con.execute("DROP TABLE profile_departments")
        with patch.object(db, "connect", return_value=self.con):
            db.init("unused")
            self.con.execute("DELETE FROM profile_roles WHERE name='Extern'")
            db.init("unused")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM profile_roles").fetchone()[0], 6)

    def test_lists_follow_the_visibility_rules(self):
        self.save(5)  # Staff: supervises user 2
        self.save(2, role_id=self.role["Master Student"], indefinite=False, end_date="2099-01-01",
                  supervisors=[{"id": 5}])
        # user 2 is in projects 1 (Research) and, here, 2 (Private); user 5 only in Research
        self.con.execute("INSERT INTO project_members(project_id,user_id,role) VALUES (2,2,'view')")
        self.con.commit()
        self.sign_in(1)
        everyone = {p["id"]: p for p in self.client.get("/api/profiles").json["people"]}
        self.assertEqual(set(everyone), {1, 2, 3, 4, 5})
        self.assertEqual(everyone[2]["profile"]["number"], "123")
        self.assertEqual([p["name"] for p in everyone[2]["projects"]], ["Private", "Research"])
        self.assertEqual(everyone[3]["missing"], ["profile"])
        self.sign_in(5)
        own = {p["id"]: p for p in self.client.get("/api/profiles").json["people"]}
        self.assertEqual(set(own), {2, 5})
        self.assertNotIn("number", own[2]["profile"])
        self.assertEqual([p["name"] for p in own[2]["projects"]], ["Research"])  # not a project the supervisor cannot open
        page = self.client.get("/api/users/5/profile").json
        self.assertEqual([s["id"] for s in page["students"]], [2])
        self.assertTrue(page["self"])
        self.sign_in(3)
        self.assertEqual([p["id"] for p in self.client.get("/api/profiles").json["people"]], [3])
        self.assertEqual(self.client.get("/api/users/5/profile").status_code, 404)

    def test_admins_edit_someone_elses_profile(self):
        body = self.form(role_id=self.role["Master Student"], indefinite=False, end_date="2099-01-01",
                         supervisors=[{"name": "Dr. Partner"}], number="777")
        self.sign_in(2)
        self.assertEqual(self.client.put("/api/users/3/profile", json=body).status_code, 403)
        self.assertNotIn("options", self.client.get("/api/users/2/profile").json)  # own view, no admin form data
        self.sign_in(1)
        form = self.client.get("/api/users/3/profile").json
        self.assertEqual(form["name"], "Viewer")
        self.assertIn("roles", form["options"])
        saved = self.client.put("/api/users/3/profile", json=body)
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual((saved.json["profile"]["number"], saved.json["missing"]), ("777", []))
        self.assertEqual(self.client.put("/api/users/3/profile", json={**body, "number": ""}).status_code, 400)  # same rules
        self.assertEqual(self.client.put("/api/users/999/profile", json=body).status_code, 404)

    def test_csv_import_fills_only_empty_fields_and_asks_to_confirm(self):
        self.save(5)  # Uploader: Staff, can supervise; number 123
        self.sign_in(1)
        csv_text = ("username,role,department,supervisors,end_date,number,github\n"
                    "viewer,Master Student,Other lab,uploader;Dr. Partner,2099-01-01,555,\n"
                    "uploader,PhD Student,,,,999,octocat\n"
                    "outsider,,,,,,\n")
        preview = self.client.post("/api/admin/profiles/import", json={"csv": csv_text}).json
        self.assertEqual(preview["errors"], 0)
        rows = {r["username"]: r for r in preview["rows"]}
        self.assertEqual((rows["viewer"]["status"], rows["viewer"]["changes"]),
                         ("new", ["role", "department", "end date", "number", "supervisors"]))
        self.assertEqual((rows["uploader"]["status"], rows["uploader"]["changes"]), ("update", ["GitHub"]))  # role and number kept
        self.assertEqual(rows["outsider"]["status"], "unchanged")
        self.assertIsNone(self.con.execute("SELECT 1 FROM user_profiles WHERE user_id=3").fetchone())  # preview only
        applied = self.client.post("/api/admin/profiles/import", json={"csv": csv_text, "apply": True}).json
        self.assertTrue(applied["applied"])
        viewer = self.profiles.profile(self.con, 3)
        self.assertEqual((viewer["role"], viewer["department"], viewer["number"], viewer["to_confirm"]),
                         ("Master Student", "Other lab", "555", True))
        self.assertEqual([s["name"] for s in viewer["supervisors"]], ["Uploader", "Dr. Partner"])
        uploader = self.profiles.profile(self.con, 5)
        self.assertEqual((uploader["role"], uploader["number"], uploader["github"]), ("Staff", "123", "octocat"))
        # complete but imported: the person still checks it at the next sign-in, and saving it confirms
        self.sign_in(3)
        self.assertEqual(self.client.get("/logbook").status_code, 302)
        self.assertEqual(self.client.put("/api/profile", json=self.form(
            role_id=self.role["Master Student"], indefinite=False, end_date="2099-01-01", number="555",
            supervisors=[{"id": 5}])).status_code, 200)
        self.assertEqual(self.client.get("/logbook").status_code, 200)

    def test_csv_import_errors_block_the_whole_file(self):
        self.sign_in(1)
        bad = "username,role,end_date\nviewer,Wizard,\nnobody,,\npic,,31-12-2030\n"
        result = self.client.post("/api/admin/profiles/import", json={"csv": bad, "apply": True}).json
        self.assertEqual((result["errors"], result["applied"]), (3, False))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM user_profiles").fetchone()[0], 0)
        self.assertEqual(self.client.post("/api/admin/profiles/import", json={"csv": "name\nx\n"}).status_code, 400)
        self.sign_in(2)
        self.assertEqual(self.client.post("/api/admin/profiles/import", json={"csv": bad}).status_code, 403)
