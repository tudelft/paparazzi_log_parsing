"""POST /api/logs/check: log id, status and flights for logs the user may see."""
from .test_historical import AppTests


class LogsCheckTests(AppTests):
    raw = b'12.0 7 INFO_MSG "[PFC] goal: wind tunnel"\n' * 100

    def stored_log(self, uid=2, log=b"<configuration/>"):
        """Upload a .data/.log pair as `uid` through the ingest path (no workers) and return the logs row."""
        stage = self.ns["STAGE_DIR"] / f"check-{uid}"
        stage.mkdir(parents=True, exist_ok=True)
        (stage / "flight.data").write_bytes(self.raw)
        (stage / "flight.log").write_bytes(log)
        iid = self.ns["insert_ingest"](self.con, "pair", "flight.data", stage.name, "flight.data", "flight.log",
                                       len(self.raw), uid)
        self.ns["ingest_pair"](self.con, self.con.execute("SELECT * FROM ingest WHERE id=?", (iid,)).fetchone())
        row = self.con.execute("SELECT * FROM logs").fetchone()
        for column in ("data_path", "log_path"):
            if row[column]:
                self.addCleanup((self.ns["STORAGE_DIR"] / row[column]).unlink, missing_ok=True)
        return row

    def parsed(self, log_id):
        """What the parser stores: two aircraft, flights out of idx order and one hidden flight."""
        self.con.execute("UPDATE logs SET status='done', project_id=1 WHERE id=?", (log_id,))
        self.con.executemany("INSERT INTO aircraft(id,log_id,drone_id,name,ac_id) VALUES(?,?,1,'Drone',?)",
                             [(1, log_id, 7), (2, log_id, 8)])
        self.con.executemany(
            "INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_time_s,takeoff_utc,hidden,comment)"
            " VALUES(?,?,?,?,?,?,?,?)",
            [(12, 1, 1, 90.5, 400.0, "2020-07-22T08:36:40Z", 1, None),
             (11, 1, 0, 312.4, 100.0, "2020-07-22T08:31:40Z", 0, "existing comment"),
             (21, 2, 0, 60.0, 200.0, "2020-07-22T08:33:20Z", 0, None)])
        self.con.commit()

    def check(self, sha):
        return self.client.post("/api/logs/check", json={"sha256": [sha.upper()]}).json["known"][sha]

    def test_parsed_log_returns_id_name_status_and_flights(self):
        log = self.stored_log()
        self.parsed(log["id"])
        self.sign_in(2)
        entry = self.check(log["sha256"])
        self.assertEqual((entry["id"], entry["name"], entry["status"]), (log["id"], log["name"], "done"))
        self.assertEqual(entry["flights"], [
            {"id": 11, "idx": 0, "takeoff_utc": "2020-07-22T08:31:40Z", "duration_s": 312.4, "hidden": 0,
             "comment": "existing comment"},
            {"id": 21, "idx": 0, "takeoff_utc": "2020-07-22T08:33:20Z", "duration_s": 60.0, "hidden": 0,
             "comment": None},
            {"id": 12, "idx": 1, "takeoff_utc": "2020-07-22T08:36:40Z", "duration_s": 90.5, "hidden": 1,
             "comment": None}])
        db_ids = {r[0] for r in self.con.execute(
            "SELECT f.id FROM flights f JOIN aircraft a ON a.id=f.aircraft_id WHERE a.log_id=?", (log["id"],))}
        self.assertEqual({f["id"] for f in entry["flights"]}, db_ids)

    def test_log_the_user_may_not_see_has_only_the_old_keys(self):
        log = self.stored_log()
        self.parsed(log["id"])
        self.con.execute("UPDATE logs SET project_id=2 WHERE id=?", (log["id"],))  # project 4 is not a member of
        self.con.commit()
        self.sign_in(4)
        self.assertEqual(self.check(log["sha256"]), {"has_log": True, "mine": False})
        self.sign_in(3)  # project member (view) of project 1, not of project 2
        self.assertNotIn("id", self.check(log["sha256"]))

    def test_project_member_sees_it(self):
        log = self.stored_log()
        self.parsed(log["id"])
        self.sign_in(3)
        entry = self.check(log["sha256"])
        self.assertEqual((entry["id"], entry["mine"], len(entry["flights"])), (log["id"], False, 3))

    def test_queued_log_has_id_and_no_flights(self):
        log = self.stored_log()
        self.assertEqual(log["status"], "queued")
        self.sign_in(2)
        entry = self.check(log["sha256"])
        self.assertEqual((entry["id"], entry["status"], entry["flights"]), (log["id"], "queued", []))

    def test_old_keys_stay(self):
        log = self.stored_log()
        self.parsed(log["id"])
        self.sign_in(2)
        entry = self.check(log["sha256"])
        self.assertEqual((entry["has_log"], entry["mine"]), (True, True))
        self.assertEqual(self.client.post("/api/logs/check", json={"sha256": ["0" * 64]}).json, {"known": {}})


if __name__ == "__main__":
    import unittest
    unittest.main()
