"""GPS quality of takeoff/landing positions (gps_quality.py, the parsers) and indoor flights by takeoff or landing."""
import json
import struct
import tempfile
import unittest
from pathlib import Path

import gps_quality
import mav_parse
from .test_historical import AppTests

GPS_FMT = ("FMT, 128, 89, FMT, BBnNZ, Type,Length,Name,Format,Columns\n"
           "FMT, 129, 23, PARM, QNf, TimeUS,Name,Value\n"
           "FMT, 131, 9, ARM, QB, TimeUS,ArmState\n"
           "FMT, 132, 36, GPS, QBIHBffff, TimeUS,Status,GMS,GWk,NSats,HDop,Lat,Lng,Alt\n"
           "FMT, 133, 30, VISP, Qfff, TimeUS,PX,PY,PZ\n")


def ap_flight(lines, fmt=GPS_FMT):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "flight.log"
        path.write_text(fmt + "".join(line + "\n" for line in lines), encoding="ascii")
        return mav_parse.parse(path, path_rate=5)["aircraft"][0]["flights"][0]


def gps(t, status=3, sats=12, hdop=0.8, lat=52.0, lon=4.0, alt=10.0):
    return f"GPS, {int(t * 1e6)}, {status}, {int(t * 1000)}, 2410, {sats}, {hdop}, {lat}, {lon}, {alt}"


class QualityRuleTests(unittest.TestCase):
    def test_rule(self):
        good = gps_quality.good
        self.assertTrue(good({"fix_type": 3, "satellites": 6, "hdop": 2.5}))
        for q in ({"fix_type": 2, "satellites": 12, "hdop": 0.8}, {"fix_type": 3, "satellites": 5, "hdop": 0.8},
                  {"fix_type": 3, "satellites": 12, "hdop": 2.6}, {"fix_type": 3, "eph_m": 5.5},
                  {"fix_type": 3, "pdop": 3.0}, {"fix_type": 3, "hdop": "nan?"}):
            self.assertFalse(good(q), q)
        self.assertTrue(good({"fix_type": 3}))                 # older logs: the fix type only
        self.assertTrue(good({"fix_type": 3, "pacc_m": 2}))
        self.assertTrue(good({"fix_type": 3, "satellites": 0, "external": "OptiTrack"}))
        self.assertTrue(good(None))

    def test_endpoint_position(self):
        position = gps_quality.endpoint_position
        self.assertEqual(position({"location": None}, 52, 4), (52, 4))  # older parse: as logged
        self.assertEqual(position(None, 52, 4), (52, 4))
        self.assertEqual(position({"gps": {"fix_type": 3, "satellites": 9, "selected": "first"}}, 52, 4), (52, 4))
        self.assertEqual(position({"gps": {"fix_type": 3, "satellites": 4, "selected": "first"}}, 52, 4),
                         (None, None))  # a tighter rule applies without re-parsing
        raw = {"fix_type": 3, "satellites": 4, "hdop": 5.5, "selected": "raw", "lat": 52, "lon": 4}
        self.assertEqual(position({"gps": raw}, None, None), (None, None))
        self.assertEqual(position({"gps": dict(raw, satellites=8, hdop=1)}, None, None), (52, 4))


