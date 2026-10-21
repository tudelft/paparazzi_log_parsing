import ast
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import db
import historical


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Load the actual app and its routes, without starting ingest/parse workers or touching stored logs.
        path = Path(__file__).resolve().parents[1] / "app.py"
        tree = ast.parse(path.read_text())
        stop = next(i for i, n in enumerate(tree.body)
                    if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                    and isinstance(n.value.func, ast.Name) and n.value.func.id == "start_workers")
        cls.ns = {"__name__": "historical_test_app", "__file__": str(path)}
        storage = tempfile.TemporaryDirectory()
        cls.addClassCleanup(storage.cleanup)
        with patch.dict(os.environ, LOGDB_SECRET_KEY="historical-tests", LOGDB_WEB_DATA=storage.name,
                        LOGDB_LOG_DIR=storage.name + "/logs"):
            exec(compile(ast.Module(body=tree.body[:stop], type_ignores=[]), str(path), "exec"), cls.ns)
        cls.app = cls.ns["app"]
        cls.app.config.update(TESTING=True, SECRET_KEY="historical-tests", SESSION_COOKIE_SECURE=False)

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.executescript(db.SCHEMA)
        self.ns["conn"] = lambda: self.con
        self.ns["current_version"] = lambda: "test"
        self.con.executemany("INSERT INTO users(id,username,display_name,is_admin,created_at) VALUES (?,?,?,?,?)",
                             [(1, "admin", "Admin", 1, "now"), (2, "pic", "Pilot", 0, "now"),
                              (3, "viewer", "Viewer", 0, "now"), (4, "outsider", "Outsider", 0, "now"),
                              (5, "uploader", "Uploader", 0, "now")])
        self.con.executemany("INSERT INTO projects(id,name,created_at) VALUES (?,?,?)",
                             [(1, "Research", "now"), (2, "Private", "now")])
        self.con.executemany("INSERT INTO project_members(project_id,user_id,role) VALUES (1,?,?)",
                             [(2, "full"), (3, "view"), (5, "add")])
        self.con.execute("INSERT INTO drones(id,name,unique_id,created_at) VALUES (1,'Drone','UID','now')")
        self.con.execute("INSERT INTO places(id,name,indoor,shape) VALUES (1,'Field',0,?)",
                         (json.dumps({"type": "circle", "center": [52, 4], "radius_m": 100}),))
        self.con.commit()
        self.client = self.app.test_client()
        self.sign_in(2)
        self.body = dict(drone_id=1, pic_user_id=2, project_id=1, date="2010-01-01",
                         duration_minutes="30.5", flight_count=5, kind="daily", place_id=1,
                         reference="Book 1 / page 2", goal="Old flight")

    def tearDown(self):
        self.con.close()

    def sign_in(self, uid):
        with self.client.session_transaction() as session:
            session["uid"] = uid

    def create(self, **changes):
        return self.client.post("/api/historical", json={**self.body, **changes})


