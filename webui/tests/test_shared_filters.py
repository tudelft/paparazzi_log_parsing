from .test_historical import AppTests
from .test_merge_identity import MergeIdentityTests


class SharedFilterTests(AppTests):
    sources = MergeIdentityTests.sources

    def setUp(self):
        super().setUp()
        self.sources(1, 1)
        self.con.execute("UPDATE logs SET start_utc='2010-01-01T12:00:00Z'")
        self.con.execute("UPDATE logs SET start_utc='2011-01-01T12:00:00Z' WHERE id=2")
        self.con.execute(
            "UPDATE flights SET takeoff_utc='2011-01-01T12:00:00Z',"
            " landing_utc='2011-01-01T12:01:40Z' WHERE id=2")
        self.con.commit()

    def test_locations_period_filters_counts_and_points_but_retains_accessible_boundaries(self):
        for uid in (1, 2, 3):
            self.sign_in(uid)
            for query, expected in (("", 2), ("?from=2010-01-01&to=2010-01-01", 1),
                                    ("?from=2011-01-01", 1), ("?to=2009-12-31", 0)):
                with self.subTest(uid=uid, query=query):
                    response = self.client.get("/api/places" + query)
                    self.assertEqual(response.status_code, 200)
                    data = response.json
                    self.assertEqual(len(data["places"]), 1)
                    self.assertEqual(data["places"][0]["flights"], expected)
                    self.assertEqual(data["places"][0]["seconds"], expected * 100)
                    self.assertEqual(sum(p["count"] for p in data["points"]), expected * 2)
        self.sign_in(4)
        self.assertEqual(self.client.get("/api/places?from=2010-01-01").json["places"], [])
        self.assertEqual(self.client.get("/api/places?nonflights=1&indoor=1").json["points"], [])

    def test_locations_visibility_filters_apply_to_points_and_totals(self):
        for changes in (
                "UPDATE flights SET verdict='ground' WHERE id=1",
                "UPDATE aircraft SET simulation='{\"detected\":true,\"evidence\":[]}' WHERE id=1"):
            with self.subTest(changes=changes):
                self.con.execute("UPDATE flights SET verdict='flight'")
                self.con.execute("UPDATE aircraft SET simulation=NULL")
                self.con.execute(changes)
                self.con.commit()
                for query, count in (("?indoor=1", 1), ("?indoor=1&nonflights=1", 2)):
                    data = self.client.get("/api/places" + query).json
                    self.assertEqual(data["places"][0]["flights"], count)
                    self.assertEqual(sum(p["count"] for p in data["points"]), count * 2)
        self.con.execute("UPDATE places SET indoor=1 WHERE id=1")
        self.con.commit()
        hidden = self.client.get("/api/places?indoor=0&nonflights=1").json
        self.assertEqual(hidden["places"][0]["flights"], 0)
        self.assertEqual(hidden["points"], [])
        self.assertEqual(self.client.get("/api/places?indoor=1&nonflights=1").json["places"][0]["flights"], 2)

    def test_locations_unknown_dates_all_time_and_corrected_dates(self):
        self.con.execute("UPDATE logs SET start_utc=NULL WHERE id=1")
        self.con.commit()
        self.assertEqual(self.client.get("/api/places?to=2010-12-31").json["places"][0]["flights"], 0)
        self.assertEqual(self.client.get("/api/places").json["places"][0]["flights"], 1)
        self.assertEqual(self.client.get("/api/places?nonflights=1").json["places"][0]["flights"], 1)
        included = self.client.get("/api/places?unknown_dates=1").json
        self.assertEqual(included["places"][0]["flights"], 2)
        self.assertEqual(sum(p["count"] for p in included["points"]), 4)
        self.assertEqual(self.client.get(
            "/api/places?unknown_dates=1&to=2010-12-31").json["places"][0]["flights"], 1)
        self.con.execute("UPDATE flights SET verdict='ground' WHERE id=1")
        self.con.commit()
        self.assertEqual(self.client.get("/api/places?unknown_dates=1").json["places"][0]["flights"], 1)
        self.assertEqual(self.client.get(
            "/api/places?unknown_dates=1&nonflights=1").json["places"][0]["flights"], 2)
        self.con.execute("UPDATE flights SET verdict='flight' WHERE id=1")
        self.con.execute("UPDATE logs SET start_utc='2010-01-01T12:00:00Z', date_override='2012-01-01' WHERE id=1")
        self.con.commit()
        self.assertEqual(self.client.get("/api/places?from=2012-01-01&to=2012-01-01").json["places"][0]["flights"], 1)
        detail = self.client.get("/api/detail/projects/1").json
        corrected = next(f for f in detail["flights"] if 1 in f["log_ids"])
        self.assertEqual(corrected["date_override"], "2012-01-01")

    def test_locations_reject_invalid_ranges(self):
        for query in ("from=bad", "to=2010-02-30", "from=2011-1-1",
                      "from=2011-01-01&to=2010-01-01"):
            with self.subTest(query=query):
                response = self.client.get("/api/places?" + query)
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.json)

    def test_estimated_dates_are_not_unknown_and_still_obey_period(self):
        self.con.execute("UPDATE logs SET date_estimated=1 WHERE id=1")
        self.con.commit()
        rows = self.client.get("/api/logbook?view=overview").json
        estimate = next(row for row in rows if row["log_id"] == 1)
        self.assertTrue(estimate["date_ok"])
        self.assertTrue(estimate["date_estimated"])
        for query, count in (("unknown_dates=0", 2),
                             ("unknown_dates=0&from=2010-01-01&to=2010-01-01", 1),
                             ("unknown_dates=0&from=2011-01-01", 1)):
            with self.subTest(query=query):
                data = self.client.get("/api/places?" + query).json
                self.assertEqual(data["places"][0]["flights"], count)

    def test_zero_flight_detail_logs_include_corrected_date_for_client_filtering(self):
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by,project_id,"
            " start_utc,date_override) VALUES(3,'Zero','zero','test.data','done','now',2,1,NULL,'2012-01-01')")
        self.con.execute("INSERT INTO aircraft(log_id,drone_id,name,ac_id) VALUES(3,1,'Drone',7)")
        self.con.commit()
        for kind, identity in (("projects", 1), ("people", 2), ("aircraft", 1)):
            with self.subTest(kind=kind):
                result = self.client.get(f"/api/detail/{kind}/{identity}")
                self.assertEqual(result.status_code, 200)
                log = next(l for l in result.json["logs"] if l["id"] == 3)
                self.assertEqual(log["num_flights"], 0)
                self.assertEqual(log["date_override"], "2012-01-01")
                self.assertTrue(log["date_ok"])
                self.assertNotIn("data_path", log)
        self.sign_in(4)
        self.assertEqual(self.client.get("/api/detail/projects/1").status_code, 404)
