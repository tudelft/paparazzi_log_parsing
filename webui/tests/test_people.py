import hashlib
import io
import unittest
import unittest.mock
from types import SimpleNamespace

from werkzeug.security import check_password_hash, generate_password_hash

import saml
from .test_historical import AppTests
from .test_links import LinkFixtures


class PeoplePicFlightsTests(LinkFixtures):
    def test_pic_rows_use_merged_visible_flights_and_paper_totals(self):
        self.flight(1, "12:00:00")
        self.flight(2, "12:00:01", kind="gcs")
        self.flight(3, "15:00:00")
        self.con.execute("UPDATE flights SET pic_user_id=2 WHERE id IN (1,2)")
        self.con.execute("UPDATE flights SET pic_user_id=3,pac_user_id=2 WHERE id=3")
        self.con.commit()
        self.create(kind="daily", date="2010-01-02", flight_count=4, duration_minutes=60)
        response = self.client.get("/api/people-pic-flights")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])
        rows = response.json["flights"]
        self.assertEqual(len(rows), 3)
        pilot = [r for r in rows if r["user_id"] == 2]
        self.assertEqual(len(pilot), 2)
        self.assertEqual(sum(r["flight_count"] for r in pilot), 5)
        self.assertEqual(sum(r["duration_s"] for r in pilot), 3700)
        self.assertTrue(all("date_ok" in r and "simulation" in r for r in rows))
        self.sign_in(4)
        self.assertEqual(self.client.get("/api/people-pic-flights").json["flights"], [])


class SamlAttributeTests(unittest.TestCase):
    def test_supported_attribute_spellings(self):
        for uid_key, display_key in (
                ("uid", "displayName"), ("UID", "Displayname"),
                ("urn:oid:0.9.2342.19200300.100.1.1", "urn:oid:2.16.840.1.113730.3.1.241")):
            with self.subTest(uid=uid_key, display=display_key):
                attrs = {uid_key: ["examplepilot"], display_key: ["Example Pilot"],
                         "mail": ["pilot@example.test"]}
                auth = SimpleNamespace(get_attributes=lambda: attrs, get_nameid=lambda: "stable-subject")
                self.assertEqual(saml.identity_from_auth(auth), {
                    "subject": "stable-subject", "username": "examplepilot",
                    "display_name": "Example Pilot", "email": "pilot@example.test"})


