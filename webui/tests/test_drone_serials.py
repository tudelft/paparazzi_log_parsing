from .test_historical import AppTests


class DroneSerialTests(AppTests):
    def add(self, **body):
        return self.client.post("/api/admin/drones", json={"name": "New", **body})

    def test_new_aircraft_get_the_lowest_free_tud_serial_after_400(self):
        self.con.executemany("INSERT INTO drones(name,unique_id,created_at) VALUES (?,?,'now')",
                             [("A", "TUD00001"), ("B", "TUD00401"), ("C", "tud00402"), ("D", "TUD00404"),
                              ("E", "TUD0403"), ("F", "NLR-1")])
        self.con.commit()
        self.sign_in(1)
        self.assertEqual(self.client.get("/api/drones").json["next_serial"], "TUD00403")  # gaps below 401 stay free
        self.assertEqual(self.client.get("/api/admin/drones").json["next_serial"], "TUD00403")
        response = self.add(unique_id=" ")
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["unique_id"], "TUD00403")
        self.assertEqual(self.add(name="Next").json["unique_id"], "TUD00405")
        self.assertEqual(self.add(name="Old", unique_id="TUD00002").json["unique_id"], "TUD00002")  # by hand
        self.assertEqual(self.add(name="Own", unique_id="EUSC0003").json["unique_id"], "EUSC0003")
        self.assertEqual(self.add(name="Clash", unique_id="TUD00001").status_code, 409)
        self.sign_in(2)
        self.assertIsNone(self.client.get("/api/drones").json["next_serial"])

    def test_an_empty_fleet_starts_at_401(self):
        self.con.execute("DELETE FROM drones")
        self.con.commit()
        self.sign_in(1)
        self.assertEqual(self.client.get("/api/admin/drones").json["next_serial"], "TUD00401")
