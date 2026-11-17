"""Silent TU Delft check on the login page (SAML IsPassive)."""
import ast
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlparse

from flask import Flask, g, jsonify, redirect, request, send_from_directory, session


class FakeAuth:
    def __init__(self, ok=True, reason=None):
        self.ok, self.reason, self.login_args = ok, reason, None

    def login(self, return_to=None, is_passive=False):
        self.login_args = (return_to, is_passive)
        return "https://idp.example/sso?" + urlencode({"RelayState": return_to, "passive": int(is_passive)})

    def process_response(self):
        pass

    def get_errors(self):
        return [] if self.ok else ["invalid_response"]

    def is_authenticated(self):
        return self.ok

    def get_last_error_reason(self):
        return self.reason


class SamlPassiveTests(unittest.TestCase):
    def setUp(self):
        self.pages = tempfile.TemporaryDirectory()
        (Path(self.pages.name) / "login.html").write_text("login page")
        self.enabled, self.passive = True, True
        self.auth = FakeAuth()
        self.start_fails = False
        self.logins = []
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        fake_saml = SimpleNamespace(
            saml_enabled=lambda: self.enabled,
            passive_enabled=lambda: self.enabled and self.passive,
            auth_for=self.auth_for,
            identity_from_auth=lambda auth: {"subject": "s1", "email": "a@example.org"},
            metadata_url=lambda: "https://idp.example/metadata")

        def login_saml_identity(ident):
            self.logins.append(ident)
            session.clear()
            session["uid"] = 1
            return {"id": 1}, None

        namespace = dict(app=self.app, request=request, session=session, redirect=redirect, jsonify=jsonify, g=g,
                         urlencode=urlencode, send_from_directory=send_from_directory, PAGES_DIR=self.pages.name,
                         DATA_DIR=Path(self.pages.name), saml=fake_saml, login_saml_identity=login_saml_identity,
                         conn=lambda: self, user_for_key=lambda key: None, scopes=None, profiles=None)
        tree = ast.parse((Path(__file__).resolve().parents[1] / "app.py").read_text())
        funcs = {"safe_next", "passive_fallback", "login_page", "saml_login", "saml_acs", "saml_sls", "logout",
                 "require_login"}
        consts = {"SSO_CHECKED", "PASSIVE_RELAY", "PUBLIC_ENDPOINTS"}
        nodes = [n for n in tree.body
                 if (isinstance(n, ast.FunctionDef) and n.name in funcs)
                 or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) in consts for t in n.targets))]
        self.assertEqual(len(nodes), len(funcs) + len(consts))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), namespace)

        @self.app.get("/")
        def index():
            return "home"

        @self.app.get("/flights")
        def flights():
            return "flights"

        self.client = self.app.test_client()

    def tearDown(self):
        self.pages.cleanup()

    def execute(self, sql, params=()):  # conn() for require_login: user 1 exists and is active
        return SimpleNamespace(fetchone=lambda: {"id": 1} if params == (1,) else None)

    def auth_for(self, req, data_dir):
        if self.start_fails:
            raise OSError("metadata unreachable")
        return self.auth

    def location(self, resp):
        self.assertEqual(resp.status_code, 302, resp.data)
        return resp.headers["Location"]

    def test_first_visit_starts_a_silent_check_and_keeps_next(self):
        loc = self.location(self.client.get("/login?next=/flights"))
        self.assertTrue(loc.startswith("/login/tudelft?"))
        self.assertEqual(parse_qs(urlparse(loc).query), {"passive": ["1"], "next": ["/flights"]})
        self.location(self.client.get(loc))
        self.assertEqual(self.auth.login_args, ("passive:/flights", True))

    def test_second_visit_shows_the_login_page(self):
        self.client.get("/login")
        resp = self.client.get("/login")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, b"login page")

    def test_local_error_ready_and_fallback_show_the_page(self):
        for query in ("local=1", "error=sso", "ready=1", "sso=none"):
            with self.subTest(query=query):
                self.client = self.app.test_client()
                self.assertEqual(self.client.get("/login?" + query).status_code, 200)

    def test_disabled_shows_the_page(self):
        self.passive = False
        self.assertEqual(self.client.get("/login").status_code, 200)

    def test_button_login_is_not_passive(self):
        self.location(self.client.get("/login/tudelft?next=/flights"))
        self.assertEqual(self.auth.login_args, ("/flights", False))

    def test_tudelft_session_signs_in_and_returns_to_next(self):
        loc = self.location(self.client.post("/saml/acs", data={"RelayState": "passive:/flights"}))
        self.assertEqual(loc, "/flights")
        self.assertEqual(len(self.logins), 1)

    def test_no_tudelft_session_shows_login_page_without_error(self):
        self.auth = FakeAuth(ok=False, reason="The status code of the Response was not Success, was Responder")
        with self.assertLogs(self.app.logger, "INFO") as logs:
            loc = self.location(self.client.post("/saml/acs", data={"RelayState": "passive:/flights"}))
        self.assertEqual(urlparse(loc).path, "/login")
        self.assertEqual(parse_qs(urlparse(loc).query), {"sso": ["none"], "next": ["/flights"]})
        self.assertTrue(all(r.levelname == "INFO" for r in logs.records))
        self.assertEqual(self.logins, [])

    def test_failed_button_login_still_reports_an_error(self):
        self.auth = FakeAuth(ok=False, reason="bad signature")
        with self.assertLogs(self.app.logger, "WARNING"):
            loc = self.location(self.client.post("/saml/acs", data={"RelayState": "/flights"}))
        self.assertEqual(loc, "/login?error=sso")

    def test_relay_state_cannot_redirect_elsewhere(self):
        for relay in ("passive://evil.example", "//evil.example", "https://evil.example"):
            with self.subTest(relay=relay):
                self.assertEqual(self.location(self.client.post("/saml/acs", data={"RelayState": relay})), "/")

    def test_failed_start_of_silent_check_shows_the_page(self):
        self.start_fails = True
        with self.assertLogs(self.app.logger, "ERROR"):
            loc = self.location(self.client.get("/login/tudelft?passive=1&next=/"))
        self.assertEqual(loc, "/login?sso=none")

    def test_sign_out_is_not_undone_by_a_silent_check(self):
        with self.client.session_transaction() as s:
            s["uid"] = 1
        self.assertEqual(self.client.post("/logout").status_code, 200)
        with self.client.session_transaction() as s:
            self.assertNotIn("uid", s)
        self.assertEqual(self.client.get("/login").status_code, 200)

    def test_flag_survives_clearing_an_anonymous_session(self):
        self.client.get("/login")  # silent check done
        loc = self.location(self.client.get("/flights"))  # require_login clears the session
        self.assertTrue(loc.startswith("/login?"))
        self.assertEqual(self.client.get(loc).status_code, 200)  # no second silent check


if __name__ == "__main__":
    unittest.main()
