import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import config
from .test_historical import AppTests


class MergeIdentityTests(AppTests):
    def merge_fixture(self):
        self.sources(1, None)
        return self.client.get("/api/logbook").json[0]

    def merge_row(self, template, identity, seconds, duration=100, **changes):
        start = datetime(2010, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)
        row = deepcopy(template)
        row.update(flight_id=identity, log_id=identity,
                   takeoff_utc=start.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                   landing_utc=(start + timedelta(seconds=duration)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                   duration_s=duration)
        row.update(changes)
        return row

    def test_nonoverlapping_flights_do_not_compare_aircraft(self):
        template = self.merge_fixture()
        rows = [self.merge_row(template, i + 1, i * 1000) for i in range(1000)]
        with patch.dict(self.ns, _same_aircraft=lambda *args: self.fail("Expired group compared")):
            self.assertEqual(len(self.ns["merge_sources"](rows)), 1000)

    def test_overlap_slack_boundary_chains_and_closest_group_preference(self):
        template = self.merge_fixture()
        merge = self.ns["merge_sources"]
        for gap, expected in ((160, 1), (160.001, 2)):
            rows = [self.merge_row(template, 1, 0, log_type="onboard_sd"),
                    self.merge_row(template, 2, gap, log_type="gcs")]
            self.assertEqual(len(merge(rows)), expected)
        # Back-to-back logs of one type (restarts, battery swaps, consecutive PX4 files) are separate flights.
        rows = [self.merge_row(template, i + 1, i * 150) for i in range(5)]
        self.assertEqual(len(merge(rows)), 5)
        rows = [self.merge_row(template, 1, 0),
                self.merge_row(template, 2, 1, log_id=1),
                self.merge_row(template, 3, 2)]
        groups = merge(rows)
        self.assertEqual({tuple(s["flight_id"] for s in g["sources"]) for g in groups}, {(1,), (2, 3)})

    def test_same_type_recordings_merge_only_when_they_cover_the_same_time(self):
        template = self.merge_fixture()
        merge = self.ns["merge_sources"]
        # Two ground stations recording the same flight.
        rows = [self.merge_row(template, 1, 0, duration=300, log_type="gcs"),
                self.merge_row(template, 2, 2, duration=290, log_type="gcs")]
        self.assertEqual(len(merge(rows)), 1)
        # Three SD logs 30 and 20 minutes apart, each starting within the slack of the previous landing.
        rows = [self.merge_row(template, 1, 0, duration=1790, log_type="onboard_sd"),
                self.merge_row(template, 2, 1800, duration=1190, log_type="onboard_sd"),
                self.merge_row(template, 3, 3000, duration=600, log_type="onboard_sd")]
        self.assertEqual(len(merge(rows)), 3)
        # A ground station covering both SD logs joins only one of them.
        rows.append(self.merge_row(template, 4, 1, duration=3600, log_type="gcs"))
        groups = merge(rows)
        self.assertEqual(sorted(len(g["sources"]) for g in groups), [1, 1, 2])

    def test_each_recording_joins_its_best_partner(self):
        template = self.merge_fixture()
        # The SD flight takes off first; an earlier short GCS flight from the same GCS log is within the slack,
        # the GCS flight of the same takeoff comes 0.4 s later and must win.
        rows = [self.merge_row(template, 1, 0, duration=5, log_id=10, log_type="gcs"),
                self.merge_row(template, 2, 25.0, duration=18, log_id=20, log_type="onboard_sd"),
                self.merge_row(template, 3, 25.4, duration=16.7, log_id=10, log_type="gcs")]
        groups = self.ns["merge_sources"](rows)
        self.assertEqual({frozenset(s["flight_id"] for s in g["sources"]) for g in groups},
                         {frozenset({1}), frozenset({2, 3})})

    def test_longest_onboard_recording_leads_the_merged_entry(self):
        template = self.merge_fixture()
        rows = [self.merge_row(template, 1, 0, duration=8, log_type="px4_ulog", verdict="ground"),
                self.merge_row(template, 2, 30, duration=184, log_type="px4_ulog", verdict="flight"),
                self.merge_row(template, 3, 29, duration=180, log_type="gcs", verdict="flight")]
        # Consecutive PX4 files 22 s apart are separate; 2 and 3 are the same flight.
        groups = sorted(self.ns["merge_sources"](rows), key=lambda g: g["flight_id"])
        self.assertEqual([(g["flight_id"], g["duration_s"], g["verdict"]) for g in groups],
                         [(1, 8, "ground"), (2, 184, "flight")])
        merged = self.ns["merge_sources"]([self.merge_row(template, 1, 0, duration=10, log_type="gcs"),
                                           self.merge_row(template, 2, 0, duration=40, log_type="onboard_sd"),
                                           self.merge_row(template, 3, 0, duration=100, log_type="onboard_sd",
                                                          verdict="flight")])
        self.assertEqual([g["flight_id"] for g in merged if len(g["sources"]) > 1], [3])

    def test_july_short_gcs_event_does_not_steal_overlapping_sd_flight(self):
        template = self.merge_fixture()
        rows = [
            self.merge_row(template, 3796, 0, duration=1.641, log_id=2430, log_type="gcs"),
            self.merge_row(template, 3797, 36.460, duration=66.134, log_id=2430, log_type="gcs"),
            self.merge_row(template, 3802, 36.973, duration=66.3179, log_id=2431, log_type="onboard_sd"),
        ]
        groups = self.ns["merge_sources"](rows)
        self.assertEqual({frozenset(s["flight_id"] for s in g["sources"]) for g in groups},
                         {frozenset({3796}), frozenset({3797, 3802})})

    def test_equal_timing_rank_preserves_stable_first_group(self):
        template = self.merge_fixture()
        rows = [self.merge_row(template, 1, 0, log_id=1),
                self.merge_row(template, 2, 0, log_id=1),
                self.merge_row(template, 3, 1)]
        groups = self.ns["merge_sources"](rows)
        self.assertEqual({tuple(s["flight_id"] for s in g["sources"]) for g in groups}, {(1, 3), (2,)})

    def test_long_intervals_unknown_dates_and_backwards_fractional_order(self):
        template = self.merge_fixture()
        merge = self.ns["merge_sources"]
        rows = [self.merge_row(template, 1, 0, duration=10000),
                self.merge_row(template, 2, 1000),
                self.merge_row(template, 3, 2000, takeoff_utc=None)]
        self.assertEqual(sorted(len(g["sources"]) for g in merge(rows)), [1, 2])
        # .0001Z sorts before .000Z even though it is chronologically later.
        rows = [self.merge_row(template, 1, 0, duration=0),
                self.merge_row(template, 2, 60.0001, duration=0),
                self.merge_row(template, 3, 60, duration=0)]
        rows[2]["takeoff_utc"] = "2010-01-01T00:01:00.000Z"
        self.assertEqual({tuple(s["flight_id"] for s in g["sources"]) for g in merge(rows)},
                         {(1,), (2, 3)})

    def sources(self, sd_drone, gcs_drone):
        for identity, kind, drone, name in (
                (1, "onboard_sd", sd_drone, "Airframe"),
                (2, "gcs", gcs_drone, "GCS configuration")):
            self.con.execute(
                "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by,project_id,log_type)"
                " VALUES(?,?,?,'test.data','done','now',2,1,?)", (identity, kind, kind, kind))
            self.con.execute(
                "INSERT INTO aircraft(id,log_id,drone_id,name,ac_id) VALUES(?,?,?,?,7)",
                (identity, identity, drone, name))
            self.con.execute(
                "INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc,landing_utc,"
                "takeoff_lat,takeoff_lon,landed,verdict)"
                " VALUES(?,?,1,100,'2010-01-01T12:00:00.000Z','2010-01-01T12:01:40.000Z',52,4,1,'flight')",
                (identity, identity))
        self.con.commit()

    def test_linked_source_identity_survives_on_both_apis_and_detail(self):
        for sd, gcs in ((None, 1), (1, None), (1, 1)):
            with self.subTest(sd=sd, gcs=gcs):
                self.con.execute("DELETE FROM logs")
                self.sources(sd, gcs)
                for endpoint in ("/api/logbook", "/api/stats"):
                    result = self.client.get(endpoint).json
                    rows = result["flights"] if endpoint == "/api/stats" else result
                    self.assertEqual(len(rows), 1)
                    self.assertEqual((rows[0]["aircraft"], rows[0]["drone_id"], rows[0]["aircraft_uid"]),
                                     ("Drone", 1, "UID"))
                detail = self.client.get("/api/flights/1/detail").json["entry"]
                self.assertEqual(detail["flight_id"], 1)
                self.assertEqual(detail["drone_id"], 1)
                self.assertEqual(len(detail["sources"]), 2)

    def test_conflicting_fleet_identities_are_not_merged(self):
        self.con.execute("INSERT INTO drones(id,name,unique_id,created_at) VALUES(2,'Other Drone','OTHER','now')")
        self.sources(1, 2)
        rows = self.client.get("/api/logbook").json
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["drone_id"] for r in rows}, {1, 2})
        self.assertTrue(all(len(r["sources"]) == 1 for r in rows))

    def test_unlinked_sources_keep_existing_name_without_inventing_identity(self):
        self.sources(None, None)
        rows = self.client.get("/api/logbook").json
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["aircraft"], "GCS configuration")
        self.assertIsNone(rows[0]["drone_id"])
        self.assertIsNone(rows[0]["aircraft_uid"])

    def test_hiding_unlinked_filters_lists_totals_and_location_logs_not_paper(self):
        self.sources(1, None)
        self.con.execute(
            "UPDATE flights SET takeoff_utc='2010-01-02T12:00:00.000Z',"
            " landing_utc='2010-01-02T12:01:40.000Z' WHERE id=2")
        self.con.commit()
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(config.ALL["HIDE_UNLINKED_AIRCRAFT"][0], "0")
        for value, count, flights, seconds in (("0", 3, 7, 2030), ("1", 2, 6, 1930)):
            with patch.dict(os.environ, LOGDB_HIDE_UNLINKED_AIRCRAFT=value):
                for uid in (1, 2, 3):
                    self.sign_in(uid)
                    for endpoint in ("/api/logbook", "/api/stats", "/api/detail/locations/1"):
                        payload = self.client.get(endpoint).json
                        rows = payload if isinstance(payload, list) else payload["flights"]
                        self.assertEqual(len(rows), count, (value, uid, endpoint))
                        self.assertEqual(sum(r.get("flight_count", 1) for r in rows), flights)
                        self.assertEqual(sum(r["duration_s"] for r in rows), seconds)
                    place = self.client.get("/api/places").json["places"][0]
                    self.assertEqual((place["flights"], place["seconds"]), (flights, seconds))
                detail = self.client.get("/api/detail/locations/1").json
                self.assertEqual({r["id"] for r in detail["logs"]}, {1} if value == "1" else {1, 2})

    def test_hiding_keeps_merged_flights_when_either_source_is_linked(self):
        with patch.dict(os.environ, LOGDB_HIDE_UNLINKED_AIRCRAFT="1"):
            for sd, gcs in ((1, None), (None, 1), (1, 1)):
                self.con.execute("DELETE FROM logs")
                self.sources(sd, gcs)
                rows = self.client.get("/api/logbook").json
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["drone_id"], 1)
                self.assertEqual(len(rows[0]["sources"]), 2)
                self.assertEqual(self.client.get("/api/flights/1/detail").status_code, 200)

    def test_hiding_preserves_direct_details_and_existing_access_checks(self):
        self.sources(None, None)
        self.con.execute("UPDATE flights SET project_id=1")
        self.con.commit()
        with patch.dict(os.environ, LOGDB_HIDE_UNLINKED_AIRCRAFT="1"):
            for uid in (1, 2, 3):
                self.sign_in(uid)
                self.assertEqual(self.client.get("/api/logbook").json, [])
                self.assertEqual(self.client.get("/api/stats").json["flights"], [])
                self.assertEqual(self.client.get("/api/flights/1/detail").status_code, 200)
                self.assertEqual(self.client.get("/api/logs/1").status_code, 200)
                self.assertEqual(len(self.client.get("/api/detail/projects/1").json["flights"]), 1)
            self.sign_in(1)
            location = self.client.get("/api/detail/locations/1").json
            self.assertEqual((location["flights"], location["logs"]), ([], []))
            places = self.client.get("/api/places").json
            self.assertEqual(places["points"], [])
            self.assertEqual(places["places"][0]["flights"], 0)
            self.sign_in(4)
            self.assertEqual(self.client.get("/api/flights/1/detail").status_code, 404)
            self.assertEqual(self.client.get("/api/logs/1").status_code, 404)
            self.assertEqual(self.client.get("/api/detail/locations/1").status_code, 404)

    def test_aircraft_edit_data_remains_available_with_notice_hidden(self):
        self.sources(None, None)
        self.sign_in(1)
        with patch.dict(os.environ, LOGDB_HIDE_UNLINKED_AIRCRAFT="1"):
            payload = self.client.get("/api/drones").json
            self.assertTrue(payload["hide_unlinked_aircraft"])
            self.assertTrue(payload["unlinked"])
            self.assertEqual(self.client.get("/api/admin/drones").json["unlinked"], payload["unlinked"])
            self.sign_in(3)
            self.assertEqual(self.client.get("/api/drones").json["unlinked"], [])
            self.assertEqual(self.client.get("/api/admin/drones").status_code, 403)
        with patch.dict(os.environ, LOGDB_HIDE_UNLINKED_AIRCRAFT="0"):
            self.assertFalse(self.client.get("/api/drones").json["hide_unlinked_aircraft"])
