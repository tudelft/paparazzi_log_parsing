import json
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import db
from . import test_historical


PARSER = Path(__file__).resolve().parents[2] / "cpp/build/paparazzi_log_parsing"
GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc)
START = datetime(2023, 9, 8, 14, 25, tzinfo=timezone.utc)


def gps_line(t, utc, ac=5, fix=3, message="GPS"):
    seconds = (utc - GPS_EPOCH).total_seconds() + 18
    week, tow = divmod(seconds, 604800)
    return f"{t:.4f} {ac} {message} {fix} {int(week)} {tow * 1000:.0f}\n"


def parse_fixture(lines, start=START, message="GPS", fields=("mode", "week", "itow"), extra_messages=()):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "fixture.data"
        path.write_text("".join(lines))
        header = f'<configuration time_of_day="{start.timestamp()}">\n' if start else "<configuration>\n"
        header += '<aircraft name="ClockTest" ac_id="5"/>\n<protocol>\n<msg_class name="telemetry">\n'
        for name, names in ((message, fields), ("ROTORCRAFT_STATUS", ("ap_mode", "ap_in_flight")), *extra_messages):
            header += f'<message name="{name}">\n'
            header += "".join(f'<field name="{field}"/>\n' for field in names)
            header += "</message>\n"
        header += "</msg_class>\n</protocol>\n</configuration>\n"
        path.with_suffix(".log").write_text(header)
        result = subprocess.run([str(PARSER), str(path)], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)


class ParserClockTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(PARSER.exists(), "Build the C++ parser first")
        version = subprocess.check_output([str(PARSER), "--version"], text=True).strip()
        self.assertEqual(version, "1.7.7", "Rebuild the parser before running clock tests")

    def samples(self, start=START, ac=5, fix=3, message="GPS"):
        return [gps_line(t, start + timedelta(seconds=t), ac, fix, message) for t in (0, 20, 40, 60)]

    def test_week_late_header_corrects_every_absolute_timestamp(self):
        lines = self.samples()
        lines.insert(1, "1 5 ROTORCRAFT_STATUS 0 1\n")
        lines.append("61 5 ROTORCRAFT_STATUS 0 0\n")
        doc = parse_fixture(lines, START + timedelta(days=7, seconds=2))
        timing = doc["log"]["timing"]
        self.assertEqual(timing["source"], "gps_corrected")
        self.assertAlmostEqual(timing["correction_s"], -604802)
        self.assertEqual(timing["original_start_utc"], "2023-09-15T14:25:02.000Z")
        self.assertEqual(doc["log"]["start_utc"], "2023-09-08T14:25:00.000Z")
        self.assertEqual(doc["aircraft"][0]["flights"][0]["takeoff"]["utc"], "2023-09-08T14:25:01.000Z")
        self.assertEqual(doc["aircraft"][0]["flights"][0]["landing"]["utc"], "2023-09-08T14:26:01.000Z")

    def test_correct_clock_and_small_delays_are_preserved(self):
        for seconds in (0, 2, 5):
            with self.subTest(seconds=seconds):
                doc = parse_fixture(self.samples(), START + timedelta(seconds=seconds))
                self.assertEqual(doc["log"]["timing"]["source"], "companion")
                self.assertEqual(doc["log"]["timing"]["correction_s"], 0)
                self.assertEqual(doc["log"]["timing"]["warning"], "")

    def test_missing_companion_clock_is_recovered_only_with_evidence(self):
        recovered = parse_fixture(self.samples(), start=None)["log"]
        self.assertEqual(recovered["start_utc"], "2023-09-08T14:25:00.000Z")
        self.assertEqual(recovered["timing"]["source"], "gps_corrected")
        self.assertIsNone(recovered["timing"]["original_start_utc"])
        self.assertIsNone(recovered["timing"]["correction_s"])
        unknown = parse_fixture(["0 5 ROTORCRAFT_STATUS 0 0\n"], start=None)["log"]
        self.assertIsNone(unknown["start_utc"])
        self.assertEqual(unknown["timing"]["source"], "unknown")

    def test_non_calendar_offsets_warn_without_correction(self):
        for seconds in (6, 3600, 86400 + 61):
            with self.subTest(seconds=seconds):
                timing = parse_fixture(self.samples(), START + timedelta(seconds=seconds))["log"]["timing"]
                self.assertEqual(timing["source"], "companion")
                self.assertEqual(timing["correction_s"], 0)
                self.assertIn("not a clear whole-day error", timing["warning"])

    def test_invalid_gps_and_time_of_week_only_do_not_correct(self):
        cases = [
            (self.samples(fix=0), ("mode", "week", "itow")),
            (["0 5 GPS 3 9999 0\n", "40 5 GPS 3 9999 40000\n"], ("mode", "week", "itow")),
            (["0 5 GPS 3 0 0\n", "40 5 GPS 3 0 40000\n"], ("mode", "week", "itow")),
            (["0 5 GPS 3 2278 604800000\n"], ("mode", "week", "itow")),
            (["0 5 GPS 3 1000\n", "40 5 GPS 3 41000\n"], ("mode", "itow")),
        ]
        for lines, fields in cases:
            with self.subTest(lines=lines):
                timing = parse_fixture(lines, START + timedelta(days=7), fields=fields)["log"]["timing"]
                self.assertEqual(timing["source"], "companion")

    def test_insufficient_or_inconsistent_evidence_warns(self):
        for lines in (self.samples()[:2], self.samples() + [gps_line(80, START + timedelta(seconds=180))]):
            timing = parse_fixture(lines, START + timedelta(days=7))["log"]["timing"]
            self.assertEqual(timing["source"], "companion")
            self.assertIn("insufficient or inconsistent", timing["warning"])

    def test_sample_span_and_consistency_boundaries(self):
        for span, jitter, corrected in ((30, 5, True), (29, 0, False), (30, 6, False)):
            with self.subTest(span=span, jitter=jitter):
                lines = [gps_line(t, START + timedelta(seconds=t + (jitter if i == 2 else 0)))
                         for i, t in enumerate((0, 10, span))]
                timing = parse_fixture(lines, START + timedelta(days=7))["log"]["timing"]
                self.assertEqual(timing["source"], "gps_corrected" if corrected else "companion")

    def test_whole_day_tolerance_is_inclusive(self):
        for seconds in (-60, 60):
            with self.subTest(seconds=seconds):
                timing = parse_fixture(self.samples(), START + timedelta(days=7, seconds=seconds))["log"]["timing"]
                self.assertEqual(timing["source"], "gps_corrected")
                self.assertEqual(timing["correction_s"], -604800 - seconds)

    def test_conflicting_aircraft_do_not_shift_entire_log(self):
        lines = sorted(self.samples() + self.samples(START + timedelta(days=1), ac=6),
                       key=lambda line: float(line.split()[0]))
        timing = parse_fixture(lines, START + timedelta(days=7))["log"]["timing"]
        self.assertEqual(timing["source"], "companion")
        self.assertIn("Aircraft GPS clocks conflict", timing["warning"])

    def test_gps_week_rollover_and_lla(self):
        start = datetime(2023, 9, 9, 23, 59, 22, tzinfo=timezone.utc)
        for message in ("GPS", "GPS_LLA"):
            with self.subTest(message=message):
                doc = parse_fixture(self.samples(start, message=message), start + timedelta(days=7), message)
                self.assertEqual(doc["log"]["timing"]["source"], "gps_corrected")
                self.assertEqual(doc["log"]["start_utc"], "2023-09-09T23:59:22.000Z")

    def test_historical_leap_second_offset(self):
        start = datetime(2010, 1, 1, tzinfo=timezone.utc)
        lines = []
        for t in (0, 20, 40):
            # The fixture helper adds 18 seconds; GPS-UTC was 15 seconds in 2010.
            lines.append(gps_line(t, start + timedelta(seconds=t - 3)))
        doc = parse_fixture(lines, start + timedelta(days=7))
        self.assertEqual(doc["log"]["start_utc"], "2010-01-01T00:00:00.000Z")


