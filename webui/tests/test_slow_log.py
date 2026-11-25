import threading
import time
import unittest
from unittest.mock import patch

import db
from .test_historical import AppTests


class SlowRequestLogTests(AppTests):
    """LOGDB_SLOW_REQUEST_MS: slow requests and long waits for the write lock are warnings in the service log."""

    def test_slow_request_is_logged_without_query_string(self):
        with patch.dict("os.environ", LOGDB_SLOW_REQUEST_MS="1"), \
                patch.object(self.ns["time"], "monotonic", side_effect=[100.0, 102.5]), \
                self.assertLogs(self.app.logger, "WARNING") as logs:
            self.client.get("/api/me?token=secret")
        line = next(m for m in logs.output if "slow request" in m)
        self.assertIn("GET /api/me took 2.5 s (status 200, user 2, 1 of 8 threads busy at the start, 0 now)", line)
        self.assertNotIn("secret", line)

    def test_fast_request_and_zero_setting_are_quiet(self):
        for value in ("2000", "0"):
            with patch.dict("os.environ", LOGDB_SLOW_REQUEST_MS=value), self.assertNoLogs(self.app.logger, "WARNING"):
                self.client.get("/api/me")


class WriteLockLogTests(unittest.TestCase):
    def test_long_wait_and_hold_name_the_holder(self):
        lock = db.WriteLock()
        lock.slow_s = 0.1
        held = threading.Event()

        def parse():
            db.activity.what = "parsing log 12"
            with lock:
                held.set()
                time.sleep(0.15)

        worker = threading.Thread(target=parse)
        with self.assertLogs("logdb", "WARNING") as logs:
            worker.start()
            held.wait()
            db.activity.what = "PUT /api/flights/3"
            with lock:
                pass
            worker.join()
        db.activity.what = None
        text = "\n".join(logs.output)
        self.assertRegex(text, r"PUT /api/flights/3 waited 0\.\d s for the database write lock, held by parsing log 12")
        self.assertIn("parsing log 12 held the database write lock", text)

    def test_quick_use_and_off_are_quiet(self):
        lock = db.WriteLock()
        with self.assertNoLogs("logdb", "WARNING"):
            with lock:
                pass
            lock.slow_s = 0
            with lock:
                time.sleep(0.01)
