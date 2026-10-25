from .test_historical import AppTests


class LinkFixtures(AppTests):
    def setUp(self):
        super().setUp()
        self.body.update(kind="flight", flight_count=1, takeoff_time="12:02", duration_minutes="30",
                         reference="F00001")

    def flight(self, ident, start, duration=100, kind="onboard_sd", log=None, idx=1, project=1, verdict="flight"):
        log = log or ident
        if not self.con.execute("SELECT 1 FROM logs WHERE id=?", (log,)).fetchone():
            self.con.execute(
                "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by,project_id,log_type)"
                " VALUES(?,?,?,'test.data','done','now',2,?,?)", (log, f"log{log}", f"sha{log}", project, kind))
            self.con.execute("INSERT INTO aircraft(id,log_id,drone_id,name,ac_id) VALUES(?,?,1,'Drone',7)", (log, log))
        end = self.at(start, duration)
        self.con.execute(
            "INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc,landing_utc,takeoff_lat,takeoff_lon,"
            "landed,verdict,has_gps_position) VALUES(?,?,?,?,?,?,52,4,1,?,1)",
            (ident, log, idx, duration, self.at(start), end, verdict))
        self.con.commit()

    @staticmethod
    def at(start, plus=0):
        h, m, s = map(int, start.split(":"))
        total = h * 3600 + m * 60 + s + plus
        return f"2010-01-01T{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}.000Z"

    def logbook(self):
        return self.client.get("/api/logbook").json

    def relink(self):
        self.ns["links"].auto_link(self.con, "now")
        self.con.commit()

    def decide(self, record_id, flight_ids, same):
        return self.client.post("/api/links", json=dict(record_id=record_id, flight_ids=flight_ids, same=same))



class FlightLinkTests(LinkFixtures):
    def test_clear_match_links_automatically_and_counts_as_the_logs(self):
        self.flight(1, "12:00:00", kind="onboard_sd")
        self.flight(2, "12:00:01", duration=99, kind="gcs")
        record = self.create().json["id"]
        rows = self.logbook()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["source_type"], row["flight_count"], row["duration_s"]), ("telemetry", 1, 100))
        self.assertEqual(row["reference"], "F00001")
        self.assertEqual([(r["id"], r["origin"]) for r in row["linked_records"]], [(record, "auto")])
        self.assertEqual(sorted(s["flight_id"] for s in row["sources"]), [1, 2])
        self.assertEqual(row["conflicts"], [])
        compact = self.client.get("/api/logbook?compact=1").json[0]
        self.assertEqual(compact["linked_records"][0]["id"], record)
        stats = self.client.get("/api/detail/aircraft/1").json["flights"]
        self.assertEqual(len(stats), 1)

    def test_ambiguous_candidates_are_only_suggested(self):
        self.flight(1, "12:00:00")
        self.flight(2, "12:02:40")  # back-to-back logs, 60 s apart
        record = self.create().json["id"]
        rows = self.logbook()
        self.assertEqual(len(rows), 3)
        paper = next(r for r in rows if r["source_type"] == "paper")
        self.assertEqual(paper["manual_id"], record)
        self.assertEqual(sorted(d["flight_ids"][0] for d in paper["possible_duplicates"]), [1, 2])
        # One paper line for a flight with a battery swap: link both logged flights by hand.
        for fid in (1, 2):
            self.assertEqual(self.decide(record, [fid], True).status_code, 200)
        rows = self.logbook()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["flight_count"], rows[0]["duration_s"]), (2, 200))
        self.assertEqual(rows[0]["linked_records"][0]["origin"], "manual")

    def test_unlink_is_remembered_and_new_uploads_do_not_relink(self):
        self.flight(1, "12:00:00")
        record = self.create().json["id"]
        self.assertEqual(len(self.logbook()), 1)
        self.assertEqual(self.decide(record, [1], False).status_code, 200)
        rows = self.logbook()
        self.assertEqual(len(rows), 2)
        paper = next(r for r in rows if r["source_type"] == "paper")
        self.assertEqual(paper["possible_duplicates"], [])
        self.flight(2, "12:00:02", kind="gcs")  # another recording of the same flight arrives later
        self.relink()
        self.assertEqual(sum(r["source_type"] == "paper" for r in self.logbook()), 1)
        # A second paper record of another flight does not steal the link either.
        self.create(takeoff_time="12:01", reference="F00002", duration_minutes="31")
        self.assertEqual(sum(r["source_type"] == "paper" for r in self.logbook()), 2)

    def test_reupload_of_a_record_or_reparse_keeps_links(self):
        self.flight(1, "12:00:00")
        record = self.create().json["id"]
        # Re-parse: flights get new ids but keep log, ac_id and flight number.
        self.con.execute("DELETE FROM aircraft WHERE log_id=1")
        self.con.execute("INSERT INTO aircraft(id,log_id,drone_id,name,ac_id) VALUES(9,1,1,'Drone',7)")
        self.con.execute(
            "INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc,landing_utc,takeoff_lat,takeoff_lon,"
            "landed,verdict,has_gps_position) VALUES(19,9,1,100,?,?,52,4,1,'flight',1)",
            (self.at("12:00:00"), self.at("12:00:00", 100)))
        self.con.commit()
        rows = self.logbook()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["linked_records"][0]["id"], record)
        # Editing the record re-matches automatic links; the same values link again.
        response = self.client.put(f"/api/historical/{record}", json={**self.body, "comment": "checked"})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(len(self.logbook()), 1)
        # A re-parse that renumbers flights (takeoff moved) makes the link stale instead of wrong.
        self.con.execute("UPDATE flights SET takeoff_utc=? WHERE id=19", (self.at("12:30:00"),))
        self.con.commit()
        self.assertEqual(len(self.logbook()), 2)

    def test_conflicts_show_the_log_value_and_the_paper_value(self):
        self.flight(1, "12:00:00")
        self.con.execute("UPDATE flights SET pic_user_id=5 WHERE id=1")
        self.con.commit()
        self.create(gcs_user_id=3)
        row = self.logbook()[0]
        self.assertEqual(row["pic_user"]["id"], 5)
        self.assertEqual([(c["field"], c["paper"], c["log"]) for c in row["conflicts"]],
                         [("PIC", "Pilot", "Uploader")])

    def test_link_permissions(self):
        self.flight(1, "12:00:00")
        self.flight(2, "15:00:00", log=2)
        record = self.create().json["id"]
        self.sign_in(3)  # view-only member
        self.assertEqual(self.decide(record, [2], True).status_code, 403)
        self.sign_in(4)  # outsider
        self.assertEqual(self.decide(record, [2], True).status_code, 404)
        self.sign_in(2)
        self.assertEqual(self.decide(record, [2], "yes").status_code, 400)
        self.assertEqual(self.decide(record, [999], True).status_code, 404)
        self.assertEqual(self.decide(record, [2], True).status_code, 200)
        self.assertEqual(len(self.logbook()), 1)

    def test_ground_runs_and_deleted_records_do_not_link(self):
        self.flight(1, "12:00:00", verdict="ground")
        record = self.create().json["id"]
        self.assertEqual(len(self.logbook()), 2)
        self.assertEqual(self.client.delete(f"/api/historical/{record}").status_code, 200)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM flight_links").fetchone()[0], 0)


