import gzip
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from .test_historical import AppTests
from .test_simulation import PARSER

try:
    import h5py
    import numpy as np
    import matlab_export
except ImportError:  # h5py is an optional server dependency for this feature only
    h5py = None

PROTOCOL = """<configuration time_of_day="1">
<protocol><msg_class name="telemetry">
 <message name="ATTITUDE" id="6"><field name="phi" type="float" unit="rad" alt_unit="deg" alt_unit_coef="57.2958"/>
  <field name="psi" type="float"/><field name="theta" type="float"/></message>
 <message name="ACTUATORS" id="105"><field name="values" type="int16[]"/></message>
 <message name="INFO_MSG" id="1"><field name="msg" type="char[]"/></message>
 <message name="AUTOPILOT_VERSION" id="2"><field name="version" type="uint32"/><field name="desc" type="char[]"/></message>
 <message name="ROTORCRAFT_STATUS" id="3"><field name="ap_mode" type="uint8"/><field name="end" type="int8"/></message>
</msg_class></protocol>
<aircraft name="Quad1" ac_id="5"></aircraft><aircraft name="Quad2" ac_id="7"></aircraft>
</configuration>
"""
ROWS = ['0.5 5 AUTOPILOT_VERSION 51500 "v5.15_devel-167-g55bfedf"', "1.0 5 ATTITUDE 0.1 0.2 0.3",
        "1.0 7 ATTITUDE 1 2 3", "2.0 5 ACTUATORS 1,2,3", "3.0 5 ACTUATORS 4,5,6,7",
        '4.0 5 INFO_MSG "[PFC] pic: Pilot One"', "5.0 5 ATTITUDE 0.1 bad 0.3", "6.0 5 UNKNOWN_MSG 1 2",
        "7.0 5 ROTORCRAFT_STATUS 3 -1", "garbage line", "30.0 5 ATTITUDE 0.4 0.5 0.6",
        "4290.0 5 ATTITUDE 0.7 0.8 0.9", "5.0 5 ATTITUDE 1.0 1.1 1.2"]  # the last row wraps the onboard clock


def text(file, ref):
    ds = file[ref]
    return "" if "MATLAB_empty" in ds.attrs else "".join(map(chr, ds[()].ravel()))


def column(ds):
    return ds[()].ravel()


@unittest.skipUnless(h5py is not None and PARSER.exists(), "needs h5py and a built cpp parser")
class MatlabExporterTests(unittest.TestCase):
    def export(self, windows=None):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (folder / "x.data.gz").write_bytes(gzip.compress(("\n".join(ROWS) + "\n").encode()))
        (folder / "x.log.gz").write_bytes(gzip.compress(PROTOCOL.encode()))
        (folder / "work").mkdir()
        warnings = matlab_export.export(str(PARSER), folder / "x.data.gz", folder / "x.log.gz", folder / "out.mat",
                                        dict(name="x.data", sha256="abc", log_type="gcs", size_bytes=1000,
                                             scope="log" if windows is None else "flights"),
                                        folder / "work", windows)
        file = self.enterContext(h5py.File(folder / "out.mat", "r"))
        return file, warnings, (folder / "out.mat").read_bytes()[:128]

    def element(self, file, group, field, i):
        """Field ``field`` of element ``i`` of a struct array group (a column of references)."""
        return file[group[field][i, 0]]

    def test_whole_log_layout_values_and_raw_rows(self):
        file, warnings, header = self.export()
        self.assertTrue(header.startswith(b"MATLAB 7.3 MAT-file"))
        self.assertEqual(header[124:128], b"\x00\x02IM")
        s = file["s"]
        self.assertEqual(s.attrs["MATLAB_class"], b"struct")
        aircraft = s["aircrafts"]
        self.assertEqual(aircraft["AC_ID"].shape, (2, 1))
        self.assertEqual([self.element(file, aircraft, "AC_ID", i)[0, 0] for i in range(2)], [5, 7])
        self.assertEqual(text(file, aircraft["name"][1, 0]), "Quad2")
        self.assertEqual(self.element(file, aircraft, "version", 0)[0, 0], 51500)
        self.assertEqual(text(file, aircraft["version_desc"][0, 0]), "v5.15_devel-167-g55bfedf")
        data = self.element(file, aircraft, "data", 0)
        att = data["ATTITUDE"]
        np.testing.assert_allclose(column(att["timestamp"]), [1, 30, 4290, 4294.967296 + 5])
        np.testing.assert_allclose(column(att["phi_alt"]), np.array([0.1, 0.4, 0.7, 1.0]) * 57.2958)
        self.assertEqual(att["phi"].attrs["MATLAB_class"], b"double")
        acts = data["ACTUATORS"]["values"][()]  # MATLAB 2×4, stored transposed
        np.testing.assert_array_equal(acts[:, 0], [1, 2, 3, np.nan])
        np.testing.assert_array_equal(acts[:, 1], [4, 5, 6, 7])
        status = data["ROTORCRAFT_STATUS"]
        self.assertEqual((status["ap_mode"].dtype, status["end_"][0, 0]), (np.uint32, -1))
        self.assertEqual(text(file, data["INFO_MSG"]["msg"][0, 0]), "[PFC] pic: Pilot One")
        raw = self.element(file, aircraft, "raw", 0)
        self.assertEqual(text(file, raw["lines"].ref), "5.0 5 ATTITUDE 0.1 bad 0.3\n6.0 5 UNKNOWN_MSG 1 2")
        self.assertEqual(text(file, s["raw"]["lines"].ref), "garbage line")
        self.assertEqual(len(warnings), 3)
        self.assertEqual(text(file, s["messages"]["ATTITUDE"]["units"][0, 0]), "rad")

    def test_flight_windows_are_relative_to_takeoff_and_keep_every_aircraft(self):
        windows = [dict(index=1, ac_id=5, takeoff_s=2.0, landing_s=6.0, takeoff_utc="2026-10-21T09:05:12Z",
                        landing_utc=None),
                   dict(index=2, ac_id=5, takeoff_s=4290.0, landing_s=None, takeoff_utc=None, landing_utc=None)]
        file, _, _ = self.export(windows)
        flights = file["s"]["flights"]
        self.assertEqual(flights["index"].shape, (2, 1))
        first = self.element(file, flights, "aircrafts", 0)
        self.assertEqual([file[r][0, 0] for r in first["AC_ID"][:, 0]], [5, 7])
        data = file[first["data"][0, 0]]
        np.testing.assert_allclose(column(data["ATTITUDE"]["timestamp"]), [-1.0])
        np.testing.assert_allclose(column(data["AUTOPILOT_VERSION"]["timestamp"]), [-1.5])  # inside the 10 s margin
        np.testing.assert_allclose(column(self.element(file, flights, "window_s", 0)), [-10, 14])
        second = file[self.element(file, flights, "aircrafts", 1)["data"][0, 0]]
        np.testing.assert_allclose(column(second["ATTITUDE"]["timestamp"]), [0.0, 4294.967296 + 5 - 4290.0])
        self.assertEqual(self.element(file, flights, "time_offset_s", 1)[0, 0], 4290.0)

    def test_missing_protocol_is_refused(self):
        with self.assertRaises(matlab_export.ExportError):
            matlab_export.parse_protocol("<configuration></configuration>")


