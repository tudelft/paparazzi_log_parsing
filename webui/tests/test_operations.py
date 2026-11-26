import datetime
import json
import os
import unittest
from unittest.mock import patch

import operations
import weather
from .test_historical import AppTests

DAY = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
FORECAST = {"timezone": "Europe/Amsterdam", "fetched_at": "now", "note": None,
            "hours": [{"time": f"{DAY}T10:00", "wind_speed_ms": 5.0, "wind_gusts_ms": 8.0}]}


class OperationsTests(AppTests):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, LOGDB_BOOKINGS="1", LOGDB_OPERATIONS="1", LOGDB_NOTAM_URL="https://notam.example.org")
        env.start()
        self.addCleanup(env.stop)
        forecast = patch.object(operations.weather, "forecast_window", return_value=FORECAST)
        forecast.start()
        self.addCleanup(forecast.stop)
        self.sign_in(1)
        self.assertEqual(self.client.put("/api/admin/places/1", json={"bookable": True}).status_code, 200)

    def plan(self, **body):
        return self.client.post("/api/operations", json={
            "place_id": 1, "start": f"{DAY}T10:00", "end": f"{DAY}T14:00", "project_id": 1, **body})

    def item_id(self):
        return self.client.get("/api/operations/options").json["item_id"]

    def qualification(self, name, user_id=None, until=None):
        tid = self.con.execute("INSERT INTO qualification_types(name, requires_evidence) VALUES (?, 0)", (name,)).lastrowid
        if user_id:
            self.con.execute("INSERT INTO qualification_submissions(user_id,type_id,valid_until,status,submitted_by,"
                             "submitted_at) VALUES (?,?,?,'approved',?, 'now')", (user_id, tid, until, user_id))
        self.con.commit()
        return tid

    def set_rules(self, rules):
        item = self.item_id()
        r = self.client.put(f"/api/admin/booking-things/{item}", json={"name": "Outdoor operations", "rules": rules})
        self.assertEqual(r.status_code, 200, r.json)

    def test_off_without_the_switch(self):
        with patch.dict(os.environ, LOGDB_OPERATIONS="0"):
            self.assertEqual(self.client.get("/api/operations/options").status_code, 404)
            things = self.client.get(f"/api/bookings?from={DAY}&to={DAY}").json["things"]
            self.assertFalse(any(t["kind"] == "operations" for t in things))
            self.assertFalse(self.client.get("/api/me").json["operations"])

    def test_plan_a_flight(self):
        self.assertTrue(self.client.get("/api/me").json["operations"])
        things = self.client.get(f"/api/bookings?from={DAY}&to={DAY}").json["things"]
        item, = [t for t in things if t["kind"] == "operations"]
        self.assertEqual(item["name"], "Outdoor operations")
        self.assertEqual(self.client.delete(f"/api/admin/booking-things/{item['id']}").status_code, 409)
        self.sign_in(2)
        options = self.client.get("/api/operations/options").json
        self.assertEqual(([p["name"] for p in options["places"]], options["rule"]), (["Field"], "direct"))
        self.assertEqual([p["id"] for p in options["projects"]], [1])
        self.assertEqual(self.plan(project_id=2).status_code, 400)  # not their project
        self.assertEqual(self.client.post("/api/bookings", json={  # the generic booking refuses the item
            "thing_id": item["id"], "description": "x", "start": f"{DAY}T10:00", "end": f"{DAY}T11:00"}).status_code, 409)
        prep = self.client.post("/api/operations/prepare", json={
            "place_id": 1, "start": f"{DAY}T10:00", "end": f"{DAY}T14:00", "drone_id": 1}).json
        self.assertEqual((prep["weather"], prep["slots"], prep["notam_url"]), (FORECAST, [], "https://notam.example.org"))
        self.assertEqual(prep["checks"], [{"who": "Drone", "what": "fleet status", "ok": True, "detail": "not set"}])
        r = self.plan(crew=[3], drone_id=1, goal="Wind tests", notam={"checked": True, "reference": "A1234/26"})
        self.assertEqual((r.status_code, r.json["status"]), (201, "approved"), r.json)
        slot = self.client.get(f"/api/operations/{r.json['id']}").json
        team, = slot["teams"]
        self.assertEqual((slot["place"], team["pic"], team["project"], [c["name"] for c in team["crew"]], team["drone"]),
                         ("Field", "Pilot", "Research", ["Viewer"], "Drone"))
        self.assertEqual(team["preparation"]["notam"], {"checked": True, "reference": "A1234/26"})
        self.assertEqual(team["preparation"]["weather"], FORECAST)
        listed, = self.client.get(f"/api/bookings?from={DAY}&to={DAY}").json["bookings"]
        self.assertEqual(listed["operation"], {"place_id": 1, "place": "Field", "teams": 1, "pending": 0, "pics": ["Pilot"]})
        self.assertEqual(listed["description"], "Field: Pilot")
        self.assertEqual(self.client.post("/api/operations/prepare", json={
            "place_id": 1, "start": f"{DAY}T12:00", "end": f"{DAY}T13:00"}).json["slots"][0]["id"], r.json["id"])
        # only bookable outdoor locations
        self.con.execute("UPDATE places SET bookable=0")
        self.con.commit()
        self.assertEqual(self.plan().status_code, 400)

    def test_qualification_rules_and_approval(self):
        direct = self.qualification("A2")
        waits = self.qualification("A1/A3", user_id=2)
        self.set_rules([{"type_id": direct, "needs_approval": False}, {"type_id": waits, "needs_approval": True}])
        item = self.client.get(f"/api/bookings?from={DAY}&to={DAY}").json["things"]
        self.assertEqual([t["rules"] for t in item if t["kind"] == "operations"],  # by type name
                         [[{"type_id": waits, "needs_approval": True}, {"type_id": direct, "needs_approval": False}]])
        self.sign_in(5)  # no qualification: cannot plan
        self.assertEqual(self.plan().status_code, 403)
        self.sign_in(2)
        self.assertEqual(self.client.get("/api/operations/options").json["rule"], "approval")
        r = self.plan()
        self.assertEqual(r.json["status"], "pending", r.json)
        self.sign_in(1)
        queued, = self.client.get("/api/admin/operation-pending").json["teams"]
        self.assertEqual((queued["person"], queued["place"]), ("Pilot", "Field"))
        self.assertEqual(self.client.get("/api/admin/booking-pending").json["bookings"], [])  # not a plain booking
        url = f"/api/operations/teams/{queued['id']}/review"
        self.assertEqual(self.client.post(url, json={"action": "approve"}).status_code, 200)
        self.assertEqual(self.client.post(url, json={"action": "approve"}).status_code, 409)
        self.assertEqual(self.client.get(f"/api/operations/{r.json['id']}").json["teams"][0]["status"], "approved")
        decided, = self.client.get("/api/admin/approval-history?kind=operation").json["items"]
        self.assertEqual((decided["what"], decided["outcome"], decided["by"]), ("Field", "approved", "Admin"))
        # the checks: the PIC's qualification on the day of the flight
        self.con.execute("UPDATE qualification_submissions SET valid_until=? WHERE user_id=2",
                         ((datetime.date.today() + datetime.timedelta(days=1)).isoformat(),))
        self.con.commit()
        self.sign_in(2)
        checks = self.client.post("/api/operations/prepare", json={
            "place_id": 1, "start": f"{DAY}T10:00", "end": f"{DAY}T11:00"}).json["checks"]
        self.assertEqual([(c["what"], c["ok"]) for c in checks], [("A1/A3", False), ("A1/A3 or A2", False)])

    def test_join_and_leave(self):
        self.sign_in(2)
        bid = self.plan().json["id"]
        self.sign_in(5)
        r = self.client.post(f"/api/operations/{bid}/teams", json={"project_id": 1, "goal": "Swarm"})
        self.assertEqual((r.status_code, r.json["status"]), (201, "approved"), r.json)
        self.assertEqual(self.client.post(f"/api/operations/{bid}/teams", json={"project_id": 1}).status_code, 409)
        slot = self.client.get(f"/api/operations/{bid}").json
        self.assertEqual(([t["pic"] for t in slot["teams"]], slot["can_join"], slot["can_move"]),
                         (["Pilot", "Uploader"], False, False))
        mine = slot["teams"][1]["id"]
        self.assertEqual(self.client.delete(f"/api/operations/teams/{slot['teams'][0]['id']}").status_code, 403)
        self.assertEqual(self.client.put(f"/api/operations/{bid}", json={
            "start": f"{DAY}T09:00", "end": f"{DAY}T12:00"}).status_code, 403)
        self.assertFalse(self.client.delete(f"/api/operations/teams/{mine}").json["slot_removed"])
        self.sign_in(2)
        first = self.client.get(f"/api/operations/{bid}").json
        self.assertTrue(first["can_move"])  # its only team moves it
        self.assertEqual(self.client.put(f"/api/operations/{bid}", json={
            "start": f"{DAY}T09:00", "end": f"{DAY}T12:00"}).status_code, 200)
        self.assertTrue(self.client.delete(f"/api/operations/teams/{first['teams'][0]['id']}").json["slot_removed"])
        self.assertEqual(self.client.get(f"/api/operations/{bid}").status_code, 404)

    def test_edit_a_team(self):
        self.sign_in(2)
        bid = self.plan(goal="First").json["id"]
        team = self.client.get(f"/api/operations/{bid}").json["teams"][0]["id"]
        r = self.client.put(f"/api/operations/teams/{team}", json={"project_id": 1, "goal": "Second", "crew": [3]})
        self.assertEqual(r.json["status"], "approved", r.json)
        edited = self.client.get(f"/api/operations/{bid}").json["teams"][0]
        self.assertEqual((edited["goal"], [c["id"] for c in edited["crew"]]), ("Second", [3]))
        self.sign_in(3)
        self.assertEqual(self.client.put(f"/api/operations/teams/{team}", json={"project_id": 1}).status_code, 403)

    def test_flight_area_and_buffer(self):
        # the location (a circle of 100 m around 52, 4): a 40 m square area in the middle (corners 28 m out), at most 80 m high
        square = [[52.0 + dy, 4.0 + dx] for dy, dx in ((-0.00018, -0.00029), (-0.00018, 0.00029), (0.00018, 0.00029),
                                                         (0.00018, -0.00029))]
        for bad in ({"max_height_m": 0}, {"flight_area": [[52, 4], [52.1, 4]]}, {"flight_area": [[91, 4]] * 3}):
            self.assertEqual(self.client.put("/api/admin/places/1", json=bad).status_code, 400, bad)
        self.assertEqual(self.client.put("/api/admin/places/1", json={"max_height_m": 80, "flight_area": square}).status_code, 200)
        place, = self.client.get("/api/places").json["places"]
        self.assertEqual((place["max_height_m"], len(place["flight_area"])), (80, 4))
        self.sign_in(2)
        prepare = lambda **b: self.client.post("/api/operations/prepare", json={  # noqa: E731
            "place_id": 1, "start": f"{DAY}T10:00", "end": f"{DAY}T11:00", **b})
        area = prepare().json["area"]  # the location's area and height: the 80 m buffer reaches outside
        self.assertEqual((area["own_area"], area["height_m"], area["buffer_m"], area["method"], area["inside"]),
                         (False, 80, 80.0, "1:1", False))
        self.assertTrue(prepare(height_m=50).json["area"]["inside"])  # lower: the 50 m buffer fits
        self.assertEqual(prepare(height_m=90).status_code, 400)  # above the location's maximum
        r = self.plan(height_m=50)
        self.assertEqual(r.status_code, 201, r.json)  # a buffer outside would only warn
        row = self.con.execute("SELECT height_m, flight_area, buffer_m, buffer_method, buffer_inside FROM operation_teams").fetchone()
        self.assertEqual((row[0], len(json.loads(row[1])), row[2], row[3], row[4]), (50, 4, 50.0, "1:1", 1))
        team, = self.client.get(f"/api/operations/{r.json['id']}").json["teams"]
        self.assertEqual((team["height_m"], team["preparation"]["area"]["inside"]), (50, True))
        own = [[p[0] + 0.0006, p[1]] for p in square]  # a team's own area, moved towards the edge
        self.assertEqual(self.client.put(f"/api/operations/teams/{team['id']}", json={
            "project_id": 1, "height_m": 50, "flight_area": own}).json["status"], "approved")
        self.assertEqual(self.con.execute("SELECT buffer_inside FROM operation_teams").fetchone()[0], 0)
        self.assertTrue(self.client.get(f"/api/operations/{r.json['id']}").json["teams"][0]["own_area"])