class PeopleDirectoryTests(AppTests):
    def test_admin_people_exposes_enabled_state_for_login_badges(self):
        self.sign_in(1)
        self.con.execute("UPDATE users SET is_active=0 WHERE id=4")
        self.con.commit()
        users = {u["id"]: u for u in self.client.get("/api/admin/people").json["users"]}
        self.assertTrue(users[2]["is_active"])
        self.assertFalse(users[4]["is_active"])
        self.assertFalse(users[2]["has_password"])
        self.assertEqual(users[2]["provider"], "local")

    def test_admin_role_changes_and_self_demotion_guard(self):
        url = "/api/admin/users/2/admin"
        self.assertEqual(self.client.put(url, json={"is_admin": True}).status_code, 403)
        self.sign_in(1)
        self.assertEqual(self.client.put(url, json={"is_admin": True}).status_code, 200)
        self.sign_in(2)
        self.assertTrue(self.client.get("/api/me").json["user"]["is_admin"])
        self.assertEqual(self.client.put(url, json={"is_admin": False}).status_code, 400)
        self.sign_in(1)
        self.assertEqual(self.client.put(url, json={"is_admin": False}).status_code, 200)
        self.sign_in(2)
        self.assertFalse(self.client.get("/api/me").json["user"]["is_admin"])
        self.assertEqual(self.client.get("/api/admin/people").status_code, 403)
        self.assertEqual(self.con.execute("SELECT role FROM project_members WHERE user_id=2").fetchone()[0], "full")

    def test_admin_role_validation_and_last_admin(self):
        self.sign_in(1)
        for body in (None, [], {}, {"is_admin": 1}, {"is_admin": "true"}):
            self.assertEqual(self.client.put("/api/admin/users/2/admin", json=body).status_code, 400)
        self.assertEqual(self.client.put("/api/admin/users/999/admin", json={"is_admin": True}).status_code, 404)
        self.assertEqual(self.client.put("/api/admin/users/1/admin", json={"is_admin": False}).status_code, 400)
        self.con.execute("UPDATE users SET is_external=1 WHERE id=3")
        self.con.execute("UPDATE users SET is_active=0 WHERE id=4")
        self.con.commit()
        for uid in (3, 4):
            self.assertEqual(self.client.put(
                f"/api/admin/users/{uid}/admin", json={"is_admin": True}).status_code, 400)
            self.assertFalse(self.con.execute("SELECT is_admin FROM users WHERE id=?", (uid,)).fetchone()[0])

    def test_admin_key_details_and_scoped_revocation(self):
        created = self.client.post("/api/keys", json={"name": "Uploader"})
        self.assertEqual(created.status_code, 200)
        key = created.json
        self.sign_in(3)
        other = self.client.post("/api/keys", json={"name": "Other"}).json
        self.sign_in(1)
        response = self.client.get("/api/admin/users/2/keys")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json), 1)
        self.assertEqual(set(response.json[0]), {"id", "name", "prefix", "created_at", "last_used_at", "read_only"})
        self.assertEqual(response.json[0]["name"], "Uploader")
        self.assertIsNone(response.json[0]["last_used_at"])
        self.assertNotIn(key["key"], response.get_data(as_text=True))
        self.assertEqual(self.client.get("/api/admin/users/999/keys").status_code, 404)
        self.assertEqual(self.client.get("/api/admin/users/4/keys").json, [])
        self.assertEqual(self.client.delete(f"/api/admin/users/3/keys/{key['id']}").status_code, 404)
        self.assertEqual(self.client.get("/api/me", headers={
            "Authorization": "Bearer " + key["key"]}).status_code, 200)
        self.assertIsNotNone(self.client.get("/api/admin/users/2/keys").json[0]["last_used_at"])
        self.assertEqual(self.client.delete(f"/api/admin/users/2/keys/{key['id']}").status_code, 200)
        self.assertEqual(self.client.get("/api/me", headers={
            "Authorization": "Bearer " + key["key"]}).status_code, 401)
        self.assertEqual(self.client.delete(f"/api/admin/users/2/keys/{key['id']}").status_code, 404)
        self.assertIsNotNone(self.con.execute("SELECT id FROM api_keys WHERE id=?", (other["id"],)).fetchone())

    def test_admin_roles_and_keys_require_admin_browser_session(self):
        self.sign_in(1)
        key = self.client.post("/api/keys", json={"name": "Admin"}).json
        headers = {"Authorization": "Bearer " + key["key"]}
        routes = (("get", "/api/admin/users/2/keys", None),
                  ("delete", f"/api/admin/users/1/keys/{key['id']}", None),
                  ("put", "/api/admin/users/2/admin", {"is_admin": True}))
        for uid, auth in ((1, headers), (2, {})):
            self.sign_in(uid)
            for method, url, body in routes:
                with self.subTest(uid=uid, method=method):
                    self.assertEqual(getattr(self.client, method)(url, json=body, headers=auth).status_code, 403)
        self.assertIsNotNone(self.con.execute("SELECT id FROM api_keys WHERE id=?", (key["id"],)).fetchone())
        self.assertFalse(self.con.execute("SELECT is_admin FROM users WHERE id=2").fetchone()[0])

    def test_optional_email_on_creation_and_update(self):
        self.sign_in(1)
        for index, fields in enumerate(({}, {"email": ""}, {"email": "  "}, {"email": None})):
            response = self.client.post("/api/admin/users", json={"username": f"person{index}", **fields})
            self.assertEqual(response.status_code, 200, response.json)
            self.assertIsNone(self.con.execute(
                "SELECT email FROM users WHERE id=?", (response.json["id"],)).fetchone()[0])
        for email in ("invalid", 42, [], "bad @example.test"):
            self.assertEqual(self.client.post("/api/admin/users", json={
                "username": "invalid-email", "email": email}).status_code, 400)
            self.assertEqual(self.client.put("/api/admin/users/2", json={"email": email}).status_code, 400)
        self.assertEqual(self.client.put("/api/admin/users/2", json={"email": "pilot@example.test"}).status_code, 200)
        self.assertEqual(self.client.put("/api/admin/users/3", json={"email": "PILOT@example.test"}).status_code, 409)
        self.assertEqual(self.client.post("/api/admin/users", json={
            "username": "duplicate-email", "email": "PILOT@example.test"}).status_code, 409)
        self.assertEqual(self.client.put("/api/admin/users/2", json={"email": ""}).status_code, 200)
        self.assertIsNone(self.con.execute("SELECT email FROM users WHERE id=2").fetchone()[0])

    def test_log_names_stay_unlinked_until_admin_adds_aliases(self):
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by)"
            " VALUES(1,'test','crew-test','test.data','done','now',2)")
        self.con.execute(
            "INSERT INTO aircraft(id,log_id,name,pic,pac,gcs)"
            " VALUES(1,1,'Drone','Example','Example Pilot','Unlinked operator')")
        self.con.commit()
        self.sign_in(1)
        self.ns["assign_log_flights"](self.con, 1)
        response = self.client.get("/api/admin/people")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json["users"]), 5)
        self.assertEqual({r["name"] for r in response.json["unlinked"]},
                         {"Example", "Example Pilot", "Unlinked operator"})
        for alias in ("Example", "Example Pilot"):
            response = self.client.post("/api/admin/aliases", json={"user_id": 2, "alias": alias})
            self.assertEqual(response.status_code, 200)
        resolve = self.ns["person_resolver"](self.con)
        self.assertEqual(resolve("pic", "Example", None)["id"], 2)
        self.assertEqual(resolve("pac", "Example Pilot", None)["id"], 2)
        self.assertIsNone(resolve("gcs", "Unlinked operator", None))
        response = self.client.get("/api/admin/people")
        self.assertEqual([r["name"] for r in response.json["unlinked"]], ["Unlinked operator"])
        self.assertEqual(self.client.delete("/api/admin/users/2").status_code, 200)
        response = self.client.get("/api/admin/people")
        self.assertEqual(len(response.json["users"]), 4)
        self.assertEqual({r["name"] for r in response.json["unlinked"]},
                         {"Example", "Example Pilot", "Unlinked operator"})

    def test_people_and_aircraft_report_whether_they_have_logs(self):
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by)"
            " VALUES(1,'test','has-logs','test.data','done','now',5)")
        self.con.execute("INSERT INTO aircraft(id,log_id,name,pic,pac,gcs,drone_id) VALUES(1,1,'Drone','Pilot','Unknown',NULL,1)")
        self.con.execute("INSERT INTO drones(id,name,unique_id,created_at) VALUES (2,'Spare','UID2','now')")
        self.con.commit()
        self.sign_in(1)
        admin = {u["id"]: u["has_logs"] for u in self.client.get("/api/admin/people").json["users"]}
        self.assertEqual(admin, {1: False, 2: True, 3: False, 4: False, 5: True})
        directory = {u["id"]: u["has_logs"] for u in self.client.get("/api/people-directory").json["users"]}
        self.assertTrue(directory[2] and directory[5] and not directory[3])
        drones = {d["id"]: (d["log_count"], d["record_count"]) for d in self.client.get("/api/drones").json["drones"]}
        self.assertEqual(drones, {1: (1, 0), 2: (0, 0)})

    def test_unlinked_names_can_be_added_as_external_people(self):
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by)"
            " VALUES(1,'test','crew-test','test.data','done','now',2)")
        self.con.execute("INSERT INTO aircraft(id,log_id,name,pic,pac,gcs) VALUES(1,1,'Drone','Guest Pilot','Pilot',NULL)")
        self.con.commit()
        self.sign_in(1)
        self.ns["assign_log_flights"](self.con, 1)
        response = self.client.post("/api/admin/people/external", json={"name": " Guest Pilot "})
        self.assertEqual(response.status_code, 201, response.json)
        uid = response.json["id"]
        user = self.con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        self.assertEqual((user["username"], user["display_name"], user["password_hash"], user["is_external"]),
                         ("GuestPilot", "Guest Pilot", None, 1))
        self.assertEqual(self.ns["person_resolver"](self.con)("pic", "guest pilot", None)["id"], uid)
        people = self.client.get("/api/admin/people").json
        self.assertEqual(people["unlinked"], [])
        self.assertTrue({u["id"]: u for u in people["users"]}[uid]["is_external"])
        for name in ("Guest Pilot", "Pilot", ""):
            self.assertIn(self.client.post("/api/admin/people/external", json={"name": name}).status_code, (400, 409))
        self.assertEqual(self.client.post(f"/api/admin/users/{uid}/setup-link").status_code, 403)
        self.assertEqual(self.client.put("/api/admin/projects/1/members", json={"user_id": uid, "role": "view"}).status_code, 400)
        upload = self.client.post("/api/admin/projects/import", data={"file": (
            io.BytesIO(b"project,person,role\nResearch,Guest Pilot,view\n"), "projects.csv")})
        self.assertEqual(upload.status_code, 400)
        self.assertIn("is external", upload.json["error"])

        self.assertEqual(self.client.put("/api/admin/users/1/external", json={"is_external": True}).status_code, 400)
        self.assertEqual(self.client.put("/api/admin/users/2/external", json={"is_external": "yes"}).status_code, 400)
        self.assertEqual(self.client.put("/api/admin/users/2/external", json={"is_external": True}).status_code, 200)
        self.assertFalse(self.con.execute("SELECT 1 FROM project_members WHERE user_id=2").fetchone())
        self.sign_in(2)
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.sign_in(1)
        self.assertEqual(self.client.put("/api/admin/users/2/external", json={"is_external": False}).status_code, 200)
        self.sign_in(2)
        self.assertEqual(self.client.get("/api/me").status_code, 200)

    def test_aliases_visible_to_normal_users_within_directory_scope(self):
        self.con.executemany("INSERT INTO user_aliases(user_id,alias) VALUES (?,?)",
                             [(2, "Pilot alias"), (2, "<crew>"), (4, "Private alias")])
        self.con.commit()
        self.sign_in(3)
        response = self.client.get("/api/people-directory")
        self.assertEqual(response.status_code, 200)
        users = {u["id"]: u for u in response.json["users"]}
        self.assertEqual(users[2]["aliases"], ["<crew>", "Pilot alias"])
        self.assertEqual(users[3]["aliases"], [])
        self.assertNotIn(4, users)
        self.sign_in(1)
        response = self.client.get("/api/admin/people")
        self.assertEqual(response.status_code, 200)
        users = {u["id"]: u for u in response.json["users"]}
        self.assertEqual(users[2]["aliases"], ["<crew>", "Pilot alias"])


