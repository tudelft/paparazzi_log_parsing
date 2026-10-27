import json
import unittest

from .test_historical import AppTests


class AccountMergeTests(AppTests):
    """Admins merge two accounts of one person and move a wrongly matched TU Delft sign-in."""

    def setUp(self):
        super().setUp()
        con = self.con
        # user 5 ("Uploader") is a second account of user 2 ("Pilot"), created by a TU Delft login
        con.execute("UPDATE users SET provider='saml', subject='netid-pilot', email='pilot@example.org' WHERE id=5")
        con.execute("UPDATE users SET password_hash='hash' WHERE id=2")
        con.execute("INSERT INTO project_members(project_id,user_id,role) VALUES (2,5,'view')")
        con.execute("INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by,project_id)"
                    " VALUES (1,'a.data','s1','a.gz','done','now',5,1), (2,'b.data','s2','b.gz','done','now',2,1)")
        con.execute("INSERT INTO log_uploaders(log_id,user_id,uploaded_at) VALUES (2,5,'now')")
        con.execute("INSERT INTO aircraft(id,log_id,name,ac_id) VALUES (1,1,'Drone',5)")
        con.execute("INSERT INTO flights(id,aircraft_id,idx,pic_user_id,gcs_user_id) VALUES (1,1,0,5,2)")
        con.execute("INSERT INTO user_aliases(user_id,alias) VALUES (5,'P. Lot')")
        con.execute("INSERT INTO api_keys(user_id,name,key_hash,prefix,created_at) VALUES (5,'script','h','logdb_x','now')")
        con.execute("INSERT INTO qualification_types(id,name,requires_evidence) VALUES (1,'A2',0)")
        con.executemany("INSERT INTO qualification_submissions(id,user_id,type_id,valid_until,status,submitted_at)"
                        " VALUES (?,?,1,?,'approved','now')", [(1, 2, "2026-01-01"), (2, 5, "2028-01-01")])
        con.commit()
        self.sign_in(1)

    def preview(self, source, into):
        return self.client.get(f"/api/admin/users/{source}/merge?into={into}")

    def test_merge_moves_everything_and_keeps_both_sign_ins(self):
        plan = self.preview(5, 2).json
        self.assertEqual((plan["crew"], plan["uploads"], plan["api_keys"]), ({"pic": 1, "pac": 0, "gcs": 0}, 2, 1))
        self.assertEqual(plan["aliases"], ["P. Lot", "Uploader"])
        self.assertEqual(plan["sign_in"], "password and TU Delft")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM users WHERE id=5").fetchone()[0], 1)  # preview only
        response = self.client.post("/api/admin/users/5/merge", json=dict(into=2))
        self.assertEqual(response.status_code, 200, response.json)
        con = self.con
        self.assertIsNone(con.execute("SELECT 1 FROM users WHERE id=5").fetchone())
        kept = con.execute("SELECT * FROM users WHERE id=2").fetchone()
        self.assertEqual((kept["provider"], kept["subject"], kept["password_hash"], kept["email"]),
                         ("local", "netid-pilot", "hash", "pilot@example.org"))
        self.assertEqual(con.execute("SELECT pic_user_id FROM flights WHERE id=1").fetchone()[0], 2)
        self.assertEqual(con.execute("SELECT uploaded_by FROM logs WHERE id=1").fetchone()[0], 2)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM log_uploaders").fetchone()[0], 0)  # 2 uploaded it first
        self.assertEqual(dict(con.execute("SELECT project_id, role FROM project_members WHERE user_id=2").fetchall()),
                         {1: "full", 2: "view"})
        self.assertEqual(dict(con.execute("SELECT id, status FROM qualification_submissions").fetchall()),
                         {1: "superseded", 2: "approved"})  # the one valid longest stays
        self.assertEqual(con.execute("SELECT user_id FROM api_keys").fetchone()[0], 2)
        self.assertEqual(sorted(r[0] for r in con.execute("SELECT alias FROM user_aliases WHERE user_id=2")),
                         ["P. Lot", "Uploader"])
        audit = con.execute("SELECT subject_id, actor_id, action, details_json FROM qualification_audit").fetchone()
        self.assertEqual((audit[0], audit[1], audit[2]), (2, 1, "account_merged"))
        self.assertEqual(json.loads(audit[3])["merged_user"], {"id": 5, "name": "Uploader"})

    def test_merge_refusals(self):
        self.assertEqual(self.preview(2, 2).status_code, 400)
        self.assertEqual(self.preview(1, 2).status_code, 400)  # your own account cannot be merged away
        self.con.execute("UPDATE users SET subject='netid-other' WHERE id=2")
        self.con.commit()
        self.assertIn("different TU Delft", self.preview(5, 2).json["error"])
        self.con.execute("UPDATE users SET subject=NULL, is_external=1 WHERE id=3")
        self.con.commit()
        self.assertEqual(self.client.post("/api/admin/users/3/merge", json=dict(into=4)).status_code, 400)
        self.sign_in(2)
        self.assertEqual(self.preview(5, 3).status_code, 403)  # admins only

    def test_move_tu_delft_sign_in_to_another_or_a_new_account(self):
        move = lambda source, **body: self.client.post(f"/api/admin/users/{source}/sso-move", json=body)  # noqa: E731
        self.assertEqual(move(3, to=4).status_code, 400)  # no TU Delft sign-in on 3
        response = move(5, to=3, clear_email=True)
        self.assertEqual(response.status_code, 200, response.json)
        con = self.con
        self.assertEqual(tuple(con.execute("SELECT subject, provider, email FROM users WHERE id=3").fetchone()),
                         ("netid-pilot", "saml", "pilot@example.org"))
        self.assertEqual(tuple(con.execute("SELECT subject, provider, email FROM users WHERE id=5").fetchone()),
                         (None, "local", None))
        self.assertEqual(move(3, to=2).status_code, 200)  # the password stays a local account with TU Delft
        self.assertEqual(con.execute("SELECT provider FROM users WHERE id=2").fetchone()[0], "local")
        con.execute("UPDATE users SET subject='netid-x' WHERE id=4")
        con.commit()
        self.assertEqual(move(2, to=4).status_code, 409)  # 4 already has one
        self.assertEqual(move(2, to=None, username="admin").status_code, 409)
        response = move(2, to=None, username="pilot-tud", display_name="P. Pilot")
        new = con.execute("SELECT * FROM users WHERE id=?", (response.json["user_id"],)).fetchone()
        self.assertEqual((new["username"], new["display_name"], new["subject"], new["provider"]),
                         ("pilot-tud", "P. Pilot", "netid-pilot", "saml"))
        with self.app.test_request_context():
            user, error = self.ns["login_saml_identity"](dict(subject="netid-pilot", email="pilot@example.org"))
        self.assertEqual((user["id"], error), (new["id"], None))  # the next TU Delft login goes there
        self.assertEqual([r[0] for r in con.execute("SELECT action FROM qualification_audit")], ["sso_moved"] * 3)


if __name__ == "__main__":
    unittest.main()
