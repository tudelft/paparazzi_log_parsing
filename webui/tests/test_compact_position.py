import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from .test_simulation import PARSER
from .test_clock import parse_fixture


class CompactPositionTests(unittest.TestCase):
    def parse(self, lines, unit="decivolt", rate="5", extra_messages=None):
        messages = {
            "ROTORCRAFT_STATUS": ("ap_mode", "ap_in_flight", "vsupply"),
            "ROTORCRAFT_FP_MIN": ("east", "north", "up", "gspeed"),
            "ROTORCRAFT_FP": ("east", "north", "up", "veast", "vnorth", "phi", "theta", "psi"),
            "INS_REF": ("ecef_x0", "ecef_y0", "ecef_z0", "lat0", "lon0", "alt0", "hmsl0"),
            "AIR_DATA": ("pressure", "amsl_baro", "airspeed", "tas"),
        }
        messages.update(extra_messages or {})
        protocol = ""
        for name, fields in messages.items():
            protocol += f'<message name="{name}">\n'
            protocol += "".join(f'<field name="{f}" unit="{unit if f in ("vsupply", "voltage") else ""}"/>\n'
                                for f in fields)
            protocol += "</message>\n"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.data"
            path.write_text("".join(line + "\n" for line in lines))
            path.with_suffix(".log").write_text(
                '<configuration time_of_day="1751457600">\n'
                '<aircraft ac_id="5" name="Synthetic"/>\n'
                '<protocol>\n<msg_class name="telemetry">\n' + protocol +
                '</msg_class>\n</protocol>\n</configuration>\n')
            result = subprocess.run([str(PARSER), "-r", rate, str(path)],
                                    capture_output=True, text=True, check=True)
            return json.loads(result.stdout)["aircraft"][0]["flights"][0]

    origin = "INS_REF 0 637813700 0 0 900000000 0 -30000"
    gps_fields = {"GPS_INT": ("ecef_x", "ecef_y", "ecef_z", "lat", "lon", "alt", "numsv", "fix")}
    gps = "GPS_INT 0 637813700 0 0 900000000 0 8 3"

    def test_inconsistent_reference_does_not_replace_recent_gps(self):
        flight = self.parse([
            "0 5 INS_REF -637813700 0 0 0 1800000000 0 -30000",
            "1 5 " + self.gps,
            "1.1 5 ROTORCRAFT_FP_MIN 0 0 0 0",
            "1.2 5 ROTORCRAFT_STATUS 13 1 242",
            "2 5 " + self.gps,
            "2.1 5 ROTORCRAFT_FP_MIN 0 0 0 0",
            "3 5 ROTORCRAFT_STATUS 13 0 230",
        ], extra_messages=self.gps_fields)
        self.assertAlmostEqual(flight["takeoff"]["location"]["lon"], 90)
        self.assertEqual(len(flight["path"]), 1)
        self.assertAlmostEqual(flight["path"][0]["position"]["lon"], 90)
        self.assertEqual(flight["distance_m"], 0)

    def test_delayed_changed_reference_is_applied_before_receipt_with_gps_evidence(self):
        flight = self.parse([
            "0 5 INS_REF -637813700 0 0 0 1800000000 0 -30000",
            "1 5 " + self.gps,
            "4 5 ROTORCRAFT_FP_MIN 0 0 0 0",
            "4.1 5 ROTORCRAFT_STATUS 13 1 242",
            "5 5 ROTORCRAFT_FP_MIN 256 0 0 0",
            "6 5 " + self.origin,
            "7 5 ROTORCRAFT_STATUS 13 0 230",
        ], extra_messages=self.gps_fields)
        self.assertAlmostEqual(flight["takeoff"]["location"]["lon"], 90)
        self.assertEqual(len(flight["path"]), 1)
        self.assertAlmostEqual(flight["path"][0]["position"]["lon"], 90.000009, places=6)

    def test_stale_fix_and_uninitialized_frame_leave_takeoff_unknown(self):
        flight = self.parse([
            "0 5 INS_REF -637813700 0 0 0 1800000000 0 -30000",
            "1 5 " + self.gps,
            "20 5 ROTORCRAFT_FP_MIN 0 0 0 0",
            "21 5 ROTORCRAFT_STATUS 13 1 242",
            "25 5 " + self.gps,
            "26 5 ROTORCRAFT_STATUS 13 0 230",
        ], extra_messages=self.gps_fields)
        self.assertIsNone(flight["takeoff"]["location"])
        self.assertAlmostEqual(flight["landing"]["location"]["lon"], 90)
        self.assertEqual(len(flight["path"]), 1)

    def test_inconsistent_vertical_frame_is_rejected(self):
        flight = self.parse([
            "0 5 " + self.origin, "1 5 " + self.gps,
            "4 5 ROTORCRAFT_STATUS 13 1 242",
            "5 5 ROTORCRAFT_FP_MIN 0 0 2560000 0",
            "7 5 ROTORCRAFT_STATUS 13 0 230",
        ], extra_messages=self.gps_fields)
        self.assertEqual(flight["path"], [])

    def test_established_reference_survives_gps_outage_and_repeated_reports(self):
        flight = self.parse([
            "0 5 " + self.origin, "1 5 " + self.gps,
            "4 5 ROTORCRAFT_FP_MIN 0 0 0 0",
            "80 5 " + self.origin,
            "80.5 5 INS_REF 0 637814200 0 0 900000000 5000 -25000",
            "81 5 ROTORCRAFT_STATUS 13 1 242",
            "82 5 ROTORCRAFT_FP_MIN 25600 0 0 0",
            "83 5 ROTORCRAFT_STATUS 13 0 230",
        ], extra_messages=self.gps_fields)
        self.assertEqual(len(flight["path"]), 1)
        self.assertAlmostEqual(flight["path"][0]["position"]["lon"], 90.0008983, places=6)

    def test_material_reference_change_requires_new_corroboration(self):
        flight = self.parse([
            "0 5 " + self.origin, "1 5 " + self.gps,
            "4 5 ROTORCRAFT_FP_MIN 0 0 0 0",
            "80 5 INS_REF -637813700 0 0 0 1800000000 0 -30000",
            "81 5 ROTORCRAFT_STATUS 13 1 242",
            "82 5 ROTORCRAFT_FP_MIN 0 0 0 0",
            "83 5 ROTORCRAFT_STATUS 13 0 230",
        ], extra_messages=self.gps_fields)
        self.assertEqual(flight["path"], [])

    def test_rejected_frame_does_not_accumulate_ins_initialization_jumps(self):
        messages = {**self.gps_fields,
                    "INS": ("ins_x", "ins_y", "ins_z", "ins_xd", "ins_yd", "ins_zd",
                            "ins_xdd", "ins_ydd", "ins_zdd")}
        flight = self.parse([
            "0 5 INS_REF -637813700 0 0 0 1800000000 0 -30000",
            "1 5 ROTORCRAFT_FP_MIN 0 0 0 0",
            "2 5 ROTORCRAFT_STATUS 13 1 242",
            "3 5 INS 0 0 0 0 0 0 0 0 0",
            "4 5 INS 240000000 54000000 0 2000000000 2000000000 0 0 0 0",
            "5 5 " + self.gps,
            "6 5 ROTORCRAFT_STATUS 13 0 230",
        ], extra_messages=messages)
        self.assertEqual(flight["distance_m"], 0)
        self.assertIsNone(flight["assessment"]["metrics"]["ins_horizontal_m"])
        self.assertEqual(flight["assessment"]["metrics"]["max_speed_ms"], 0)

    def test_legacy_bat_timer_positions_voltage_and_navigation(self):
        flight = self.parse([
            "0 5 " + self.origin, "1 5 NAVIGATION 7 2",
            "2 5 BAT 126 102",
            "3 5 ROTORCRAFT_FP_MIN 0 0 0 1800",
            "4 5 NAVIGATION 8 3",
            "5 5 ROTORCRAFT_FP_MIN 25600 0 0 1800",
            "6 5 BAT 130 99", "7 5 BAT 0 98"],
            unit="1e-1V", extra_messages={"BAT": ("flight_time", "voltage"),
                                        "NAVIGATION": ("cur_block", "cur_stage")})
        self.assertEqual(flight["duration_s"], 5)
        self.assertEqual(flight["takeoff"]["voltage_v"], 10.2)
        self.assertEqual(flight["landing"]["voltage_v"], 9.8)
        self.assertEqual(len(flight["path"]), 2)
        self.assertEqual(flight["path"][-1]["nav"]["block_id"], 8)

    def test_bat_timer_end_of_recording_and_modern_precedence(self):
        messages = {"BAT": ("flight_time", "voltage"),
                    "NAVIGATION": ("cur_block", "cur_stage", "flight_time")}
        flight = self.parse(["0 5 NAVIGATION 1 0 1", "1 5 BAT 0 24", "4 5 NAVIGATION 1 0 0"],
                            unit="V", extra_messages=messages)
        self.assertEqual(flight["duration_s"], 4)
        self.assertEqual(flight["landing"]["voltage_v"], 24)
        flight = self.parse(["1 5 BAT 126 102", "5 5 BAT 130 100"], unit="1e-1V",
                            extra_messages={"BAT": ("flight_time", "voltage")})
        self.assertEqual(flight["duration_s"], 4)

    def test_ground_only_bat_does_not_create_flights(self):
        doc = parse_fixture(["1 5 BAT 0 102\n", "5 5 BAT 0 100\n"],
                            extra_messages=(("BAT", ("flight_time", "voltage")),))
        self.assertEqual(len(doc["aircraft"]), 1)
        self.assertEqual(doc["aircraft"][0]["flights"], [])

    def test_full_position_retains_attitude_and_reconstructs_speed(self):
        flight = self.parse([
            "0 5 ROTORCRAFT_STATUS 13 1 242",
            "1 5 ROTORCRAFT_FP 0 0 2560 1572864 2097152 0 0 4096",
            "2 5 ROTORCRAFT_FP 25600 0 2560 1572864 2097152 0 0 4096",
            "3 5 " + self.origin,
            "4 5 ROTORCRAFT_STATUS 13 0 230"])
        self.assertEqual(len(flight["path"]), 2)
        self.assertAlmostEqual(flight["distance_m"], 100, delta=.2)
        self.assertEqual(flight["path"][0]["groundspeed_ms"], 5)
        self.assertAlmostEqual(flight["path"][0]["attitude"]["euler_deg"]["yaw"], 57.3, delta=.01)

    def test_delayed_reference_preserves_early_path_sensors_and_speed(self):
        flight = self.parse([
            "0 5 ROTORCRAFT_STATUS 13 1 242",
            "0.5 5 AIR_DATA 101325 100 20 21",
            "1 5 ROTORCRAFT_FP_MIN 0 0 2560 1800",
            "2 5 ROTORCRAFT_FP_MIN 25600 0 2560 1900",
            "3 5 " + self.origin,
            "4 5 ROTORCRAFT_STATUS 13 0 230",
        ])
        self.assertEqual(flight["duration_s"], 4)
        self.assertEqual(len(flight["path"]), 2)
        first, last = flight["path"]
        self.assertAlmostEqual(first["position"]["gps_alt_m"], -20, delta=.05)
        self.assertAlmostEqual(first["position"]["lat"], 0, delta=1e-6)
        self.assertAlmostEqual(last["position"]["lon"], 90.0008983, delta=1e-6)
        self.assertAlmostEqual(flight["distance_m"], 100, delta=.2)
        self.assertEqual(first["groundspeed_ms"], 18)
        self.assertEqual(first["battery_voltage_v"], 24.2)
        self.assertEqual(first["position"]["baro_alt_m"], 100)
        self.assertEqual(first["airspeed"]["eas_ms"], 20)
        self.assertEqual(flight["landing"]["voltage_v"], 23)

    def test_missing_invalid_and_other_aircraft_references_do_not_fabricate_positions(self):
        for reference in ("", "2 6 " + self.origin,
                          "2 5 INS_REF 0 0 0 0 0 0 0"):
            flight = self.parse(["0 5 ROTORCRAFT_STATUS 13 1 242",
                                 "1 5 ROTORCRAFT_FP_MIN 25600 0 0 1800",
                                 reference, "4 5 ROTORCRAFT_STATUS 13 0 230"])
            self.assertEqual(flight["path"], [])

    def test_reference_changes_rate_and_modern_voltage_units(self):
        lines = ["0 5 " + self.origin, "0.1 5 ROTORCRAFT_STATUS 13 1 24.2",
                 "1 5 ROTORCRAFT_FP_MIN 0 0 0 1800",
                 "1.01 5 ROTORCRAFT_FP_MIN 256 0 0 1800",
                 "2 5 INS_REF -637813700 0 0 0 1800000000 0 -30000",
                 "3 5 ROTORCRAFT_FP_MIN 0 0 0 1800",
                 "4 5 ROTORCRAFT_STATUS 13 0 23"]
        flight = self.parse(lines, unit="V")
        self.assertEqual(len(flight["path"]), 2)
        self.assertAlmostEqual(flight["path"][-1]["position"]["lon"], 180, delta=1e-6)
        self.assertEqual(flight["takeoff"]["voltage_v"], 24.2)
        self.assertEqual(self.parse(lines, unit="V", rate="0")["path"], [])

    def test_north_up_and_invalid_reference_reset(self):
        flight = self.parse([
            "0 5 " + self.origin, "0.1 5 ROTORCRAFT_STATUS 13 1 242",
            "1 5 ROTORCRAFT_FP_MIN 0 25600 2560 1000",
            "2 5 INS_REF 0 0 0 0 0 0 0",
            "3 5 ROTORCRAFT_FP_MIN 0 51200 2560 1000",
            "4 5 " + self.origin, "5 5 ROTORCRAFT_FP_MIN 0 76800 2560 1000",
            "6 5 ROTORCRAFT_STATUS 13 0 230"])
        self.assertEqual(len(flight["path"]), 2)
        self.assertAlmostEqual(flight["path"][0]["position"]["lat"], .0009044, delta=1e-6)
        self.assertAlmostEqual(flight["path"][0]["position"]["gps_alt_m"], -20, delta=.05)
