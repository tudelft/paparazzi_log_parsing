import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import storage_usage

from .test_historical import AppTests


class StorageUsageTests(AppTests):
    """Disk use on Statistics (admins): a background scan of the log storage plus database figures."""

    def test_scan_sorts_files_by_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {"ab/abcd/flight.data.gz": 100, "ab/abcd/flight.log.gz": 20, "ab/abcd/paths-1.json.gz": 7,
                     "attachments/x.gz": 30, "previews/y.pdf": 5, "qualification-evidence/z.pdf": 40,
                     ".incoming/w/log.data": 9, "stray.txt": 1}
            for rel, size in files.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_bytes(b"x" * size)
            self.assertEqual(storage_usage._walk(root), {"logs": [120, 2], "paths": [7, 1], "attachments": [35, 2],
                                                         "evidence": [40, 1], "waiting": [9, 1], "other": [1, 1]})

    def test_route_is_for_admins_and_scans_in_the_background(self):
        self.assertEqual(self.client.get("/api/admin/storage").status_code, 403)
        self.sign_in(1)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(self.ns, STORAGE_DIR=Path(tmp)), \
                patch.dict(storage_usage._state, {"result": None, "measured_at": None, "running": False, "error": None}):
            (Path(tmp) / "ab" / "abcd").mkdir(parents=True)
            (Path(tmp) / "ab" / "abcd" / "f.data.gz").write_bytes(b"x" * 50)
            first = self.client.get("/api/admin/storage").json
            self.assertIn("free", first)
            for _ in range(50):  # the scan finishes in a background thread
                if not storage_usage._state["running"]:
                    break
                time.sleep(0.05)
            later = self.client.get("/api/admin/storage").json
            self.assertEqual(later["scan"]["logs"], {"bytes": 50, "files": 1})
            self.assertIsNotNone(later["measured_at"])
            self.assertFalse(later["measuring"])  # no new scan within SCAN_HOURS
