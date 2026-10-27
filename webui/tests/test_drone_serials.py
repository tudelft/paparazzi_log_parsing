from .test_historical import AppTests


class DroneSerialTests(AppTests):
    def add(self, **body):
        return self.client.post("/api/admin/drones", json={"name": "New", **body})

    def test_new_aircraft_get_the_lowest_free_tud_serial(self):
        self.con.executemany("INSERT INTO drones(name,unique_id,created_at) VALUES (?,?,'now')",
                             [("A", "TUD00001"), ("B", "tud00002"), ("C", "TUD00004"), ("D", "TUD0003"), ("E", "NLR-1")])
        self.con.commit()
        self.sign_in(1)
        self.assertEqual(self.client.get("/api/drones").json["next_serial"], "TUD00003")
        self.assertEqual(self.client.get("/api/admin/drones").json["next_serial"], "TUD00003")
        response = self.add(unique_id=" ")
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["unique_id"], "TUD00003")
        self.assertEqual(self.add(name="Next").json["unique_id"], "TUD00005")
        self.assertEqual(self.add(name="Own", unique_id="EUSC0003").json["unique_id"], "EUSC0003")
        self.assertEqual(self.add(name="Clash", unique_id="TUD00001").status_code, 409)
        self.sign_in(2)
        self.assertIsNone(self.client.get("/api/drones").json["next_serial"])
