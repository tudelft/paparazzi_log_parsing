from .test_historical import AppTests
from .test_merge_identity import MergeIdentityTests


class StatsTests(AppTests):
    sources = MergeIdentityTests.sources

    def test_source_sizes_are_unique_access_scoped_and_include_all_merged_logs(self):
        self.sources(1, 1)
        self.con.execute("UPDATE logs SET size_bytes=id*1000,start_utc='2010-01-01T12:00:00.000Z'")
        self.con.execute("UPDATE flights SET has_gps_position=1")
        self.con.execute(
            "INSERT INTO flights(aircraft_id,idx,duration_s,takeoff_utc,landing_utc,"
            "takeoff_lat,takeoff_lon,landed,verdict,has_gps_position)"
            " VALUES(1,2,60,'2010-01-02T12:00:00.000Z','2010-01-02T12:01:00.000Z',52,4,1,'flight',1)")
        self.con.commit()
        for uid in (1, 2, 3):
            self.sign_in(uid)
            data = self.client.get("/api/stats").json
            self.assertEqual(data["logs"], [{"id": 1, "size_bytes": 1000}, {"id": 2, "size_bytes": 2000}])
            self.assertEqual(sorted(f["log_ids"] for f in data["flights"]), [[1], [1, 2]])
            self.assertTrue(all(f["date_ok"] for f in data["flights"]))
        self.sign_in(4)
        data = self.client.get("/api/stats").json
        self.assertEqual(data["logs"], [])
        self.assertEqual(data["flights"], [])

    def test_unknown_sizes_are_explicit_and_paper_records_have_no_source_logs(self):
        self.sources(1, 1)
        self.con.execute("UPDATE flights SET has_gps_position=1")
        self.con.commit()
        self.assertEqual(self.create().status_code, 201)
        data = self.client.get("/api/stats").json
        self.assertTrue(all(log["size_bytes"] is None for log in data["logs"]))
        paper = next(f for f in data["flights"] if f["source_type"] == "paper")
        self.assertEqual(paper["log_ids"], [])
