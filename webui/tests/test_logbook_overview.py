import gzip
import hashlib
import json
import os
import time
from datetime import datetime, timedelta
from statistics import median
from unittest.mock import patch

from .test_historical import AppTests
from .test_merge_identity import MergeIdentityTests


class LogbookOverviewTests(AppTests):
    sources = MergeIdentityTests.sources

    def setUp(self):
        super().setUp()
        self.sources(1, 1)
        self.con.execute("UPDATE flights SET has_gps_position=1, pic_user_id=2, project_id=1")
        self.con.execute("UPDATE aircraft SET pic='Synthetic Pilot', simulation=?",
                         (json.dumps({"detected": False, "evidence": ["synthetic-evidence"]}),))
        self.con.commit()
        self.assertEqual(self.create().status_code, 201)

    def test_compact_keeps_overview_csv_and_edits_with_merged_and_paper_records(self):
        full = self.client.get("/api/logbook").json
        compact = self.client.get("/api/logbook?compact=1").json
        self.assertEqual([r["record_key"] for r in compact], [r["record_key"] for r in full])
        for before, after in zip(full, compact):
            self.assertEqual({k: before[k] for k in self.ns["LOGBOOK_OVERVIEW_KEYS"] if k in before},
                             {k: v for k, v in after.items() if k in self.ns["LOGBOOK_OVERVIEW_KEYS"]})
            for role in ("pic", "pac", "gcs"):
                user = before[role + "_user"]
                self.assertEqual(after[role + "_user"],
                                 {k: user[k] for k in ("id", "display_name", "source") if k in user}
                                 if user else None)
            self.assertEqual(after["sources"], [
                {k: s[k] for k in ("flight_id", "log_id", "log_type")} for s in before["sources"]])
            self.assertNotIn("assessment", after)
            self.assertNotIn("takeoff", after)
            self.assertNotIn("landing", after)
        telemetry = next(r for r in compact if r["source_type"] == "telemetry")
        self.assertEqual(len(telemetry["sources"]), 2)
        self.assertEqual(telemetry["simulation"], {"detected": False})
        self.assertEqual(self.client.get("/api/logbook?compact=0").json, full)
        self.assertIn("assessment", next(r for r in full if r["source_type"] == "telemetry"))

    def test_visibility_project_hidden_and_api_key_rules_are_unchanged(self):
        for uid in (1, 2, 3, 4, 5):
            self.sign_in(uid)
            for query in ("", "&project=1", "&project=2", "&project=none", "&hidden=1"):
                before = self.client.get("/api/logbook?" + query.lstrip("&"))
                after = self.client.get("/api/logbook?compact=1" + query)
                self.assertEqual(before.status_code, after.status_code)
                if before.status_code == 200:
                    self.assertEqual([r["record_key"] for r in before.json],
                                     [r["record_key"] for r in after.json])
        self.con.execute("UPDATE flights SET hidden=1")
        self.con.commit()
        self.sign_in(1)
        self.assertTrue(self.client.get("/api/logbook?compact=1&hidden=1").json)
        self.sign_in(3)
        self.assertEqual(self.client.get("/api/logbook?compact=1&hidden=1").status_code, 403)
        self.assertEqual(self.client.get("/api/flights/1/path").status_code, 200)
        self.sign_in(4)
        self.assertEqual(self.client.get("/api/flights/1/path").status_code, 404)
        self.con.execute(
            "INSERT INTO api_keys(user_id,name,key_hash,prefix,created_at) VALUES(2,'fixture',?,'test','now')",
            (hashlib.sha256(b"logdb_overview-key").hexdigest(),))
        self.con.commit()
        self.client.post("/api/logout")
        headers = {"Authorization": "Bearer logdb_overview-key"}
        self.assertEqual(self.client.get("/api/logbook?compact=1", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/logbook?compact=1&hidden=1", headers=headers).status_code, 403)

    def test_simulation_unknown_and_source_separation(self):
        self.con.execute("UPDATE aircraft SET simulation=NULL WHERE id=1")
        self.con.execute("UPDATE aircraft SET simulation=? WHERE id=2",
                         (json.dumps({"detected": True, "evidence": ["synthetic"]}),))
        self.con.commit()
        full = self.client.get("/api/logbook").json
        compact = self.client.get("/api/logbook?compact=1").json
        self.assertEqual(len(compact), 3)
        self.assertEqual([r["record_key"] for r in compact], [r["record_key"] for r in full])
        self.assertEqual([r.get("simulation") for r in compact if r["source_type"] == "telemetry"],
                         [None if r["simulation"] is None else {"detected": r["simulation"]["detected"]}
                          for r in full if r["source_type"] == "telemetry"])

    def test_unlinked_filter_and_small_or_unauthorized_responses(self):
        self.con.execute("UPDATE aircraft SET drone_id=NULL")
        self.con.commit()
        with patch.dict(os.environ, LOGDB_HIDE_UNLINKED_AIRCRAFT="1"):
            for suffix in ("", "?compact=1"):
                self.assertTrue(all(r["source_type"] == "paper"
                                    for r in self.client.get("/api/logbook" + suffix).json))
        self.sign_in(4)
        response = self.client.get("/api/logbook?compact=1", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(response.json, [])
        self.assertNotIn("Content-Encoding", response.headers)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get("/api/logbook?compact=1").status_code, 401)

    def test_comments_remain_in_overview_and_editable_through_flight_api(self):
        self.con.execute("UPDATE flights SET project_id=NULL")
        self.con.commit()
        telemetry = next(r for r in self.client.get("/api/logbook?compact=1").json
                         if r["source_type"] == "telemetry")
        for source in telemetry["sources"]:
            response = self.client.put(f'/api/flights/{source["flight_id"]}/comment',
                                       json={"comment": "Synthetic flight note"})
            self.assertEqual(response.status_code, 200)
        full = self.client.get("/api/flights/1/detail").json["entry"]
        compact = next(r for r in self.client.get("/api/logbook?compact=1").json
                       if r["source_type"] == "telemetry")
        self.assertEqual(full["comment"], "Synthetic flight note")
        self.assertEqual(compact["comment"], full["comment"])
        for uid, status in ((3, 403), (4, 404)):
            self.sign_in(uid)
            self.assertEqual(self.client.put("/api/flights/1/comment", json={"comment": "Not allowed"}).status_code,
                             status)

    def test_compression_negotiation_default_compatibility(self):
        for suffix in ("", "?compact=1"):
            plain = self.client.get("/api/logbook" + suffix)
            self.assertNotIn("Content-Encoding", plain.headers)
            self.assertIn("Accept-Encoding", plain.headers["Vary"])
            for encoding in ("gzip", "br, gzip;q=0.8", "*;q=1"):
                compressed = self.client.get("/api/logbook" + suffix, headers={"Accept-Encoding": encoding})
                self.assertEqual(compressed.headers["Content-Encoding"], "gzip")
                self.assertEqual(gzip.decompress(compressed.data), plain.data)
                self.assertEqual(int(compressed.headers["Content-Length"]), len(compressed.data))
                self.assertIn("no-store", compressed.headers["Cache-Control"])
            for encoding in ("identity", "gzip;q=0", "br", "gzip;q=0,*;q=1",
                             "gzip;q=0.1,identity;q=1"):
                response = self.client.get("/api/logbook" + suffix, headers={"Accept-Encoding": encoding})
                self.assertNotIn("Content-Encoding", response.headers, encoding)
                self.assertEqual(response.json, plain.json)

    def seed_benchmark(self):
        self.con.execute("DELETE FROM flights")
        self.con.execute("UPDATE logs SET start_utc='2025-01-01T12:00:00Z'")
        for index in range(500):
            start = datetime(2025, 1, 1, 12) + timedelta(minutes=index * 10)
            assessment = json.dumps({"reasons": [f"Synthetic assessment metric {n}: {index + n}" for n in range(20)]})
            event = json.dumps({"synthetic_event": list(range(30))})
            for aircraft in (1, 2):
                self.con.execute(
                    "INSERT INTO flights(aircraft_id,idx,duration_s,takeoff_utc,landing_utc,"
                    "takeoff_lat,takeoff_lon,landed,verdict,has_gps_position,assessment_json,"
                    "takeoff_json,landing_json,pic_user_id,project_id)"
                    " VALUES(?,?,100,?,?,52,4,1,'flight',1,?,?,?,2,1)",
                    (aircraft, index, start.isoformat(timespec="milliseconds") + "Z",
                     (start + timedelta(seconds=100)).isoformat(timespec="milliseconds") + "Z", assessment, event, event))
        self.con.commit()

    def test_synthetic_payload_before_after_measurement(self):
        self.seed_benchmark()
        rows = self.client.get("/api/logbook?compact=1").json
        self.assertEqual(len(rows), 501)
        self.assertEqual(sum(len(r["sources"]) for r in rows), 1000)
        results = {}
        for label, url, headers in (
                ("full", "/api/logbook", {}),
                ("compact", "/api/logbook?compact=1", {}),
                ("gzip", "/api/logbook?compact=1", {"Accept-Encoding": "gzip"})):
            timings = []
            for _ in range(3):
                start = time.perf_counter()
                response = self.client.get(url, headers=headers)
                timings.append((time.perf_counter() - start) * 1000)
            results[label] = {"bytes": len(response.data), "response_ms": round(median(timings), 2)}
        self.assertLess(results["compact"]["bytes"], results["full"]["bytes"])
        self.assertLess(results["gzip"]["bytes"], results["compact"]["bytes"])
        print("Synthetic 500 merged flights + paper record (median of 3 requests):", results)
