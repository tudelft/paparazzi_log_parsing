from .test_historical import AppTests


class ResponsibleForTests(AppTests):
    """Issue 225: people see the drones they are the responsible person (manager) of.
    Users: 1 Admin, 2 Pilot and 3 Viewer (project 1), 4 Outsider (no project). Project 2 has no members."""

    def setUp(self):
        super().setUp()
        self.con.executemany("INSERT INTO drones(id,name,unique_id,status,default_project_id,manager_id,created_at)"
                             " VALUES (?,?,?,?,?,?,'now')",
                             [(2, "Quad X1", "TUD00002", "Ready", 1, 4),     # project 1, outsider responsible
                              (3, "Hidden wing", "TUD00003", "Maintenance", 2, 4),  # project 2: no members
                              (4, "Bebop", "TUD00004", None, 1, 2)])
        self.con.commit()

    def person(self, uid):
        r = self.client.get(f"/api/detail/people/{uid}")
        self.assertEqual(r.status_code, 200, r.json)
        return [(d["name"], d["unique_id"], d["status"]) for d in r.json["item"]["responsible_for"]]

    def test_person_page_lists_the_drones(self):
        self.sign_in(4)  # the person themself sees all of theirs, also outside their projects
        self.assertEqual(self.person(4), [("Hidden wing", "TUD00003", "Maintenance"), ("Quad X1", "TUD00002", "Ready")])
        self.sign_in(1)
        self.assertEqual(len(self.person(4)), 2)
        self.sign_in(2)  # others only see the ones of their own projects
        self.assertEqual(self.person(2), [("Bebop", "TUD00004", None)])
        self.assertEqual(self.client.get("/api/detail/people/4").status_code, 404)  # no shared project or flight
        self.sign_in(3)
        self.assertEqual(self.person(2), [("Bebop", "TUD00004", None)])

    def test_fleet_includes_own_drones_outside_my_projects(self):
        self.sign_in(4)
        names = {d["name"]: d for d in self.client.get("/api/drones").json["drones"]}
        self.assertEqual(set(names), {"Quad X1", "Hidden wing"})
        self.assertEqual(names["Hidden wing"]["manager_id"], 4)
        self.assertTrue(names["Hidden wing"]["can_edit"])
        self.assertEqual(self.client.get("/api/detail/aircraft/3").status_code, 200)
        self.sign_in(2)  # not responsible and not on project 2
        self.assertNotIn("Hidden wing", {d["name"] for d in self.client.get("/api/drones").json["drones"]})
        self.assertEqual(self.client.get("/api/detail/aircraft/3").status_code, 404)
        self.sign_in(5)  # no drones of their own: only their project's (same as before)
        self.assertEqual({d["name"] for d in self.client.get("/api/drones").json["drones"]}, {"Quad X1", "Bebop"})
