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
        fid = self.con.execute("SELECT MIN(id) FROM flights").fetchone()[0]
        path, _, _, _ = self.ns["combine_other_logs"](self.con, {"base": None}, sd_path, [], [], [fid])
        self.assertEqual(path[0]["link"], {"ping_ms": 6, "rssi_dbm": -70, "uplink_lost_s": 0})  # own value kept
        self.assertIsNone(path[1]["link"])  # 4 s from the nearest GCS sample
        self.assertEqual(path[2]["link"]["ping_ms"], 9)
        self.assertEqual(sd_path, original)  # the (cached) input is not changed

    def test_messages_settings_and_path_gaps_from_the_other_log(self):
        # this log's time 0 is at epoch 1000; the GCS log's at 1100 (its times are 100 s smaller)
        def point(t, utc_t, lat=None):
            return {"time_s": t, "utc": None, "distance_m": 5,
                    "position": {"lat": lat, "lon": 4.0 if lat else None}}
        gcs = {"label": "GCS log", "base": 1100.0,
               "path": [point(20, 0, 52.0), point(30, 0, 52.1), point(40, 0, 52.2)],
               "messages": [{"time_s": 10.5, "text": "Takeoff"}, {"time_s": 25, "text": "Low battery"}],
               "changes": [{"time_s": 26, "idx": 3, "to_value": 1.0}, {"time_s": 12, "idx": 2, "to_value": 5.0}]}
        sd_path = [point(120, 0), point(150, 0, 51.9)]  # no position at 120; nothing between 121 and 149
        messages = [{"time_s": 111, "text": "Takeoff"}]
        changes = [{"time_s": 112, "idx": 2, "to_value": 5.0}]
        originals = self.ns["other_recordings"]
        self.ns["other_recordings"] = lambda con, ids: [gcs]
        self.addCleanup(self.ns.__setitem__, "other_recordings", originals)
        own = {"base": 1000.0, "takeoff_time_s": 100, "landing_time_s": 160}
        path, msgs, chs, added = self.ns["combine_other_logs"](self.con, own, sd_path, messages, changes, [1])
        self.assertEqual([m["text"] for m in msgs], ["Takeoff", "Low battery"])  # the duplicate Takeoff is dropped
        self.assertEqual((msgs[1]["time_s"], msgs[1]["log"]), (125, "GCS log"))
        self.assertEqual([(c["idx"], c["time_s"]) for c in chs], [(2, 112), (3, 126)])
        self.assertEqual([p["time_s"] for p in path], [120, 130, 140, 150])
        self.assertEqual(path[0]["position"], {"lat": 52.0, "lon": 4.0, "source": "other_log"})  # filled at 120
        self.assertEqual((path[1]["from_other_log"], path[1]["distance_m"]), ("GCS log", None))  # gap filled
        self.assertEqual(added, 3)
        self.assertEqual(sd_path[0]["position"]["lat"], None)  # inputs unchanged

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