class FakeJobs:
    def __init__(self):
        self.jobs = {}

    def submit(self, job_id, log_id, filename, task):
        self.jobs[job_id] = dict(id=job_id, log_id=log_id, filename=filename, status="queued", progress=0.0,
                                 error=None, task=task)
        return self.jobs[job_id]

    def status(self, job_id):
        return self.jobs.get(job_id)

    def path(self, job_id):
        return Path(__file__)


@unittest.skipUnless(h5py is not None, "needs h5py")
class MatlabExportRouteTests(AppTests):
    def setUp(self):
        super().setUp()
        self.jobs = FakeJobs()
        original = self.ns["export_jobs"]
        self.ns["export_jobs"] = lambda: self.jobs
        self.addCleanup(self.ns.__setitem__, "export_jobs", original)
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,log_path,status,uploaded_at,uploaded_by,project_id,log_type,"
            "size_bytes) VALUES (1,'26_10_21__09_04_50_SD.data','sha','a.data.gz','a.log.gz','done','now',2,1,"
            "'onboard_sd',100), (2,'px4.ulg','sha2','b.ulg.gz',NULL,'done','now',2,1,'px4_ulog',100)")
        self.con.execute("INSERT INTO aircraft(id,log_id,drone_id,name,ac_id) VALUES (1,1,1,'Drone',5)")
        self.con.executemany(
            "INSERT INTO flights(id,aircraft_id,idx,takeoff_time_s,landing_time_s,verdict) VALUES (?,1,?,?,?,?)",
            [(10, 0, 100.0, 200.0, "flight"), (11, 1, 300.0, 310.0, "ground"), (12, 2, 400.0, None, "uncertain")])
        self.con.commit()

    def ask(self, log_id=1, **body):
        return self.client.post(f"/api/logs/{log_id}/matlab", json=body)

    def test_scopes_windows_and_reuse(self):
        whole, flights, one = self.ask(scope="log"), self.ask(scope="flights"), self.ask(scope="flight", flight_id=10)
        self.assertEqual({whole.status_code, flights.status_code, one.status_code}, {202})
        self.assertEqual(len({whole.json["id"], flights.json["id"], one.json["id"]}), 3)
        self.assertEqual(self.ask(scope="flights").json["id"], flights.json["id"])  # identical request, same job
        self.assertEqual(one.json["filename"], "26_10_21__09_04_50_SD_flight_10.mat")
        self.assertEqual(whole.json["filename"], "26_10_21__09_04_50_SD_whole_log.mat")
        with patch.object(matlab_export, "export", return_value=[]) as export:
            self.jobs.jobs[flights.json["id"]]["task"]("out.mat", "work", lambda f: None)
        windows = export.call_args.args[6]
        self.assertEqual([[w["index"], w["takeoff_s"], w["landing_s"]] for w in windows],
                         [[1, 100.0, 200.0], [2, 400.0, None]])  # the ground record is left out
        self.assertEqual(self.client.get(f"/api/matlab-exports/{one.json['id']}").json["status"], "queued")

    def test_refusals_and_permissions(self):
        self.assertEqual(self.ask(scope="everything").status_code, 400)
        self.assertEqual(self.ask(scope="flight", flight_id=99).status_code, 404)
        self.assertEqual(self.ask(log_id=2, scope="log").status_code, 409)
        job = self.ask(scope="log").json["id"]
        self.assertEqual(self.client.get(f"/api/matlab-exports/{job}/download").status_code, 409)  # not ready
        self.sign_in(4)  # not in the log's project and not its uploader
        self.assertEqual(self.ask(scope="log").status_code, 404)
        self.assertEqual(self.client.get(f"/api/matlab-exports/{job}").status_code, 404)
        self.assertEqual(self.client.get("/api/matlab-exports/not-a-job").status_code, 404)
        original = self.ns["user_for_key"]
        self.addCleanup(self.ns.__setitem__, "user_for_key", original)
        self.ns["user_for_key"] = lambda token: self.con.execute("SELECT * FROM users WHERE id=1").fetchone()
        response = self.client.post("/api/logs/1/matlab", json=dict(scope="log"),
                                    headers={"Authorization": "Bearer test-only"})
        self.assertEqual(response.status_code, 403)  # API keys cannot export


