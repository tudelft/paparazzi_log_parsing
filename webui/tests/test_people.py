import hashlib

from werkzeug.security import check_password_hash, generate_password_hash

from .test_historical import AppTests


class UsernameTests(AppTests):
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
        self.assertEqual(self.con.execute("SELECT user_id FROM user_aliases WHERE alias='pic'").fetchone()[0], 2)
        self.assertEqual(self.con.execute("SELECT role FROM project_members WHERE user_id=2").fetchone()[0], "full")
        resolver = self.ns["person_resolver"](self.con)
        self.assertEqual(resolver("pic", "pic", None)["id"], 2)
        self.assertEqual(resolver("pic", "newpilot", None)["id"], 2)
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
        self.assertEqual(self.rename("RESERVED").status_code, 409)
        self.assertEqual(self.con.execute("SELECT username FROM users WHERE id=2").fetchone()[0], "pic")
        self.assertEqual(self.rename("PIC").status_code, 200)
        self.assertEqual(self.rename("PIC").status_code, 200)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM user_aliases WHERE user_id=2").fetchone()[0], 1)
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
