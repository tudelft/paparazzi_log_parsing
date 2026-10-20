import ast
import io
import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from flask import Flask, abort, jsonify, request

import db
import weather


class WeatherTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(db.SCHEMA)
        self.entry = dict(takeoff_lat=52.1234, takeoff_lon=4.1234,
                          takeoff_utc="2022-09-09T09:40:15.000Z", date_ok=True)
        weather._last_request = None
        self.payload = {"hourly": {"time": ["2022-09-09T09:00"],
                                  **{k: [2.5] for k in weather.FIELDS}}}

    def tearDown(self):
        self.con.close()

    def response(self, payload=None):
        return io.BytesIO(json.dumps(self.payload if payload is None else payload).encode())

    def test_no_network_until_clicked_and_persistent_cache(self):
        with patch.object(weather, "urlopen", return_value=self.response()) as request:
            self.assertIsNone(weather.lookup(self.con, self.entry))
            request.assert_not_called()
            result = weather.lookup(self.con, self.entry, fetch=True)
            self.assertEqual(result["wind_speed_ms"], 2.5)
            params = parse_qs(urlsplit(request.call_args.args[0]).query)
            self.assertEqual(params["latitude"], ["52.12"])
            self.assertEqual(params["wind_speed_unit"], ["ms"])
            self.assertEqual(params["timezone"], ["UTC"])
            nearby = {**self.entry, "takeoff_lat": 52.124, "takeoff_utc": "2022-09-09T09:55:00Z"}
            self.assertEqual(weather.lookup(self.con, nearby, fetch=True), result)
            self.assertEqual(weather.lookup(self.con, self.entry), result)
            self.assertEqual(request.call_count, 1)

    def test_invalid_location_dates_and_recent_flights(self):
        for changes in ({"takeoff_lat": None}, {"takeoff_lat": float("nan")},
                        {"takeoff_lon": 181}, {"date_ok": False},
                        {"takeoff_utc": "1980-not-a-date"}, {"takeoff_utc": "2022-09-09"},
                        {"takeoff_utc": "1939-01-01T00:00:00Z"},
                        {"takeoff_utc": "2099-01-01T00:00:00Z"}):
            with self.subTest(changes=changes), patch.object(weather, "urlopen") as request:
                with self.assertRaises(weather.WeatherError) as error:
                    weather.lookup(self.con, {**self.entry, **changes}, fetch=True)
                self.assertEqual(error.exception.status, 400)
                request.assert_not_called()

    def test_rate_limit_and_busy_requests(self):
        weather._last_request = weather.time.monotonic()
        with self.assertRaises(weather.WeatherError) as error:
            weather.lookup(self.con, self.entry, fetch=True)
        self.assertEqual(error.exception.status, 429)
        with weather._lock:
            with self.assertRaises(weather.WeatherError) as error:
                weather.lookup(self.con, self.entry, fetch=True)
            self.assertEqual(error.exception.status, 429)

    def test_errors_not_cached_and_retried(self):
        with patch.object(weather, "urlopen", side_effect=URLError("offline")):
            with self.assertRaises(weather.WeatherError):
                weather.lookup(self.con, self.entry, fetch=True)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM weather_cache").fetchone()[0], 0)
        weather._last_request = None
        with patch.object(weather, "urlopen", return_value=self.response()):
            self.assertIsNotNone(weather.lookup(self.con, self.entry, fetch=True))

    def test_null_and_malformed_responses(self):
        payloads = [{}, {"hourly": {"time": []}},
                    {"hourly": {"time": ["2022-09-09T09:00"],
                                **{k: [None] for k in weather.FIELDS}}}]
        for payload in payloads:
            weather._last_request = None
            with patch.object(weather, "urlopen", return_value=self.response(payload)):
                with self.assertRaises(weather.WeatherError):
                    weather.lookup(self.con, self.entry, fetch=True)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM weather_cache").fetchone()[0], 0)

    def test_endpoint_visibility_and_explicit_fetch(self):
        # Load only the route, without starting the application's parse workers.
        source = Path(__file__).with_name("app.py").read_text()
        module = ast.parse(source)
        self.assertTrue(any(isinstance(n, ast.Import) and any(a.name == "weather" for a in n.names)
                            for n in module.body), "weather must be imported during normal server startup")
        route = next(n for n in module.body
                     if isinstance(n, ast.FunctionDef) and n.name == "flight_weather")
        app = Flask(__name__)
        entries = []
        namespace = dict(app=app, abort=abort, jsonify=jsonify, request=request, weather=weather,
                         logbook_rows=lambda: entries, conn=lambda: self.con)
        exec(compile(ast.Module(body=[route], type_ignores=[]), "app.py", "exec"), namespace)
        client = app.test_client()
        with patch.object(weather, "urlopen", return_value=self.response()) as provider:
            self.assertEqual(client.post("/api/flights/42/weather").status_code, 404)
            provider.assert_not_called()
            entries.append({**self.entry, "sources": [{"flight_id": 42}]})
            self.assertEqual(client.get("/api/flights/42/weather").json, {"weather": None})
            provider.assert_not_called()
            self.assertEqual(client.post("/api/flights/42/weather").status_code, 200)
            self.assertEqual(client.get("/api/flights/42/weather").status_code, 200)
            self.assertEqual(provider.call_count, 1)


if __name__ == "__main__":
    unittest.main()
