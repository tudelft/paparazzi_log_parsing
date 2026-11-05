import json

import db
from .test_historical import AppTests
from .test_merge_identity import MergeIdentityTests

LINK = {"reports": 120, "mean_ping_ms": 15.0, "max_ping_ms": 181.2, "longest_gap_s": 4.2, "error_pct": 0.0,
        "min_rssi_dbm": -91, "min_rssi_ac_dbm": None}


class LinkQualityTests(AppTests):
    sources = MergeIdentityTests.sources

    def setUp(self):
        super().setUp()
        self.sources(1, 1)
        self.con.execute("UPDATE aircraft SET pic='Pilot'")
        self.con.execute("UPDATE flights SET has_gps_position=1, project_id=1")
        self.con.commit()

    def test_warning_thresholds(self):
        warning = self.ns["link_warning"]
        self.assertIsNone(warning(None))
        self.assertIsNone(warning({"longest_gap_s": 2.9, "error_pct": 4.9}))
        self.assertEqual(warning({"longest_gap_s": 4.2, "error_pct": 6}), "no datalink for 4.2 s, 6% bad frames")

    def test_logbook_and_flight_page_show_the_link_summary(self):
        self.con.execute("UPDATE flights SET link_json=?", (json.dumps(LINK),))
        self.con.commit()
        row = self.client.get("/api/logbook").json[0]
        self.assertEqual(row["link"], LINK)
        self.assertEqual(row["link_warning"], "no datalink for 4.2 s")
        compact = self.client.get("/api/logbook?compact=1").json[0]
        self.assertEqual(compact["link_warning"], "no datalink for 4.2 s")
        self.assertNotIn("link", compact)
        detail = self.client.get(f'/api/flights/{row["flight_id"]}/detail').json
        self.assertEqual(detail["entry"]["link"], LINK)

    def test_store_parsed_keeps_the_parser_link_summary(self):
        doc = {"log": {}, "aircraft": [{"ac_id": 5, "name": "Drone", "flights": [
            {"flight": 1, "duration_s": 60, "takeoff": {"time_s": 0}, "landing": {"time_s": 60},
             "assessment": {"verdict": "flight"}, "link": LINK, "path": []},
            {"flight": 2, "duration_s": 60, "takeoff": {"time_s": 100}, "landing": {"time_s": 160},
             "assessment": {"verdict": "flight"}, "link": None, "path": []}]}]}
        db.store_parsed(self.con, 1, doc, None)
        stored = [r[0] for r in self.con.execute(
            "SELECT f.link_json FROM flights f JOIN aircraft a ON a.id=f.aircraft_id WHERE a.log_id=1 ORDER BY f.idx")]
        self.assertEqual([json.loads(s) if s else None for s in stored], [LINK, None])
