import io
import math
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

import mav_parse
from test_historical import AppTests


def _tiny_ulog(path):
    """Armed PX4 log with GPS, attitude, commander text and a preflight warning."""
    t0 = 1_000_000
    utc0 = 1_784_621_695_000_000  # 2026-07-21-ish, microseconds
    fmt = [
        "vehicle_status:uint64_t timestamp;uint8_t arming_state;uint8_t nav_state;uint8_t system_id;"
        "uint8_t failsafe;uint8_t vehicle_type;",
        "vehicle_control_mode:uint64_t timestamp;bool flag_armed;",
        "vehicle_gps_position:uint64_t timestamp;double latitude_deg;double longitude_deg;"
        "float altitude_msl_m;uint64_t time_utc_usec;float vel_m_s;uint8_t fix_type;",
        "vehicle_attitude:uint64_t timestamp;float[4] q;",
        "battery_status:uint64_t timestamp;float voltage_v;",
    ]
    add = [(0, 1, "vehicle_status"), (0, 2, "vehicle_control_mode"),
           (0, 3, "vehicle_gps_position"), (0, 4, "vehicle_attitude"), (0, 5, "battery_status")]
    logged = [
        (t0, ord("6"), "[commander] Armed by RC"),
        (t0 + 1000, ord("4"), "[health_and_arming_checks] Preflight Fail: Compass 1 fault"),
        (t0 + 8_000_000, ord("6"), "[commander] Disarmed by landing"),
    ]
    samples = []
    q = (1.0, 0.0, 0.0, 0.0)
    for i, armed in ((0, 1), (1, 1), (2, 1), (8, 0)):
        tus = t0 + i * 1_000_000
        samples.append((1, struct.pack("<QBBBBB", tus, 2 if armed else 1, 3, 1, 0, 1)))
        samples.append((2, struct.pack("<Q?", tus, bool(armed))))
        lat, lon = 51.99 + i * 0.0002, 4.37 + i * 0.0002
        samples.append((3, struct.pack("<QddfQfB", tus, lat, lon, 10.0 + i, utc0 + i * 1_000_000, 5.0, 3)))
        samples.append((4, struct.pack("<Qffff", tus, *q)))
        samples.append((5, struct.pack("<Qf", tus, 16.2)))
    mav_parse.write_ulog(
        path, start_us=t0,
        info={"sys_name": "PX4", "sys_uuid": "deadbeefcafebabe", "ver_hw": "TEST_HW", "ver_sw": "abc123"},
        formats=fmt, add=add, logged=logged, samples=samples,
    )


def _tiny_ap_log(path):
    Path(path).write_text(
        "FMT, 128, 89, FMT, BBnNZ, Type,Length,Name,Format,Columns\n"
        "FMT, 129, 23, PARM, QNf, TimeUS,Name,Value\n"
        "FMT, 130, 50, MSG, QZ, TimeUS,Message\n"
        "FMT, 131, 9, ARM, QB, TimeUS,ArmState\n"
        "FMT, 132, 23, GPS, QBIHfff, TimeUS,Status,GMS,GWk,Lat,Lng,Alt\n"
        "FMT, 133, 20, ATT, Qfff, TimeUS,Roll,Pitch,Yaw\n"
        "PARM, 0, SYSID_THISMAV, 7\n"
        "MSG, 1000, ArduCopter V4.5.0\n"
        "MSG, 2000, PreArm: Compass not calibrated\n"
        "GPS, 3000000, 3, 123456, 2410, 51.99, 4.37, 12.0\n"
        "ATT, 3100000, 1.0, -2.0, 90.0\n"
        "ARM, 4000000, 1\n"
        "GPS, 5000000, 3, 125000, 2410, 51.9903, 4.3703, 25.0\n"
        "ATT, 5100000, 8.0, 3.0, 95.0\n"
        "ARM, 9000000, 0\n"
        "GPS, 9100000, 3, 129000, 2410, 51.9904, 4.3704, 12.5\n",
        encoding="ascii",
    )