@unittest.skipUnless(h5py is not None, "needs h5py")
class MatlabJobTests(unittest.TestCase):
    def jobs(self, **kw):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        return matlab_export.Jobs(folder / "exports", folder / "tmp", **kw), folder / "exports"

    def wait(self, jobs, job_id):
        for _ in range(200):
            job = jobs.status(job_id)
            if job and job["status"] in ("ready", "failed"):
                return job
            time.sleep(0.01)
        self.fail("export did not finish")

    def test_ready_files_are_published_atomically_and_reused(self):
        jobs, folder = self.jobs()
        calls = []

        def task(out, work, progress):
            calls.append(out)
            self.assertFalse((folder / ("a" * 32 + ".mat")).exists())  # not published while running
            Path(out).write_bytes(b"mat")
            return ["warning"]
        job_id = "a" * 32
        jobs.submit(job_id, 1, "x.mat", task)
        self.assertEqual(self.wait(jobs, job_id)["status"], "ready")
        self.assertEqual(jobs.submit(job_id, 1, "x.mat", task)["status"], "ready")
        self.assertEqual(len(calls), 1)
        self.assertEqual(jobs.path(job_id).read_bytes(), b"mat")
        restarted = matlab_export.Jobs(folder, folder.parent / "tmp")  # ready exports survive a restart
        self.assertEqual(restarted.status(job_id)["filename"], "x.mat")

    def test_failures_are_reported_and_never_published(self):
        jobs, folder = self.jobs()

        def task(out, work, progress):
            Path(out).write_bytes(b"partial")
            raise matlab_export.ExportError("No protocol")
        jobs.submit("b" * 32, 1, "x.mat", task)
        job = self.wait(jobs, "b" * 32)
        self.assertEqual((job["status"], job["error"]), ("failed", "No protocol"))
        self.assertEqual(list(folder.iterdir()), [])

    def test_expiry_and_size_budget_remove_the_oldest(self):
        jobs, folder = self.jobs(max_bytes=10)
        for i, age in enumerate((3, 2, 1)):
            job_id = str(i) * 32
            (folder / f"{job_id}.mat").write_bytes(b"123456")
            (folder / f"{job_id}.json").write_text('{"log_id": 1, "filename": "x.mat"}')
            stamp = time.time() - age * 3600
            os.utime(folder / f"{job_id}.mat", (stamp, stamp))
        old = "9" * 32
        (folder / f"{old}.mat").write_bytes(b"1")
        (folder / f"{old}.json").write_text('{"log_id": 1, "filename": "x.mat"}')
        stamp = time.time() - matlab_export.KEEP_S - 60
        os.utime(folder / f"{old}.mat", (stamp, stamp))
        jobs.cleanup()
        self.assertEqual(sorted(p.name for p in folder.glob("*.mat")), ["2" * 32 + ".mat"])
        self.assertEqual(sorted(p.name for p in folder.glob("*.json")), ["2" * 32 + ".json"])
