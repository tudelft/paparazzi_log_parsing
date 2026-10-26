import hashlib
import io
import json
from unittest.mock import patch

import db
import historical

from .test_links import LinkFixtures


class OperatorTests(LinkFixtures):
    def setUp(self):
        super().setUp()
        self.con.executemany("INSERT INTO operators(id,name,registration,country,own,created_at) VALUES (?,?,?,?,?,'now')",
                             [(1, "Our lab", "NLD-OWN-1", "NL", 1), (2, "Partner", "NLD-PARTNER-2", "NL", 0),
                              (3, "Test centre", "TC-3", "NL", 0)])
        self.con.commit()

    def entry(self, **match):
        return next(r for r in self.logbook() if all(r.get(k) == v for k, v in match.items()))

    def operator_of(self, **match):
        r = self.entry(**match)
        return r["operator_name"], r["operator_source"], r["operator_own"]

    def edit(self, keys, operator_id, uid=1):
        self.sign_in(uid)
        return self.client.put("/api/logbook/records", json=dict(record_keys=keys, operator_id=operator_id))

    def csv(self, text):
        response = self.client.post("/api/historical/import", data={"file": (io.BytesIO(text.encode()), "paper.csv")})
        response.request.input_stream.close()
        return response

    def test_flight_project_place_and_own_operator_in_that_order(self):
        self.flight(1, "12:00:00")
        self.assertEqual(self.operator_of(flight_id=1), ("Our lab", "default", True))
        self.con.execute("UPDATE places SET operator_id=3 WHERE id=1")  # the flight takes off in this place
        self.assertEqual(self.operator_of(flight_id=1), ("Test centre", "place", False))
        self.con.execute("UPDATE projects SET operator_id=2 WHERE id=1")
        self.assertEqual(self.operator_of(flight_id=1), ("Partner", "project", False))
        self.con.execute("UPDATE flights SET operator_id=1 WHERE id=1")
        self.assertEqual(self.operator_of(flight_id=1), ("Our lab", "flight", True))
        row = self.entry(flight_id=1)
        self.assertEqual(row["operator_registration"], "NLD-OWN-1")
        compact = self.client.get("/api/logbook?compact=1").json[0]
        self.assertEqual((compact["operator_id"], compact["operator_source"]), (1, "flight"))
        detail = self.client.get("/api/detail/aircraft/1").json["flights"][0]
        self.assertEqual((detail["operator_name"], detail["operator_own"]), ("Our lab", True))

    def test_without_operators_every_flight_counts_as_own(self):
        self.con.execute("DELETE FROM operators")
        self.flight(1, "12:00:00")
        self.assertEqual(self.operator_of(flight_id=1), (None, "default", True))

    def test_logbook_edit_sets_and_clears_the_operator_of_all_recordings(self):
        self.flight(1, "12:00:00", kind="onboard_sd")
        self.flight(2, "12:00:01", duration=99, kind="gcs")
        self.assertEqual(self.edit(["log:1", "log:2"], 2).status_code, 200)
        self.assertEqual([tuple(r) for r in self.con.execute("SELECT operator_id FROM flights ORDER BY id")], [(2,), (2,)])
        self.assertEqual(self.operator_of(flight_id=1), ("Partner", "flight", False))
        self.assertEqual(self.edit(["log:1"], 99).status_code, 400)
        self.assertEqual(self.edit(["log:1", "log:2"], None).status_code, 200)
        self.assertEqual(self.operator_of(flight_id=1), ("Our lab", "default", True))
        self.assertEqual(self.edit(["log:1"], 2, uid=4).status_code, 404)  # not visible to this person

    def test_reparse_keeps_the_operator_set_on_a_flight(self):
        self.flight(1, "12:00:00")
        self.con.execute("UPDATE flights SET operator_id=2 WHERE id=1")
        kept = self.ns["snapshot_flight_edits"](self.con, 1)
        self.con.execute("DELETE FROM aircraft WHERE log_id=1")
        self.con.execute("INSERT INTO aircraft(id,log_id,drone_id,name,ac_id) VALUES(9,1,1,'Drone',7)")
        self.con.execute("INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc) VALUES(19,9,1,100,?)",
                         (self.at("12:00:00"),))
        self.ns["assign_log_flights"](self.con, 1, kept)
        self.assertEqual(self.con.execute("SELECT operator_id FROM flights WHERE id=19").fetchone()[0], 2)

    def test_paper_record_operator_also_applies_to_the_linked_log(self):
        self.flight(1, "12:00:00")
        record = self.create(operator_id=2).json["id"]
        self.assertEqual(self.con.execute("SELECT operator_id FROM flight_records WHERE id=?", (record,)).fetchone()[0], 2)
        row = self.entry(flight_id=1)
        self.assertEqual(row["linked_records"][0]["id"], record)
        self.assertEqual((row["operator_name"], row["operator_source"]), ("Partner", "flight"))
        # Changing the entry in the logbook changes its linked paper record too, so the entry can be reset.
        self.assertEqual(self.edit(["log:1"], None).status_code, 200)
        self.assertEqual(self.operator_of(flight_id=1), ("Our lab", "default", True))
        self.assertIsNone(self.con.execute("SELECT operator_id FROM flight_records WHERE id=?", (record,)).fetchone()[0])
        self.assertEqual(self.edit(["log:1"], 3).status_code, 200)
        self.assertEqual(self.con.execute("SELECT operator_id FROM flight_records WHERE id=?", (record,)).fetchone()[0], 3)
        # A paper record on its own, and its edit from the logbook (admins only).
        other = self.create(date="2010-01-05", reference="F00002").json["id"]
        self.assertEqual(self.operator_of(manual_id=other), ("Our lab", "default", True))
        self.assertEqual(self.edit([f"paper:{other}"], 3, uid=2).status_code, 403)
        self.assertEqual(self.edit([f"paper:{other}"], 3).status_code, 200)
        self.assertEqual(self.operator_of(manual_id=other), ("Test centre", "flight", False))
        self.assertEqual(self.create(operator_id=42, date="2010-01-06").status_code, 400)

    def test_operator_is_not_part_of_the_record_fingerprint(self):
        record = self.create(date="2010-01-05").json["id"]
        row = dict(self.con.execute("SELECT * FROM flight_records WHERE id=?", (record,)).fetchone())
        before = {k: row[k] for k in historical.FIELDS if k != "operator_id"}
        legacy = hashlib.sha256(json.dumps(before, sort_keys=True, allow_nan=False).encode()).hexdigest()
        self.assertEqual(row["fingerprint"], legacy)  # records stored before operators keep their identity
        self.assertEqual(self.create(date="2010-01-05", operator_id=2).status_code, 409)  # same record

    def test_csv_operator_column_fills_records_and_a_csv_without_it_keeps_them(self):
        self.sign_in(1)
        header = "aircraft,pic,project,date,duration_minutes,flight_count,kind,reference"
        line = "Drone,Pilot,Research,2010-01-05,30,3,daily,Book 1"
        self.assertEqual(self.csv(f"{header}\n{line}\n").status_code, 201)
        result = self.csv(f"{header},operator\n{line},nld-partner-2\n")  # by registration, any case
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(self.con.execute("SELECT operator_id FROM flight_records").fetchone()[0], 2)
        again = self.csv(f"{header}\n{line}\n")
        self.assertEqual((again.json["imported"], again.json["unchanged"]), (0, 1))
        self.assertEqual(self.con.execute("SELECT operator_id FROM flight_records").fetchone()[0], 2)
        self.assertEqual(self.csv(f"{header},operator\n{line},Test centre\n").status_code, 200)
        self.assertEqual(self.con.execute("SELECT operator_id FROM flight_records").fetchone()[0], 3)
        self.assertEqual(self.csv(f"{header},operator\n{line},\n").status_code, 200)  # blank: back to the default
        self.assertIsNone(self.con.execute("SELECT operator_id FROM flight_records").fetchone()[0])
        unknown = self.csv(f"{header},operator\n{line},Nobody\n")
        self.assertEqual(unknown.status_code, 400)
        self.assertIn("operator", unknown.json["error"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM flight_records").fetchone()[0], 1)

    def test_external_pic_on_a_default_operator_is_marked_to_check(self):
        self.flight(1, "12:00:00")
        self.con.execute("UPDATE users SET is_external=1 WHERE id=2")
        self.sign_in(1)  # external crew cannot sign in
        self.con.execute("UPDATE flights SET pic_user_id=2 WHERE id=1")
        self.assertTrue(self.entry(flight_id=1)["operator_check"])
        self.con.execute("UPDATE flights SET operator_id=2 WHERE id=1")
        self.assertFalse(self.entry(flight_id=1)["operator_check"])

    def test_admin_manages_operators_with_one_own_operator(self):
        self.assertEqual(self.client.get("/api/operators").json["operators"][0]["name"], "Our lab")
        self.assertEqual(self.client.post("/api/admin/operators", json=dict(name="X")).status_code, 403)
        self.sign_in(1)
        created = self.client.post("/api/admin/operators", json=dict(name="New lab", registration="NEW-9", own=True))
        self.assertEqual(created.status_code, 200, created.json)
        own = self.con.execute("SELECT name FROM operators WHERE own=1").fetchall()
        self.assertEqual([r[0] for r in own], ["New lab"])
        self.assertEqual(self.client.post("/api/admin/operators", json=dict(name="partner")).status_code, 409)
        self.assertEqual(self.client.post("/api/admin/operators", json=dict(name="")).status_code, 400)
        self.assertEqual(self.client.put("/api/admin/operators/2", json=dict(name="Partner", note="Their authorisation")).status_code, 200)
        self.assertEqual(self.con.execute("SELECT note FROM operators WHERE id=2").fetchone()[0], "Their authorisation")
        self.assertEqual(self.client.put("/api/admin/operators/3", json=dict(note="Range")).status_code, 200)  # the rest is kept
        self.assertEqual(tuple(self.con.execute("SELECT name, registration, note FROM operators WHERE id=3").fetchone()),
                         ("Test centre", "TC-3", "Range"))
        self.assertEqual(self.client.put("/api/admin/projects/1", json=dict(operator_id=2, name="")).status_code, 400)
        self.assertIsNone(self.con.execute("SELECT operator_id FROM projects WHERE id=1").fetchone()[0])  # nothing half-saved
        self.assertEqual(self.client.put("/api/admin/projects/1", json=dict(operator_id=2)).status_code, 200)
        self.assertEqual(tuple(self.con.execute("SELECT name, operator_id FROM projects WHERE id=1").fetchone()), ("Research", 2))
        self.assertEqual(self.client.put("/api/admin/projects/1", json=dict(operator_id=99)).status_code, 400)
        self.assertEqual(self.client.put("/api/admin/places/1", json=dict(operator_id=3)).status_code, 200)
        self.assertEqual(tuple(self.con.execute("SELECT name, operator_id FROM places WHERE id=1").fetchone()), ("Field", 3))
        used = self.client.delete("/api/admin/operators/2")
        self.assertEqual(used.status_code, 409)
        self.assertIn("1 projects", used.json["error"])
        self.client.put("/api/admin/projects/1", json=dict(operator_id=None))
        self.assertEqual(self.client.delete("/api/admin/operators/2").status_code, 200)

    def test_places_count_only_the_chosen_operators(self):
        self.flight(1, "12:00:00")
        self.flight(2, "14:00:00")
        self.con.execute("UPDATE flights SET operator_id=2 WHERE id=2")
        self.con.commit()
        flights = {op: self.client.get(f"/api/places?unknown_dates=1&operator={op}").json["places"][0]["flights"]
                   for op in ("all", "own", "other")}
        self.assertEqual(flights, {"all": 2, "own": 1, "other": 1})
        self.assertEqual(self.client.get("/api/places?operator=Own").status_code, 400)

    def test_people_directory_rows_carry_the_operator_for_the_shared_filter(self):
        self.flight(1, "12:00:00")
        self.con.execute("UPDATE flights SET operator_id=2, pic_user_id=1 WHERE id=1")
        self.sign_in(1)
        rows = self.client.get("/api/people-pic-flights").json["flights"]
        self.assertEqual([r["operator_own"] for r in rows], [False])

    def test_the_operator_a_pic_flies_for_comes_after_project_and_place(self):
        self.flight(1, "12:00:00")
        self.con.execute("UPDATE flights SET pic_user_id=2 WHERE id=1")
        self.con.execute("UPDATE users SET operator_id=2 WHERE id=2")  # the PIC flies for the partner
        self.assertEqual(self.operator_of(flight_id=1), ("Partner", "pic", False))
        self.con.execute("UPDATE places SET operator_id=3 WHERE id=1")
        self.assertEqual(self.operator_of(flight_id=1), ("Test centre", "place", False))
        self.con.execute("UPDATE places SET operator_id=NULL WHERE id=1")
        self.con.execute("UPDATE flights SET operator_id=1 WHERE id=1")  # flown under our operation after all
        self.assertEqual(self.operator_of(flight_id=1), ("Our lab", "flight", True))
        self.con.commit()
        created = self.create(date="2010-01-05")
        self.assertEqual(created.status_code, 201, created.json)
        paper = created.json["id"]  # paper records follow their PIC too
        self.assertEqual(self.operator_of(manual_id=paper), ("Partner", "pic", False))
        # External crew with an operator are not marked to check; without one they are.
        self.sign_in(1)  # external crew cannot sign in
        self.con.execute("UPDATE users SET is_external=1 WHERE id=2")
        self.assertFalse(self.entry(manual_id=paper)["operator_check"])
        self.con.execute("UPDATE users SET operator_id=NULL WHERE id=2")
        self.assertTrue(self.entry(manual_id=paper)["operator_check"])

    def test_anyone_can_add_a_partner_operator_and_admins_set_who_flies_for_it(self):
        self.sign_in(2)
        added = self.client.post("/api/operators", json=dict(name="New partner", registration="NP-1", country="BE"))
        self.assertEqual(added.status_code, 201, added.json)
        self.assertEqual(self.client.post("/api/operators", json=dict(name="Ours", own=True)).status_code, 403)
        self.assertEqual(self.client.post("/api/operators", json=dict(name="new PARTNER")).status_code, 409)
        self.assertEqual(self.client.put(f"/api/admin/operators/{added.json['id']}", json=dict(note="x")).status_code, 403)
        self.assertEqual(self.client.put("/api/admin/users/2/operator", json=dict(operator_id=2)).status_code, 403)
        self.sign_in(1)
        self.assertEqual(self.client.put("/api/admin/users/2/operator", json=dict(operator_id=added.json["id"])).status_code, 200)
        self.assertEqual(self.client.put("/api/admin/users/2/operator", json=dict(operator_id=99)).status_code, 400)
        people = self.client.get("/api/admin/people").json
        self.assertEqual(next(u for u in people["users"] if u["id"] == 2)["operator_id"], added.json["id"])
        self.assertIn("New partner", [o["name"] for o in people["operators"]])
        used = self.client.delete(f"/api/admin/operators/{added.json['id']}")
        self.assertEqual(used.status_code, 409)
        self.assertIn("1 people", used.json["error"])
        external = self.client.post("/api/admin/people/external", json=dict(name="Guest Pilot", operator_id=3))
        self.assertEqual(external.status_code, 201, external.json)
        self.assertEqual(tuple(self.con.execute("SELECT is_external, operator_id FROM users WHERE id=?",
                                                (external.json["id"],)).fetchone()), (1, 3))
        self.assertEqual(self.client.post("/api/admin/people/external", json=dict(name="Other", operator_id=99)).status_code, 400)

    def test_schema_migration_adds_operator_columns(self):
        for table in ("flights", "projects", "places", "users"):
            self.con.execute(f"ALTER TABLE {table} DROP COLUMN operator_id")
        self.con.commit()
        with patch.object(db, "connect", return_value=self.con):
            db.init("unused")
            db.init("unused")
        for table in ("flights", "flight_records", "projects", "places", "users"):
            self.assertIn("operator_id", {r[1] for r in self.con.execute(f"PRAGMA table_info({table})")})