class HistoricalTests(AppTests):
    def test_create_read_update_delete_and_no_fake_logs(self):
        response = self.create()
        self.assertEqual(response.status_code, 201, response.json)
        rid = response.json["id"]
        row = self.client.get(f"/api/historical/{rid}").json
        self.assertEqual(row["record"]["duration_s"], 1830)
        self.assertEqual(row["record"]["source_type"], "paper")
        self.assertTrue(row["can_edit"])
        updated = self.client.put(f"/api/historical/{rid}", json={**self.body, "duration_minutes": 45})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM logs").fetchone()[0], 0)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM flights").fetchone()[0], 0)
        self.assertEqual(self.client.delete(f"/api/historical/{rid}").status_code, 200)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM users").fetchone()[0], 5)

    def test_access_controls_for_every_route(self):
        rid = self.create().json["id"]
        for uid, get_status in ((3, 200), (4, 404), (5, 200)):
            self.sign_in(uid)
            self.assertEqual(self.client.get(f"/api/historical/{rid}").status_code, get_status)
            self.assertEqual(self.create(date="2011-01-01").status_code, 403)
            self.assertIn(self.client.put(f"/api/historical/{rid}", json=self.body).status_code, (403, 404))
            self.assertIn(self.client.delete(f"/api/historical/{rid}").status_code, (403, 404))
        self.sign_in(2)
        self.assertEqual(self.create(project_id=2).status_code, 403)
        self.assertEqual(self.client.put(f"/api/historical/{rid}", json={**self.body, "project_id": 2}).status_code, 403)
        self.sign_in(1)
        self.assertEqual(self.create(project_id=2).status_code, 201)
        self.sign_in(4)
        self.assertEqual(self.client.get("/api/logbook").json, [])

    def test_validation_and_duplicates(self):
        for changes in ({"duration_minutes": "NaN"}, {"duration_minutes": -1}, {"duration_minutes": 1441},
                        {"duration_minutes": "1e9999999999"}, {"flight_count": "1" * 5000},
                        {"date": "2010-02-30"}, {"date": "2999-01-01"}, {"flight_count": 0},
                        {"kind": "flight"}, {"takeoff_time": "12:00"}, {"pic_user_id": 999},
                        {"kind": []}, {"takeoff_time": False}, {"goal": 0}):
            with self.subTest(changes=changes):
                self.assertEqual(self.create(**changes).status_code, 400)
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(self.create().status_code, 409)

    def csv(self, text):
        response = self.client.post("/api/historical/import", data={"file": (io.BytesIO(text.encode()), "paper.csv")})
        response.request.input_stream.close()
        return response

    def test_csv_atomic_validation_dedup_and_retry(self):
        self.sign_in(1)
        header = "aircraft,pic,project,date,duration_minutes,flight_count,reference\n"
        good = "Drone,pic,Research,2010-01-01,30,5,Paper\n"
        bad = "Drone,unknown,Research,2010-01-02,10,1,Paper\n"
        result = self.csv(header + good + bad)
        self.assertEqual(result.status_code, 400)
        self.assertIn("line 3", result.json["error"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM flight_records").fetchone()[0], 0)
        self.assertEqual(self.csv(header + good + good).status_code, 409)
        result = self.csv(header + good)
        self.assertEqual(result.status_code, 201, result.json)
        self.assertEqual(result.json["flights"], 5)
        self.assertEqual(self.csv(header + good).status_code, 409)
        self.assertEqual(self.csv("unexpected\nx\n").status_code, 400)

    def test_logbook_stats_entity_overviews_and_date_precision(self):
        rid = self.create().json["id"]
        rows = self.client.get("/api/logbook").json
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["takeoff_utc"], "2010-01-01")
        self.assertIsNone(r["distance_m"])
        self.assertEqual(r["sources"], [])
        self.assertEqual(r["pic_user"]["id"], 2)
        self.assertEqual(r["takeoff_place_id"], 1)
        self.assertEqual(r["flight_count"], 5)
        stats = self.client.get("/api/stats").json["flights"]
        self.assertEqual(sum(f["flight_count"] for f in stats), 5)
        for url in ("/api/detail/people/2", "/api/detail/aircraft/1", "/api/detail/projects/1",
                    "/api/detail/locations/1"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual(response.json["flights"][0]["manual_id"], rid)
            self.assertEqual(response.json["logs"], [])
        self.assertEqual(self.client.get("/api/logbook?project=2").json, [])

    def test_recorded_time_and_deletion_guards(self):
        self.sign_in(1)
        self.assertEqual(self.create(kind="flight", flight_count=1, takeoff_time="09:12").status_code, 201)
        self.assertEqual(self.client.get("/api/logbook").json[0]["takeoff_utc"], "2010-01-01T09:12:00.000Z")
        self.assertEqual(self.client.delete("/api/admin/drones/1").status_code, 409)
        self.assertEqual(self.client.delete("/api/admin/projects/1").status_code, 409)
        self.assertEqual(self.client.delete("/api/admin/users/2").status_code, 409)
        self.assertEqual(self.client.delete("/api/admin/places/1").status_code, 409)

    def test_csv_bom_quoting_limits_encoding_and_api_keys(self):
        self.sign_in(1)
        header = "aircraft,pic,project,date,duration_minutes,flight_count,comment\n"
        row = 'Drone,pic,Research,2010-01-01,30,1,"Old, handwritten\nnotes"\n'
        result = self.csv("\ufeff" + header + row)
        self.assertEqual(result.status_code, 201, result.json)
        record = self.client.get(f"/api/historical/{result.json['ids'][0]}").json["record"]
        self.assertEqual(record["comment"], "Old, handwritten\nnotes")
        self.assertEqual(self.csv(header + '"unterminated\n').status_code, 400)
        self.assertEqual(self.csv(header).status_code, 400)
        self.assertEqual(self.csv("x" * 1_000_001).status_code, 400)
        many = "aircraft,pic,project,date,duration_minutes,flight_count,reference\n" + "".join(
            f"Drone,pic,Research,2010-02-01,10,1,{i}\n" for i in range(1001))
        result = self.csv(many)
        self.assertEqual(result.status_code, 400)
        self.assertIn("1000 records", result.json["error"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM flight_records").fetchone()[0], 1)
        result = self.client.post("/api/historical/import",
                                  data={"file": (io.BytesIO(b"\xff\xfe"), "paper.csv")})
        self.assertEqual(result.status_code, 400)
        self.ns["user_for_key"] = lambda key: self.con.execute("SELECT * FROM users WHERE id=2").fetchone()
        result = self.client.post("/api/historical", json=self.body,
                                  headers={"Authorization": "Bearer test-only"})
        self.assertEqual(result.status_code, 403)

    def test_csv_import_and_template_are_admin_only(self):
        for uid in (2, 3, 4, 5):
            self.sign_in(uid)
            self.assertEqual(self.csv("unused").status_code, 403)
            self.assertEqual(self.client.get("/api/historical/template").status_code, 403)
        self.sign_in(1)
        response = self.client.get("/api/historical/template")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text.strip(), ",".join(historical.COLUMNS))

    def test_paper_and_telemetry_are_separate_and_preserve_counts(self):
        self.con.execute("INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by,project_id,log_type)"
                         " VALUES(1,'Digital','test','test.data','done','now',2,1,'onboard_sd')")
        self.con.execute("INSERT INTO aircraft(id,log_id,drone_id,name,pic) VALUES(1,1,1,'Drone','pic')")
        self.con.execute("INSERT INTO flights(id,aircraft_id,idx,duration_s,distance_m,takeoff_utc,landing_utc,verdict)"
                         " VALUES(1,1,0,100,1000,'2010-01-01T12:00:00.000Z','2010-01-01T12:01:40.000Z','flight')")
        self.con.commit()
        self.assertEqual(self.create().status_code, 201)
        response = self.client.get("/api/logbook")
        self.assertEqual(response.status_code, 200, response.json)
        rows = response.json
        self.assertEqual(len(rows), 2)
        self.assertEqual({f["record_key"] for f in rows}, {"log:1", "paper:1"})
        self.assertEqual(sum(f["flight_count"] for f in rows), 6)
        self.assertEqual(sum(f["duration_s"] for f in rows), 1930)
        self.assertEqual(rows[0]["source_type"], "telemetry")
        self.assertEqual(rows[0]["sources"][0]["log_id"], 1)
        self.assertEqual(self.client.get("/api/places").json["places"][0]["flights"], 5)


if __name__ == "__main__":
    unittest.main()
