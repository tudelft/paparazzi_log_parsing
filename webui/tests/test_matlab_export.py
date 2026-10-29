import gzip
import io
import os
import sys
import tempfile
import time
import unittest
import zipfile
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
<protocol>
<msg_class name="telemetry">
 <message name="ATTITUDE" id="6"><field name="phi" type="float" unit="rad" alt_unit="deg" alt_unit_coef="57.2958"/>
  <field name="psi" type="float"/><field name="theta" type="float"/></message>
 <message name="ACTUATORS" id="105"><field name="values" type="int16[]"/></message>
 <message name="INFO_MSG" id="1"><field name="msg" type="char[]"/></message>
 <message name="AUTOPILOT_VERSION" id="2"><field name="version" type="uint32"/><field name="desc" type="char[]"/></message>
 <message name="ROTORCRAFT_STATUS" id="3"><field name="ap_mode" type="uint8" values="KILL|FAILSAFE|HOME|RATE"/>
  <field name="end" type="int8"/></message>
 <message name="DL_VALUE" id="31"><field name="index" type="uint8"/><field name="value" type="float"/></message>
 <message name="ROTORCRAFT_NAV_STATUS" id="4"><field name="cur_block" type="uint8"/><field name="cur_stage" type="uint8"/></message>
</msg_class>
</protocol>
<aircraft name="Quad1" ac_id="5">
<airframe name="quad">
  <servos>
    <servo name="M1" no="0"/>
    <servo name="M2" no="1"/>
    <servo name="M3" no="2"/>
    <servo name="M4" no="3"/>
  </servos>
</airframe>
<flight_plan name="plan">
<blocks>
<block name="Wait">
  <call fun="x"/>
</block>
<block name="Takeoff">
  <go wp="A"/>
  <stay wp="B"/>