class ArduPilotSelectionTests(unittest.TestCase):
    def test_unconverged_first_fix_is_skipped(self):
        # like the CyberZoo log: a 3D fix with few satellites and an altitude far off, then good fixes
        flight = ap_flight(["ARM, 1000000, 1", gps(2, sats=4, hdop=5.5, lat=52.002, alt=568),
                            gps(4, lat=52.0001), gps(6, lat=52.0002), gps(8, lat=52.0003), "ARM, 9000000, 0"])
        self.assertEqual(flight["takeoff"]["location"]["lat"], 52.0001)
        self.assertEqual(flight["takeoff"]["location"]["alt_m"], 10)
        self.assertEqual(flight["takeoff"]["gps"], {"fix_type": 3, "satellites": 12, "hdop": 0.8, "good": True,
                                                    "selected": "later"})
        self.assertEqual(flight["landing"]["location"]["lat"], 52.0003)
        self.assertEqual(flight["landing"]["gps"]["selected"], "last")
        self.assertLess(flight["from_takeoff_m"], 30)
        self.assertEqual(flight["gps_quality"], {"fixes": 4, "good_fixes": 3, "good_fraction": 0.75})

    def test_no_good_fix_gives_no_location_but_the_raw_sample(self):
        flight = ap_flight(["ARM, 1000000, 1", gps(2, sats=4, hdop=5.5, alt=568),
                            gps(5, sats=5, hdop=1.7, lat=52.001, alt=24), "ARM, 9000000, 0"])
        self.assertIsNone(flight["takeoff"]["location"])
        self.assertIsNone(flight["landing"]["location"])
        self.assertEqual(flight["distance_m"], 0)
        self.assertTrue(all(p["position"] is None or p["position"]["lat"] is None for p in flight["path"]))
        self.assertEqual(flight["landing"]["gps"]["selected"], "raw")
        self.assertEqual(flight["landing"]["gps"]["lat"], 52.001)
        self.assertFalse(flight["landing"]["gps"]["good"])

    def test_good_fix_too_late_after_takeoff_and_bad_fix_before_landing(self):
        flight = ap_flight(["ARM, 1000000, 1", gps(2, status=1, sats=0, hdop=99), gps(40, lat=52.0001),
                            gps(50, lat=52.0002), gps(55, sats=4, lat=52.01), "ARM, 60000000, 0"])
        self.assertIsNone(flight["takeoff"]["location"])  # more than 30 s after takeoff
        self.assertEqual(flight["landing"]["location"]["lat"], 52.0002)
        self.assertEqual(flight["landing"]["gps"]["selected"], "earlier")

    def test_older_gps_message_without_satellites_or_hdop(self):
        fmt = GPS_FMT.replace("QBIHBffff, TimeUS,Status,GMS,GWk,NSats,HDop,Lat,Lng,Alt", "QBIHfff, TimeUS,Status,GMS,GWk,Lat,Lng,Alt")
        flight = ap_flight(["ARM, 1000000, 1", "GPS, 2000000, 2, 2000, 2410, 52.1, 4, 10",
                            "GPS, 3000000, 3, 3000, 2410, 52.0, 4, 10",
                            "ARM, 9000000, 0"], fmt)
        self.assertEqual(flight["takeoff"]["location"]["lat"], 52.0)  # the 2D fix is not used
        self.assertEqual(flight["takeoff"]["gps"], {"fix_type": 3, "good": True, "selected": "later"})

    def test_external_navigation_marks_the_flight(self):
        no_gps, later = gps(5, status=0, sats=0, hdop=99), gps(22, status=0, sats=0, hdop=99)
        flight = ap_flight([no_gps, "ARM, 1000000, 1", "VISP, 2000000, 0.1, 0.2, -1", "ARM, 9000000, 0"])
        self.assertEqual(flight["assessment"]["external_positioning"], "ArduPilot ExternalNav")
        flight = ap_flight([no_gps, "PARM, 0, EK3_SRC1_POSXY, 6", "ARM, 1000000, 1", "ARM, 9000000, 0"])
        self.assertEqual(flight["assessment"]["external_positioning"], "ArduPilot ExternalNav")
        flight = ap_flight([no_gps, "PARM, 0, EK3_SRC1_POSXY, 3", "ARM, 1000000, 1", "ARM, 9000000, 0",
                            "ARM, 19000000, 1", "VISP, 20000000, 0, 0, 0", later, "ARM, 25000000, 0"])
        self.assertNotIn("external_positioning", flight["assessment"])  # vision data only after this flight


class TlogAndPX4QualityTests(unittest.TestCase):
    def test_tlog_poor_fix_drops_the_fused_position_too(self):
        from pymavlink import mavutil
        mav = mavutil.mavlink.MAVLink(None, srcSystem=1, srcComponent=1)
        t0, chunks = 1_784_621_695_000_000, []

        def add(offset_s, msg):
            chunks.append(struct.pack(">Q", t0 + int(offset_s * 1e6)) + msg.pack(mav))

        add(0, mav.heartbeat_encode(2, 3, 128, 0, 4))
        add(1, mav.gps_raw_int_encode(t0, 3, 519920000, 43700000, 568000, 550, 65535, 0, 0, 4))
        add(1.1, mav.global_position_int_encode(0, 519920000, 43700000, 568000, 0, 0, 0, 0, 0))
        add(2, mav.gps_raw_int_encode(t0, 3, 519900000, 43700000, 12000, 90, 65535, 0, 0, 11))
        add(3, mav.gps_raw_int_encode(t0, 3, 519900100, 43700000, 12000, 90, 65535, 0, 0, 11))
        add(4, mav.heartbeat_encode(2, 3, 0, 0, 3))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "flight.tlog"
            path.write_bytes(b"".join(chunks))
            flight = mav_parse.parse(path, path_rate=5)["aircraft"][0]["flights"][0]
        self.assertAlmostEqual(flight["takeoff"]["location"]["lat"], 51.99)
        self.assertEqual(flight["takeoff"]["gps"]["hdop"], 0.9)
        self.assertEqual(flight["takeoff"]["gps"]["satellites"], 11)

    def test_px4_mocap_topic_marks_the_flight(self):
        formats = ["vehicle_control_mode:uint64_t timestamp;bool flag_armed;",
                   "vehicle_mocap_odometry:uint64_t timestamp;float x;"]
        add = [(0, 1, "vehicle_control_mode"), (0, 2, "vehicle_mocap_odometry")]
        rows = [(1, struct.pack("<Q?", 1_000_000, True)), (2, struct.pack("<Qf", 2_000_000, 0.5)),
                (1, struct.pack("<Q?", 9_000_000, False))]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "mocap.ulg"
            mav_parse.write_ulog(path, start_us=1_000_000, info={}, formats=formats, add=add, samples=rows, logged=[])
            flight = mav_parse.parse(path, path_rate=5)["aircraft"][0]["flights"][0]
        self.assertEqual(flight["assessment"]["external_positioning"], "PX4 motion capture")


