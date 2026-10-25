import json
from unittest.mock import patch

import db
from .test_historical import AppTests


class LocationFallbackTests(AppTests):
    def insert_flight(self, takeoff=(None, None), landing=(None, None), path=None, identity=1,
                      kind="onboard_sd"):
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,paths_file,status,uploaded_at,uploaded_by,project_id,log_type,"
            "start_utc) VALUES(?,?,?,'test.data',?,'done','now',2,1,?,'2010-01-01T11:59:00.000Z')",
            (identity, str(identity), str(identity), f"location-{identity}.json.gz" if path is not None else None, kind))
        self.con.execute("INSERT INTO aircraft(id,log_id,name,ac_id) VALUES(?,?,'Drone',7)", (identity, identity))
        self.con.execute(
            "INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc,landing_utc,"
            " takeoff_lat,takeoff_lon,landing_lat,landing_lon,takeoff_json,landing_json,verdict,path_idx)"
            " VALUES(?,?,1,60,'2010-01-01T12:00:00.000Z',NULL,?,?,?,?,?,NULL,'flight',0)",
            (identity, identity, *takeoff, *landing, json.dumps({"location": None})))
        self.con.execute("UPDATE flights SET has_gps_position=? WHERE id=?",
                         (int(db.valid_gps_location(*takeoff) or db.valid_gps_location(*landing)
                              or db.path_has_gps_position(path or [])), identity))
        if path is not None:
            target = self.ns["STORAGE_DIR"] / f"location-{identity}.json.gz"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(db.pack([path]))
            self.addCleanup(target.unlink, missing_ok=True)
            self.addCleanup(self.ns["load_paths"].cache_clear)
        self.con.commit()

    def test_each_missing_endpoint_uses_other_without_changing_events_or_database(self):
        for takeoff, landing, inferred in (((52, 4), (None, None), "landing"),
                                           ((None, None), (52, 4), "takeoff"),
                                           ((0, 0), (52, 4), "takeoff")):
            with self.subTest(inferred=inferred, takeoff=takeoff):
                self.con.execute("DELETE FROM logs")
                self.insert_flight(takeoff, landing)
                with patch.dict(self.ns, flight_path=lambda *args, **kwargs: self.fail("No path scan needed")):
                    entry = self.client.get("/api/logbook").json[0]
                for end in ("takeoff", "landing"):
                    self.assertEqual((entry[end + "_lat"], entry[end + "_lon"]), (52, 4))
                    self.assertEqual(entry[end + "_place"], "Field")
                    self.assertEqual(entry[end + "_place_id"], 1)
                    self.assertEqual(entry[end + "_location_inferred"], end == inferred)
                self.assertFalse(entry["indoor"])
                detail = self.client.get("/api/flights/1/detail").json["entry"]
                self.assertEqual(detail["takeoff"], {"location": None})
                self.assertIsNone(detail["landing"])
                self.assertIsNone(detail["landing_utc"])
                if inferred == "takeoff":
                    weather = self.client.get("/api/flights/1/weather")
                    self.assertEqual(weather.status_code, 400)
                    self.assertIn("inferred", weather.json["error"])
                raw = self.con.execute("SELECT * FROM flights").fetchone()
                self.assertEqual((raw["takeoff_lat"], raw["takeoff_lon"]), takeoff)
                self.assertEqual((raw["landing_lat"], raw["landing_lon"]), landing)
                upload = self.client.get("/api/logs/1").json["aircraft"][0]["flights"][0]
                self.assertEqual((upload[inferred + "_lat"], upload[inferred + "_lon"]), (52, 4))

    def test_known_distinct_endpoints_stay_distinct(self):
        self.insert_flight((52, 4), (53, 5))
        entry = self.client.get("/api/logbook").json[0]
        self.assertEqual((entry["landing_lat"], entry["landing_lon"]), (53, 5))
        self.assertFalse(entry["takeoff_location_inferred"])
        self.assertFalse(entry["landing_location_inferred"])

    def test_places_marks_unnamed_endpoints_and_refreshes_after_naming(self):
        self.insert_flight((52, 4), (53, 5))
        points = self.client.get("/api/places").json["points"]
        self.assertEqual(points, [
            {"lat": 52, "lon": 4, "count": 1, "unnamed_count": 0},
            {"lat": 53, "lon": 5, "count": 1, "unnamed_count": 1},
        ])
        self.sign_in(1)
        response = self.client.post("/api/admin/places", json={
            "name": "New field", "shape": {"type": "circle", "center": [53, 5], "radius_m": 200},
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(p["unnamed_count"] == 0 for p in self.client.get("/api/places").json["points"]))
        self.sign_in(4)
        self.assertEqual(self.client.get("/api/places").json["points"], [])

    def test_places_unnamed_count_uses_exact_endpoints_before_rounding(self):
        self.con.execute("UPDATE places SET shape=? WHERE id=1",
                         (json.dumps({"type": "circle", "center": [52, 4], "radius_m": 10}),))
        self.insert_flight((52, 4), (52.0004, 4))
        self.assertEqual(self.client.get("/api/places").json["points"],
                         [{"lat": 52, "lon": 4, "count": 2, "unnamed_count": 1}])

    def test_no_endpoint_or_path_gps_is_indoor_in_all_shared_views(self):
        self.insert_flight(path=[{"position": {"lat": 0, "lon": 0}}])
        entry = self.client.get("/api/logbook").json[0]
        self.assertTrue(entry["indoor"])
        self.assertTrue(entry["indoor_no_gps"])
        self.assertIsNone(entry["takeoff_lat"])
        self.assertTrue(self.client.get("/api/stats").json["flights"][0]["indoor"])
        self.assertTrue(self.client.get("/api/flights/1/detail").json["entry"]["indoor_no_gps"])

    def test_no_endpoints_but_valid_path_is_inferred_indoor(self):
        self.insert_flight(path=[{"position": {"lat": 52, "lon": 4}}])
        entry = self.client.get("/api/logbook").json[0]
        self.assertTrue(entry["indoor"])
        self.assertTrue(entry["indoor_no_gps"])
        self.assertIsNone(entry["takeoff_lat"])
        self.assertTrue(self.client.get("/api/stats").json["flights"][0]["indoor"])
        self.assertTrue(self.client.get("/api/detail/projects/1").json["flights"][0]["indoor"])
        self.assertTrue(self.client.get("/api/flights/1/detail").json["entry"]["indoor_no_gps"])
        self.assertEqual(self.client.get("/api/flights/1/path").json,
                         [{"position": {"lat": 52, "lon": 4}}])

    def test_merged_secondary_path_does_not_replace_endpoint_evidence(self):
        self.insert_flight(path=[])
        self.insert_flight(path=[{"position": {"lat": 52, "lon": 4}}], identity=2, kind="gcs")
        entry = self.client.get("/api/logbook").json[0]
        self.assertEqual(len(entry["sources"]), 2)
        self.assertTrue(entry["indoor"])

    def test_merged_secondary_endpoint_is_used_before_indoor_inference(self):
        self.insert_flight()
        self.insert_flight(landing=(52, 4), identity=2, kind="gcs")
        entry = self.client.get("/api/logbook").json[0]
        self.assertEqual(len(entry["sources"]), 2)
        self.assertFalse(entry["indoor"])
        self.assertEqual((entry["takeoff_lat"], entry["landing_lat"]), (52, 52))
        self.assertTrue(entry["takeoff_location_inferred"])

    def test_missing_paths_and_invalid_endpoints_are_no_gps(self):
        for coords in ((None, None), (0, 0), (91, 4), (52, None)):
            with self.subTest(coords=coords):
                self.con.execute("DELETE FROM logs")
                self.insert_flight(takeoff=coords, landing=coords)
                entry = self.client.get("/api/logbook").json[0]
                self.assertTrue(entry["indoor_no_gps"])
                self.assertIsNone(entry["takeoff_lat"])

    def test_no_gps_does_not_modify_paper_records(self):
        response = self.create(place_id=None)
        self.assertEqual(response.status_code, 201)
        entry = self.client.get("/api/logbook").json[0]
        self.assertEqual(entry["source_type"], "paper")
        self.assertFalse(entry["indoor"])

    def test_overviews_use_summary_without_loading_paths(self):
        self.insert_flight(path=[{"position": {"lat": 52, "lon": 4}}])
        with patch.dict(self.ns, load_paths=lambda *args: self.fail("Overview loaded paths"),
                        flight_path=lambda *args, **kwargs: self.fail("Overview loaded a path")):
            for url in ("/api/logbook", "/api/stats", "/api/detail/projects/1", "/api/places"):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, url)
            self.assertTrue(self.client.get("/api/logbook").json[0]["indoor"])

    def test_legacy_backfill_preserves_gps_detection_and_is_idempotent(self):
        self.insert_flight(path=[{"position": {"lat": 52, "lon": 4}}])
        self.insert_flight(path=[{"position": {"lat": 0, "lon": 0}}], identity=2)
        self.insert_flight(takeoff=(52, 4), identity=3)
        self.con.execute("UPDATE flights SET has_gps_position=NULL")
        self.con.commit()
        self.ns["backfill_gps_positions"](self.con)
        self.assertEqual([r[0] for r in self.con.execute("SELECT has_gps_position FROM flights ORDER BY id")],
                         [1, 0, 1])
        with patch("pathlib.Path.read_bytes", side_effect=AssertionError("Already backfilled")):
            self.ns["backfill_gps_positions"](self.con)

    def test_missing_legacy_path_fails_explicitly_and_can_retry(self):
        self.insert_flight(path=[{"position": {"lat": 52, "lon": 4}}])
        self.con.execute("UPDATE flights SET has_gps_position=NULL")
        self.con.commit()
        with patch("pathlib.Path.read_bytes", side_effect=FileNotFoundError("missing share file")):
            with self.assertLogs(self.app.logger, level="ERROR"), self.assertRaises(FileNotFoundError):
                self.ns["backfill_gps_positions"](self.con)
        self.assertIsNone(self.con.execute("SELECT has_gps_position FROM flights").fetchone()[0])
        self.assertEqual(self.client.get("/api/logbook").status_code, 503)
        self.ns["backfill_gps_positions"](self.con)
        self.assertTrue(self.client.get("/api/logbook").json[0]["indoor"])

    def test_legacy_blob_backfill_and_parse_storage(self):
        self.insert_flight()
        self.con.execute("ALTER TABLE flights ADD COLUMN path_gz BLOB")
        self.con.execute("UPDATE flights SET has_gps_position=NULL,path_gz=?",
                         (db.pack([{"position": {"lat": 52, "lon": 4}}]),))
        self.con.commit()
        self.ns["backfill_gps_positions"](self.con)
        self.assertEqual(self.con.execute("SELECT has_gps_position FROM flights").fetchone()[0], 1)
        for path, expected in (([], 0), ([{"position": {"lat": 52, "lon": 4}}], 1),
                               ([{"position": {"lat": 0, "lon": 0}}], 0)):
            doc = {"aircraft": [{"flights": [{"path": path}]}]}
            db.store_parsed(self.con, 1, doc, None)
            self.assertEqual(self.con.execute("SELECT has_gps_position FROM flights").fetchone()[0], expected)
        db.store_parsed(self.con, 1, {"aircraft": [{"flights": [
            {"takeoff": {"location": {"lat": 52, "lon": 4}}, "path": []}]}]}, None)
        self.assertEqual(self.con.execute("SELECT has_gps_position FROM flights").fetchone()[0], 1)

    def test_backfill_reads_each_log_once_and_preserves_flight_data(self):
        self.insert_flight(path=[{"position": {"lat": 52, "lon": 4}}])
        self.con.execute("UPDATE flights SET has_gps_position=NULL")
        self.con.execute("INSERT INTO flights(aircraft_id,idx,path_idx,duration_s) VALUES(1,2,0,25)")
        self.con.commit()
        with patch("pathlib.Path.read_bytes", return_value=db.pack([
                [{"position": {"lat": 52, "lon": 4}}]])) as read:
            self.ns["backfill_gps_positions"](self.con)
            read.assert_called_once()
        self.assertEqual([tuple(r) for r in self.con.execute(
            "SELECT duration_s,has_gps_position FROM flights ORDER BY id")], [(60, 1), (25, 1)])
