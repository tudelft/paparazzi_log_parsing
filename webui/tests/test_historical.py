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
        settings = patch.dict(os.environ, LOGDB_HIDE_UNLINKED_AIRCRAFT="0")
        settings.start()
        self.addCleanup(settings.stop)
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
        for row in self.con.execute("SELECT path FROM attachments"):
            (self.ns["STORAGE_DIR"] / row["path"]).unlink(missing_ok=True)
        self.con.close()

    def sign_in(self, uid):
        with self.client.session_transaction() as session:
            session["uid"] = uid

    def create(self, **changes):
        return self.client.post("/api/historical", json={**self.body, **changes})


class HistoricalTests(AppTests):
    def test_overviews_show_recorded_place_for_both_paper_endpoints(self):
        self.assertEqual(self.create().status_code, 201)
        for url in ("/api/logbook", "/api/detail/projects/1", "/api/detail/aircraft/1"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            entry = (response.json if url == "/api/logbook" else response.json["flights"])[0]
            self.assertEqual(entry["takeoff_place"], "Field")
            self.assertEqual(entry["landing_place"], "Field")
            self.assertEqual(entry["landing_place_id"], entry["takeoff_place_id"])
            self.assertIsNone(entry["landing_utc"])
            self.assertIsNone(entry.get("landing_lat"))
        self.assertEqual(self.create(date="2010-01-02", place_id=None).status_code, 201)
        missing = next(r for r in self.client.get("/api/logbook").json if r["takeoff_utc"] == "2010-01-02")
        self.assertIsNone(missing["takeoff_place"])
        self.assertIsNone(missing["landing_place"])

    def test_favicon_is_available_without_sign_in(self):
        with self.client.session_transaction() as session:
            session.clear()
        with self.client.get("/favicon.svg") as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "image/svg+xml")
            self.assertIn(b'viewBox="0 0 64 64"', response.data)

    def test_home_is_logbook_and_uploads_has_own_route(self):
        for path in ("/", "/logbook"):
            with self.client.get(path) as response:
                self.assertEqual(response.status_code, 200)
                self.assertIn("<title>Flight logbook</title>", response.get_data(as_text=True))
        with self.client.get("/uploads") as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn('id="drop', response.get_data(as_text=True))

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
                        {"kind": "flight"}, {"takeoff_time": "12:00Z"}, {"pic_user_id": 999},
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
        good = "Drone,Pilot,Research,2010-01-01,30,5,Paper\n"
        bad = "Drone,unknown,Research,2010-01-02,10,1,Paper\n"
        result = self.csv(header + good + bad)
        self.assertEqual(result.status_code, 400)
        self.assertIn("line 3", result.json["error"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM flight_records").fetchone()[0], 0)
        self.assertEqual(self.csv(header + good + good).status_code, 409)
        result = self.csv(header + good)
        self.assertEqual(result.status_code, 201, result.json)
        self.assertEqual(result.json["flights"], 5)
        again = self.csv(header + good)  # an identical re-import changes nothing
        self.assertEqual(again.status_code, 200, again.json)
        self.assertEqual((again.json["imported"], again.json["unchanged"]), (0, 1))
        self.assertEqual(self.csv("unexpected\nx\n").status_code, 400)

    def test_csv_crew_matches_logs_and_rejects_ambiguous_names(self):
        self.sign_in(1)
        header = "aircraft,pic,pac,gcs,project,date,duration_minutes,flight_count\n"
        row = "Drone,Pilot,Viewer,Uploader,Research,2010-01-01,10,1\n"
        self.con.execute("UPDATE users SET display_name='pilot' WHERE id=4")
        self.con.commit()
        response = self.csv(header + row)
        self.assertEqual(response.status_code, 400)
        self.assertIn("line 2", response.json["error"])
        self.assertIn("crew alias", response.json["error"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM flight_records").fetchone()[0], 0)
        self.con.execute("INSERT INTO user_aliases(user_id,alias) VALUES(2,'Pilot')")
        self.con.execute("UPDATE users SET username='viewer-login' WHERE id=3")
        self.con.execute("UPDATE users SET username='uploader-login' WHERE id=5")
        self.con.commit()
        for role, value in (("pic", "pic"), ("pac", "viewer-login"), ("gcs", "uploader-login")):
            fields = ["Drone", "Pilot", "Viewer", "Uploader", "Research", "2010-01-01", "10", "1"]
            fields[{"pic": 1, "pac": 2, "gcs": 3}[role]] = value
            self.assertEqual(self.csv(header + ",".join(fields) + "\n").status_code, 400)
        response = self.csv(header + row)
        self.assertEqual(response.status_code, 201, response.json)
        record = self.con.execute("SELECT pic_user_id,pac_user_id,gcs_user_id FROM flight_records").fetchone()
        self.assertEqual(tuple(record), (2, 3, 5))
        resolve = self.ns["person_resolver"](self.con)
        for role, name, uid in (("pic", "Pilot", 2), ("pac", "Viewer", 3), ("gcs", "Uploader", 5)):
            self.assertEqual(resolve(role, name, None)["id"], uid)

    def test_csv_source_attached_once_shared_and_retry_safe(self):
        self.sign_in(1)
        header = "aircraft,pic,project,date,duration_minutes,flight_count\n"
        text = header + "Drone,Pilot,Research,2010-01-01,10,1\nDrone,Pilot,Private,2010-01-02,20,1\n"
        response = self.csv(text)
        self.assertEqual(response.status_code, 201, response.json)
        aid = response.json["attachment_id"]
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachment_links").fetchone()[0], 2)
        for identity in response.json["ids"]:
            files = self.client.get(f"/api/attachments?kind=paper&id={identity}").json["files"]
            self.assertEqual([f["id"] for f in files], [aid])
        self.assertEqual(self.csv(text).status_code, 200)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 1)
        self.sign_in(3)
        download = self.client.get(f"/api/attachments/{aid}/download")
        self.assertEqual(download.data, text.encode())
        download.close()
        self.sign_in(4)
        self.assertEqual(self.client.get(f"/api/attachments/{aid}/download").status_code, 404)

    def records(self):
        return [dict(r) for r in self.con.execute("SELECT * FROM flight_records ORDER BY id")]

    def test_csv_reimport_updates_by_reference(self):
        self.sign_in(1)
        self.con.execute("INSERT INTO drones(id,name,unique_id,created_at) VALUES (2,'Other','UID2','now')")
        self.con.commit()
        header = "aircraft,pic,project,date,duration_minutes,flight_count,location,comment,reference\n"
        first = self.csv(header + "Drone,Pilot,Research,2010-01-01,10,1,,first,F00001\n"
                                  "Drone,Pilot,Research,2010-01-01,12,1,,second,F00002\n")
        self.assertEqual(first.status_code, 201, first.json)
        ids = first.json["ids"]
        corrected = header + ("Other,Pilot,Private,2010-01-01,10,1,Field,fixed,F00001\n"
                              "Drone,Pilot,Research,2010-01-01,12,1,,second,F00002\n"
                              "Drone,Pilot,Research,2010-01-01,15,1,,third,F00003\n")
        preview = self.client.post("/api/historical/import", data={
            "file": (io.BytesIO(corrected.encode()), "paper.csv"), "preview": "1"})
        preview.request.input_stream.close()
        self.assertEqual([(r["action"], r["record_id"]) for r in preview.json["rows"]],
                         [("update", ids[0]), ("unchanged", ids[1]), ("create", None)])
        self.assertEqual(preview.json["rows"][0]["changes"], ["aircraft", "project", "location", "comment"])
        self.assertEqual(len(self.records()), 2)  # the preview writes nothing
        result = self.csv(corrected)
        self.assertEqual(result.status_code, 201, result.json)
        self.assertEqual((result.json["imported"], result.json["updated"], result.json["unchanged"]), (1, 1, 1))
        self.assertEqual(result.json["updated_ids"], [ids[0]])
        rows = self.records()
        self.assertEqual(len(rows), 3)
        self.assertEqual((rows[0]["drone_id"], rows[0]["project_id"], rows[0]["place_id"], rows[0]["comment"]),
                         (2, 2, 1, "fixed"))
        # the CSV is linked to the records it created or changed, not to the unchanged one
        linked = {r[0] for r in self.con.execute(
            "SELECT record_id FROM attachment_links WHERE attachment_id=?", (result.json["attachment_id"],))}
        self.assertEqual(linked, {ids[0], rows[2]["id"]})

    def test_csv_reimport_matches_timed_flights_and_keeps_reference(self):
        self.sign_in(1)
        header = "aircraft,pic,project,date,duration_minutes,flight_count,takeoff_time,comment,reference\n"
        self.assertEqual(self.csv(header + "Drone,Pilot,Research,2010-01-01,10,1,09:30,a,F00001\n"
                                           "Drone,Pilot,Research,2010-01-01,10,1,11:00,b,\n").status_code, 201)
        header = "aircraft,pic,project,date,duration_minutes,flight_count,takeoff_time,comment\n"
        result = self.csv(header + "Drone,Pilot,Research,2010-01-01,10,1,09:30,changed\n"
                                   "Drone,Pilot,Research,2010-01-01,10,1,11:00,b\n")
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual((result.json["updated"], result.json["unchanged"]), (1, 1))
        rows = self.records()
        self.assertEqual([(r["comment"], r["reference"]) for r in rows], [("changed", "F00001"), ("b", None)])
        # a different reference at the same takeoff is a different flight
        header = "aircraft,pic,project,date,duration_minutes,flight_count,takeoff_time,reference\n"
        result = self.csv(header + "Drone,Pilot,Research,2010-01-01,10,1,09:30,F00009\n")
        self.assertEqual(result.json["imported"], 1)

    def test_csv_untimed_flights_and_daily_summaries_need_a_reference(self):
        self.sign_in(1)
        header = "aircraft,pic,project,date,duration_minutes,flight_count,comment\n"
        self.assertEqual(self.csv(header + "Drone,Pilot,Research,2010-01-01,10,1,a\n"
                                           "Drone,Pilot,Research,2010-01-01,20,3,day\n").status_code, 201)
        result = self.csv(header + "Drone,Pilot,Research,2010-01-01,10,1,b\n"
                                   "Drone,Pilot,Research,2010-01-01,20,3,other day\n")
        self.assertEqual((result.json["imported"], result.json["updated"]), (2, 0))

    def test_csv_ambiguous_or_repeated_targets_reject_the_file(self):
        self.sign_in(1)
        header = "aircraft,pic,project,date,duration_minutes,flight_count,takeoff_time,comment,reference\n"
        self.assertEqual(self.csv(header + "Drone,Pilot,Research,2010-01-01,10,1,09:30,a,Book 1\n"
                                           "Drone,Pilot,Research,2010-01-01,10,1,09:30,b,Book 1\n").status_code, 201)
        result = self.csv(header + "Drone,Pilot,Research,2010-01-01,10,1,09:30,c,Book 1\n")
        self.assertEqual(result.status_code, 409)
        self.assertIn("Matches paper records", result.json["error"])
        # a shared reference only matches the record with the same date and takeoff time
        self.assertEqual(self.csv(header + "Drone,Pilot,Research,2010-01-02,10,1,09:30,d,Book 1\n").status_code, 201)
        result = self.csv(header + "Drone,Pilot,Research,2010-01-02,10,1,09:30,e,Book 1\n")
        self.assertEqual(result.json["updated"], 1)
        result = self.csv(header + "Drone,Pilot,Research,2010-01-02,10,1,09:30,f,Book 1\n"
                                   "Drone,Pilot,Research,2010-01-02,10,1,09:30,g,Book 1\n")
        self.assertEqual(result.status_code, 409)
        self.assertIn("line 2", result.json["error"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM flight_records").fetchone()[0], 3)

    def test_failed_csv_keeps_no_records_or_source_attachment(self):
        self.sign_in(1)
        text = ("aircraft,pic,project,date,duration_minutes,flight_count\n"
                "Drone,Pilot,Research,2010-01-01,10,1\n")
        self.assertEqual(self.csv(text + "Drone,Unknown,Research,2010-01-02,10,1\n").status_code, 400)
        with patch.object(self.ns["attachments"], "add_links", side_effect=OSError("test failure")):
            with self.assertLogs(self.app.logger, level="ERROR"):
                self.assertEqual(self.csv(text).status_code, 503)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM flight_records").fetchone()[0], 0)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 0)
        root = self.ns["STORAGE_DIR"]
        self.assertEqual(list((root / "attachments").iterdir()), [])
        self.assertEqual(list((root / ".incoming-attachments").iterdir()), [])

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
        row = 'Drone,Pilot,Research,2010-01-01,30,1,"Old, handwritten\nnotes"\n'
        result = self.csv("\ufeff" + header + row)
        self.assertEqual(result.status_code, 201, result.json)
        record = self.client.get(f"/api/historical/{result.json['ids'][0]}").json["record"]
        self.assertEqual(record["comment"], "Old, handwritten\nnotes")
        self.assertEqual(self.csv(header + '"unterminated\n').status_code, 400)
        self.assertEqual(self.csv(header).status_code, 400)
        self.assertEqual(self.csv("x" * 1_000_001).status_code, 400)
        many = "aircraft,pic,project,date,duration_minutes,flight_count,reference\n" + "".join(
            f"Drone,Pilot,Research,2010-02-01,10,1,{i}\n" for i in range(1001))
        result = self.csv(many)
        self.assertEqual(result.status_code, 400)
        self.assertIn("1000 records", result.json["error"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM flight_records").fetchone()[0], 1)
        result = self.client.post("/api/historical/import",
                                  data={"file": (io.BytesIO(b"\xff\xfe"), "paper.csv")})
        self.assertEqual(result.status_code, 400)

    def test_api_keys_add_records_but_cannot_change_them_or_manage_keys(self):
        original = self.ns["user_for_key"]
        self.addCleanup(self.ns.__setitem__, "user_for_key", original)
        key = {"Authorization": "Bearer test-only"}

        def as_key(uid):
            self.ns["user_for_key"] = lambda token: self.con.execute("SELECT * FROM users WHERE id=?",
                                                                     (uid,)).fetchone()

        def key_csv(text):
            response = self.client.post("/api/historical/import", headers=key,
                                        data={"file": (io.BytesIO(text.encode()), "paper.csv")})
            response.request.input_stream.close()
            return response

        csv_text = "aircraft,pic,project,date,duration_minutes,flight_count\nDrone,Pilot,Research,2010-03-01,10,1\n"
        as_key(3)  # view-only member: same project rule as in the browser
        self.assertEqual(self.client.post("/api/historical", json=self.body, headers=key).status_code, 403)
        as_key(2)  # full project member, not admin
        result = self.client.post("/api/historical", json=self.body, headers=key)
        self.assertEqual(result.status_code, 201, result.json)
        rid = result.json["id"]
        self.assertEqual(self.client.get(f"/api/historical/{rid}", headers=key).status_code, 200)
        self.assertEqual(self.client.put(f"/api/historical/{rid}", json={**self.body, "duration_minutes": 45},
                                         headers=key).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/historical/{rid}", headers=key).status_code, 403)
        self.assertEqual(key_csv(csv_text).status_code, 403)  # CSV import stays admin-only
        self.assertEqual(self.client.get("/api/keys", headers=key).status_code, 403)
        self.assertEqual(self.client.post("/api/keys", json={"name": "x"}, headers=key).status_code, 403)
        as_key(1)  # admin key: scripted CSV import of old logbooks
        result = key_csv(csv_text)
        self.assertEqual(result.status_code, 201, result.json)
        self.assertEqual(self.client.post("/api/keys", json={"name": "x"}, headers=key).status_code, 403)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM flight_records").fetchone()[0], 2)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM api_keys").fetchone()[0], 0)
        self.sign_in(2)  # the browser session is unchanged
        self.assertEqual(self.client.delete(f"/api/historical/{rid}").status_code, 200)

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
        self.con.execute("INSERT INTO flights(id,aircraft_id,idx,duration_s,distance_m,takeoff_utc,landing_utc,verdict,"
                         "has_gps_position) VALUES(1,1,0,100,1000,'2010-01-01T12:00:00.000Z','2010-01-01T12:01:40.000Z','flight',0)")
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

    def test_logbook_borrows_decoded_landing_from_completed_source(self):
        landing = {"location": {"lat": 52.0, "lon": 4.0, "alt_m": 12}}
        for identity, kind, landed in ((1, "onboard_sd", 0), (2, "gcs", 1)):
            self.con.execute(
                "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by,project_id,log_type)"
                " VALUES(?,?,?,'test.data','done','now',2,1,?)",
                (identity, kind, kind, kind))
            self.con.execute("INSERT INTO aircraft(id,log_id,drone_id,name,ac_id) VALUES(?,?,1,'Drone',7)",
                             (identity, identity))
            self.con.execute(
                "INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc,landing_utc,landed,"
                " landing_lat,landing_lon,landing_json,verdict)"
                " VALUES(?,?,0,?,'2010-01-01T12:00:00.000Z',?,?,?,?,?,'flight')",
                (identity, identity, 100 if landed else 60,
                 "2010-01-01T12:01:40.000Z" if landed else None, landed,
                 52.0 if landed else None, 4.0 if landed else None,
                 json.dumps(landing) if landed else None))
        self.con.commit()
        response = self.client.get("/api/logbook")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json), 1)
        row = response.json[0]
        self.assertEqual(row["flight_id"], 1)
        self.assertEqual(row["landing"], landing)
        self.assertEqual(row["landing_utc"], "2010-01-01T12:01:40.000Z")
        self.assertEqual((row["landing_lat"], row["landing_lon"]), (52.0, 4.0))
        self.assertEqual(row["duration_s"], 100)
        self.assertTrue(row["landed"])
        self.assertEqual(len(row["sources"]), 2)


class LogbookBulkTests(AppTests):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.create().status_code, 201)
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by,project_id,log_type)"
            " VALUES(1,'Digital','test','test.data','done','now',2,1,'onboard_sd')")
        self.con.execute("INSERT INTO aircraft(id,log_id,drone_id,name,ac_id) VALUES(1,1,1,'Drone',7)")
        self.con.executemany(
            "INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc,verdict,project_id,has_gps_position)"
            " VALUES(?,1,?,100,?,'flight',1,0)",
            [(1, 0, "2010-01-01T12:00:00.000Z"), (2, 1, "2010-01-01T13:00:00.000Z")])
        self.con.commit()
        self.sign_in(1)

    def update(self, **changes):
        return self.client.put("/api/logbook/records", json={
            "record_keys": ["log:1", "paper:1"], **changes})

    def test_mixed_edit_and_reversible_hide(self):
        response = self.update(project_id=2, pic_user_id=4, pac_user_id=3, gcs_user_id=5)
        self.assertEqual(response.status_code, 200, response.json)
        for table in ("flights", "flight_records"):
            row = self.con.execute(f"SELECT * FROM {table} WHERE id=1").fetchone()
            self.assertEqual(tuple(row[k] for k in ("project_id", "pic_user_id", "pac_user_id", "gcs_user_id")),
                             (2, 4, 3, 5))
        self.assertEqual(self.update(hidden=True).status_code, 200)
        self.assertEqual([r["record_key"] for r in self.client.get("/api/logbook").json], ["log:2"])
        self.assertEqual(len(self.client.get("/api/stats").json["flights"]), 1)
        hidden = self.client.get("/api/logbook?hidden=1")
        self.assertEqual({r["record_key"] for r in hidden.json}, {"log:1", "paper:1"})
        self.assertTrue(all(r["hidden"] for r in hidden.json))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM logs").fetchone()[0], 1)
        self.assertEqual(self.update(hidden=False).status_code, 200)
        rows = self.client.get("/api/logbook").json
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(r["flight_count"] for r in rows), 7)
        self.assertEqual(sum(r["duration_s"] for r in rows), 2030)
        self.assertEqual(self.client.get("/api/logbook?hidden=1").json, [])
        self.assertEqual(self.update(pac_user_id=None, gcs_user_id=None).status_code, 200)
        self.assertIsNone(self.con.execute("SELECT pac_user_id FROM flight_records").fetchone()[0])

    def test_hiding_merged_sources_does_not_leave_a_duplicate(self):
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by,project_id,log_type)"
            " VALUES(2,'GCS','gcs','gcs.data','done','now',2,1,'gcs')")
        self.con.execute("INSERT INTO aircraft(id,log_id,drone_id,name,ac_id) VALUES(2,2,1,'Drone',7)")
        self.con.execute(
            "INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc,verdict,project_id,has_gps_position)"
            " VALUES(3,2,0,100,'2010-01-01T12:00:00.000Z','flight',1,0)")
        self.con.commit()
        row = next(r for r in self.client.get("/api/logbook").json if r["record_key"] == "log:1")
        keys = [f"log:{s['flight_id']}" for s in row["sources"]]
        self.assertEqual(set(keys), {"log:1", "log:3"})
        self.assertEqual(self.update(record_keys=keys, hidden=True).status_code, 200)
        self.assertEqual({r["record_key"] for r in self.client.get("/api/logbook").json}, {"log:2", "paper:1"})
        hidden = self.client.get("/api/logbook?hidden=1").json
        self.assertEqual(len(hidden), 1)
        self.assertEqual(len(hidden[0]["sources"]), 2)
        self.assertEqual(self.update(record_keys=keys, hidden=False).status_code, 200)
        self.assertEqual(len(self.client.get("/api/logbook").json), 3)

    def test_validation_and_permissions_are_atomic(self):
        for change in ({"pic_user_id": None}, {"project_id": None}, {"pic_user_id": 999},
                       {"hidden": "true"}, {"record_keys": ["log:1", "paper:999"], "project_id": 2}):
            response = self.update(**change)
            self.assertIn(response.status_code, (400, 404), response.json)
            self.assertEqual(self.con.execute("SELECT project_id FROM flights WHERE id=1").fetchone()[0], 1)
            self.assertIsNone(self.con.execute("SELECT pic_user_id FROM flights WHERE id=1").fetchone()[0])
        self.sign_in(2)
        self.assertEqual(self.update(pic_user_id=3).status_code, 403)
        self.assertEqual(self.update(hidden=True).status_code, 403)
        self.assertEqual(self.client.get("/api/logbook?hidden=1").status_code, 403)
        response = self.update(record_keys=["log:1"], pac_user_id=3)
        self.assertEqual(response.status_code, 200, response.json)

    def test_paper_duplicate_rejection_rolls_back_telemetry(self):
        self.assertEqual(self.create(pic_user_id=3).status_code, 201)
        response = self.update(pic_user_id=3)
        self.assertEqual(response.status_code, 409, response.json)
        self.assertIsNone(self.con.execute("SELECT pic_user_id FROM flights WHERE id=1").fetchone()[0])
        self.assertEqual(self.con.execute("SELECT pic_user_id FROM flight_records WHERE id=1").fetchone()[0], 2)

    def test_hidden_state_survives_reparse_and_schema_migration(self):
        self.assertEqual(self.update(hidden=True).status_code, 200)
        kept = self.ns["snapshot_flight_edits"](self.con, 1)
        doc = {"aircraft": [{"ac_id": 7, "name": "Drone", "flights": [
            {"flight": 0, "duration_s": 100, "takeoff": {"utc": "2010-01-01T12:00:00.000Z"}},
            {"flight": 1, "duration_s": 100, "takeoff": {"utc": "2010-01-01T13:00:00.000Z"}}]}]}
        db.store_parsed(self.con, 1, doc, "paths.gz")
        self.ns["assign_log_flights"](self.con, 1, kept)
        self.con.commit()
        self.assertEqual([r["hidden"] for r in self.con.execute("SELECT hidden FROM flights ORDER BY idx")], [1, 0])
        self.con.execute("ALTER TABLE flights DROP COLUMN hidden")
        self.con.execute("ALTER TABLE flight_records DROP COLUMN hidden")
        self.con.commit()
        with patch.object(db, "connect", return_value=self.con):
            db.init("unused")
            db.init("unused")
        for table in ("flights", "flight_records"):
            self.assertEqual(self.con.execute(f"SELECT SUM(hidden) FROM {table}").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
