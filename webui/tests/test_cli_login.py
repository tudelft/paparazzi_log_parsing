from unittest.mock import patch

from .test_historical import AppTests


class CliLoginTests(AppTests):
    """Browser login of scripts (RFC 8628 device flow) and the flight files lookup used by the clients."""

    def setUp(self):
        super().setUp()
        self.ns["_failures"].clear()
        self.addCleanup(self.ns["_failures"].clear)

    def start(self):
        with self.client.session_transaction() as session:
            session.clear()
        response = self.client.post("/api/cli-login", json={"client": "mavlab-logs (Python) on laptop"})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def poll(self, device_code):
        with patch.dict(self.ns, CLI_POLL_SECONDS=0):  # no slow_down between the polls of a test
            return self.client.post("/api/cli-login/token", data={"device_code": device_code})

    def test_approved_login_hands_over_an_expiring_key_once(self):
        start = self.start()
        self.assertNotIn(start["device_code"], start["verification_uri_complete"])
        self.assertTrue(start["verification_uri_complete"].endswith("/cli-login?code=" + start["user_code"]))
        self.assertEqual(self.poll(start["device_code"]).json["error"], "authorization_pending")
        # the approval page needs a signed-in browser and returns there after signing in
        page = self.client.get("/cli-login?code=" + start["user_code"])
        self.assertEqual(page.status_code, 302)
        self.assertIn("/login?next=%2Fcli-login%3Fcode%3D", page.location)
        self.sign_in(2)
        info = self.client.get("/api/cli-login/" + start["user_code"].replace("-", "").lower())
        self.assertEqual(info.json["client"], "mavlab-logs (Python) on laptop")
        self.assertEqual(self.client.post("/api/cli-login/" + start["user_code"], data={"approve": "1"}).status_code, 400)
        self.assertTrue(self.client.post("/api/cli-login/" + start["user_code"], json={"approve": True}).json["approved"])
        self.assertEqual(self.client.get("/api/cli-login/" + start["user_code"]).status_code, 404)
        token = self.poll(start["device_code"])
        self.assertEqual(token.status_code, 200, token.json)
        key = token.json["access_token"]
        self.assertEqual(token.json["user"]["id"], 2)
        self.assertEqual(self.poll(start["device_code"]).json["error"], "invalid_grant")
        with self.client.session_transaction() as session:
            session.clear()
        me = self.client.get("/api/me", headers={"Authorization": "Bearer " + key})
        self.assertEqual(me.json["user"]["id"], 2)
        row = self.con.execute("SELECT name, read_only, expires_at FROM api_keys WHERE user_id=2").fetchone()
        self.assertEqual((row["name"], row["read_only"]), ("Login: mavlab-logs (Python) on laptop", 0))
        self.assertEqual(row["expires_at"], token.json["expires_at"])
        # the server enforces the expiry, and the account page no longer lists the key
        self.con.execute("UPDATE api_keys SET expires_at='2000-01-01T00:00:00Z'")
        self.con.commit()
        self.assertEqual(self.client.get("/api/me", headers={"Authorization": "Bearer " + key}).status_code, 401)
        self.sign_in(2)
        self.assertEqual(self.client.get("/api/keys").json, [])

    def test_revoke_all_keeps_manual_keys_unless_asked(self):
        self.sign_in(2)
        manual = self.client.post("/api/keys", json={"name": "laptop"}).json["key"]
        read_only = self.client.post("/api/keys", json={"name": "ci", "read_only": True}).json["key"]
        self.con.execute("INSERT INTO api_keys (user_id, name, key_hash, prefix, created_at, expires_at)"
                         " VALUES (2, 'Login: x', 'h', 'logdb_', '2026-01-01T00:00:00Z', '2099-01-01T00:00:00Z')")
        self.con.execute("INSERT INTO api_keys (user_id, name, key_hash, prefix, created_at, expires_at)"
                         " VALUES (3, 'Login: y', 'h3', 'logdb_', '2026-01-01T00:00:00Z', '2099-01-01T00:00:00Z')")
        self.con.commit()
        with self.client.session_transaction() as session:
            session.clear()
        auth = lambda key: {"Authorization": "Bearer " + key}  # noqa: E731
        self.assertEqual(self.client.post("/api/keys/revoke-all", headers=auth(read_only)).status_code, 403)
        self.assertEqual(self.client.post("/api/keys/revoke-all", headers=auth(manual)).json["revoked"], 1)
        self.assertEqual(self.client.get("/api/me", headers=auth(manual)).status_code, 200)
        revoked = self.client.post("/api/keys/revoke-all", json={"include_manual": True}, headers=auth(manual))
        self.assertEqual(revoked.json["revoked"], 2)
        self.assertEqual(self.client.get("/api/me", headers=auth(manual)).status_code, 401)
        self.assertEqual(self.con.execute("SELECT user_id FROM api_keys").fetchall()[0][0], 3)  # other users keep theirs

    def test_denied_expired_and_repeated_logins(self):
        start = self.start()
        self.sign_in(3)
        self.client.post("/api/cli-login/" + start["user_code"], json={"approve": False})
        self.assertEqual(self.poll(start["device_code"]).json["error"], "access_denied")
        self.assertEqual(self.poll("wrong").json["error"], "invalid_grant")
        late = self.start()
        self.con.execute("UPDATE cli_logins SET expires_at='2000-01-01T00:00:00Z' WHERE user_code=?",
                         (late["user_code"],))
        self.con.commit()
        self.assertEqual(self.poll(late["device_code"]).json["error"], "expired_token")
        # polling too fast is slowed down
        fast = self.start()
        self.client.post("/api/cli-login/token", data={"device_code": fast["device_code"]})
        self.assertEqual(self.client.post("/api/cli-login/token", data={"device_code": fast["device_code"]})
                         .json["error"], "slow_down")
        # a second login of the same client replaces its key; API keys cannot approve logins
        keys = []
        for _ in range(2):
            again = self.start()
            self.sign_in(3)
            self.client.post("/api/cli-login/" + again["user_code"], json={"approve": True})
            keys.append(self.poll(again["device_code"]).json["access_token"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM api_keys WHERE user_id=3").fetchone()[0], 1)
        other = self.start()
        response = self.client.post("/api/cli-login/" + other["user_code"], json={"approve": True},
                                    headers={"Authorization": "Bearer " + keys[1]})
        self.assertEqual(response.status_code, 403)

    def test_safe_next(self):
        safe = self.ns["safe_next"]
        self.assertEqual(safe("/cli-login?code=BCDF-GHJK"), "/cli-login?code=BCDF-GHJK")
        for bad in (None, "", "https://example.org", "//example.org", "/\\example.org", "/x\nLocation: y"):
            self.assertEqual(safe(bad), "/")

    def test_flight_files_lists_the_original_files_of_a_visible_log(self):
        self.con.execute("INSERT INTO logs(id,name,sha256,data_path,log_path,status,uploaded_at,uploaded_by,project_id,"
                         "log_type,size_bytes) VALUES(1,'Hex/24_10_30__16_27_37_SD.data','abc','ab/abc/24_10_30__16_27_37_SD"
                         ".data.gz','ab/abc/24_10_30__16_27_37_SD.log.gz','done','now',2,2,'onboard_sd',4)")
        self.con.execute("INSERT INTO aircraft(id,log_id,drone_id,name,pic) VALUES(1,1,1,'Drone','pic')")
        self.con.execute("INSERT INTO flights(id,aircraft_id,idx,verdict) VALUES(7,1,0,'flight')")
        self.con.commit()
        self.sign_in(2)
        files = self.client.get("/api/flights/7/files").json
        self.assertEqual((files["log_id"], files["sha256"]), (1, "abc"))
        self.assertEqual([(f["kind"], f["name"], f["url"]) for f in files["files"]],
                         [("data", "24_10_30__16_27_37_SD.data", "/api/logs/1/download/data"),
                          ("log", "24_10_30__16_27_37_SD.log", "/api/logs/1/download/log")])
        self.sign_in(4)  # not in the log's project
        self.assertEqual(self.client.get("/api/flights/7/files").status_code, 404)
        self.assertEqual(self.client.get("/api/flights/8/files").status_code, 404)
