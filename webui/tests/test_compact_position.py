import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from .test_simulation import PARSER


class CompactPositionTests(unittest.TestCase):
    def parse(self, lines, unit="decivolt", rate="5"):
        messages = {
            "ROTORCRAFT_STATUS": ("ap_mode", "ap_in_flight", "vsupply"),
            "ROTORCRAFT_FP_MIN": ("east", "north", "up", "gspeed"),
            "INS_REF": ("ecef_x0", "ecef_y0", "ecef_z0", "lat0", "lon0", "alt0", "hmsl0"),
            "AIR_DATA": ("pressure", "amsl_baro", "airspeed", "tas"),
        }
        protocol = ""
        for name, fields in messages.items():
            protocol += f'<message name="{name}">\n'
            protocol += "".join(f'<field name="{f}" unit="{unit if f == "vsupply" else ""}"/>\n'
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