def _tiny_tlog(path):
    from pymavlink import mavutil

    mav = mavutil.mavlink.MAVLink(None, srcSystem=1, srcComponent=1)
    mav.robust_parsing = True
    chunks = []
    t0 = 1_784_621_695_000_000

    def add(offset_us, msg):
        chunks.append(struct.pack(">Q", t0 + offset_us) + msg.pack(mav))

    add(0, mav.heartbeat_encode(2, 3, 128, 0, 4))
    add(10_000, mav.statustext_encode(6, b"ArduCopter ready"))
    add(1_000_000, mav.gps_raw_int_encode(t0, 3, 519900000, 43700000, 12000, 65535, 65535, 200, 0, 12))
    add(1_100_000, mav.attitude_encode(1100, 0.1, -0.05, 1.2, 0, 0, 0))
    add(2_000_000, mav.gps_raw_int_encode(t0 + 2_000_000, 3, 519903000, 43700300, 25000, 65535, 65535, 800, 0, 12))
    add(5_000_000, mav.heartbeat_encode(2, 3, 0, 0, 3))
    Path(path).write_bytes(b"".join(chunks))


class SniffTests(unittest.TestCase):
    def test_sniff_ulog_magic_and_paparazzi_xml(self):
        with tempfile.TemporaryDirectory() as d:
            ulg = Path(d) / "x.ulg"
            ulg.write_bytes(mav_parse.ULOG_MAGIC + b"\x00" * 20)
            self.assertEqual(mav_parse.sniff(ulg), mav_parse.LOG_PX4)
            xml = Path(d) / "ac.log"
            xml.write_text('<?xml version="1.0"?><protocol></protocol>')
            self.assertIsNone(mav_parse.sniff(xml))
            self.assertTrue(mav_parse.looks_paparazzi_xml(xml))
            ap = Path(d) / "flash.log"
            ap.write_text("FMT, 128, 89, FMT, BBnNZ, Type,Length,Name,Format,Columns\n")
            self.assertEqual(mav_parse.sniff(ap), mav_parse.LOG_AP)
            raw = Path(d) / "x.bin"
            raw.write_bytes(mav_parse.DF_HEAD + b"\x00" * 8)
            self.assertEqual(mav_parse.sniff(raw), mav_parse.LOG_AP)
            tlog = Path(d) / "link.tlog"
            tlog.write_bytes(struct.pack(">Q", 1) + b"\xfd" + b"\x00" * 20)
            self.assertEqual(mav_parse.sniff(tlog), mav_parse.LOG_TLOG)


