"""Previous/next flight on the flight page: same project, by takeoff, then log name (natural) and flight number."""
from .test_historical import AppTests


class FlightNeighbourTests(AppTests):
    def test_order_and_project(self):
        rows = [dict(flight_id=1, project_id=1, takeoff_utc="2026-11-10T09:00:00Z", log_name="a.data", idx=0),
                dict(flight_id=9, project_id=1, takeoff_utc=None, date_override="2026-11-18", log_name="LOG00018.BFL", idx=0),
                dict(flight_id=5, project_id=1, takeoff_utc=None, date_override="2026-11-18", log_name="LOG00015.BFL", idx=0),
                dict(flight_id=7, project_id=2, takeoff_utc="2026-11-12T09:00:00Z", log_name="b.data", idx=0),
                dict(flight_id=None, manual_id=3, project_id=1, takeoff_utc="2026-11-11")]  # paper record: no page
        neighbours = self.ns["neighbour_flights"]
        self.assertEqual(neighbours(rows, rows[0]), (None, 5))
        self.assertEqual(neighbours(rows, rows[2]), (1, 9))  # LOG00015 before LOG00018
        self.assertEqual(neighbours(rows, rows[1]), (5, None))
        self.assertEqual(neighbours(rows, rows[3]), (None, None))  # alone in project 2
        self.assertEqual(neighbours(rows, dict(rows[0], project_id=None)), (None, None))  # no project


if __name__ == "__main__":
    import unittest
    unittest.main()
