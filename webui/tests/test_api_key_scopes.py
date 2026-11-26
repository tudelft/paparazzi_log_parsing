import hashlib
import inspect
import io
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import db
import scopes

from .test_historical import AppTests

PDF = b"%PDF-1.4\nTest\n%%EOF\n"


class ApiKeyScopeTests(AppTests):
    """Issue 235: a key's scopes narrow what it may do; endpoints without a scope are refused (fail closed)."""

    def setUp(self):
        super().setUp()
        settings = patch.dict(os.environ, LOGDB_DOCUMENTS="1")
        settings.start()
        self.addCleanup(settings.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        old = self.app.config["DOCUMENTS_DIR"]
        self.app.config["DOCUMENTS_DIR"] = Path(folder.name)
        self.addCleanup(self.app.config.__setitem__, "DOCUMENTS_DIR", old)

    def new_key(self, uid, scope_list=None, **body):
        self.sign_in(uid)
        if scope_list is not None:
            body["scopes"] = scope_list
        r = self.client.post("/api/keys", json={"name": "script", **body})
        self.assertEqual(r.status_code, 200, r.json)
        return {"Authorization": "Bearer " + r.json["key"]}, r.json

    def old_key(self, uid, read_only):
        token = f"logdb_old{uid}{read_only}" + "k" * 30
        self.con.execute("INSERT INTO api_keys(user_id,name,key_hash,prefix,created_at,read_only) VALUES (?,?,?,?,?,?)",
                         (uid, f"old{read_only}", hashlib.sha256(token.encode()).hexdigest(), "logdb_old", "now",
                          read_only))
        self.con.commit()
        return {"Authorization": "Bearer " + token}

    def as_key(self, method, url, key, **kwargs):
        with self.client.session_transaction() as session:
            session.clear()
        return getattr(self.client, method)(url, headers=key, **kwargs)

    def document(self, key):
        return self.as_key("post", "/api/admin/documents", key, content_type="multipart/form-data",
                           data={"title": "Insurance", "file": (io.BytesIO(PDF), "insurance.pdf")})

    def test_each_scope_allows_its_routes_only(self):
        read, created = self.new_key(1, ["read"])
        self.assertEqual(created["scopes"], ["read"])
        self.assertEqual(self.as_key("get", "/api/me", read).status_code, 200)
        r = self.as_key("post", "/api/logs/check", read, json={"files": []})
        self.assertEqual((r.status_code, r.json["error"]), (403, "this API key does not have the logs:upload scope"))
        self.assertEqual(self.as_key("post", "/api/logs/1/matlab", read).status_code != 403, True)  # read-only POST

        upload, _ = self.new_key(1, ["logs:upload"])
        self.assertEqual(self.as_key("get", "/api/me", upload).status_code, 403)  # no read scope
        self.assertNotEqual(self.as_key("post", "/api/logs/check", upload, json={"files": []}).status_code, 403)
        self.assertEqual(self.as_key("put", "/api/flights/1/comment", upload, json={"comment": "x"}).status_code, 403)

        edit, _ = self.new_key(1, ["logs:edit"])
        self.assertNotEqual(self.as_key("put", "/api/flights/1/comment", edit, json={"comment": "x"}).status_code, 403)
        self.assertEqual(self.as_key("post", "/api/logs/check", edit, json={"files": []}).status_code, 403)

        docs, _ = self.new_key(1, ["read", "documents:upload"])
        r = self.document(docs)
        self.assertEqual(r.status_code, 201, r.json)
        did = r.json["id"]
        self.assertEqual(self.as_key("delete", f"/api/admin/documents/{did}", docs).status_code, 403)
        manage, _ = self.new_key(1, ["documents:manage"])
        self.assertEqual(self.document(manage).status_code, 403)
        self.assertEqual(self.as_key("put", f"/api/admin/documents/{did}", manage, json={"title": "Renamed"}).status_code, 200)
        self.assertEqual(self.as_key("delete", f"/api/admin/documents/{did}", manage).status_code, 200)

        place = {"name": "Field", "lat": 52, "lon": 4.4, "radius_m": 100}
        self.assertEqual(self.as_key("post", "/api/admin/places", docs, json=place).status_code, 403)
        admin, _ = self.new_key(1, ["admin"])
        self.assertNotEqual(self.as_key("post", "/api/admin/places", admin, json=place).status_code, 403)
        self.assertEqual(self.document(admin).status_code, 403)  # admin does not include documents

    def test_unmapped_and_session_routes_are_refused(self):
        everything, created = self.new_key(1, list(scopes.SCOPES))
        r = self.as_key("post", "/api/bookings", everything, json={})
        self.assertEqual((r.status_code, r.json["error"]), (403, "API keys with scopes cannot do this; use a signed-in browser"))
        self.assertEqual(self.as_key("delete", f"/api/keys/{created['id']}", everything).status_code, 403)
        self.assertEqual(self.as_key("post", "/api/keys/revoke-all", everything).status_code, 200)  # the clients' sign-out
        read, _ = self.new_key(1, ["read"])
        self.assertEqual(self.as_key("post", "/api/keys/revoke-all", read).status_code, 403)

    def test_every_write_endpoint_has_a_decision(self):
        """A new route that changes something must be added to scopes.py or to this list (refused to scoped keys)."""
        refused = {"account_email", "account_email_test", "add_booking", "add_operator", "approve_drone_request",
                   "change_password", "cli_login_decide", "cli_login_start", "cli_login_token", "confirm_reading",
                   "create_key", "decide_flight_link", "decide_source_link", "delete_booking", "delete_key",
                   "delete_log", "edit_booking", "flight_weather", "forget_flight_link", "historical_record",
                   "narrow_key", "new_calendar_link", "reject_drone_request", "remove_calendar_link", "request_drone",
                   "request_matlab_bulk", "review_qualification", "save_own_profile", "save_person_profile",
                   "setup_password", "submit_qualification", "read_qualification_evidence", "submit_training",
                   "unlink_attachment", "edit_attachment",
                   "update_logbook_records",
                   # Safety (safety.py): reports and the reporting text need a browser session
                   "create_report", "save_safety_settings",
                   # account routes that can lead to a sign-in or more rights: browser session only, for every key
                   "admin_add_user", "admin_new_setup_link", "admin_update_user", "admin_remove_password",
                   "admin_set_admin", "admin_rename_user", "admin_merge_users", "admin_move_sso", "admin_set_external",
                   "admin_set_user_operator", "admin_add_external", "admin_narrow_user_key", "admin_revoke_user_key",
                   "start_view_as", "stop_view_as",
                   # other administration that needs a browser session (also for old full keys)
                   "admin_ambiguous_drone_name", "admin_log_aircraft_link", "document_import_preview",
                   "document_import_commit", "add_booking_thing", "edit_booking_thing", "delete_booking_thing",
                   "order_booking_things", "save_profile_options", "import_profile_csv", "save_qualification_type",
                   "save_qualification_types", "edit_qualification_dates", "delete_qualification",
                   "check_qualification_qr", "fill_qualification_numbers", "qualification_import_preview",
                   "qualification_import_commit", "add_training", "edit_training", "delete_training",
                   "import_training", "reset_progress", "add_question", "edit_question", "delete_question",
                   "order_questions", "upload_question_image", "upload_reading_file", "remove_reading_file",
                   # training access extensions (training.py) and outdoor operations (operations.py): browser session
                   "request_extension", "withdraw_extension", "review_extension", "test_access_mail",
                   "operation_prepare", "plan_flight", "join_slot", "edit_team", "leave_slot", "move_slot", "review_team",
                   "link_flight", "set_external"}
        unmapped = {rule.endpoint for rule in self.app.url_map.iter_rules()
                    if rule.rule.startswith("/api/") and rule.methods - {"GET", "HEAD", "OPTIONS"}
                    and rule.endpoint not in scopes.READ_POSTS | scopes.WRITE_ANY
                    and scopes.scope_for(rule.endpoint) is None}
        self.assertEqual(unmapped, refused)
        mapped = set().union(*scopes.ENDPOINTS.values())
        self.assertEqual(mapped - set(self.app.view_functions), set())  # no misspelled endpoint names
        session_only = {e for e in mapped if "session_required()" in inspect.getsource(self.app.view_functions[e])}
        self.assertEqual(session_only, set())  # a session-only route belongs in the refused list, not in a scope

    def test_get_and_write_in_one_view_never_test_for_get_only(self):
        """HEAD runs a view like GET, and `read` allows it: a view that also writes must not treat "not GET" as a
        write (a HEAD request once accepted every date suggestion)."""
        methods = {}
        for rule in self.app.url_map.iter_rules():
            methods.setdefault(rule.endpoint, set()).update(rule.methods)
        for endpoint, used in methods.items():
            if "GET" in used and used - {"GET", "HEAD", "OPTIONS"}:
                source = inspect.getsource(self.app.view_functions[endpoint]).replace("'", '"')
                for bad in ('request.method == "GET"', 'request.method != "GET"'):
                    self.assertNotIn(bad, source, endpoint)
        read, _ = self.new_key(1, ["read"])
        self.assertEqual(self.as_key("head", "/api/admin/date-suggestions", read).status_code, 200)
        self.assertEqual(self.as_key("post", "/api/admin/date-suggestions", read, json={}).status_code, 403)

    def test_account_routes_need_a_browser_session_for_every_key(self):
        """Routes that return a setup link, change an email or remove a password could give a key holder a sign-in."""
        self.con.execute("UPDATE users SET email=lower(username)||'@example.org', password_hash='x' WHERE id=2")
        self.con.commit()
        calls = [("post", "/api/admin/users", {"username": "new", "email": "new@example.org", "is_admin": True}),
                 ("post", "/api/admin/users/2/setup-link", {}),
                 ("put", "/api/admin/users/2", {"email": "taken@example.org"}),
                 ("delete", "/api/admin/users/2/password", None)]
        scoped, _ = self.new_key(1, list(scopes.SCOPES))
        for key in (self.old_key(1, 0), scoped):
            for method, url, body in calls:
                self.assertEqual(self.as_key(method, url, key, json=body).status_code, 403, (method, url))
        self.assertIsNone(self.con.execute("SELECT 1 FROM users WHERE username='new'").fetchone())
        self.sign_in(1)
        for method, url, body in calls:
            self.assertEqual(getattr(self.client, method)(url, json=body).status_code, 200, (method, url))

    def test_document_import_needs_a_browser_session(self):
        manage, _ = self.new_key(1, ["read", "documents:upload", "documents:manage"])
        for key in (self.old_key(1, 0), manage):
            r = self.as_key("post", "/api/admin/documents/import", key, content_type="multipart/form-data",
                            data={"file": (io.BytesIO(b"PK"), "import.zip")})
            self.assertEqual(r.status_code, 403)
            self.assertEqual(self.as_key("post", "/api/admin/documents/import/commit", key,
                                         json={"token": "x"}).status_code, 403)
            self.assertEqual(self.as_key("get", "/api/admin/documents/import/template", key).status_code, 403)

    def test_old_keys_behave_as_before(self):
        full, read_only = self.old_key(1, 0), self.old_key(1, 1)
        self.assertNotEqual(self.as_key("post", "/api/logs/check", full, json={"files": []}).status_code, 403)
        self.assertEqual(self.as_key("delete", "/api/logs/999", full).status_code, 404)  # no scope needed: reaches the route
        self.assertEqual(self.document(full).status_code, 403)  # documents: browser session or a documents scope
        r = self.as_key("post", "/api/logs/check", read_only, json={"files": []})
        self.assertEqual((r.status_code, r.json["error"]), (403, "this API key is read-only"))
        self.sign_in(1)
        listed = {k["name"]: k["scopes"] for k in self.client.get("/api/keys").json}
        self.assertEqual(listed, {"old0": None, "old1": ["read"]})  # None: shown as full (old)

    def test_creating_and_narrowing(self):
        self.sign_in(2)
        r = self.client.post("/api/keys", json={"name": "x", "scopes": ["read", "admin"]})
        self.assertEqual((r.status_code, r.json["error"]),
                         (400, "only administrators can give a key documents:upload, documents:manage, admin"))
        for bad in ([], ["write"], "read", [1]):
            self.assertEqual(self.client.post("/api/keys", json={"name": "x", "scopes": bad}).status_code, 400, bad)
        self.assertEqual(self.client.post("/api/keys", json={"name": "x"}).json["scopes"], ["read"])  # default
        self.assertEqual(self.client.post("/api/keys", json={"name": "y", "read_only": True}).json["scopes"], ["read"])
        self.assertEqual(self.client.post("/api/keys", json={"name": "z", "read_only": False}).json["scopes"], ["read"])
        key, created = self.new_key(2, ["read", "logs:upload"])
        url = f"/api/keys/{created['id']}"
        self.assertEqual(self.client.put(url, json={"scopes": ["read", "logs:edit"]}).status_code, 400)  # no widening
        self.assertEqual(self.client.put(url, json={"scopes": ["read"]}).json["scopes"], ["read"])
        self.assertEqual(self.as_key("post", "/api/logs/check", key, json={"files": []}).status_code, 403)
        self.assertEqual(self.as_key("put", url, key, json={"scopes": ["read"]}).status_code, 403)  # keys cannot
        old = self.old_key(2, 0)
        old_id = self.con.execute("SELECT id FROM api_keys WHERE name='old0'").fetchone()[0]
        self.sign_in(1)  # an administrator narrows someone's old full key
        admin_url = f"/api/admin/users/2/keys/{old_id}"
        self.assertEqual(self.client.put(admin_url, json={"scopes": ["read", "admin"]}).status_code, 400)
        self.assertEqual(self.client.put(admin_url, json={"scopes": ["read", "logs:upload", "logs:edit"]}).status_code,
                         200)  # an old full key of a non-administrator: the three scopes such a person may use
        self.assertEqual(self.client.put(admin_url, json={"scopes": ["read"]}).json["scopes"], ["read"])
        self.assertEqual(self.as_key("post", "/api/logs/check", old, json={"files": []}).status_code, 403)
        self.sign_in(1)
        self.assertEqual({k["id"]: k["scopes"] for k in self.client.get("/api/admin/users/2/keys").json}[old_id], ["read"])
        self.sign_in(2)
        self.assertEqual(self.client.put(admin_url, json={"scopes": ["read"]}).status_code, 403)

    def test_narrowing_an_old_full_key_never_adds_documents_scopes(self):
        self.old_key(1, 0)
        old_id = self.con.execute("SELECT id FROM api_keys WHERE name='old0'").fetchone()[0]
        self.sign_in(1)
        for url in (f"/api/keys/{old_id}", f"/api/admin/users/1/keys/{old_id}"):
            for extra in (["documents:upload"], ["documents:manage"]):
                r = self.client.put(url, json={"scopes": ["read", "admin"] + extra})
                self.assertEqual(r.status_code, 400, (url, extra))
        self.assertEqual(self.client.put(f"/api/keys/{old_id}", json={"scopes": list(scopes.SCOPES)}).status_code, 400)
        self.assertEqual(self.client.put(f"/api/keys/{old_id}", json={"scopes": scopes.old_full(True)}).json["scopes"],
                         ["read", "logs:upload", "logs:edit", "admin"])

    def test_migration_adds_the_column_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "logs.db")
            con = sqlite3.connect(path)
            con.executescript("CREATE TABLE users (id INTEGER PRIMARY KEY);"
                              "CREATE TABLE api_keys (id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT, key_hash TEXT,"
                              " prefix TEXT, created_at TEXT, last_used_at TEXT, expires_at TEXT);"
                              "INSERT INTO api_keys VALUES (1, 1, 'old', 'h', 'logdb_', 'now', NULL, NULL);"
                              "INSERT INTO api_keys VALUES (2, 1, 'Login: python', 'h2', 'logdb_', 'now', NULL,"
                              " '2999-01-01T00:00:00Z');")
            con.commit()
            con.close()
            for _ in range(2):
                db.init(path)
            con = sqlite3.connect(path)
            self.assertEqual(con.execute("SELECT read_only, scopes FROM api_keys ORDER BY id").fetchall(),
                             [(0, None), (0, "read logs:upload logs:edit")])  # browser-login keys get their scopes
            con.close()