class GroundRiskTests(unittest.TestCase):
    def test_margin(self):
        import ground_risk
        field = {"type": "polygon", "points": [[52.0, 4.0], [52.0, 4.01], [52.005, 4.01], [52.005, 4.0]]}  # ~685 x 556 m
        middle = [[52.002, 4.004], [52.002, 4.006], [52.003, 4.006], [52.003, 4.004]]
        room = ground_risk.margin(middle, field)
        self.assertAlmostEqual(room, 222.4, delta=2)  # 0.002 deg of latitude to the south edge
        self.assertTrue(ground_risk.assess(middle, 120, field)["inside"])
        self.assertFalse(ground_risk.assess(middle, 250, field)["inside"])
        crossing = [[51.999, 4.004], [51.999, 4.006], [52.001, 4.006], [52.001, 4.004]]
        self.assertLess(ground_risk.margin(crossing, field), 0)
        circle = {"type": "circle", "center": [52.0, 4.0], "radius_m": 500}
        self.assertAlmostEqual(ground_risk.margin([[52.0, 4.0], [52.001, 4.0], [52.0, 4.001]], circle), 388.8, delta=2)
        self.assertEqual(ground_risk.assess(None, 120, field)["inside"], None)


class ForecastWindowTests(unittest.TestCase):
    def test_hours_of_the_window(self):
        data = {"timezone": "Europe/Amsterdam", "hourly": {"time": [f"2026-12-01T{h:02d}:00" for h in range(24)],
                **{f: [1.0] * 24 for f in weather.FORECAST_FIELDS}}}
        weather._windows.clear()
        with patch.object(weather, "_fetch_forecast", return_value=data) as fetch:
            out = weather.forecast_window(52.0, 4.0, "2026-12-01T10:30", "2026-12-01T13:00")
            self.assertEqual([h["time"][11:] for h in out["hours"]], ["10:00", "11:00", "12:00"])
            later = weather.forecast_window(52.0, 4.0, "2026-12-20T10:00", "2026-12-20T12:00")
            self.assertEqual(later["hours"], [])
            self.assertIn("days ahead", later["note"])
            self.assertEqual(fetch.call_count, 1)  # cached per position
        self.assertTrue(json.dumps(out))


if __name__ == "__main__":
    unittest.main()
