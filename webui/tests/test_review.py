from copy import deepcopy
from unittest.mock import patch

import links

from .test_links import LinkFixtures


class ReviewQueueTests(LinkFixtures):
    def queue(self):
        return self.client.get("/api/review").json

    def test_other_aircraft_index_preserves_boundaries_order_and_exclusions(self):
        self.flight(1, "12:00:00")
        self.create()
        template = next(r for r in self.logbook() if r["source_type"] == "telemetry")
        paper = dict(deepcopy(template), source_type="paper", manual_id=100, record_kind="daily",
                     takeoff_utc="2010-01-01T12:00:00", duration_s=600, possible_duplicates=[])
        telemetry = []
        for identity, clock in enumerate(("12:25:00", "11:45:00", "12:25:00.001",
                                          "11:44:59.999", "invalid", "12:00:00",
                                          "12:01:00", "12:02:00"), 1):
            row = deepcopy(template)
            row.update(drone_id=2, takeoff_utc=f"2010-01-01T{clock}",
                       sources=[dict(flight_id=identity, log_type="gcs")], linked_records=[])
            telemetry.append(row)
        telemetry[5]["drone_id"] = paper["drone_id"]
        telemetry[6]["linked_records"] = [{"id": 999}]
        telemetry[7]["sources"].append(dict(flight_id=99, log_type="onboard_sd"))
        with patch.dict(self.ns, logbook_rows=lambda **kwargs: telemetry + [paper]), patch.object(
                links, "resolved", return_value=[dict(record_id=100, flight_id=99)]):
            cases = self.queue()["cases"]
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["kind"], "other aircraft")
        self.assertEqual([r["flight_ids"] for r in cases[0]["candidates"]], [[1], [2]])

    def test_other_aircraft_timestamps_are_parsed_once_per_request(self):
        telemetry = [dict(source_type="telemetry", drone_id=2, takeoff_utc="2010-01-01T12:00:00.000Z",
                          sources=[dict(flight_id=i + 1, log_type="gcs")]) for i in range(1000)]
        papers = [dict(source_type="paper", drone_id=1, manual_id=i + 1,
                       takeoff_utc="2010-01-02T12:00:00", record_kind="flight", duration_s=60)
                  for i in range(100)]
        with patch.dict(self.ns, logbook_rows=lambda **kwargs: telemetry + papers), patch.object(
                links, "_ts", wraps=links._ts) as parse:
            self.assertEqual(self.queue()["cases"], [])
        self.assertEqual(parse.call_count, len(telemetry) + len(papers))

    def test_possible_duplicate_is_decided_and_can_be_undone(self):
        self.flight(1, "12:00:00")
        self.flight(2, "12:02:40")  # back-to-back logs: not linked automatically
        record = self.create().json["id"]
        cases = self.queue()["cases"]
        self.assertEqual([(c["kind"], c["paper"]["record_id"]) for c in cases], [("possible duplicate", record)])
        self.assertEqual([f["flight_ids"] for f in cases[0]["candidates"]], [[2], [1]])
        self.assertTrue(cases[0]["paper"]["can_edit"] and all(f["can_edit"] for f in cases[0]["candidates"]))
        self.assertEqual(self.decide(record, [1], True).status_code, 200)
        data = self.queue()
        self.assertEqual(data["cases"], [])
        self.assertEqual([(r["record_id"], r["flight_id"], r["same"], r["by_name"]) for r in data["recent"]],
                         [(record, 1, 1, "Pilot")])
        undo = self.client.delete("/api/links", json={"record_id": record, "flight_ids": [1]})
        self.assertEqual(undo.status_code, 200)
        data = self.queue()
        self.assertEqual((len(data["cases"]), data["recent"]), (1, []))  # back to automatic: still ambiguous

    def test_other_aircraft_case(self):
        self.con.execute("INSERT INTO drones(id,name,unique_id,created_at) VALUES (2,'Other','UID2','now')")
        self.flight(1, "12:01:00")
        self.con.execute("UPDATE aircraft SET drone_id=2 WHERE log_id=1")
        self.con.commit()
        record = self.create().json["id"]
        cases = self.queue()["cases"]
        self.assertEqual([(c["kind"], [f["aircraft"] for f in c["candidates"]]) for c in cases],
                         [("other aircraft", ["Other"])])
        self.assertEqual(self.decide(record, [1], False).status_code, 200)
        self.assertEqual(self.queue()["cases"], [])

    def test_conflicts_are_kept_or_copied_and_reopen_on_new_values(self):
        self.flight(1, "12:00:00")
        self.con.execute("UPDATE flights SET pic_user_id=5 WHERE id=1")
        self.con.commit()
        record = self.create().json["id"]
        case = self.queue()["cases"][0]
        conflict = case["conflicts"][0]
        self.assertEqual((case["kind"], conflict["field"], conflict["paper"], conflict["log"], conflict["paper_id"]),
                         ("conflict", "PIC", "Pilot", "Uploader", 2))
        keep = {k: conflict[k] for k in ("record_id", "field", "paper", "log")}
        self.assertEqual(self.client.post("/api/review/conflicts", json=keep).status_code, 200)
        self.assertEqual(self.queue()["cases"], [])
        entry = self.logbook()[0]  # still visible in the logbook, with who accepted the log value
        self.assertEqual(entry["conflicts"][0]["reviewed_by"], "Pilot")
        self.con.execute("UPDATE flights SET pic_user_id=3 WHERE id=1")  # another log value: a new conflict
        self.con.commit()
        self.assertEqual(self.queue()["cases"][0]["conflicts"][0]["log"], "Viewer")
        # Use the paper value: the flight gets the paper's PIC and the conflict is gone.
        self.assertEqual(self.client.put("/api/flights/crew", json={"flight_ids": [1], "pic_user_id": 2}).status_code, 200)
        self.assertEqual(self.queue()["cases"], [])
        self.assertEqual(self.logbook()[0]["conflicts"], [])
        self.assertEqual(record, entry["linked_records"][0]["id"])

    def test_view_only_users_see_cases_but_cannot_decide(self):
        self.flight(1, "12:00:00")
        self.flight(2, "12:02:40")
        record = self.create().json["id"]
        self.sign_in(3)
        case = self.queue()["cases"][0]
        self.assertFalse(case["paper"]["can_edit"])
        self.assertEqual(self.client.delete("/api/links", json={"record_id": record, "flight_ids": [1]}).status_code, 403)
        self.assertEqual(self.client.post("/api/review/conflicts", json={
            "record_id": record, "field": "PIC", "paper": "a", "log": "b"}).status_code, 403)
        self.sign_in(4)
        self.assertEqual(self.queue()["cases"], [])
