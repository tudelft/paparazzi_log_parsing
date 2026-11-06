from .test_historical import AppTests
from .test_merge_identity import MergeIdentityTests


class ReparseVisibilityTests(AppTests):
    sources = MergeIdentityTests.sources

    def setUp(self):
        super().setUp()
        self.sources(1, 1)
        self.con.execute("UPDATE aircraft SET pic='Pilot'")
        self.con.execute("UPDATE flights SET has_gps_position=1, project_id=1")
        self.con.commit()

    def flights_shown(self):
        return sum(len(r["sources"]) for r in self.client.get("/api/logbook").json)

    def test_previous_flights_stay_visible_while_a_log_is_reparsed(self):
        shown = self.flights_shown()
        self.assertGreater(shown, 0)
        for status in ("queued", "parsing"):
            self.con.execute("UPDATE logs SET status=?", (status,))
            self.con.commit()
            self.assertEqual(self.flights_shown(), shown, status)
            self.assertEqual(self.client.get("/api/status").json["counts"][status], 2)
        self.con.execute("UPDATE logs SET status='error'")  # a failed parse still hides its flights
        self.con.commit()
        self.assertEqual(self.flights_shown(), 0)
