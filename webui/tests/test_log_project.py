"""A log without a project follows its flights when they are put in one project by hand."""
import os

import db
from .test_historical import AppTests


class LogProjectTests(AppTests):
    def log(self, flight_projects, uploader=5):
        log_id = self.con.execute(
            "INSERT INTO logs(name,sha256,data_path,log_path,status,uploaded_at,uploaded_by,project_id)"
            " VALUES ('LOG00025.BFL',?,'x.gz','y.gz','done','now',?,NULL)", (os.urandom(16).hex(), uploader)).lastrowid
        aircraft = self.con.execute("INSERT INTO aircraft(log_id,ac_id,name) VALUES (?,1,'LOG00025')", (log_id,)).lastrowid
        flights = [self.con.execute(
            "INSERT INTO flights(aircraft_id,idx,takeoff_time_s,takeoff_utc,project_id) VALUES (?,?,1,'2026-11-18',?)",
            (aircraft, i, project)).lastrowid for i, project in enumerate(flight_projects)]
        self.con.commit()
        return log_id, flights

    def log_project(self, log_id):
        return self.con.execute("SELECT project_id FROM logs WHERE id=?", (log_id,)).fetchone()[0]

    def test_setting_the_flight_project_puts_the_log_in_it(self):
        log_id, (flight,) = self.log([None])
        self.sign_in(3)  # member (view) of project 1: does not see the uploader's log without a project
        self.assertEqual(self.client.get(f"/api/logs/{log_id}").status_code, 404)
        self.sign_in(5)  # the uploader, Upload on project 1
        self.assertEqual(self.client.put(f"/api/flights/{flight}/project", json={"project_id": 1}).status_code, 200)
        self.assertEqual(self.log_project(log_id), 1)
        self.sign_in(3)
        self.assertEqual(self.client.get(f"/api/logs/{log_id}").status_code, 200)

    def test_flights_in_several_projects_leave_the_log_alone(self):
        log_id, (first, _) = self.log([None, 2])
        self.sign_in(1)
        self.assertEqual(self.client.put(f"/api/flights/{first}/project", json={"project_id": 1}).status_code, 200)
        self.assertIsNone(self.log_project(log_id))

    def test_a_log_with_a_project_keeps_it(self):
        log_id, (flight,) = self.log([None])
        self.con.execute("UPDATE logs SET project_id=2 WHERE id=?", (log_id,))
        self.con.commit()
        self.sign_in(1)
        self.assertEqual(self.client.put(f"/api/flights/{flight}/project", json={"project_id": 1}).status_code, 200)
        self.assertEqual(self.log_project(log_id), 2)

    def test_existing_logs_are_fixed_once(self):
        fixed, _ = self.log([1, None])
        mixed, _ = self.log([1, 2])
        none, _ = self.log([None])
        self.assertEqual(db.run_once(self.con, "log_project_from_flights", db.LOG_PROJECT_FROM_FLIGHTS), 1)
        self.assertEqual([self.log_project(i) for i in (fixed, mixed, none)], [1, None, None])
        self.con.execute("UPDATE logs SET project_id=NULL WHERE id=?", (fixed,))  # an admin clears it afterwards
        self.assertEqual(db.run_once(self.con, "log_project_from_flights", db.LOG_PROJECT_FROM_FLIGHTS), 0)
        self.assertIsNone(self.log_project(fixed))

    def test_startup_runs_the_fix(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "logs.db"
            db.init(path)  # a new database: nothing to fix, the fix counts as done
            con = sqlite3.connect(path)
            con.execute("DELETE FROM data_migrations")  # as on a database from before this release
            con.execute("INSERT INTO projects(id,name,created_at) VALUES (1,'Research','now')")
            con.execute("INSERT INTO logs(id,name,sha256,data_path,log_path,status,uploaded_at,project_id)"
                        " VALUES (7,'LOG00025.BFL','s','x','y','done','now',NULL)")
            con.execute("INSERT INTO aircraft(id,log_id,ac_id,name) VALUES (3,7,1,'LOG00025')")
            con.execute("INSERT INTO flights(aircraft_id,idx,takeoff_time_s,project_id) VALUES (3,0,1,1)")
            con.commit()
            con.close()
            db.init(path)
            con = sqlite3.connect(path)
            self.assertEqual(con.execute("SELECT project_id FROM logs WHERE id=7").fetchone()[0], 1)
            con.close()


if __name__ == "__main__":
    import unittest
    unittest.main()