class ParseTests(unittest.TestCase):
    def test_ulog_flights_path_and_all_messages(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sample.ulg"
            _tiny_ulog(path)
            doc = mav_parse.parse(path, path_rate=2)
        self.assertEqual(doc["parser"]["name"], mav_parse.PARSER_NAME)
        self.assertEqual(doc["log"]["type"], mav_parse.LOG_PX4)
        ac = doc["aircraft"][0]
        self.assertIn("cafebabe", ac["name"].lower())
        self.assertNotIn("TEST_HW", ac["name"])
        self.assertTrue(ac["autopilot_versions"])
        self.assertGreaterEqual(len(ac["flights"]), 1)
        flight = ac["flights"][0]
        self.assertTrue(flight["path"])
        self.assertIsNotNone(flight["path"][0]["position"]["lat"])
        self.assertIsNotNone(flight["path"][0]["attitude"]["euler_deg"])
        texts = [m["text"] for m in ac["info_messages"]]
        self.assertTrue(any("Armed" in t for t in texts))
        self.assertTrue(any("[topics]" in t for t in texts))
        self.assertTrue(any("Preflight" in t for t in texts))
        self.assertTrue(ac["preflight_checks"])
        self.assertGreater(ac["info_messages"][0]["time_s"] or 0, -0.001)

    def test_ardupilot_text_log_messages_and_arming(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "flash.log"
            _tiny_ap_log(path)
            doc = mav_parse.parse(path, path_rate=1)
        ac = doc["aircraft"][0]
        self.assertEqual(doc["log"]["type"], mav_parse.LOG_AP)
        texts = " ".join(m["text"] for m in ac["info_messages"])
        self.assertIn("ArduCopter", texts)
        self.assertIn("ARM", texts)
        self.assertTrue(ac["preflight_checks"])
        self.assertGreaterEqual(len(ac["flights"]), 1)
        self.assertTrue(any(p.get("position") for p in ac["flights"][0]["path"]))

    def test_tlog_statustext_and_heartbeat_arming(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "link.tlog"
            _tiny_tlog(path)
            doc = mav_parse.parse(path, path_rate=1)
        ac = doc["aircraft"][0]
        self.assertEqual(doc["log"]["type"], mav_parse.LOG_TLOG)
        texts = " ".join(m["text"] for m in ac["info_messages"])
        self.assertIn("ArduCopter ready", texts)
        self.assertGreaterEqual(len(ac["flights"]), 1)


class UploadGroupingTests(AppTests):
    def post_files(self, names_and_bytes):
        files = [(io.BytesIO(data), name) for name, data in names_and_bytes]
        names = [name for name, _ in names_and_bytes]
        response = self.client.post("/api/upload", data={"names": names, "files": files})
        response.request.input_stream.close()
        return response

    def test_ulg_is_accepted_and_paparazzi_pair_still_is(self):
        with tempfile.TemporaryDirectory() as d:
            ulg = Path(d) / "one.ulg"
            _tiny_ulog(ulg)
            r = self.post_files([("one.ulg", ulg.read_bytes())])
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(r.json["results"][0]["status"], "accepted")
        pair = self.post_files([("ac.data", b"payload"), ("ac.log", b"<protocol/>")])
        self.assertEqual(pair.json["results"][0]["status"], "accepted")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM ingest").fetchone()[0], 2)

    def test_orphan_paparazzi_log_rejected_after_ingest_sniff(self):
        r = self.post_files([("lonely.log", b'<?xml version="1.0"?><protocol></protocol>')])
        self.assertEqual(r.json["results"][0]["status"], "accepted")
        iid = r.json["results"][0]["ingest_id"]
        row = self.con.execute("SELECT * FROM ingest WHERE id=?", (iid,)).fetchone()
        self.ns["ingest_pair"](self.con, row)
        got = self.con.execute("SELECT status, error FROM ingest WHERE id=?", (iid,)).fetchone()
        self.assertEqual(got["status"], "rejected")
        self.assertIn("matching .data", got["error"])

    def test_zip_with_pair_and_ulg(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("ac.data", b"payload")
            z.writestr("ac.log", b"<protocol/>")
            with tempfile.TemporaryDirectory() as d:
                ulg = Path(d) / "q.ulg"
                _tiny_ulog(ulg)
                z.writestr("q.ulg", ulg.read_bytes())
        r = self.post_files([("mix.zip", buf.getvalue())])
        self.assertEqual(r.json["results"][0]["status"], "accepted")
        iid = r.json["results"][0]["ingest_id"]
        row = self.con.execute("SELECT * FROM ingest WHERE id=?", (iid,)).fetchone()
        self.ns["explode_zip"](self.con, row)
        kinds = {r["kind"] for r in self.con.execute("SELECT kind, name FROM ingest WHERE id!=?", (iid,))}
        names = {r["name"] for r in self.con.execute("SELECT name FROM ingest WHERE id!=?", (iid,))}
        self.assertIn("pair", kinds)
        self.assertIn("file", kinds)
        self.assertTrue(any(n.endswith(".ulg") for n in names))


class FleetLinkTests(AppTests):
    def test_unique_folder_links_and_later_logs_use_board_id(self):
        self.con.execute(
            "INSERT INTO drones(id,name,unique_id,created_at) VALUES (10,'Lorenz Ranger 1600','TUD00490','now')")
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at) VALUES (1,'Ranger 1600/a.ulg','aa','a.gz','done','now')")
        self.con.execute("INSERT INTO aircraft(log_id, ac_id, name) VALUES (1, 1, 'standard_plane 003e003d')")
        self.con.commit()
        self.ns["link_log_aircraft"](self.con, 1)
        self.assertEqual(self.con.execute("SELECT drone_id FROM aircraft WHERE log_id=1").fetchone()[0], 10)
        aliases = {r[0] for r in self.con.execute("SELECT alias FROM drone_aliases WHERE drone_id=10")}
        self.assertIn("standard_plane 003e003d", aliases)
        self.assertIn("003e003d", aliases)
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at) VALUES (2,'a.ulg','bb','b.gz','done','now')")
        self.con.execute("INSERT INTO aircraft(log_id, ac_id, name) VALUES (2, 1, 'standard_plane 003e003d')")
        self.ns["link_log_aircraft"](self.con, 2)
        self.assertEqual(self.con.execute("SELECT drone_id FROM aircraft WHERE log_id=2").fetchone()[0], 10)

    def test_generic_quad_folder_does_not_pick_a_fleet_quad(self):
        self.con.execute(
            "INSERT INTO drones(id,name,unique_id,created_at) VALUES (10,'Lorenz Quad','TUD00489','now')")
        self.con.execute(
            "INSERT INTO drones(id,name,unique_id,created_at) VALUES (11,'Till Quad','TUD00487','now')")
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at) VALUES (1,'Quadrotor/a.ulg','aa','a.gz','done','now')")
        self.con.execute("INSERT INTO aircraft(log_id, ac_id, name) VALUES (1, 1, 'quad_x 00500040')")
        self.con.commit()
        self.ns["link_log_aircraft"](self.con, 1)
        self.assertIsNone(self.con.execute("SELECT drone_id FROM aircraft WHERE log_id=1").fetchone()[0])
        payload = self.ns["fleet_payload"](self.con, include_unlinked=True)
        row = next(e for e in payload["unlinked"] if e["name"] == "quad_x 00500040")
        self.assertEqual(row["count"], 1)
        self.assertEqual(row["folders"], ["Quadrotor"])
        self.assertIn("00500040", row["board_ids"])
        self.assertEqual(row["candidates"], [])
        self.assertEqual(row.get("names") or [row["name"]], ["quad_x 00500040"])

    def test_unlinked_groups_same_board_id(self):
        self.con.execute(
            "INSERT INTO drones(id,name,unique_id,created_at) VALUES (10,'Lorenz Quad','TUD00489','now')")
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at) VALUES (1,'Quadrotor/a.ulg','aa','a.gz','done','now')")
        self.con.execute("INSERT INTO aircraft(log_id, ac_id, name) VALUES (1, 1, 'quad_x 00500040')")
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at) VALUES (2,'Quadrotor/b.ulg','bb','b.gz','done','now')")
        self.con.execute("INSERT INTO aircraft(log_id, ac_id, name) VALUES (2, 1, 'airframe_1001 00500040')")
        self.con.commit()
        payload = self.ns["fleet_payload"](self.con, include_unlinked=True)
        rows = [e for e in payload["unlinked"] if "00500040" in (e.get("board_ids") or [])]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["count"], 2)
        self.assertEqual(set(rows[0]["names"]), {"quad_x 00500040", "airframe_1001 00500040"})

    def test_linking_unlinked_board_id_covers_later_logs(self):
        self.sign_in(1)
        self.con.execute(
            "INSERT INTO drones(id,name,unique_id,created_at) VALUES (10,'Lorenz Quad','TUD00489','now')")
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at) VALUES (1,'Quadrotor/a.ulg','aa','a.gz','done','now')")
        self.con.execute("INSERT INTO aircraft(log_id, ac_id, name) VALUES (1, 1, 'quad_x 00500040')")
        self.con.commit()
        r = self.client.post("/api/admin/drone-aliases", json={"drone_id": 10, "alias": "quad_x 00500040"})
        self.assertEqual(r.status_code, 200, r.json)
        aliases = {row[0] for row in self.con.execute("SELECT alias FROM drone_aliases WHERE drone_id=10")}
        self.assertIn("quad_x 00500040", aliases)
        self.assertIn("00500040", aliases)
        self.assertEqual(self.con.execute("SELECT drone_id FROM aircraft WHERE log_id=1").fetchone()[0], 10)
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at) VALUES (2,'lonely.ulg','bb','b.gz','done','now')")
        self.con.execute("INSERT INTO aircraft(log_id, ac_id, name) VALUES (2, 1, 'quad_x 00500040')")
        self.ns["link_log_aircraft"](self.con, 2)
        self.assertEqual(self.con.execute("SELECT drone_id FROM aircraft WHERE log_id=2").fetchone()[0], 10)


class DistanceHelperTests(unittest.TestCase):
    def test_haversine_nearby_delft(self):
        m = mav_parse._haversine_m(51.99, 4.37, 51.9903, 4.3703)
        self.assertTrue(20 < m < 80)
        self.assertTrue(math.isfinite(m))


if __name__ == "__main__":
    unittest.main()
