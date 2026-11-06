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

    def test_uplink_warning_and_merging_gcs_and_sd_summaries(self):
        warning, merge = self.ns["link_warning"], self.ns["merge_links"]
        self.assertIsNone(warning({"longest_uplink_gap_s": 9, "uplink_error_pct": 4}))
        self.assertEqual(warning({"longest_uplink_gap_s": 12, "uplink_error_pct": 7}),
                         "no uplink for 12 s, 7% bad uplink frames")
        gcs = dict(LINK, longest_uplink_gap_s=None, uplink_reports=0)
        sd = {"reports": 0, "mean_ping_ms": None, "longest_gap_s": None, "uplink_reports": 16,
              "longest_uplink_gap_s": 11, "uplink_error_pct": 0}
        merged = merge([gcs, sd, None])
        self.assertEqual((merged["longest_gap_s"], merged["longest_uplink_gap_s"], merged["min_rssi_dbm"],
                          merged["mean_ping_ms"], merged["reports"], merged["uplink_reports"]),
                         (4.2, 11, -91, 15.0, 120, 16))
        self.assertIs(merge([None, sd]), sd)
        self.assertIsNone(merge([None]))

    def test_sd_path_gets_the_gcs_link_samples_by_utc(self):
        gcs_path = [{"utc": "2026-11-06T10:00:00.000Z", "link": {"ping_ms": 6, "rssi_dbm": -70, "uplink_lost_s": None}},
                    {"utc": "2026-11-06T10:00:10.000Z", "link": {"ping_ms": 9, "rssi_dbm": -80, "uplink_lost_s": None}}]
        sd_path = [{"utc": "2026-11-06T10:00:01.000Z", "link": {"ping_ms": None, "uplink_lost_s": 0}},
                   {"utc": "2026-11-06T10:00:05.000Z", "link": None},
                   {"utc": "2026-11-06T10:00:09.500Z"}]
        original = [dict(p) for p in sd_path]
        loads = self.ns["flight_path"]
        self.ns["flight_path"] = lambda *args, **kw: gcs_path
        self.addCleanup(self.ns.__setitem__, "flight_path", loads)
        self.ns["add_link_from_other_logs"](self.con, sd_path, [self.con.execute("SELECT MIN(id) FROM flights").fetchone()[0]])
        self.assertEqual(sd_path[0]["link"], {"ping_ms": 6, "rssi_dbm": -70, "uplink_lost_s": 0})  # own value kept
        self.assertIsNone(sd_path[1]["link"])  # 4 s from the nearest GCS sample
        self.assertEqual(sd_path[2]["link"]["ping_ms"], 9)
        self.assertEqual(original[0]["link"], {"ping_ms": None, "uplink_lost_s": 0})  # points are copied, not changed

    def test_logbook_and_flight_page_show_the_link_summary(self):
        self.con.execute("UPDATE flights SET link_json=? WHERE id=(SELECT MIN(id) FROM flights)", (json.dumps(LINK),))
        self.con.commit()  # one recording (the GCS log) has link reports; the merged entry shows them
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
