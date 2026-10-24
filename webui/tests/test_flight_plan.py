import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from .test_clock import PARSER


class FlightPlanTests(unittest.TestCase):
    def parse(self, sectors):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / "fixture.data"
            data.write_text("0 5 ROTORCRAFT_STATUS 0 0\n")
            data.with_suffix(".log").write_text(
                '<configuration time_of_day="1751457600">\n<aircraft ac_id="5" name="Synthetic">\n'
                '<flight_plan lat0="52" lon0="4" alt="20" name="Test">\n'
                + sectors +
                '<waypoints>\n<waypoint name="A" x="0" y="0"/>\n'
                '<waypoint name="B" x="100" y="0"/>\n'
                '<waypoint name="C" lat="52.001" lon="4.001"/>\n</waypoints>\n'
                '<blocks><block name="Wait"><while cond="TRUE"/></block><block name="Land"/></blocks>\n'
                '</flight_plan>\n</aircraft>\n<protocol>\n<msg_class name="telemetry">\n'
                '<message name="ROTORCRAFT_STATUS">\n<field name="ap_mode"/>\n'
                '<field name="ap_in_flight"/>\n</message>\n'
                '</msg_class>\n</protocol>\n</configuration>\n')
            result = subprocess.run([str(PARSER), str(data)], capture_output=True, text=True, check=True)
            return json.loads(result.stdout)["aircraft"][0]["flight_plan"]

    def test_sectors_preserve_order_names_colors_and_waypoint_references(self):
        plan = self.parse(
            '<sectors><sector name="Flight &amp; area" color="orange">\n'
            '<corner name="C"/><corner name="A"></corner><corner name="B"/>\n'
            '</sector><sector name="Missing"><corner name="Unknown"/></sector>'
            '<sector name="Empty"/></sectors>\n')
        self.assertEqual(plan["sectors"], [
            {"name": "Flight & area", "color": "orange", "corners": ["C", "A", "B"]},
            {"name": "Missing", "color": None, "corners": ["Unknown"]},
            {"name": "Empty", "color": None, "corners": []},
        ])
        self.assertEqual([w["name"] for w in plan["waypoints"]], ["A", "B", "C"])
        self.assertAlmostEqual(plan["waypoints"][0]["lat"], 52)
        self.assertGreater(plan["waypoints"][1]["lon"], 4)
        self.assertEqual([b["name"] for b in plan["blocks"]], ["Wait", "Land"])

    def test_flight_plan_without_sectors_has_empty_list(self):
        self.assertEqual(self.parse("")["sectors"], [])
