"""sd2log without a GPS date writes the conversion time into the .log header and names the log *_no_GPS:
the parser must not take that time as the recording time (parser 1.14.0)."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from .test_historical import AppTests

PARSER = Path(__file__).resolve().parents[2] / "cpp/build/paparazzi_log_parsing"
CONVERTED = 1795083900.0  # 2026-11-19 10:25 UTC: when sd2log ran, not when the aircraft flew
LINES = ["0 5 ROTORCRAFT_STATUS 0 0\n", "1 5 ROTORCRAFT_STATUS 0 1\n", "61 5 ROTORCRAFT_STATUS 0 0\n"]


def parse(file_name, data_file_attribute):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / file_name
        path.write_text("".join(LINES))
        attr = f' data_file="{data_file_attribute}"' if data_file_attribute else ""
        path.with_suffix(".log").write_text(
            f'<configuration{attr} time_of_day="{CONVERTED:.2f}">\n<aircraft name="Hex" ac_id="5"/>\n<protocol>\n'
            '<msg_class name="telemetry">\n<message name="ROTORCRAFT_STATUS">\n<field name="ap_mode"/>\n'
            '<field name="ap_in_flight"/>\n</message>\n</msg_class>\n</protocol>\n</configuration>\n')
        out = subprocess.run([str(PARSER), str(path)], capture_output=True, text=True, check=True)
        return json.loads(out.stdout)


@unittest.skipUnless(PARSER.exists(), "build the C++ parser first (make -C cpp)")
class NoGpsConversionTimeTests(unittest.TestCase):
    def test_conversion_time_is_not_the_start(self):
        for file_name, attribute in (("26_11_19__10_25_00_no_GPS_SD.data", "26_11_19__10_25_00_no_GPS_SD.data"),
                                     ("renamed_SD.data", "26_11_19__10_25_00_no_GPS_SD.data"),  # header says so
                                     ("26_11_19__10_25_00_no_GPS_SD.data", None)):              # name says so
            with self.subTest(file_name=file_name, attribute=attribute):
                log = parse(file_name, attribute)["log"]
                self.assertIsNone(log["start_utc"])
                self.assertEqual(log["timing"]["source"], "unknown")
                self.assertIn("header_conversion_time", log["timing"]["reasons"])
                self.assertIn("_no_GPS", log["timing"]["warning"])
                self.assertEqual(log["type"], "onboard_sd")

    def test_a_dated_log_keeps_its_time(self):
        log = parse("26_11_19__10_25_00_SD.data", "26_11_19__10_25_00_SD.data")["log"]
        self.assertEqual(log["start_utc"], "2026-11-19T10:25:00.000Z")
        self.assertEqual(log["timing"]["source"], "companion")


class NoGpsNameDateTests(unittest.TestCase):
    def test_the_file_name_of_a_no_gps_log_gives_no_date(self):
        import date_suggest
        undated = lambda iso: False  # every date is plausible here
        self.assertIsNone(date_suggest.date_in_name("26_11_19__10_25_00_no_GPS_SD.data", undated))
        self.assertEqual(date_suggest.date_in_name("CyberZoo 2026-11-12/26_11_19__10_25_00_no_GPS_SD.data", undated),
                         ("2026-11-12", "CyberZoo 2026-11-12"))  # a folder the pilot named still counts
        self.assertEqual(date_suggest.date_in_name("26_11_12__14_02_10_SD.data", undated)[0], "2026-11-12")


class RequeueNoGpsTests(AppTests):
    def test_once_and_only_with_the_new_parser(self):
        for i, name in enumerate(("26_11_19__10_25_00_no_GPS_SD.data", "26_11_12__14_02_10_SD.data")):
            self.con.execute("INSERT INTO logs(id,name,sha256,data_path,log_path,status,uploaded_at) VALUES"
                             " (?,?,?,'x','y','done','now')", (i + 1, name, f"s{i}"))
        status = lambda: [r[0] for r in self.con.execute("SELECT status FROM logs ORDER BY id")]
        requeue = self.ns["requeue_no_gps_logs"]
        self.ns["parser_version"] = lambda: "1.13.0"
        self.assertEqual(requeue(self.con), 0)  # the old parser would give the same wrong date
        self.assertEqual(status(), ["done", "done"])
        self.ns["parser_version"] = lambda: "1.14.0"
        self.assertEqual(requeue(self.con), 1)
        self.assertEqual(status(), ["queued", "done"])
        self.con.execute("UPDATE logs SET status='done'")
        self.assertEqual(requeue(self.con), 0)  # once
        self.assertEqual(status(), ["done", "done"])


if __name__ == "__main__":
    unittest.main()
