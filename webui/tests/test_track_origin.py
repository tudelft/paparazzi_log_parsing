"""Tracks without GPS (local position only, e.g. motion capture or simulation) drawn from a chosen location."""
import json
import os

from .test_historical import AppTests

SQUARE = json.dumps({"type": "polygon", "points": [[52.0, 4.0], [52.0, 4.002], [52.002, 4.002], [52.002, 4.0]]})
HALL = json.dumps({"type": "polygon", "points": [[53.0, 5.0], [53.0, 5.002], [53.002, 5.002], [53.002, 5.0]]})


def point(t, north, east, lat=None, lon=None):
    return {"time_s": t, "position": {"lat": lat, "lon": lon, "gps_alt_m": None},
            "ins": {"position_ned_m": {"north": north, "east": east, "down": -1.0}}}


class TrackOriginTests(AppTests):
    def setUp(self):
        super().setUp()
        self.con.executemany("INSERT INTO places(id,name,indoor,shape) VALUES (?,?,1,?)",
                             [(11, "Arena", SQUARE), (12, "Hall", HALL)])
        log_id = self.con.execute(
            "INSERT INTO logs(name,sha256,data_path,log_path,status,uploaded_at,uploaded_by,project_id)"
            " VALUES ('LOG00025.BFL',?,'x','y','done','now',2,1)", (os.urandom(16).hex(),)).lastrowid
        aircraft = self.con.execute("INSERT INTO aircraft(log_id,ac_id,name) VALUES (?,1,'LOG00025')",
                                    (log_id,)).lastrowid
        self.flight = self.con.execute(
            "INSERT INTO flights(aircraft_id,idx,takeoff_time_s,takeoff_utc,project_id,has_gps_position,track_quality)"
            " VALUES (?,0,1,'2026-11-18',1,0,'{}')", (aircraft,)).lastrowid
        self.log_id = log_id
        self.con.commit()
        self.aircraft = aircraft
        self.path = [point(1, 0.0, 0.0), point(2, 111.3, 0.0), point(3, None, None)]

    def flown_at_hall(self):
        """A flight of project 1 (seen by its members) that took off in the Hall."""
        self.con.execute("INSERT INTO flights(aircraft_id,idx,takeoff_time_s,takeoff_utc,project_id,takeoff_lat,takeoff_lon,"
                         "has_gps_position) VALUES (?,1,1,'2026-11-18T10:00:00Z',1,53.001,5.001,1)", (self.aircraft,))
        self.con.commit()
        self.ns["flight_path"] = lambda *args: self.path

    def test_place_local_track(self):
        place, has_local = self.ns["place_local_track"], self.ns["has_local_track"]
        origin = {"lat": 52.0, "lon": 4.0}
        placed = place(self.path, origin)
        self.assertEqual((placed[0]["ins"]["lat"], placed[0]["ins"]["lon"]), (52.0, 4.0))
        self.assertAlmostEqual(placed[1]["ins"]["lat"], 52.001, places=5)  # 111.3 m north is about 0.001°
        self.assertNotIn("lat", placed[2]["ins"])  # no local position: nothing to place
        self.assertIsNone(placed[1]["position"]["lat"])  # never as GPS
        self.assertNotIn("lat", self.path[1]["ins"])  # the cached path is unchanged
        with_gps = [point(1, 0.0, 0.0, 52.0, 4.0), point(2, 1.0, 1.0)]
        self.assertFalse(has_local(with_gps))
        self.assertIs(place(with_gps, origin), with_gps)
        own_origin = [dict(point(1, 0.0, 0.0), ins={"position_ned_m": {"north": 0.0}, "lat": 51.0, "lon": 3.0})]
        self.assertFalse(has_local(own_origin))  # Paparazzi: placed from the logged origin already

    def test_project_default_and_flight_override(self):
        path_url, url = f"/api/flights/{self.flight}/path", f"/api/flights/{self.flight}/track-origin"
        self.sign_in(2)
        self.assertNotIn("lat", self.client.get(path_url).json[1]["ins"])  # no origin: no map track
        self.assertEqual(self.client.put("/api/admin/projects/1", json={"track_origin_place_id": 11}).status_code, 403)
        self.sign_in(1)
        self.assertEqual(self.client.put("/api/admin/projects/1", json={"track_origin_place_id": 9}).status_code, 400)
        self.assertEqual(self.client.put("/api/admin/projects/1", json={"track_origin_place_id": 11}).status_code, 200)
        self.assertEqual(self.ns["track_origin"](self.con, self.flight)["source"], "project")
        self.assertAlmostEqual(self.client.get(path_url).json[0]["ins"]["lat"], 52.001)  # the place's centre
        self.sign_in(3)  # See only
        self.assertEqual(self.client.put(url, json={"place_id": 12}).status_code, 403)
        self.sign_in(2)
        self.assertEqual(self.client.put(url, json={"place_id": 9}).status_code, 400)
        self.assertEqual(self.client.put(url, json={"place_id": 12}).status_code, 400)  # a location they cannot see
        self.flown_at_hall()
        self.assertEqual(self.client.put(url, json={"place_id": 12}).status_code, 200)
        origin = self.ns["track_origin"](self.con, self.flight)
        self.assertEqual((origin["name"], origin["source"], origin["project_place"]["name"]), ("Hall", "flight", "Arena"))
        snapshot = self.ns["snapshot_flight_edits"](self.con, self.log_id)  # kept across a re-parse
        self.assertEqual(snapshot[(1, 0)]["track_origin_place_id"], 12)
        self.assertEqual(self.client.put(url, json={"place_id": None}).status_code, 200)
        self.assertEqual(self.ns["track_origin"](self.con, self.flight)["source"], "project")
        self.sign_in(1)
        self.assertEqual(self.client.delete("/api/admin/places/11").status_code, 200)
        self.assertIsNone(self.ns["track_origin"](self.con, self.flight))

    def test_only_locations_the_person_may_see_are_offered(self):
        self.con.execute("INSERT INTO places(id,name,indoor,shape) VALUES (13,'Secret site',0,?)",
                         (json.dumps({"type": "circle", "center": [10, 10], "radius_m": 50}),))
        self.con.commit()
        listed = lambda: [p["name"] for p in self.client.get(f"/api/flights/{self.flight}/detail").json["track_origin"]["places"]]  # noqa: E731
        self.sign_in(1)
        self.assertEqual(listed(), ["Arena", "Field", "Hall", "Secret site"])  # administrators: every location
        self.sign_in(2)
        self.assertEqual(listed(), [])
        self.flown_at_hall()
        self.assertEqual(listed(), ["Hall"])
        self.sign_in(1)  # the project's default stays listed, also when nobody flew there
        self.assertEqual(self.client.put("/api/admin/projects/1", json={"track_origin_place_id": 11}).status_code, 200)
        self.sign_in(2)
        self.assertEqual(listed(), ["Arena", "Hall"])

    def test_a_project_manager_sets_its_track_origin(self):
        url = "/api/projects/1/track-origin"
        self.sign_in(2)  # full access, not a manager
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.put(url, json={"place_id": 12}).status_code, 403)
        self.con.execute("UPDATE project_members SET role='manager' WHERE project_id=1 AND user_id=2")
        self.con.commit()
        self.assertEqual(self.client.get(url).json, {"place_id": None, "places": []})
        self.assertEqual(self.client.put(url, json={"place_id": 12}).status_code, 400)  # not a location they may see
        self.flown_at_hall()
        self.assertEqual([p["name"] for p in self.client.get(url).json["places"]], ["Hall"])
        self.assertEqual(self.client.put(url, json={"place_id": 12}).status_code, 200)
        self.assertEqual(self.ns["track_origin"](self.con, self.flight)["name"], "Hall")
        self.assertEqual(self.client.put(url, json={"place_id": None}).status_code, 200)
        self.assertIsNone(self.ns["track_origin"](self.con, self.flight))
        self.assertEqual(self.client.put("/api/admin/projects/1", json={"name": "Renamed"}).status_code, 403)
        self.sign_in(1)
        self.assertEqual(self.client.put(url, json={"place_id": 11}).status_code, 200)  # administrators: any location


if __name__ == "__main__":
    import unittest
    unittest.main()
