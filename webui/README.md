# Paparazzi log web interface

Upload Paparazzi logs (`.data` + `.log` files, `.zip` archives or whole folders) in the browser. Every log is kept on
disk, parsed with the fast C++ parser (`../cpp`) and stored in a SQLite database.

## Run

    cd ../cpp && mkdir -p build && cd build && cmake -DCMAKE_BUILD_TYPE=Release .. && make   # builds the parser
    cd ../../webui
    pip install -r requirements.txt
    python3 app.py                                                  # http://127.0.0.1:8000

Environment variables: `PPRZ_WEB_DATA` (storage + database, default `./data`), `FASTREADER` (parser binary),
`PPRZ_PATH_RATE` (flight path points per second, default 5), `PPRZ_WORKERS` (parallel parsers, default 2),
`HOST`, `PORT`.

## Accounts

Everything (pages, API, downloads) requires a login. Accounts are local for now and managed on the command line:

    python3 app.py create-user alice --admin --name "Alice"   # asks for a password (min. 10 characters)
    python3 app.py create-user bob
    python3 app.py set-password bob
    python3 app.py list-users
    python3 app.py deactivate-user bob / activate-user bob

Passwords are stored hashed, sessions last 12 hours (`PPRZ_SESSION_HOURS`), repeated failed logins are rate limited.
Serve it over https and set `PPRZ_COOKIE_SECURE=1`. Logs can be deleted by their uploader or an admin.
The `users` table has `provider`/`subject` columns so a TU Delft SSO login (OpenID Connect through Microsoft Entra ID
or SURFconext) can be added later; that needs a client ID/secret registered with TU Delft ICT or SURFconext.

## Behaviour

- `.data` files are paired with the `.log` of the same name (same folder / zip directory). Other files are ignored.
- The SHA-256 of the `.data` file identifies a log. A log that is already known is not stored or parsed again.
  (If the first upload had no `.log` and a later one has, the `.log` is added and the log is re-parsed.)
  The hash is passed to the parser (`--sha256`) and is also part of its JSON output (`log.sha256`).
- Each parsed log records which parser produced it (`parser_name`, `parser_version`, `parser_path_rate_hz`).
  After installing a newer parser (bump `PARSER_VERSION` in `cpp/fastreader.cpp`) use **Re-parse outdated**
  (`POST /api/reparse-outdated`) to re-run it on every log with another version, and on failed logs.
- Files are stored under `data/logs/<sha[:2]>/<sha>/`, the database is `data/logs.db`.

## Database

`logs` (hash, files, status, parser version) -> `aircraft` (crew, goal, versions, messages, config) ->
`flights` (takeoff/landing, gzip-compressed path) and `setting_changes` (settings changed from their default).

## API

`POST /api/upload`, `GET /api/logs`, `GET /api/logs/<id>`, `GET /api/aircraft/<id>/config`,
`GET /api/flights/<id>/path`, `GET /api/logs/<id>/download/{data,log}`, `POST /api/logs/<id>/reparse`,
`POST /api/reparse-outdated`, `DELETE /api/logs/<id>`, `GET /api/status`.

## Log types and the logbook

The parser detects whether a log is an onboard SD log (`onboard_sd`) or a ground station log (`gcs`) and reports it as
`log.type` / `log.type_basis` (file name `_SD`, `.log` header, or ground-only messages such as `LINK_REPORT`/`PONG`).
The logbook (`/logbook`) lists one row per flight and merges the SD and GCS recordings of the same flight (same
aircraft, overlapping takeoff-landing interval, 60 s slack) into one row: flight data come from the SD log, PIC/PAC/GCS
and goal from whichever log has them. Each row shows its sources and offers a download for each log. Admins manage
aliases (names linked to users) and named takeoff/landing areas drawn on a map at `/admin`. A named area can be
marked as indoors; the logbook shows outdoor flights by default (filter: outdoor / indoor / all), and combines
flights within 1 km by default.

## Real flight or not

The autopilot's "in flight" flag also toggles for short arming on the ground, motor tests or hand-carrying. For each
takeoff/landing the parser therefore adds `assessment` (`verdict`: `flight`, `uncertain` or `ground`, plus `score`,
`reasons` and the raw `metrics`). It is a score built from the duration, altitude change (INS, else barometer, else GPS),
horizontal movement, speed, tilt and the dynamic INS acceleration, so indoor and tethered flights (little horizontal
movement) still count when they climb and accelerate. Flights under 2 s can never be `flight`. If the motors were off for most of the
flight (`motors_on` in ROTORCRAFT_STATUS) it is always `ground`. The logbook hides `ground` and `uncertain` verdicts unless
**Show uncertain/non-flights** is ticked, marks them in the list and shows the reasons in the details. Re-parse existing logs (parser version 1.2.2) to get the verdicts.
