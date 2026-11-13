import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import db
from .test_historical import AppTests
from .test_mail import MAIL_ENV


class ProjectManagerTests(AppTests):
    """User 4 manages project 1 (Research); 2 has full, 3 view, 5 add there. Project 2 has no members."""

    def setUp(self):
        super().setUp()
        self.con.execute("INSERT INTO project_members(project_id,user_id,role) VALUES (1,4,'manager')")
        self.con.execute("UPDATE drones SET default_project_id=1 WHERE id=1")
        self.con.execute("INSERT INTO drone_projects(drone_id,project_id,start_date,end_date) VALUES (1,1,NULL,NULL)")
        self.con.execute("INSERT INTO drones(id,name,unique_id,default_project_id,created_at) VALUES (2,'Other','O1',2,'now')")
        self.con.execute("INSERT INTO users(id,username,display_name,is_admin,created_at) VALUES (6,'spare','Spare',0,'now')")
        self.con.commit()

    def member(self, uid, role, project=1):
        return self.client.put(f"/api/projects/{project}/members", json={"user_id": uid, "role": role})

    def role(self, uid, project=1):
        row = self.con.execute("SELECT role FROM project_members WHERE project_id=? AND user_id=?", (project, uid)).fetchone()
        return row[0] if row else None

    def as_key(self, uid):
        user = dict(self.con.execute("SELECT *, 0 AS key_read_only FROM users WHERE id=?", (uid,)).fetchone())
        return patch.dict(self.ns, user_for_key=lambda key: user)

    def test_manager_has_full_rights(self):
        self.sign_in(4)
        project = next(p for p in self.client.get("/api/projects").json if p["id"] == 1)
        self.assertTrue(project["can_add"] and project["can_delete"] and project["can_manage"])
        self.assertTrue(self.client.get("/api/me").json["can_upload"])
        self.sign_in(2)
        self.assertFalse(next(p for p in self.client.get("/api/projects").json if p["id"] == 1)["can_manage"])

    def test_manager_manages_members_but_not_managers(self):
        self.sign_in(4)
        self.assertEqual(self.member(6, "add").status_code, 200)
        self.assertEqual(self.role(6), "add")
        self.assertEqual(self.member(6, "full").status_code, 200)
        self.assertEqual(self.member(3, "").status_code, 200)
        self.assertIsNone(self.role(3))
        self.assertEqual(self.member(6, "manager").status_code, 403)   # only admins appoint managers
        self.assertEqual(self.role(6), "full")
        self.assertEqual(self.member(4, "view").status_code, 403)      # nor change a manager, also not themselves
        self.assertEqual(self.member(4, "").status_code, 403)
        self.assertEqual(self.role(4), "manager")
        self.assertEqual(self.member(6, "view", project=2).status_code, 403)  # not their project
        self.assertEqual(self.client.put("/api/admin/projects/1", json={"name": "Renamed"}).status_code, 403)
        self.assertEqual(self.client.delete("/api/admin/projects/1").status_code, 403)
        self.sign_in(2)  # full is not manager
        self.assertEqual(self.member(6, "view").status_code, 403)
        self.sign_in(1)
        self.assertEqual(self.member(6, "manager").status_code, 200)
        self.assertEqual(self.role(6), "manager")
        self.assertEqual(self.member(4, "").status_code, 200)
        self.assertIsNone(self.role(4))

    def test_csv_import_accepts_manager(self):
        self.sign_in(1)
        r = self.client.post("/api/admin/projects/import", json={"csv": "project,person,role\nResearch,Spare,manager\n"})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(self.role(6), "manager")
        r = self.client.post("/api/admin/projects/import", json={"csv": "project,person,role\nResearch,Spare,boss\n"})
        self.assertEqual(r.status_code, 400)

    def request(self, **body):
        return self.client.post("/api/drone-requests", json={"project_id": 1, "name": "Newbie", **body})

    def test_request_approve_and_visibility(self):
        self.sign_in(3)  # view only
        self.assertEqual(self.request().status_code, 403)
        self.sign_in(5)
        self.assertEqual(self.request(project_id=2).status_code, 403)
        self.assertEqual(self.client.get("/api/drones").json["request_projects"], [{"id": 1, "name": "Research"}])
        with patch.dict(os.environ, MAIL_ENV):
            self.con.execute("UPDATE users SET email=lower(username)||'@example.org'")
            r = self.request(comments="for tests")
            self.assertEqual(r.status_code, 201, r.json)
            rid = r.json["id"]
            to = {row[0] for row in self.con.execute("SELECT user_id FROM mail_outbox WHERE kind IN ('requests','admin')")}
            self.assertEqual(to, {1, 4})  # the project's manager and the admin
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM drones").fetchone()[0], 2)  # nothing created yet
        mine = self.client.get("/api/drones").json["requests"]
        self.assertEqual([(q["name"], q["can_decide"]) for q in mine], [("Newbie", False)])
        self.assertNotIn("unique_id", mine[0])  # no serial in a request: it is generated at approval
        self.sign_in(2)  # full member, not the requester: does not see it
        self.assertEqual(self.client.get("/api/drones").json["requests"], [])
        self.assertEqual(self.client.post(f"/api/drone-requests/{rid}/approve").status_code, 403)
        self.sign_in(5)
        self.assertEqual(self.client.post(f"/api/drone-requests/{rid}/approve").status_code, 403)
        self.sign_in(4)
        self.assertTrue(self.client.get("/api/drones").json["requests"][0]["can_decide"])
        with patch.dict(os.environ, MAIL_ENV):
            r = self.client.post(f"/api/drone-requests/{rid}/approve")
            self.assertEqual(r.status_code, 200, r.json)
            self.assertIn("approved", self.con.execute("SELECT subject FROM mail_outbox WHERE user_id=5"
                                                       " ORDER BY id DESC").fetchone()[0])
        drone = self.con.execute("SELECT * FROM drones WHERE id=?", (r.json["id"],)).fetchone()
        self.assertEqual((drone["name"], drone["unique_id"], drone["default_project_id"], drone["manager_id"],
                          drone["comments"]), ("Newbie", "TUD00401", 1, 5, "for tests"))
        self.assertEqual(r.json["unique_id"], "TUD00401")
        self.assertEqual(self.con.execute("SELECT project_id FROM drone_projects WHERE drone_id=?",
                                          (drone["id"],)).fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM drone_requests").fetchone()[0], 0)

    def test_pending_request_reserves_nothing_and_clash_fails_at_approval(self):
        self.sign_in(5)
        rid = self.request(name="Shared").json["id"]
        second = self.request(name="Shared")  # a second request with the same name is fine
        self.assertEqual(second.status_code, 201)
        self.sign_in(1)
        added = self.client.post("/api/admin/drones", json={"name": "Shared", "manager_id": 1})
        self.assertEqual(added.status_code, 200, added.json)  # the pending request did not hold the name
        self.assertEqual(self.ns["drone_name_map"](self.con)[1].get("shared"), added.json["id"])
        r = self.client.post(f"/api/drone-requests/{rid}/approve")
        self.assertEqual(r.status_code, 409)
        self.assertIn("Cannot approve", r.json["error"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM drone_requests").fetchone()[0], 2)  # kept
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM drones WHERE name='Shared'").fetchone()[0], 1)

    def test_reject_mails_reason_and_requester_can_withdraw(self):
        self.sign_in(5)
        first = self.request().json["id"]
        second = self.request(name="Second").json["id"]
        self.sign_in(2)
        self.assertEqual(self.client.delete(f"/api/drone-requests/{first}").status_code, 403)
        self.sign_in(4)
        with patch.dict(os.environ, MAIL_ENV):
            self.con.execute("UPDATE users SET email=lower(username)||'@example.org'")
            self.assertEqual(self.client.delete(f"/api/drone-requests/{first}", json={"reason": "Use Drone"}).status_code, 200)
            subject, body = self.con.execute("SELECT subject, body FROM mail_outbox WHERE user_id=5").fetchone()
        self.assertIn("rejected", subject)
        self.assertIn("Reason: Use Drone", body)
        self.sign_in(5)
        self.assertEqual(self.client.delete(f"/api/drone-requests/{second}").status_code, 200)  # withdraw
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM drone_requests").fetchone()[0], 0)

    def test_api_keys_cannot_request_or_decide(self):
        self.sign_in(5)
        rid = self.request().json["id"]
        for uid, call in ((5, lambda: self.client.post("/api/drone-requests", json={"project_id": 1, "name": "K"},
                                                       headers={"Authorization": "Bearer test"})),
                          (4, lambda: self.client.post(f"/api/drone-requests/{rid}/approve",
                                                       headers={"Authorization": "Bearer test"})),
                          (1, lambda: self.client.delete(f"/api/drone-requests/{rid}",
                                                         headers={"Authorization": "Bearer test"}))):
            with self.as_key(uid):
                self.assertEqual(call().status_code, 403)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM drone_requests").fetchone()[0], 1)

    def test_manager_edits_details_but_not_timeline(self):
        self.sign_in(4)
        fleet = {d["id"]: d for d in self.client.get("/api/drones").json["drones"]}
        self.assertTrue(fleet[1]["can_edit"])
        r = self.client.put("/api/drones/1", json={"status": "Maintenance", "comments": "Prop", "manager_id": 2})
        self.assertEqual(r.status_code, 200, r.json)
        row = self.con.execute("SELECT status, comments, manager_id FROM drones WHERE id=1").fetchone()
        self.assertEqual(tuple(row), ("Maintenance", "Prop", 2))
        drone = next(d for d in self.client.get("/api/drones").json["drones"] if d["id"] == 1)
        self.assertEqual((drone["manager"], drone["manager_id"]), ("Pilot", 2))
        self.assertEqual(self.client.put("/api/drones/1", json={"name": "Renamed"}).status_code, 403)
        self.assertEqual(self.client.put("/api/drones/2", json={"status": "Ready"}).status_code, 403)  # not their project
        self.assertEqual(self.client.put("/api/admin/drones/1/projects", json={"periods": []}).status_code, 403)
        self.assertEqual(self.client.delete("/api/admin/drones/1").status_code, 403)
        self.assertEqual(self.client.post("/api/admin/drones", json={"name": "X", "manager_id": 4}).status_code, 403)
        self.sign_in(2)  # full member: no edits, except as the linked manager
        self.assertEqual(self.client.put("/api/drones/1", json={"comments": "Mine now"}).status_code, 200)
        self.sign_in(3)
        self.assertEqual(self.client.put("/api/drones/1", json={"comments": "No"}).status_code, 403)
        self.assertFalse(self.client.get("/api/drones").json["drones"][0]["can_edit"])

    def test_manager_is_always_a_person(self):
        self.sign_in(1)
        self.assertEqual(self.client.put("/api/drones/1", json={"comments": "no manager yet"}).status_code, 200)
        self.assertEqual(self.client.put("/api/drones/1", json={"manager": "pilot"}).status_code, 200)
        drone = next(d for d in self.client.get("/api/drones").json["drones"] if d["id"] == 1)
        self.assertEqual((drone["manager"], drone["manager_id"]), ("Pilot", 2))
        for body in ({"manager": "Somebody Else"}, {"manager": ""}, {"manager_id": None}, {"manager_id": 999}):
            with self.subTest(body=body):  # no free text, no clearing, no unknown account
                self.assertEqual(self.client.put("/api/drones/1", json=body).status_code, 400)
        self.assertEqual(self.con.execute("SELECT manager_id FROM drones WHERE id=1").fetchone()[0], 2)
        self.assertEqual(self.client.put("/api/drones/1", json={"manager_id": 5}).status_code, 200)  # another person
        self.con.execute("DELETE FROM users WHERE id=6")
        self.con.execute("UPDATE users SET display_name='Pilot' WHERE id=3")
        self.con.commit()
        for body in ({}, {"manager": "Pilot"}, {"manager": "Nobody"}):  # none, two accounts, no account
            with self.subTest(body=body):
                r = self.client.post("/api/admin/drones", json={"name": "Fresh", **body})
                self.assertEqual(r.status_code, 400, r.json)
        r = self.client.post("/api/admin/drones", json={"name": "Fresh", "manager": "pic"})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(self.con.execute("SELECT manager_id FROM drones WHERE id=?", (r.json["id"],)).fetchone()[0], 2)

    def test_fleet_csv_links_manager_and_rejects_unknown_names(self):
        self.sign_in(1)
        for csv, error in (("AC Nice Name,Serial Unique,Manager (Post Holder)\nLinked,L-1,viewer\nLoose,L-2,Nobody Known\n",
                            "Row 3: manager 'Nobody Known'"),
                           ("AC Nice Name,Serial Unique\nLinked,L-1\n", "Row 2: a new aircraft needs a Manager")):
            with self.subTest(csv=csv):
                r = self.client.post("/api/admin/drones/import", json={"csv": csv})
                self.assertEqual(r.status_code, 400, r.json)
                self.assertIn(error, r.json["error"])
                self.assertEqual(self.con.execute("SELECT COUNT(*) FROM drones").fetchone()[0], 2)  # file rejected
        csv = "AC Nice Name,Serial Unique,Manager (Post Holder)\nLinked,L-1,viewer\nDrone,UID,\n"
        r = self.client.post("/api/admin/drones/import", json={"csv": csv})  # an existing aircraft may stay without
        self.assertEqual(r.status_code, 200, r.json)
        rows = dict(self.con.execute("SELECT name, manager_id FROM drones WHERE name IN ('Linked','Drone')").fetchall())
        self.assertEqual(rows, {"Linked": 3, "Drone": None})


class ManagerLinkMigrationTests(unittest.TestCase):
    def test_matching_manager_text_is_linked_and_the_rest_dropped(self):
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        db.init(path)
        con = sqlite3.connect(path)  # a database from before: drones without manager_id
        con.executescript(
            "PRAGMA foreign_keys=OFF; DROP TABLE drones;"
            "CREATE TABLE drones (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,"
            " unique_id TEXT NOT NULL UNIQUE COLLATE NOCASE, default_project_id INTEGER, manager TEXT,"
            " current_user TEXT, status TEXT, bought_on TEXT, inventory_project TEXT, comments TEXT,"
            " created_at TEXT NOT NULL);"
            "INSERT INTO users(id,username,display_name,created_at) VALUES (1,'jdoe','Jane Doe','now'),"
            " (2,'twin1','Twin','now'), (3,'twin2','Twin','now'), (4,'gone','Gone','now');"
            "UPDATE users SET is_active=0 WHERE id=4;"
            "INSERT INTO drones(id,name,unique_id,manager,created_at) VALUES (1,'A','1','jane doe','now'),"
            " (2,'B','2','JDOE','now'), (3,'C','3','Twin','now'), (4,'D','4','Stranger','now'), (5,'E','5',NULL,'now'),"
            " (6,'F','6','Gone','now');")
        con.close()
        for _ in range(2):  # idempotent
            db.init(path)
        con = sqlite3.connect(path)
        self.assertEqual(con.execute("SELECT id, manager_id FROM drones ORDER BY id").fetchall(),
                         [(1, 1), (2, 1), (3, None), (4, None), (5, None), (6, None)])
        self.assertNotIn("manager", {r[1] for r in con.execute("PRAGMA table_info(drones)")})  # the text column goes
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("DELETE FROM users WHERE id=1")  # a deleted account leaves the aircraft without a manager
        self.assertIsNone(con.execute("SELECT manager_id FROM drones WHERE id=1").fetchone()[0])
        self.assertIn("drone_requests", {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")})
        con.close()


if __name__ == "__main__":
    unittest.main()
