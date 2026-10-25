from .test_historical import AppTests


class CoUploaderTests(AppTests):
    raw = b'12.0 7 INFO_MSG "[PFC] goal: wind tunnel"\n' * 100

    def ingest(self, uid, name="flight.data", log=None):
        stage = self.ns["STAGE_DIR"] / f"co-{name}-{uid}"
        stage.mkdir(parents=True, exist_ok=True)
        (stage / name).write_bytes(self.raw)
        log_name = None
        if log is not None:
            log_name = name.replace(".data", ".log")
            (stage / log_name).write_bytes(log)
        iid = self.ns["insert_ingest"](self.con, "pair", name, stage.name, name, log_name, len(self.raw), uid)
        self.ns["ingest_pair"](self.con, self.con.execute("SELECT * FROM ingest WHERE id=?", (iid,)).fetchone())
        return self.con.execute("SELECT * FROM ingest WHERE id=?", (iid,)).fetchone()

    def stored(self):
        log = self.con.execute("SELECT * FROM logs").fetchone()
        for column in ("data_path", "log_path"):
            if log[column]:
                self.addCleanup((self.ns["STORAGE_DIR"] / log[column]).unlink, missing_ok=True)
        return log

    def test_second_uploader_becomes_co_uploader_and_sees_the_log(self):
        self.assertEqual(self.ingest(2)["status"], "stored")
        log = self.stored()
        receipt = self.ingest(4, "copy.data")  # an outsider who has the same file under another name
        self.assertEqual((receipt["status"], receipt["log_id"]), ("duplicate", log["id"]))
        self.assertTrue(receipt["error"].startswith(self.ns["CO_UPLOADED"]))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM logs").fetchone()[0], 1)  # one copy
        self.assertEqual([tuple(r) for r in self.con.execute("SELECT log_id, user_id FROM log_uploaders")],
                         [(log["id"], 4)])
        # The log is in a project the co-uploader is not a member of; they still see it.
        self.con.execute("UPDATE logs SET project_id=2, status='done' WHERE id=?", (log["id"],))
        self.con.execute("INSERT INTO aircraft(id,log_id,drone_id,name,ac_id) VALUES(1,?,1,'Drone',7)", (log["id"],))
        self.con.execute(
            "INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc,landing_utc,takeoff_lat,takeoff_lon,landed,"
            "verdict,has_gps_position) VALUES(1,1,1,60,'2010-01-01T12:00:00.000Z','2010-01-01T12:01:00.000Z',52,4,1,"
            "'flight',1)")
        self.con.commit()
        self.sign_in(4)
        detail = self.client.get(f"/api/logs/{log['id']}").json
        self.assertEqual([c["name"] for c in detail["co_uploaders"]], ["Outsider"])
        self.assertEqual(detail["uploaded_by_name"], "Pilot")
        self.assertFalse(detail["can_delete"])
        self.assertEqual([l["id"] for l in self.client.get(f"/api/logs?log={log['id']}").json["logs"]], [log["id"]])
        self.assertEqual([r["flight_id"] for r in self.client.get("/api/logbook").json], [1])
        self.assertEqual([c["log_id"] for c in self.client.get("/api/status").json["co_uploaded"]], [log["id"]])
        self.assertTrue(self.client.post("/api/logs/check", json={"sha256": [log["sha256"]]}).json
                        ["known"][log["sha256"]]["mine"])
        self.sign_in(3)  # another person without the file and without project access: nothing
        self.assertEqual(self.client.get(f"/api/logs/{log['id']}").status_code, 404)
        self.assertEqual(self.client.get("/api/logbook").json, [])

    def test_check_tells_scripts_to_upload_files_they_did_not_upload_yet(self):
        self.ingest(2)
        log = self.stored()
        self.con.execute("UPDATE logs SET project_id=1 WHERE id=?", (log["id"],))
        self.con.commit()
        self.sign_in(5)  # project member with upload rights, but not an uploader of this file
        known = self.client.post("/api/logs/check", json={"sha256": [log["sha256"]]}).json["known"]
        self.assertFalse(known[log["sha256"]]["mine"])
        self.sign_in(2)
        known = self.client.post("/api/logs/check", json={"sha256": [log["sha256"]]}).json["known"]
        self.assertTrue(known[log["sha256"]]["mine"])

    def test_own_reupload_and_missing_log_from_co_uploader(self):
        self.ingest(2)
        log = self.stored()
        receipt = self.ingest(2, "again.data")
        self.assertEqual(receipt["error"], "already uploaded")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM log_uploaders").fetchone()[0], 0)
        xml = b'<configuration><conf><aircraft ac_id="7" name="Drone"/></conf></configuration>'
        receipt = self.ingest(4, "copy.data", log=xml)
        self.assertEqual(receipt["error"], self.ns["CO_UPLOADED"] + ", missing .log added")
        self.assertIsNotNone(self.stored()["log_path"])

    def test_co_uploader_edits_unassigned_flights_like_the_uploader(self):
        self.ingest(2)
        log = self.stored()
        self.ingest(4, "copy.data")
        self.con.execute("UPDATE logs SET status='done' WHERE id=?", (log["id"],))
        self.con.execute("INSERT INTO aircraft(id,log_id,drone_id,name,ac_id) VALUES(1,?,NULL,'Drone',7)", (log["id"],))
        self.con.execute(
            "INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc,landing_utc,takeoff_lat,takeoff_lon,landed,"
            "verdict,has_gps_position) VALUES(1,1,1,60,'2010-01-01T12:00:00.000Z','2010-01-01T12:01:00.000Z',52,4,1,"
            "'flight',1)")
        self.con.commit()
        self.sign_in(4)
        row = self.client.get("/api/logbook").json[0]
        self.assertTrue(row["can_edit_project"])
        self.assertEqual(self.client.put("/api/flights/1/comment", json={"comment": "checked"}).status_code, 200)
