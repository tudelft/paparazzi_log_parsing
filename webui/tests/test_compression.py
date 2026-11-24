import gzip
import json

from .test_historical import AppTests


class CompressionAndCachingTests(AppTests):
    """Large answers are gzipped for browsers that accept it; the page scripts are cached and revalidated (304)."""

    def test_large_json_is_gzipped_only_when_accepted(self):
        self.sign_in(1)
        # a JSON answer above the 16 KB threshold, from a route that does not compress by itself
        app = self.app
        app.add_url_rule("/api/test-large-json", "test_large_json", lambda: app.json.response(items=["x" * 40] * 1000))
        plain = self.client.get("/api/test-large-json")
        self.assertNotIn("Content-Encoding", plain.headers)
        packed = self.client.get("/api/test-large-json", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(packed.headers["Content-Encoding"], "gzip")
        self.assertIn("Accept-Encoding", packed.headers["Vary"])
        self.assertEqual(json.loads(gzip.decompress(packed.data)), json.loads(plain.data))
        small = self.client.get("/api/me", headers={"Accept-Encoding": "gzip"})
        self.assertNotIn("Content-Encoding", small.headers)

    def test_page_scripts_are_revalidated(self):
        for path in ("/theme.js", "/theme.css"):
            first = self.client.get(path)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.headers["Cache-Control"], "no-cache")
            again = self.client.get(path, headers={"If-None-Match": first.headers["ETag"]})
            self.assertEqual(again.status_code, 304, path)