class StoredClockTests(test_historical.AppTests):
    def insert_log(self, name, doc, override=None):
        log_id = self.con.execute(
            "INSERT INTO logs(name,sha256,data_path,status,uploaded_at,uploaded_by,project_id,date_override)"
            " VALUES (?,?,?,'done','now',2,1,?)", (name, name, name, override)).lastrowid
        db.store_parsed(self.con, log_id, doc, None)
        self.con.commit()
        return log_id

    def fixture(self, source, day):
        start = f"2023-09-{day:02d}T14:25:00.000Z"
        return {"parser": {"name": "fastreader", "version": "1.5.0"},
                "log": {"start_utc": start, "type": source, "timing": {
                    "source": "gps_corrected", "original_start_utc": "2023-09-15T14:25:00.000Z",
                    "effective_start_utc": start, "correction_s": -604800, "warning": "Clock corrected"}},
                "aircraft": [{"ac_id": 5, "name": "ClockTest", "flights": [{
                    "flight": 1, "duration_s": 60, "landed": True,
                    "takeoff": {"time_s": 1, "utc": start, "location": {"lat": 52, "lon": 4}},
                    "landing": {"time_s": 61, "utc": f"2023-09-{day:02d}T14:26:00.000Z"},
                    "assessment": {"verdict": "flight"}}]}]}

    def test_timing_survives_storage_and_is_exposed_by_both_apis(self):
        doc = self.fixture("onboard_sd", 8)
        log_id = self.insert_log("sd", doc)
        flight_id = self.con.execute("SELECT id FROM flights").fetchone()[0]
        self.assertEqual(self.client.get(f"/api/logs/{log_id}").json["timing"], doc["log"]["timing"])
        self.assertEqual(self.client.get(f"/api/flights/{flight_id}/detail").json["timing"], doc["log"]["timing"])

    def test_manual_date_remains_authoritative_across_reparse(self):
        doc = self.fixture("onboard_sd", 8)
        log_id = self.insert_log("sd", doc, "2023-09-10")
        db.store_parsed(self.con, log_id, doc, None)
        self.con.commit()
        result = self.client.get(f"/api/logs/{log_id}").json
        self.assertEqual(result["date_override"], "2023-09-10")
        self.assertEqual(result["start_utc"], "2023-09-10T14:25:00.000Z")
        self.assertEqual(result["timing"]["source"], "manual")
        self.assertEqual(result["timing"]["parsed_source"], "gps_corrected")
        self.assertEqual(result["timing"]["original_start_utc"], "2023-09-15T14:25:00.000Z")
        flight_id = self.con.execute("SELECT id FROM flights").fetchone()[0]
        detail = self.client.get(f"/api/flights/{flight_id}/detail").json
        self.assertEqual(detail["timing"]["source"], "manual")
        self.assertTrue(detail["entry"]["takeoff_utc"].startswith("2023-09-10"))

    def test_corrected_sources_merge_normally(self):
        lines = [gps_line(t, START + timedelta(seconds=t)) for t in (0, 20, 40, 60)]
        lines.insert(1, "1 5 ROTORCRAFT_STATUS 0 1\n")
        lines.append("61 5 ROTORCRAFT_STATUS 0 0\n")
        for name, source, start in (("gcs", "gcs", START), ("sd", "onboard_sd", START + timedelta(days=7))):
            doc = parse_fixture(lines, start)
            doc["log"]["type"] = source
            self.insert_log(name, doc)
        rows = self.client.get("/api/logbook").json
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["sources"]), 2)
        self.assertTrue(rows[0]["takeoff_utc"].startswith("2023-09-08"))

    def test_legacy_log_has_no_invented_clock_evidence(self):
        doc = self.fixture("gcs", 8)
        del doc["log"]["timing"]
        log_id = self.insert_log("legacy", doc)
        self.assertIsNone(self.client.get(f"/api/logs/{log_id}").json["timing"])
        flight_id = self.con.execute("SELECT id FROM flights").fetchone()[0]
        self.assertIsNone(self.client.get(f"/api/flights/{flight_id}/detail").json["timing"])


class ClockMigrationTests(unittest.TestCase):
    def test_legacy_schema_upgrade_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.db"
            with sqlite3.connect(path) as con:
                schema = "\n".join(line for line in db.SCHEMA.splitlines()
                                   if not line.strip().startswith(("timing TEXT", "has_gps_position INTEGER")))
                con.executescript(schema)
                con.execute("INSERT INTO logs(name,sha256,data_path,status,uploaded_at)"
                            " VALUES ('legacy','hash','legacy.data','done','now')")
                con.execute("INSERT INTO aircraft(id,log_id,name) VALUES(1,1,'Legacy aircraft')")
                con.execute("INSERT INTO flights(aircraft_id,idx,duration_s) VALUES(1,1,60)")
            db.init(path)
            db.init(path)
            with sqlite3.connect(path) as con:
                self.assertEqual(con.execute("SELECT name,timing FROM logs").fetchone(), ("legacy", None))
                self.assertIn("has_gps_position", {r[1] for r in con.execute("PRAGMA table_info(flights)")})
                self.assertEqual(con.execute("SELECT duration_s,has_gps_position FROM flights").fetchone(),
                                 (60, None))


if __name__ == "__main__":
    unittest.main()
