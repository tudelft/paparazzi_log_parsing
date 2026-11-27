import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from .test_historical import AppTests


class RetentionTests(AppTests):
    """Logs with a real flight and paper records are kept for LOGDB_RETENTION_YEARS (#313)."""

    def setUp(self):
        super().setUp()
        self.recent = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT10:00:00.000Z")

    def add_log(self, log_id, takeoff, verdict="flight", simulation=None, start=None):
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by,project_id,log_type,start_utc)"
            " VALUES(?,?,?,'t.data','done','2026-01-02T00:00:00Z',2,1,'onboard_sd',?)",
            (log_id, f"log{log_id}.data", f"sha{log_id}", start or takeoff))
        self.con.execute("INSERT INTO aircraft(id,log_id,drone_id,name,ac_id,simulation) VALUES(?,?,1,'Drone',7,?)",
                         (log_id, log_id, json.dumps(simulation) if simulation else None))
        self.con.execute("INSERT INTO flights(aircraft_id,idx,duration_s,takeoff_utc,verdict,project_id,has_gps_position)"
                         " VALUES(?,0,100,?,?,1,0)", (log_id, takeoff, verdict))
        self.con.commit()

    def delete(self, log_id, **body):
        return self.client.delete(f"/api/logs/{log_id}", json=body or None)

    def test_a_recent_log_is_kept_and_an_admin_deletes_it_only_with_a_reason(self):
        self.add_log(1, self.recent)
        self.sign_in(2)  # full access to the project
        response = self.delete(1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json["kept_until"], self.client.get("/api/logs/1").json["kept_until"])
        self.sign_in(1)
        self.assertEqual(self.delete(1).status_code, 400)
        self.assertEqual(self.delete(1, reason="Uploaded to the wrong server").status_code, 200)
        row = self.con.execute("SELECT * FROM deleted_records").fetchone()
        self.assertEqual((row["kind"], row["record_id"], row["reason"], row["deleted_by_name"]),
                         ("log", 1, "Uploaded to the wrong server", "Admin"))
        self.assertEqual(json.loads(row["snapshot_json"])["flights"][0]["takeoff_utc"], self.recent)
        for sql in ("UPDATE deleted_records SET reason='x'", "DELETE FROM deleted_records"):
            with self.assertRaises(sqlite3.DatabaseError):
                self.con.execute(sql)
        records = self.client.get("/api/admin/deleted-records").json["records"]
        self.assertEqual([r["label"] for r in records], ["log1.data"])

    def test_old_logs_ground_runs_and_simulations_may_be_deleted(self):
        self.add_log(1, "2010-05-01T10:00:00.000Z")
        self.add_log(2, self.recent, verdict="ground")
        self.add_log(3, self.recent, simulation={"detected": True, "evidence": ["telemetry:NPS_WIND"]})
        self.sign_in(2)
        for log_id in (1, 2, 3):
            self.assertEqual(self.delete(log_id).status_code, 200, log_id)
        self.assertIsNone(self.con.execute("SELECT 1 FROM deleted_records").fetchone())

    def test_an_undated_log_counts_from_its_upload(self):
        self.add_log(1, "1980-01-06T00:10:00.000Z")  # file clock without a calendar date
        self.assertEqual(self.ns["log_kept_until"](self.con, 1), "2029-01-02")
        self.con.execute("UPDATE logs SET date_override='2010-05-01'")
        self.assertEqual(self.ns["log_kept_until"](self.con, 1), "2013-05-01")

    def test_paper_records_and_the_setting(self):
        recent = self.create(date=self.recent[:10]).json["id"]
        old = self.create().json["id"]  # 2010
        self.sign_in(2)
        self.assertEqual(self.client.get(f"/api/historical/{recent}").json["record"]["kept_until"][:4],
                         str(int(self.recent[:4]) + 3))
        self.assertEqual(self.client.delete(f"/api/historical/{recent}").status_code, 409)
        self.assertEqual(self.client.delete(f"/api/historical/{old}").status_code, 200)
        self.sign_in(1)
        self.assertEqual(self.client.delete(f"/api/historical/{recent}", json={"reason": "Duplicate of a log"}).status_code, 200)
        self.assertEqual(self.con.execute("SELECT kind FROM deleted_records").fetchone()[0], "paper")
        with patch.dict(os.environ, LOGDB_RETENTION_YEARS="0"):
            self.add_log(1, self.recent)
            self.sign_in(2)
            self.assertEqual(self.delete(1).status_code, 200)
