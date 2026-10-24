import json
from copy import deepcopy
from unittest.mock import patch

import db
from .test_historical import AppTests
from .test_merge_identity import MergeIdentityTests
from .test_simulation import parse_simulation_fixture


SIMULATION = {"detected": True, "evidence": ["telemetry:NPS_WIND"]}
PHYSICAL = {"detected": False, "evidence": []}


class SimulationWebTests(AppTests):
    sources = MergeIdentityTests.sources

    def setUp(self):
        super().setUp()
        self.sources(1, 1)
        self.con.execute("UPDATE aircraft SET pic='Pilot'")
        self.con.execute("UPDATE flights SET has_gps_position=1, project_id=1")
        self.con.commit()

    def set_simulation(self, identity, value):
        self.con.execute("UPDATE aircraft SET simulation=? WHERE id=?",
                         (json.dumps(value) if value is not None else None, identity))
        self.con.commit()

    def test_metadata_reaches_overviews_upload_and_direct_flight_for_authorized_users(self):
        self.set_simulation(1, SIMULATION)
        self.set_simulation(2, PHYSICAL)
        for uid in (1, 2, 3):
            self.sign_in(uid)
            for url in ("/api/logbook", "/api/stats", "/api/detail/people/2",
                        "/api/detail/aircraft/1", "/api/detail/projects/1", "/api/detail/locations/1"):
                result = self.client.get(url)
                self.assertEqual(result.status_code, 200, url)
                rows = result.json if isinstance(result.json, list) else result.json["flights"]
                self.assertEqual(len(rows), 2, url)
                self.assertEqual(sum(bool(r["simulation"]["detected"]) for r in rows), 1, url)
            self.assertEqual(self.client.get("/api/logs/1").json["aircraft"][0]["simulation"], SIMULATION)
            for identity, expected in ((1, SIMULATION), (2, PHYSICAL)):
                entry = self.client.get(f"/api/flights/{identity}/detail").json["entry"]
                self.assertEqual(entry["simulation"], expected)
                self.assertEqual(len(entry["sources"]), 1)
        self.sign_in(4)
        self.assertEqual(self.client.get("/api/logbook").json, [])
        self.assertEqual(self.client.get("/api/flights/1/detail").status_code, 404)
        self.assertEqual(self.client.get("/api/logs/1").status_code, 404)

    def test_locations_default_totals_and_opt_in(self):
        self.set_simulation(1, SIMULATION)
        self.set_simulation(2, PHYSICAL)
        for uid in (1, 2, 3):
            self.sign_in(uid)
            for suffix, count in (("", 1), ("?nonflights=1", 2)):
                data = self.client.get("/api/places" + suffix).json
                place = next(p for p in data["places"] if p["id"] == 1)
                self.assertEqual(place["flights"], count)
                self.assertEqual(place["seconds"], count * 100)
                self.assertEqual(sum(p["count"] for p in data["points"]), count * 2)

    def test_simulations_merge_with_simulations_and_retain_all_evidence(self):
        other = {"detected": True, "evidence": ["configuration:nps-only-pc"]}
        self.set_simulation(1, SIMULATION)
        self.set_simulation(2, other)
        rows = self.client.get("/api/logbook").json
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["simulation"], {
            "detected": True, "evidence": ["configuration:nps-only-pc", "telemetry:NPS_WIND"],
        })

    def test_ambiguous_simulation_cannot_infer_identity_from_physical_gcs(self):
        self.con.execute("INSERT INTO ambiguous_drone_names(name) VALUES('Airframe')")
        self.con.execute("UPDATE aircraft SET drone_id=NULL WHERE id=1")
        self.set_simulation(1, SIMULATION)
        rows = self.client.get("/api/logbook").json
        self.assertEqual(len(rows), 2)
        simulation = next(r for r in rows if (r["simulation"] or {}).get("detected"))
        self.assertIsNone(simulation["drone_id"])

    def test_unknown_metadata_remains_unknown_and_never_merges_with_detected_simulation(self):
        rows = self.client.get("/api/logbook").json
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["simulation"])
        self.set_simulation(1, SIMULATION)
        rows = self.client.get("/api/logbook").json
        self.assertEqual(len(rows), 2)

    def test_storage_preserves_metadata_and_legacy_missing_metadata(self):
        doc = {"aircraft": [{"ac_id": 7, "name": "Test", "simulation": deepcopy(SIMULATION)}]}
        for metadata in (SIMULATION, PHYSICAL, None):
            with self.subTest(metadata=metadata):
                if metadata is None:
                    doc["aircraft"][0].pop("simulation", None)
                else:
                    doc["aircraft"][0]["simulation"] = metadata
                db.store_parsed(self.con, 1, doc, None)
                stored = self.con.execute("SELECT simulation FROM aircraft WHERE log_id=1").fetchone()[0]
                self.assertEqual(json.loads(stored) if stored is not None else None, metadata)

    def test_parser_to_database_to_web_with_multiple_aircraft(self):
        doc = parse_simulation_fixture(
            {5: ("Drone", ""), 6: ("Physical", "")},
            ["1 5 NPS_WIND 0 0 0\n"],
        )
        self.con.execute("DELETE FROM logs WHERE id=2")
        db.store_parsed(self.con, 1, doc, None)
        self.con.commit()
        rows = {r["ac_id"]: r for r in self.client.get("/api/logbook").json}
        self.assertEqual(rows[5]["simulation"], SIMULATION)
        self.assertEqual(rows[6]["simulation"], PHYSICAL)
        self.assertEqual(len(rows), 2)
        self.assertEqual(self.client.get(
            f'/api/flights/{rows[5]["flight_id"]}/detail').json["entry"]["simulation"], SIMULATION)

    def test_schema_migration_is_idempotent_and_preserves_legacy_aircraft(self):
        self.con.execute("ALTER TABLE aircraft DROP COLUMN simulation")
        with patch.object(db, "connect", return_value=self.con):
            db.init("unused-test-path")
            db.init("unused-test-path")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM aircraft").fetchone()[0], 2)
        self.assertTrue(all(r[0] is None for r in self.con.execute("SELECT simulation FROM aircraft")))
