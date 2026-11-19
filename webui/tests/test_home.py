import datetime
import io
import json
import os
import shutil
import subprocess
import unittest
from unittest.mock import patch

from urllib.parse import quote

import attachments
import weather
from .test_historical import AppTests


def days_ago(n):
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


class HomeTests(AppTests):
    def setUp(self):
        super().setUp()
        weather._forecasts.clear()
        self.thumbs = self.app.config["ATTACHMENT_THUMBS"]
        office = patch.dict(self.app.config, ATTACHMENT_OFFICE=None)
        office.start()
        self.addCleanup(office.stop)

    def tearDown(self):
        shutil.rmtree(self.thumbs, ignore_errors=True)
        super().tearDown()

    def upload_file(self, targets, content=b"wind,temp\n1,20\n" * 100, name="wind.csv"):
        metadata = {"filename": name, "description": "", "targets": targets}
        return self.client.post("/api/attachments", data=content, content_type="application/octet-stream",
                                headers={"X-Attachment-Metadata": quote(json.dumps(metadata))})

    def flight(self, project, takeoff, pic=2, goal="Hover"):
        log = self.con.execute(  # uploaded by the admin: the pilot sees it only through the project
            "INSERT INTO logs(name,sha256,data_path,status,uploaded_at,uploaded_by,project_id,start_utc)"
            " VALUES ('x.data',?,'x.data.gz','done','now',1,?,?)", (os.urandom(16).hex(), project, takeoff)).lastrowid
        aircraft = self.con.execute("INSERT INTO aircraft(log_id,ac_id,name,drone_id,goal) VALUES (?,7,'Drone',1,?)",
                                    (log, goal)).lastrowid
        fid = self.con.execute(
            "INSERT INTO flights(aircraft_id,idx,takeoff_utc,duration_s,project_id,pic_user_id,takeoff_lat,takeoff_lon,"
            "has_gps_position) VALUES (?,0,?,600,?,?,52,4,1)", (aircraft, takeoff, project, pic)).lastrowid
        self.con.commit()
        return fid

    def test_only_what_the_user_can_see(self):
        mine = self.flight(1, days_ago(3), goal="Visible")
        private = self.flight(2, days_ago(1), goal="Private")
        self.sign_in(1)  # the admin may add files to both
        for fid, name in ((mine, "mine.jpg"), (private, "private.mp4")):
            self.assertEqual(self.upload_file([dict(kind="flight", id=fid)], content=b"x" * 100, name=name).status_code,
                             201)
        self.upload_file([dict(kind="flight", id=mine)], name="notes.csv")  # not media
        self.sign_in(2)
        d = self.client.get("/api/home").json
        self.assertEqual([r["goal"] for r in d["recent"]], ["Visible"])
        self.assertEqual([(m["filename"], m["url"]) for m in d["media"]], [("mine.jpg", f"/flight/{mine}")])
        self.assertEqual((d["flying"]["flights_90d"], d["flying"]["days_since"]), (1, 3))
        self.assertEqual(sum(m["flights"] for m in d["flying"]["months"]), 1)
        self.assertEqual((d["places"], d["place_id"]), ([{"id": 1, "name": "Field"}], 1))
        self.assertEqual(d["projects"], ["Research"])
        self.sign_in(4)  # in no project
        d = self.client.get("/api/home").json
        self.assertEqual((d["recent"], d["media"], d["places"], d["place_id"]), ([], [], [], None))
        self.assertEqual(self.client.get("/api/home/weather?place=1").status_code, 404)

    def test_signed_out_logbook_link_returns_to_the_logbook(self):
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get("/").headers["Location"], "/login?next=%2F")  # the Operations Manual's link
        self.assertEqual(anonymous.get("/home").headers["Location"], "/login?next=%2Fhome")
        self.assertEqual(self.client.get("/home").status_code, 200)

    def test_attention_items(self):
        today = datetime.date.today()
        self.con.executemany("INSERT INTO qualification_types(id,name,requires_evidence) VALUES (?,?,0)",
                             [(5, "A2 certificate"), (6, "Old licence"), (7, "Pending licence")])
        self.con.executemany(
            "INSERT INTO qualification_submissions(user_id,type_id,status,valid_until,submitted_at) VALUES (2,?,?,?,'now')",
            [(5, "approved", (today + datetime.timedelta(days=10)).isoformat()),
             (6, "approved", (today - datetime.timedelta(days=400)).isoformat()),  # long gone: left on purpose
             (7, "pending", (today + datetime.timedelta(days=5)).isoformat())])  # not approved: nothing to renew
        self.con.execute("INSERT INTO logs(name,sha256,data_path,status,uploaded_at,uploaded_by,project_id)"
                         " VALUES ('undated.data',?,'u.data.gz','done','now',2,1)", (os.urandom(16).hex(),))
        self.con.execute("INSERT INTO logs(name,sha256,data_path,status,uploaded_at,uploaded_by,project_id)"
                         " VALUES ('broken.data',?,'b.data.gz','error','now',5,1)", (os.urandom(16).hex(),))
        self.con.commit()
        d = self.client.get("/api/home").json
        self.assertEqual([(q["name"], q["days"]) for q in d["qualifications"]], [("A2 certificate", 10)])
        self.assertEqual(d["logs"], {"undated": 1, "failed": 0})  # the failed log is someone else's upload
        self.sign_in(3)
        self.assertEqual(self.client.get("/api/home").json["qualifications"], [])

    def test_weather_place_and_forecast(self):
        self.flight(1, days_ago(2))
        self.con.execute("INSERT INTO places(id,name,indoor,shape) VALUES (2,'Hall',1,?)",
                         (json.dumps({"type": "polygon", "points": [[52, 4], [52.1, 4.1], [52, 4.1]]}),))
        self.con.commit()
        self.assertEqual(self.client.put("/api/account/home", json={"place_id": 2}).status_code, 400)  # indoor
        self.assertEqual(self.client.put("/api/account/home", json={"place_id": 1}).json["place_id"], 1)
        self.assertEqual(self.client.get("/api/home").json["place_saved"], 1)
        now = datetime.datetime.now(datetime.timezone.utc).replace(minute=0, second=0, microsecond=0)
        times = [(now + datetime.timedelta(hours=h)).strftime("%Y-%m-%dT%H:00") for h in range(-2, 30)]
        body = {"timezone": "GMT", "utc_offset_seconds": 0, "hourly": {"time": times, **{
            field: [1.5] * len(times) for field in weather.FORECAST_FIELDS}}}
        calls = []

        def fake(url, timeout):
            calls.append(url)
            return io.BytesIO(json.dumps(body).encode())
        with patch("weather.urlopen", fake):
            w = self.client.get("/api/home/weather?place=1").json
            self.assertEqual(self.client.get("/api/home/weather?place=1").status_code, 200)  # from the cache
        self.assertEqual(len(calls), 1)
        self.assertIn("latitude=52.0000", calls[0])
        self.assertEqual((w["place"], len(w["hours"]), w["hours"][0]["time"]), ("Field", 8, times[2]))
        self.assertEqual(self.client.get("/api/home/weather?place=2").status_code, 404)

    def test_media_newest_flight_first(self):
        old, new = self.flight(1, days_ago(30), goal="Old"), self.flight(1, days_ago(2), goal="New")
        self.sign_in(1)
        for fid, name in ((new, "new.jpg"), (old, "old.mp4")):  # the old flight's video is uploaded last
            self.assertEqual(self.upload_file([dict(kind="flight", id=fid)], content=b"x" * 100, name=name).status_code,
                             201)
        media = self.client.get("/api/home").json["media"]
        self.assertEqual([m["filename"] for m in media], ["new.jpg", "old.mp4"])
        self.assertEqual(media[1]["flown"][:10], days_ago(30)[:10])

    def test_thumbnail_made_once_and_only_for_visible_media(self):
        fid = self.flight(1, days_ago(1))
        private = self.flight(2, days_ago(1))
        self.sign_in(1)
        photo = self.upload_file([dict(kind="flight", id=fid)], content=b"p" * 50, name="photo.jpg").json["id"]
        hidden = self.upload_file([dict(kind="flight", id=private)], content=b"q" * 50, name="secret.jpg").json["id"]
        csv = self.upload_file([dict(kind="flight", id=fid)], name="data.csv").json["id"]
        self.sign_in(2)
        calls = []

        def fake(source, compressed, size, video, ffmpeg, target):
            calls.append(video)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"\xff\xd8thumb")
            return True
        with patch.dict(self.app.config, ATTACHMENT_FFMPEG="ffmpeg"), patch("attachments.thumbnail", fake):
            for _ in range(2):
                r = self.client.get(f"/api/attachments/{photo}/thumb")
                self.assertEqual((r.status_code, r.mimetype, r.data), (200, "image/jpeg", b"\xff\xd8thumb"))
                r.close()
            self.assertEqual(calls, [False])
            self.assertEqual(self.client.get(f"/api/attachments/{hidden}/thumb").status_code, 404)
            self.assertEqual(self.client.get(f"/api/attachments/{csv}/thumb").status_code, 404)
        with patch.dict(self.app.config, ATTACHMENT_FFMPEG=None):
            self.assertEqual(self.client.get(f"/api/attachments/{photo}/thumb").status_code, 404)  # off: plain tile

    def test_failed_thumbnail_is_not_retried(self):
        fid = self.flight(1, days_ago(1))
        video = self.upload_file([dict(kind="flight", id=fid)], content=b"v" * 50, name="clip.mp4").json["id"]
        calls = []
        with patch.dict(self.app.config, ATTACHMENT_FFMPEG="ffmpeg"), \
                patch("attachments.thumbnail", lambda *a: calls.append(a[3]) or False):
            self.assertEqual(self.client.get(f"/api/attachments/{video}/thumb").status_code, 404)
            self.assertEqual(self.client.get(f"/api/attachments/{video}/thumb").status_code, 404)
        self.assertEqual(calls, [True])

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed")
    def test_ffmpeg_thumbnail_of_a_video(self):
        work = self.thumbs.parent / "ffmpeg-test"
        work.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, work, True)
        clip = work / "clip.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=10", "-t", "2",
                        "-pix_fmt", "yuv420p", "-y", str(clip)], check=True)
        target = work / "out.jpg"
        self.assertTrue(attachments.thumbnail(clip, False, clip.stat().st_size, True, shutil.which("ffmpeg"), target))
        self.assertEqual(target.read_bytes()[:2], b"\xff\xd8")
        self.assertFalse(attachments.thumbnail(work / "missing.mp4", False, 0, True, shutil.which("ffmpeg"),
                                               work / "none.jpg"))


if __name__ == "__main__":
    unittest.main()
