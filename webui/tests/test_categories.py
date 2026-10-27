import io
from unittest.mock import patch

import db

from .test_links import LinkFixtures


class CategoryTests(LinkFixtures):
    def setUp(self):
        super().setUp()
        self.con.executemany("INSERT INTO operators(id,name,registration,own,created_at) VALUES (?,?,?,?,'now')",
                             [(1, "Our lab", "NLD-OWN-1", 1), (2, "Partner", "NLD-PARTNER-2", 0)])
        self.con.executemany(
            "INSERT INTO authorisations(id,name,category,operator_id,valid_from,valid_until,created_at)"
            " VALUES (?,?,?,?,?,?,'now')",
            [(1, "ROC-light", "national", None, "2017-11-08", "2021-09-30"),
             (2, "ROC", "national", None, "2018-10-15", "2021-12-31"),
             (3, "OA-1", "specific", None, "2022-08-12", "2023-12-31"),
             (4, "Partner OA", "specific", 2, "2020-01-01", None)])
        self.con.commit()

    def entry(self, **match):
        return next(r for r in self.logbook() if all(r.get(k) == v for k, v in match.items()))

    def category_of(self, **match):
        r = self.entry(**match)
        return r["category"], r["authorisation_name"], r["category_source"]

    def on(self, flight_id, day):
        stamp = day + "T12:00:00.000Z"
        self.con.execute("UPDATE flights SET takeoff_utc=? WHERE id=?", (stamp, flight_id))
        self.con.execute("UPDATE logs SET start_utc=? WHERE id=(SELECT a.log_id FROM flights f"
                         " JOIN aircraft a ON a.id=f.aircraft_id WHERE f.id=?)", (stamp, flight_id))
        self.con.commit()

    def edit(self, keys, uid=1, **body):
        self.sign_in(uid)
        return self.client.put("/api/logbook/records", json=dict(record_keys=keys, **body))

    def test_the_date_gives_national_rules_open_or_unknown(self):
        self.flight(1, "12:00:00")
        self.on(1, "2016-06-01")
        self.assertEqual(self.category_of(flight_id=1), ("national", None, "date"))  # before the ROCs
        self.on(1, "2018-03-01")
        self.assertEqual(self.category_of(flight_id=1), ("national", "ROC-light", "date"))
        self.on(1, "2019-03-01")  # both valid: the newest one
        self.assertEqual(self.category_of(flight_id=1), ("national", "ROC", "date"))
        self.on(1, "2022-03-01")  # no authorisation of ours: only OPEN was possible
        self.assertEqual(self.category_of(flight_id=1), ("open", None, "date"))
        self.on(1, "2023-03-01")  # an EU authorisation was valid: OPEN or SPECIFIC
        self.assertEqual(self.category_of(flight_id=1), (None, None, None))
        self.assertIsNone(self.entry(flight_id=1)["category_check"])
        self.con.execute("UPDATE flights SET operator_id=2 WHERE id=1")  # not ours: our catalog says nothing
        self.on(1, "2022-03-01")
        self.assertEqual(self.category_of(flight_id=1), (None, None, None))
        self.on(1, "2019-03-01")
        self.assertEqual(self.category_of(flight_id=1), ("national", None, "date"))

    def test_without_a_catalog_only_the_eu_date_applies(self):
        self.con.execute("DELETE FROM authorisations")
        self.flight(1, "12:00:00")
        self.on(1, "2022-03-01")
        self.assertEqual(self.category_of(flight_id=1), (None, None, None))
        self.on(1, "2020-12-30")
        self.assertEqual(self.category_of(flight_id=1), ("national", None, "date"))

    def test_flight_indoor_project_place_and_date_in_that_order(self):
        self.flight(1, "12:00:00")
        self.on(1, "2023-03-01")
        self.con.execute("UPDATE places SET category='specific', authorisation_id=3 WHERE id=1")
        self.assertEqual(self.category_of(flight_id=1), ("specific", "OA-1", "place"))
        self.con.execute("UPDATE projects SET category='open' WHERE id=1")
        self.assertEqual(self.category_of(flight_id=1), ("open", None, "project"))
        self.con.execute("UPDATE places SET indoor=1 WHERE id=1")
        self.assertEqual(self.category_of(flight_id=1), ("indoor", None, "indoor"))
        self.con.execute("UPDATE flights SET category='specific', authorisation_id=3 WHERE id=1")
        self.assertEqual(self.category_of(flight_id=1), ("specific", "OA-1", "flight"))
        detail = self.client.get("/api/detail/aircraft/1").json["flights"][0]
        self.assertEqual((detail["category"], detail["authorisation_name"]), ("specific", "OA-1"))

    def test_specific_without_authorisation_or_outside_its_validity_is_marked(self):
        self.flight(1, "12:00:00")
        self.on(1, "2023-03-01")
        self.con.execute("UPDATE flights SET category='specific' WHERE id=1")
        self.assertEqual(self.entry(flight_id=1)["category_check"], "SPECIFIC without an authorisation")
        self.con.execute("UPDATE flights SET authorisation_id=3 WHERE id=1")
        self.assertIsNone(self.entry(flight_id=1)["category_check"])
        self.on(1, "2024-03-01")
        self.assertEqual(self.entry(flight_id=1)["category_check"], "Flown outside the validity of OA-1")
        self.con.execute("UPDATE flights SET operator_id=2 WHERE id=1")  # another operator's flight: theirs to check
        self.assertIsNone(self.entry(flight_id=1)["category_check"])

    def test_logbook_edit_sets_category_from_the_authorisation_and_clears_it(self):
        self.flight(1, "12:00:00", kind="onboard_sd")
        self.flight(2, "12:00:01", duration=99, kind="gcs")
        self.assertEqual(self.edit(["log:1", "log:2"], authorisation_id=3).status_code, 200)
        self.assertEqual([tuple(r) for r in self.con.execute("SELECT category, authorisation_id FROM flights ORDER BY id")],
                         [("specific", 3), ("specific", 3)])
        self.assertEqual(self.edit(["log:1"], category="open", authorisation_id=3).status_code, 400)  # mismatch
        self.assertEqual(self.edit(["log:1"], category="sometimes").status_code, 400)
        self.assertEqual(self.edit(["log:1"], authorisation_id=99).status_code, 400)
        self.assertEqual(self.edit(["log:1", "log:2"], category="open").status_code, 200)
        self.assertEqual(self.category_of(flight_id=1), ("open", None, "flight"))
        self.assertEqual(self.edit(["log:1", "log:2"], category=None, authorisation_id=None).status_code, 200)
        self.assertEqual(self.category_of(flight_id=1)[2], None)  # no date known: no rule applies

    def test_reparse_keeps_the_category_set_on_a_flight(self):
        self.flight(1, "12:00:00")
        self.con.execute("UPDATE flights SET category='specific', authorisation_id=3 WHERE id=1")
        kept = self.ns["snapshot_flight_edits"](self.con, 1)
        self.con.execute("DELETE FROM aircraft WHERE log_id=1")
        self.con.execute("INSERT INTO aircraft(id,log_id,drone_id,name,ac_id) VALUES(9,1,1,'Drone',7)")
        self.con.execute("INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc) VALUES(19,9,1,100,?)",
                         (self.at("12:00:00"),))
        self.ns["assign_log_flights"](self.con, 1, kept)
        self.assertEqual(tuple(self.con.execute("SELECT category, authorisation_id FROM flights WHERE id=19").fetchone()),
                         ("specific", 3))

    def test_paper_record_category_also_applies_to_the_linked_log_and_csv_columns(self):
        self.flight(1, "12:00:00")
        record = self.create(category="open").json["id"]
        self.assertEqual(self.category_of(flight_id=1), ("open", None, "flight"))
        self.assertEqual(self.edit(["log:1"], authorisation_id=2).status_code, 200)
        self.assertEqual(tuple(self.con.execute("SELECT category, authorisation_id FROM flight_records WHERE id=?",
                                                (record,)).fetchone()), ("national", 2))
        self.assertEqual(self.create(date="2010-01-06", category="never").status_code, 400)
        self.sign_in(1)
        header = "aircraft,pic,project,date,duration_minutes,flight_count,kind,reference"
        line = "Drone,Pilot,Research,2010-01-05,30,3,daily,Book 1"

        def csv(text):
            response = self.client.post("/api/historical/import",
                                        data={"file": (io.BytesIO(text.encode()), "paper.csv")})
            response.request.input_stream.close()
            return response
        self.assertEqual(csv(f"{header},category\n{line},National rules (ROC / ROC-light)\n").status_code, 201)
        stored = lambda: tuple(self.con.execute(  # noqa: E731
            "SELECT category, authorisation_id FROM flight_records WHERE reference='Book 1'").fetchone())
        self.assertEqual(stored(), ("national", None))
        self.assertEqual(csv(f"{header},authorisation\n{line},roc-light\n").status_code, 200)
        self.assertEqual(stored(), ("national", 1))
        again = csv(f"{header}\n{line}\n")  # without the columns: kept
        self.assertEqual((again.json["imported"], again.json["unchanged"]), (0, 1))
        self.assertEqual(stored(), ("national", 1))
        self.assertEqual(csv(f"{header},category,authorisation\n{line},open,ROC\n").status_code, 400)  # mismatch
        self.assertEqual(csv(f"{header},authorisation\n{line},Nothing\n").status_code, 400)

    def test_admin_manages_the_authorisation_catalog(self):
        self.assertEqual(self.client.get("/api/authorisations").json["authorisations"][0]["name"], "ROC-light")
        self.assertEqual(self.client.post("/api/admin/authorisations", json=dict(name="X", category="luc")).status_code, 403)
        self.sign_in(1)
        new = self.client.post("/api/admin/authorisations",
                               json=dict(name="OA-2", category="specific", valid_from="2026-08-26", valid_until="2031-08-26"))
        self.assertEqual(new.status_code, 200, new.json)
        for bad in (dict(name="OA-2", category="specific"), dict(name="Y", category="open"),
                    dict(name="Y", category="luc", valid_from="2026-02-30"),
                    dict(name="Y", category="luc", valid_from="2026-02-01", valid_until="2025-01-01")):
            self.assertIn(self.client.post("/api/admin/authorisations", json=bad).status_code, (400, 409), bad)
        self.flight(1, "12:00:00")
        self.con.execute("UPDATE flights SET category='specific', authorisation_id=3 WHERE id=1")
        self.con.commit()
        # Changing the category of an authorisation moves what names it along.
        self.assertEqual(self.client.put("/api/admin/authorisations/3", json=dict(category="luc")).status_code, 200)
        self.assertEqual(self.con.execute("SELECT category FROM flights WHERE id=1").fetchone()[0], "luc")
        self.assertEqual(self.client.delete("/api/admin/authorisations/3").status_code, 409)
        self.assertEqual(self.client.delete(f"/api/admin/authorisations/{new.json['id']}").status_code, 200)

    def test_project_and_place_defaults_through_the_api(self):
        self.sign_in(1)
        self.assertEqual(self.client.put("/api/admin/projects/1", json=dict(authorisation_id=3)).status_code, 200)
        self.assertEqual(tuple(self.con.execute("SELECT category, authorisation_id FROM projects WHERE id=1").fetchone()),
                         ("specific", 3))
        self.assertEqual(self.client.put("/api/admin/projects/1", json=dict(category="bad")).status_code, 400)
        self.assertEqual(self.client.put("/api/admin/places/1", json=dict(name="Field", category="open")).status_code, 200)
        self.assertEqual(self.con.execute("SELECT category FROM places WHERE id=1").fetchone()[0], "open")

    def test_category_filter_of_the_places_overview(self):
        self.sign_in(1)
        self.assertEqual(self.client.get("/api/places?category=open").status_code, 200)
        self.assertEqual(self.client.get("/api/places?category=bogus").status_code, 400)

    def test_schema_migration_adds_the_category_columns(self):
        for table in ("flights", "places"):  # (SQLite cannot drop the last column after a commented one in projects)
            self.con.execute(f"ALTER TABLE {table} DROP COLUMN authorisation_id")
            self.con.execute(f"ALTER TABLE {table} DROP COLUMN category")
        self.con.commit()
        with patch.object(db, "connect", return_value=self.con):
            db.init("unused")
            db.init("unused")
        for table in ("flights", "flight_records", "projects", "places"):
            columns = {r[1] for r in self.con.execute(f"PRAGMA table_info({table})")}
            self.assertTrue({"category", "authorisation_id"} <= columns, table)
