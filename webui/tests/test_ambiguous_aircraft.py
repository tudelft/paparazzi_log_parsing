import os
from unittest.mock import patch

import db
from .test_historical import AppTests
from .test_merge_identity import MergeIdentityTests


class AmbiguousAircraftTests(AppTests):
    sources = MergeIdentityTests.sources

    def setUp(self):
        super().setUp()
        self.sign_in(1)
        self.con.execute("INSERT INTO drones(id,name,unique_id,created_at) VALUES(2,'Other','OTHER','now')")
        self.con.execute("INSERT INTO drone_aliases(drone_id,alias) VALUES(2,'Airframe')")
        self.con.execute("INSERT INTO drone_aliases(drone_id,alias) VALUES(1,'GCS configuration')")
        self.sources(2, 1)
        self.con.execute("UPDATE flights SET has_gps_position=1")
        self.con.commit()

    def mark(self):
        response = self.client.post("/api/admin/ambiguous-drone-names", json={"name": "Airframe"})
        self.assertEqual(response.status_code, 200, response.json)

    def test_remove_alias_does_not_recreate_it_from_folder_or_scan_unrelated_logs(self):
        self.con.execute("UPDATE logs SET name='Other/flight.data' WHERE id=1")
        self.con.commit()
        original = self.ns["link_log_aircraft"]
        calls = []

        def link(con, log_id, **kwargs):
            calls.append(log_id)
            return original(con, log_id, **kwargs)

        with patch.dict(self.ns, link_log_aircraft=link):
            response = self.client.delete("/api/admin/drone-aliases", json={"alias": "Airframe"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.con.execute("SELECT 1 FROM drone_aliases WHERE alias='Airframe'").fetchone())
        self.assertEqual(calls, [1])
        self.assertEqual(self.con.execute("SELECT drone_id FROM aircraft WHERE id=1").fetchone()[0], 2)

    def test_detail_save_relinks_only_after_a_rename_and_builds_the_name_map_once(self):
        def counting(name):
            original, calls = self.ns[name], []

            def wrapper(*args, **kwargs):
                calls.append(1)
                return original(*args, **kwargs)
            return calls, patch.dict(self.ns, {name: wrapper})

        relinks, relink_patch = counting("relink_all_aircraft")
        with relink_patch:
            response = self.client.put("/api/admin/drones/2", json=dict(manager="Pilot", status="Ready", comments="x"))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(relinks, [])
        for i in range(100, 120):  # unrelated logs must not each rebuild the name map
            self.con.execute("INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by)"
                             " VALUES (?,?,?,?,'done','now',1)", (i, f"log{i}.data", f"{i:064x}", f"x/{i}.data.gz"))
        self.con.commit()
        maps, map_patch = counting("drone_name_map")
        with map_patch:
            response = self.client.put("/api/admin/drones/2", json=dict(name="Renamed"))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertLess(len(maps), 5)  # built once for all logs (plus after a remembered alias), not once per log
        self.assertIsNotNone(self.con.execute(
            "SELECT 1 FROM drone_aliases WHERE drone_id=2 AND alias='Other'").fetchone())
        self.assertEqual(self.con.execute("SELECT drone_id FROM aircraft WHERE id=1").fetchone()[0], 2)

    def test_remove_alias_unlinks_matching_names_and_board_tokens_but_preserves_overrides(self):
        self.con.execute("INSERT INTO drone_aliases(drone_id,alias) VALUES(2,'00500040')")
        self.con.execute("UPDATE aircraft SET name='quad_x 00500040' WHERE id=1")
        self.con.commit()
        response = self.client.delete("/api/admin/drone-aliases", json={"alias": "00500040"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.con.execute("SELECT drone_id FROM aircraft WHERE id=1").fetchone()[0])
        self.con.execute("INSERT INTO log_aircraft_links(log_id,ac_id,drone_id) VALUES(1,7,2)")
        self.con.execute("UPDATE aircraft SET name='Airframe' WHERE id=1")
        self.con.commit()
        self.client.delete("/api/admin/drone-aliases", json={"alias": "Airframe"})
        self.assertEqual(self.con.execute("SELECT drone_id FROM aircraft WHERE id=1").fetchone()[0], 2)
        self.assertEqual(self.con.execute("SELECT drone_id FROM aircraft WHERE id=2").fetchone()[0], 1)

    def unassign(self):
        """After marking: SD recording 1 (shared name) unlinked without a project, GCS recording 2 on project 2."""
        self.mark()
        self.con.execute("UPDATE flights SET project_id=NULL, project_source=NULL")
        self.con.execute("UPDATE flights SET project_id=2, project_source='timeline' WHERE id=2")
        self.con.execute("UPDATE logs SET project_id=NULL WHERE id=1")
        self.con.commit()

    def test_unlinked_shared_name_takes_the_project_of_its_linked_recording(self):
        self.unassign()
        entry = self.client.get("/api/logbook").json[0]
        self.assertEqual((entry["flight_id"], entry["drone_id"], entry["project_id"]), (1, 1, 2))  # shown at once
        self.ns["assign_open_flights"](self.con)
        self.assertEqual(tuple(self.con.execute("SELECT project_id, project_source FROM flights WHERE id=1").fetchone()),
                         (2, "timeline"))  # and stored, so the SD files follow the project
        self.assertEqual(self.con.execute("SELECT project_id FROM logs WHERE id=1").fetchone()[0], 2)

    def test_split_or_manual_choices_keep_their_project(self):
        self.unassign()
        self.con.execute("INSERT INTO source_links(a_log_id,a_ac_id,a_idx,b_log_id,b_ac_id,b_idx,same,created_at)"
                         " VALUES (1,7,1,2,7,1,0,'now')")
        self.con.commit()
        self.ns["assign_open_flights"](self.con)
        self.assertIsNone(self.con.execute("SELECT project_id FROM flights WHERE id=1").fetchone()[0])
        self.con.execute("DELETE FROM source_links")
        self.con.execute("UPDATE flights SET project_source='manual' WHERE id=1")  # "No project" chosen by hand
        self.con.commit()
        self.ns["assign_open_flights"](self.con)
        self.assertIsNone(self.con.execute("SELECT project_id FROM flights WHERE id=1").fetchone()[0])
        rows = self.client.get("/api/logbook").json
        self.assertIsNone(rows[0]["project_id"])

    def test_remove_alias_validation_and_permissions(self):
        self.assertEqual(self.client.delete("/api/admin/drone-aliases", json={"alias": ""}).status_code, 400)
        self.sign_in(2)
        self.assertEqual(self.client.delete("/api/admin/drone-aliases", json={"alias": "Airframe"}).status_code, 403)
        self.assertIsNotNone(self.con.execute("SELECT 1 FROM drone_aliases WHERE alias='Airframe'").fetchone())

    def test_mark_clears_only_generic_links_and_merges_with_specific_identity(self):
        self.assertEqual(len(self.client.get("/api/logbook").json), 2)
        self.con.execute("INSERT INTO aircraft(id,log_id,name,drone_id,ac_id) VALUES(3,1,'Unrelated legacy name',1,8)")
        self.con.commit()
        self.mark()
        self.assertEqual(self.con.execute("SELECT drone_id FROM aircraft WHERE id=3").fetchone()[0], 1)
        self.assertIsNone(self.con.execute("SELECT drone_id FROM aircraft WHERE id=1").fetchone()[0])
        self.assertEqual(self.con.execute("SELECT drone_id FROM aircraft WHERE id=2").fetchone()[0], 1)
        self.assertIsNone(self.con.execute("SELECT 1 FROM drone_aliases WHERE alias='Airframe'").fetchone())
        for uid in (1, 2, 3):
            self.sign_in(uid)
            with patch.dict(os.environ, LOGDB_HIDE_UNLINKED_AIRCRAFT="1"):
                for url in ("/api/logbook", "/api/stats", "/api/detail/locations/1",
                            "/api/detail/aircraft/1"):
                    payload = self.client.get(url).json
                    rows = payload if isinstance(payload, list) else payload["flights"]
                    self.assertEqual(len(rows), 1, url)
                    self.assertEqual(rows[0]["drone_id"], 1)
                for fid in (1, 2):
                    entry = self.client.get(f"/api/flights/{fid}/detail").json["entry"]
                    self.assertTrue(entry["aircraft_identity_inferred"])
                    self.assertEqual({s["flight_id"] for s in entry["sources"]}, {1, 2})

    def test_shared_name_never_links_by_folder_token_or_new_alias(self):
        self.mark()
        with patch.dict(self.ns, guess_drone_from_log_path=lambda *args: self.fail("Ambiguous folder match")):
            self.ns["link_log_aircraft"](self.con, 1)
        self.assertIsNone(self.con.execute("SELECT drone_id FROM aircraft WHERE id=1").fetchone()[0])
        response = self.client.post("/api/admin/drone-aliases", json={"alias": "AIRFRAME", "drone_id": 1})
        self.assertEqual(response.status_code, 409)
        self.ns["remember_drone_log_identity"](self.con, 1, "Airframe")
        self.assertIsNone(self.con.execute("SELECT 1 FROM drone_aliases WHERE alias='Airframe'").fetchone())

    def test_unresolved_without_gcs_or_with_wrong_id_position_or_time(self):
        self.mark()
        for sql in ("UPDATE aircraft SET ac_id=8 WHERE id=2",
                    "UPDATE flights SET takeoff_lat=53 WHERE id=2",
                    "UPDATE flights SET takeoff_lat=NULL WHERE id=2",
                    "UPDATE flights SET takeoff_utc='2011-01-01T12:00:00.000Z',"
                    "landing_utc='2011-01-01T12:01:40.000Z' WHERE id=2",
                    "UPDATE logs SET log_type='onboard_sd' WHERE id=2"):
            with self.subTest(sql=sql):
                self.con.execute("SAVEPOINT scenario")
                self.con.execute(sql)
                rows = self.client.get("/api/logbook").json
                self.assertEqual(len(rows), 2)
                self.assertEqual(sum(r["drone_id"] is None for r in rows), 1)
                self.con.execute("ROLLBACK TO scenario")
                self.con.execute("RELEASE scenario")
        self.con.execute("DELETE FROM logs WHERE id=2")
        self.con.commit()
        self.assertIsNone(self.client.get("/api/logbook").json[0]["drone_id"])
        with patch.dict(os.environ, LOGDB_HIDE_UNLINKED_AIRCRAFT="1"):
            self.assertEqual(self.client.get("/api/logbook").json, [])
            self.assertEqual(self.client.get("/api/flights/1/detail").status_code, 200)

    def test_multiple_plausible_fleet_identities_do_not_pick_first(self):
        self.mark()
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by,project_id,log_type)"
            " VALUES(3,'other','other','test.data','done','now',2,1,'gcs')")
        self.con.execute("INSERT INTO aircraft(id,log_id,name,drone_id,ac_id) VALUES(3,3,'Other',2,7)")
        self.con.execute(
            "INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc,landing_utc,"
            " takeoff_lat,takeoff_lon,landed,verdict) SELECT 3,3,1,duration_s,takeoff_utc,landing_utc,"
            " takeoff_lat,takeoff_lon,landed,verdict FROM flights WHERE id=2")
        self.con.commit()
        rows = self.client.get("/api/logbook").json
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(len(r["sources"]) == 1 for r in rows))
        self.assertIsNone(next(r for r in rows if r["flight_id"] == 1)["drone_id"])

    def test_two_ambiguous_sd_logs_do_not_resolve_one_another(self):
        self.con.execute("UPDATE aircraft SET name='Airframe' WHERE id=2")
        self.con.execute("UPDATE logs SET log_type='onboard_sd' WHERE id=2")
        self.con.commit()
        self.mark()
        rows = self.client.get("/api/logbook").json
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["drone_id"] is None and len(r["sources"]) == 1 for r in rows))

    def test_multiple_candidate_flights_on_same_gcs_log_remain_unresolved(self):
        self.mark()
        self.con.execute(
            "INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc,landing_utc,"
            " takeoff_lat,takeoff_lon,landed,verdict) SELECT 3,2,2,duration_s,takeoff_utc,landing_utc,"
            " takeoff_lat,takeoff_lon,landed,verdict FROM flights WHERE id=2")
        self.con.commit()
        rows = self.client.get("/api/logbook").json
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(len(r["sources"]) == 1 for r in rows))

    def test_september_actual_overlap_wins_over_neighbouring_slack_candidates(self):
        self.mark()
        self.con.execute(
            "UPDATE flights SET duration_s=50.850,takeoff_utc='2026-09-02T09:59:29.781Z',"
            "landing_utc='2026-09-02T10:00:20.631Z' WHERE id=1")
        self.con.execute(
            "UPDATE flights SET duration_s=51.605,takeoff_utc='2026-09-02T09:59:29.750Z',"
            "landing_utc='2026-09-02T10:00:21.355Z' WHERE id=2")
        self.con.execute(
            "INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc,landing_utc,"
            "takeoff_lat,takeoff_lon,landed,verdict,has_gps_position) VALUES"
            "(3,2,2,147.688,'2026-09-02T10:01:11.404Z','2026-09-02T10:03:39.092Z',52,4,1,'flight',1)")
        self.con.execute(
            "INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc,landing_utc,"
            "takeoff_lat,takeoff_lon,landed,verdict,has_gps_position) VALUES"
            "(4,1,2,147.674,'2026-09-02T10:01:11.031Z','2026-09-02T10:03:38.705Z',52,4,1,'flight',1)")
        self.con.commit()
        rows = self.client.get("/api/logbook").json
        self.assertEqual({frozenset(s["flight_id"] for s in r["sources"]) for r in rows},
                         {frozenset({1, 2}), frozenset({3, 4})})
        self.assertTrue(all(r["aircraft_identity_inferred"] for r in rows))

    def test_multiple_actual_overlaps_remain_unresolved_despite_better_timing(self):
        self.mark()
        self.con.execute(
            "INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc,landing_utc,"
            "takeoff_lat,takeoff_lon,landed,verdict,has_gps_position) VALUES"
            "(3,2,2,100,'2010-01-01T12:00:20.000Z','2010-01-01T12:02:00.000Z',52,4,1,'flight',1)")
        self.con.commit()
        rows = self.client.get("/api/logbook").json
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(len(r["sources"]) == 1 for r in rows))

    def test_manual_assignment_survives_mark_relink_and_reparse(self):
        route = "/api/admin/logs/1/aircraft/7"
        self.assertEqual(self.client.put(route, json={"drone_id": 2}).status_code, 200)
        self.mark()
        self.ns["relink_all_aircraft"](self.con)
        self.assertEqual(self.con.execute("SELECT drone_id FROM aircraft WHERE id=1").fetchone()[0], 2)
        self.assertEqual(len(self.client.get("/api/logbook").json), 2)
        doc = {"aircraft": [{"ac_id": 7, "name": "Airframe", "flights": []}]}
        db.store_parsed(self.con, 1, doc, None)
        self.ns["link_log_aircraft"](self.con, 1)
        self.con.commit()
        self.assertEqual(self.con.execute("SELECT drone_id FROM aircraft WHERE log_id=1").fetchone()[0], 2)
        self.assertEqual(self.client.put(route, json={"drone_id": None}).status_code, 200)
        self.assertIsNone(self.con.execute("SELECT drone_id FROM aircraft WHERE log_id=1").fetchone()[0])

    def test_case_insensitive_mark_is_idempotent_and_persists_across_initialization(self):
        self.mark()
        self.assertEqual(self.client.post("/api/admin/ambiguous-drone-names",
                                        json={"name": "  AIRFRAME  "}).status_code, 200)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM ambiguous_drone_names").fetchone()[0], 1)
        self.con.executescript(db.SCHEMA)
        self.assertEqual(self.con.execute("SELECT name FROM ambiguous_drone_names").fetchone()[0], "Airframe")
        self.ns["link_log_aircraft"](self.con, 1)
        self.assertIsNone(self.con.execute("SELECT drone_id FROM aircraft WHERE id=1").fetchone()[0])

    def test_person_role_totals_match_statistics_when_unlinked_aircraft_hidden(self):
        self.mark()
        self.con.execute("DELETE FROM logs WHERE id=2")
        self.con.execute("UPDATE aircraft SET pic='Pilot',pac='Pilot' WHERE id=1")
        self.con.commit()
        for setting, expected in (("0", 1), ("1", 0)):
            with patch.dict(os.environ, LOGDB_HIDE_UNLINKED_AIRCRAFT=setting):
                for uid in (1, 2, 3):
                    self.sign_in(uid)
                    stats = self.client.get("/api/stats").json["flights"]
                    detail = self.client.get("/api/detail/people/2")
                    self.assertEqual(detail.status_code, 200)
                    mine = detail.json["flights"]
                    self.assertEqual(len(mine), expected)
                    for role in ("pic", "pac"):
                        seconds = lambda rows: sum(r["duration_s"] or 0 for r in rows
                                                   if (r.get(role + "_user") or {}).get("id") == 2)
                        self.assertEqual(seconds(mine), seconds(stats))
                    # Uploaded files remain accessible even when excluded from flight totals.
                    self.assertEqual(self.client.get("/api/flights/1/detail").status_code, 200)

    def test_admin_management_visibility_validation_and_remove(self):
        for name in ("", " ", 123, "x" * 81):
            self.assertEqual(self.client.post("/api/admin/ambiguous-drone-names", json={"name": name}).status_code, 400)
        self.assertEqual(self.client.post("/api/admin/ambiguous-drone-names", json={"name": "Drone"}).status_code, 409)
        self.mark()
        payload = self.client.get("/api/drones").json
        self.assertEqual(payload["ambiguous"][0]["name"], "Airframe")
        self.assertEqual(payload["ambiguous"][0]["recordings"][0]["log_id"], 1)
        self.assertFalse(any(r["name"] == "Airframe" for r in payload["unlinked"]))
        for drone in ("2", 999, True):
            self.assertEqual(self.client.put("/api/admin/logs/1/aircraft/7", json={"drone_id": drone}).status_code, 400)
        self.assertEqual(self.client.put("/api/admin/logs/1/aircraft/7", json={}).status_code, 400)
        self.assertEqual(self.client.post("/api/admin/ambiguous-drone-names", json=[]).status_code, 400)
        self.sign_in(3)
        self.assertEqual(self.client.get("/api/drones").json["ambiguous"], [])
        self.assertEqual(self.client.post("/api/admin/ambiguous-drone-names", json={"name": "test"}).status_code, 403)
        self.assertEqual(self.client.put("/api/admin/logs/1/aircraft/7", json={"drone_id": 1}).status_code, 403)
        self.sign_in(1)
        admin = dict(self.con.execute("SELECT * FROM users WHERE id=1").fetchone())
        with patch.dict(self.ns, user_for_key=lambda key: admin):
            self.assertEqual(self.client.post("/api/admin/ambiguous-drone-names", json={"name": "test"},
                                             headers={"Authorization": "Bearer test"}).status_code, 403)
            self.assertEqual(self.client.put("/api/admin/logs/1/aircraft/7", json={"drone_id": 1},
                                            headers={"Authorization": "Bearer test"}).status_code, 403)
        self.assertEqual(self.client.delete("/api/admin/ambiguous-drone-names", json={"name": "Airframe"}).status_code, 200)
        self.assertIsNone(self.con.execute("SELECT 1 FROM drone_aliases WHERE alias='Airframe'").fetchone())
