import gzip
import json
import os

from . import test_historical


GCS_LOG = """<!-- logged with runtime paparazzi_version v7.1_unstable-330-gee550fc3ba -->
<!-- logged with build paparazzi_version v7.1_unstable-329-ga9447b3fce -->
<configuration time_of_day="1" data_file="x.data"></configuration>
"""


class GcsAutopilotFallbackTests(test_historical.AppTests):
    def write_log(self, rel="test.log.gz", text=GCS_LOG):
        path = self.ns["STORAGE_DIR"] / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wb") as handle:
            handle.write(text.encode())
        self.addCleanup(path.unlink, missing_ok=True)
        return rel

    def insert_log(self, versions=None, log_path="test.log.gz"):
        cur = self.con.execute(
            "INSERT INTO logs(name,sha256,data_path,log_path,status,uploaded_at,uploaded_by,project_id,log_type)"
            " VALUES ('imav.data',?,'test.data.gz',?,'done','now',2,1,'gcs')",
            (os.urandom(16).hex(), log_path))
        log_id = cur.lastrowid
        aircraft = self.con.execute(
            "INSERT INTO aircraft(log_id,ac_id,name,autopilot_versions) VALUES (?,?,?,?)",
            (log_id, 129, "Adam", json.dumps(versions or []))).lastrowid
        fid = self.con.execute(
            "INSERT INTO flights(aircraft_id,idx,duration_s,takeoff_utc,landing_utc,verdict,project_id,has_gps_position)"
            " VALUES (?,1,60,'2010-01-01T12:00:00.000Z','2010-01-01T12:01:00.000Z','flight',1,0)",
            (aircraft,)).lastrowid
        self.con.commit()
        return log_id, fid

    def test_prefers_runtime_comment_and_encodes_like_telemetry(self):
        rel = self.write_log()
        self.assertEqual(self.ns["gcs_paparazzi_version"](rel), "v7.1_unstable-330-gee550fc3ba")
        versions, from_gcs = self.ns["with_gcs_autopilot_fallback"]([], rel)
        self.assertTrue(from_gcs)
        self.assertEqual(versions, ["70100 v7.1_unstable-330-gee550fc3ba"])

    def test_keeps_aircraft_version(self):
        rel = self.write_log()
        sent = ["70100 v7.1_unstable-321-g094588e3f0-dirty"]
        versions, from_gcs = self.ns["with_gcs_autopilot_fallback"](sent, rel)
        self.assertFalse(from_gcs)
        self.assertEqual(versions, sent)

    def test_flight_and_log_api_use_gcs_header_when_missing(self):
        self.write_log()
        log_id, flight_id = self.insert_log()
        detail = self.client.get(f"/api/flights/{flight_id}/detail").json
        self.assertTrue(detail["autopilot_versions_from_gcs"])
        self.assertEqual(detail["autopilot_versions"], ["70100 v7.1_unstable-330-gee550fc3ba"])
        aircraft = self.client.get(f"/api/logs/{log_id}").json["aircraft"][0]
        self.assertTrue(aircraft["autopilot_versions_from_gcs"])
        self.assertEqual(aircraft["autopilot_versions"], ["70100 v7.1_unstable-330-gee550fc3ba"])

    def test_flight_api_does_not_replace_recorded_version(self):
        self.write_log()
        sent = ["70100 v7.1_unstable-321-g094588e3f0-dirty"]
        _, flight_id = self.insert_log(versions=sent)
        detail = self.client.get(f"/api/flights/{flight_id}/detail").json
        self.assertFalse(detail["autopilot_versions_from_gcs"])
        self.assertEqual(detail["autopilot_versions"], sent)

    def test_no_header_leaves_version_empty(self):
        self.write_log(text="<configuration></configuration>\n")
        _, flight_id = self.insert_log()
        detail = self.client.get(f"/api/flights/{flight_id}/detail").json
        self.assertFalse(detail["autopilot_versions_from_gcs"])
        self.assertEqual(detail["autopilot_versions"], [])
