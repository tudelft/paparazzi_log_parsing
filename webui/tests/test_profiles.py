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
        self.soon = self.profiles.latest_end(1)  # within the end date limit of every student role

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
        saved = self.save(2, role_id=self.role["Master Student"], indefinite=False, end_date=self.soon,
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

    def test_end_date_limits_per_role(self):
        latest = self.profiles.latest_end
        self.assertEqual(latest(4, "2024-02-29"), "2028-02-29")
        self.assertEqual(latest(1, "2024-02-29"), "2025-02-28")
        phd = dict(role_id=self.role["PhD Student"], supervisors=[{"name": "Dr. Partner"}])
        self.assertIn("cannot be indefinite", self.save(2, **phd).json["error"])
        self.assertIn("at most 5 years", self.save(2, **phd, indefinite=False, end_date="2999-01-01").json["error"])
        self.assertEqual(self.save(2, **phd, indefinite=False, end_date=latest(5)).status_code, 200)
        self.assertEqual(self.save(2, indefinite=True).status_code, 200)  # Staff may be indefinite
        roles = {r["name"]: r for r in self.client.get("/api/profile").json["options"]["roles"]}
        self.assertEqual((roles["Bachelor Student"]["latest_end"], roles["Master Student"]["latest_end"]),
                         (latest(1), latest(2)))
        self.assertEqual([n for n, r in roles.items() if r["allow_indefinite"]], ["Staff", "Extern"])
        # a profile outside new limits is asked again
        self.assertEqual(self.profiles.missing(self.con, 2), [])
        self.con.execute("UPDATE profile_roles SET allow_indefinite=0 WHERE name='Staff'")
        self.assertEqual(self.profiles.missing(self.con, 2), ["end_date"])

    def test_admins_may_set_an_end_date_outside_the_limits(self):
        master = dict(role_id=self.role["Master Student"], supervisors=[{"name": "Dr. Partner"}])
        self.assertEqual(self.save(2, **master, indefinite=False, end_date=self.soon).status_code, 200)
        self.sign_in(1)  # an odd case: the admin sets indefinite for a Master student
        r = self.client.put("/api/users/2/profile", json=self.form(**master))
        self.assertEqual(r.status_code, 200, r.json)
        self.assertNotIn("end_date", self.profiles.missing(self.con, 2))  # the person is not asked to correct it
        self.sign_in(2)
        self.assertEqual(self.save(2, **master, number="999").status_code, 200)  # unchanged: may save other fields
        self.assertTrue(self.profiles.profile(self.con, 2)["end_date_by_admin"])
        far = self.profiles.latest_end(3)
        self.assertIn("at most 2 years", self.save(2, **master, indefinite=False, end_date=far).json["error"])
        self.assertEqual(self.save(2, **master, indefinite=False, end_date=self.soon).status_code, 200)
        self.assertFalse(self.profiles.profile(self.con, 2)["end_date_by_admin"])  # their own date: limits hold again
        self.sign_in(1)  # an admin changing only another field leaves the person's date to the limits
        self.assertEqual(self.client.put("/api/users/2/profile", json=self.form(
            **master, indefinite=False, end_date=self.soon, number="111")).status_code, 200)
        self.assertFalse(self.profiles.profile(self.con, 2)["end_date_by_admin"])

    def test_admins_set_the_limits(self):
        self.sign_in(1)
        roles = self.client.get("/api/profile").json["options"]["roles"]
        for r in roles:
            r.update(max_years="3" if r["name"] == "Extern" else r["max_years"], allow_indefinite=r["allow_indefinite"])
        saved = self.client.put("/api/admin/profile-options", json={"roles": roles})
        self.assertEqual(saved.status_code, 200, saved.json)
        extern = next(r for r in saved.json["roles"] if r["name"] == "Extern")
        self.assertEqual((extern["max_years"], extern["allow_indefinite"]), (3, True))
        for bad in ("0", "1.5", "x", 51, True):
            roles[0]["max_years"] = bad
            self.assertIn("whole number", self.client.put("/api/admin/profile-options", json={"roles": roles}).json["error"])

    def test_migration_adds_the_limits_to_the_default_roles(self):
        db = self.ns["db"]
        for table in ("document_profile_roles", "user_supervisors", "user_profiles", "profile_roles"):
            self.con.execute(f"DROP TABLE {table}")
        self.con.execute("CREATE TABLE profile_roles (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,"
                         " can_supervise INTEGER NOT NULL DEFAULT 0, needs_supervisor INTEGER NOT NULL DEFAULT 0,"
                         " sort_order INTEGER NOT NULL DEFAULT 0)")
        self.con.executemany("INSERT INTO profile_roles (name) VALUES (?)", [("master student",), ("Staff",), ("Visitor",)])
        with patch.object(db, "connect", return_value=self.con):
            db.init("unused")
            self.con.execute("UPDATE profile_roles SET max_years=9 WHERE name='Staff'")
            db.init("unused")  # only once: admins' changes stay
        self.assertEqual([tuple(r) for r in self.con.execute(
            "SELECT name, max_years, allow_indefinite FROM profile_roles ORDER BY id")],
            [("master student", 2, 0), ("Staff", 9, 1), ("Visitor", None, 1)])

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
        self.save(2, role_id=self.role["Master Student"], indefinite=False, end_date=self.soon,
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
        body = self.form(role_id=self.role["Master Student"], indefinite=False, end_date=self.soon,
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
        self.assertEqual(self.client.put("/api/users/3/profile", json={**body, "github": "-bad-"}).status_code, 400)
        # an admin fills in only what they know; the person is asked for the rest
        partial = self.client.put("/api/users/4/profile", json={"department_text": "Elsewhere", "supervisors": []})
        self.assertEqual(partial.status_code, 200, partial.json)
        self.assertEqual(partial.json["missing"], ["role", "end_date", "number"])
        self.assertTrue(partial.json["profile"]["to_confirm"])
        self.assertEqual(self.client.put("/api/users/4/profile", json={"end_date": "2000-01-01"}).status_code, 200)
        self.assertEqual(self.client.put("/api/users/999/profile", json=body).status_code, 404)

    def test_csv_import_fills_only_empty_fields_and_asks_to_confirm(self):
        self.save(5)  # Uploader: Staff, can supervise; number 123
        self.sign_in(1)
        csv_text = ("username,role,department,supervisors,end_date,number,github\n"
                    f"viewer,Master Student,Other lab,uploader;Dr. Partner,{self.soon},555,\n"
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
            role_id=self.role["Master Student"], indefinite=False, end_date=self.soon, number="555",
            supervisors=[{"id": 5}])).status_code, 200)
        self.assertEqual(self.client.get("/logbook").status_code, 200)

    def test_csv_import_flags_end_dates_outside_the_limits(self):
        self.sign_in(1)
        csv_text = f"username,role,end_date\nviewer,Master Student,indefinite\npic,Bachelor Student,{self.soon}\n"
        rows = {r["username"]: r for r in self.client.post("/api/admin/profiles/import", json={"csv": csv_text}).json["rows"]}
        self.assertIn("cannot be indefinite", rows["viewer"]["outside_limits"])
        self.assertIsNone(rows["pic"]["outside_limits"])
        self.assertTrue(self.client.post("/api/admin/profiles/import", json={"csv": csv_text, "apply": True}).json["applied"])
        self.assertNotIn("end_date", self.profiles.missing(self.con, 3))  # an admin's import: kept, not asked again

    def test_csv_import_errors_block_the_whole_file(self):
        self.sign_in(1)
        bad = "username,role,end_date\nviewer,Wizard,\nnobody,,\npic,,31-12-2030\n"
        result = self.client.post("/api/admin/profiles/import", json={"csv": bad, "apply": True}).json
        self.assertEqual((result["errors"], result["applied"]), (3, False))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM user_profiles").fetchone()[0], 0)
        self.assertEqual(self.client.post("/api/admin/profiles/import", json={"csv": "name\nx\n"}).status_code, 400)
        self.sign_in(2)
        self.assertEqual(self.client.post("/api/admin/profiles/import", json={"csv": bad}).status_code, 403)