</block>
</blocks>
</flight_plan>
</aircraft>
<aircraft name="Quad2" ac_id="7"></aircraft>
</configuration>
"""
ROWS = ['0.5 5 AUTOPILOT_VERSION 51500 "v5.15_devel-167-g55bfedf"', "1.0 5 ATTITUDE 0.1 0.2 0.3",
        "1.0 7 ATTITUDE 1 2 3", "2.0 5 ACTUATORS 1,2,3", "3.0 5 ACTUATORS 4,5,6,7",
        '4.0 5 INFO_MSG "[PFC] pic: Pilot One"', "5.0 5 ATTITUDE 0.1 bad 0.3", "6.0 5 UNKNOWN_MSG 1 2",
        "7.0 5 ROTORCRAFT_STATUS 3 -1", "garbage line", "30.0 5 ATTITUDE 0.4 0.5 0.6",
        "4.5 5 DL_VALUE 0 1.0", "40.0 5 DL_VALUE 0 2.0", "8.0 5 ROTORCRAFT_NAV_STATUS 1 1", "9.0 5 ROTORCRAFT_NAV_STATUS 0 0", "9.5 5 ROTORCRAFT_NAV_STATUS 7 0",
        "4290.0 5 ATTITUDE 0.7 0.8 0.9", "5.0 5 ATTITUDE 1.0 1.1 1.2"]  # the last row wraps the onboard clock
EXTRAS = dict(time0_utc="2026-10-21T09:00:00.000Z", time0_posix=1792573200.0, aircraft={5: dict(
    pic="Pilot A", pac=None, gcs="Operator C", goal="Hover test", simulation=dict(detected=False, evidence=[]),
    settings=[dict(index=0, var="gain", shortname="Gain", unit=None, parent="Control")],
    setting_changes=[dict(time_s=5.0, idx=0, var="gain", from_value=1.0, to_value=2.0, in_flight=1)],
    checklist=[dict(time_s=0.8, text="pic: Pilot A", checklist_label="Enter the PIC", checklist_value="Pilot A"),
               dict(time_s=0.9, text="props")],
    config=dict(xml=dict(airframe="<airframe/>"), flight_plan=dict(
        name="plan", lat0=52.0, lon0=4.0, waypoints=[dict(index=0, name="HOME", lat=52.0, lon=4.0, alt_m=10.0)],
        blocks=[dict(index=0, name="Wait")])))})


def text(file, ref):
    ds = file[ref]
    return "" if "MATLAB_empty" in ds.attrs else "".join(map(chr, ds[()].ravel()))


def column(ds):
    return ds[()].ravel()


def chars(ds):
    """Rows of a MATLAB N×L char matrix (stored transposed), trailing padding removed."""
    return ["".join(map(chr, row)).rstrip() for row in ds[()].T]


@unittest.skipUnless(h5py is not None and PARSER.exists(), "needs h5py and a built cpp parser")
class MatlabExporterTests(unittest.TestCase):
    def export(self, windows=None, flights=()):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (folder / "x.data.gz").write_bytes(gzip.compress(("\n".join(ROWS) + "\n").encode()))
        (folder / "x.log.gz").write_bytes(gzip.compress(PROTOCOL.encode()))
        (folder / "work").mkdir()
        warnings = matlab_export.export(str(PARSER), folder / "x.data.gz", folder / "x.log.gz", folder / "out.mat",
                                        dict(name="x.data", sha256="abc", log_type="gcs", size_bytes=1000,
                                             scope="log" if windows is None else "flights"),
                                        folder / "work", windows, extras=dict(EXTRAS, flights=list(flights)))
        file = self.enterContext(h5py.File(folder / "out.mat", "r"))
        return file, warnings, (folder / "out.mat").read_bytes()[:128]

    def element(self, file, group, field, i):
        """Field ``field`` of element ``i`` of a struct array group (a column of references)."""
        return file[group[field][i, 0]]

    def test_whole_log_layout_values_and_unparsed_rows(self):
        file, warnings, header = self.export(flights=[dict(index=3, ac_id=5, takeoff_s=2.0, landing_s=6.0)])
        self.assertTrue(header.startswith(b"MATLAB 7.3 MAT-file"))
        self.assertEqual(header[124:128], b"\x00\x02IM")
        s = file["s"]
        self.assertEqual(s.attrs["MATLAB_class"], b"struct")
        self.assertEqual(text(file, s["info"]["exporter_version"].ref), "5")
        aircraft = s["aircraft"]
        self.assertEqual(aircraft["id"].shape, (2, 1))
        self.assertEqual([self.element(file, aircraft, "id", i)[0, 0] for i in range(2)], [5, 7])
        self.assertEqual(text(file, aircraft["name"][1, 0]), "Quad2")
        version = self.element(file, aircraft, "version", 0)
        self.assertEqual(version["number"][0, 0], 51500)
        self.assertEqual(text(file, version["desc"].ref), "v5.15_devel-167-g55bfedf")
        data = self.element(file, aircraft, "data", 0)
        att = data["ATTITUDE"]
        np.testing.assert_allclose(column(att["t"]), [1, 30, 4290, 4294.967296 + 5])
        # converted unit under the field name; no raw copy (raw = value / coef)
        np.testing.assert_allclose(column(att["phi"]), np.array([0.1, 0.4, 0.7, 1.0]) * 57.2958)
        self.assertEqual(sorted(att), ["phi", "psi", "t", "theta"])
        msg = s["messages"]["ATTITUDE"]
        self.assertEqual((text(file, msg["units"][0, 0]), text(file, msg["raw_units"][0, 0])), ("deg", "rad"))
        self.assertEqual(column(msg["coef"])[0], 57.2958)
        acts = data["ACTUATORS"]  # named columns when the airframe lists as many servos, else values (N×k)
        np.testing.assert_array_equal(column(acts["M4"]), [np.nan, 7])
        np.testing.assert_array_equal(column(acts["M1"]), [1, 4])
        self.assertNotIn("values", acts)
        status = data["ROTORCRAFT_STATUS"]
        self.assertEqual((status["ap_mode"].dtype, status["end_"][0, 0]), (np.uint32, -1))
        self.assertEqual(sorted(status), ["ap_mode", "end_", "t"])  # enum names only in s.messages
        self.assertEqual(text(file, s["messages"]["ROTORCRAFT_STATUS"]["enums"]["ap_mode"][0, 1]), "FAILSAFE")
        self.assertEqual(sorted(data["ROTORCRAFT_NAV_STATUS"]), ["cur_block", "cur_stage", "t"])
        config = file[aircraft["config"][0, 0]]
        plan = config["flight_plan"]
        self.assertEqual([text(file, r) for r in plan["blocks"][0, :]], ["Wait", "Takeoff"])
        stages = plan["stages"]
        self.assertEqual([text(file, r) for r in stages["name"][0, :]], ["call", "go A", "stay B"])
        np.testing.assert_array_equal(column(stages["block"]), [0, 1, 1])
        self.assertEqual([text(file, r) for r in config["actuators"][0, :]], ["M1", "M2", "M3", "M4"])
        self.assertEqual(text(file, config["xml"]["airframe"].ref), "<airframe/>")
        self.assertEqual(plan["waypoints"]["HOME"]["alt"][0, 0], 10.0)
        g = file[aircraft["crew"][0, 0]]
        self.assertEqual((text(file, g["pic"].ref), text(file, g["pac"].ref)), ("Pilot A", ""))
        settings = file[aircraft["settings"][0, 0]]
        np.testing.assert_allclose(column(settings["values"]["gain"]["value"]), [1.0, 2.0])  # every report
        np.testing.assert_allclose(column(settings["values"]["gain"]["t"]), [4.5, 40.0])
        self.assertEqual(text(file, settings["list"]["parent"][0, 0]), "Control")
        changes = settings["changes"]
        self.assertEqual((text(file, changes["name"][0, 0]), column(changes["t"])[0]), ("gain", 5.0))
        self.assertEqual(column(changes["to"])[0], 2.0)
        checklist = file[aircraft["checklist"][0, 0]]
        self.assertEqual([text(file, r) for r in checklist["items"]["item"][0, :]], ["Enter the PIC", "props"])
        self.assertEqual([text(file, r) for r in checklist["items"]["answer"][0, :]], ["Pilot A", ""])
        self.assertEqual(text(file, checklist["answers"]["pic"].ref), "Pilot A")
        self.assertEqual(text(file, s["log"]["time0_utc"].ref), "2026-10-21T09:00:00.000Z")
        listed = self.element(file, aircraft, "flights", 0)  # flight summaries without data in a whole-log export
        self.assertEqual(file[listed["index"][0, 0]][0, 0], 3.0)
        self.assertEqual(file[listed["time"][0, 0]]["start_s"][0, 0], -8.0)
        self.assertNotIn("data", listed)
        self.assertEqual(text(file, data["INFO_MSG"]["msg"][0, 0]), "[PFC] pic: Pilot One")
        lines = self.element(file, aircraft, "unparsed", 0)["lines"]
        self.assertEqual([text(file, r) for r in lines[0, :]], ["5.0 5 ATTITUDE 0.1 bad 0.3", "6.0 5 UNKNOWN_MSG 1 2"])
        self.assertEqual(text(file, s["log"]["unparsed"]["lines"][0, 0]), "garbage line")
        self.assertEqual(len(warnings), 3)
        self.assertEqual(s["log"]["warnings"].shape, (1, 3))

    def test_firmware_build_identity_and_a_missing_trailing_array(self):
        protocol = PROTOCOL.replace('<field name="desc" type="char[]"/></message>', '<field name="desc" type="char[]"/>'
                                    '<field name="sha1" type="uint8[20]"/><field name="build_time" type="uint32"/>'
                                    '<field name="mcu_uid" type="uint8[]"/></message>')
        sha1 = ",".join(str(b) for b in range(171, 191))
        rows = [f'0.5 5 AUTOPILOT_VERSION 51500 "v5.15_devel-167-g55bfedf" {sha1} 1793000000'] + ROWS[1:]
        with patch.object(sys.modules[__name__], "PROTOCOL", protocol), \
                patch.object(sys.modules[__name__], "ROWS", rows):
            file, warnings, _ = self.export()
        self.assertFalse([w for w in warnings if "AUTOPILOT_VERSION" in w])  # no board ID: empty, not rejected
        version = self.element(file, file["s"]["aircraft"], "version", 0)
        self.assertEqual(text(file, version["sha1"].ref), "".join(f"{b:02x}" for b in range(171, 191)))
        self.assertEqual(text(file, version["build_time_utc"].ref), "2026-10-26T07:33:20Z")
        self.assertEqual(text(file, version["mcu_uid"].ref), "")
        self.assertEqual(text(file, version["desc"].ref), "v5.15_devel-167-g55bfedf")
        data = self.element(file, file["s"]["aircraft"], "data", 0)
        np.testing.assert_array_equal(column(data["AUTOPILOT_VERSION"]["sha1"]), list(range(171, 191)))

    def test_flights_belong_to_their_aircraft_and_count_from_takeoff(self):
        windows = [dict(index=1, ac_id=5, takeoff_s=2.0, landing_s=6.0, takeoff_utc="2026-10-21T09:05:12Z",
                        landing_utc=None, assessment=dict(verdict="flight", reasons=["climb"])),
                   dict(index=2, ac_id=5, takeoff_s=4290.0, landing_s=None, takeoff_utc=None, landing_utc=None)]
        file, _, _ = self.export(windows)
        aircraft = file["s"]["aircraft"]
        self.assertEqual(aircraft["id"].shape, (1, 1))  # only aircraft with an exported flight
        flights = self.element(file, aircraft, "flights", 0)
        self.assertEqual(flights["index"].shape, (2, 1))
        data = file[flights["data"][0, 0]]
        np.testing.assert_allclose(column(data["ATTITUDE"]["t"]), [-1.0])  # aircraft 7's row is not here
        np.testing.assert_allclose(column(data["AUTOPILOT_VERSION"]["t"]), [-1.5])  # inside the 10 s margin
        time_ = self.element(file, flights, "time", 0)
        np.testing.assert_allclose(column(time_["window_s"]), [-10, 14])
        self.assertEqual(text(file, time_["takeoff_utc"].ref), "2026-10-21T09:05:12Z")
        summary = self.element(file, flights, "summary", 0)
        self.assertEqual((text(file, summary["verdict"].ref), text(file, summary["reasons"][0, 0])), ("flight", "climb"))
        second = file[flights["data"][1, 0]]
        np.testing.assert_allclose(column(second["ATTITUDE"]["t"]), [0.0, 4294.967296 + 5 - 4290.0])
        self.assertEqual(self.element(file, flights, "time", 1)["offset_s"][0, 0], 4290.0)
        settings = file[aircraft["settings"][0, 0]]
        np.testing.assert_allclose(column(settings["values"]["gain"]["t"]), [4.5, 40.0])  # whole log

    def test_zip_stream_holds_the_files(self):
        import io
        import zipfile
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (folder / "a.mat").write_bytes(b"first" * 1000)
        (folder / "b.mat").write_bytes(b"second")
        data = b"".join(matlab_export.zip_stream([(folder / "a.mat", "x/a.mat"), (folder / "b.mat", "b.mat")]))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(archive.namelist(), ["x/a.mat", "b.mat"])
            self.assertEqual(archive.read("x/a.mat"), b"first" * 1000)

    def test_local_conversion_of_plain_and_gzip_logs(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (folder / "a.data").write_text("\n".join(ROWS) + "\n")
        (folder / "a.log").write_text(PROTOCOL)
        (folder / "b.data.gz").write_bytes(gzip.compress(("\n".join(ROWS) + "\n").encode()))
        (folder / "b.log.gz").write_bytes(gzip.compress(PROTOCOL.encode()))
        with patch("builtins.print"):
            code = matlab_export.main([str(folder / "a.data"), str(folder / "b.data.gz"), "-o", str(folder / "out"),
                                       "--parser", str(PARSER)])
        self.assertEqual(code, 0)
        for name in ("a_whole_log.mat", "b_whole_log.mat"):
            with h5py.File(folder / "out" / name, "r") as file:
                self.assertEqual(text(file, file["s"]["info"]["exporter_version"].ref), "5")
                self.assertEqual(file["s"]["aircraft"]["id"].shape, (2, 1))

    def test_parsed_inputs_follow_the_web_export(self):
        doc = dict(log=dict(start_utc="2026-10-21T09:00:00Z"), aircraft=[dict(
            ac_id=5, mission=dict(pic="Pilot A"), preflight_checks=[dict(text="pic: Pilot A")],
            settings_changes=dict(timeline=[dict(time_s=5.0, index=0, var="gain", **{"from": 1.0, "to": 2.0})]),
            flights=[dict(flight=1, duration_s=60.0, landed=True, assessment=dict(verdict="flight"),
                          takeoff=dict(time_s=100.0, utc="2026-10-21T09:01:40Z", location=dict(lat=52.0, lon=4.0)),
                          landing=dict(time_s=160.0)),
                     dict(flight=0, takeoff=dict(time_s=10.0), landing=dict(time_s=20.0))])])
        flights, extras = matlab_export.parsed_inputs(doc)
        self.assertEqual([(f["index"], f["takeoff_s"]) for f in flights], [(1, 10.0), (2, 100.0)])
        self.assertEqual(flights[1]["summary"]["takeoff_lat"], 52.0)
        self.assertEqual(flights[1]["takeoff_posix"], 1792573300.0)
        self.assertEqual(extras["aircraft"][5]["pic"], "Pilot A")
        self.assertEqual(extras["aircraft"][5]["setting_changes"][0]["to_value"], 2.0)

    def test_a_field_named_t_does_not_hide_the_time(self):
        messages, _ = matlab_export.parse_protocol(
            '<protocol>\n<msg_class name="telemetry"><message name="M" id="1"><field name="t" type="float"/>'
            '<field name="end" type="float"/></message></msg_class>\n</protocol>')
        self.assertEqual([f["key"] for f in messages["M"]["fields"]], ["t_2", "end_"])

    def test_missing_protocol_is_refused(self):
        with self.assertRaises(matlab_export.ExportError):
            matlab_export.parse_protocol("<configuration></configuration>")


class FakeJobs:
    """Jobs without workers: submitted tasks wait in ``jobs`` until a test runs them."""

    def __init__(self, folder):
        self.folder, self.jobs = Path(folder), {}
        self.folder.mkdir()

    def submit(self, job_id, log_id, filename, task, limit=None):
        self.jobs.setdefault(job_id, dict(id=job_id, log_id=log_id, filename=filename, status="queued",
                                          progress=0.0, error=None, task=task))
        return dict(self.jobs[job_id])

    def status(self, job_id):
        return self.jobs.get(job_id)

    def path(self, job_id):
        return self.folder / f"{job_id}.mat"

    def finish(self, job_id, content):
        self.path(job_id).write_bytes(content)
        self.jobs[job_id].update(status="ready", progress=1.0, size_bytes=len(content))

    def bundle(self, *args):
        return matlab_export.Jobs.bundle(self, *args)

    def bundle_status(self, bundle_id):
        return matlab_export.Jobs.bundle_status(self, bundle_id)

    def bundle_files(self, bundle_id):
        return matlab_export.Jobs.bundle_files(self, bundle_id)


class MatlabRouteFixture(AppTests):
    def setUp(self):
        super().setUp()
        self.jobs = FakeJobs(Path(self.enterContext(tempfile.TemporaryDirectory())) / "exports")
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


@unittest.skipUnless(h5py is not None, "needs h5py")
class MatlabExportRouteTests(MatlabRouteFixture):
    def test_scopes_windows_and_reuse(self):
        whole, flights, one = self.ask(scope="log"), self.ask(scope="flights"), self.ask(scope="flight", flight_id=10)
        self.assertEqual({whole.status_code, flights.status_code, one.status_code}, {202})
        self.assertEqual(len({whole.json["id"], flights.json["id"], one.json["id"]}), 3)
        self.assertEqual(self.ask(scope="flights").json["id"], flights.json["id"])  # identical request, same job
        self.assertEqual(one.json["filename"], "26_10_21__09_04_50_SD_flight_10.mat")
        self.assertEqual(whole.json["filename"], "26_10_21__09_04_50_SD_whole_log.mat")
        with patch.object(matlab_export, "export", return_value=[]) as export:
            self.jobs.jobs[flights.json["id"]]["task"]("out.mat", "work", lambda f: None)
        windows, extras = export.call_args.args[6], export.call_args.args[9]
        self.assertEqual(sorted(extras["aircraft"][5]), ["checklist", "config", "gcs", "goal", "pac", "pic",
                                                         "setting_changes", "settings", "simulation"])
        self.assertEqual([[w["index"], w["takeoff_s"], w["landing_s"]] for w in windows],
                         [[1, 100.0, 200.0], [3, 400.0, None]])  # the ground record (flight 2) is left out
        self.assertEqual(self.client.get(f"/api/matlab-exports/{one.json['id']}").json["status"], "queued")

    def test_every_local_page_script_is_served(self):
        import re
        static = Path(__file__).parent.parent / "static"
        scripts = {src for page in static.glob("*.html")
                   for src in re.findall(r'<script src="(/[^"]+)"', page.read_text())}
        self.assertIn("/matlab.js", scripts)
        for src in sorted(scripts):
            with self.client.get(src) as response:
                self.assertEqual(response.status_code, 200, src)

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
class MatlabBulkRouteTests(MatlabRouteFixture):
    def setUp(self):
        super().setUp()
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,log_path,status,uploaded_at,uploaded_by,project_id,log_type,"
            "size_bytes) VALUES (3,'26_10_21__09_04_50.data','sha3','c.data.gz','c.log.gz','done','now',2,1,'gcs',100)")
        self.con.execute("INSERT INTO aircraft(id,log_id,name,ac_id) VALUES (3,3,'Drone',5)")
        self.con.executemany(
            "INSERT INTO flights(id,aircraft_id,idx,takeoff_time_s,landing_time_s,verdict) VALUES (?,3,?,?,?,?)",
            [(30, 0, 50.0, 150.0, "flight"), (31, 1, 500.0, 600.0, "flight")])
        self.con.commit()

    def bulk(self, entries):
        return self.client.post("/api/matlab-bulk", json=dict(entries=entries))

    def test_sd_is_preferred_and_each_log_is_its_own_export(self):
        response = self.bulk([[30, 10], [31], [12, 99]])  # entry 1 has SD and GCS; 99 does not exist
        self.assertEqual(response.status_code, 202)
        self.assertEqual((response.json["flights"], response.json["logs"], response.json["skipped"]), (3, 2, 0))
        self.assertEqual(response.json["filename"], "logbook_3_flights.zip")
        self.assertEqual((response.json["status"], response.json["parts"], response.json["ready"]), ("queued", 2, 0))
        self.assertEqual(self.bulk([[31], [30, 10], [12]]).json["id"], response.json["id"])  # same selection
        windows = {}
        for job in list(self.jobs.jobs.values()):
            with patch.object(matlab_export, "export", return_value=[]) as export:
                job["task"]("out.mat", "work", lambda f: None)
            windows[job["log_id"]] = [w["takeoff_s"] for w in export.call_args.args[6]]
            self.jobs.finish(job["id"], f"mat of log {job['log_id']}".encode())
        self.assertEqual(windows, {1: [100.0, 400.0], 3: [500.0]})
        ready = self.client.get(f"/api/matlab-exports/{response.json['id']}").json
        self.assertEqual((ready["status"], ready["ready"]), ("ready", 2))
        with self.client.get(ready["download"]) as download:
            self.assertEqual(download.mimetype, "application/zip")
            with zipfile.ZipFile(io.BytesIO(download.data)) as archive:
                self.assertEqual(archive.namelist(), ["26_10_21__09_04_50_SD_flights.mat", "26_10_21__09_04_50_flights.mat"])
                self.assertEqual(archive.read("26_10_21__09_04_50_flights.mat"), b"mat of log 3")

    def test_a_whole_log_selection_reuses_its_all_flights_export(self):
        single = self.ask(log_id=3, scope="flights").json["id"]
        bulk = self.bulk([[30], [31]]).json
        self.assertEqual(self.jobs.bundle_files(bulk["id"])[0][0].stem, single)

    def test_bulk_refusals(self):
        self.assertEqual(self.bulk([]).status_code, 400)
        self.assertEqual(self.bulk([["10"]]).status_code, 400)
        self.assertEqual(self.bulk([[99]]).status_code, 409)
        job = self.bulk([[10], [30]]).json["id"]
        self.ns["MATLAB_BULK_MAX_LOGS"] = 1
        self.addCleanup(self.ns.__setitem__, "MATLAB_BULK_MAX_LOGS", 200)
        self.assertEqual(self.bulk([[10], [30]]).status_code, 400)
        self.sign_in(4)  # sees neither log
        self.assertEqual(self.bulk([[10], [30]]).status_code, 409)
        self.assertEqual(self.client.get(f"/api/matlab-exports/{job}").status_code, 404)


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