class UsernameTests(AppTests):
    def test_log_matching_uses_unique_display_names_and_explicit_aliases(self):
        self.con.execute("UPDATE users SET username='piet',display_name='Piet ter Baas' WHERE id=2")
        self.con.execute("UPDATE users SET username='pietj',display_name='Piet van der Plas' WHERE id=3")
        resolve = self.ns["person_resolver"](self.con)
        self.assertIsNone(resolve("pic", "Piet", None))
        self.assertIsNone(resolve("pic", "pietj", None))
        self.assertEqual(resolve("pic", "PIET TER BAAS", None)["id"], 2)
        self.assertEqual(resolve("pic", "Piet van der Plas", None)["id"], 3)
        self.con.execute("UPDATE users SET display_name='piet ter baas' WHERE id=3")
        self.con.execute("UPDATE users SET display_name=NULL WHERE id=4")
        resolve = self.ns["person_resolver"](self.con)
        self.assertIsNone(resolve("pic", "Piet ter Baas", None))
        self.assertIsNone(resolve("pic", "outsider", None))
        self.con.execute("INSERT INTO user_aliases(user_id,alias) VALUES(2,'Piet')")
        self.con.execute("INSERT INTO user_aliases(user_id,alias) VALUES(3,'Piet ter Baas')")
        resolve = self.ns["person_resolver"](self.con)
        self.assertEqual(resolve("pic", "piet", None)["id"], 2)
        self.assertEqual(resolve("pic", "Piet ter Baas", None)["id"], 3)
        rows = [{"pic": "Piet", "pic_user_id": 3}]
        self.ns["add_people"](rows, resolve)
        self.assertEqual(rows[0]["pic_user"]["id"], 3)
        self.assertEqual(resolve("pic", "", 4)["id"], 4)

    def test_combined_names_save_is_atomic_and_preserves_matching(self):
        body = {"username": "newpilot", "display_name": "New Pilot",
                "email": "new@example.test", "default_project_id": 1}
        self.assertEqual(self.client.put("/api/admin/users/2/profile", json=body).status_code, 403)
        self.sign_in(1)
        for invalid in ({**body, "display_name": "x" * 81}, {**body, "username": "admin"},
                        {**body, "email": "invalid"}, {**body, "default_project_id": 999}):
            self.assertIn(self.client.put("/api/admin/users/2/profile", json=invalid).status_code, (400, 404, 409))
            row = self.con.execute("SELECT username,display_name FROM users WHERE id=2").fetchone()
            self.assertEqual(tuple(row), ("pic", "Pilot"))
            self.assertIsNone(self.con.execute("SELECT email FROM users WHERE id=2").fetchone()[0])
        self.assertEqual(self.client.put("/api/admin/users/2/profile", json=body).status_code, 200)
        row = self.con.execute("SELECT username,display_name FROM users WHERE id=2").fetchone()
        self.assertEqual(tuple(row), ("newpilot", "New Pilot"))
        self.assertEqual(tuple(self.con.execute(
            "SELECT email,default_project_id FROM users WHERE id=2").fetchone()), ("new@example.test", 1))
        resolve = self.ns["person_resolver"](self.con)
        for name in ("Pilot", "New Pilot"):
            self.assertEqual(resolve("pic", name, None)["id"], 2)
        # Saving only a new email address does not redo the project of every open flight.
        calls = []
        original = self.ns["assign_open_flights"]
        with unittest.mock.patch.dict(self.ns, assign_open_flights=lambda con: calls.append(1) or original(con)):
            self.assertEqual(self.client.put("/api/admin/users/2/profile",
                                             json={**body, "email": "other@example.test"}).status_code, 200)
            self.assertEqual(calls, [])
            self.assertEqual(self.client.put("/api/admin/users/2/profile",
                                             json={**body, "default_project_id": None}).status_code, 200)
            self.assertEqual(calls, [1])
        for name in ("pic", "newpilot"):
            self.assertIsNone(resolve("pic", name, None))
        self.assertEqual(self.client.put("/api/admin/users/2/profile", json={
            **body, "display_name": "", "email": "", "default_project_id": None}).status_code, 200)
        self.assertIsNone(self.con.execute("SELECT display_name FROM users WHERE id=2").fetchone()[0])

    def rename(self, name, uid=2):
        return self.client.put(f"/api/admin/users/{uid}/username", json={"username": name})

    def test_admin_rename_preserves_identity_and_log_matching(self):
        self.con.execute("UPDATE users SET password_hash=?,provider='local' WHERE id=2",
                         (generate_password_hash("test-password"),))
        self.con.commit()
        self.sign_in(1)
        self.assertEqual(self.rename("newpilot").status_code, 200)
        row = self.con.execute("SELECT * FROM users WHERE id=2").fetchone()
        self.assertEqual(row["username"], "newpilot")
        self.assertTrue(check_password_hash(row["password_hash"], "test-password"))
        self.assertIsNone(self.con.execute("SELECT user_id FROM user_aliases WHERE alias='pic'").fetchone())
        self.assertEqual(self.con.execute("SELECT role FROM project_members WHERE user_id=2").fetchone()[0], "full")
        resolver = self.ns["person_resolver"](self.con)
        self.assertIsNone(resolver("pic", "pic", None))
        self.assertIsNone(resolver("pic", "newpilot", None))
        self.assertEqual(resolver("pic", "Pilot", None)["id"], 2)
        self.sign_in(2)
        self.assertEqual(self.client.get("/api/me").json["user"]["username"], "newpilot")
        self.assertEqual(self.client.post("/login", json={
            "username": "pic", "password": "test-password"}).status_code, 401)
        self.assertEqual(self.client.post("/login", json={
            "username": "newpilot", "password": "test-password"}).status_code, 200)

    def test_permissions_validation_and_conflicts(self):
        self.assertEqual(self.rename("changed").status_code, 403)
        self.sign_in(1)
        for body in ([], {}, {"username": "new", "email": "extra@example.test"}):
            self.assertEqual(self.client.put("/api/admin/users/2/username", json=body).status_code, 400)
        for name in (None, 123, [], "", "with spaces", "x" * 41, "bad\x00name", "bad\x7fname"):
            self.assertEqual(self.rename(name).status_code, 400)
        self.assertEqual(self.rename("ADMIN").status_code, 409)
        self.assertEqual(self.rename("new", uid=999).status_code, 404)
        self.con.execute("INSERT INTO user_aliases(user_id,alias) VALUES (3,'reserved')")
        self.con.commit()
        self.assertEqual(self.rename("RESERVED").status_code, 200)
        self.assertEqual(self.ns["person_resolver"](self.con)("pic", "reserved", None)["id"], 3)
        self.assertEqual(self.rename("PIC").status_code, 200)
        self.assertEqual(self.rename("PIC").status_code, 200)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM user_aliases WHERE user_id=2").fetchone()[0], 0)
        self.con.execute("INSERT INTO api_keys(user_id,name,key_hash,prefix,created_at)"
                         " VALUES (1,'test',?,'test','now')", (hashlib.sha256(b"logdb_rename-test").hexdigest(),))
        self.con.commit()
        response = self.client.put("/api/admin/users/2/username", json={"username": "forbidden"},
                                   headers={"Authorization": "Bearer logdb_rename-test"})
        self.assertEqual(response.status_code, 403)

    def test_sso_rename_preserves_subject(self):
        self.con.execute("UPDATE users SET provider='saml',subject='test-subject' WHERE id=2")
        self.con.commit()
        self.sign_in(1)
        self.assertEqual(self.rename("renamed-sso").status_code, 200)
        with self.app.test_request_context():
            user, error = self.ns["login_saml_identity"](
                dict(subject="test-subject", username="pic", display_name="Pilot"))
        self.assertIsNone(error)
        self.assertEqual(user["id"], 2)
        self.assertEqual(user["username"], "renamed-sso")
