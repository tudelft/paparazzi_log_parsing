"""Flight page: type the goal, and link the recording to a fleet aircraft (Edit rights on the flight)."""
import os

from .test_historical import AppTests


class FlightGoalAircraftTests(AppTests):
    def setUp(self):
        super().setUp()
        self.con.execute("INSERT INTO drones(id,name,unique_id,created_at) VALUES (2,'Other','UID2','now')")
        self.con.executemany("INSERT INTO drone_projects(drone_id,project_id) VALUES (?,?)", [(1, 1), (2, 2)])
        log_id = self.con.execute(
            "INSERT INTO logs(name,sha256,data_path,log_path,status,uploaded_at,uploaded_by,project_id)"
            " VALUES ('LOG00025.BFL',?,'x','y','done','now',2,1)", (os.urandom(16).hex(),)).lastrowid
        aircraft = self.con.execute("INSERT INTO aircraft(log_id,ac_id,name,goal) VALUES (?,1,'LOG00025','hover')",
                                    (log_id,)).lastrowid
        self.flight = self.con.execute(
            "INSERT INTO flights(aircraft_id,idx,takeoff_time_s,takeoff_utc,project_id,has_gps_position,track_quality)"
            " VALUES (?,0,1,'2026-11-18',1,0,'{}')",
            (aircraft,)).lastrowid
        self.log_id = log_id
        self.con.commit()

    def entry(self):
        response = self.client.get("/api/logbook")
        self.assertEqual(response.status_code, 200, response.data[:500])
        return next(r for r in response.json if r.get("flight_id") == self.flight)

    def test_goal(self):
        url = f"/api/flights/{self.flight}/goal"
        self.sign_in(3)  # See only
        self.assertEqual(self.client.put(url, json={"goal": "x"}).status_code, 403)
        self.sign_in(2)
        self.assertEqual(self.client.put(url, json={"goal": "  Wind   tunnel test "}).json["goal"], "Wind tunnel test")
        self.assertEqual(self.entry()["goal"], "Wind tunnel test")
        snapshot = self.ns["snapshot_flight_edits"](self.con, self.log_id)  # kept across a re-parse
        self.assertEqual(snapshot[(1, 0)]["goal_override"], "Wind tunnel test")
        self.assertEqual(self.client.put(url, json={"goal": ""}).json["goal"], None)
        self.assertEqual(self.entry()["goal"], "hover")  # back to the logged goal
        self.assertEqual(self.client.put(url, json={"goal": "x" * 301}).status_code, 400)

    def test_aircraft(self):
        url = f"/api/flights/{self.flight}/aircraft"
        self.sign_in(3)
        self.assertEqual(self.client.put(url, json={"drone_id": 1}).status_code, 403)
        self.sign_in(2)  # Full on project 1: may pick aircraft of project 1, not of project 2
        self.assertEqual([d["id"] for d in self.client.get(f"/api/flights/{self.flight}/aircraft-choices").json["drones"]], [1])
        self.assertEqual(self.client.put(url, json={"drone_id": 2}).status_code, 400)
        self.assertEqual(self.client.put(url, json={"drone_id": 1}).status_code, 200)
        self.assertEqual(self.con.execute("SELECT drone_id FROM aircraft WHERE log_id=?", (self.log_id,)).fetchone()[0], 1)
        self.assertEqual(self.entry()["drone_id"], 1)
        self.assertEqual(self.client.put(url, json={"drone_id": None}).status_code, 200)
        self.assertIsNone(self.con.execute("SELECT drone_id FROM log_aircraft_links WHERE log_id=?", (self.log_id,)).fetchone())
        self.sign_in(1)  # admins may pick any
        self.assertEqual(self.client.put(url, json={"drone_id": 2}).status_code, 200)


if __name__ == "__main__":
    import unittest
    unittest.main()
