import json
import os
import sqlite3
import tempfile
from unittest.mock import patch

import db

from .test_historical import AppTests


class InfoMessageTests(AppTests):
    """Aircraft text messages are stored compressed; older plain rows are converted in the background."""

    def test_old_and_new_rows_read_the_same_and_convert_both_ways(self):
        messages = [{"time_s": 1.0, "text": "Engine ok"}] * 300
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "logs.db")
            con = sqlite3.connect(path)
            con.row_factory = sqlite3.Row
            con.executescript(db.SCHEMA)
            con.execute("INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at) VALUES (1,'a','s','d','done','n')")
            con.execute("INSERT INTO aircraft(id,log_id,name,ac_id,info_messages) VALUES (1,1,'x',1,?)", (json.dumps(messages),))
            con.execute("INSERT INTO aircraft(id,log_id,name,ac_id,info_messages_gz) VALUES (2,1,'y',2,?)", (db.pack(messages),))
            con.commit()
            read = lambda i: db.info_messages(con.execute("SELECT * FROM aircraft WHERE id=?", (i,)).fetchone())  # noqa: E731
            self.assertEqual((read(1), read(2)), (messages, messages))
            self.assertEqual(db.compress_info_messages(path, pause=0), 1)
            row = con.execute("SELECT info_messages, LENGTH(info_messages_gz) AS n FROM aircraft WHERE id=1").fetchone()
            self.assertIsNone(row["info_messages"])
            self.assertLess(row["n"], len(json.dumps(messages)) / 20)  # repeated messages compress very well
            self.assertEqual(read(1), messages)
            self.assertEqual(db.compress_info_messages(path, pause=0), 0)  # nothing left to do
            self.assertEqual(db.decompress_info_messages(path), 2)  # and back, for an older Web UI
            self.assertEqual(json.loads(con.execute("SELECT info_messages FROM aircraft WHERE id=2").fetchone()[0]), messages)
            con.close()

    def test_log_page_shows_compressed_messages(self):
        self.con.execute("INSERT INTO logs(id,name,sha256,data_path,status,uploaded_at,uploaded_by,project_id)"
                         " VALUES (7,'a.data','s7','a.gz','done','now',2,1)")
        self.con.execute("INSERT INTO aircraft(id,log_id,name,ac_id,info_messages_gz) VALUES (7,7,'x',1,?)",
                         (db.pack([{"text": "hello"}]),))
        self.con.commit()
        with patch.dict(self.ns, can_delete_log=lambda con, row: False, can_set_log_date=lambda con, row: False):
            log = self.client.get("/api/logs/7").json
        self.assertEqual(log["aircraft"][0]["info_messages"], [{"text": "hello"}])
        self.assertNotIn("info_messages_gz", log["aircraft"][0])
