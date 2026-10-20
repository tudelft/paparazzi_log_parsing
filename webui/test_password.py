import ast
import sqlite3
import unittest
from pathlib import Path

from flask import Flask, abort, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

import db


class PasswordTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(db.SCHEMA)
        for uid, provider in ((1, "local"), (2, "saml")):
            self.con.execute(
                "INSERT INTO users(id, username, provider, password_hash, created_at) VALUES (?,?,?,?,?)",
                (uid, provider, provider, generate_password_hash("old-password"), "2026-01-01"))
        self.con.execute(
            "INSERT INTO password_setup_tokens(user_id, token_hash, created_at, expires_at) VALUES (1,'token','now','later')")
        self.con.commit()
        self.uid, self.admin, self.key = 1, False, False
        self.failures = []
        self.app = Flask(__name__)

        @self.app.before_request
        def user():
            g.user = self.con.execute("SELECT * FROM users WHERE id=?", (self.uid,)).fetchone()

        def session_required():
            if self.key:
                abort(403)

        def admin_required():
            session_required()
            if not self.admin:
                abort(403)

        namespace = dict(app=self.app, request=request, jsonify=jsonify, g=g, conn=lambda: self.con,
                         session_required=session_required, admin_required=admin_required,
                         too_many_failures=lambda k: len(self.failures) >= 8,
                         note_failure=self.failures.append, now=lambda: "now",
                         check_password_hash=check_password_hash, generate_password_hash=generate_password_hash,
                         issue_setup_token=lambda con, uid: ("new-token", "later"),
                         setup_url=lambda token: "/setup/" + token, db=db)
        tree = ast.parse(Path(__file__).with_name("app.py").read_text())
        names = {"change_password", "admin_new_setup_link", "admin_remove_password"}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.assertEqual(len(nodes), len(names))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), namespace)
        self.client = self.app.test_client()

    def tearDown(self):
        self.con.close()

    def change(self, **kwargs):
        return self.client.post("/api/account/password",
                                json={"current_password": "old-password", "password": "new-password", **kwargs})

    def test_change_and_invalidate_setup_links(self):
        self.assertEqual(self.change().status_code, 200)
        row = self.con.execute("SELECT password_hash FROM users WHERE id=1").fetchone()
        self.assertTrue(check_password_hash(row[0], "new-password"))
        self.assertFalse(check_password_hash(row[0], "old-password"))
        self.assertIsNotNone(self.con.execute("SELECT used_at FROM password_setup_tokens").fetchone()[0])

    def test_validation_and_wrong_current_password(self):
        for password in ("short", None, 42, "x" * 1025):
            self.assertEqual(self.change(password=password).status_code, 400)
        self.assertEqual(self.change(current_password="wrong").status_code, 403)
        self.assertEqual(len(self.failures), 1)
        self.failures = [1] * 8
        self.assertEqual(self.change().status_code, 429)

    def test_saml_and_keys_cannot_change_password(self):
        self.uid = 2
        self.assertEqual(self.change().status_code, 403)
        self.uid, self.key = 1, True
        self.assertEqual(self.change().status_code, 403)

    def test_admin_reset_permissions_and_local_filter(self):
        self.assertEqual(self.client.post("/api/admin/users/1/setup-link").status_code, 403)
        self.admin = True
        self.assertEqual(self.client.post("/api/admin/users/1/setup-link").status_code, 200)
        self.assertEqual(self.client.post("/api/admin/users/2/setup-link").status_code, 403)
        self.assertEqual(self.client.post("/api/admin/users/999/setup-link").status_code, 404)
        self.key = True
        self.assertEqual(self.client.post("/api/admin/users/1/setup-link").status_code, 403)

    def test_remove_password_keeps_profile_and_invalidates_links(self):
        self.admin = True
        response = self.client.delete("/api/admin/users/1/password")
        self.assertEqual(response.status_code, 200)
        row = self.con.execute("SELECT * FROM users WHERE id=1").fetchone()
        self.assertIsNone(row["password_hash"])
        self.assertEqual(row["username"], "local")
        self.assertEqual(row["provider"], "local")
        self.assertIsNotNone(self.con.execute("SELECT used_at FROM password_setup_tokens").fetchone()[0])

    def test_remove_password_permissions_and_last_admin(self):
        self.assertEqual(self.client.delete("/api/admin/users/1/password").status_code, 403)
        self.admin, self.key = True, True
        self.assertEqual(self.client.delete("/api/admin/users/1/password").status_code, 403)
        self.key = False
        self.assertEqual(self.client.delete("/api/admin/users/2/password").status_code, 400)
        self.assertEqual(self.client.delete("/api/admin/users/999/password").status_code, 404)
        self.con.execute("UPDATE users SET is_admin=1 WHERE id=1")
        self.con.commit()
        self.assertEqual(self.client.delete("/api/admin/users/1/password").status_code, 400)
        self.con.execute("UPDATE users SET is_admin=1 WHERE id=2")
        self.con.commit()
        self.assertEqual(self.client.delete("/api/admin/users/1/password").status_code, 200)


if __name__ == "__main__":
    unittest.main()
