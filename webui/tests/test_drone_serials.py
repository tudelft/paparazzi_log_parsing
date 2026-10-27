import sqlite3
import unittest

from .test_historical import AppTests


class DroneSerialTests(AppTests):
    def add(self, **body):
        return self.client.post("/api/admin/drones", json={"name": "New", **body})

    def test_new_aircraft_get_the_lowest_free_tud_serial_after_400(self):
        self.con.executemany("INSERT INTO drones(name,unique_id,created_at) VALUES (?,?,'now')",
                             [("A", "TUD00001"), ("B", "TUD00401"), ("C", "tud00402"), ("D", "TUD00404"),
                              ("E", "TUD0403"), ("F", "NLR-1")])
        self.con.commit()
        self.sign_in(1)
        self.assertEqual(self.client.get("/api/drones").json["next_serial"], "TUD00403")  # gaps below 401 stay free
        self.assertEqual(self.client.get("/api/admin/drones").json["next_serial"], "TUD00403")
        response = self.add(unique_id=" ")
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["unique_id"], "TUD00403")
        self.assertEqual(self.add(name="Next").json["unique_id"], "TUD00405")
        self.assertEqual(self.add(name="Old", unique_id="TUD00002").json["unique_id"], "TUD00002")  # by hand
        self.assertEqual(self.add(name="Own", unique_id="EUSC0003").json["unique_id"], "EUSC0003")
        self.assertEqual(self.add(name="Clash", unique_id="TUD00001").status_code, 409)
        self.sign_in(2)
        self.assertIsNone(self.client.get("/api/drones").json["next_serial"])

    def test_an_empty_fleet_starts_at_401(self):
        self.con.execute("DELETE FROM drones")
        self.con.commit()
        self.sign_in(1)
        self.assertEqual(self.client.get("/api/admin/drones").json["next_serial"], "TUD00401")


class DroneProjectStartTests(AppTests):
    """A new aircraft's first project period has no start, so logs from before it joined the fleet count."""

    def flight(self, day):
        self.con.execute("INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by,log_type,start_utc)"
                         " VALUES(1,'a.data','s','a.gz','done','now',2,'gcs',?)", (f"{day}T09:00:00.000Z",))
        self.con.execute("INSERT INTO aircraft(id,log_id,name,ac_id) VALUES(1,1,'Cargo1',5)")
        self.con.execute("INSERT INTO flights(id,aircraft_id,idx,takeoff_utc) VALUES(1,1,0,?)",
                         (f"{day}T09:05:00.000Z",))
        self.con.commit()

    def test_a_new_aircraft_covers_earlier_logs(self):
        self.flight("2024-10-02")
        self.sign_in(1)
        response = self.client.post("/api/admin/drones", json=dict(name="Cargo1", default_project_id=1))
        self.assertEqual(response.status_code, 200, response.json)
        drone = response.json["id"]
        self.assertEqual(tuple(self.con.execute("SELECT project_id, start_date, end_date FROM drone_projects"
                                                " WHERE drone_id=?", (drone,)).fetchone()), (1, None, None))
        self.assertEqual(self.con.execute("SELECT project_id FROM flights WHERE id=1").fetchone()[0], 1)

    def test_only_the_first_period_can_be_open_at_the_start(self):
        self.sign_in(1)
        put = lambda periods: self.client.put("/api/admin/drones/1/projects", json=dict(periods=periods))  # noqa: E731
        ok = put([dict(project_id=1, start_date="", end_date="2024-12-31"), dict(project_id=2, start_date="2025-01-01")])
        self.assertEqual(ok.status_code, 200, ok.json)
        self.assertEqual([(p["start_date"], p["end_date"]) for p in ok.json["projects"]],
                         [(None, "2024-12-31"), ("2025-01-01", None)])
        self.assertEqual(self.ns["project_for_drone_on"](self.con, 1, "2019-05-05"), 1)
        self.assertEqual(self.ns["project_for_drone_on"](self.con, 1, "2025-05-05"), 2)
        self.assertEqual(put([dict(project_id=1), dict(project_id=2, end_date="2024-01-01")]).status_code, 400)
        self.assertEqual(put([dict(project_id=1, end_date="2024-12-31"), dict(project_id=2, start_date="2024-06-01")])
                         .status_code, 400)  # overlaps the open start


class DroneProjectMigrationTests(unittest.TestCase):
    def test_not_null_start_is_dropped_keeping_rows(self):
        import os
        import tempfile
        import db
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        db.init(path)
        con = sqlite3.connect(path)  # a database from before: start_date NOT NULL
        con.executescript("DROP TABLE drone_projects;"
                          "CREATE TABLE drone_projects (id INTEGER PRIMARY KEY, drone_id INTEGER NOT NULL,"
                          " project_id INTEGER NOT NULL, start_date TEXT NOT NULL, end_date TEXT);"
                          "INSERT INTO drones(id,name,unique_id,created_at) VALUES (1,'D','U','now');"
                          "INSERT INTO projects(id,name,created_at) VALUES (2,'P','now');"
                          "INSERT INTO drone_projects VALUES (7, 1, 2, '2026-10-27', NULL);")
        con.close()
        for _ in range(2):  # idempotent
            db.init(path)
        con = sqlite3.connect(path)
        self.assertEqual(con.execute("SELECT * FROM drone_projects").fetchall(), [(7, 1, 2, "2026-10-27", None)])
        self.assertEqual([r[3] for r in con.execute("PRAGMA table_info(drone_projects)") if r[1] == "start_date"], [0])
        con.close()