class SourceLinkTests(LinkFixtures):
    def join(self, flight_ids, same=True):
        return self.client.post("/api/sources/links", json=dict(flight_ids=flight_ids, same=same))

    def entries(self):
        return sorted(sorted(s["flight_id"] for s in r["sources"]) for r in self.logbook())

    def test_split_a_wrong_merge_and_join_it_again(self):
        self.flight(1, "12:00:00", kind="onboard_sd")
        self.flight(2, "12:00:01", duration=99, kind="gcs")
        self.assertEqual(self.entries(), [[1, 2]])
        self.assertEqual(self.join([2, 1], same=False).status_code, 200)
        self.assertEqual(self.entries(), [[1], [2]])
        # A re-parse gives new flight ids; the decision is kept by log, ac_id and flight number.
        self.con.execute("DELETE FROM aircraft WHERE log_id=2")
        self.con.execute("INSERT INTO aircraft(id,log_id,drone_id,name,ac_id) VALUES(9,2,1,'Drone',7)")
        self.con.execute(
            "INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc,landing_utc,takeoff_lat,takeoff_lon,"
            "landed,verdict,has_gps_position) VALUES(29,9,1,99,?,?,52,4,1,'flight',1)",
            (self.at("12:00:01"), self.at("12:00:01", 99)))
        self.con.commit()
        self.assertEqual(self.entries(), [[1], [29]])
        self.assertEqual(self.join([1, 29]).status_code, 200)  # the latest decision wins
        self.assertEqual(self.entries(), [[1, 29]])

    def test_join_recordings_the_automatic_merge_missed(self):
        self.flight(1, "12:00:00", kind="onboard_sd")
        self.flight(2, "12:05:00", kind="gcs")  # e.g. a ground station clock five minutes off
        self.assertEqual(self.entries(), [[1], [2]])
        self.assertEqual(self.join([1, 2]).status_code, 200)
        rows = self.logbook()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["flight_count"], 1)

    def test_join_validation_and_permissions(self):
        self.flight(1, "12:00:00")
        self.flight(2, "12:30:00", log=1, idx=2)
        self.flight(3, "13:00:00")
        self.assertEqual(self.join([1, 2]).status_code, 400)  # same log
        self.assertEqual(self.join([1]).status_code, 400)
        self.assertEqual(self.join([1, 1]).status_code, 400)
        self.con.execute("INSERT INTO drones(id,name,unique_id,created_at) VALUES (2,'Other','UID2','now')")
        self.con.execute("UPDATE aircraft SET drone_id=2 WHERE log_id=3")
        self.con.commit()
        self.assertEqual(self.join([1, 3]).status_code, 400)  # different fleet aircraft
        self.sign_in(3)
        self.assertEqual(self.join([1, 3], same=False).status_code, 403)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM source_links").fetchone()[0], 0)

    def test_paper_record_and_joined_logs_count_once(self):
        self.flight(1, "12:00:00", kind="onboard_sd")
        self.flight(2, "12:04:00", kind="onboard_sd")
        record = self.create().json["id"]
        self.assertEqual(self.decide(record, [1, 2], True).status_code, 200)
        self.assertEqual(self.logbook()[0]["flight_count"], 2)  # one paper line, two logged flights
        self.assertEqual(self.join([1, 2]).status_code, 200)    # ...that turn out to be one flight
        row = self.logbook()[0]
        self.assertEqual((len(self.logbook()), row["flight_count"], row["reference"]), (1, 1, "F00001"))
