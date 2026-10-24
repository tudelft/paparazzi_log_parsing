import json

from .test_clock import parse_fixture
from .test_historical import AppTests
import db


class NonfiniteTests(AppTests):
    def parse(self, values):
        lines = ["0 5 ROTORCRAFT_STATUS 0 1\n"]
        lines += [f"{i + 1} 5 DL_VALUE 0 {value}\n" for i, value in enumerate(values)]
        lines.append("20 5 ROTORCRAFT_STATUS 0 0\n")
        return parse_fixture(lines, extra_messages=(("DL_VALUE", ("index", "value")),))

    def test_invalid_settings_are_valid_json_and_preserve_all_evidence(self):
        doc = self.parse(["60", "70.1", "nan", "inf", "-inf"])
        changes = doc["aircraft"][0]["settings_changes"]
        self.assertIsNone(changes["by_setting"][0]["final"])
        self.assertEqual(changes["timeline"][0]["to"], 70.1)
        invalid = changes["invalid_reports"]
        self.assertEqual([r["invalid_value"] for r in invalid], ["NaN", "+Infinity", "-Infinity"])
        self.assertEqual([r["time_s"] for r in invalid], [3, 4, 5])
        self.assertTrue(all(r["value"] is None and r["index"] == 0 for r in invalid))
        json.dumps(doc, allow_nan=False)
        log_id = self.con.execute(
            "INSERT INTO logs(name,sha256,data_path,status,uploaded_at,uploaded_by,project_id)"
            " VALUES('fixture','fixture','fixture','done','now',2,1)").lastrowid
        db.store_parsed(self.con, log_id, doc, None)
        self.con.commit()
        flight = self.con.execute("SELECT id FROM flights").fetchone()[0]
        result = self.client.get(f"/api/flights/{flight}/detail")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["invalid_setting_reports"], invalid)

    def test_invalid_first_report_and_recovery_do_not_invent_zero_changes(self):
        changes = self.parse(["nan", "5", "6"])["aircraft"][0]["settings_changes"]
        self.assertIsNone(changes["by_setting"][0]["default"])
        self.assertEqual(changes["by_setting"][0]["final"], 6)
        self.assertEqual([(c["from"], c["to"]) for c in changes["timeline"]], [(5, 6)])

    def test_finite_setting_behavior_is_unchanged(self):
        changes = self.parse(["60", "60", "70.1"])["aircraft"][0]["settings_changes"]
        self.assertEqual(changes["invalid_reports"], [])
        self.assertEqual(len(changes["timeline"]), 1)
        self.assertEqual(changes["timeline"][0]["from"], 60)
        self.assertEqual(changes["timeline"][0]["to"], 70.1)

    def test_nonfinite_sensor_samples_and_timestamps_do_not_poison_metrics(self):
        doc = parse_fixture([
            "0 5 ROTORCRAFT_STATUS 0 1\n",
            "1 5 AIR_DATA nan inf -inf 0\n",
            "nan 5 ROTORCRAFT_STATUS 0 0\n",
            "20 5 ROTORCRAFT_STATUS 0 0\n",
        ], extra_messages=(("AIR_DATA", ("pressure", "diff_p", "temperature", "airspeed")),))
        json.dumps(doc, allow_nan=False)
        self.assertEqual(doc["aircraft"][0]["flights"][0]["duration_s"], 20)
