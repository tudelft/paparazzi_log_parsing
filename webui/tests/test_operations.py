import datetime
import json
import os
import unittest
from unittest.mock import patch

import db
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
            "place_id": 1, "start": f"{DAY}T10:00", "end": f"{DAY}T14:00", "projects": [1], **body})

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
        self.assertEqual(self.plan(projects=[2]).status_code, 400)  # not their project
        self.assertEqual(self.plan(projects=[]).status_code, 400)
        self.assertEqual(self.client.post("/api/bookings", json={  # the generic booking refuses the item
            "thing_id": item["id"], "description": "x", "start": f"{DAY}T10:00", "end": f"{DAY}T11:00"}).status_code, 409)
        prep = self.client.post("/api/operations/prepare", json={
            "place_id": 1, "start": f"{DAY}T10:00", "end": f"{DAY}T14:00", "drones": [1]}).json
        self.assertEqual((prep["weather"], prep["slots"], prep["notam_url"]), (FORECAST, [], "https://notam.example.org"))
        self.assertEqual(prep["checks"], [{"who": "Drone", "what": "fleet status", "ok": True, "detail": "not set"}])
        r = self.plan(crew=[3], drones=[1], goal="Wind tests", notam={"checked": True, "reference": "A1234/26"})
        self.assertEqual((r.status_code, r.json["status"]), (201, "approved"), r.json)
        slot = self.client.get(f"/api/operations/{r.json['id']}").json
        team, = slot["teams"]
        self.assertEqual((slot["place"], team["pic"], [p["name"] for p in team["projects"]][0], [c["name"] for c in team["crew"]],
                          [d["name"] for d in team["drones"]]), ("Field", "Pilot", "Research", ["Viewer"], ["Drone"]))
        self.assertEqual(team["preparation"]["notam"], {"checked": True, "reference": "A1234/26"})
        self.assertEqual(team["preparation"]["weather"], FORECAST)
        listed, = self.client.get(f"/api/bookings?from={DAY}&to={DAY}").json["bookings"]
        self.assertEqual(listed["operation"], {"place_id": 1, "place": "Field", "teams": 1, "pending": 0, "pics": ["Pilot"],
                                               "external_status": None, "external_ref": None})
        self.assertEqual(listed["description"], "Field: Pilot")
        self.assertEqual(self.client.post("/api/operations/prepare", json={
            "place_id": 1, "start": f"{DAY}T12:00", "end": f"{DAY}T13:00"}).json["slots"][0]["id"], r.json["id"])
        # only bookable outdoor locations
        self.con.execute("UPDATE places SET bookable=0")
        self.con.commit()
        self.assertEqual(self.plan().status_code, 400)

    def test_seen_by_everyone_or_only_by_who_may_plan(self):
        waits = self.qualification("A1/A3", user_id=2)
        self.set_rules([{"type_id": waits, "needs_approval": False}])
        item = self.item_id()
        seen = lambda: [t["can_view"] for t in self.client.get(f"/api/bookings?from={DAY}&to={DAY}").json["things"]  # noqa: E731
                        if t["kind"] == "operations"]
        self.sign_in(5)  # no qualification
        self.assertEqual(seen(), [True])  # by default everyone sees it
        self.sign_in(1)
        r = self.client.put(f"/api/admin/booking-things/{item}", json={
            "name": "Outdoor operations", "qualification_for": "viewing", "rules": [{"type_id": waits, "needs_approval": False}]})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(seen(), [True])  # administrators
        self.sign_in(2)
        self.assertEqual(seen(), [True])  # may plan
        self.sign_in(5)
        self.assertEqual(seen(), [False])

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
        r = self.client.post(f"/api/operations/{bid}/teams", json={"projects": [1], "goal": "Swarm"})
        self.assertEqual((r.status_code, r.json["status"]), (201, "approved"), r.json)
        second = self.client.post(f"/api/operations/{bid}/teams", json={"projects": [1], "goal": "Other project"})
        self.assertEqual(second.status_code, 201)  # one PIC may have several teams (other projects or aircraft)
        slot = self.client.get(f"/api/operations/{bid}").json
        self.assertEqual(([t["pic"] for t in slot["teams"]], slot["can_join"], slot["can_move"]),
                         (["Pilot", "Uploader", "Uploader"], True, False))
        self.assertEqual(self.client.delete(f"/api/operations/teams/{second.json['team_id']}").status_code, 200)
        slot = self.client.get(f"/api/operations/{bid}").json
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
        r = self.client.put(f"/api/operations/teams/{team}", json={"projects": [1], "goal": "Second", "crew": [3]})
        self.assertEqual(r.json["status"], "approved", r.json)
        edited = self.client.get(f"/api/operations/{bid}").json["teams"][0]
        self.assertEqual((edited["goal"], [c["id"] for c in edited["crew"]]), ("Second", [3]))
        self.sign_in(3)
        self.assertEqual(self.client.put(f"/api/operations/teams/{team}", json={"projects": [1]}).status_code, 403)

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
            "projects": [1], "height_m": 50, "flight_area": own}).json["status"], "approved")
        self.assertEqual(self.con.execute("SELECT buffer_inside FROM operation_teams").fetchone()[0], 0)
        self.assertTrue(self.client.get(f"/api/operations/{r.json['id']}").json["teams"][0]["own_area"])

    def flight(self, ident, local, pic=None, log=None):
        """A logged flight over the location (52, 4) taking off at a local time of DAY (LOGDB_TIME_ZONE)."""
        from zoneinfo import ZoneInfo
        log = log or ident
        if not self.con.execute("SELECT 1 FROM logs WHERE id=?", (log,)).fetchone():
            self.con.execute("INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by,project_id,log_type)"
                             " VALUES(?,?,?,'test.data','done','now',2,1,'onboard_sd')", (log, f"log{log}", f"sha{log}"))
            self.con.execute("INSERT INTO aircraft(id,log_id,drone_id,name,ac_id) VALUES(?,?,1,'Drone',7)", (log, log))
        at = datetime.datetime.fromisoformat(f"{DAY}T{local}").replace(tzinfo=ZoneInfo("Europe/Amsterdam"))
        utc = at.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        self.con.execute("INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc,landing_utc,takeoff_lat,takeoff_lon,"
                         "landed,verdict,has_gps_position,pic_user_id) VALUES(?,?,?,300,?,?,52,4,1,'flight',1,?)",
                         (ident, log, ident, utc, utc, pic))
        self.con.commit()

    def test_several_aircraft(self):
        self.con.execute("INSERT INTO drones(id,name,unique_id,status,created_at) VALUES (2,'Other','UID2','Repair','now')")
        self.con.commit()
        self.sign_in(2)
        self.assertEqual(self.plan(drones=[1, 99]).status_code, 400)
        self.assertEqual(self.plan(drones="1").status_code, 400)
        r = self.plan(drones=[2, 1, 2])
        self.assertEqual(r.status_code, 201, r.json)
        team, = self.client.get(f"/api/operations/{r.json['id']}").json["teams"]
        self.assertEqual([d["name"] for d in team["drones"]], ["Drone", "Other"])
        self.assertEqual([(c["who"], c["ok"]) for c in team["preparation"]["checks"]], [("Other", False), ("Drone", True)])
        self.assertEqual(self.client.put(f"/api/operations/teams/{team['id']}", json={"projects": [1], "drones": [2]}).status_code, 200)
        self.assertEqual([r[0] for r in self.con.execute("SELECT drone_id FROM operation_drones")], [2])
        # a team's one aircraft from before moves to operation_drones
        self.con.execute("UPDATE operation_teams SET drone_id=1")
        self.con.execute(db.OPERATION_DRONES_FROM_TEAMS)
        self.assertEqual(sorted(r[0] for r in self.con.execute("SELECT drone_id FROM operation_drones")), [1, 2])

    def test_several_projects_and_teams_of_one_pic(self):
        self.con.execute("INSERT INTO project_members(project_id,user_id,role) VALUES (2,2,'edit')")
        self.con.execute("INSERT INTO drones(id,name,unique_id,created_at) VALUES (2,'Other','UID2','now')")
        self.con.commit()
        self.sign_in(2)
        r = self.plan(projects=[2, 1, 2], drones=[1])
        self.assertEqual(r.status_code, 201, r.json)
        bid = r.json["id"]
        second = self.client.post(f"/api/operations/{bid}/teams", json={"projects": [2], "drones": [2]})
        self.assertEqual(second.status_code, 201, second.json)
        teams = self.client.get(f"/api/operations/{bid}").json["teams"]
        self.assertEqual([[p["name"] for p in t["projects"]] for t in teams], [["Private", "Research"], ["Private"]])
        # the same PIC in both teams: a flight goes to the team of the aircraft that flew it
        self.flight(11, "10:30", pic=2)
        self.con.execute("UPDATE aircraft SET drone_id=2")
        self.con.commit()
        slot = self.client.get(f"/api/operations/{bid}").json
        self.assertEqual([[f["flight_id"] for f in t["flights"]] for t in slot["teams"]], [[], [11]])
        # a team's one project from before moves to operation_projects
        self.con.execute("DELETE FROM operation_projects")
        self.con.execute("UPDATE operation_teams SET project_id=1")
        self.con.execute(db.OPERATION_PROJECTS_FROM_TEAMS)
        self.assertEqual([r[0] for r in self.con.execute("SELECT project_id FROM operation_projects")], [1, 1])

    def test_removing_someone_elses_team_mails_its_pic(self):
        mail_env = patch.dict(os.environ, {"LOGDB_SMTP_HOST": "relay.example.org", "LOGDB_MAIL_FROM": "logbook@example.org"})
        mail_env.start()
        self.addCleanup(mail_env.stop)
        self.con.execute("UPDATE users SET email=lower(username)||'@example.org'")
        self.con.commit()
        r = self.plan(pic_id=2)  # the admin plans for Pilot
        team = r.json["team_id"]
        self.assertTrue(self.client.get(f"/api/operations/{r.json['id']}").json["can_join"])  # creator, not in a team
        self.assertTrue(self.client.delete(f"/api/operations/teams/{team}").json["slot_removed"])
        mails = self.con.execute("SELECT user_id, subject, body FROM mail_outbox WHERE subject LIKE 'Removed:%'").fetchall()
        self.assertEqual([(m[0], "Admin took your team out" in m[2], "the slot is cancelled" in m[2]) for m in mails], [(2, True, True)])

    def test_cancel_slot(self):
        self.sign_in(2)
        bid = self.plan().json["id"]
        self.sign_in(5)
        self.assertEqual(self.client.post(f"/api/operations/{bid}/teams", json={"projects": [1]}).status_code, 201)
        self.assertFalse(self.client.get(f"/api/operations/{bid}").json["can_cancel"])  # not every team is theirs
        self.assertEqual(self.client.delete(f"/api/operations/{bid}").status_code, 403)
        self.sign_in(1)  # an administrator cancels it with both teams
        self.assertTrue(self.client.get(f"/api/operations/{bid}").json["can_cancel"])
        self.assertEqual(self.client.delete(f"/api/operations/{bid}").status_code, 200)
        self.assertIsNone(self.con.execute("SELECT 1 FROM bookings WHERE id=?", (bid,)).fetchone())
        self.assertIsNone(self.con.execute("SELECT 1 FROM operation_teams WHERE booking_id=?", (bid,)).fetchone())
        self.sign_in(2)  # who planned every team cancels their own slot
        bid = self.plan().json["id"]
        self.assertEqual(self.client.delete(f"/api/operations/{bid}").status_code, 200)

    def test_last_flown_project_first(self):
        self.sign_in(2)
        self.assertIsNone(self.client.get("/api/operations/options").json["last_project_id"])
        self.con.execute("INSERT INTO project_members(project_id,user_id,role) VALUES (2,2,'edit')")
        self.flight(11, "10:30", pic=2)
        self.flight(12, "12:00", pic=2)
        self.flight(13, "13:00", pic=4)  # someone else's later flight does not count
        self.con.execute("UPDATE flights SET project_id=1, project_source='manual' WHERE id IN (11,13)")
        self.con.execute("UPDATE flights SET project_id=2, project_source='manual' WHERE id=12")
        self.con.execute("UPDATE flights SET takeoff_utc=replace(takeoff_utc, ?, '2026-01-05'),"
                         " landing_utc=replace(landing_utc, ?, '2026-01-05')", (DAY, DAY))  # flown: in the past
        self.con.execute("UPDATE logs SET start_utc='2026-01-05T08:00:00Z'")  # a plausible log clock
        self.con.commit()
        options = self.client.get("/api/operations/options").json
        self.assertEqual((sorted(p["id"] for p in options["projects"]), options["last_project_id"]), ([1, 2], 2))

    def test_flights_linked_to_teams(self):
        self.sign_in(2)
        bid = self.plan().json["id"]  # 10:00-14:00 local, PIC Pilot (2)
        self.flight(11, "10:30", pic=2)  # the team's PIC: linked
        self.flight(12, "13:50", pic=4)  # someone else: there, but no team
        self.flight(13, "16:00", pic=2)  # after the slot and its margin
        slot = self.client.get(f"/api/operations/{bid}").json
        team = slot["teams"][0]
        self.assertEqual([(f["flight_id"], f["automatic"]) for f in team["flights"]], [(11, True)])
        self.assertEqual([f["flight_id"] for f in slot["other_flights"]], [12])
        url = f"/api/operations/teams/{team['id']}/flights"
        self.assertEqual(self.client.post(url, json={"flight_id": 12, "action": "link"}).status_code, 200)
        self.assertEqual(self.client.post(url, json={"flight_id": 11, "action": "unlink"}).status_code, 200)
        slot = self.client.get(f"/api/operations/{bid}").json
        self.assertEqual([(f["flight_id"], f["automatic"]) for f in slot["teams"][0]["flights"]], [(12, False)])
        self.assertEqual([f["flight_id"] for f in slot["other_flights"]], [11])
        self.assertEqual(self.client.post(url, json={"flight_id": 999, "action": "link"}).status_code, 404)
        self.sign_in(3)  # not the team's
        self.assertEqual(self.client.post(url, json={"flight_id": 11, "action": "link"}).status_code, 403)
        # the flight page: the operation, or "not planned" at a bookable location
        self.sign_in(2)
        rows = self.client.get("/api/logbook").json
        entry = lambda fid: next(r for r in (rows["rows"] if isinstance(rows, dict) else rows)  # noqa: E731
                                 if any(s["flight_id"] == fid for s in r.get("sources") or []))
        with self.app.test_request_context():
            from flask import g
            g.user = self.con.execute("SELECT * FROM users WHERE id=2").fetchone()
            linked = operations.flight_operation(self.con, entry(12))
            self.assertEqual((linked["booking_id"], linked["pic"], linked["automatic"]), (bid, "Pilot", False))
            self.assertEqual(linked["map"]["boundary"]["type"], "circle")  # the fixture location
            missing = operations.flight_operation(self.con, entry(11))
            self.assertEqual((missing["missing"], missing["map"]["area"]), ("Field", None))  # Field has no default area
            self.con.execute("UPDATE places SET flight_area=?, max_height_m=50 WHERE id=1",
                             (json.dumps([[52.0001, 4.0001], [52.0002, 4.0001], [52.0002, 4.0002]]),))
            self.con.commit()
            self.assertEqual(operations.flight_operation(self.con, entry(11))["map"]["buffer_m"], 50)
        # a re-parse that renumbers the flights leaves the stale link out
        self.con.execute("UPDATE flights SET takeoff_utc='2001-01-01T00:00:00.000Z' WHERE id=12")
        self.con.commit()
        self.assertEqual(operations.flight_decisions(self.con).get(12), None)

    def test_a_slot_chooses_the_category_of_its_flights_for_who_may(self):
        oa = self.con.execute("INSERT INTO authorisations(name,category,valid_from,valid_until,created_at)"
                              " VALUES ('OA-9','specific','2020-01-01',NULL,'now')").lastrowid
        old = self.con.execute("INSERT INTO authorisations(name,category,valid_from,valid_until,created_at)"
                               " VALUES ('OA-old','specific','2020-01-01','2021-01-01','now')").lastrowid
        pilot = self.qualification("SPECIFIC pilot", user_id=2)
        self.sign_in(1)
        r = self.client.put(f"/api/admin/authorisations/{oa}", json={"qualification_types": [pilot]})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(self.client.put("/api/admin/categories/luc", json={"qualification_types": [pilot]}).status_code, 200)
        self.assertEqual(self.client.put("/api/admin/categories/open", json={"qualification_types": [pilot]}).status_code, 404)
        listed = self.client.get("/api/authorisations").json
        self.assertEqual((listed["category_rules"]["luc"], [a["qualification_types"] for a in listed["authorisations"]
                                                            if a["id"] == oa][0]), ([pilot], [pilot]))
        self.sign_in(3)  # without the qualification: OPEN only
        self.assertFalse(self.client.get("/api/authorisations").json["may_choose_categories"]["luc"])
        self.assertEqual(self.plan(pic_id=3, authorisation_id=oa).status_code, 403)
        self.assertEqual(self.plan(pic_id=3, category="open").status_code, 201)
        self.sign_in(2)
        self.assertEqual(self.plan(authorisation_id=old).status_code, 400)  # not valid that day
        self.assertEqual(self.plan(pic_id=3, authorisation_id=oa).status_code, 403)  # the PIC needs it too
        r = self.plan(authorisation_id=oa)
        self.assertEqual(r.status_code, 201, r.json)
        team, = [t for t in self.client.get(f"/api/operations/{r.json['id']}").json["teams"] if t["pic_id"] == 2]
        self.assertEqual((team["category"], team["authorisation_id"]), ("specific", oa))
        self.flight(11, "10:30", pic=2)  # matched to the team: its category
        self.flight(12, "16:00", pic=2)  # after the slot: the default
        self.con.execute("UPDATE logs SET date_override=? WHERE id=12", (DAY,))  # a future day: no plausible clock
        self.con.commit()
        rows = self.client.get("/api/logbook").json
        rows = rows["rows"] if isinstance(rows, dict) else rows
        got = {r["flight_id"]: (r["category"], r["authorisation_name"], r["category_source"]) for r in rows}
        self.assertEqual((got[11], got[12]), (("specific", "OA-9", "booking"), ("open", None, "default")))
        # an exception in Edit flight: the editor and the flight's PIC need what it asks
        self.assertEqual(self.client.put("/api/logbook/records", json={"record_keys": ["log:12"], "category": "luc"})
                         .status_code, 200)
        self.con.execute("UPDATE flights SET pic_user_id=3 WHERE id=11")
        self.con.commit()
        r = self.client.put("/api/logbook/records", json={"record_keys": ["log:11"], "category": "luc"})
        self.assertEqual(r.status_code, 403)
        self.assertIn("The PIC (Viewer) needs", r.json["error"])
        self.assertEqual(self.client.put("/api/logbook/records", json={"record_keys": ["log:11"], "category": None})
                         .status_code, 200)  # back to the default is always allowed
        self.sign_in(1)  # administrators may always
        self.assertEqual(self.client.put("/api/logbook/records", json={"record_keys": ["log:11"], "category": "luc"})
                         .status_code, 200)

    def booking_mails(self):
        return self.con.execute("SELECT * FROM mail_outbox WHERE kind='booking' ORDER BY id").fetchall()

    def test_booking_by_mail(self):
        mail_env = patch.dict(os.environ, {"LOGDB_SMTP_HOST": "relay.example.org", "LOGDB_MAIL_FROM": "logbook@example.org"})
        mail_env.start()
        self.addCleanup(mail_env.stop)
        self.con.execute("UPDATE users SET email=lower(username)||'@example.org'")
        self.con.commit()
        for bad in ({"booking_mode": "mail"}, {"booking_mode": "x"}, {"booking_mode": "mail", "booking_mail_to": "nope",
                                                                      "booking_mail_text": "x"}):
            self.assertEqual(self.client.put("/api/admin/places/1", json=bad).status_code, 400, bad)
        self.assertEqual(self.client.put("/api/admin/places/1", json={
            "booking_mode": "mail", "booking_provider": "Unmanned Valley", "booking_lead_hours": 24,
            "booking_mail_to": "ops@example.org", "booking_mail_text": "Please book {place} on {date} {start}-{end} for {teams}."
        }).status_code, 200)
        self.sign_in(2)
        self.assertEqual(self.client.get("/api/operations/options").json["places"][0]["booking"],
                         {"mode": "mail", "provider": "Unmanned Valley", "lead_hours": 24, "mail_to": "ops@example.org"})
        bid = self.plan(goal="Wind").json["id"]
        request, = self.booking_mails()
        self.assertEqual((request["to_addr"], request["reply_to"]), ("ops@example.org", "pic@example.org"))
        self.assertTrue(request["subject"].startswith("Booking request: Field, "), request["subject"])
        self.assertIn(f"Please book Field on {DAY} 10:00-14:00 for Pilot (Research).", request["body"])
        self.assertIn(f"\nType: request\nLocation: Field\nBooked at: Unmanned Valley\nDate: {DAY}\nStart: 10:00\nEnd: 14:00\n"
                      f"Book by: {(datetime.date.fromisoformat(DAY) - datetime.timedelta(days=1)).isoformat()} 10:00\n",
                      request["body"])
        self.assertTrue(request["body"].endswith(f"Reference: S{bid}"), request["body"])
        self.assertNotIn("Time zone:", request["body"])  # no country set for the location
        self.con.execute("UPDATE places SET country='BE' WHERE id=1")  # another country than the agenda's zone
        self.assertIn("\nTime zone: Europe/Amsterdam\n", operations.booking_mail(self.con, bid, "reminder")[1])
        self.con.execute("UPDATE places SET country='NL' WHERE id=1")
        self.assertNotIn("Time zone:", operations.booking_mail(self.con, bid, "reminder")[1])
        self.con.commit()
        ext = self.client.get(f"/api/operations/{bid}").json["external"]
        self.assertEqual((ext["status"], ext["ref"], ext["can_set"]), ("requested", None, False))
        self.assertEqual(self.client.post(f"/api/operations/{bid}/external", json={"ref": "48213"}).status_code, 403)
        self.sign_in(5)  # joins: the mailbox hears of it
        self.client.post(f"/api/operations/{bid}/teams", json={"projects": [1]})
        self.assertTrue(self.booking_mails()[-1]["subject"].startswith("Booking update: "))
        self.sign_in(1)
        self.assertEqual(self.client.post(f"/api/operations/{bid}/external", json={"ref": " 48213 "}).status_code, 200)
        ext = self.client.get(f"/api/operations/{bid}").json["external"]
        self.assertEqual((ext["status"], ext["ref"], ext["booked_by"]), ("booked", "48213", "Admin"))
        self.assertEqual(self.client.get(f"/api/bookings?from={DAY}&to={DAY}").json["bookings"][0]["operation"]["external_ref"], "48213")
        # a new time: requested again, with a change mail that names the booking number
        self.assertEqual(self.client.put(f"/api/operations/{bid}", json={"start": f"{DAY}T11:00", "end": f"{DAY}T15:00"}).status_code, 200)
        change = self.booking_mails()[-1]
        self.assertIn("(booking number 48213)", change["body"])
        self.assertEqual(self.client.get(f"/api/operations/{bid}").json["external"]["status"], "requested")
        # the reminder: once, 12 h before the deadline (the day before at 11:00)
        day_before = (datetime.date.fromisoformat(DAY) - datetime.timedelta(days=1)).isoformat()
        two_before = (datetime.date.fromisoformat(day_before) - datetime.timedelta(days=1)).isoformat()
        operations.queue_booking_reminders(self.con, now=f"{two_before}T22:59")
        self.assertEqual(len(self.booking_mails()), 3)
        operations.queue_booking_reminders(self.con, now=f"{two_before}T23:00")
        self.assertTrue(self.booking_mails()[-1]["subject"].startswith("Not booked yet: "))
        operations.queue_booking_reminders(self.con, now=f"{day_before}T09:00")
        self.assertEqual(len(self.booking_mails()), 4)  # once
        # the last team leaves: cancelled
        for team in self.client.get(f"/api/operations/{bid}").json["teams"]:
            self.client.delete(f"/api/operations/teams/{team['id']}")
        self.assertTrue(self.booking_mails()[-1]["subject"].startswith("Booking cancelled: "))
        self.assertEqual(self.client.get(f"/api/operations/{bid}").status_code, 404)

    def test_booking_request_waits_for_approval(self):
        mail_env = patch.dict(os.environ, {"LOGDB_SMTP_HOST": "relay.example.org", "LOGDB_MAIL_FROM": "logbook@example.org"})
        mail_env.start()
        self.addCleanup(mail_env.stop)
        self.client.put("/api/admin/places/1", json={"booking_mode": "mail", "booking_mail_to": "ops@example.org",
                                                     "booking_mail_text": "Book it."})
        waits = self.qualification("A1/A3", user_id=2)
        self.set_rules([{"type_id": waits, "needs_approval": True}])
        self.sign_in(2)
        bid = self.plan().json["id"]
        self.assertEqual(self.booking_mails(), [])  # nothing to book before a team is approved
        self.sign_in(1)
        team = self.client.get("/api/admin/operation-pending").json["teams"][0]["id"]
        self.client.post(f"/api/operations/teams/{team}/review", json={"action": "approve"})
        self.assertEqual([m["subject"][:16] for m in self.booking_mails()], ["Booking request:"])
        self.assertEqual(self.client.get(f"/api/operations/{bid}").json["external"]["status"], "requested")
        sent = []
        smtp = type("FakeSMTP", (), {"send_message": lambda _, m: sent.append(m), "quit": lambda _: None})()
        import mail
        mail.send_due(self.con, lambda: smtp)
        msg = next(m for m in sent if m["Subject"].startswith("Booking request:"))
        self.assertEqual(msg["To"], "ops@example.org")
        self.assertIn(mail.EXTERNAL["booking"], msg.get_body(("plain",)).get_content())
        self.assertIn("Request details:", msg.get_body(("html",)).get_content())


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
