import math
import unittest

from .test_clock import parse_fixture


GPS_FIELDS = ("mode", "utm_east", "utm_north", "alt", "speed", "utm_zone")
GPS_INT_FIELDS = ("ecef_x", "ecef_y", "ecef_z", "lat", "lon", "alt", "numsv", "fix")
INS_FIELDS = ("ins_x", "ins_y", "ins_z", "ins_xd", "ins_yd", "ins_zd",
              "ins_xdd", "ins_ydd", "ins_zdd")


class HorizontalDistanceTests(unittest.TestCase):
    def parse(self, samples):
        return parse_fixture(
            ["0 5 ROTORCRAFT_STATUS 0 1\n", *samples, "10 5 ROTORCRAFT_STATUS 0 0\n"],
            fields=GPS_FIELDS, extra_messages=(("GPS_INT", GPS_INT_FIELDS), ("INS", INS_FIELDS)))

    def assert_distances(self, doc, distance, current, maximum, delta=0.01):
        self.assertEqual(doc["parser"]["version"], "1.7.5", "Rebuild the parser before testing distances")
        aircraft = doc["aircraft"][0]
        flight = aircraft["flights"][0]
        for key, expected in (("distance_m", distance), ("from_takeoff_m", current),
                              ("max_from_takeoff_m", maximum)):
            self.assertAlmostEqual(flight[key], expected, delta=delta)
        self.assertAlmostEqual(doc["log"]["total_flight_distance_m"], distance, delta=delta)
        self.assertAlmostEqual(aircraft["summary"]["total_flight_distance_m"], distance, delta=delta)
        return flight

    def test_stationary_mixed_gps_altitude_references_add_no_distance(self):
        doc = self.parse([
            "1 5 GPS_INT 0 0 0 0 30000000 100000 12 3\n",
            "2 5 GPS 3 50000000 0 50000 0 31\n",
            "3 5 GPS_INT 0 0 0 0 30000000 110000 12 3\n",
            "4 5 GPS 3 50000000 0 60000 0 31\n",
        ])
        flight = self.assert_distances(doc, 0, 0, 0)
        self.assertGreater(len(flight["path"]), 1)
        self.assertTrue(all(point["distance_m"] < 0.01 and point["from_takeoff_m"] < 0.01
                            for point in flight["path"]))
        alts = [point["position"]["gps_alt_m"] for point in flight["path"]]
        self.assertGreater(max(alts) - min(alts), 40)

    def test_gps_horizontal_out_and_back_ignores_climb(self):
        doc = self.parse([
            "1 5 GPS_INT 0 0 0 0 30000000 50000 12 3\n",
            "2 5 GPS_INT 0 0 0 1000 30000000 150000 12 3\n",
            "3 5 GPS_INT 0 0 0 0 30000000 250000 12 3\n",
        ])
        step = 6371000 * math.radians(0.0001)
        flight = self.assert_distances(doc, 2 * step, 0, step)
        self.assertAlmostEqual(flight["path"][-1]["distance_m"], 2 * step, delta=0.01)

    def test_vertical_ins_motion_adds_no_distance(self):
        doc = self.parse([
            "1 5 INS 0 0 0 0 0 0 0 0 0\n",
            "2 5 INS 0 0 -25600 0 0 0 0 0 0\n",
            "3 5 INS 0 0 25600 0 0 0 0 0 0\n",
        ])
        self.assert_distances(doc, 0, 0, 0)

    def test_ins_horizontal_out_and_back_ignores_down_axis(self):
        doc = self.parse([
            "1 5 INS 0 0 0 0 0 0 0 0 0\n",
            "2 5 INS 768 1024 -25600 0 0 0 0 0 0\n",
            "3 5 INS 0 0 25600 0 0 0 0 0 0\n",
        ])
        self.assert_distances(doc, 10, 0, 5)

    def test_horizontal_gps_glitch_guard_is_preserved(self):
        doc = self.parse([
            "1 5 GPS_INT 0 0 0 0 30000000 50000 12 3\n",
            "2 5 GPS_INT 0 0 0 1000000 30000000 50000 12 3\n",
        ])
        self.assert_distances(doc, 0, 0, 0)
