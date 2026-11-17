import gzip
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import attachments
import db
import storage
from . import test_historical


def docx_bytes(text="Flight request"):
    """A minimal Word document: the ZIP parts LibreOffice needs to open it."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-'
                   'package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override '
                   'PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                   'wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/'
                   'officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
                   f'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    return buffer.getvalue()


class AttachmentTests(test_historical.AppTests):
    def setUp(self):
        super().setUp()
        # Word conversion is off unless a test turns it on, whatever is installed on this machine.
        office = patch.dict(self.app.config, ATTACHMENT_OFFICE=None)
        office.start()
        self.addCleanup(office.stop)

    def tearDown(self):
        for row in self.con.execute("SELECT path FROM attachments"):
            (self.ns["STORAGE_DIR"] / row["path"]).unlink(missing_ok=True)
        shutil.rmtree(self.ns["STORAGE_DIR"] / attachments.PREVIEWS, ignore_errors=True)
        super().tearDown()

    def upload_file(self, targets, content=b"wind speed,temperature\n1,20\n" * 1000, name="wind.csv", **extra):
        metadata = {"filename": name, "description": "Wind tunnel", "targets": targets, **extra}
        return self.client.post("/api/attachments", data=content, content_type="application/octet-stream",
                                headers={"X-Attachment-Metadata": quote(json.dumps(metadata))})

    def paper(self, **changes):
        return dict(kind="paper", id=self.create(**changes).json["id"])

    def log(self, project=1, name="test.data"):
        cur = self.con.execute(
            "INSERT INTO logs(name,sha256,data_path,log_path,status,uploaded_at,uploaded_by,project_id)"
            " VALUES (?,?,'test.data.gz','test.log.gz','done','now',2,?)", (name, os.urandom(16).hex(), project))
        log_id = cur.lastrowid
        aircraft = self.con.execute("INSERT INTO aircraft(log_id,ac_id,name) VALUES (?,7,'Drone')", (log_id,)).lastrowid
        fid = self.con.execute(
            "INSERT INTO flights(aircraft_id,idx,takeoff_time_s,takeoff_utc,project_id) VALUES (?,1,12,'2010-01-01',?)",
            (aircraft, project)).lastrowid
        self.con.commit()
        return dict(kind="log", id=log_id), dict(kind="flight", id=fid)

    def test_compressed_shared_file_roundtrip_and_last_unlink(self):
        paper = self.paper()
        log, flight = self.log()
        data = b"wind,temperature\n1.2,20\n" * 10000
        response = self.upload_file([paper, log, flight], data, "測定.csv")
        self.assertEqual(response.status_code, 201, response.json)
        aid = response.json["id"]
        row = self.con.execute("SELECT * FROM attachments").fetchone()
        self.assertTrue(row["compressed"])
        self.assertLess(row["stored_bytes"], len(data) * 0.1)
        self.assertEqual(row["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 1)
        downloaded = self.client.get(f"/api/attachments/{aid}/download")
        self.assertEqual(downloaded.data, data)
        self.assertNotIn("Content-Encoding", downloaded.headers)
        encoded = self.client.get(f"/api/attachments/{aid}/download", headers={"Accept-Encoding": "br, gzip"})
        self.assertEqual((encoded.headers["Content-Encoding"], gzip.decompress(encoded.data)), ("gzip", data))
        self.assertEqual(int(encoded.headers["Content-Length"]), row["stored_bytes"])
        self.assertIn("filename*=UTF-8", downloaded.headers["Content-Disposition"])
        self.assertEqual(downloaded.headers["X-Content-Type-Options"], "nosniff")
        listing = self.client.get(f'/api/attachments?kind=flight&id={flight["id"]}').json
        self.assertEqual(len(listing["files"]), 1)  # Direct and inherited links do not duplicate a file.
        path = self.ns["STORAGE_DIR"] / row["path"]
        for link in listing["files"][0]["links"]:
            self.assertEqual(self.client.delete(f'/api/attachments/{aid}/links/{link["link_id"]}').status_code, 200)
        self.assertFalse(path.exists())
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 0)
        self.assertEqual(self.client.get(f"/api/attachments/{aid}/download").status_code, 404)

    def test_duplicates_link_and_same_name_supersedes(self):
        log, flight = self.log()
        _, other = self.log(name="other.data")
        first = self.upload_file([flight], name="prep.pdf")
        again = self.upload_file([flight, other], name="prep.pdf")
        self.assertEqual((again.status_code, again.json["id"], again.json["duplicate"]), (200, first.json["id"], True))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachment_links").fetchone()[0], 2)
        newer = self.upload_file([flight], b"revised", "PREP.pdf")
        self.assertEqual(newer.status_code, 201)
        self.assertEqual([x["id"] for x in newer.json["replaces"]], [first.json["id"]])
        files = {f["id"]: f for f in self.client.get(f'/api/attachments?kind=flight&id={flight["id"]}').json["files"]}
        self.assertEqual(files[first.json["id"]]["superseded"]["by"], newer.json["id"])
        self.assertIsNone(files[newer.json["id"]]["superseded"])
        # The other flight keeps its current copy; removing the newer file restores the earlier version.
        self.assertTrue(all(f["superseded"] is None for f in
                            self.client.get(f'/api/attachments?kind=flight&id={other["id"]}').json["files"]))
        link = files[newer.json["id"]]["links"][0]["link_id"]
        self.assertEqual(self.client.delete(f'/api/attachments/{newer.json["id"]}/links/{link}').status_code, 200)
        files = self.client.get(f'/api/attachments?kind=flight&id={flight["id"]}').json["files"]
        self.assertEqual([(f["id"], f["superseded"]) for f in files], [(first.json["id"], None)])

    def test_category_on_upload_listing_and_duplicates(self):
        _, flight = self.log()
        named = self.upload_file([flight], name="notam.pdf", category="preparation & PERMITS")
        self.assertEqual(named.status_code, 201, named.json)
        plain = self.upload_file([flight], name="plain.csv")
        bad = self.upload_file([flight], name="bad.csv", category="Photos")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("Preparation & permits", bad.json["error"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 2)  # nothing stored
        data = self.client.get(f'/api/attachments?kind=flight&id={flight["id"]}').json
        self.assertEqual(data["categories"], attachments.DEFAULT_CATEGORIES)
        files = {f["id"]: f for f in data["files"]}
        self.assertEqual(files[named.json["id"]]["category"], "Preparation & permits")
        self.assertIsNone(files[plain.json["id"]]["category"])
        self.assertTrue(files[plain.json["id"]]["can_edit"])
        # the same file again: an existing category stays, a missing one is filled in
        self.upload_file([flight], name="notam.pdf", category="Archive")
        self.upload_file([flight], name="plain.csv", category="Data")
        got = dict(self.con.execute("SELECT filename, category FROM attachments").fetchall())
        self.assertEqual(got, {"notam.pdf": "Preparation & permits", "plain.csv": "Data"})

    def test_flight_listing_names_its_log_for_uploads_to_all_flights(self):
        log, flight = self.log()
        data = self.client.get(f'/api/attachments?kind=flight&id={flight["id"]}').json
        self.assertEqual((data["parent"]["kind"], data["parent"]["id"], data["parent"]["can_edit"]), ("log", log["id"], True))
        self.assertIsNone(self.client.get(f'/api/attachments?kind=log&id={log["id"]}').json["parent"])
        self.sign_in(3)  # view access: the log is named, but not editable
        self.assertFalse(self.client.get(f'/api/attachments?kind=flight&id={flight["id"]}').json["parent"]["can_edit"])

    def test_flight_listing_offers_the_aircrafts_flights_that_day(self):
        self.con.execute("INSERT INTO drones(id,name,unique_id,created_at) VALUES (2,'Other','UID2','now')")

        def flight(drone, takeoff, hidden=0, start=None, override=None, project=1):
            log = self.con.execute(
                "INSERT INTO logs(name,sha256,data_path,status,uploaded_at,uploaded_by,project_id,start_utc,date_override)"
                " VALUES ('x.data',?,'x.data.gz','done','now',2,?,?,?)",
                (os.urandom(16).hex(), project, start or takeoff, override)).lastrowid
            aircraft = self.con.execute("INSERT INTO aircraft(log_id,ac_id,name,drone_id) VALUES (?,7,'Drone',?)",
                                        (log, drone)).lastrowid
            return self.con.execute("INSERT INTO flights(aircraft_id,idx,takeoff_utc,hidden,project_id) VALUES (?,0,?,?,?)",
                                    (aircraft, takeoff, hidden, project)).lastrowid
        morning = flight(1, "2026-11-10T08:00:00Z")
        afternoon = flight(1, "2026-11-10T14:00:00Z")
        flight(1, "2026-11-10T15:00:00Z", hidden=1)
        flight(1, "2026-11-11T08:00:00Z")
        corrected = flight(1, "2000-01-01T12:00:00Z", start="2000-01-01T11:00:00Z", override="2026-11-10")  # bad clock
        other = flight(2, "2026-11-10T09:00:00Z")
        unlinked = flight(None, "2026-11-10T10:00:00Z")  # not linked to a fleet aircraft
        private = flight(1, "2026-11-10T16:00:00Z", project=2)
        self.con.commit()
        day = self.client.get(f"/api/attachments?kind=flight&id={morning}").json["day"]
        self.assertEqual((day["aircraft"], day["date"]), ("Drone", "2026-11-10"))
        self.assertEqual([f["id"] for f in day["flights"]], [morning, corrected, afternoon])  # not hidden/private/other
        self.assertTrue(all(f["can_edit"] for f in day["flights"]))
        every = self.client.get(f"/api/attachments?kind=flight&id={morning}").json["day_all"]
        self.assertEqual([f["id"] for f in every["flights"]], [morning, other, unlinked, corrected, afternoon])
        data = self.client.get(f"/api/attachments?kind=flight&id={unlinked}").json
        self.assertIsNone(data["day"])  # no fleet aircraft
        self.assertEqual(len(data["day_all"]["flights"]), 5)
        lonely = flight(2, "2026-11-12T09:00:00Z")
        self.con.commit()
        data = self.client.get(f"/api/attachments?kind=flight&id={lonely}").json
        self.assertEqual((data["day"], data["day_all"]), (None, None))  # only itself
        self.assertNotIn(private, [f["id"] for f in day["flights"]])
        self.sign_in(3)  # view access only: nothing to upload to
        data = self.client.get(f"/api/attachments?kind=flight&id={morning}").json
        self.assertEqual((data["day"], data["day_all"]), (None, None))

    def test_match_files_to_flights_by_time(self):
        def flight(takeoff, landing, project=1, hidden=0, start=None, override=None):
            log = self.con.execute(
                "INSERT INTO logs(name,sha256,data_path,status,uploaded_at,uploaded_by,project_id,start_utc,date_override)"
                " VALUES ('x.data',?,'x.data.gz','done','now',2,?,?,?)",
                (os.urandom(16).hex(), project, start or takeoff, override)).lastrowid
            aircraft = self.con.execute("INSERT INTO aircraft(log_id,ac_id,name,drone_id) VALUES (?,7,'Drone',1)",
                                        (log,)).lastrowid
            return self.con.execute("INSERT INTO flights(aircraft_id,idx,takeoff_utc,landing_utc,hidden,project_id)"
                                    " VALUES (?,0,?,?,?,?)", (aircraft, takeoff, landing, hidden, project)).lastrowid
        morning = flight("2026-11-10T09:00:00Z", "2026-11-10T09:20:00Z")
        noon = flight("2026-11-10T12:00:00Z", "2026-11-10T12:10:00Z")
        flight("2026-11-10T15:00:00Z", "2026-11-10T15:10:00Z", hidden=1)
        private = flight("2026-11-10T17:00:00Z", "2026-11-10T17:10:00Z", project=2)
        fixed = flight("2000-01-01T07:00:00Z", "2000-01-01T07:05:00Z", start="2000-01-01T06:59:00Z", override="2026-11-11")
        self.con.commit()
        times = ["2026-11-10T09:10:00Z",       # during the morning flight
                 "2026-11-10T10:45:00+01:00",  # 09:45 UTC: 25 min after landing
                 "2026-11-10T11:50:00Z",       # 10 min before noon takeoff
                 "2026-11-10T14:00:00Z",       # no flight near: that day only
                 "2026-11-10T17:05:00Z",       # the private flight: not offered
                 "2026-11-11T07:02:00Z",       # the log with a corrected date
                 "2026-11-20T10:00:00Z", "2026-11-10T10:00:00", "nonsense"]
        r = self.client.post("/api/attachments/match", json={"times": times})
        self.assertEqual(r.status_code, 200, r.json)
        got = [(m["flight"]["id"] if m and m["flight"] else None, m and m["how"],
                [f["id"] for f in m["day"]["flights"]] if m and m["day"] else None) for m in r.json["matches"]]
        day = [morning, noon]
        self.assertEqual(got, [(morning, "during", day), (morning, "after landing", day), (noon, "before takeoff", day),
                               (None, None, day), (None, None, day), (fixed, "during", [fixed]), (None, None, None),
                               (None, None, None), (None, None, None)])  # no time zone or not a time: no match
        self.assertNotIn(private, [f["id"] for m in r.json["matches"] if m and m["day"] for f in m["day"]["flights"]])
        # videos: a range matches the flight it overlaps most; over several flights it lists them all
        ranges = [{"start": "2026-11-10T08:55:00Z", "end": "2026-11-10T09:05:00Z"},    # starts before takeoff
                  {"start": "2026-11-10T09:15:00Z", "end": "2026-11-10T12:08:00Z"},    # over both flights
                  {"start": "2026-11-10T13:00:00Z", "end": "2026-11-10T12:00:00Z"}]    # end before start: a moment
        m = self.client.post("/api/attachments/match", json={"times": ranges}).json["matches"]
        self.assertEqual((m[0]["flight"]["id"], m[0]["how"], m[0]["flights"]), (morning, "overlaps", None))
        self.assertEqual((m[1]["flight"]["id"], m[1]["how"], [f["id"] for f in m[1]["flights"]]),
                         (noon, "overlaps", [morning, noon]))  # 5 min of the morning flight, 8 of noon
        self.assertEqual((m[2]["flight"], m[2]["day"]["date"]), (None, "2026-11-10"))
        self.sign_in(3)  # view access: nothing to add files to
        self.assertEqual(self.client.post("/api/attachments/match", json={"times": times[:1]}).json["matches"],
                         [{"flight": None, "how": None, "flights": None, "day": None}])
        self.assertEqual(self.client.post("/api/attachments/match", json={"times": "x"}).status_code, 400)

    def test_upload_role_adds_files_but_only_full_access_removes(self):
        _, flight = self.log()
        other_log, other = self.log(name="other.data")
        theirs = self.upload_file([flight], name="theirs.pdf").json["id"]  # by user 2 (full access)
        self.sign_in(5)  # Upload ("add") on project 1
        mine = self.upload_file([flight], b"mine", "mine.pdf")
        self.assertEqual(mine.status_code, 201, mine.json)
        mine = mine.json["id"]
        files = {f["id"]: f for f in self.client.get(f'/api/attachments?kind=flight&id={flight["id"]}').json["files"]}
        self.assertTrue(files[mine]["can_edit"])        # own file: may change category / description
        self.assertFalse(files[theirs]["can_edit"])     # someone else's: not
        self.assertTrue(files[mine]["can_share"])
        self.assertFalse(files[mine]["links"][0]["can_edit"])  # removing a link still needs Delete
        self.assertEqual(self.client.patch(f"/api/attachments/{mine}", json={"category": "Notes"}).status_code, 200)
        self.assertEqual(self.client.patch(f"/api/attachments/{theirs}", json={"category": "Notes"}).status_code, 403)
        self.assertEqual(self.client.post(f"/api/attachments/{mine}/links", json={"targets": [other]}).status_code, 200)
        link = files[mine]["links"][0]["link_id"]
        self.assertEqual(self.client.delete(f"/api/attachments/{mine}/links/{link}").status_code, 403)
        match = self.client.post("/api/attachments/match", json={"times": ["2010-01-01T00:00:12Z"]}).json["matches"][0]
        self.assertIn(flight["id"], [f["id"] for f in match["day"]["flights"]])
        found = self.client.get("/api/attachments/targets?q=other.data").json["targets"]  # search offers Upload targets
        self.assertIn(("log", other_log["id"]), [(t["kind"], t["id"]) for t in found])
        self.sign_in(3)  # See only: still nothing
        self.assertEqual(self.upload_file([flight], b"view", "view.pdf").status_code, 403)

    def test_new_version_inherits_the_category(self):
        _, flight = self.log()
        self.upload_file([flight], name="plan.pdf", category="Notes")
        newer = self.upload_file([flight], b"revised", "PLAN.pdf")
        self.assertEqual(newer.json["replaces"][0]["filename"], "plan.pdf")
        own = self.upload_file([flight], b"third", "plan.pdf", category="Archive")
        self.assertEqual([r[0] for r in self.con.execute("SELECT category FROM attachments ORDER BY id")],
                         ["Notes", "Notes", "Archive"])
        self.assertEqual(own.status_code, 201)

    def test_edit_category_and_description(self):
        paper = self.paper()
        aid = self.upload_file([paper]).json["id"]
        r = self.client.patch(f"/api/attachments/{aid}", json={"category": "media", "description": "  Photos  "})
        self.assertEqual((r.status_code, r.json["category"], r.json["description"]), (200, "Media", "Photos"))
        self.assertEqual(self.client.patch(f"/api/attachments/{aid}", json={"category": "Photos"}).status_code, 400)
        self.assertEqual(self.client.patch(f"/api/attachments/{aid}", json={"description": "x" * 1001}).status_code, 400)
        self.assertEqual(self.client.patch(f"/api/attachments/{aid}", json={}).status_code, 400)
        self.assertEqual(self.client.patch(f"/api/attachments/{aid}", json={"category": None}).status_code, 200)
        self.assertEqual(tuple(self.con.execute("SELECT category, description FROM attachments").fetchone()),
                         (None, "Photos"))
        self.sign_in(3)  # can see the record, but has view access only
        self.assertEqual(self.client.patch(f"/api/attachments/{aid}", json={"category": "Data"}).status_code, 403)
        self.sign_in(4)  # cannot see it at all
        self.assertEqual(self.client.patch(f"/api/attachments/{aid}", json={"category": "Data"}).status_code, 404)

    def test_categories_setting_order_and_removed_category(self):
        _, flight = self.log()
        aid = self.upload_file([flight], category="Archive").json["id"]
        with patch.dict(self.app.config, ATTACHMENT_CATEGORIES=["Permits", "Photos"]):
            data = self.client.get(f'/api/attachments?kind=flight&id={flight["id"]}').json
            self.assertEqual(data["categories"], ["Permits", "Photos"])
            self.assertEqual(data["files"][0]["category"], "Archive")  # kept although no longer listed
            self.assertEqual(self.upload_file([flight], name="p.jpg", category="photos").status_code, 201)
            self.assertEqual(self.client.patch(f"/api/attachments/{aid}", json={"category": "Archive"}).status_code, 400)
        config = __import__("config")
        self.assertEqual(config.parse_categories(" A , B ,"), ["A", "B"])
        for value in ("", " , ", "A, a", "x" * 41):
            with self.subTest(value=value), self.assertRaises(ValueError):
                config.parse_categories(value)

    def test_backfill_moves_description_prefix(self):
        _, flight = self.log()
        for name, description in (("a.pdf", "[Preparation & permits] NOTAM"), ("b.jpg", "[Photos]  Field"),
                                  ("c.txt", "No prefix"), ("d.txt", "[Unknown] x")):
            self.upload_file([flight], name=name, description=description)
        self.upload_file([flight], name="e.txt", description="[Notes] kept", category="Data")
        categories = attachments.DEFAULT_CATEGORIES
        changes, skipped = attachments.backfill_categories(self.con, categories, apply=False)
        self.assertEqual([c[1:] for c in changes], [("Preparation & permits", "NOTAM")])
        self.assertEqual(skipped, {"Photos": 1, "Unknown": 1})
        self.assertIsNone(self.con.execute("SELECT category FROM attachments WHERE filename='a.pdf'").fetchone()[0])
        mapping = attachments.parse_mapping(["Photos=media"], categories)
        changes, skipped = attachments.backfill_categories(self.con, categories, mapping)
        self.assertEqual(len(changes), 2)
        got = {r[0]: (r[1], r[2]) for r in self.con.execute("SELECT filename, category, description FROM attachments")}
        self.assertEqual(got, {"a.pdf": ("Preparation & permits", "NOTAM"), "b.jpg": ("Media", "Field"),
                               "c.txt": (None, "No prefix"), "d.txt": (None, "[Unknown] x"),
                               "e.txt": ("Data", "[Notes] kept")})
        self.assertEqual(attachments.backfill_categories(self.con, categories)[0], [])  # idempotent
        with self.assertRaises(ValueError):
            attachments.parse_mapping(["Photos=Pictures"], categories)

    def test_migration_adds_the_category_column(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "logs.db")
            con = sqlite3.connect(path)
            con.execute("CREATE TABLE attachments (id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT NOT NULL,"
                        " description TEXT NOT NULL DEFAULT '', path TEXT NOT NULL UNIQUE, compressed INTEGER NOT NULL,"
                        " size_bytes INTEGER NOT NULL, stored_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,"
                        " uploaded_by INTEGER, uploaded_at TEXT NOT NULL)")
            con.commit()
            con.close()
            for _ in range(2):  # idempotent
                db.init(path)
            con = sqlite3.connect(path)
            self.assertIn("category", {r[1] for r in con.execute("PRAGMA table_info(attachments)")})
            con.close()

    def test_startup_merges_existing_duplicates(self):
        _, flight = self.log()
        paper = self.paper()
        ids = [self.upload_file([flight, paper]).json["id"]]
        # Simulate copies stored before duplicate detection existed.
        row = self.con.execute("SELECT * FROM attachments WHERE id=?", (ids[0],)).fetchone()
        copy = self.ns["STORAGE_DIR"] / "attachments" / "copy.gz"
        shutil.copy(self.ns["STORAGE_DIR"] / row["path"], copy)
        dup = self.con.execute(
            "INSERT INTO attachments(filename,description,path,compressed,size_bytes,stored_bytes,sha256,uploaded_by,"
            " uploaded_at) SELECT filename,description,'attachments/copy.gz',compressed,size_bytes,stored_bytes,sha256,"
            " uploaded_by,uploaded_at FROM attachments WHERE id=?", (ids[0],)).lastrowid
        self.con.execute("UPDATE attachment_links SET attachment_id=? WHERE record_id=?", (dup, paper["id"]))
        self.con.commit()
        self.assertEqual(attachments.merge_duplicates(self.con, self.ns["STORAGE_DIR"]), 1)
        self.assertEqual(attachments.merge_duplicates(self.con, self.ns["STORAGE_DIR"]), 0)
        self.assertFalse(copy.exists())
        self.assertEqual([r[0] for r in self.con.execute("SELECT DISTINCT attachment_id FROM attachment_links")],
                         [ids[0]])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachment_links").fetchone()[0], 2)

    def test_incompressible_and_range_download(self):
        paper = self.paper()
        data = os.urandom(1 << 16)
        response = self.upload_file([paper], data, "video.mp4")
        self.assertEqual(response.status_code, 201)
        row = self.con.execute("SELECT * FROM attachments").fetchone()
        self.assertFalse(row["compressed"])
        self.assertEqual(row["stored_bytes"], len(data))
        aid = response.json["id"]
        response = self.client.get(f"/api/attachments/{aid}/download", headers={"Range": "bytes=10-19"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, data[10:20])
        response.close()

    def test_view_inline_only_for_allowlisted_types(self):
        paper = self.paper()
        pdf = self.upload_file([paper], b"%PDF-1.4\n" + os.urandom(4096), "Flight request.PDF").json["id"]
        text = self.upload_file([paper], b"wind,temperature\n1,20\n" * 1000, "wind.csv").json["id"]
        image = self.upload_file([paper], os.urandom(2048), "photo.jpeg").json["id"]
        listing = {f["id"]: f for f in self.client.get(f'/api/attachments?kind=paper&id={paper["id"]}').json["files"]}
        for aid, mimetype in ((pdf, "application/pdf"), (text, "text/plain; charset=utf-8"), (image, "image/jpeg")):
            self.assertTrue(listing[aid]["viewable"])
            response = self.client.get(f"/api/attachments/{aid}/view")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Content-Type"], mimetype)
            self.assertTrue(response.headers["Content-Disposition"].startswith("inline;"))
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(response.headers["Cache-Control"], "private, no-store")
            download = self.client.get(f"/api/attachments/{aid}/download")
            self.assertEqual(response.data, download.data)
            response.close()
            download.close()
        self.assertNotIn("Content-Security-Policy", self.client.get(f"/api/attachments/{pdf}/view").headers)
        self.assertIn("sandbox", self.client.get(f"/api/attachments/{text}/view").headers["Content-Security-Policy"])
        for name in ("page.html", "drawing.svg", "data.xml", "report.docx", "noextension"):
            aid = self.upload_file([paper], b"<script>alert(1)</script>" * 100, name).json["id"]
            self.assertEqual(self.client.get(f"/api/attachments/{aid}/view").status_code, 415, name)
            download = self.client.get(f"/api/attachments/{aid}/download")
            self.assertEqual(download.headers["Content-Type"], "application/octet-stream")
            self.assertTrue(download.headers["Content-Disposition"].startswith("attachment;"))
        self.sign_in(4)
        self.assertEqual(self.client.get(f"/api/attachments/{pdf}/view").status_code, 404)

    def fake_office(self, fail=False):
        calls = []

        def run(cmd, **kwargs):
            calls.append(cmd)
            if fail:
                raise subprocess.CalledProcessError(1, cmd)
            outdir = Path(cmd[cmd.index("--outdir") + 1])
            (outdir / "document.pdf").write_bytes(b"%PDF-1.4 converted")
        self.app.config["ATTACHMENT_OFFICE"] = "/opt/office/soffice"
        return calls, patch.object(attachments.subprocess, "run", side_effect=run)

    def test_docx_is_converted_once_and_shown_as_pdf(self):
        paper = self.paper()
        data = docx_bytes()
        aid = self.upload_file([paper], data, "Flight request.docx").json["id"]
        listing = self.client.get(f'/api/attachments?kind=paper&id={paper["id"]}').json["files"]
        self.assertFalse(listing[0]["viewable"])  # no converter configured
        self.assertEqual(self.client.get(f"/api/attachments/{aid}/view").status_code, 415)
        calls, fake = self.fake_office()
        with fake:
            self.assertTrue(self.client.get(f'/api/attachments?kind=paper&id={paper["id"]}').json["files"][0]["viewable"])
            for _ in range(2):
                response = self.client.get(f"/api/attachments/{aid}/view")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["Content-Type"], "application/pdf")
                self.assertTrue(response.headers["Content-Disposition"].startswith("inline;"))
                self.assertIn("Flight%20request.pdf", response.headers["Content-Disposition"])
                self.assertEqual(response.headers["Cache-Control"], "private, no-store")
                self.assertEqual(response.data, b"%PDF-1.4 converted")
                response.close()
        self.assertEqual(len(calls), 1)  # cached per file content
        self.assertIn("--infilter=MS Word 2007 XML", calls[0])
        download = self.client.get(f"/api/attachments/{aid}/download")
        self.assertEqual((download.headers["Content-Type"], download.data), ("application/octet-stream", data))
        download.close()
        self.sign_in(4)
        self.assertEqual(self.client.get(f"/api/attachments/{aid}/view").status_code, 404)
        self.sign_in(2)
        preview = self.ns["STORAGE_DIR"] / attachments.PREVIEWS / f"{hashlib.sha256(data).hexdigest()}.pdf"
        self.assertTrue(preview.is_file())
        link = self.client.get(f'/api/attachments?kind=paper&id={paper["id"]}').json["files"][0]["links"][0]
        self.assertEqual(self.client.delete(f'/api/attachments/{aid}/links/{link["link_id"]}').status_code, 200)
        self.assertFalse(preview.exists())  # removed with the last copy of the document

    def test_docx_conversion_failures_cache_nothing(self):
        paper = self.paper()
        real = self.upload_file([paper], docx_bytes(), "a.docx").json["id"]
        renamed = self.upload_file([paper], b"<html><script>alert(1)</script></html>" * 50, "b.docx").json["id"]
        calls, fake = self.fake_office(fail=True)
        with fake:
            self.assertEqual(self.client.get(f"/api/attachments/{real}/view").status_code, 422)
            self.assertEqual(self.client.get(f"/api/attachments/{renamed}/view").status_code, 422)
        self.assertEqual(len(calls), 1)  # the renamed HTML file never reaches LibreOffice
        self.assertFalse((self.ns["STORAGE_DIR"] / attachments.PREVIEWS).exists()
                         and any((self.ns["STORAGE_DIR"] / attachments.PREVIEWS).iterdir()))

    @unittest.skipUnless(shutil.which("soffice"), "LibreOffice is not installed")
    def test_docx_conversion_with_libreoffice(self):
        paper = self.paper()
        aid = self.upload_file([paper], docx_bytes(), "real.docx").json["id"]
        self.app.config["ATTACHMENT_OFFICE"] = shutil.which("soffice")
        response = self.client.get(f"/api/attachments/{aid}/view")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.startswith(b"%PDF-"))
        response.close()

    def test_permissions_sharing_and_hidden_links(self):
        paper = self.paper()
        response = self.upload_file([paper])
        aid = response.json["id"]
        self.sign_in(1)
        private = self.paper(project_id=2, date="2011-01-01")
        self.assertEqual(self.client.post(f"/api/attachments/{aid}/links", json={"targets": [private]}).status_code, 200)
        self.sign_in(3)
        listing = self.client.get(f'/api/attachments?kind=paper&id={paper["id"]}').json["files"][0]
        self.assertEqual(len(listing["links"]), 1)
        self.assertFalse(listing["can_share"])
        self.assertEqual(self.client.get(f"/api/attachments/{aid}/download").status_code, 200)
        self.assertEqual(self.upload_file([paper]).status_code, 403)
        self.assertEqual(self.client.delete(f'/api/attachments/{aid}/links/{listing["links"][0]["link_id"]}').status_code, 403)
        self.sign_in(4)
        self.assertEqual(self.client.get(f"/api/attachments/{aid}/download").status_code, 404)
        self.assertEqual(self.client.get(f'/api/attachments?kind=paper&id={paper["id"]}').status_code, 404)
        self.sign_in(2)
        # A user who cannot manage all existing links cannot share the file further.
        another = self.paper(date="2012-01-01")
        self.assertEqual(self.client.post(f"/api/attachments/{aid}/links", json={"targets": [another]}).status_code, 404)
        listing = self.client.get(f'/api/attachments?kind=paper&id={paper["id"]}').json["files"][0]
        self.assertEqual(self.client.delete(f'/api/attachments/{aid}/links/{listing["links"][0]["link_id"]}').status_code, 200)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 1)
        self.sign_in(1)
        self.assertEqual(self.client.delete(f'/api/historical/{private["id"]}').status_code, 200)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 0)

    def test_validation_stream_limits_and_no_partial_files(self):
        paper = self.paper()
        for targets, name in (([], "x"), ([dict(kind=[], id=1)], "x"), ([paper], "../x"), ([paper], "a\nb")):
            self.assertEqual(self.upload_file(targets, name=name).status_code, 400)
        self.assertEqual(self.upload_file([paper], b"").status_code, 400)
        self.assertEqual(self.client.post("/api/attachments", data=b"x").status_code, 400)
        self.app.config["ATTACHMENT_MAX_BYTES"] = 1024
        self.addCleanup(self.app.config.__setitem__, "ATTACHMENT_MAX_BYTES", 10 << 30)
        response = self.upload_file([paper], b"x" * 1025)
        self.assertEqual(response.status_code, 413, response.json)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 0)
        self.assertEqual(list((self.ns["STORAGE_DIR"] / ".incoming-attachments").glob("*")), [])

    def test_reparse_preserves_matching_and_falls_back_to_log(self):
        log, flight = self.log()
        aid = self.upload_file([flight]).json["id"]
        saved = attachments.snapshot(self.con, log["id"])
        doc = dict(aircraft=[dict(ac_id=7, name="Drone", flights=[dict(flight=1, takeoff=dict(time_s=12))])])
        db.store_parsed(self.con, log["id"], doc, "paths.gz")
        attachments.restore(self.con, log["id"], saved)
        link = self.con.execute("SELECT * FROM attachment_links WHERE attachment_id=?", (aid,)).fetchone()
        self.assertIsNotNone(link["flight_id"])
        saved = attachments.snapshot(self.con, log["id"])
        db.store_parsed(self.con, log["id"], dict(aircraft=[]), "paths.gz")
        attachments.restore(self.con, log["id"], saved)
        link = self.con.execute("SELECT * FROM attachment_links WHERE attachment_id=?", (aid,)).fetchone()
        self.assertEqual(link["log_id"], log["id"])
        self.assertIsNone(link["flight_id"])
        self.con.commit()
        self.assertEqual(self.client.delete(f'/api/logs/{log["id"]}').status_code, 200)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 0)

    def test_log_inheritance_does_not_bypass_visibility(self):
        log, flight = self.log(project=2)
        self.con.execute("UPDATE flights SET project_id=1 WHERE id=?", (flight["id"],))
        self.con.commit()
        self.sign_in(1)
        self.upload_file([log])
        self.sign_in(2)
        response = self.client.get(f'/api/attachments?kind=flight&id={flight["id"]}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["files"], [])

    def test_search_and_api_keys(self):
        paper = self.paper()
        self.assertEqual(self.client.get("/api/attachments/targets?q=Drone").json["targets"][0]["id"], paper["id"])
        self.assertEqual(self.client.get(f'/api/attachments/targets?q={paper["id"]}').json["targets"][0]["id"], paper["id"])
        self.sign_in(3)
        self.assertEqual(self.client.get("/api/attachments/targets?q=Drone").json["targets"], [])
        self.sign_in(2)
        token = "logdb_" + "a" * 30
        self.con.execute("INSERT INTO api_keys(user_id,name,key_hash,prefix,created_at) VALUES (2,'test',?,?, 'now')",
                         (hashlib.sha256(token.encode()).hexdigest(), "test"))
        self.con.commit()
        key = {"Authorization": "Bearer " + token}
        # A key uploads to records its user may edit and links the same file to more of them
        other = self.paper(date="2010-01-02")
        metadata = quote(json.dumps(dict(filename="ctr.pdf", targets=[paper])))
        response = self.client.post("/api/attachments", data=b"%PDF", content_type="application/octet-stream",
                                    headers={**key, "X-Attachment-Metadata": metadata})
        self.assertEqual(response.status_code, 201, response.json)
        aid = response.json["id"]
        response = self.client.post(f"/api/attachments/{aid}/links", json=dict(targets=[other]), headers=key)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachment_links WHERE attachment_id=?",
                                          (aid,)).fetchone()[0], 2)
        # ... but not to records of a project without edit rights
        self.sign_in(1)
        private = self.paper(project_id=2)
        self.sign_in(2)
        metadata = quote(json.dumps(dict(filename="ctr.pdf", targets=[private])))
        response = self.client.post("/api/attachments", data=b"%PDF", content_type="application/octet-stream",
                                    headers={**key, "X-Attachment-Metadata": metadata})
        self.assertIn(response.status_code, (403, 404))
        response = self.client.post(f"/api/attachments/{aid}/links", json=dict(targets=[private]), headers=key)
        self.assertIn(response.status_code, (403, 404))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 1)
        # Unlinking (and so deleting) still needs a browser session
        link = self.con.execute("SELECT id FROM attachment_links WHERE attachment_id=?", (aid,)).fetchone()["id"]
        self.assertEqual(self.client.delete(f"/api/attachments/{aid}/links/{link}", headers=key).status_code, 403)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachment_links").fetchone()[0], 2)

    def test_upload_rechecks_permissions_and_cleans_staging(self):
        paper = self.paper()
        original = storage.save_attachment

        def revoke(*args, **kwargs):
            saved = original(*args, **kwargs)
            self.con.execute("UPDATE project_members SET role='view' WHERE user_id=2")
            self.con.commit()
            return saved

        with patch("storage.save_attachment", side_effect=revoke):
            self.assertEqual(self.upload_file([paper]).status_code, 403)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 0)
        root = self.ns["STORAGE_DIR"]
        self.assertEqual(list((root / ".incoming-attachments").glob("*")), [])
        self.assertEqual(list((root / "attachments").glob("*")), [])

    def test_ingest_compresses_and_adds_renamed_companion(self):
        stage = self.ns["STAGE_DIR"] / "pair-test"
        stage.mkdir(parents=True, exist_ok=True)
        raw = b'12.0 7 INFO_MSG "[PFC] goal: wind tunnel"\n' * 100
        (stage / "original.data").write_bytes(raw)
        iid = self.ns["insert_ingest"](self.con, "pair", "original.data", "pair-test",
                                      "original.data", None, len(raw), 2)
        row = self.con.execute("SELECT * FROM ingest WHERE id=?", (iid,)).fetchone()
        self.ns["ingest_pair"](self.con, row)
        log = self.con.execute("SELECT * FROM logs").fetchone()
        self.assertTrue(log["data_path"].endswith(".data.gz"))
        self.assertIsNone(log["log_path"])
        stage.mkdir(parents=True, exist_ok=True)
        (stage / "renamed.data").write_bytes(raw)
        xml = b'<configuration><conf><aircraft ac_id="7" name="Drone"/></conf></configuration>'
        (stage / "renamed.log").write_bytes(xml)
        iid = self.ns["insert_ingest"](self.con, "pair", "renamed.data", "pair-test",
                                      "renamed.data", "renamed.log", len(raw), 2)
        row = self.con.execute("SELECT * FROM ingest WHERE id=?", (iid,)).fetchone()
        self.ns["ingest_pair"](self.con, row)
        log = self.con.execute("SELECT * FROM logs").fetchone()
        self.assertTrue(log["log_path"].endswith("original.log.gz"))
        root = self.ns["STORAGE_DIR"]
        self.addCleanup((root / log["data_path"]).unlink, missing_ok=True)
        self.addCleanup((root / log["log_path"]).unlink, missing_ok=True)
        self.assertEqual(gzip.decompress((root / log["log_path"]).read_bytes()), xml)
        self.assertFalse(stage.exists())
        if not Path(self.ns["FASTREADER"]).is_file():
            self.skipTest("Parser binary not built; ingest roundtrip above passed.")
        self.ns["parse_log"](log["id"])
        parsed = self.con.execute("SELECT status,error,paths_file FROM logs").fetchone()
        self.assertEqual(parsed["status"], "done", parsed["error"])
        self.addCleanup((root / parsed["paths_file"]).unlink, missing_ok=True)

    def test_compressed_logs_download_zip_and_parser_input(self):
        log, _ = self.log()
        root = self.ns["STORAGE_DIR"]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.data"
            companion = Path(directory) / "sample.log"
            raw = b"12.0 7 GPS 1 2 3\n" * 1000
            xml = b"<protocol>test</protocol>"
            source.write_bytes(raw)
            companion.write_bytes(xml)
            sha = hashlib.sha256(raw).hexdigest()
            data_rel, log_rel = self.ns["store_files"](sha, "sample.data", source, companion)
            self.addCleanup((root / data_rel).unlink, missing_ok=True)
            self.addCleanup((root / log_rel).unlink, missing_ok=True)
            self.assertEqual(gzip.decompress((root / data_rel).read_bytes()), raw)
            self.con.execute("UPDATE logs SET data_path=?,log_path=?,sha256=? WHERE id=?",
                             (data_rel, log_rel, sha, log["id"]))
            self.con.commit()
            response = self.client.get(f'/api/logs/{log["id"]}/download/data')
            self.assertEqual(response.data, raw)
            self.assertIn('filename="sample.data"', response.headers["Content-Disposition"])
            # Browsers accept gzip: the stored file goes out as is and the browser decompresses it.
            response = self.client.get(f'/api/logs/{log["id"]}/download/data', headers={"Accept-Encoding": "gzip"})
            self.assertEqual(response.headers["Content-Encoding"], "gzip")
            self.assertEqual(response.data, (root / data_rel).read_bytes())
            self.assertEqual(gzip.decompress(response.data), raw)
            self.assertIn('filename="sample.data"', response.headers["Content-Disposition"])
            self.assertNotIn("Content-Encoding", self.client.get(
                f'/api/logs/{log["id"]}/download/data', headers={"Accept-Encoding": "gzip;q=0, deflate"}).headers)
            response = self.client.get(f'/api/logs/{log["id"]}/download')
            with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                self.assertEqual(archive.namelist(), ["sample.data", "sample.log"])
                self.assertEqual(archive.read("sample.data"), raw)
                self.assertEqual(archive.read("sample.log"), xml)
                self.assertEqual(archive.getinfo("sample.data").compress_type, zipfile.ZIP_DEFLATED)

            def parser(cmd, stdout, stderr):
                parsed = Path(cmd[-1])
                self.assertEqual(parsed.read_bytes(), raw)
                self.assertEqual(parsed.with_suffix(".log").read_bytes(), xml)
                stdout.write(b'{"aircraft":[]}')
                return type("Result", (), dict(returncode=0))()

            with patch("subprocess.run", side_effect=parser):
                self.ns["parse_log"](log["id"])
            row = self.con.execute("SELECT status,error,paths_file FROM logs WHERE id=?", (log["id"],)).fetchone()
            self.assertEqual(row["status"], "done", row["error"])
            self.addCleanup((root / row["paths_file"]).unlink, missing_ok=True)
            self.assertEqual(list(self.ns["TMP_DIR"].glob("parse-*")), [])


class StorageTests(unittest.TestCase):
    def test_local_compression_transfers_only_finished_gzip_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "local"
            work.mkdir()
            source = work / "sample.data"
            raw = b"example telemetry\n" * 10000
            source.write_bytes(raw)
            destination = root / "shared" / "sample.data.gz"
            real_replace = storage.os.replace

            def publish(temporary, final):
                self.assertEqual(Path(temporary).parent, destination.parent)
                self.assertEqual(gzip.decompress(Path(temporary).read_bytes()), raw)
                self.assertFalse(destination.exists())
                real_replace(temporary, final)

            with patch.object(storage.os, "replace", side_effect=publish):
                storage.gzip_file(source, destination, work_dir=work)
            self.assertEqual(gzip.decompress(destination.read_bytes()), raw)
            self.assertLess(destination.stat().st_size, len(raw) // 10)
            self.assertEqual(list(work.iterdir()), [source])
            self.assertEqual(list(destination.parent.iterdir()), [destination])
            destination.unlink()
            with patch.object(storage.os, "replace", side_effect=OSError("share disconnected")):
                with self.assertRaisesRegex(OSError, "disconnected"):
                    storage.gzip_file(source, destination, work_dir=work)
            self.assertEqual(list(work.iterdir()), [source])
            self.assertEqual(list(destination.parent.iterdir()), [])

    def test_local_disk_headroom_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(storage.shutil, "disk_usage", return_value=type("Usage", (), {"free": 100})()):
                with self.assertRaisesRegex(OSError, "disk space"):
                    storage.ensure_space(directory, required=51, minimum_free=50)
                storage.ensure_space(directory, required=50, minimum_free=50)
                with self.assertRaisesRegex(OSError, "disk space"):
                    storage.copy_local(io.BytesIO(b"data"), Path(directory) / "stage", minimum_free=100)
            self.assertEqual((Path(directory) / "stage").stat().st_size, 0)

    def test_stream_limit_without_content_length_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "exceeds"):
                storage.save_attachment(io.BytesIO(b"x" * 2000), directory, 1000)
            self.assertEqual(list(Path(directory).iterdir()), [])
