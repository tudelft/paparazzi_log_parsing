import io
from unittest.mock import patch

import db
from .test_historical import AppTests


class CatalogImportTests(AppTests):
    def setUp(self):
        super().setUp()
        self.sign_in(1)

    def upload(self, kind, text):
        return self.client.post(f"/api/admin/{kind}/import", data={
            "file": (io.BytesIO(text.encode("utf-8")), "catalog.csv")})

    def test_project_import_replaces_members_and_reuses_project(self):
        self.con.execute("INSERT INTO user_aliases(user_id,alias) VALUES(2,'Example pilot')")
        self.con.execute("INSERT INTO project_members(project_id,user_id,role) VALUES(2,4,'full')")
        self.con.commit()
        text = "project,person,role\nResearch,Example pilot,\nResearch,Viewer,view\nNew project,Uploader,full\n"
        response = self.upload("projects", text)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json, {"created": 1, "updated": 1})
        self.assertEqual([tuple(r) for r in self.con.execute(
            "SELECT user_id,role FROM project_members WHERE project_id=1 ORDER BY user_id")],
            [(2, "add"), (3, "view")])
        self.assertEqual(self.con.execute("SELECT role FROM project_members WHERE project_id=2").fetchone()[0], "full")
        pid = self.con.execute("SELECT id FROM projects WHERE name='New project'").fetchone()[0]
        response = self.upload("projects", "project,person,role\nresearch,Viewer,full\nNew project,,\n")
        self.assertEqual(response.json, {"created": 0, "updated": 2})
        self.assertEqual([tuple(r) for r in self.con.execute(
            "SELECT user_id,role FROM project_members WHERE project_id=1")], [(3, "full")])
        self.assertEqual(self.con.execute("SELECT id FROM projects WHERE name='New project'").fetchone()[0], pid)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM project_members WHERE project_id=?", (pid,)).fetchone()[0], 0)

    def test_project_validation_is_atomic(self):
        cases = (
            "project,person,role\nNew,Viewer,view\nResearch,Unknown,add\n",
            "project,person,role\nResearch,Viewer,owner\n",
            "project,person,role\nResearch,Viewer,view\nResearch,Viewer,full\n",
            "project,person\nResearch,pic\n",
            "project,person,role\nResearch,,full\n",
            "project,person,role\nResearch,Viewer\n",
            "project,project,person\nResearch,Research,Viewer\n",
        )
        for text in cases:
            with self.subTest(text=text):
                response = self.upload("projects", text)
                self.assertEqual(response.status_code, 400, response.json)
                self.assertEqual(self.con.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 2)
                self.assertEqual(self.con.execute("SELECT COUNT(*) FROM project_members WHERE project_id=1").fetchone()[0], 3)
        self.con.execute("UPDATE users SET display_name='Pilot' WHERE id=3")
        self.con.commit()
        response = self.upload("projects", "project,person\nResearch,Pilot\n")
        self.assertEqual(response.status_code, 400, response.json)
        self.assertIn("ambiguous", response.json["error"])

    def make_flights(self):
        self.con.execute("UPDATE drones SET default_project_id=1 WHERE id=1")
        self.con.execute("INSERT INTO drone_projects(drone_id,project_id,start_date) VALUES(1,1,'2010-01-01')")
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by,project_id,log_type,start_utc)"
            " VALUES(1,'Digital','test','test.data','done','now',2,1,'onboard_sd','2010-01-01T00:00:00Z')")
        self.con.execute("INSERT INTO aircraft(id,log_id,drone_id,name,ac_id) VALUES(1,1,1,'Drone',7)")
        for fid, stamp, source in (
                (1, "2010-01-15", "timeline"), (2, "2010-02-15", "timeline"),
                (3, "2010-02-16", "manual"), (4, "2010-02-17", None)):
            self.con.execute(
                "INSERT INTO flights(id,aircraft_id,idx,takeoff_utc,project_id,project_source)"
                " VALUES(?,1,?,?,1,?)", (fid, fid, stamp + "T12:00:00Z", source))
        self.con.commit()

    def test_aircraft_reimport_updates_project_history_and_only_automatic_flights(self):
        self.make_flights()
        text = ("AC Nice Name,Serial Unique,Project,project_effective_date,Comments\n"
                "Renamed drone,UID,Private,2010-02-01,Updated\n")
        response = self.upload("drones", text)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual((response.json["created"], response.json["updated"]), (0, 1))
        row = self.con.execute("SELECT * FROM drones WHERE id=1").fetchone()
        self.assertEqual((row["name"], row["default_project_id"], row["comments"]), ("Renamed drone", 2, "Updated"))
        periods = self.ns["drone_periods"](self.con, 1)
        self.assertEqual([(r["project_id"], r["start_date"], r["end_date"]) for r in periods],
                         [(1, "2010-01-01", "2010-01-31"), (2, "2010-02-01", None)])
        self.assertEqual([r[0] for r in self.con.execute("SELECT project_id FROM flights ORDER BY id")], [1, 2, 1, 1])
        self.assertEqual(self.upload("drones", text).status_code, 200)
        self.assertEqual(len(self.ns["drone_periods"](self.con, 1)), 2)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM drones").fetchone()[0], 1)

    def test_aircraft_change_requires_date_and_rolls_back_whole_file(self):
        self.make_flights()
        text = "AC Nice Name,Serial Unique,Project\nNew aircraft,NEW,Research\nDrone,UID,Private\n"
        response = self.upload("drones", text)
        self.assertEqual(response.status_code, 400, response.json)
        self.assertIn("effective_date", response.json["error"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM drones").fetchone()[0], 1)
        for project, day in (("Missing", "2010-02-01"), ("Private", "2010-02-30"), ("Private", "01/02/2010")):
            response = self.upload("drones", "name,serial,Project,project_effective_date\n"
                                   f"Drone,UID,{project},{day}\n")
            self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(self.con.execute("SELECT default_project_id FROM drones").fetchone()[0], 1)
        self.assertEqual(len(self.ns["drone_periods"](self.con, 1)), 1)

    def test_aircraft_blank_or_omitted_columns_preserve_existing_values(self):
        self.make_flights()
        self.con.execute("UPDATE drones SET manager='Example manager',inventory_project='Research' WHERE id=1")
        self.con.commit()
        for text in ("name,serial,Project\nDrone,UID,\n", "name,serial\nDrone,UID\n"):
            response = self.upload("drones", text)
            self.assertEqual(response.status_code, 200, response.json)
            row = self.con.execute("SELECT * FROM drones WHERE id=1").fetchone()
            self.assertEqual((row["default_project_id"], row["inventory_project"], row["manager"]),
                             (1, "Research", "Example manager"))
        self.assertEqual(self.upload("drones", "name,serial,Project\nNew drone,NEW,Private\n").status_code, 200)
        new = self.con.execute("SELECT * FROM drones WHERE unique_id='NEW'").fetchone()
        self.assertEqual(new["default_project_id"], 2)

    def test_import_period_preserves_later_history_and_manual_clear(self):
        self.make_flights()
        self.con.execute("UPDATE drone_projects SET end_date='2010-03-31' WHERE drone_id=1")
        self.con.execute("INSERT INTO drone_projects(drone_id,project_id,start_date) VALUES(1,1,'2010-04-01')")
        self.con.commit()
        response = self.client.put("/api/flights/2/project", json={"project_id": None})
        self.assertEqual(response.status_code, 200, response.json)
        self.ns["assign_open_flights"](self.con)
        self.assertIsNone(self.con.execute("SELECT project_id FROM flights WHERE id=2").fetchone()[0])
        self.con.commit()
        response = self.upload("drones", "name,serial,Project,project_effective_date\nDrone,UID,Private,2010-02-01\n")
        self.assertEqual(response.status_code, 200, response.json)
        periods = self.ns["drone_periods"](self.con, 1)
        self.assertEqual([(p["project_id"], p["start_date"], p["end_date"]) for p in periods],
                         [(1, "2010-01-01", "2010-01-31"), (2, "2010-02-01", "2010-03-31"),
                          (1, "2010-04-01", None)])
        self.assertIsNone(self.con.execute("SELECT project_id FROM flights WHERE id=2").fetchone()[0])
        kept = self.ns["snapshot_flight_edits"](self.con, 1)
        self.con.execute("UPDATE flights SET project_id=NULL,project_source=NULL")
        self.ns["assign_log_flights"](self.con, 1, kept)
        self.assertEqual(tuple(self.con.execute("SELECT project_id,project_source FROM flights WHERE id=2").fetchone()),
                         (None, "manual"))

    def test_new_assignments_track_origin_and_keep_manual_choices(self):
        self.make_flights()
        self.con.execute("UPDATE flights SET project_id=NULL,project_source=NULL")
        self.ns["assign_open_flights"](self.con)
        self.assertEqual([tuple(r) for r in self.con.execute(
            "SELECT project_id,project_source FROM flights ORDER BY id")], [(1, "timeline")] * 4)
        self.con.commit()
        response = self.client.put("/api/logbook/records", json={
            "record_keys": ["log:2"], "project_id": 1})
        self.assertEqual(response.status_code, 200, response.json)
        response = self.upload("drones", "name,serial,Project,project_effective_date\nDrone,UID,Private,2010-02-01\n")
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual([tuple(r) for r in self.con.execute(
            "SELECT project_id,project_source FROM flights ORDER BY id")],
            [(1, "timeline"), (1, "manual"), (2, "timeline"), (2, "timeline")])

    def test_permissions_templates_and_legacy_migration(self):
        for kind in ("projects", "drones"):
            response = self.client.get(f"/api/admin/{kind}/template")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Example", response.text)
        self.sign_in(2)
        for kind in ("projects", "drones"):
            self.assertEqual(self.upload(kind, "project,person\nResearch,Pilot\n").status_code, 403)
            self.assertEqual(self.client.get(f"/api/admin/{kind}/template").status_code, 403)
        self.make_flights()
        self.con.execute("ALTER TABLE flights DROP COLUMN project_source")
        self.con.commit()
        with patch.object(db, "connect", return_value=self.con):
            db.init("unused")
            db.init("unused")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM flights WHERE project_source IS NULL").fetchone()[0], 4)
