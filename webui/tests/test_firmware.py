import copy
import gzip
import io
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import db
import config
import firmware
from .test_clock import START, StoredClockTests, gps_line, parse_fixture
from .test_historical import AppTests


def fixture(revision="abcdef1234", start=datetime(1980, 1, 9, tzinfo=timezone.utc)):
    lines = []
    for t in (0, 20, 40, 60):
        parts = gps_line(t, START + timedelta(seconds=t)).split()
        parts[4] = "0"
        lines.append(" ".join(parts) + "\n")
    lines += [f'60 5 AUTOPILOT_VERSION 70100 "v7.1-1-g{revision}-dirty"\n',
              "61 5 ROTORCRAFT_STATUS 0 1\n", "121 5 ROTORCRAFT_STATUS 0 0\n"]
    return parse_fixture(lines, start)


METADATA = {"resolution": "resolved", "commit": "a" * 40,
            "committed_at": "2023-09-05T12:00:00.000Z", "authored_at": "2023-09-04T12:00:00.000Z"}


class FirmwareEvidenceTests(unittest.TestCase):
    def test_persistent_resolution_cache_and_configuration_scope(self):
        full = "abcdef1234" + "a" * 30
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cache.db"
            db.init(path)
            con = sqlite3.connect(path)
            with patch.object(firmware, "lookup", return_value={**METADATA, "commit": full}) as lookup:
                resolved = firmware.resolve(con, full[:10], "team/private", "test-token")
                self.assertEqual(resolved["commit"], full)
                self.assertEqual(lookup.call_count, 1)
            con.close()
            con = sqlite3.connect(path)
            self.addCleanup(con.close)
            with patch.object(firmware, "lookup", return_value=METADATA) as lookup:
                self.assertEqual(firmware.resolve(con, full[:10], "team/private", "test-token"), resolved)
                lookup.assert_not_called()
                firmware.resolve(con, full[:10], "team/private", "changed-token")
                self.assertEqual(lookup.call_count, 1)
            stored = repr(con.execute("SELECT * FROM firmware_cache").fetchall())
            self.assertNotIn("test-token", stored)
            self.assertNotIn("changed-token", stored)
        self.assertEqual(firmware.resolve(None, full[:10], enabled=False)["resolution"], "lookup_disabled")
        self.assertEqual(firmware.resolve(None, "--bad")["resolution"], "revision_missing")

    def test_github_lookup_order_and_nondefault_public_private_repositories(self):
        full = "abcdef1234" + "a" * 30
        commit = {"sha": full, "commit": {"committer": {"date": METADATA["committed_at"]},
                                         "author": {"date": METADATA["authored_at"]}}}
        empty = {"items": [], "total_count": 0, "incomplete_results": False}
        for target in ("upstream", "global", "public", "private"):
            paths = []

            def request(path, token):
                self.assertEqual(token, "fixture-token")
                paths.append(path)
                if path == f"/repos/paparazzi/paparazzi/commits/{full[:10]}":
                    return commit if target == "upstream" else None
                if path.startswith("/search/commits?"):
                    return {"items": [{"sha": full, "repository": {"full_name": "found/paparazzi"}}],
                            "total_count": 1, "incomplete_results": False} if target == "global" else empty
                if path == "/repos/found/paparazzi":
                    return {"source": {"full_name": "paparazzi/paparazzi"}}
                if path == f"/repos/found/paparazzi/commits/{full[:10]}":
                    return commit
                if path == f"/repos/team/public/commits/{full[:10]}":
                    return commit if target == "public" else None
                if path == f"/repos/team/private/commits/{full[:10]}":
                    return commit
                self.fail(path)

            with self.subTest(target=target), patch.object(firmware, "request_json", side_effect=request):
                result = firmware.lookup(full[:10], ["team/public", "team/private"], "fixture-token")
                self.assertEqual(result["commit"], full)
                self.assertEqual(result["repository"], {"upstream": "paparazzi/paparazzi", "global": "found/paparazzi",
                                                       "public": "team/public", "private": "team/private"}[target])
                self.assertEqual(len(paths), {"upstream": 1, "global": 4, "public": 3, "private": 4}[target])

    def test_search_ambiguity_incomplete_unrelated_and_malformed_responses(self):
        full = "abcdef1234" + "a" * 30
        item = {"sha": full, "repository": {"full_name": "unrelated/project"}}
        for search, expected in (
                ({"items": [item, {**item, "sha": full[:-1] + "b"}], "total_count": 2,
                  "incomplete_results": False}, "revision_ambiguous"),
                ({"items": [], "total_count": 1, "incomplete_results": True}, "search_incomplete"),
                ({"items": [None], "total_count": 1, "incomplete_results": False}, "invalid_response"),
                ({"items": [item], "total_count": "bad", "incomplete_results": False}, "invalid_response")):
            with self.subTest(expected=expected), patch.object(firmware, "request_json", side_effect=[None, search]):
                with self.assertRaises(firmware.LookupError) as error:
                    firmware.lookup(full[:10], [], None)
                self.assertEqual(error.exception.resolution, expected)
        with patch.object(firmware, "request_json", side_effect=[
                None, {"items": [item], "total_count": 1, "incomplete_results": False},
                {"source": {"full_name": "other/project"}}]):
            self.assertEqual(firmware.lookup(full[:10], [], None)["resolution"], "revision_unresolved")

    def test_http_errors_redaction_and_response_validation(self):
        for failure, expected in (
                (HTTPError("https://api.github.com", 429, "secret-response", {"Retry-After": "600"}, None),
                 "github_access_or_rate_limit"),
                (HTTPError("https://api.github.com", 401, "secret-response", {}, None), "github_error"),
                (URLError("secret-response"), "github_unavailable")):
            with self.subTest(expected=expected), patch.object(firmware, "build_opener") as opener:
                opener.return_value.open.side_effect = failure
                with self.assertRaises(firmware.LookupError) as error:
                    firmware.request_json("/repos/paparazzi/paparazzi/commits/abcdef1234", "fixture-token")
                self.assertEqual(error.exception.resolution, expected)
                self.assertNotIn("secret", str(error.exception))
                request = opener.return_value.open.call_args.args[0]
                self.assertEqual(request.full_url, "https://api.github.com/repos/paparazzi/paparazzi/commits/abcdef1234")
                self.assertEqual(request.get_header("Authorization"), "Bearer fixture-token")
        with patch.object(firmware, "build_opener") as opener:
            opener.return_value.open.return_value.__enter__.return_value = io.BytesIO(b'{"ok": true}')
            self.assertEqual(firmware.request_json("/test", None), {"ok": True})
        with self.assertRaises(firmware.LookupError):
            firmware.commit_metadata({"sha": "f" * 40}, "abcdef1234", "team/fork")
        self.assertIsNone(firmware.NoRedirects().redirect_request(None, None, 302, None, None, "https://example.invalid"))

    def test_misses_expire_and_outages_back_off_across_hashes(self):
        con = sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        con.executescript(db.SCHEMA)
        missing = {"resolution": "revision_unresolved", "lookup_warning": "not found"}
        with patch.object(firmware.time, "time", return_value=1000), patch.object(
                firmware, "lookup", return_value=missing) as lookup, self.assertLogs("firmware"):
            self.assertEqual(firmware.resolve(con, "abcdef1234"), missing)
            self.assertEqual(firmware.resolve(con, "abcdef1234"), missing)
            self.assertEqual(lookup.call_count, 1)
        with patch.object(firmware.time, "time", return_value=5000), patch.object(
                firmware, "lookup", side_effect=firmware.LookupError("github_unavailable", "offline")) as lookup:
            with self.assertLogs("firmware"):
                first = firmware.resolve(con, "abcdef1234")
            self.assertEqual(firmware.resolve(con, "bbbbbbb"), first)
            self.assertEqual(lookup.call_count, 1)
        with patch.object(firmware.time, "time", return_value=6000), patch.object(
                firmware, "lookup", return_value=METADATA) as lookup:
            self.assertEqual(firmware.resolve(con, "abcdef1234"), METADATA)
            self.assertEqual(lookup.call_count, 1)

    def test_repository_configuration_is_names_only(self):
        self.assertEqual(firmware.repositories("team/public, team/private\nteam/public"), ["team/public", "team/private"])
        for bad in ("https://github.com/team/repo", "token@team/repo", "team/..", "team/repo?token=abc"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                firmware.repositories(bad)

    def test_config_check_hides_token(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, LOGDB_FIRMWARE_GITHUB_TOKEN="fixture-only-token",
                            LOGDB_WEB_DATA=folder, LOGDB_LOG_DIR=folder, LOGDB_BACKUP_DIR=""), patch(
                    "sys.stdout", output):
                config.check()
        self.assertIn("LOGDB_FIRMWARE_GITHUB_TOKEN", output.getvalue())
        self.assertNotIn("fixture-only-token", output.getvalue())
        self.assertIn("(hidden)", output.getvalue())

    def test_week_estimate_updates_all_timestamps_but_not_durations(self):
        doc = fixture()
        aircraft = doc["aircraft"][0]
        aircraft["info_messages"] = [{"time_s": 65, "utc": None, "text": "Fixture"}]
        aircraft["flights"][0]["path"] = [{"time_s": 66, "utc": None, "lat": 52, "lon": 4}]
        aircraft["settings_changes"] = {"timeline": [{"time_s": 67, "utc": None}]}
        flight = doc["aircraft"][0]["flights"][0]
        duration = flight["duration_s"]
        original = doc["log"]["start_utc"]
        with patch.object(firmware, "resolve", return_value=METADATA):
            firmware.enrich(doc, "/fixture")
        timing = doc["log"]["timing"]
        self.assertEqual(doc["log"]["start_utc"], START.isoformat(timespec="milliseconds").replace("+00:00", "Z"))
        self.assertEqual(timing["status"], "estimated")
        self.assertEqual(timing["source"], "firmware_gps_estimated")
        self.assertEqual(timing["original_start_utc"], original)
        self.assertTrue(timing["estimate"]["week_uncertain"])
        self.assertEqual(timing["estimate"]["alternative_interval_days"], 7)
        self.assertEqual(flight["takeoff"]["utc"], "2023-09-08T14:26:01.000Z")
        self.assertEqual(flight["landing"]["utc"], "2023-09-08T14:27:01.000Z")
        self.assertEqual(flight["duration_s"], duration)
        self.assertEqual(doc["aircraft"][0]["data_span"]["last_utc"], doc["log"]["end_utc"])
        self.assertEqual(aircraft["info_messages"][0]["utc"], "2023-09-08T14:26:05.000Z")
        self.assertEqual(flight["path"][0]["utc"], "2023-09-08T14:26:06.000Z")
        self.assertEqual(aircraft["settings_changes"]["timeline"][0]["utc"], "2023-09-08T14:26:07.000Z")

    def test_missing_header_can_be_estimated(self):
        doc = fixture(start=None)
        with patch.object(firmware, "resolve", return_value=METADATA):
            firmware.enrich(doc, "/fixture")
        self.assertTrue(doc["log"]["timing"]["estimated"])
        self.assertIsNone(doc["log"]["timing"]["original_start_utc"])
        self.assertEqual(doc["aircraft"][0]["flights"][0]["takeoff"]["utc"], "2023-09-08T14:26:01.000Z")

    def test_plausible_dates_are_never_replaced_even_when_conflicting(self):
        doc = fixture(start=datetime(2020, 1, 1, tzinfo=timezone.utc))
        before = doc["log"]["start_utc"]
        with patch.object(firmware, "resolve", return_value=METADATA):
            firmware.enrich(doc, "/fixture")
        self.assertEqual(doc["log"]["start_utc"], before)
        self.assertEqual(doc["log"]["timing"]["status"], "conflicting")
        self.assertIn("recorded_date_predates_firmware", doc["log"]["timing"]["reasons"])

    def test_unresolved_inconsistent_and_future_evidence_do_not_estimate(self):
        base = fixture()
        for change in ("unresolved", "too_short", "spread", "nonzero_week", "aircraft_conflict", "future", "ancient"):
            doc = copy.deepcopy(base)
            evidence = doc["log"]["timing"]["gps_evidence"]
            metadata = dict(METADATA)
            if change == "unresolved":
                metadata = {"resolution": "revision_unresolved"}
            elif change == "too_short":
                evidence[0]["week_zero_span_s"] = 29
            elif change == "spread":
                evidence[0]["week_zero_offset_spread_s"] = 5.01
            elif change == "nonzero_week":
                evidence[0]["weeks"].append({"value": 1, "samples": 1})
            elif change == "aircraft_conflict":
                other = {**evidence[0], "ac_id": 6, "week_zero_start_tow_s": evidence[0]["week_zero_start_tow_s"] + 6}
                evidence.append(other)
                doc["log"]["timing"]["firmware"].append({**doc["log"]["timing"]["firmware"][0], "ac_id": 6})
            elif change == "future":
                metadata["committed_at"] = "2099-01-01T00:00:00.000Z"
            else:
                metadata["committed_at"] = "1980-01-01T00:00:00.000Z"
            with self.subTest(change=change), patch.object(firmware, "resolve", return_value=metadata):
                firmware.enrich(doc, "/fixture")
                self.assertEqual(doc["log"]["start_utc"], base["log"]["start_utc"])
                self.assertNotEqual(doc["log"]["timing"]["status"], "estimated")

    def test_week_boundary_and_historical_leap_seconds(self):
        for utc in (datetime(2010, 1, 2, 23, 59, 50, tzinfo=timezone.utc),
                    datetime(2023, 9, 9, 23, 59, 50, tzinfo=timezone.utc)):
            offset = sum(utc.timestamp() >= leap for leap in firmware.LEAPS)
            tow = (utc.timestamp() + offset - firmware.GPS_EPOCH) % firmware.WEEK
            doc = fixture()
            evidence = doc["log"]["timing"]["gps_evidence"][0]
            evidence["week_zero_start_tow_s"] = tow
            metadata = {**METADATA, "committed_at": firmware.iso(utc.timestamp() - 3600)}
            with patch.object(firmware, "resolve", return_value=metadata):
                firmware.enrich(doc, "/fixture")
            self.assertEqual(doc["log"]["start_utc"], firmware.iso(utc.timestamp()))


class StoredFirmwareTests(AppTests):
    insert_log = StoredClockTests.insert_log

    def test_parse_pipeline_enriches_and_persists_without_workers(self):
        doc = fixture()
        log_id = self.insert_log("pipeline", doc)
        relative = "firmware-pipeline.data.gz"
        path = self.ns["STORAGE_DIR"] / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wb") as handle:
            handle.write(b"0 5 GPS 3 0 292721750\n")
        self.addCleanup(path.unlink, missing_ok=True)
        self.con.execute("UPDATE logs SET data_path=?,status='queued' WHERE id=?", (relative, log_id))
        self.con.commit()

        def parser(_cmd, stdout, stderr):
            stdout.write(json.dumps(doc).encode())
            return SimpleNamespace(returncode=0)

        with patch.object(self.ns["subprocess"], "run", side_effect=parser), patch.object(
                firmware, "resolve", return_value=METADATA) as resolve:
            self.ns["parse_log"](log_id)
        row = self.con.execute("SELECT * FROM logs WHERE id=?", (log_id,)).fetchone()
        self.assertEqual(row["status"], "done", row["error"])
        self.assertEqual(row["start_utc"], "2023-09-08T14:25:00.000Z")
        self.assertEqual(row["date_estimated"], 1)
        self.assertEqual(json.loads(row["timing"])["status"], "estimated")
        resolve.assert_called_once()
        if row["paths_file"]:
            (self.ns["STORAGE_DIR"] / row["paths_file"]).unlink(missing_ok=True)

    def test_estimate_persists_and_manual_confirmation_survives_reparse(self):
        doc = fixture()
        with patch.object(firmware, "resolve", return_value=METADATA):
            firmware.enrich(doc, "/fixture")
        log_id = self.insert_log("estimate", doc)
        flight_id = self.con.execute("SELECT id FROM flights").fetchone()[0]
        for url in (f"/api/logs/{log_id}", f"/api/flights/{flight_id}/detail"):
            result = self.client.get(url).json
            self.assertEqual(result["timing"], doc["log"]["timing"])
        row = self.client.get("/api/logbook?view=overview").json[0]
        self.assertTrue(row["date_estimated"])
        self.assertTrue(self.client.get(f"/api/logs/{log_id}").json["date_estimated"])
        response = self.client.put(f"/api/logs/{log_id}/date", json={"date_override": "2023-09-15"})
        self.assertEqual(response.status_code, 200)
        import db
        db.store_parsed(self.con, log_id, doc, None)
        self.con.commit()
        result = self.client.get(f"/api/logs/{log_id}").json
        self.assertFalse(result["date_estimated"])
        self.assertEqual(result["timing"]["status"], "manual")
        self.assertEqual(result["timing"]["parsed_status"], "estimated")
        self.assertEqual(result["start_utc"], "2023-09-15T14:25:00.000Z")

    def test_estimated_recordings_do_not_automatically_merge(self):
        doc = fixture()
        with patch.object(firmware, "resolve", return_value=METADATA):
            firmware.enrich(doc, "/fixture")
        doc["log"]["type"] = "onboard_sd"
        self.insert_log("sd", doc)
        verified = copy.deepcopy(doc)
        verified["log"]["type"] = "gcs"
        verified["log"]["timing"]["estimated"] = False
        verified["log"]["timing"]["status"] = "valid"
        self.insert_log("gcs", verified)
        rows = self.client.get("/api/logbook").json
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(r["date_estimated"] for r in rows), 1)