class IndoorByLandingTests(AppTests):
    def setUp(self):
        super().setUp()
        self.con.execute("INSERT INTO places(id,name,indoor,shape) VALUES (2,'Arena',1,?)",
                         (json.dumps({"type": "circle", "center": [52.01, 4.01], "radius_m": 50}),))
        self.con.commit()

    def insert_flight(self, takeoff, landing, takeoff_event=None, landing_event=None, assessment=None):
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by,project_id,log_type,start_utc)"
            " VALUES(1,'1','1','test.bin','done','now',2,1,'ardupilot','2010-01-01T11:59:00.000Z')")
        self.con.execute("INSERT INTO aircraft(id,log_id,name,ac_id) VALUES(1,1,'Drone',7)")
        self.con.execute(
            "INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc,takeoff_lat,takeoff_lon,landing_lat,"
            " landing_lon,takeoff_json,landing_json,verdict,assessment_json,has_gps_position)"
            " VALUES(1,1,1,60,'2010-01-01T12:00:00.000Z',?,?,?,?,?,?,'flight',?,1)",
            (*takeoff, *landing, json.dumps(takeoff_event or {}), json.dumps(landing_event or {}),
             json.dumps(assessment or {"verdict": "flight"})))
        self.con.commit()
        return self.client.get("/api/logbook").json[0]

    def test_landing_in_an_indoor_place_makes_the_flight_indoor(self):
        entry = self.insert_flight((52.0, 4.0), (52.01, 4.01))
        self.assertEqual(entry["takeoff_place"], "Field")
        self.assertEqual(entry["landing_place"], "Arena")
        self.assertTrue(entry["indoor"])
        self.assertFalse(entry["indoor_no_gps"])
        self.assertEqual(entry["category_source"], "indoor")

    def test_outdoor_endpoints_stay_outdoor(self):
        self.assertFalse(self.insert_flight((52.0, 4.0), (52.0, 4.0))["indoor"])

    def test_untrusted_stored_takeoff_is_not_used(self):
        # parsed before the rule was tightened: the stored quality no longer passes, so only the landing counts
        bad = {"gps": {"fix_type": 3, "satellites": 4, "hdop": 5.5, "good": True, "selected": "first"}}
        entry = self.insert_flight((52.0, 4.0), (52.01, 4.01), takeoff_event=bad)
        self.assertEqual((entry["takeoff_lat"], entry["takeoff_lon"]), (52.01, 4.01))
        self.assertTrue(entry["takeoff_location_inferred"])
        self.assertEqual(entry["takeoff_place"], "Arena")
        self.assertTrue(entry["indoor"])

    def test_external_positioning_is_indoor_with_its_source(self):
        entry = self.insert_flight((None, None), (None, None),
                                   assessment={"verdict": "flight", "external_positioning": "ArduPilot ExternalNav"})
        self.assertTrue(entry["indoor"])
        self.assertTrue(entry["optitrack"])
        self.assertEqual(entry["external_positioning"], "ArduPilot ExternalNav")
        self.assertEqual(entry["takeoff_place"], "ArduPilot ExternalNav")


PARSER = Path(__file__).resolve().parents[2] / "cpp/build/paparazzi_log_parsing"
GPS_INT_FIELDS = ("ecef_x", "ecef_y", "ecef_z", "lat", "lon", "alt", "hmsl", "ecef_xd", "ecef_yd", "ecef_zd",
                  "pacc", "sacc", "tow", "pdop", "numsv", "fix")


