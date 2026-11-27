import json

from .test_historical import AppTests


class TagTests(AppTests):
    """Tags of logbook entries (#368)."""

    def setUp(self):
        super().setUp()
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by,project_id,log_type)"
            " VALUES(1,'Digital','test','test.data','done','now',2,1,'onboard_sd')")
        self.con.execute("INSERT INTO aircraft(id,log_id,drone_id,name,ac_id) VALUES(1,1,1,'Drone',7)")
        self.con.executemany(
            "INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc,verdict,project_id,has_gps_position,"
            " takeoff_lat,takeoff_lon) VALUES(?,1,?,100,?,'flight',1,1,52.0,4.0)",
            [(1, 0, "2011-01-01T12:00:00.000Z"), (2, 1, "2011-01-01T13:00:00.000Z")])
        self.con.commit()

    def entry(self, key):
        return next(r for r in self.client.get("/api/logbook?compact=1").json if r["record_key"] == key)

    def test_tag_a_flight_creates_tags_and_matches_names_without_case(self):
        self.sign_in(3)  # view access only
        self.assertEqual(self.client.put("/api/flights/1/tags", json={"tags": ["Bench test"]}).status_code, 403)
        self.sign_in(2)
        self.assertEqual(self.client.put("/api/flights/1/tags", json={"tags": ["Bench test", "a,b"]}).status_code, 400)
        self.assertEqual(self.client.put("/api/flights/1/tags", json={"tags": ["Bench  test", "new props"]}).status_code, 200)
        self.assertEqual(self.client.put("/api/flights/2/tags", json={"tags": ["bench TEST"]}).status_code, 200)
        self.assertEqual(self.entry("log:1")["tags"], ["Bench test", "new props"])
        self.assertEqual(self.entry("log:2")["tags"], ["Bench test"])
        catalog = {t["name"]: t["entries"] for t in self.client.get("/api/tags").json["tags"]}
        self.assertEqual(catalog, {"Bench test": 2, "new props": 1})
        kept = self.ns["snapshot_flight_edits"](self.con, 1)[(7, 0)]
        self.assertEqual(len(json.loads(kept["tags"])), 2)  # restored after a re-parse

    def test_bulk_tags_paper_records_and_admin_management(self):
        record = self.create(tags=["Paper study"]).json["id"]
        self.assertEqual(self.entry(f"paper:{record}")["tags"], ["Paper study"])
        self.sign_in(1)
        response = self.client.put("/api/logbook/records", json={"record_keys": ["log:1", "log:2", f"paper:{record}"],
                                                                "tags_add": ["sim-study-3"]})
        self.assertEqual(response.status_code, 200, response.json)
        ids = {t["name"]: t["id"] for t in self.client.get("/api/tags").json["tags"]}
        self.client.put("/api/logbook/records", json={"record_keys": ["log:2"], "tags_remove": [ids["sim-study-3"]]})
        self.assertEqual((self.entry("log:1")["tags"], self.entry("log:2")["tags"]), (["sim-study-3"], []))
        # hidden by default: the entry says which of its tags are hidden
        self.assertEqual(self.client.put(f"/api/admin/tags/{ids['sim-study-3']}", json={"hidden": True}).status_code, 200)
        self.assertEqual(self.entry("log:1")["tags_hidden"], ["sim-study-3"])
        # rename, merge into another, delete
        self.assertEqual(self.client.put(f"/api/admin/tags/{ids['Paper study']}", json={"name": "SIM-study-3"}).status_code, 409)
        self.client.post(f"/api/admin/tags/{ids['Paper study']}/merge", json={"into": ids["sim-study-3"]})
        self.assertEqual(self.entry(f"paper:{record}")["tags"], ["sim-study-3"])
        self.client.delete(f"/api/admin/tags/{ids['sim-study-3']}")
        self.assertEqual((self.entry("log:1")["tags"], self.entry(f"paper:{record}")["tags"]), ([], []))
        self.sign_in(2)
        self.assertEqual(self.client.put(f"/api/admin/tags/{ids['Paper study']}", json={"hidden": True}).status_code, 403)

    def test_hidden_tags_are_left_out_of_the_home_totals(self):
        self.sign_in(1)
        self.client.put("/api/flights/1/tags", json={"tags": ["sim-study-3"]})
        tag = self.client.get("/api/tags").json["tags"][0]["id"]
        self.client.put(f"/api/admin/tags/{tag}", json={"hidden": True})
        rows = self.client.get("/api/logbook").json
        home = __import__("home")
        self.assertEqual([home.real_flight(r) for r in sorted(rows, key=lambda r: r["flight_id"])], [False, True])


class UploadTagTests(AppTests):
    """Tags given with an upload go to the flights of its log (#368)."""

    def test_upload_tags_reach_the_log_and_its_flights(self):
        import io
        self.sign_in(2)
        response = self.client.post("/api/upload", data={"files": (io.BytesIO(b"blackbox"), "LOG1.BFL"),
                                                          "names": "LOG1.BFL", "tags": ["sim-study-3", "bench test,new"]})
        self.assertEqual(response.status_code, 200, response.json)
        iid = response.json["results"][0]["ingest_id"]
        given = json.loads(self.con.execute("SELECT tags FROM ingest WHERE id=?", (iid,)).fetchone()[0])
        self.assertEqual(len(given), 3)  # names may also come comma-separated; new ones are created
        bad = self.client.post("/api/upload", data={"files": (io.BytesIO(b"x"), "L.BFL"), "names": "L.BFL", "tags": "x" * 41})
        self.assertEqual(bad.status_code, 400)
        # the log is already there with a flight: it gets the tags at once
        self.con.execute("INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by,project_id)"
                         " VALUES(1,'LOG1.BFL','s','d','done','now',2,1)")
        self.con.execute("INSERT INTO aircraft(id,log_id,drone_id,name,ac_id) VALUES(1,1,1,'Drone',7)")
        self.con.execute("INSERT INTO flights(id,aircraft_id,idx,duration_s,project_id) VALUES(1,1,0,100,1)")
        self.con.commit()
        self.ns["set_ingest"](self.con, iid, status="duplicate", log_id=1)
        self.assertEqual(sorted(json.loads(self.con.execute("SELECT tags FROM flights WHERE id=1").fetchone()[0])), sorted(given))
        # a flight that a (re-)parse adds gets them too; a flight kept from before keeps its own tags
        self.con.execute("INSERT INTO flights(id,aircraft_id,idx,duration_s,project_id) VALUES(2,1,1,100,1)")
        self.con.execute("UPDATE flights SET tags=NULL WHERE id=1")
        self.con.commit()
        self.ns["assign_log_flights"](self.con, 1, kept={(7, 0): {"tags": None}})
        rows = dict(self.con.execute("SELECT id, tags FROM flights").fetchall())
        self.assertEqual((rows[1], sorted(json.loads(rows[2]))), (None, sorted(given)))
