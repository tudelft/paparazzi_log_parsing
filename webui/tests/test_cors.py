import os
from unittest.mock import patch

from .test_historical import AppTests

ORIGIN = "https://app.example.org"


class CorsTests(AppTests):
    """LOGDB_CORS_ORIGINS lets a page on another origin call /api with a bearer key; off by default."""

    def allow(self, value):
        settings = patch.dict(os.environ, LOGDB_CORS_ORIGINS=value)
        settings.start()
        self.addCleanup(settings.stop)

    def preflight(self, path="/api/upload", origin=ORIGIN):
        return self.client.options(path, headers={
            "Origin": origin, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization"})

    def test_off_by_default(self):
        r = self.preflight()
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("Access-Control-Allow-Origin", r.headers)
        self.assertNotIn("Access-Control-Allow-Origin", self.client.get("/api/me", headers={"Origin": ORIGIN}).headers)

    def test_preflight_needs_no_sign_in_for_an_allowed_origin(self):
        self.allow(ORIGIN + "/, https://other.example.org")
        r = self.preflight()
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], ORIGIN)
        self.assertIn("Authorization", r.headers["Access-Control-Allow-Headers"])
        self.assertIn("POST", r.headers["Access-Control-Allow-Methods"])
        self.assertEqual(r.headers["Vary"], "Origin")
        self.assertEqual(self.preflight(origin="https://evil.example.org").status_code, 403)

    def test_actual_answers_carry_the_header_even_when_refused(self):
        self.allow(ORIGIN)
        r = self.client.post("/api/logs/check", json={}, headers={"Origin": ORIGIN, "Authorization": "Bearer nope"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], ORIGIN)
        other = self.client.post("/api/logs/check", json={}, headers={"Origin": "https://evil.example.org"})
        self.assertNotIn("Access-Control-Allow-Origin", other.headers)

    def test_star_allows_any_origin_but_never_credentials_or_pages(self):
        self.allow("*")
        self.assertEqual(self.preflight(origin="https://anything.example.org").status_code, 204)
        r = self.client.get("/api/me", headers={"Origin": ORIGIN})
        self.assertNotIn("Access-Control-Allow-Credentials", r.headers)
        self.assertNotIn("Access-Control-Allow-Origin", self.client.get("/theme.css", headers={"Origin": ORIGIN}).headers)
        self.assertEqual(self.preflight(path="/aircraft").status_code, 403)