def paparazzi_flight(lines, gps_fields=GPS_INT_FIELDS):
    import subprocess
    messages = {"ROTORCRAFT_STATUS": ("ap_mode", "ap_in_flight"), "GPS_INT": gps_fields}
    protocol = "".join(f'<message name="{name}">\n' + "".join(f'<field name="{f}"/>\n' for f in fields)
                       + "</message>\n" for name, fields in messages.items())
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "fixture.data"
        path.write_text("".join(line + "\n" for line in lines))
        path.with_suffix(".log").write_text(
            '<configuration time_of_day="1751457600">\n<aircraft ac_id="5" name="Synthetic"/>\n<protocol>\n'
            '<msg_class name="telemetry">\n' + protocol + '</msg_class>\n</protocol>\n</configuration>\n')
        out = subprocess.run([str(PARSER), "-r", "5", str(path)], capture_output=True, text=True, check=True)
        return json.loads(out.stdout)["aircraft"][0]["flights"][0]


def gps_int(t, lat=52.0, numsv=9, pdop=120, fix=3, alt=10.0, old=False):
    position = f"0 0 0 {int(round(lat * 1e7))} 40000000 {int(alt * 1000)}"
    if old:  # an older GPS_INT without hmsl, speeds, accuracies and PDOP
        return f"{t} 5 GPS_INT {position} {numsv} {fix}"
    return f"{t} 5 GPS_INT {position} {int(alt * 1000)} 0 0 0 150 50 0 {pdop} {numsv} {fix}"


@unittest.skipUnless(PARSER.exists(), "build the C++ parser first (cpp/, Release)")
class PaparazziQualityTests(unittest.TestCase):
    def test_unconverged_first_fix_is_skipped(self):
        flight = paparazzi_flight([gps_int(0.5, numsv=4, pdop=550, lat=52.002, alt=568), "1 5 ROTORCRAFT_STATUS 13 1",
                                   gps_int(2, numsv=4, pdop=550, lat=52.002, alt=568), gps_int(4, lat=52.0001),
                                   gps_int(6, lat=52.0002), "8 5 ROTORCRAFT_STATUS 13 0"])
        self.assertAlmostEqual(flight["takeoff"]["location"]["lat"], 52.0001)
        self.assertEqual(flight["takeoff"]["gps"], {"fix_type": 3, "satellites": 9, "pdop": 1.2, "pacc_m": 1.5,
                                                    "good": True, "selected": "later"})
        self.assertAlmostEqual(flight["landing"]["location"]["lat"], 52.0002)
        self.assertEqual(flight["landing"]["gps"]["selected"], "last")
        self.assertLess(flight["from_takeoff_m"], 30)

    def test_no_good_fix_gives_the_raw_sample(self):
        flight = paparazzi_flight([gps_int(0.5, numsv=4, pdop=550), "1 5 ROTORCRAFT_STATUS 13 1",
                                   gps_int(4, numsv=5, pdop=170, lat=52.001), "8 5 ROTORCRAFT_STATUS 13 0"])
        self.assertIsNone(flight["takeoff"]["location"])
        self.assertIsNone(flight["landing"]["location"])
        self.assertEqual(flight["distance_m"], 0)
        self.assertEqual(flight["takeoff"]["gps"]["selected"], "raw")
        self.assertFalse(flight["landing"]["gps"]["good"])
        self.assertAlmostEqual(flight["landing"]["gps"]["lat"], 52.001)

    def test_older_gps_int_and_optitrack(self):
        old = tuple(f for f in GPS_INT_FIELDS if f not in ("hmsl", "ecef_xd", "ecef_yd", "ecef_zd", "pacc", "sacc",
                                                            "tow", "pdop"))
        flight = paparazzi_flight([gps_int(0.5, numsv=4, old=True), "1 5 ROTORCRAFT_STATUS 13 1",
                                   gps_int(2, numsv=8, old=True), "8 5 ROTORCRAFT_STATUS 13 0"], old)
        self.assertEqual(flight["takeoff"]["gps"], {"fix_type": 3, "satellites": 8, "good": True, "selected": "later"})
        flight = paparazzi_flight([gps_int(0.5, numsv=0, pdop=10), "1 5 ROTORCRAFT_STATUS 13 1",
                                   gps_int(2, numsv=0, pdop=10), "8 5 ROTORCRAFT_STATUS 13 0"])
        self.assertTrue(flight["assessment"]["optitrack"])
        self.assertEqual(flight["takeoff"]["gps"]["external"], "OptiTrack")
        self.assertIsNotNone(flight["takeoff"]["location"])
