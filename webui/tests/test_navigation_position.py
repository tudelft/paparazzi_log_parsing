import unittest

from .test_clock import parse_fixture


class NavigationPositionTests(unittest.TestCase):
    messages = (
        ("NAVIGATION", ("cur_block", "cur_stage", "flight_time", "pos_x", "pos_y")),
        ("NAVIGATION_REF", ("utm_east", "utm_north", "utm_zone")),
    )
    reference = "0 5 NAVIGATION_REF 500000 5400000 32\n"
    calibration = (
        "1 5 GPS 3 50000000 540000000 100000 1000 32\n",
        "1.1 5 NAVIGATION 1 0 0 0 0\n",
        "2 5 ROTORCRAFT_STATUS 0 1\n",
    )

    def parse(self, lines, messages=None):
        return parse_fixture(
            lines, fields=("mode", "utm_east", "utm_north", "alt", "speed", "utm_zone"),
            extra_messages=self.messages if messages is None else messages,
        )["aircraft"][0]["flights"][0]

    def test_validated_navigation_fills_gap_without_inventing_altitude(self):
        flight = self.parse([
            self.reference, *self.calibration,
            "4 5 NAVIGATION 1 0 1 30 0\n",
            "5 5 NAVIGATION 1 0 2 40 0\n",
            "6 5 GPS 3 50005000 540000000 101000 1000 32\n",
            "6.1 5 NAVIGATION 1 0 3 50 0\n",
            "7 5 ROTORCRAFT_STATUS 0 0\n",
        ])
        self.assertEqual([p["time_s"] for p in flight["path"]], [4, 5, 6])
        self.assertEqual(flight["path"][0]["position"]["source"], "navigation")
        self.assertIsNone(flight["path"][0]["position"]["gps_alt_m"])
        self.assertEqual(flight["path"][-1]["position"]["gps_alt_m"], 101)
        self.assertAlmostEqual(flight["distance_m"], 20, delta=.1)
        self.assertEqual(flight["assessment"]["metrics"]["gps_alt_range_m"], 0)

    def test_recent_gps_is_unchanged(self):
        flight = self.parse([
            self.reference, *self.calibration,
            "2.5 5 GPS 3 50001000 540000000 100000 1000 32\n",
            "2.6 5 NAVIGATION 1 0 1 10 0\n",
            "3 5 ROTORCRAFT_STATUS 0 0\n",
        ])
        self.assertEqual([p["time_s"] for p in flight["path"]], [2.5])
        self.assertNotIn("source", flight["path"][0]["position"])

    def test_gps_replaces_fallback_in_same_sampling_bucket(self):
        flight = self.parse([
            self.reference, *self.calibration,
            "4 5 NAVIGATION 1 0 1 30 0\n",
            "4.1 5 GPS 3 50003000 540000000 100000 1000 32\n",
            "5 5 ROTORCRAFT_STATUS 0 0\n",
        ])
        self.assertEqual([p["time_s"] for p in flight["path"]], [4.1])
        self.assertNotIn("source", flight["path"][0]["position"])

    def test_no_reference_or_wrong_reference_does_not_fabricate_path(self):
        for reference in ("", "0 5 NAVIGATION_REF 600000 5400000 32\n",
                          "0 5 NAVIGATION_REF 500000 5400000 31\n"):
            with self.subTest(reference=reference):
                flight = self.parse([
                    reference, *self.calibration, "4 5 NAVIGATION 1 0 1 30 0\n",
                    "5 5 ROTORCRAFT_STATUS 0 0\n",
                ])
                self.assertEqual(flight["path"], [])

    def test_no_gps_corroboration_does_not_fabricate_path(self):
        flight = self.parse([
            self.reference, "1 5 NAVIGATION 1 0 0 0 0\n",
            "2 5 ROTORCRAFT_STATUS 0 1\n", "4 5 NAVIGATION 1 0 1 30 0\n",
            "5 5 ROTORCRAFT_STATUS 0 0\n",
        ])
        self.assertEqual(flight["path"], [])
        self.assertIsNone(flight["takeoff"]["location"])

    def test_other_aircraft_gps_cannot_validate_reference(self):
        flight = self.parse([
            self.reference, "1 6 GPS 3 50000000 540000000 100000 1000 32\n",
            "1.1 5 NAVIGATION 1 0 0 0 0\n",
            "2 5 ROTORCRAFT_STATUS 0 1\n", "4 5 NAVIGATION 1 0 1 30 0\n",
            "5 5 ROTORCRAFT_STATUS 0 0\n",
        ])
        self.assertEqual(flight["path"], [])

    def test_stale_or_low_quality_gps_cannot_validate_reference(self):
        for gps in ("0.5 5 GPS 3 50000000 540000000 100000 1000 32\n",
                    "1 5 GPS 2 50000000 540000000 100000 1000 32\n"):
            with self.subTest(gps=gps):
                flight = self.parse([
                    self.reference, gps, *self.calibration[1:],
                    "4 5 NAVIGATION 1 0 1 30 0\n", "5 5 ROTORCRAFT_STATUS 0 0\n",
                ])
                self.assertEqual(flight["path"], [])

    def test_reference_change_or_invalidation_requires_new_validation(self):
        for update in ("3 5 NAVIGATION_REF 500100 5400000 32\n",
                       "3 5 NAVIGATION_REF 500000 5400000 0\n"):
            with self.subTest(update=update):
                flight = self.parse([
                    self.reference, *self.calibration, update,
                    "4 5 NAVIGATION 1 0 1 30 0\n", "5 5 ROTORCRAFT_STATUS 0 0\n",
                ])
                self.assertEqual(flight["path"], [])

    def test_repeated_reference_preserves_validation_but_trust_expires(self):
        flight = self.parse([
            self.reference, *self.calibration,
            "3 5 NAVIGATION_REF 500000 5400000 32\n",
            "4 5 NAVIGATION 1 0 1 30 0\n",
            "122 5 NAVIGATION 1 0 119 40 0\n",
            "123 5 ROTORCRAFT_STATUS 0 0\n",
        ])
        self.assertEqual([p["time_s"] for p in flight["path"]], [4])
        self.assertIsNone(flight["landing"]["location"])

    def test_bad_sample_revokes_trust(self):
        for position in ("nan 0", "1000000 0", "3000 0"):
            with self.subTest(position=position):
                flight = self.parse([
                    self.reference, *self.calibration,
                    f"4 5 NAVIGATION 1 0 1 {position}\n",
                    "5 5 NAVIGATION 1 0 2 40 0\n", "6 5 ROTORCRAFT_STATUS 0 0\n",
                ])
                self.assertEqual(flight["path"], [])

    def test_gps_disagreement_revokes_trust(self):
        flight = self.parse([
            self.reference, *self.calibration,
            "3 5 GPS 3 50000000 540000000 100000 1000 32\n",
            "3.1 5 NAVIGATION 1 0 1 100 0\n",
            "6 5 NAVIGATION 1 0 4 110 0\n", "7 5 ROTORCRAFT_STATUS 0 0\n",
        ])
        self.assertEqual([p["time_s"] for p in flight["path"]], [3])

    def test_navigation_without_position_fields_keeps_legacy_behavior(self):
        flight = self.parse([
            self.reference, "1 5 NAVIGATION 1 0 0\n",
            "2 5 NAVIGATION 1 0 1\n", "5 5 NAVIGATION 1 0 0\n",
        ], messages=(("NAVIGATION", ("cur_block", "cur_stage", "flight_time")),))
        self.assertEqual(flight["duration_s"], 3)
        self.assertEqual(flight["path"], [])

    def test_navigation_endpoint_has_unknown_altitude(self):
        flight = self.parse([
            self.reference, *self.calibration, "4 5 NAVIGATION 1 0 1 30 0\n",
            "5 5 ROTORCRAFT_STATUS 0 0\n",
        ])
        self.assertIsNotNone(flight["landing"]["location"])
        self.assertIsNone(flight["landing"]["location"]["alt_m"])
