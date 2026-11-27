from .test_historical import AppTests


class LogsWithoutFlightsTests(AppTests):
    """GET /api/logbook/logs-without-flights: the Logbook's "Logs without flights" rows."""

    def add_log(self, log_id, project_id, start, *, status="done", uploaded_by=1, flight=False, override=None):
        self.con.execute(
            "INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by,project_id,start_utc,date_override)"
            " VALUES (?,?,?,?,?,'now',?,?,?,?)",
            (log_id, f"log{log_id}.data", f"sha{log_id}", f"x/{log_id}", status, uploaded_by, project_id, start, override))
        self.con.execute("INSERT INTO aircraft(id,log_id,name,ac_id) VALUES(?,?,'Bench',7)", (log_id, log_id))
        if flight:
            self.con.execute(
                "INSERT INTO flights(id,aircraft_id,idx,duration_s,takeoff_utc,landing_utc,landed,verdict,has_gps_position)"
                " VALUES(?,?,1,60,?,?,1,'flight',0)", (log_id, log_id, start, start))
        self.con.commit()

    def ids(self, query=""):
        response = self.client.get("/api/logbook/logs-without-flights" + query)
        self.assertEqual(response.status_code, 200, response.json)
        return [log["id"] for log in response.json["logs"]]

    def setUp(self):
        super().setUp()
        self.add_log(1, 1, "2026-05-01T10:00:00.000Z")                  # Research, no flight
        self.add_log(2, 2, "2026-05-02T10:00:00.000Z")                  # Private, no flight
        self.add_log(3, 1, "2026-05-03T10:00:00.000Z", flight=True)     # has a flight
        self.add_log(4, 1, "2026-05-04T10:00:00.000Z", status="error")  # parse failed: the Logs page's business
        self.add_log(5, 1, "1980-01-06T10:00:00.000Z")                  # no usable date
        self.add_log(6, None, "2026-05-06T10:00:00.000Z", uploaded_by=2)  # no project, uploaded by the pilot

    def test_rows_carry_what_the_opened_row_shows(self):
        self.sign_in(1)
        log = next(l for l in self.client.get("/api/logbook/logs-without-flights").json["logs"] if l["id"] == 1)
        self.assertTrue({"size_bytes", "outdated", "parser_version", "uploaded_by_name", "has_log"} <= set(log))

    def test_scope_is_the_logs_page_scope(self):
        self.sign_in(1)
        self.assertEqual(self.ids(), [6, 5, 2, 1])
        self.sign_in(2)  # member of Research and uploader of log 6; not of Private
        self.assertEqual(self.ids(), [6, 5, 1])
        self.sign_in(4)  # outsider: nothing
        self.assertEqual(self.ids(), [])
        self.sign_in(1)
        self.assertEqual(self.ids("?project=2"), [2])

    def test_period_unknown_dates_and_search(self):
        self.sign_in(2)
        self.assertEqual(self.ids("?from=2026-05-01&to=2026-05-05"), [5, 1])
        self.assertEqual(self.ids("?from=2026-05-01&to=2026-05-05&unknown_dates=0"), [1])
        self.add_log(7, 1, "1980-01-06T10:00:00.000Z", override="2026-04-01")  # a set date counts
        self.assertEqual(self.ids("?from=2026-04-01&to=2026-04-30"), [7, 5])
        self.assertEqual(self.ids("?q=LOG6"), [6])
        self.assertEqual(self.ids("?q=research"), [7, 5, 1])
        self.assertEqual(self.client.get("/api/logbook/logs-without-flights?from=2026-13-01").status_code, 400)

    def test_rows_carry_what_the_logbook_needs(self):
        self.sign_in(2)
        log = next(l for l in self.client.get("/api/logbook/logs-without-flights").json["logs"] if l["id"] == 5)
        self.assertEqual((log["date_ok"], log["project_name"], log["aircraft_names"]), (False, "Research", "Bench"))
        self.assertTrue(log["can_edit_date"])      # full member of Research
        self.assertFalse(log["can_edit_project"])  # a log's project is changed by admins
        self.assertNotIn("sha256", log)
