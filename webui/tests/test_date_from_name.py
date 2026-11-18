"""A log without a usable clock takes the date in its file or folder name when it is parsed (issue 273)."""
import os

from .test_historical import AppTests


class DateFromNameTests(AppTests):
    def log(self, name, start="1980-01-06T10:00:00Z", override=None):
        log_id = self.con.execute(
            "INSERT INTO logs(name,sha256,data_path,log_path,status,uploaded_at,uploaded_by,start_utc,date_override)"
            " VALUES (?,?,'x','y','done','now',2,?,?)", (name, os.urandom(16).hex(), start, override)).lastrowid
        self.con.commit()
        return log_id

    def logged(self, log_id):
        return tuple(self.con.execute("SELECT date_override, date_override_by, date_override_note FROM logs WHERE id=?",
                                      (log_id,)).fetchone())

    def test_folder_and_file_dates(self):
        apply = self.ns["date_from_name"]
        folder = self.log("Hex 1/2026-11-13/LOG00030.BFL")
        self.assertEqual(apply(self.con, folder), "2026-11-13")
        self.assertEqual(self.logged(folder), ("2026-11-13", None, "automatically from the date in the folder name 2026-11-13"))
        file = self.log("flights/log_20261112_1.ulg")
        self.assertEqual(apply(self.con, file), "2026-11-12")
        self.assertEqual(self.logged(file)[2], "automatically from the date in the file name")

    def test_left_alone(self):
        apply = self.ns["date_from_name"]
        dated = self.log("2026-11-13/LOG00030.BFL", start="2026-10-01T10:00:00Z")  # the clock is fine
        overridden = self.log("2026-11-13/LOG00031.BFL", override="2026-11-14")    # a person set it
        nameless = self.log("Hex 1/LOG00032.BFL")                                    # no date anywhere
        for log_id in (dated, overridden, nameless):
            self.assertIsNone(apply(self.con, log_id))
        self.assertEqual([self.logged(i)[0] for i in (dated, overridden, nameless)], [None, "2026-11-14", None])


if __name__ == "__main__":
    import unittest
    unittest.main()
