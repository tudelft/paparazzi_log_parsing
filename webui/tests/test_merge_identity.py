from .test_historical import AppTests


class MergeIdentityTests(AppTests):
    def sources(self, sd_drone, gcs_drone):
        for identity, kind, drone, name in (
                (1, "onboard_sd", sd_drone, "Airframe"),
                (2, "gcs", gcs_drone, "GCS configuration")):
            self.con.execute(
                "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by,project_id,log_type)"
                " VALUES(?,?,?,'test.data','done','now',2,1,?)", (identity, kind, kind, kind))
            self.con.execute(
                "INSERT INTO aircraft(id,log_id,drone_id,name,ac_id) VALUES(?,?,?,?,7)",
                (identity, identity, drone, name))
            self.con.execute(
                "INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc,landing_utc,"
                "takeoff_lat,takeoff_lon,landed,verdict)"
                " VALUES(?,?,1,100,'2010-01-01T12:00:00.000Z','2010-01-01T12:01:40.000Z',52,4,1,'flight')",
                (identity, identity))
        self.con.commit()

    def test_linked_source_identity_survives_on_both_apis_and_detail(self):
        for sd, gcs in ((None, 1), (1, None), (1, 1)):
            with self.subTest(sd=sd, gcs=gcs):
                self.con.execute("DELETE FROM logs")
                self.sources(sd, gcs)
                for endpoint in ("/api/logbook", "/api/stats"):
                    result = self.client.get(endpoint).json
                    rows = result["flights"] if endpoint == "/api/stats" else result
                    self.assertEqual(len(rows), 1)
                    self.assertEqual((rows[0]["aircraft"], rows[0]["drone_id"], rows[0]["aircraft_uid"]),
                                     ("Drone", 1, "UID"))
                detail = self.client.get("/api/flights/1/detail").json["entry"]
                self.assertEqual(detail["flight_id"], 1)
                self.assertEqual(detail["drone_id"], 1)
                self.assertEqual(len(detail["sources"]), 2)

    def test_conflicting_fleet_identities_are_not_merged(self):
        self.con.execute("INSERT INTO drones(id,name,unique_id,created_at) VALUES(2,'Other Drone','OTHER','now')")
        self.sources(1, 2)
        rows = self.client.get("/api/logbook").json
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["drone_id"] for r in rows}, {1, 2})
        self.assertTrue(all(len(r["sources"]) == 1 for r in rows))

    def test_unlinked_sources_keep_existing_name_without_inventing_identity(self):
        self.sources(None, None)
        rows = self.client.get("/api/logbook").json
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["aircraft"], "GCS configuration")
        self.assertIsNone(rows[0]["drone_id"])
        self.assertIsNone(rows[0]["aircraft_uid"])
