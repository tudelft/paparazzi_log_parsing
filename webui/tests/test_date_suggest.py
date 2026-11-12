from datetime import datetime, timedelta

from .test_historical import AppTests


class DateSuggestionTests(AppTests):
    """SD logs with an unset clock (1980) keep the time of day; a unique GCS match of the same aircraft,
    duration and time of day suggests the date (pairs like SD 718 -> GCS 713 on 2020-07-22)."""

    def flight(self, ident, kind, takeoff, duration, log=None, drone=1, idx=1):
        log = log or ident
        start = datetime.fromisoformat(takeoff)
        if not self.con.execute("SELECT 1 FROM logs WHERE id=?", (log,)).fetchone():
            self.con.execute(
                "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by,project_id,log_type,start_utc)"
                " VALUES(?,?,?,'x.data','done','now',2,1,?,?)",
                (log, f"log{log}", f"sha{log}", kind, (start - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")))
            self.con.execute("INSERT INTO aircraft(id,log_id,drone_id,name,ac_id) VALUES(?,?,?,'Drone',7)",
                             (log, log, drone))
        iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        self.con.execute(
            "INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc,landing_utc,takeoff_lat,takeoff_lon,landed,"
            "verdict,has_gps_position) VALUES(?,?,?,?,?,?,52,4,1,'flight',1)",
            (ident, log, idx, duration, iso(start), iso(start + timedelta(seconds=duration))))
        self.con.commit()

    def suggestions(self):
        return self.ns["date_suggestions"](self.con)

    def test_unique_gcs_match_suggests_the_date_and_accepting_sets_it(self):
        self.flight(718, "onboard_sd", "1980-01-06T10:00:12", 300)
        self.flight(713, "gcs", "2020-07-22T10:00:00", 300.4)
        self.assertEqual(len(self.client.get("/api/logbook").json), 2)  # 1980 time is not matched
        detail = self.client.get("/api/logs/718").json
        suggestion = detail["date_suggestion"]
        self.assertEqual((suggestion["date"], suggestion["gcs_log_id"], suggestion["offset_s"]),
                         ("2020-07-22", 713, -12.0))
        self.assertIsNone(self.client.get("/api/logs/713").json["date_suggestion"])
        self.sign_in(3)  # view-only member: no suggestion offered, cannot accept
        self.assertIsNone(self.client.get("/api/logs/718").json["date_suggestion"])
        self.assertEqual(self.client.post("/api/logs/718/date/suggestion").status_code, 403)
        self.sign_in(2)
        self.assertEqual(self.client.post("/api/logs/718/date/suggestion").json["date_override"], "2020-07-22")
        row = self.con.execute("SELECT date_override, date_override_by, date_override_note FROM logs WHERE id=718").fetchone()
        self.assertEqual((row[0], row[1]), ("2020-07-22", 2))
        self.assertIn("GCS log 713", row[2])
        detail = self.client.get("/api/logs/718").json
        self.assertEqual((detail["date_suggestion"], detail["date_override_by_name"]), (None, "Pilot"))
        rows = self.client.get("/api/logbook").json  # with the date set, SD and GCS are one flight
        self.assertEqual([sorted(s["flight_id"] for s in r["sources"]) for r in rows], [[713, 718]])
        self.assertEqual(self.client.post("/api/logs/718/date/suggestion").status_code, 409)

    def test_estimated_date_is_confirmed_by_the_gcs_match(self):
        # The firmware estimate put the SD log a week off; estimated dates do not merge, so the flight counted twice.
        self.flight(718, "onboard_sd", "2020-07-15T10:00:12", 300)
        self.con.execute("UPDATE logs SET date_estimated=1 WHERE id=718")
        self.flight(713, "gcs", "2020-07-22T10:00:00", 300.4)
        self.assertEqual(len(self.client.get("/api/logbook").json), 2)
        suggestion = self.client.get("/api/logs/718").json["date_suggestion"]
        self.assertEqual((suggestion["date"], suggestion["gcs_log_id"]), ("2020-07-22", 713))
        self.assertEqual(self.client.post("/api/logs/718/date/suggestion").status_code, 200)
        rows = self.client.get("/api/logbook").json
        self.assertEqual([sorted(s["flight_id"] for s in r["sources"]) for r in rows], [[713, 718]])
        self.assertEqual(rows[0]["takeoff_utc"][:10], "2020-07-22")

    def test_an_estimated_log_detected_as_gcs_is_still_the_onboard_side(self):
        # SD log 718 was renamed log64_<aircraft>.data and detected as GCS; its estimated date was already right.
        self.flight(718, "gcs", "2020-07-22T09:34:15.810", 1296.0)
        self.con.execute("UPDATE logs SET date_estimated=1 WHERE id=718")
        self.flight(713, "gcs", "2020-07-22T09:33:47.275", 1295.891)
        self.assertEqual(len(self.client.get("/api/logbook").json), 2)
        suggestion = self.client.get("/api/logs/718").json["date_suggestion"]
        self.assertEqual((suggestion["date"], suggestion["gcs_log_id"]), ("2020-07-22", 713))
        self.assertIsNone(self.client.get("/api/logs/713").json["date_suggestion"])
        self.assertEqual(self.client.post("/api/logs/718/date/suggestion").status_code, 200)
        rows = self.client.get("/api/logbook").json
        self.assertEqual([sorted(s["flight_id"] for s in r["sources"]) for r in rows], [[713, 718]])

    def test_no_suggestion_when_short_ambiguous_taken_or_inconsistent(self):
        cases = {
            "short flight": [(1, "onboard_sd", "1980-01-06T10:00:12", 45), (2, "gcs", "2020-07-22T10:00:00", 45)],
            "two GCS days": [(1, "onboard_sd", "1980-01-06T10:00:12", 300), (2, "gcs", "2020-07-22T10:00:00", 300),
                             (3, "gcs", "2020-08-11T10:00:05", 300)],
            "GCS already paired": [(1, "onboard_sd", "1980-01-06T10:00:12", 300), (2, "gcs", "2020-07-22T10:00:00", 300),
                                   (3, "onboard_sd", "2020-07-22T10:00:01", 299)],
            "other aircraft": [(1, "onboard_sd", "1980-01-06T10:00:12", 300), (2, "gcs", "2020-07-22T10:00:00", 300)],
            "two claimants": [(1, "onboard_sd", "1980-01-06T10:00:12", 300), (2, "onboard_sd", "1980-01-13T10:00:10", 300),
                              (3, "gcs", "2020-07-22T10:00:00", 300)],
            "time of day differs": [(1, "onboard_sd", "1980-01-06T10:01:00", 300), (2, "gcs", "2020-07-22T10:00:00", 300)],
        }
        for name, flights in cases.items():
            with self.subTest(name):
                for table in ("flights", "aircraft", "logs"):
                    self.con.execute(f"DELETE FROM {table}")
                self.con.commit()
                for ident, kind, takeoff, duration in flights:
                    drone = 2 if name == "other aircraft" and kind == "gcs" else 1
                    if drone == 2:
                        self.con.execute("INSERT OR IGNORE INTO drones(id,name,unique_id,created_at)"
                                         " VALUES (2,'Other','UID2','now')")
                    self.flight(ident, kind, takeoff, duration, drone=drone)
                self.assertEqual(self.suggestions(), {})

    def test_all_flights_of_a_log_must_agree(self):
        self.flight(1, "onboard_sd", "1980-01-06T10:00:12", 300)
        self.flight(2, "onboard_sd", "1980-01-06T11:00:12", 200, log=1, idx=2)
        self.flight(3, "gcs", "2020-07-22T10:00:00", 300)
        self.flight(4, "gcs", "2020-07-23T11:00:00", 200)
        self.assertEqual(self.suggestions(), {})
        self.con.execute("UPDATE flights SET takeoff_utc='2020-07-22T11:00:00.000Z', landing_utc='2020-07-22T11:03:20.000Z'"
                         " WHERE id=4")
        self.con.commit()
        self.assertEqual(self.suggestions()[1]["flights"], 2)

    def test_undated_recordings_are_not_merged_by_time(self):
        self.flight(1, "onboard_sd", "1980-01-06T10:00:00", 300)
        self.flight(2, "gcs", "1980-01-06T10:00:01", 300)
        self.assertEqual(len(self.client.get("/api/logbook").json), 2)
        self.assertEqual(self.client.post("/api/sources/links", json={"flight_ids": [1, 2], "same": True}).status_code, 200)
        self.assertEqual(len(self.client.get("/api/logbook").json), 1)  # a person can still join them

    def test_admin_lists_and_accepts_all(self):
        self.flight(718, "onboard_sd", "1980-01-06T10:00:12", 300)
        self.flight(713, "gcs", "2020-07-22T10:00:00", 300)
        self.flight(750, "onboard_sd", "1980-01-06T14:30:11", 600)
        self.flight(751, "gcs", "2020-11-20T14:30:00", 600.2)
        self.assertEqual(self.client.get("/api/admin/date-suggestions").status_code, 403)
        self.sign_in(1)
        listed = self.client.get("/api/admin/date-suggestions").json["suggestions"]
        self.assertEqual([(s["log_id"], s["date"]) for s in listed], [(718, "2020-07-22"), (750, "2020-11-20")])
        self.assertEqual(self.client.post("/api/admin/date-suggestions").json["accepted"], 2)
        self.assertEqual([tuple(r) for r in self.con.execute(
            "SELECT id, date_override, date_override_by FROM logs WHERE date_override IS NOT NULL ORDER BY id")],
            [(718, "2020-07-22", 1), (750, "2020-11-20", 1)])
        self.assertEqual(self.client.get("/api/admin/date-suggestions").json["suggestions"], [])


class NameDateSuggestionTests(AppTests):
    """Logs without a usable date get the date in their file or folder name, else that of the same aircraft's
    agreeing dated neighbours in the same upload folder."""

    def log(self, ident, name, start="1980-01-06T10:00:00.000Z", drone=1, kind="betaflight"):
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by,project_id,log_type,start_utc)"
            " VALUES(?,?,?,'x.data','done','now',2,1,?,?)", (ident, name, f"sha{ident}", kind, start))
        self.con.execute("INSERT INTO aircraft(id,log_id,drone_id,name,ac_id) VALUES(?,?,?,'Drone',7)", (ident, ident, drone))
        self.con.commit()

    def suggestions(self):
        return self.ns["date_suggestions"](self.con)

    def test_dates_in_file_and_folder_names(self):
        self.log(1, "DarkO 1/2026-04-29/LOG00317.BFL")
        self.log(2, "sd/24_05_13__10_22_33_SD.data")
        self.log(3, "flights 20250102/LOG1.BFL")
        self.log(4, "2099-01-01/LOG2.BFL")          # in the future: not plausible
        self.log(5, "LOG00318.BFL")                 # no date anywhere, no neighbours
        self.log(6, "2026-04-29/LOG3.BFL", start="2026-04-28T10:00:00.000Z")  # already dated
        self.log(7, "sd/80_01_08__17_08_26_SD.data")   # unset clock: the time is not a date
        self.log(8, "sd/run_80_01_08_17_08_26.data")
        self.log(9, "sd/flight_26_05_14__09_00_00.data")
        self.log(10, "px4/19_11_29.ulg")                # PX4 file name is the time of day
        self.log(11, "px4/2026-05-15/19_11_29.ulg")     # ... and the folder the date
        self.log(12, "qgc/log_45_2026-3-2-14-25-30.ulg")
        self.log(13, "x/2026041.data")
        found = self.suggestions()
        self.assertEqual({i: (s["date"], s["source"]) for i, s in found.items()},
                         {1: ("2026-04-29", "name"), 2: ("2024-05-13", "name"), 3: ("2025-01-02", "name"),
                          9: ("2026-05-14", "name"), 11: ("2026-05-15", "name"), 12: ("2026-03-02", "name")})
        self.assertEqual(found[1]["reason"], "date in the folder name 2026-04-29")
        self.assertEqual(found[2]["reason"], "date in the file name")

    def test_agreeing_neighbours_of_the_same_aircraft(self):
        self.con.execute("INSERT INTO drones(id,name,unique_id,created_at) VALUES (2,'Other','UID2','now')")
        self.log(14, "DarkO/LOG00314.BFL", start="2026-04-29T09:00:00.000Z")
        self.log(15, "DarkO/LOG00315.BFL", drone=2, start="2026-02-16T09:00:00.000Z")  # other aircraft: ignored
        self.log(16, "DarkO/LOG00316.BFL")
        self.log(17, "DarkO/LOG00317.BFL")
        self.log(18, "DarkO/LOG00318.BFL", start="2026-04-29T11:00:00.000Z")
        found = self.suggestions()
        self.assertEqual({i: (s["date"], s["source"], s["neighbour_log_ids"]) for i, s in found.items()},
                         {16: ("2026-04-29", "folder", [14, 18]), 17: ("2026-04-29", "folder", [14, 18])})
        self.con.execute("UPDATE logs SET start_utc='2026-02-16T09:00:00.000Z' WHERE id=14")
        self.con.commit()
        self.assertEqual(self.suggestions(), {})  # neighbours disagree

    def test_a_gcs_match_comes_first(self):
        DateSuggestionTests.flight(self, 718, "onboard_sd", "1980-01-06T10:00:12", 300)
        DateSuggestionTests.flight(self, 713, "gcs", "2020-07-22T10:00:00", 300)
        self.con.execute("UPDATE logs SET name='2020-07-21/log718' WHERE id=718")
        self.con.commit()
        self.assertEqual((self.suggestions()[718]["source"], self.suggestions()[718]["date"]), ("gcs", "2020-07-22"))

    def test_use_for_all_in_this_folder_and_review_a_selection(self):
        self.log(1, "DarkO 1/2026-04-29/LOG00317.BFL")
        self.log(2, "DarkO 1/2026-04-29/LOG00319.BFL")
        self.log(3, "DarkO 1/2026-04-30/LOG00320.BFL")
        self.log(4, "DarkO 1/2026-05-01/LOG00321.BFL")
        self.assertEqual(self.client.get("/api/logs/1").json["date_suggestion"]["folder_count"], 2)
        self.assertEqual(self.client.post("/api/logs/1/date/suggestion", json={"folder": True}).json["accepted"], 2)
        rows = self.con.execute("SELECT id, date_override, date_override_note FROM logs ORDER BY id").fetchall()
        self.assertEqual([tuple(r[:2]) for r in rows], [(1, "2026-04-29"), (2, "2026-04-29"), (3, None), (4, None)])
        self.assertEqual(rows[0][2], "suggested: date in the folder name 2026-04-29")
        self.sign_in(1)
        self.assertEqual(self.client.post("/api/admin/date-suggestions", json={"log_ids": [4, 99]}).json["accepted"], 1)
        self.assertEqual([s["log_id"] for s in self.client.get("/api/admin/date-suggestions").json["suggestions"]], [3])
        self.assertEqual(self.client.post("/api/admin/date-suggestions", json={"log_ids": "3"}).status_code, 400)


class UndatedProjectTests(AppTests):
    """A flight without a usable date gets the aircraft's project when its timeline is one open-ended period; it
    is assigned again from the dated timeline once a date is set or the timeline changes."""

    def setUp(self):
        super().setUp()
        DateSuggestionTests.flight(self, 718, "betaflight", "1980-01-06T10:00:12", 300)
        self.con.execute("UPDATE logs SET project_id=NULL WHERE id=718")
        self.con.execute("UPDATE flights SET project_id=NULL, project_source=NULL")
        self.con.commit()

    def periods(self, *periods):
        self.sign_in(1)
        r = self.client.put("/api/admin/drones/1/projects",
                            json={"periods": [dict(project_id=p, start_date=a, end_date=b) for p, a, b in periods]})
        self.assertEqual(r.status_code, 200, r.json)

    def project(self):
        return tuple(self.con.execute("SELECT f.project_id, f.project_source, l.project_id FROM flights f"
                                      " JOIN aircraft a ON a.id=f.aircraft_id JOIN logs l ON l.id=a.log_id").fetchone())

    def test_only_open_ended_project_is_used_and_redone(self):
        self.periods((1, None, "2026-01-31"))           # one period, but with an end: not used
        self.assertEqual(self.project(), (None, None, None))
        self.periods((1, None, None))
        self.assertEqual(self.project(), (1, "timeline_undated", 1))
        self.periods((1, None, "2026-01-31"), (2, "2026-02-01", None))   # a second project: unknown again
        self.assertEqual(self.project(), (None, None, None))
        self.sign_in(2)
        self.assertEqual(self.client.put("/api/logs/718/date", json={"date_override": "2026-03-01"}).status_code, 200)
        self.assertEqual(self.project(), (2, "timeline", 2))

    def test_setting_a_date_moves_it_to_the_dated_timeline(self):
        self.periods((1, None, None))
        self.assertEqual(self.project(), (1, "timeline_undated", 1))
        self.con.execute("INSERT INTO drone_projects(drone_id,project_id,start_date,end_date) VALUES (1,2,'2026-02-01',NULL)")
        self.con.execute("UPDATE drone_projects SET end_date='2026-01-31' WHERE project_id=1")
        self.con.commit()
        self.assertEqual(self.client.put("/api/logs/718/date", json={"date_override": "2026-03-01"}).status_code, 200)
        self.assertEqual(self.project(), (2, "timeline", 2))

    def test_a_chosen_project_is_kept(self):
        self.con.execute("UPDATE flights SET project_id=2, project_source='manual'")
        self.con.execute("UPDATE logs SET project_id=2")
        self.con.commit()
        self.periods((1, None, None))
        self.assertEqual(self.project(), (2, "manual", 2))
