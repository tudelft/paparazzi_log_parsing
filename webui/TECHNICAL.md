# Technical and administration guide

Detailed setup, operations, permissions and implementation reference for the flight log database.
For everyday use, start with the [user guide](README.md). Coding assistants should also read
[AGENTS.md](../AGENTS.md).

- [Try it locally](#try-it-locally)
- [Install on a server](#install-on-a-server)
- [Storage and backup](#storage-and-backup)
- [Settings](#settings)
- [Accounts and TU Delft login](#accounts-and-tu-delft-login)
- [Tests](#tests)
- [Interface layout](#interface-layout)
- [Training questionnaires](#training-questionnaires)
- [Person profiles](#person-profiles)
- [Private licences and qualifications](#private-licences-and-qualifications)
- [Uploading with a script](#uploading-with-a-script)
- [How it works](#how-it-works)

## Try it locally

Follow the [quick start](../README.md#quick-start).

## Install on a server

On Ubuntu or Debian, [install.sh](install.sh) installs and updates everything:

    sudo bash install.sh                  # install, or update an existing install keeping its settings
    sudo bash install.sh --reconfigure    # update and answer all questions again

It asks for the host name, how to serve the site (https with Let's Encrypt via Caddy, https with your own
certificate via nginx, plain http, or no web server so you can add your own proxy), the log and backup directories,
the backup schedule, the SAML settings and the first admin account. If the logs go on a network share, mount it
first (see [Storage and backup](#storage-and-backup)).

| Where | What |
|---|---|
| `/opt/flight-logs/app` | Code, parser and Python venv (an update does `git pull`, rebuilds and restarts) |
| `/etc/flight-logs/logdb.env` | [Settings](#settings) |
| `/var/lib/flight-logs` | Database, session key and SAML keys (local disk) |
| `flight-logs.service` | The app: one gunicorn process on `127.0.0.1:8000`, run as user `logdb` |
| `flight-logs-backup.timer` | The database backup |

Day-to-day:

    systemctl status flight-logs                     # logs: journalctl -u flight-logs -f
    sudo systemctl restart flight-logs               # after editing /etc/flight-logs/logdb.env
    sudo systemctl start flight-logs-backup          # back up now
    sudo -u logdb /opt/flight-logs/app/webui/venv/bin/python /opt/flight-logs/app/webui/app.py check-config

<a id="slow-pages"></a>**Slow pages.** A request slower than `LOGDB_SLOW_REQUEST_MS` (2 s) is a warning in the service
log with its method and path (never the query string), time, status, upload size, user id and how many gunicorn threads
were busy at its start and end (all `THREADS` busy: requests queue, for example behind long uploads). A write that
waits more than half of it for the database write lock, or holds it longer, is logged with what held the lock
(a request, `parsing log <id>` or `storing upload <id>`). Page loads only read; in WAL mode reads never wait for writes,
so a page that hangs is waiting for a free thread or doing slow work, not for the database. Find them with
`sudo journalctl -u flight-logs --since today | grep -E "slow request|write lock"` (sudo: the journal needs it).
A streamed download is timed until its response starts.

After an update that changes the parser version, use **Manage ▾ → Re-parse outdated…** on the Logs page (admins).
Re-parsing keeps flight numbers (`flights.id`, used in `/flight/<number>`, `pprz_fetch` and the Python client): a flight
with the same aircraft ID and flight index whose takeoff moved at most 60 s keeps its number (`db.store_parsed`). Other
flights get a new number above every number of that log, so a dropped number never points at another flight.

An ordinary update automatically reloads the installer if Git changes it, even when running the
installed copy directly. One invocation updates the code, dependencies and service files. First
installs and `--reconfigure` runs keep the running installer and its interactive answers; it prints
a note if the downloaded installer differs.

Before an existing installation is updated (including `--reconfigure`), the installer runs the
**currently installed** online database-and-keys backup command, before questions, package changes
or Git updates. Backup failure stops the update. Installer self-reloads reuse the successful backup
instead of creating a second one. Fresh installations skip this step.

The shared page footer shows the nearest reachable `webui-vMAJOR.MINOR.PATCH` release tag, the number
of commits since it (when nonzero), and a link to the Web UI's exact Git commit. Web UI release tags
are separate from parser versions; how to make one is in [AGENTS.md](../AGENTS.md#commits-and-releases).
The **Parser** workflow (`.github/workflows/parser.yml`) only runs when `cpp/` changes: it builds the parser on
Linux x64, macOS arm64 and Windows x64 for pull requests and master, and on master publishes a GitHub release
`parser-v<PARSER_VERSION>` with the three binaries when that version has no release yet (MATLAB's `pprz_setup`
downloads them). Existing releases are never changed, so a new binary needs a new `PARSER_VERSION`. On Windows
`cpp/platform.h` replaces the POSIX file calls; `-DBUILD_BLACKBOX=OFF` builds only the parser.
Deployments must fetch tags to display release names; the installer does
this explicitly on updates. Version detection trusts only the application checkout through a
per-command Git `safe.directory` setting, allowing the `logdb` service to inspect root-owned code
without changing ownership or global Git configuration.
The revision, release description and tracked-change status are
captured once when the server process starts and returned with authenticated `/api/me` responses;
pulling code without restarting does not change that reported revision. Restart after updates.
Untracked files (including local data and environments) do not mark the revision modified.
Deployments without Git metadata or a working Git executable show **version unavailable** and link
to the repository instead; the server logs the detection problem. This identifies the Web UI checkout,
not the installed parser binary or a separately cached browser asset.

**OptiTrack detection (parser 1.8.2).** A flight counts as OptiTrack (and so indoor) when the aircraft logs
`EXTERNAL_POSE*`, reports GPS fixes with 0 satellites, or mentions "optitrack" in its airframe or flight-plan
configuration. XML comments and `<description>` text no longer count, so a note such as "can be used with
Optitrack" does not mark outdoor flights. Re-parse outdated logs to update existing flights.

**GPS quality of positions (Paparazzi parser 1.15.0, mav-log parser 1.1.0).** Takeoff/landing locations, the path,
distances and ranges from takeoff use only good fixes. One rule for every log type, in `webui/gps_quality.py`
(the C++ parser repeats the constants in `cpp/fastreader.cpp`, `GPS_MIN_*`/`GPS_MAX_*`):

| Constant | Value | Meaning |
|---|---|---|
| `MIN_FIX_TYPE` | 3 | 3D fix |
| `MIN_SATELLITES` | 6 | satellites used |
| `MAX_HDOP` | 2.5 | HDOP; PDOP where only that is logged (Paparazzi) |
| `MAX_EPH_M` | 5 m | horizontal accuracy (PX4 `eph`) or Paparazzi `pacc`, only when no DOP is logged |
| `ENDPOINT_WINDOW_S` | 30 s | how far from the takeoff/landing a fix may be |

A field a log does not carry is not checked, so older logs fall back to the fix type alone. Sources: ArduPilot
`GPS` (`Status`, `NSats`, `HDop`), MAVLink `GPS_RAW_INT` (`fix_type`, `satellites_visible`, `eph` = HDOP×100; a
`GLOBAL_POSITION_INT` is not used while the latest raw fix is bad), PX4 `vehicle_gps_position` (`fix_type`,
`satellites_used`, `hdop`, `eph`; the existing `eph`/`epv` limits stay) and `vehicle_global_position` (`eph`),
Paparazzi `GPS_INT` (`fix`, `numsv`, `pdop`/100, `pacc`) and `GPS` (mode, with `numSV`/`PDOP` from a `GPS_SOL` of
the last 5 s). A Paparazzi fix with 0 satellites is OptiTrack sent as GPS (`external`), a trusted position.
Positions derived from GPS (Paparazzi INS frame, `NAVIGATION`) take the quality of the latest GPS message.
Blackbox logs (satellite count only) keep their own rule (at least 5 satellites).

The takeoff location is the position at takeoff (a fix at most 5 s old), else the first good fix within 30 s after
takeoff, which is also the origin of the range from takeoff; the landing location is the last good fix in the 30 s
before landing. Each endpoint stores the quality in `takeoff.gps` / `landing.gps` (`takeoff_json`/`landing_json`; no
schema change): `fix_type`, `satellites`, `hdop` or `pdop`, `eph_m` or `pacc_m` when logged, `good`, `external`, and
`selected`: `first`/`last` (the fix at the endpoint), `later`/`earlier` (a good fix after a bad one) or `raw` (no good
fix in the window: then `location` is null and `gps` holds the raw sample's `lat`, `lon` and `alt_m`). The mav-log
flights also carry `gps_quality` (`fixes`, `good_fixes`, `good_fraction`). The logbook read model passes every
endpoint through `gps_quality.endpoint_position`: results without `gps` (older parses) keep their coordinates, a
stored fix that no longer passes the rule is dropped, and a `raw` sample is used when it passes, so the constants can
be tuned without re-parsing. A flight without any good endpoint fix is an indoor / no-GPS flight (see **Missing
telemetry locations**). Re-parse outdated logs to apply the rule to existing flights.

**External positioning (mav-log parser 1.1.0).** Like OptiTrack in Paparazzi logs, motion capture or external
navigation found in other logs sets `assessment.external_positioning` (the source) and makes the flight indoor:
ArduPilot `VISP`/`VISO`/`VISV` messages during the flight or `EK3_SRC1_POSXY`/`EK3_SRC1_VELXY` = 6 (ExternalNav)
("ArduPilot ExternalNav"); MAVLink `VISION_POSITION_ESTIMATE`, `VISION_SPEED_ESTIMATE` or `ATT_POS_MOCAP` during the
flight ("MAVLink external vision"); PX4 `vehicle_mocap_odometry` ("PX4 motion capture") or `vehicle_visual_odometry`
samples during the flight, or `estimator_status_flags` with `cs_ev_pos` and without `cs_gps` ("PX4 external
vision"). The read model reports it as `external_positioning` (Paparazzi OptiTrack: "OptiTrack") and `optitrack`
true, shows it as the place when no named place applies, and the pages label it "motion capture (source)".

**INS path (parser 1.13.0).** Each path point's `ins` also carries `lat`/`lon`: the INS NED position converted
through the latest `INS_REF` origin. The flight page offers **GPS path** or **OptiTrack / INS path** when these
exist, defaulting to the INS path for OptiTrack flights (indoors the GPS receiver wanders); the choice is kept in
the URL (`?path=gps|ins`). With the INS path, the takeoff and landing markers are its first and last point. The
maps zoom to level 23 (tiles above 19 are scaled up). Distances and the logbook places still come from GPS.

**Fixed-wing navigation fallback.** Parser 1.7.9 can fill gaps in direct GPS using recorded
`NAVIGATION.pos_x/pos_y` (metres east/north) and the latest received `NAVIGATION_REF`
UTM origin. GPS still takes precedence for two seconds. A reference must first agree
within 25 m with a valid GPS fix within 0.25 seconds of a navigation sample, per aircraft;
that validation expires after 120 seconds. Changed/invalid references, contradictory
near-simultaneous GPS, invalid local positions or implausible movement revoke trust.
Without GPS corroboration no navigation fallback is used. Repeated identical references
do not renew validation. Recovered path positions have `source: "navigation"` and
`gps_alt_m: null` because this message supplies no altitude; barometric/sensor data remain
separate. The Height chart does not splice barometric altitude into these GPS-height gaps
(the barometric datum may differ). Navigation-derived endpoints similarly have `alt_m: null`. Real gaps over five
seconds remain disconnected on the map. Reparse affected logs to recover stored sections.

**Legacy compact rotorcraft telemetry.** Parser 1.7.2 decodes `ROTORCRAFT_FP_MIN`
east/north/up (1/256 m) and ground speed (cm/s) using the aircraft's `INS_REF`
ECEF origin and tangent frame. Parser 1.7.3 also decodes `ROTORCRAFT_FP` positions
and horizontal velocity (1/524288 m/s), retaining its attitude fields, using the same
reference conversion. Paths, endpoint coordinates, distance and sensor graphs use
the existing JSON format; altitude is converted to the reference's mean-sea-level datum.
Parser 1.7.5 indexes reference changes and valid GPS fixes per aircraft before streaming
the report. A first reference can arrive up to 60 seconds after local samples; an updated
reference can also explain earlier samples within that window when GPS corroborates it.
An invalid reference is a barrier: lookahead does not cross an explicit invalidation.
The closest GPS fix within 60 seconds must agree within 500 m horizontally and 250 m
vertically (allowing for vertical datums and timing). The check is per sample, not just
whether the reference itself is geographically plausible. A GPS-corroborated reference
remains usable during longer GPS outages. Small reference refinements within 100 m of
the GPS-validated reference retain trust (including altitude refinements); cumulative
changes are compared against that validated anchor, not just the preceding report.
Contradictory evidence, material reference changes or invalidation remove that trust.
Without any GPS stream, compact/local-only recordings
still use valid references without requiring unavailable GPS corroboration.
Direct GPS takes precedence over local positions for two seconds after a valid fix.
Endpoint positions expire after five seconds without accepted updates; an exact takeoff
or landing location can therefore be null rather than an old or uninitialized coordinate.
Nearby future GPS is used only to check a frame, not to fabricate an earlier endpoint.
Rejected local frames also disable associated INS fallback until a compatible local sample
is received, avoiding initialization jumps in distance/assessment metrics. The parser
reports rejected local-position counts on stderr. Sensor data in a rejected INS frame is
not treated as trustworthy flight evidence. Reparse to remove previously stored bad paths
and metrics. The web UI's existing endpoint-inference rules still apply to null endpoints.
`ROTORCRAFT_STATUS.vsupply` with the protocol unit `decivolt` is converted to volts;
volt-valued and unitless protocols retain their previous scaling.
Deploying the rebuilt parser and reparsing affected uploads is sufficient: no web UI or
database migration is required. Previously stored paths/voltages are not changed until reparse.

**Flight-plan sectors.** Paparazzi parser 1.7.6 includes `flight_plan.sectors` with each
sector's name, optional color and ordered corner waypoint names. The flight page resolves
those names against the parsed geographic waypoints and draws closed polygons, using the
declared color when valid or the default purple otherwise. **Show sectors** toggles polygons
independently of **Show flight plan** waypoint markers. Sectors without at least three
located corners are not drawn; the control tooltip lists those sectors. Logs without sectors
disable the control. Reparse outdated Paparazzi logs to populate sectors in existing results.
Waypoint dots and labels whose names start with `_` are hidden by default and can be revealed
with **Show hidden waypoints** while **Show flight plan** is enabled. Hidden waypoints remain
available as sector corners; their visibility does not change sector geometry. This display-only
rule also applies to existing parsed waypoints without reparsing.
Parser 1.7.7 also accepts legacy uppercase and mixed-case flight-plan attributes, including
reference coordinates, waypoint positions/names, blocks and sector corner references.
Rebuild the parser and reparse affected older logs to restore their mapped flight plans.
Parser 1.7.8 also accepts uppercase and mixed-case `time_of_day` header attributes,
preserving the companion recording timestamp even without GPS timing. Reparse affected logs
after deploying the rebuilt parser; no database migration is required.

**Legacy fixed-wing flight detection.** Parser 1.7.4 uses `BAT.flight_time` when
the protocol has no usable `NAVIGATION.flight_time` field. A positive timer marks
flight; zero marks ground. Navigation block/stage updates still work without that field.
`BAT.voltage` declared as `1e-1V` or `decivolt` is converted to volts. Newer navigation
timers retain precedence over the legacy timer.
If recording starts with a positive timer, the parser reports the observed flight interval
only; it does not invent a takeoff before the recording. A flight still active at the end
is left incomplete. Deploy the rebuilt parser and reparse affected uploads; the existing
web UI consumes the unchanged JSON format.

**PX4 navigation and flight boundaries.** MAV reader 1.0.3 uses `vehicle_land_detected`
airborne intervals intersected with arming when both are available. An armed ground recording
does not become a flight; disarming while still reported airborne does not prove a landing.
Logs without land-detection telemetry retain the armed-interval fallback.
Geographic positions prefer quality-accepted raw `vehicle_gps_position` when it covers the
recording: the first/last accepted fixes must be within five seconds of the logger start/end,
with no gaps over five seconds between accepted fixes. Otherwise, `vehicle_global_position`
is used when that topic has samples; logs without it retain the quality-filtered raw GPS
fallback. One source is selected for the recording, never alternating between streams.
This preserves continuous outdoor GPS when the estimator is late, resetting or intermittently
dead-reckoning, without using partially initialized GPS to replace a usable estimator path.
Global dead-reckoning samples are rejected. Positions must have valid nonzero latitude/longitude;
reported horizontal/vertical error must be finite and at most 10/15 m respectively.
Raw GPS additionally requires a 3D fix and at least six satellites when those fields are present.
Missing quality fields in older formats do not by themselves invalidate a position.
Position updates expire after five seconds. Endpoints use only preceding fresh positions,
never a later fix; unknown endpoints remain null in parser output. The UI's existing
endpoint-inference labels still apply. Distance does not bridge position gaps or estimator
reset-counter changes, and is only the accepted recorded path, not a guaranteed complete flight
distance. Local position/velocity validity flags are respected; invalid axes remain null.
Altitude-range assessment uses one datum (accepted global altitude, or barometric altitude
when no global altitude is available). Missing GPS does not by itself prove an indoor flight.
Deploy and reparse outdated uploads to update existing PX4 results. Paparazzi parsing is unchanged.

**Betaflight and INDIFlight Blackbox.** Parser `blackbox` 1.0.1 (separate from `mav-log`)
runs `.bfl`/`.bbl` files through `blackbox_decode` (INDIFlight's field-aware build of
Nicholas Sherlock's tools, built next to the Paparazzi parser) and then the INDIFlight
log-tools SI conversion (metres, motor 0–1, unit quaternions). CSV is streamed; only
the logbook columns are kept, and path samples are taken at `path_rate`. Files that
contain several logs (one per arming) are decoded with `--index` as separate flights,
each with its own header block (start time, `motorOutput`, firmware). The decoder is
killed if it produces no output for 300 s.
Stock Betaflight and INDIFlight share the same container; extra INDIFlight columns
(EKF pose, pitot, RLS) are used when present. GPS coordinates come from merged G
frames (degrees, at least five satellites); unsigned GPS altitude is read as signed
32-bit so small negatives are not 4e9 m. Baro altitude is centimetres. An INDIFlight
`ekf_pos` jump of about 65 m between consecutive samples (16-bit millimetre wrap) is
discarded in favour of `pos`. Arming prefers `stateFlags` bit 0 when that bit is used,
otherwise `flightModeFlags` bit 0 (the arm box). Motors use the `motorOutput` header
range. Voltage is treated as centivolts when the raw value is above 60. An empty craft
name falls back to the filename. Log type is `indiflight` or `betaflight` from the
firmware revision header. Rebuild `blackbox_decode` with the C++ parser; Blackbox
uploads need a reparse, existing PX4/ArduPilot/tlog results do not.

The flight map uses only finite, in-range geographic coordinates, not barometer-only position
objects. Missing coordinates and sample gaps over five seconds split the displayed line; isolated
positions remain visible as dots. First/last recorded-position markers are distinct from takeoff/
landing markers, which require coordinates in the recorded endpoint itself. The scrubber marker
is hidden when its selected sample has no geographic position. Sensor-only paths still support charts.

**Non-finite Paparazzi values.** Parser 1.7.1 serializes non-finite numeric output as JSON `null`,
not invalid bare NaN/Infinity tokens. Non-finite numeric telemetry is rejected before calculations.
`DL_VALUE` is retained separately: `settings_changes.invalid_reports` records timestamp, setting
index/name, `value: null` and `invalid_value` (`NaN`, `+Infinity` or `-Infinity`).
Invalid reports do not become ordinary setting changes or fabricated zero values. Comparisons
resume after two consecutive finite reports; defaults/finals can be null. Flight settings lists
show these reports as **Invalid value (...)**, using the existing flight/all-recording filter.
Original uploads remain unchanged; reparse failed logs to recover affected recordings.

**Paparazzi simulation metadata.** Parser 1.7.0 adds a `simulation` object to each aircraft in its
JSON output, for example `{"detected": true, "evidence": ["telemetry:NPS_WIND"]}`.
It detects recorded `NPS_RATE_ATTITUDE`, `NPS_POS_LLH`, `NPS_SPEED_POS`, `NPS_GYRO_BIAS`,
`NPS_SENSORS_SCALED` or `NPS_WIND` messages for that aircraft, or an embedded airframe whose
targets are exclusively `nps` on board `pc` (`configuration:nps-only-pc`). Both evidence sources
are retained when present. Names, paths, protocol message definitions and an NPS target alongside
hardware targets do not trigger detection. `detected: false` with empty evidence means no supported
simulation evidence was found, not proof of a physical flight.
The web database persists this per-aircraft metadata. Flight details show the detection evidence;
upload details and flight lists label detected simulations. Logbook, Statistics and person/aircraft/
project/location details exclude them from displayed flights, totals and charts when **Hide
non-flights** is ticked (off by default); non-flights are simulations, uncertain flights and ground records.
The shared filter's defaults show indoor flights, non-flights and unknown dates; a saved browser selection keeps its
values. The APIs keep their own defaults. Locations overview counts follow the same API default (`GET /api/places?nonflights=1` includes
all three categories); its map hints retain non-simulation ground positions by default.
Other overview APIs retain the metadata and
all accessible rows so their browser filters can switch without refetching.
Simulation and non-simulation sources cannot merge, including day/session grouping. Existing fleet
links and direct flight/upload access are preserved. Legacy results and parsers without this metadata
remain unassessed (`simulation: null`), not automatically excluded; reparse outdated Paparazzi uploads
with parser 1.7.0 to detect existing simulations. Flight details explicitly identify unassessed records.

**Paparazzi distances.** From parser 1.6.0, distance flown, distance from takeoff and maximum
distance from takeoff are horizontal (ground-plane) metres, including cumulative distances in saved
paths and aircraft/log totals. GPS uses latitude/longitude; the GPS-free INS fallback uses north/east
displacement only. Vertical movement adds no distance. Altitudes remain available for charts and
flight assessment. This prevents differing GPS altitude reference levels from inflating distances;
horizontal GPS noise/jumps can still affect them. Reparse existing uploads to replace old 3D distances.

**Text messages are stored compressed.** An aircraft's `INFO_MSG` texts (shown on the flight page) can be several
hundred kB per log, so they are stored gzip-compressed in `aircraft.info_messages_gz`; readers use
`db.info_messages(row)`. After the update to 4.11.2 a background thread converts older rows (plain JSON in
`aircraft.info_messages`) in small batches and logs how many it converted. The file only shrinks after a one-time
`VACUUM`, run with the service stopped (it needs free space about the size of the database and takes about a minute):

    sudo systemctl stop flight-logs
    sudo -u logdb /opt/flight-logs/app/webui/venv/bin/python -c \
        "import sqlite3; sqlite3.connect('/var/lib/flight-logs/logs.db', isolation_level=None).execute('VACUUM')"
    sudo systemctl start flight-logs

To go back to an older Web UI, stop the service and undo the conversion first with
`db.decompress_info_messages(<path to logs.db>)` (run from the `webui` directory with the venv's Python).

**Storage on Statistics.** Admins see a Storage card (`GET /api/admin/storage`, `storage_usage.py`): what the
stored logs, flight paths, attachments (with PDF previews) and qualification evidence take on disk, the original
size of the uploaded `.data` files, the database file and the free space on the log storage and the database disk.
Walking the log storage can be slow on a network share, so a background thread measures it at most every six
hours (`?refresh=1`, **Measure now**, starts a new one); the database figures and free space are read live.

**Logbook read model cache.** The logbook, statistics, review, places, overview and flight pages share one
read model (`logbook_rows`). Building it takes about a second per few thousand flights, so it is kept in memory
per user and request options and rebuilt only after a committed write it may show, or at the start of a new UTC
day. Triggers raise a counter (`logbook_version`, recreated at every start) on writes to every table except those
in `db.LOGBOOK_UNRELATED` (API keys, sign-in and upload bookkeeping, caches, training, bookings, documents, profiles) and
`users.last_login`, so those no longer empty the cache; a table missing from that list only costs a refresh. The finished logbook response is kept the same way.
At most eight of each are kept; each read model takes roughly 25 MB per 5000 flights. These responses carry a
`Server-Timing` header (`built`, `cached` or `response cached`, with the time) shown in the browser's Network tab.
Flight detail and path responses are gzip-compressed for clients that accept it, and so is every other JSON, HTML,
CSS or JavaScript answer over 16 KB (for example `/api/stats` and `/api/admin/people`; files sent from disk and streams
are left alone). Pages are `no-store`; `theme.js`, `theme.css` and the other page scripts are `no-cache` (kept by the
browser and revalidated: 304 while unchanged, so a release still shows at once). `theme.js` starts `GET /api/me` and each
page's first data requests at once (a list per page in `theme.js`: Aircraft, Documents, Review, Training, Safety, Logs,
Projects, People and the flight page; admin or not as last seen in the browser) and hands those answers to the page's
own requests, so a page no longer waits for `/api/me` before asking for its data. Home and the Logbook start
`/api/home` and `/api/logbook` themselves; the overview pages, Locations and Statistics never waited. The Agenda's
first request depends on the week shown and is not started early.

**Missing telemetry locations.** The shared logbook read model copies the known endpoint's GPS
latitude/longitude for display and named-place resolution when the other endpoint is missing.
The flight Overview labels the inferred location; no event, time, altitude or raw coordinates are
written or fabricated. Historical weather still requires a logged takeoff position, not a landing
position copied for display. Upload details use the same coordinate fallback. When neither endpoint has
valid coordinates after checking endpoints from every merged source, the entry is
classified as indoors for display/filtering (labelled as inferred on the flight page). Valid positions
exclude zero/zero, non-finite and out-of-range coordinates. This is a no-GPS heuristic, not proof of
indoor operation; a partial GPS path does not override this endpoint-based classification and
remains available on the map. A flight is also indoor when its takeoff **or** landing position lies in a named
indoor place, or with OptiTrack / external positioning. Paper records are
unchanged. These fallbacks apply to existing records without reparsing.

**Paparazzi clock validation.** Parser 1.5.0 checks `GPS` / `GPS_LLA` messages containing a full GPS
week, time-of-week and a valid 3D fix. It converts GPS time to UTC with historical leap-second offsets;
time-of-week alone cannot establish a date. At least three samples spanning 30 seconds must imply a
log start within a five-second range. Aircraft contributing usable clock evidence must also agree.
Invalid fixes and implausible dates (before 2000 or more than a day in the future) are excluded.
With consistent evidence, an offset within 60 seconds of a nonzero whole number of days is corrected
using the GPS-derived start, including its small seconds offset. Other offsets greater than five seconds
only produce a warning; smaller differences leave companion timing unchanged.
Missing companion timing can be recovered from consistent GPS evidence. Insufficient/inconsistent
evidence or conflicting aircraft clocks retain the companion clock with a warning.

Logs details and the flight Overview's **Log sources and upload details** show the original
companion start, effective start, parser correction in seconds, source and any warning for the selected
recording. Manual calendar-date overrides remain authoritative and survive reparsing; the parser's
original clock evidence remains visible. Old results have no clock evidence until reparsed. All absolute
timestamps in newly parsed results, including paths and messages, use the chosen clock; relative times
and flight durations are unchanged. Normal source merging then uses the corrected absolute times;
there is no track-similarity matching across dates.

**Firmware evidence and estimated weeks (parser 1.8.0).** The parser adds date status/reason codes,
per-aircraft GPS sample and fix counts, observed week values, accepted spans/offset spreads, and
structured firmware revision/dirty flags alongside the unchanged raw version messages. `valid`
means the retained calendar date is plausible, not independently verified. Invalid/missing GPS
fields are counted rather than silently discarded. Week-zero evidence retains the GPS time-of-week
relative to recording time zero, including continuity across Sunday rollover.

**Firmware build identity (parser 1.9.0).** Newer pprzlink appends `sha1` (full git hash), `build_time`
(UTC seconds) and `mcu_uid` (board unique ID) to `AUTOPILOT_VERSION`. The parser reads them by name from
the log's own protocol and adds `sha1`, `build_time_utc` and `mcu_uid` (hex) to each firmware entry; a
full `sha1` replaces the short `-g<hash>` from `desc` as the revision, so GitHub resolves it exactly.
Zero/empty values are `null`. Logs without these fields parse as before; `raw` stays `version desc`. The flight page
(Software) and the Logs log details show the full commit, build time and board ID; MATLAB exports add
them to `s.aircraft(i).version`. An empty array prints as no token, so the column exporter accepts missing
trailing array fields as empty (a board without a unique ID). PX4 and ArduPilot logs are unaffected.

**Matching by autopilot board.** Each recording stores its board ID (`aircraft.mcu_uid`). When a recording is
linked to a fleet aircraft by a clear name or by hand, an unknown board ID is remembered for that aircraft
(`drone_boards`); a recording whose name is ambiguous or unknown is then linked by its board, and earlier
unplaced recordings of that board follow. A clear name or a hand assignment always wins over the board; a
board that also appears under another aircraft is marked on the Aircraft page (Aliases tab). Admins can stop
matching by a board (a board moved to another airframe, a shared test board: ⋯ → *Stop matching a board…* of the
open aircraft) and allow it again under **Shared names**. Simulated recordings never match by board (their ID is the PC's machine ID). The
migration fills board IDs of logs already parsed with parser 1.9.0.
Admins can also link a board directly (`PUT /api/admin/drone-boards` with `mcu_uid` and `drone_id`, stored as
`manual`): every recording on that board follows it and hand assignments of single recordings on the board are
replaced; a clear name still wins. **Link names + board** in Unlinked aircraft names stores the names as aliases
and their board IDs in one go, and the ambiguous-name list shows one row per board (with how its recordings are
linked now) and one row per recording without a board ID.

During Web UI ingestion, GitHub resolves firmware hashes and stores full hashes, repository provenance,
committer dates, author dates and explicit resolution failures in the log's timing JSON.
`LOGDB_FIRMWARE_GITHUB_ENABLED=1` enables this by default: revision hashes from uploaded logs are sent
to GitHub, **never telemetry, flight locations or log contents**. Set it to `0` to disable network lookups
and automatic week estimates. No local Git mirrors or extra installer dependencies are needed.

Resolution order is persistent SQLite cache, direct lookup in `paparazzi/paparazzi`, global public
commit search by hash, then direct lookups in `LOGDB_FIRMWARE_GITHUB_REPOS` (comma-separated
`owner/repository` names). Global search only accepts commits from the Paparazzi fork network and
rejects ambiguous abbreviated hashes and incomplete search results. Search covers default branches;
configured repositories can be **public or private**, and direct hash lookup covers their non-default
branches too. The server stops at the first resolved commit; no enumeration of all forks is needed.

For private repositories, configure `LOGDB_FIRMWARE_GITHUB_TOKEN` in the protected server config
file (never in a URL or browser). A fine-grained token needs access to the selected repositories with
**Contents: read**; organization policy may require approval. Public lookups work without a token but
have lower rate limits. Tokens are hidden by `check-config`, excluded from API responses/logs/cache,
and sent only to `api.github.com`; redirects are not followed. Restart the app after config changes.
Successful resolutions persist indefinitely; misses expire after an hour, lookup errors after five
minutes (or GitHub's longer rate-limit delay). Cache scopes include repository/token configuration
fingerprints, never plaintext tokens. Rate-limit/network failures temporarily pause further requests
across hashes. Parsing still completes with explicit warnings, retaining unknown dates when no
estimate can be supported. After fixing configuration or waiting for cache expiry, use the individual
log's **Re-parse** action to retry, or as an administrator use **Re-parse unknown dates**
on the Logs page. This queues completed logs with missing or implausible dates even when
their parser version is current. It skips manual date overrides, plausible dates (including
estimated dates), failed logs and logs already queued or parsing. It uses the same project
scope as **Re-parse outdated**, not the displayed page or date filters. Repository/token changes
do not mark logs outdated. Cached misses/errors still respect their retry delays when configuration
has not changed. No bulk reparse is started automatically.

**sd2log without a GPS date (parser 1.14.0).** When sd2log finds no GPS fix in an SD log (e.g. indoor), it writes
the time of the *conversion* as `time_of_day` in the `.log` header and names the log `…_no_GPS…`. That time says
nothing about the flight, so the parser does not use it: such a log (by the header's `data_file` or the file name)
gets no start time (`timing.source` `unknown`, reason `header_conversion_time`, a warning) and counts as date
unknown, so the date suggestions below or a date set by hand apply; it stays an onboard log. The date in a
`_no_GPS` file name is not used as a suggestion either (a folder name still is). Logs parsed before are re-parsed
once at startup when parser 1.14.0 or newer is installed (`requeue_no_gps_logs`, recorded in `data_migrations`),
so not every log the version change marks outdated.

**Dates from a matching ground station log.** An onboard log whose clock was never set (date before 1990,
no date entered) usually keeps the right time of day. The same check runs for logs whose date is only
estimated from firmware: estimated dates never merge with other recordings, so a matching GCS log confirms
the day and accepting it ends the estimate (the recordings then merge and count once). A ground station logs the PC
clock, so a log with an estimated date counts as the onboard side even when it was detected as GCS (an SD log
renamed `logNN_<aircraft>.data` next to a ground station `.log` header). `date_suggest.py` looks for a dated GCS
recording of the same flight: same aircraft (fleet link, else logged name), same duration (±1 s or 1 %), same
time of day (±30 s), at least 60 s long (short flights match too many others), and no dated onboard partner yet.
Every matched flight of the log must have exactly one such GCS flight, no other undated flight may claim it,
and all must agree on the day. Without a GCS match, the date comes from the upload path (`logs.name`): the first
plausible date in the file name, then in the folders from the deepest up (`2026-04-29`, `20260429`, or
`26_04_29` as in Paparazzi log names, of which only the first group counts because the time follows it, and
`2026-4-29` as in ground station download names; a file named only `HH_MM_SS`, as PX4 stores them under a date
folder, is a time; not before 1990 or in the future). Failing that, the dated logs of the same
aircraft just before and after it in the same upload folder (natural file-name order, undated logs skipped) give
the day when they agree; files uploaded without a folder have no neighbours. Each suggestion has a `source`
(`gcs`, `name`, `folder`) and a `reason`, shown in the log details ("Date probably 2026-04-29: date in the folder
name 2026-04-29") with **Use this date**, and **Use for all in this folder (N)** when more logs in that upload folder
that you may date have one (`POST /api/logs/<id>/date/suggestion` with `{"folder": true}`). Admins get **Review date
suggestions (N)** on the Logs page: a dialog listing them by folder with checkboxes (`GET /api/admin/date-suggestions`;
`POST` accepts all, or `{"log_ids": [...]}`). Accepting sets the normal date override and records who set it and
why (`date_override_by`, `date_override_note` = "suggested: " + reason); a date typed by hand records the person
only. One exception: a log whose clock gives no date at all takes the date in its file or folder name by itself
when it is parsed (`date_from_name`, the same rule as the `name` suggestion above; `date_override_by` empty,
`date_override_note` "automatically from the date in the folder name …", shown on Logs as *Set automatically
from …*), unless it already has a date override. A GCS match, dated neighbours and estimated dates stay
suggestions: nothing else is changed without a person accepting it. An unaccepted suggestion simply stays, and is
recomputed, so it disappears once the log has a date.

Until a date is set, a flight taking off on an unset clock (before 1990) is never merged with other
recordings by time: those times are meaningless across logs, and back-to-back 1980 logs used to chain. A person
can still join them by hand (*Same flight*).

A calendar date more than a day before the latest resolved firmware commit is flagged, never
replaced if otherwise plausible. For an unknown/implausible date only, consistent week-zero samples
(at least three spanning 30 seconds, within five seconds, agreeing across aircraft) plus resolved
firmware evidence for each contributing aircraft can produce an **estimated** date: the first
GPS-compatible recording start on or after the latest firmware committer date, within the following
seven days. Historical GPS/UTC leap offsets are applied. Estimates beyond tomorrow are rejected.
Commit timestamps can be wrong, firmware can be old, and dirty builds have unrecorded changes:
the week remains uncertain and later seven-day alternatives remain possible.

The estimate shifts all absolute recording/aircraft/event/message/path timestamps, not durations
or relative times. Original timing, evidence and estimate basis remain stored. The Web UI labels
estimated dates in Logs, Logbook, entity histories and flight details; they follow normal period
filters and are not hidden by **Show unknown dates** being off. The API exposes `date_estimated`
separately from `date_ok`. Estimated clocks are not used for automatic source merging or automatic
paper associations until manually confirmed; explicit joins remain available. Manual date overrides
remain authoritative across reparses and clear the displayed estimate flag while preserving evidence.
The standalone C++ parser emits evidence only; GitHub enrichment and estimated-week assignment happen
in Web UI ingestion. Reparse outdated logs after updating and configuring any additional repositories.

**Flight pages.** The Overview opens first: aircraft, goal and flight time, then When and where, Crew
and Operation groups, the comment and a small non-interactive path map (it opens Path & charts). Takeoff
and landing times follow one Date row; battery and logged wind are takeoff → landing rows, coordinates are on
hover over the place name, and INS values are under Log sources; log sources, upload details
and raw-data links are folded away. Changes are made with **Edit flight** ([below](#flight-page-changes));
the Files tab is the file manager described under attachments (compact list, search, category filter, **Upload files**, a click on a file's row to edit it). Autopilot software is shown in Overview with the decoded version, Git commit and base tag links,
and warnings for local (dirty) changes. Multiple reported builds remain visible; raw version messages
can be expanded. Commit links use `paparazzi/paparazzi` and may not resolve for private forks.
When the aircraft did not send `AUTOPILOT_VERSION`, the GCS `runtime paparazzi_version` comment at
the top of the companion `.log` is shown instead and labelled as coming from the GCS log.
Merged SD/GCS entries retain the fleet name, aircraft link and TUD/serial number from a linked
source even if the main telemetry source is unlinked. Sources linked to different fleet aircraft
are kept separate rather than choosing an identity.
Tabs separate the path/charts, weather, settings, messages, the checklist (with a count) and files.
Messages and settings show 25 rows per page, scoped to the flight by default; their checkboxes include
the whole log. Tab counts reflect the flight scope (messages also include 30 seconds either side).
Tabs support keyboard arrows and can be linked directly, for example `/flight/42#messages`.

**Paparazzi pre-flight checklist (parser 1.8.1).** All recorded `[PFC]` events, including
PIC, PAC, GCS operator and goal, appear in the Checklist tab on the flight page and expanded
Logs entry. Crew and goal extraction remains unchanged. Each aircraft's embedded airframe
`<checklist><item name="key">Full text</item></checklist>` supplies descriptions for matching
`key: answer` messages. The parser retains the original `text`, `time_s` and `utc`, adding
`checklist_label` and `checklist_value` when a definition matches. XML entities and CDATA are
decoded; comments are ignored and multiline descriptions have normalized whitespace.
The Web UI shows the description and recorded answer, with the original message in a
focusable tooltip. Unknown keys and logs without definitions retain their original text.
Repeated events remain separate; unlogged definitions are not presented as completed checks.
Since parser 1.10.0 an item's `help` attribute is added as `checklist_help` and shown in the item's tooltip
(a dotted underline marks items with help), and `optional="true"` as `checklist_optional` (an *optional* label).
Checkbox answers logged as `true`/`false` show as ✓/✗. An item left empty (`key:`) shows as –
when optional, as a warning `!` when the airframe requires it and as – without a definition; older logs that
confirmed an item without a value keep ✓.
No database migration is needed. Existing logs need **Re-parse outdated** to recover crew/goal
events omitted by older parsers and attach full descriptions; older stored messages remain displayable.

**Pre-flight inspection (#315).** Every logbook entry has `preflight` (`app.py` `apply_preflight`), also in
`/api/logbook` and Export CSV:
- `log`: a Paparazzi log of the entry has `[PFC]` messages (`aircraft.preflight_checks`); one checklist covers every
  flight of its aircraft in that log. MAVLink pre-arm and arming messages (also stored there for other formats) are
  the autopilot's own checks and do not count.
- `confirmed`: confirmed by hand, with `preflight_by` (name) and `preflight_at`. `PUT /api/flights/<id>/preflight`
  `{"completed": true|false}` has the rights of the flight goal (`logs:edit` for keys); the bulk
  `PUT /api/logbook/records` takes `"preflight": true|false`. Stored in `flights.preflight_by`/`preflight_at` and kept
  across a re-parse; a second confirmation keeps the first one's name and time. `preflight_at` without
  `preflight_by` is done by the PIC: the one-time data fix `preflight_done_before_recording` (`db.init`,
  `data_migrations`) set it on every flight from before #315, so `preflight_by` is then the PIC's name.
- `paper`: `flight_records.preflight` is 1 on the entry's own or linked paper record. `POST /api/historical` refuses
  a new record without `"preflight": true` unless its location is indoor or its category is `indoor`; changing a
  record does not require it. The one-time fix `preflight_paper_done_before_recording` ticked every record from
  before #315. CSV column `preflight`
  (`yes`/`no`/blank), kept when the column is missing. The tick is not part of the record's fingerprint.
- `preflight_special`: the last-minute risk assessment (LMRA, the special conditions) noted with it: the last
  non-empty answer to the checklist item `special` (`log`), `flights.preflight_special` (`"special"` with the
  confirmation, at most 1000 characters; cleared when it is taken back) or `flight_records.preflight_special` (form,
  CSV column `lmra`). Export CSV calls it `lmra`; the flight page shows *Nothing special* for an empty one with a
  recorded inspection.
- `not_needed`: nothing recorded, and the category is `indoor` (an indoor place, motion capture, or no GPS) or the
  flight is a simulation.
- `null`: not recorded.

**Additional files.** Use the **Files** tab on a flight, **Additional files** on a paper record,
or **Additional files** in an expanded upload. Photos, videos, wind-tunnel measurements, offboard
computer logs and other files can be uploaded there. Search by aircraft, date, filename or numeric
ID to select up to 100 flights/logs (including paper records); one uploaded file is stored once
and linked to every selected entry. Files linked to a whole log also appear on its flights, provided
the user can see that log.
Admins and project members with **Upload** (add), Delete (full) or Manager on each selected target can upload and
link files (`can_add` per target), like adding logs; for a log without a project, its uploader. Anyone who can
read **any** linked target can download the file: linking across projects deliberately shares its
contents. Inaccessible link names/IDs are not disclosed. Sharing an existing file further requires
upload access to all its existing links as well as the new targets. Removing a link needs **Delete** (full) or
Manager on that target (`can_edit`); it does not affect other links; the stored file is removed only after its last link
is removed. Deleting a flight's parent log or a paper record removes the corresponding links.
Re-parsing retains links to flights with the same aircraft ID, flight index and takeoff time; if that
flight no longer exists, its file links move to the parent log instead of losing the file.
**API key scopes** (`scopes.py`, issue 235). A key has scopes (`api_keys.scopes`, space-separated) that narrow what
it may do on top of its user's rights: `read` (every GET and HEAD, plus the MATLAB export requests
`POST /api/logs/<id>/matlab` and `POST /api/flights/<id>/matlab`, which only read), `logs:upload` (log upload and
reservations, `logs/check`, attachments: upload and share, paper records: single and CSV), `logs:edit` (project, date,
crew, comment and pre-flight inspection of logs and flights, date suggestions, conflicts, reparse), and for administrators `documents:upload`
(add a document, replace its file), `documents:manage` (edit and delete documents, groups and the intro) and `admin`
(places, operators, authorisations, projects, aircraft, aliases, date suggestions, deleting accounts, reparse: each
route listed by name in `scopes.ENDPOINTS["admin"]`, so a new admin route needs a decision). `require_login`
checks every key request against `scopes.allows`: a request that changes something is allowed only when its endpoint
is mapped to one of the key's scopes; an unmapped endpoint is refused to every scoped key (403, fail closed), so a new
route is never reachable by key by accident (`tests/test_api_key_scopes.py` lists the deliberately unmapped ones, e.g.
deleting a log, bookings, profiles). `POST /api/keys/revoke-all` is allowed for any key with a scope beyond `read`.
Routes that need a browser session keep refusing every key, old full keys included. Among them are the account routes
that could give a key holder a sign-in or more rights: adding a user (`POST /api/admin/users`), a setup link
(`POST /api/admin/users/<id>/setup-link`), changing an account's email (`PUT /api/admin/users/<id>`), removing a
password (`DELETE /api/admin/users/<id>/password`), admin rights, names, merges and keys of other people.

`POST /api/keys` takes `scopes` (a list; the admin-only ones are refused for other people); without it the key gets
only `read` (`read_only` is still accepted and makes no difference). `PUT /api/keys/<id>` (browser session) and, for
administrators, `PUT /api/admin/users/<id>/keys/<key_id>` narrow a key to a subset of its scopes; a key never gets a
scope back. Keys from before scopes have `scopes` NULL (added by an idempotent migration) and behave as before:
`read_only=1` as `read`, `read_only=0` as all of its user's rights, except managing documents (browser session or a
documents scope). The lists return `scopes` (null for such a key, shown as *full (old)*), and they can be narrowed to
a subset of `scopes.old_full` (every scope the user may use except the documents ones), so narrowing never widens them.
Keys from a browser login (Python package, MATLAB) get `read logs:upload logs:edit`; the migration also gives these
scopes to such keys from before (the only keys with `expires_at`).
API keys can upload files (`POST /api/attachments`) and link an existing file to more targets
(`POST /api/attachments/<id>/links`) with the same permission checks; the upload returns the file `id`.
Removing links (and so deleting files) requires a browser session.
An upload whose name (ignoring case) and content (SHA-256 and size) match a file the user can already see is not
stored again: the existing file is linked and the response is HTTP 200 with `duplicate: true` (a new file is 201).
A file whose name matches a current file on a target but whose content differs supersedes it there:
the old link stays with `superseded_by`/`superseded_at` set, the list returns it with `superseded`, and the pages
fold it under "Show earlier versions". Removing the newer file's link on that target makes the earlier version
current again; other targets are unaffected. At startup, attachments stored more than once with the same name and
content (for example an upload retried after a restart) are merged into the oldest copy and the extra stored files
removed.

**Categories.** Each file has one optional category (`attachments.category`, NULL = uncategorised), the same on every
flight/log it is linked to. The list is `LOGDB_ATTACHMENT_CATEGORIES` (comma-separated, in display order; default
*Preparation & permits, Notes, Media, Data, Analysis, Archive*). Uploads take `category` in `X-Attachment-Metadata`;
it must be a listed name (any case is stored as listed), otherwise 400. A duplicate upload keeps an existing category
and fills in a missing one; a new version without a category takes the one of the version it replaces.
`GET /api/attachments` returns `category` per file, `can_edit` and the `categories` list; for a flight also
`parent`, its log as a target (null if not visible); `day`, the flights of its fleet aircraft on the same date; and
`day_all`, all flights on that date, whatever the aircraft. Days use the logbook date (takeoff UTC date after the log's
date correction), leave out hidden flights, list at most 100 flights the user may add files to, and are null when no
other flight qualifies (`day` also without a fleet aircraft). The upload dialog's **Upload to** offers *This flight only*,
*All flights of this log* (the default on a flight), *All flights of <aircraft> on <date>* and *All flights on <date>*;
flights / logs added with *Find more* are uploaded to as well.
`PATCH /api/attachments/<id>` (browser session) with `category` (null clears it) and/or `description` changes the
file for all its links; Delete or Manager on one linked flight/log is enough, or Upload on one for a file the user
uploaded. A category removed from the setting stays on
its files and is shown after the listed ones, but cannot be chosen again. The pages group files by category
(uncategorised last, empty groups hidden). The file manager (`AttachmentFiles.manage` in `attachments.js`) is the same on
a flight's Files tab, a paper record and a log (the Logs page opens it in a dialog): the compact list with search and a
category filter (on pages in the URL as `files_q` / `files_category`), **Upload files** (only with something to upload
to); a click on the row of a file the user may change opens its Edit dialog (no per-row buttons, [UI rule](#ui-rule); the name stays a link to the file); uploading and linking to more flights/logs happen in dialogs. There is
no separate attachments page.

**Matching files to flights.** The general **Upload** (header, or files dropped on a page) sends logs as before;
other files (not hidden ones) open a review (`AttachmentFiles.review`). The browser reads when each file was made:
the photo's EXIF capture time (with its offset, when present; in a JPEG's APP1 segment, or for HEIC/HEIF, PNG, WebP and
RAW the `Exif\0\0` block in the first or last megabyte); for a video (MP4/MOV, reading the first 4 MB and last 16 MB)
the duration from `mvhd` and the start from the metadata creation date with its time zone
(`com.apple.quicktime.creationdate` or `©day`), else from `mvhd` (shifted by the time zone when the file's modified
time shows the camera wrote local time); else a date and time in the name, else the file's modified time; times without a zone use the browser's. `POST /api/attachments/match`
(`{"times": [ISO time | {"start", "end"}]}`, at most 1000; a read-only POST, allowed with the read scope) returns per
item `flight` (the flight it overlaps most, else the nearest within 1 hour before takeoff or after landing),
`flights` (every flight a video overlaps, when several), `how`, and `day` (all flights of that UTC day), only flights the
user may add files to, using the logbook date (after a log's date correction). The review proposes, in that order,
the overlapped flights, the flight, all flights that day, or nothing; the user can change each, or drop a file with ×,
before uploading. In the Upload dialog the chosen files are listed with × as well. On phones both dialogs fill the
screen with the heading and buttons kept in view. When
logs are in the same upload, matching waits until they are parsed (at most 15 minutes).

The Upload dialog starts with a drop zone (drop files or choose them); files dropped
on a file list open the Upload dialog with them. The logbook shows an opened entry's files read-only (`AttachmentFiles.summary`): **Media**,
up to four images and videos as small thumbnails (`/api/attachments/<id>/thumb`; a plain tile without one) and "+N", and
**Files**, up to six other files as chips with their category, "+N more" and *Files tab →* linking to the flight's Files
tab; for an entry merged from several recordings, the files of all of them. They load when the row opens.
`GET /api/logbook/files` gives the logbook the names and categories of the current files (no earlier versions) of the
flights (linked to the flight or its log) and paper records the user may see, separate from `/api/logbook` so its
cache is not involved; the logbook filter searches those names, and **Files** (`?files=any` or `?files=cat:<name>`,
`cat:` for uncategorised) keeps entries with files (of that category).
When files are chosen, the Upload dialog suggests a category from their names (for
example photos and video *Media*, `.csv`/`.ulg`/`.data` *Data*, NOTAM/permit/approval/risk *Preparation & permits*,
report/plot *Analysis*), only if all files agree, the category is listed and the user has not picked one.
`python3 app.py attachment-categories [--dry-run] [--map OLD=NEW ...]` moves a `[Category] ` prefix at the start of
uncategorised descriptions into the category and strips it; `--map Photos=Media` accepts other prefix names.

Each file is limited to **10 GiB** by default (`LOGDB_ATTACHMENT_MAX_BYTES`).
Uploads and downloads stream in bounded chunks; files upload sequentially with progress and errors.
Keep your reverse proxy's body-size limits and timeouts large enough for your recordings.
A compressibility sample avoids recompressing typical JPEG/video/archive files. Other files use
fast gzip compression only if the stored result saves at least 5%. No resizing, transcoding or other
lossy conversion is done; downloads reproduce the original filename and exact bytes.

`GET /api/attachments/<id>/view` serves the same bytes with `Content-Disposition: inline`, with the same
access checks as the download. The type comes only from an extension allowlist (`VIEWABLE` in
`storage.py`, shared with qualification evidence at `/api/qualification-submissions/<id>/evidence/view`): PDF, `.txt`/`.csv`/`.md`/`.log` as `text/plain`, PNG/JPEG/GIF/WebP,
video (`.mp4`/`.m4v`/`.webm`, and `.mov` served as `video/mp4`, which Chrome and Firefox then play) and audio
(`.mp3`/`.m4a`/`.wav`/`.ogg`), which play in the browser's own player. Any other
file (HTML, SVG, XML, other Office documents, no extension) gets 415 and stays a download, so stored files
never run script in the site's origin. `nosniff` and `Cache-Control: private, no-store` apply to both
routes; inline text and images also get `Content-Security-Policy: sandbox` (not PDF, whose built-in
browser viewers break under a sandbox). Video and audio get `default-src 'none'; media-src 'self'` instead: a sandbox
stops the browser's player. Video and audio are stored uncompressed, so range requests (seeking) always work.

**Word documents.** When LibreOffice is available (`LOGDB_OFFICE`, default `auto`: `soffice` on PATH;
`off` disables), `.docx` attachments up to 50 MB are viewable too: `/view` converts the file to PDF
and serves that inline. The conversion runs on a temporary copy with a throwaway LibreOffice profile
(no user macros or settings), headless, under a 90 s timeout, one at a time. Only a ZIP container is
accepted and the Word import filter is forced, so a renamed file of another type never reaches another
LibreOffice import filter. The PDF is cached as `LOG_DIR/previews/<sha256>.pdf` (identical files share it)
and removed with the last attachment of that content. A failed conversion returns 422 and caches
nothing; the download is unchanged. The installer installs headless LibreOffice automatically on
fresh installs and updates, without recommended packages. The default `LOGDB_OFFICE=auto` enables
previews when it is installed; an existing explicit `off` or custom binary setting is preserved.
For manual installations on Debian/Ubuntu, use
`sudo apt install --no-install-recommends libreoffice-writer-nogui` (a few hundred MB).

**Flight weather.** The Weather tab shows logged wind at takeoff and landing when available. The
**Fetch historical weather** button requests an hourly Open-Meteo/ECMWF estimate (wind at 10 m,
temperature, humidity, precipitation, cloud cover and pressure), separately from onboard data.
Only clicking the button contacts the provider, sharing takeoff coordinates rounded to 0.01°
and the UTC date. Results are saved in SQLite and reused for that location/hour, including after
re-parsing. Requests are serialized and limited to one per 10 seconds across the server; failures
are shown and can be retried. Flights need valid coordinates and a correct date from 1940 onwards,
at least six days old. No API key is required; the free service is
[non-commercial only](https://open-meteo.com/en/terms), with attribution under CC BY 4.0.

<a id="open-in-flight-plotter"></a>**Open in Flight Plotter.** For Betaflight, INDIFlight and blackbox logs, the flight page and an expanded Logbook
row show **Open in Flight Plotter ▾** (`static/indiflight.js`), with **Open in Blackbox Viewer** in its menu. It opens
the external INDIFlight web app (`https://app.indiflight.org/?openlog=plotter` or `=viewer`) in a new tab. The app
posts `{type: "indiflight-ready"}` to its opener, and the page replies with `{type: "indiflight-log", name, data}`:
`data` is the original file from `/api/logs/<id>/download/data`, sent as a transferable `ArrayBuffer`, and `name` the
file name of that download (its `Content-Disposition`, without the upload's folders; else `log-<id>`). Only messages
from that tab and the app origin are accepted. The page stops waiting, and cancels the download, after two minutes
(saying so when the tab is still open) or as soon as the app's tab is closed. The browser fetches the log with the
user's session, so the app never contacts this server and the handoff also works when the server is only reachable
over a VPN. For testing against another app instance, set `localStorage.logdbIndiflightApp` to its origin (for
example `http://localhost:8080`) in the browser console.

**MATLAB export.** The **MATLAB ▾** menu (`static/matlab.js`) at the top right of a flight page, in an
expanded Logbook row and in a log's details on Logs offers, per Paparazzi source log (`onboard_sd`/`gcs` with its `.log`), **This flight**,
**All flights** and **Whole log**, with progress and the download inside the menu. `POST /api/logs/<id>/matlab`
(`scope` = `flight` with `flight_id`, `flights` or `log`) queues a job (a source without a flight, on Logs, offers
only **Whole log**, and **All flights** when the log has flights); with `{"check": true}` it only returns the job id,
status and `log_id`, for `pprz_fetch('log', n)` and `ml.fetch(log=n)`, which download whole logs by log number; `GET /api/matlab-exports/<job>` reports
queued/running/ready/failed with progress and `/download` returns the file.
`POST /api/flights/<id>/matlab` exports one flight by its logbook number from that recording's own log (the same job
as **This flight**); with `{"check": true}` or form field `check=1` it only returns the job id and status
(`missing` when there is no such export), so MATLAB's `pprz_fetch` reuses a cached file until the id changes.
`POST /api/flights/matlab` takes several numbers (`flight_ids`, a list or `"1,3,4"` as a form field) and a `scope`
(`selected`: those flights of each log; `all`: every flight of those logs except the ones judged ground; `log`: the
whole log) and returns one job per log (`exports`, each with `log_id` and its `flight_ids`); `check` works as above.
Every exported flight carries `flights(k).logbook` (`matlab_logbook`): the logbook entry (comment, crew as linked
people, project, operator, category, places), its recordings, and the other recordings' setting changes and
messages that this log lacks, moved onto this log's clock as on the flight page (inside the export window for
flights, all for a whole log; no telemetry of the other logs). The logbook data is part of the job identity, so a
changed comment or link gives a new export.
These routes, the status and the download also accept API keys, read-only keys included (the requests are POSTs
but only read); the bulk `.zip` needs a browser session. All re-check that the user may see the log. Jobs are identified by log hash, scope, flight windows,
parser and exporter version, so the same request reuses a finished file. Finished files are published atomically
to `WEB_DATA/exports` (local disk) with a JSON side file and kept two weeks; above `LOGDB_EXPORT_MAX_BYTES`
(50 GiB) the oldest go first. `LOGDB_EXPORT_WORKERS` (1) bounds concurrent exports (each also uses up to 8
compression threads) and at most 20 may wait. A restart drops queued/running jobs (the page asks again) and
removes partial files.

**Bulk MATLAB export.** The Logbook always shows tick boxes; ticks survive paging and a
"N selected · MATLAB ▾ · ✕" control in the toolbar exports them. `POST /api/matlab-bulk` takes `entries`, per
logbook entry the flight ids of its recordings; the server keeps the ones in visible, finished logs, picks the SD
recording when there is one and else the GCS one (entries without either are counted as `skipped`) and refuses
more than 200 distinct logs. Each log becomes an ordinary `flights` export of its selected flights (so a log
whose flights were all selected reuses its **All flights** file), queued without the 20-job limit. A bundle file
`<id>.bundle` lists them; its status combines theirs (`parts`, `ready`) and its download streams an uncompressed
`.zip` of the `.mat` files (no copy on disk), re-checking every log.

The protocol embedded in the `.log` is parsed in `matlab_export.py`; the stored `.data` is decompressed straight
into `paparazzi_log_parsing --export-columns` (`cpp/export_columns.cpp`, a separate entry point that leaves normal
parsing unchanged), which decodes every row into binary column files in a temporary folder under `WEB_DATA/tmp`
(about the size of the uncompressed log as doubles). Python then writes a MATLAB v7.3 (HDF5) file with h5py:
datasets are created through the low-level API with shared property lists, and chunks are compressed on a thread
pool across columns and written in order. Times use the parser's clock-wrap correction for old onboard logs, so
the windows match the stored takeoff/landing times. Without MATLAB on the server, loading was verified with an
independent v7.3 reader; check new layouts in MATLAB itself.

The layout of the file (format 5) and the MATLAB tools that read it are described in
[matlab/README.md](../matlab/README.md). `python3 matlab_export.py LOG.data [--flights] [-o OUT]` converts local
files the same way (without the logbook's later corrections).

## Storage and backup

SQLite must not run on a network share (locking over SMB is unreliable and can corrupt it), so storage is split:

| What | Setting | Where | Backed up by |
|---|---|---|---|
| Database, session key, SAML keys (small, ~40 kB per log) | `LOGDB_WEB_DATA` | local disk | `app.py backup` → `LOGDB_BACKUP_DIR` |
| Compressed `.data`/`.log` files, flight paths and attachments (large) | `LOGDB_LOG_DIR` | local disk or network share | the share's own backup |

New `.data` and companion `.log` files are stored as `.data.gz` / `.log.gz` (fast, lossless gzip).
Deduplication still hashes the original `.data` bytes. The parser temporarily decompresses the pair
under `<WEB_DATA>/tmp` and removes it afterwards; allow local free space for one uncompressed pair
per parse worker. Individual downloads restore the original files; paired downloads stream a compressed
ZIP containing the original `.data` / `.log`, not nested gzip files.
Single `.data` / `.log` downloads and gzip-stored attachments go out as stored, with `Content-Encoding: gzip`,
when the client sends `Accept-Encoding: gzip` (browsers, Python `requests`): the client decompresses while saving,
so only the compressed size is transferred and the server does not decompress. Other clients (plain `curl`,
`wget`) get the decompressed file. These compressed responses do not answer Range requests; files stored
uncompressed (photos, video) still do. Scripts that send `Accept-Encoding: gzip` themselves must decompress.
Uncompressed legacy log storage is **not supported** by this test-environment change: delete those
test uploads and re-upload their originals. There is no automatic conversion or destructive reset.
The database, accounts and paper records do not need to be deleted. Attachments live under
`LOG_DIR/attachments`; include them in the log-directory backup, alongside the compressed source logs.

A Samba share can be mounted with the service user as owner, in `/etc/fstab`:

    //fileserver/share  /mnt/flight-logs  cifs  credentials=/etc/flight-logs/smb.cred,uid=logdb,gid=logdb,file_mode=0660,dir_mode=0770,_netdev,nofail,x-systemd.automount,x-systemd.mount-timeout=30  0 0

For a share that `/etc/fstab` mounts with `x-systemd.automount`, `install.sh` makes the service wait only for that
automount (`Wants`/`After=<mount>.automount`): the share is mounted on first use, so a file server that is not
reachable at boot does not leave the service failed with "dependency" (it then starts, and reading a log fails until
the share is back; no restart needed). Other folders (a plain mount, a local disk) get `RequiresMountsFor`, so the
service never writes into an empty mount point; `RequiresMountsFor` on an automounted share would demand the mount
itself at boot. Run `install.sh` again after adding `x-systemd.automount`. Avoid `noauto`: the share is then
only mounted when the service starts, and a failure at boot is not retried. After changing `/etc/fstab`, run
`sudo systemctl daemon-reload`; the automount takes over at the next boot (or after unmounting the share).
A soft-mounted share can briefly report a stored file as absent. Before qualification evidence, documents,
safety sketches or attachments are reported unavailable (503, "try again in a minute"), the file is looked up again
after short pauses (`storage.STORED_FILE_RETRIES`), and the log names the path and the actual error.

with `LOGDB_LOG_DIR=/mnt/flight-logs/logs` and `LOGDB_BACKUP_DIR=/mnt/flight-logs/backup`. The credentials file has
`username=` and `password=` lines (mode 600).

**Backup.** `app.py backup` copies the database while the app keeps running, checks the copy, and writes `logs.db`,
`secret_key` and `saml/*.pem` into a UTC timestamped folder under `LOGDB_BACKUP_DIR`, for example
`backup-2026-10-24T12-45-00.123456Z`. It keeps the latest **14 complete backup sets**, not 14 days:
after a successful backup, older timestamped folders are removed. A hidden staging folder is renamed
only after all files have been written, so failed backups do not publish partial sets or prune history.
Legacy files directly in `LOGDB_BACKUP_DIR` and unrelated folders are left untouched and do not count
toward the 14 sets. The backup contains the SAML private key, so keep that directory private.

**Pre-update backups** use a separate pool: `LOGDB_BACKUP_DIR/update-backups`, or
`LOGDB_WEB_DATA/update-backups` when no backup destination is configured (normally
`/var/lib/flight-logs/update-backups`). The latest **14 complete update sets** are retained independently
of the 14 scheduled/manual sets. They use the same online SQLite backup, integrity check, atomic
publication and permission rules. These snapshots contain the database and keys, not application code,
uploaded logs or evidence; they are not a full deployment rollback. The installer does not stop the
running application for this backup. Restore by choosing a snapshot inside `update-backups` and
following the normal restore procedure below.

By default, a backup fails if it cannot set private Unix file permissions (`chmod 600`).
VMware shared folders or SMB mounts may reject this even when writes work. Prefer a private local
backup destination, or restrict the destination using share/host ACLs and explicitly set
`LOGDB_BACKUP_EXTERNAL_PERMISSIONS=1`. With that option, permission-denied/unsupported `chmod`
errors produce warnings and the backup continues; write, flush and rename failures still fail.
The option does not configure or verify those external ACLs. Temporary destination files are
cleaned up on handled failure; previous complete backup sets are retained. An abrupt process stop may
leave a hidden `.backup-*` staging folder; it is not a completed backup and is not used for restore.

**Restore.** Stop the service, choose a timestamped backup folder and copy its `logs.db`, `secret_key` and `saml/`
into `/var/lib/flight-logs` (owner `logdb`, files mode 600) and start it again.
On a new server `install.sh` offers the newest timestamped set, falling back to a legacy flat backup.
Logs uploaded after the
last backup are not in the restored database: run the [upload script](#uploading-with-a-script) again, it only
uploads what the server does not know.

## Settings

### Overview GPS summaries

Logbook, statistics and entity overviews determine no-GPS indoor flights from endpoint
coordinates across merged sources, not by opening compressed flight paths on each request.
New parses store whether a flight has a valid endpoint or a valid GPS sample in its saved path.
This summary is retained for legacy data validation; a valid path does not override the
indoor/no-GPS classification when both endpoints are unknown.

On the first startup after upgrading, the server fills missing summaries before starting workers
or serving requests. Flights with valid endpoints need no path reads; the remaining legacy paths
are checked once (including old SQLite path blobs). No raw telemetry reparse is needed.
The backfill commits per log and resumes after interruption. Missing/unreadable/corrupt path files
fail the backfill explicitly rather than permanently misclassifying flights as indoors; restore
storage access and retry. Subsequent startups skip completed summaries.

To run the backfill separately, stop the server and run from the repository root with the same
environment/configuration as the service:

```sh
./pprzlog_env/bin/python webui/app.py backfill-gps-positions
```

Then restart the server. Reparse replaces summaries along with the flight rows. Flight maps and
charts still load their paths when requested; overviews do not. This does not cache source merging
or add server-side pagination.

### Environment configuration

Settings are `LOGDB_<NAME>` values, read from the environment, else from one file: `$LOGDB_CONFIG`, else
`/etc/flight-logs/logdb.env`, else `webui/logdb.env`. [logdb.env.example](logdb.env.example) lists them all with an
explanation; it is generated from `SETTINGS` in `config.py` (`python3 config.py example > logdb.env.example`, checked by
`tests.test_config_example`). `python3 app.py check-config` shows the effective values and where they come from, and catches typos,
a missing parser, unwritable directories and http/https mismatches. `install.sh` sets the first group for you.

| Setting | Default | Meaning |
|---|---|---|
| `PUBLIC_URL` | (empty) | Address users open, e.g. `https://logs.example.org` (used for SAML) |
| `COOKIE_SECURE` | `0` | `1` when served over https |
| `PROXIES` | `0` | `1` behind nginx/Caddy (trust its `X-Forwarded-*` headers) |
| `BIND` | `127.0.0.1:8000` | Address gunicorn listens on |
| `CORS_ORIGINS` | (empty) | Pages on these origins (comma-separated, `*` for any) may call `/api/...` from a browser with an API key, e.g. `https://app.indiflight.org` uploading blackbox logs. Only `/api` answers CORS, without credentials mode, so a browser session cookie is never used cross-origin |
| `HIDE_UNLINKED_AIRCRAFT` | `0` | `1` excludes unlinked telemetry flights from Logbook, Statistics, person details and Locations; admins still link the names under **Unlinked names** on Aircraft |
| `TRAINING` | `0` | `1` shows Training: in-app questionnaires that can grant a qualification on a pass |
| `BOOKINGS` | `0` | `1` shows the [Agenda](#booking-agenda) of bookable items |
| `OPERATIONS` | `0` | `1` adds [Outdoor operations](#outdoor-operations) to the Agenda (needs `BOOKINGS`) |
| `NOTAM_URL` | (empty) | Page that Plan flight links to for checking NOTAMs and drone zones; empty: no link |
| `TIME_ZONE` | `Europe/Amsterdam` | Time zone of the Agenda's local times; see [Time zones](#time-zones) |
| `DOCUMENTS` | `0` | `1` shows [Documents](#documents): shared documents and each person's own qualifications |
| `SAFETY` | `0` | `1` shows [Safety](#safety) (`/safety`): danger, incident and accident reports. The Operations Manual links `/safety`: keep it on in production (`check-config` warns when it is off) |
| `VIEW_AS` | `0` | `1` lets administrators [view as another user](#view-as-another-user) |
| `PERSON_PROFILE` | `0` | `1` asks everyone who signs in for a [person profile](#person-profiles) |
| `SITE_NAME` | `Flight logbook` | Name in tab titles, on the sign-in page, in email subjects and the sender name |
| `SITE_LOGO` | (empty) | Logo in the header and on the sign-in page: `NAME` of `static/logos/NAME.svg` (`NAME-white.svg` on dark backgrounds when present); without it the header shows `SITE_NAME` |
| `FOOTER_TEXT` | `SITE_NAME` | Text at the bottom of every page, before the Web UI version |
| `FOOTER_LOGOS` | (empty) | Footer logos: comma-separated `NAME` or `NAME=https://link` |
| `WEB_DATA` | `webui/data` | Database and keys; local disk only |
| `LOG_DIR` | `<WEB_DATA>/logs` | Uploaded logs and flight paths; may be a share |
| `BACKUP_DIR` | (empty) | Destination of `app.py backup` |
| `BACKUP_EXTERNAL_PERMISSIONS` | `0` | Opt in to externally managed backup ACLs when chmod is denied/unsupported; warns instead of failing on those chmod errors |
| `SAML_ENABLED` | `1` | `0` hides *Sign in with TU Delft* |
| `SAML_METADATA_URL` | TU Delft IdP | Identity provider metadata |
| `SAML_ENTITY_ID` | `<PUBLIC_URL>/saml/metadata` | This service's SAML entity ID |
| `SAML_PASSIVE` | `1` | Silent check on the login page: sign in without a click with an existing TU Delft session |
| `THREADS` | `8` | Gunicorn threads (always one gunicorn process: the parse queue lives in it) |
| `SLOW_REQUEST_MS` | `2000` | Warn in the service log about slower requests and long waits for the write lock ([Slow pages](#slow-pages)); `0` off |
| `WORKERS` | `1` | Logs parsed in parallel; increase only with sufficient RAM and temporary disk |
| `INGEST_WORKERS` | `1` | Threads that hash, deduplicate and unzip uploads |
| `PATH_RATE` | `5` | Flight path points per second (`0`: no map path) |
| `ATTACHMENT_MAX_BYTES` | `10737418240` | Maximum original bytes per attachment (10 GiB) |
| `ATTACHMENT_CATEGORIES` | `Preparation & permits, Notes, Media, Data, Analysis, Archive` | Attachment categories, comma-separated, in display order |
| `BULK_MAX_PENDING` | `8` | Shared pending-job budget for bulk scripts |
| `BULK_MAX_BYTES` | `17179869184` | Shared estimated-byte budget for bulk scripts (16 GiB) |
| `LOCAL_MIN_FREE_BYTES` | `2147483648` | Local staging/decompression headroom (2 GiB; parser output needs extra space) |
| `FASTREADER` | `../cpp/build/paparazzi_log_parsing` | Parser binary |
| `BLACKBOX_DECODE` | `../cpp/build/blackbox_decode` | Betaflight/INDIFlight Blackbox decoder |
| `SESSION_HOURS` | `12` | How long a login lasts |
| `SECRET_KEY` | generated in `<WEB_DATA>/secret_key` | Session signing key |

Relative paths are relative to `webui/`.

For example, the MAVLab instance uses `SITE_NAME=MAVLab`, `SITE_LOGO=mavlab`,
`FOOTER_TEXT=MAVLab, Faculty of Aerospace Engineering, TU Delft` and
`FOOTER_LOGOS=tudelft=https://www.tudelft.nl,mavlab=https://mavlab.tudelft.nl`. Restart the service after changing
them; `check-config` reports logo names without a file. In the footer the lettering of the TU Delft and MAVLab
logos is the same height (24 px) on one bottom line (`theme.css`).

Set `LOGDB_HIDE_UNLINKED_AIRCRAFT=1` and restart the server to hide telemetry flights whose
merged aircraft identity has no fleet link. Lists, counts, flight time, charts and location
map hints use only the remaining flights, for all users including admins. A merged SD/GCS flight
stays visible if either source is linked. Paper records are unchanged (they already require a
fleet aircraft). The Aircraft page keeps unlinked names available to admins (Unlinked names tab) for
linking. Direct flight/upload details remain
accessible under the existing permissions; this is a display setting, not an access restriction.
Person detail flight lists, charts and role totals use the same filter as Statistics, so PIC/PAC
totals agree when the indoor/non-flight filters match. Person headline flight time includes
participation in any crew role, not just PIC/PAC. Project detail pages and uploads are unchanged.
Set the value back to `0` to restore
the previous overviews; no records are deleted or reparsed.

## Accounts and TU Delft login

Click your name in the top bar to open **My account**. Local users can change their password
by entering their current password and a new password of at least 10 characters. On **People**, admins
can open a person and use ⋯ → **Reset password** (or **Set password**) to
generate a one-time, 48-hour link for a local account and share it privately.
Generating a link does not change the password until it is used; a new link replaces older links.
SAML/TU Delft passwords remain managed by the identity provider. API keys cannot manage passwords.
Admins can also **Remove password** (⋯) without deleting the profile,
flights or other linked information. This clears unused setup/reset links and prevents new password
sign-ins; existing sessions and API keys are not revoked. **Set password** can restore local sign-in
later. The last administrator's login method cannot be removed.

In **People → Edit → Account**, an administrator can **Make administrator** or **Remove admin rights**
for another person, with confirmation. Only active, non-external accounts can be promoted.
Self-demotion is blocked: ask another administrator to remove your rights. The last active
administrator cannot be demoted. These changes preserve ordinary project memberships and take
effect on subsequent requests, including requests using existing API keys.
The separate API keys tab lists the selected person's API-key names, scopes (*full (old)* for a key from before
scopes), prefixes, creation times and last-use times (or Never). Admins tick keys and use the selection bar: *Narrow scopes…* (one
key) or *Revoke…* after confirmation; scripts using that key immediately lose access. The selected user's other keys remain unchanged.
Secrets and hashes are never returned; full secrets cannot be recovered.
Both role changes (`PUT /api/admin/users/<id>/admin`) and key-detail access
(`GET /api/admin/users/<id>/keys`) or revocation (`DELETE /api/admin/users/<id>/keys/<key_id>`)
require an administrator browser session, not an API key.

### View as another user

Off by default; `LOGDB_VIEW_AS=1` enables it. An administrator opens a person's page and clicks **View as** (shown
only for other active accounts that can sign in). Until **Stop viewing as** in the band above the header, every page,
list and change uses that person's access and is recorded in their name, including their private qualification
evidence and My account. Changing the password, managing API keys and approving script sign-ins are refused, and
the profile prompt is skipped. Start and stop are written to the server log (administrator and person). It only
works in the administrator's browser session (`POST /api/view-as/<id>`, `POST /api/view-as/stop`), never with an
API key, and ends when the setting is switched off, the administrator loses admin rights, the person can no
longer sign in, or the administrator signs out.

<a id="projects-page"></a>**Projects page** ([UI rule](#ui-rule)). Tabs Projects,
Authorisations and Operators (`tab=`). **Projects** is a table like Aircraft: Project (a link to the project page),
Members (the first three, "+N", all on hover), Logs, Operator and Category of its flights (`GET /api/projects` returns
`operator_id`, `category` and `authorisation_id` for this; admins read `/api/admin/projects`). Search, sort, page and
rows per page (20 by default) are in the URL (`q`, `sort`, `page`, `size`). Admins, and managers for their projects, open a project
beside the table (`open=<id>`, `ptab=members`; Operator and Category are hidden then) with the tabs Details and
Members; other rows open the project page. **Edit project** is one dialog: admins change the name, operator (also
**New operator…**) and category (`PUT /api/admin/projects/<id>` with only what changed) and the members; a manager only
the members. Each member has one access select (See / See, upload / … edit / … delete / Manager) and × to remove
(↺ undoes); a search adds an enabled, non-external person with See. Save sends one `PUT /api/projects/<id>/members` per
changed member (`role: ""` removes). ⋯ (admins): **Delete project…** (logs stay). **Add project** and **Manage ▾**
(Import CSV…, Download CSV template) are admins only; dialogs replace `confirm()`.
**Authorisations** and **Operators** are read-only tables for everyone, without tick boxes. Admins click a row to
change it in a dialog (nothing is sent when unchanged); **Delete…** at the end of that dialog asks for confirmation (the
server refuses to delete one still in use and says where). **Add operator** is for everyone (only admins tick *Our own operator*), **Add
authorisation** for admins. Under Operators, admins click an external person to set the operator they fly for
(`PUT /api/admin/users/<id>/operator`). Existing API authorization and project visibility rules apply
unchanged.

**Project roles and managers.** `project_members.role` is `view` < `add` < `edit` < `full` < `manager`
(`ACCESS_ROLES`, `PROJECT_ROLE_RANK` in `app.py`; ticks See, Upload, Edit, Delete, Manager); each includes the ones
before it and a manager has everything `full` has. There is no global manager role. `add` (Upload) adds logs and
files; `edit` also changes the project's flights, logs and paper records like `full` does (comments, crew, hidden
flights, log dates, flight projects, links between records, file category and description; `EDIT_ROLES`,
`historical.can_edit`) but deletes nothing: deleting logs and paper records and removing file links stay `full`
(`FULL_ROLES`, `historical.can_delete`, `can_remove` of an attachment link).

<a id="retention"></a>**Retention (#313, `retention.py`).** EU 2019/947 (UAS.SPEC.050(1)(g)) keeps operation records
for at least 3 years, so `LOGDB_RETENTION_YEARS` (default 3, 0 = off) limits deleting on top of the rights above:
- A log is kept until that many years after its latest real flight: not a ground run (classification) and not a
  detected simulation, dated as in the logbook (date override included), or the upload date when the file clock has no
  calendar date (`log_kept_until`). Logs without such a flight can always be deleted. A paper record is kept from its
  date. `GET /api/logs`, `/api/logs/<id>` and `/api/historical/<id>` give `kept_until` while it applies.
- Within it, `DELETE /api/logs/<id>` and `DELETE /api/historical/<id>` answer 409 (with `kept_until`) to everyone but
  administrators. An administrator sends `{"reason": "..."}` (5–1000 characters; 400 without). The reason, who, when
  and a JSON snapshot (a log with its flights) go to `deleted_records`, whose triggers refuse every change and delete.
  `GET /api/admin/deleted-records` and **Logs → Manage ▾ → Deleted records** list them.
- Hiding (the selection bar) keeps a record out of the logbook and totals without deleting it.
Admins choose **Manager** in Edit project (or use `manager` in the project CSV). A manager gets Edit project for the
projects they manage, with only the members (no rename, operator, category or delete): they add and remove
members and set See/Upload/Edit/Delete (`PUT /api/projects/<id>/members`, the same handler as
`/api/admin/projects/<id>/members`). Only admins appoint managers or change a manager's row, also the manager's
own (403). Creating, deleting and renaming projects, operators, categories, authorisations and the CSV import stay
admin-only.

**Aircraft manager and detail edits.** An aircraft's manager is always a person: `drones.manager_id`
(`REFERENCES users ON DELETE SET NULL`); APIs return the person's current name as `manager` and the id as
`manager_id`. Add aircraft (`POST /api/admin/drones`) requires one (`manager_id`, or a `manager` name naming exactly
one active, non-external account by display name or username, case-insensitive, `db.user_by_name`; else 400). A set
manager can be changed to another person but not cleared or set to free text (400); an aircraft without a manager
keeps its other fields editable. The earlier free-text `drones.manager` is migrated once: names matching exactly one
such account are linked, other names are dropped (no manager). The column is then dropped (`ALTER TABLE ... DROP
COLUMN`, SQLite 3.35 or newer, which current Python builds bundle); with an older SQLite it is emptied and never read.
Managers of a project the aircraft is in today (a timeline period covering today, else its default project) and its
manager may change only status, current user, comments and manager (`PUT /api/drones/<id>`; other fields 403).
Name, serial, project history, aliases, import and delete stay admin-only.

**Aircraft requests.** A non-admin with `add`, `edit`, `full` or `manager` on a project sees **Request aircraft**: project, name and optional details, no serial (`POST /api/drone-requests`, browser session only).
Requests are stored in `drone_requests`, not in `drones`, so a pending request reserves no name and is never used
for log matching (`drone_name_map`, folder matching, relinking). The Aircraft page lists pending requests, greyed with
**Pending**, to the requester, the project's managers and admins (`requests` in `GET /api/drones`). Admins and the
project's managers **Approve** (`POST /api/drone-requests/<id>/approve`): the aircraft is created by the same path as
**Add aircraft** (next free serial, project timeline from the start, relinking) with the requester as manager; a
name taken by then gives 409 and keeps the request. **Reject** (`DELETE
/api/drone-requests/<id>`, optional `reason`) removes it and mails the requester; the requester may withdraw their own
the same way, without a mail. Both endpoints refuse API keys. New requests are mailed to the project's managers
(kind *Aircraft requests*) and to the administrators (administrator notices); the requester is mailed on approval
and rejection.
The Aircraft title, search/status filters and visibility switches share a wrapping toolbar to keep
the fleet list near the top of the page without sacrificing narrow-screen access.

Everything requires a login. Admins create accounts on the People page or on the command line (on a server prefix
`sudo -u logdb /opt/flight-logs/app/webui/venv/bin/`):

    python3 app.py create-user alice --admin --name "Alice" --email alice@example.org   # asks for a password (min. 10 characters)
    python3 app.py set-password alice
    python3 app.py list-users
    python3 app.py deactivate-user alice                       # or activate-user

A new account needs an email address (Add user, `create-user --email`). In Add user the username is optional: without one it is taken
from the address (the part before `@`, lower case, numbered when taken, as for a first SSO login). Accounts made before that rule may have none;
once an address is set it can be changed with People → Edit account but not removed. External crew (no sign-in), people
created by the qualification import and first SSO logins without a `mail` attribute are not affected.
Addresses must be valid and unique (case-insensitive). Without an email, automatic SSO
linking by email is unavailable; password setup/reset links can still be shared privately.

**Sign in with TU Delft** uses SAML 2.0 against `https://login.tudelft.nl/sso/saml2/idp/metadata.php`. Register
`<PUBLIC_URL>/saml/metadata` with TU Delft ICT (the contact listed in the IdP metadata) once; the service's signing keys are
generated in `<WEB_DATA>/saml/` and are part of the backup (losing them means registering again). The metadata is
valid until the certificate ends (`validUntil`, `saml.metadata_valid_until`; 10 years for a generated one): python3-saml's
default of two days made TU Delft's imported copy expire, after which login.tudelft.nl showed *Oops – Something went
wrong* until ICT imported the metadata again. Before that fix a copy holds the old two-day date, so it has to be
imported once more. A first SSO login
creates a non-admin account, linked to an existing local account with the same email. Give people access to projects
on the Projects page.
SAML identity mapping accepts both `uid` / `UID` and `displayName` / `Displayname`, alongside
the supported standard OID/URN names. A persistent NameID is the preferred subject identifier, but TU Delft sends a
transient one (new at every sign-in), so then the NetID (`uid`) is the TU Delft ID; an account still holding an old
transient ID (`_…`) gets the NetID at its next sign-in. `mail` supplies email.
A login is matched, in order, to the account holding that TU Delft ID, an active account with the same email, or a
unique display name/username/alias; otherwise a new account is made. The first match stores the ID on the account.
At every sign-in the display name (`displayName`) and email (`mail`) follow TU Delft's; the old name stays a log
alias, and a name that is another person's alias or an address another account has is not taken. Both are then
read-only in Edit account once the account has signed in since (`saml_attributes_at` set; before that, a wrong
value can still be corrected), and an empty email can still be filled in. Everything TU Delft sent at the last sign-in
(`users.saml_attributes`, JSON, with `saml_attributes_at`) is shown to administrators only, under People → Details →
TU Delft SSO data (`GET /api/admin/users/<id>/sso`); it moves with the sign-in on Move TU Delft sign-in and merges.
At TU Delft that is `uid`, `displayName`, `givenName`, `sn`, `mail`, `eduPersonAffiliation` and `preferredLanguage`.

**Silent check.** With `SAML_PASSIVE=1` (default) the first visit to `/login` in a browser session first asks TU Delft,
without showing anything, whether the person is already signed in there (SAML `IsPassive`). If so they are signed in
here without a click and return to the page they asked for; if not, TU Delft answers at once and the normal login page
shows (`/login?sso=none`). It is tried once per browser session, and never right after signing out, so signing out is not
undone. `/login?local=1` skips it (local accounts). The check depends on `login.tudelft.nl`: while TU Delft sign-in
itself is broken (an error page there), set `SAML_PASSIVE=0` so the login page shows directly.

**Accounts: merge and move a TU Delft sign-in.** With ⋯ of the open person on People, admins can fix a wrong
match. **Merge into…** (`GET`/`POST /api/admin/users/<id>/merge`, `into`) moves everything of an account to another
and removes it: every `users` reference (foreign keys and the flight crew overrides), keeping the higher project
role, one approved/pending qualification per type (the one valid longest; the other becomes superseded/withdrawn) and
no duplicate uploader rows, one person profile (the kept account's, else the moved one) and no duplicate or
self-supervision. Its display name and username become aliases; the kept account takes a password and TU
Delft ID it lacks (both then work), plus email, display name, default project and operator when empty, but keeps its
own admin rights. Refused: your own account as the one removed, internal with external, two different TU Delft IDs,
and removing the last administrator. **Move TU Delft sign-in…** (`POST /api/admin/users/<id>/sso-move`, `to` or a
new `username`) puts the ID on another account without one, or on a new account, so the next login goes there; the
old account stays a local account (its password, if any, still works) and can hand over its email/display name.
**Remove TU Delft sign-in…** (`DELETE /api/admin/users/<id>/sso`) drops the ID and the saved TU Delft data without
another account: the account keeps everything else and becomes a local one (name and email editable; *Set password…*
gives it a normal login). Not for the last administrator's only way to sign in. A later TU Delft sign-in with the same
email links the account again, so change the email when that should not happen.
All are recorded in `qualification_audit` (`account_merged`, `sso_moved`, `sso_removed`).

### Tests

Regression tests live in `tests/`. Run only the smallest relevant test with the project's Python environment:

```sh
cd webui
python -m unittest tests.test_people.PeopleDirectoryTests -q
```

Choose the test class/method or module that covers your change. Do not run the full suite unless requested.
When explicitly needed, full discovery is `python -m unittest discover -s tests -t . -v`.
The tests write large temporary files: if `/tmp` is small, point `TMPDIR` at an existing folder with more free
space, for example `TMPDIR=/path/to/tmp python -m unittest discover -s tests -t .`.
App tests use temporary storage and in-memory databases, without starting ingestion workers or using real logs.

### Home page

`/home` is the start page after signing in (`safe_next` sends a missing or foreign `next` there); `/` stays the
Logbook for the Operations Manual, and a signed-out visit to `/` returns there after signing in (`next=/`). The menu
lists **Home** first and the site logo links to it. The page reads `GET /api/home`, built only from what the user
can already see: the logbook read model (flights and paper records), visible attachments (`attachments.visible`),
their own qualifications, logs and bookings. Trainings (`/api/trainings`), the user's qualifications and documents
(`/api/documents`) and, for admins, the approval queue (`/api/admin/qualification-pending`) come from their own
endpoints; `theme.js` starts `/api/trainings` next to `/api/home` (when trainings are on, as last seen in the
browser), and `/api/documents` is asked for only when Your qualifications or Documents is on; each once. Empty cards are hidden.

**Cards and Customize.** The messages and Needs your attention are always at the top. The other cards (`HOME_CARDS` in
`app.py`) come in the user's order: **Customize** (next to the greeting, for everyone) ticks and drags them and saves
`{order, off}` in `users.home_cards` (`PUT /api/home/cards`; `{"default": true}` clears it; `/api/home` returns
`cards`, with cards added in a later release appended, off when off by default). By default only Flying, Coming up,
Today, Recent flights and Latest media are on; the other cards are added with Customize. The **Only mine** tick boxes of
Flying (`flying_mine`, default on) and Coming up (`coming_mine`, default off) are saved in the same setting at once
(`PUT /api/home/cards` with only that key), so they stay in a new browser and on other devices. `layout()` packs the shown cards
into rows of 12 columns (small a third, Recent flights and Latest media half; on a tablet small half, wide whole) and
stretches each row to the full width, so a card without content leaves no gap; on a phone each card is full width.

- **Needs your attention:** expired training passes; own approved qualification types (not internal ones) whose newest
  validity ends within 30 days or ended at most 60 days ago; own uploads that failed or have no date; open review cases;
  for admins, training passes and qualifications to approve.
- **Flying:** with **Only mine** (on by default) real flights (no detected simulation, not judged ground/uncertain) where the user is PIC, including
  paper records (their flight count and time): days since the last one, flights in 90 days, flights and time this year
  (UTC dates) and flights per month for 12 months. Unticking **Only mine** switches to every real flight the user can see (their projects; admins
  all), with the number of pilots (PIC) in the last 90 days (`all_flying`).
- **Your qualifications:** the user's current approved qualifications (as Documents → My qualifications), the one that
  ends first on top, orange within 60 days.
- **Training:** active trainings: to do, waiting for approval, expiring within 60 days, then the latest passes.
- **Your aircraft:** the aircraft the user is the responsible person of, then the ones they flew as PIC most recently
  (at most 5 in all), with status and the day last flown (`aircraft`, `home.my_aircraft`).
- **Documents**: the 5 newest documents the user may see.
- **Coming up:** everyone's bookings in the next 14 days on the items the user can view, as the agenda shows them (at
  most 10, `bookings.upcoming_all`, with who booked), or with **Only mine** only the user's own (at most 5,
  `bookings.upcoming`), when `LOGDB_BOOKINGS` is on; the list scrolls inside the card, which is no taller than
  the cards beside it; **Booked today**
  lists today's bookings on things the user may view.
- **Today:** an Open-Meteo forecast (`GET /api/home/weather?place=<id>`, browser session only) for the next 8 hours at
  the middle of a place (circle centre or polygon bounds), cached in memory per position for 30 minutes and fetched
  only when the card asks. The places offered are outdoor places on flights the user can see (admins: all), the most
  flown in the last 365 days first. The default is the user's choice under **My account** (`users.home_place_id`,
  `PUT /api/account/home`, null for the most-flown place; cleared when the place is deleted), else the most flown. A
  different place picked on the card goes into the URL (`?place=`) only. No go/no-go colours: there are no agreed limits.
- **Recent flights:** the 5 newest real flights the user can see, linked to their flight or paper record.
- **Latest media:** the 8 image and video attachments the user may see with the newest flight (of the newest 400): ordered
  by the date of the flight, log or paper record they are on (a log's typed-in date first; the latest when on several),
  else the upload time; earlier versions of a replaced file are left out. A tile opens the media viewer (below) with a
  link to that flight, log or record. Thumbnails come from `GET /api/attachments/<id>/thumb` (the same access check as viewing):
  ffmpeg (`LOGDB_FFMPEG`, default `auto`: `ffmpeg` on PATH; `off` disables) makes a JPEG up to 360 px wide on first
  request, of a video the frame at 1 s (else the first). It reads local files only (`-protocol_whitelist file`), two at
  a time, under a 30 s timeout; a gzip-stored file over 200 MB is skipped. Thumbnails are cached as
  `WEB_DATA/thumbs/<sha256>.jpg` and removed with the last attachment of that content; a failure leaves
  `<sha256>.none` so it is not retried. Without ffmpeg (or on a failure) the endpoint answers 404 and the page shows a
  plain tile with a play icon for videos. `install.sh` installs it (a manual setup: `sudo apt install ffmpeg`).

### Interface layout

Pages use a compact heading hierarchy with regular-weight supporting text. Longer instructions are
available through expandable **Help** disclosures; field requirements, errors and sharing warnings
remain visible alongside the relevant controls.

<a id="aircraft-page"></a>**Aircraft page** ([UI rule](#ui-rule)). The overview table is
the same for everyone; clicking a row (not the name, which links to the aircraft's overview page) opens the
aircraft beside the table (`open=<id>` and its tab `atab` in the URL; ✕ or the row again closes it; while it is
open the table leaves out Comments and Aliases, which the panel shows), with
**Details**, **Project history** and **Aliases** (autopilot boards for admins). **Edit aircraft** is shown to admins
and to whom `can_edit` allows the details (project managers of its current project and its manager): one dialog
with name and serial (admins), status, manager, current user and comments, and for admins its aliases (one per line;
removed and added ones go through `DELETE`/`POST /api/admin/drone-aliases`) and project periods
(`PUT /api/admin/drones/<id>/projects`); the details are one `PUT /api/drones/<id>` with only the changed fields.
A failed part shows its error at the field and the dialog stays open. Clicking an editable value or its label opens
the dialog at that field. ⋯ (admins): *Mark an alias as shared…*, *Stop matching a board…*, *Remove aircraft…*.
Admins have **Add aircraft** and **Manage ▾** (Import CSV…, Download CSV template, Mark a name as shared…), and the
tabs **Unlinked names** (when there are any, also with `HIDE_UNLINKED_AIRCRAFT`) and **Shared names** (one row per name with its recordings, boards and aircraft; a click opens its
boards below it, `sname=` in the URL, with *Remove ambiguous status…* at the bottom): tick names,
boards or recordings and use **Link to aircraft…** in the selection bar (the dialog lists every aircraft, including
Discard; a single folder candidate is preselected). Pending requests show **Approve**/**Reject…** to who may decide
and **Withdraw** to the requester. Dialogs close with Cancel, Close or Escape.
<a id="people-page"></a>**People page** ([UI rule](#ui-rule)). A table like Aircraft, the
same for everyone: Name (a link to the pilot page), Flights, Hours and Last flight as PIC within the flight filters
(from `/api/people-pic-flights`), and for admins Current licences (approved, not expired), Sign-in and Last sign-in
(`users.last_login`, returned only by `GET /api/admin/people`); others see licences on the pilot page. The profile (one per person, `user_profiles`; none yet for people who have not signed in
since profiles were turned on, external crew and imported people) adds columns for viewers who see profiles (admins:
everyone; a supervisor: the people they supervise, from `GET /api/profiles`, also those who share no project with
them): Role and Department by default, and under Columns Supervisors, Expected end (with incomplete / ending soon /
ended / to confirm), Number (admins), GitHub and Confirmed. One **Filters** pop-up next to Columns (its button shows
how many are active) holds Role, Department and profile status (Incomplete, Ending soon: within 3 months, Ended) for
viewers who see profiles, for admins Sign-in (Administrators, TU Delft SSO, Local password, Never signed in, Account
disabled; `access`) and Qualification (a current approved qualification of that type), Show external
and Without flights; the profile, sign-in and qualification filters also show people without flights. Manage ▾ → **Download profiles (CSV)**
exports the profiles of the rows shown (supervisors get Manage ▾ with only this). This replaces the former Profiles
tab (`?tab=profiles` now opens People). Headers sort (Last sign-in: never counts as oldest; Role in the order of Profile options, then the furthest expected
end first); the Columns choice is per browser (`people.columns`); 20 rows
per page with the pager at the bottom. Search, filters (`role`, `dept`, `status`, `access`, `qual`), Show external, Without flights,
sort, page and rows per page are in the URL (`search`, `external`, `unused`, `sort`, `pg`, `size`). On a phone only
Name, Flights and Last flight are shown. For non-admins a row opens the pilot page. For admins a row opens
the person beside the table (`person=<id>`, `ptab=<tab>`; the table then keeps only Name and Role, and the panel takes most of the width) with the tabs Details (the
account), Profile (with `LOGDB_PERSON_PROFILE`, not for external crew; `GET /api/users/<id>/profile`; `ptab=profile`),
Qualifications, API keys and Aliases. The Edit button in the panel header follows the tab, one small dialog each:
**Edit account** (Details, Qualifications, API keys: `PUT /api/admin/users/<id>/profile`, `…/operator`, `…/admin`,
`…/external`), **Edit profile** (`PUT /api/users/<id>/profile`) and **Edit aliases** (chips with ✕ and an Add field;
`POST`/`DELETE /api/admin/aliases` for the differences). A click on a value opens the matching dialog at that field;
Save sends only what changed. Qualifications keep their own
per-item actions and Add qualification; API keys use the selection bar. ⋯ (admins):
Set/Reset password…, Remove password…, Move TU Delft sign-in…, Remove TU Delft sign-in…, Merge into…, Remove user…. Admins have **Add user** and
**Manage ▾** (Import profiles (CSV)…, Import qualifications (ZIP)… and its template, Fill numbers from PDFs…, Check QR
codes…), and always the tabs Unlinked crew names (when any; tick names, then **Link to
person…** or **Add as external…**), Approval queue, Qualification types (a click on a type edits it, the handle
reorders) and Profile options.

The Aircraft overview has one line per aircraft: a Serial column first, the current project
(full history and "+N" earlier periods on hover), total flights (uploaded logs and paper records on hover), the first
alias with "+N", and long text cut off with the full value on hover. It is sorted by serial, newest TUD number first
and other serials after by name; the headers sort too. Search, status, filters and sort are kept in the URL; hidden
columns (Columns menu) are a per-browser preference. It is
paginated at the bottom like the logbook (20 rows by default; page and rows per page in the URL).
Aircraft without uploaded logs or paper records, and people without any log, paper record or approved
qualification, stay hidden until **Show aircraft without logs** / **Show people without logs or licences**
is ticked. A person counts as having logs as uploader or as linked PIC/PAC/GCS; the open aircraft or person
stays listed. Non-admins only count qualifications they may see.
Logbook has no top-level project filter and no longer applies a previously saved project filter.
The menu (`theme.js`) has Home (`/home`), Logbook (`/`), Agenda (`/agenda`, when `LOGDB_BOOKINGS=1`), Training (`/training`, when
`LOGDB_TRAINING=1`), Safety (`/safety`, when `LOGDB_SAFETY=1`) and **Directory ▾** (People, Aircraft, Projects, Locations,
Documents at `/documents` when `LOGDB_DOCUMENTS=1`, then Logs at `/logs` and Statistics at `/stats`), then the flight filters. On the right: **⬆ Upload** (for admins and members with add or full access on a project, `can_upload` in `/api/me`) and the name menu (My account, Night mode, Sign out). Menus close on a click elsewhere or Escape.
The upload dialog (`upload.js`, loaded on first use) takes files, zips and folders, also dropped anywhere on a page,
sends them in batches to `/api/upload` and lists **Your recent uploads** (`/api/logs?mine=1`) with their parsing
status; the open page refreshes on its `logsuploaded` event. `/logbook` remains a working alias; `/uploads` was
replaced by `/logs` (log-detail anchors `/logs#log<id>`).
The Logs list has one line per log (date, log with its size, aircraft, project, flights, flight time, status;
admins also see who uploaded it and when). A log with one flight links straight to its flight page; several open
the log, where each flight links to its page. Status shows only what needs attention (queued, parsing, error,
outdated); the **outdated** / **date unknown** badges filter the list (`/api/logs?status=outdated|date_unknown`).
<a id="logs-page"></a>The Logs page follows the [UI rule](#ui-rule): an opened log has **Edit
log** (one dialog: the project for admins, `PUT /api/logs/<id>/project`; the calendar date for who may set it on a log
without a confirmed date, `PUT /api/logs/<id>/date`; only changes are sent; a click on the Project or Calendar date row
opens it there) and **⋯** (Re-parse for anyone who can see the log, Delete log… with `can_delete`). Admins have
**Manage ▾** with Re-parse outdated…, Re-parse unknown dates… and Review date suggestions (when there are any). An opened log lists its settings changes collapsed (the
count and how many in flight); opened, 50 rows and **Show all**.
Confirmations and messages are small dialogs. The list polls `/api/logs` every 3 s while logs are queued, parsed or
fingerprinted and every 30 s otherwise (not while the tab is hidden, a dialog is open or the pointer is on an opened
log); a poll that finds the same rows leaves the table alone, and an opened log keeps its details until changed ones
arrive, so the page does not flicker.
Linked aircraft, crew, projects and named locations in the logbook open their detail pages.
Coordinates without a linked location still open the external map.
Aircraft serial numbers in the logbook appear on hover over the aircraft name rather than inline.
Add aircraft fills the serial with the lowest free `TUDxxxxx` number from TUD00401 on (gaps there first; numbers
up to TUD00400 may belong to old aircraft that are no longer listed, so they are never handed out again);
it can be overwritten, and `POST /api/admin/drones` without `unique_id` assigns it too. Admins get it as
`next_serial` from `GET /api/drones` and `GET /api/admin/drones`.
**Order.** The logbook lists the newest first (inside a day row the flights run oldest first). Flights with the
same time, typically logs without a takeoff time that only have a date (Blackbox, INDIFlight), are ordered by
log file name in natural order (`LOG00015` before `LOG00018`), then by flight number in the log, not by the
order they were parsed in. Upload sends the selected or dropped logs in that name order too, so flight numbers
mostly follow the file names; parsing runs in parallel, so this is not guaranteed, hence the ordering above.

**Group by day** is display-only. A row holds flights of one fleet aircraft on one UTC day with the same PIC,
where PAC and GCS are equal or empty on one side (two different names never group; the row shows the named
person), with the same simulation and estimated-date state. Logged flights share a place at the same named
location, otherwise within 1 km of the day's first takeoff; flights without GPS or a known date stay separate.
Paper flight records group only with each other, at the same named location; paper daily totals never group.
A row of several flights is tinted with a bar on the left (its flights keep a lighter bar when opened) and shows the
count (`4×`) in the number column, with a copy button for the flight numbers, space-separated as `pprz_fetch([...])`
takes them (paper records have none).
Only rows with several flights show the expand arrow (far-left column); their takeoff/landing times list each
flight on hover. A row with flights in different projects shows the most common project with "+N"; the split
is on hover. Changes in the Logbook: see [Logbook changes](#logbook-changes).
The Category column shows only the category; the authorisation name and the category source are in its tooltip.
The Columns menu hides table columns (Date always stays). The choice is stored per browser in `localStorage`
(`logbook.hiddenColumns`), so it survives navigation, Back and new sessions; Export CSV stays complete.
Overview table links use the theme's blue accent colour, with an underline on hover or keyboard focus.

### Project and aircraft CSV imports

For initial setup, import the qualification ZIP on People first (it can create accounts and types), then the
project CSV on Projects, then the aircraft CSV on Aircraft. Both catalog imports are admin-only,
under **Manage ▾** on their page, with downloadable templates. Files are comma-separated UTF-8 (optional BOM);
Latin-1 uploads are also accepted for older fleet spreadsheets. Limit: 1 MB / 5000 rows. All changes
from one file are committed together; any invalid row leaves the database unchanged.

Project columns are `project,person,role`, one row per member. `role` is optional and defaults to
`add` (view/upload); other values are `view`, `full` (view/upload/delete) and `manager` (see
[Project roles and managers](#accounts-and-tu-delft-login)). Person matching uses
unique display names or explicit aliases, case-insensitively, not implicit usernames. Unknown,
ambiguous or inactive people and conflicting roles reject the file with a row error.
Projects match existing names case-insensitively; reimport keeps project IDs and linked data.
Each included project's **entire member list is replaced**, including changed permissions and removal
of omitted members. Projects absent from the file stay untouched. A row with a blank person and role
creates or retains a project without members. Changing a project's name in the CSV creates a new project;
use the existing Rename control for a rename that preserves identity.

Aircraft retain the fleet columns `AC Nice Name`, `Serial Unique`, `Manager (Post Holder)`,
`Current User`, `Status`, `Date Bought`, `Project`, `Comments`. `name` and `unique_id`/`serial`
are accepted alternatives for the first two columns. `Manager (Post Holder)` (or `manager`) must name exactly one
registered person (see [Aircraft manager](#accounts-and-tu-delft-login)); a new aircraft needs one, and an unknown or
ambiguous name rejects the row. A blank manager keeps an existing aircraft's manager. Reimports use the existing serial/name matching,
update provided fields and preserve omitted optional columns. Empty Project cells preserve existing
project/default/history values; a non-empty Project must match an existing project.
Changing an existing aircraft's project requires `project_effective_date` (or `Project Effective Date`),
strictly YYYY-MM-DD. This starts a new inclusive project period on that date, truncating any period
crossing it and retaining earlier history and later scheduled periods. The new period ends the day
before the next existing period, if any. Repeating the same import does not duplicate aircraft or periods.
For new aircraft the effective date is optional: without one, the initial period starts at the purchase date,
else has no start, so logs uploaded before the aircraft was added to the fleet get its project too. In Project
history only the first period may leave **From** empty (from the start); saving fills unassigned flights.

Flights now record whether their project came from a timeline or a manual edit. A CSV period change
reassigns known automatic flights in that date range, including hidden flights, but preserves manual
assignments (including a manually cleared project). Existing non-empty assignments of unknown origin
are deliberately preserved; no migration guesses which were manual. Unassigned flights can still be
filled from the timeline. Assignment origin is retained across re-parsing alongside crew edits.
Paper records are not reassigned by an aircraft CSV import.

A flight without a usable date (unset clock, no date override) gets its aircraft's project only when the
aircraft's timeline is a single period without **From** and **Until** dates, so that project is right whatever the
day was (`project_source='timeline_undated'`). Such assignments, and a log project that came only from them, are
cleared and assigned again when the log's date is set (by hand or from a suggestion) or the aircraft's timeline is
saved, so a second project or an end date sends them back to the dated timeline or to no project. Setting a date
also fills that log's unassigned flights from the timeline right away.

A log without a project takes the project of its flights when they are all in one project (flights without a
project do not count; `db.LOG_PROJECT_FROM_FLIGHTS`): after automatic assignment and when someone sets a flight's
project by hand, so project members see the log on Logs and can open and download it, not only its flights in the
logbook. A log that already has a project keeps it. Logs left without a project by hand edits before this rule are
fixed once at startup (`data_migrations` records such one-time fixes, so an administrator can clear a log's project
afterwards without a restart undoing it).

Successfully parsed logs with no flight records inherit a project from their linked aircraft's
timeline on the recording date (including a corrected date). Every aircraft in the log must resolve
to the same project; missing dates, unlinked aircraft and timeline gaps leave the log unassigned.
Existing non-empty log project assignments are preserved. Eligible existing zero-flight logs are
also filled at startup and when aircraft assignments are updated, without reparsing telemetry.
These logs appear in the aircraft/project detail pages' Log files section but add no flight time.

### Places CSV import

`POST /api/admin/places/import` (admin; Locations, **Manage ▾ → Import CSV…**) reads the same CSV limits as
the catalog imports. Columns: `place`, `lat`, `lon`, `radius_m`, optional `indoor` (yes/no, 1/0,
true/false; blank is no) and `country` (ISO 3166-1 alpha-2 code or English name, e.g. `NL` or `Netherlands`; blank
keeps the country already set; `countries.py` holds the list, stored as `places.country`). The extra columns of `logbooks/places.csv` (`timezone`, `log_names`, `source`,
`confirmed`) are accepted and ignored; any other column is an error. Every row becomes a circle.
Places match by name, case-insensitively: no match creates a place, a match with the same name,
indoor flag and circle is `unchanged`, anything else updates it (keeping its ID, so flights and paper
records stay linked). A name that matches more than one place, a repeated name in the file, or an invalid
row rejects the whole file. With form field `preview=1` the response lists each row's action without
writing; the page shows that first. Replacing a drawn polygon by a circle is flagged in the preview.

### Training questionnaires

Off unless `LOGDB_TRAINING=1`. The Training page is session-only (API keys get 403). Admins create
questionnaires ([UI rule](#ui-rule)): single choice, multiple choice and ranking, each with
optional JPEG/PNG. The list has **Add training** and **Manage ▾** (Import training…) for admins. **Add training** asks
only for a name and description and then opens **Edit training**. On a training, admins have the tabs **Training**
(the training as they take it themselves: reading, test, own pass), **Questions** (a filterable list on the left,
flagging questions without a correct answer; the selected question is edited on the right and saved on its own) and
**Passes** (Reset… per pass), always shown to them (`tab=questions|passes` in the URL). **Edit training** next to
the title opens all settings (content, passing, access request mail) in one dialog; Save sends `PUT
/api/admin/trainings/<id>` only when something changed (uploading or removing the reading PDF acts at once). **⋯** has
Export and Delete training…. Every confirmation (unsaved question, delete question, reset a pass, remove the reading
PDF, delete the training) is a small dialog. Outside the test, the training is shown as the same centred card as the test: the result
(passed in green, waiting for approval in yellow, otherwise grey) with the attempts and the score of the person's own pass,
the qualification and until when it is valid, and Reading material / Practice again. `GET /api/trainings/<id>` returns
`pass_score_text` (from the stored passing answers) and `valid_until` (of the linked qualification) for the person's own
current or waiting pass.
**Qualification valid for (days)** sets the end date of the qualification a pass grants; the pass counts as current
while that qualification is valid, so its expiry (or a reset) reopens the training. Without a qualification the field
is disabled and a pass does not expire. Questions
are reordered by dragging their handle (or the arrow keys on it; `PUT /api/admin/trainings/<id>/question-order`).
Admins order the training list the same way (the handle on each card, not while searching; `PUT
/api/admin/trainings/order` with every training once); everyone sees that order.
Answer options are shuffled per attempt, also for administrators taking it on the Training tab (`GET
/api/trainings/<id>` gives them their own test in `test_questions` and the editor's list, in order and with the
answers, in `questions`). A training may also **shuffle the questions** and draw a number of
**questions per attempt** at random (`shuffle_questions`, `question_count`); the attempt (questions, their order
and the answer order) is kept in `training_progress.draw_json` until it is submitted, so Previous/Next and a reload
show the same questions, and any change to the questions or settings starts a new attempt. With a subset, the pass
score (set against all questions) is scaled to the points of the questions drawn, rounded up. After a pass,
**Practice again** (`?practice=1`, submit with `practice: true`) answers a fresh draw; its result changes nothing.
Nothing is seeded at startup; questionnaires are added by hand or imported. **Import**
(`POST /api/admin/trainings/import`, Edit-mode dialog) accepts one JSON file per training, or a ZIP
with that JSON plus JPEG/PNG question images (same size and content checks as the image upload).
Form field `preview=1` validates and shows a summary without writing. Validation matches the edit
endpoints (kinds, 2–20 choices, exactly one correct for single, at least one for multi); any error
rejects the whole file. A pass score above total points is rejected, also when editing a training or
editing/deleting a question (adding questions is always allowed). A failed import removes the image
files it wrote. A name that already exists is
rejected unless `replace=1`, which updates that training in place (same id; pass records kept;
questions and images replaced, old image files removed after a successful write). Link a
qualification by picking an existing type (`type_id` form field); types that require evidence are
not offered, and a type that requires expiry needs validity days in the JSON. ZIP imports check
each entry's size before reading (JSON ≤ 1 MiB, images ≤ 2 MiB) and only load referenced images.
**Export** (`GET /api/admin/trainings/<id>/export`) returns the same JSON, or a ZIP when images are
present.
**Reading material**: a training may carry plain text (`reading`, at most 20 000 characters; also editable in the
Details tab) and one PDF (`reading_file`, the name of a PDF in the import ZIP, at most 20 MiB, checked for the PDF
signature and stored next to the question images). On the Details tab the PDF of a saved training can be
uploaded, replaced or removed on its own (`POST`/`DELETE /api/admin/trainings/<id>/reading-file`, same checks; the
old file is removed). Replace-import sets both from the file, so an import without them removes them; export
writes them back. When either is present, opening the test shows the material in a
dialog; **I have read and understood the material** (`POST /api/trainings/<id>/read`) stores
`training_progress.read_at`, and only then does the quiz open. Submitting without a confirmation returns `409`.
A confirmation counts until the next pass, so a retake after an expired or reset pass asks for it again.
`GET /api/trainings/<id>/reading-file` serves the PDF to signed-in people; on screens below 700 px the dialog shows
an **Open the PDF** button instead of the embedded PDF. Admins can read, confirm and take inactive trainings to try
them. The material itself is kept outside
the repository, like the questionnaires.
The quiz UI shows one question at a time in a centred ~560 px card (`qi` in the URL); Previous / Next
keep answers in the form until Submit. Ranking rows are dragged (or moved with the arrow keys). Takers
retry until they pass; each submission returns the score. Failed answers are discarded;
`training_progress` keeps the attempt count and, on a pass, the answers and timestamp. A current
pass or a pass waiting for approval blocks retakes (`409`); when the linked qualification has expired,
a queued pass is rejected or withdrawn, or an administrator resets progress
(`DELETE /api/admin/trainings/<id>/progress/<user_id>`, which withdraws that training-granted
qualification), the person may take it again. A training can require choosing who delivered the
briefing from a short list of existing People the administrator sets; that name is stored on the pass.
There is no card-photo upload. By default a pass inserts an approved `qualification_submissions` row
immediately (`training-passed` in the audit log), superseding a previous approval of that type and
withdrawing a pending one. Immediate and queued passes both store a `note` such as
`Training "Flight safety briefing" passed 9/10 on 2026-11-11 · briefed by …` (`filename` stays for
uploaded files). With `needs_approval` (off by default; requires a linked type) the pass inserts a
**pending** submission (`training-pass-queued`) instead of an approved one; admins approve or reject
it in the People approval queue (`/people?tab=approvals`); for admins the Trainings list shows
**N passes to approve** (pending submissions linked to a training pass) linking there. Qualification views link that note to `/training?id=…` (admins get
`pass_user` so the Passes list opens on that person with their answers). Images live under
`WEB_DATA/training-images`, not the log share.

**Access request mail** (Details tab, collapsed section; needs email to be set up): a training may name one or more
addresses (`access_mail_to`, e.g. a secretary who arranges room access) and a mail text (`access_mail_text`, in which
tags filled in: `{name}`, `{email}`, `{username}`, `{number}` (student or employee number), `{department}`, `{role}`,
`{supervisor}`, `{end_date}` (expected end, or `indefinite`), `{training}`, `{date}` (the pass), `{valid_until}` (pass date
plus the training's validity), `{briefed_by}`, `{request}` (`new`, or `extension` for an
[access extension](#access-extensions)) and `{note}` (the note of an extension, else `-`); the profile tags are `-` when person profiles are off or the field is empty). The mail is queued when the pass is granted: at once,
or when an admin approves a queued pass (`needs_approval`). With an optional Yes/No question (`access_question`, e.g.
"Do you already have access to the flight lab?") the person answers it on a last step after the questions (together with who delivered the briefing, when asked)
(`access_answer` in the submit body, required then; stored in `training_progress.access_answer`) and only **No**
sends the mail. Its Reply-To is the person; **Cc** (`access_mail_cc_to`, optional addresses, e.g. a secretary while
To is the mailbox a mail flow reads) and **Copy to the person** (`access_mail_cc`) add copies. A test mail goes
without the copy to the person (the Cc only on request, below). The subject is
`Access request: <name> passed <training>` (an extension: `Access request: <name>, extension of <training> until
<date>`). JSON export/import does not carry these settings.
The text may use a little markup in the HTML part (the plain-text part keeps it as typed): `**bold**`, lines
starting with `- ` as bullets and lines starting with two spaces (or a tab) indented.
Lines `{if role: PhD Student, PostDoc, Staff}`, `{elif role: ...}`, `{else}` and `{end}` (each on its own line, not
nested) keep only the part matching the person's profile role (names as in the role list, any case; `{else}` also
covers people without a role), e.g. a different access group for employees and students. Saving refuses unpaired ones.
Below the typed text every access mail ends with a fixed block, one value per line, for mail flows:

```text
Request details:
Type: <new or extension>
Note: <the note of an extension, or ->
Person: <name>
Email: <address, or ->
Username: <username>
Number: <student or employee number, or ->
Department: <department, or ->
Role: <role, or ->
Supervisor: <names, comma-separated, or ->
Supervisor email: <addresses of the supervisors with an account, comma-separated, or ->
Until: <expected end yyyy-mm-dd, indefinite, or -; for an extension the new end date>
Previous until: <for an extension the end date before it, else ->
Training: <training>
Passed: <yyyy-mm-dd>
Valid until: <yyyy-mm-dd, or ->
Briefed by: <name, or ->
Reference: T<training id>-U<user id>-<yyyy-mm-dd>[-E<extension id>]
```

The reference is the same when one pass is mailed again, so a flow can skip duplicates; an extension adds its own
`-E<id>`. Type and Note come first, so a note cannot hide a later label from a flow that reads the text after the
last `Label:` (the note is one line).

**Send test mail…** (admins, below the access mail settings; `POST /api/admin/trainings/<id>/test-access-mail` with
`user_id`, `send_to` = `me` or `to`, `include_cc`, `access_mail_to`, `access_mail_cc_to` and `access_mail_text`) queues the mail as if that active person
passed today, using the form's To and text even when not saved yet. `me` sends it only to the administrator's own
address; `to` sends it to the To addresses, where a mail flow treats it as a real request, and with
`include_cc` (**Also to the Cc**) also to the form's Cc addresses. The subject and block
are the same, only the reference ends in `-test`; there is no Cc to the person and no administrator copy. With
`extension` (**As an extension request**) it is the extension mail, as if the person asked to stay a year past their
expected end date.

<a id="access-extensions"></a>**Access extensions** (`trainings.access_extension`, **Extension requests** in the access
mail settings; needs the access mail, email and [person profiles](#person-profiles)): someone with a current pass (not
waiting for approval) and a dated profile gets **Request extension…** on the training's result card, next to
**Access until** (their expected end date). The dialog asks for a new expected end date (after the current one and
today, within the role's `max_years`; `profiles.extension_problem`) and an optional one-line note (≤ 300 characters),
and posts `POST /api/trainings/<id>/extension` (`end_date`, `note`), stored in `training_extensions`. What happens
depends on the setting:

| Setting | Request | Then |
|---|---|---|
| `off` (Not offered, the default) | no button | |
| `direct` (Sent directly) | `sent` at once; at most one per training and day | |
| `approval` (After admin approval) | `pending`, administrators get an `admin` notice | Approve in the [Approval queue](#people-approval-queue) (`POST /api/training-extensions/<id>/review`, `approve` or `reject` with an optional reason mailed as kind `reviews`) |
| `retake` (After taking the test again) | `retake`: the test opens again (reading material first, no access question) while the current pass stays | A pass grants the qualification as usual; the extension is sent with it, or when an administrator approves the pass (`needs_approval`) |

Sending saves the new end date in the profile (`profiles.extend_end_date`, never earlier than the stored one; clears
`end_date_by_admin`) and queues the access mail with `Type: extension`, regardless of the Yes/No question. A new
request withdraws an open one; `DELETE /api/trainings/<id>/extension` withdraws it (**Withdraw** on the card,
**Cancel** above the retake test), and so do a reset of the pass and a rejected retake pass. `GET /api/trainings/<id>`
returns `extension` (`mode`, `end_date`, `latest`, `today`, `open` request) or null, and `extension_retake`. The
[end date reminder](#email-notifications) names the trainings that offer an extension and links to
`/training?id=<id>&extend=1`, which opens the dialog.

#### Access requests into a SharePoint list (Power Automate)

Set the training's **To** to a shared mailbox and let a Power Automate cloud flow (premium licence not needed for
these connectors) add each request to a SharePoint list. Nothing in the web UI changes; the flow runs in the
Microsoft tenant under the account that owns it, which needs access to the mailbox and edit rights on the list.

List columns (create each with the name in brackets, without spaces, then rename the display name if wanted; the
flow uses these internal names): Title (rename to *Person*), Email `[Email]` and Training `[Training]` (single line
of text), Passed on `[PassedOn]` (date only), Reference `[Reference]` (single line of text; *Enforce unique values*
on, which also indexes it), Status `[Status]` (choice: New, In progress, Done; default New) and Notes `[Notes]`
(multiple lines). For more of the block (Number, Department, Supervisor, Until, …) add a single line of text
column and a Compose with that label each, the same way; a flow that ignores them keeps working.

Flow steps:

1. Trigger **When a new email arrives in a shared mailbox (V2)** (Office 365 Outlook): the mailbox address, folder
   Inbox. Under *Settings → Trigger conditions* add
   `@startsWith(triggerOutputs()?['body/subject'], 'Access request:')` so other mail is left alone.
2. **Html to text** (Content Conversion) with *Content* = the trigger's *Body*.
3. **Compose** named `Details`: `last(split(body('Html_to_text'), 'Request details:'))`.
4. One **Compose** per value, named `Person`, `Email`, `Training`, `Passed` and `Reference`, each
   `trim(first(split(last(split(outputs('Details'), 'Email:')), decodeUriComponent('%0A'))))` with its own label
   instead of `Email:`.
5. **Get items** (SharePoint): the site and list, *Filter Query* `Reference eq '@{outputs('Reference')}'`,
   *Top Count* 1.
6. **Condition** `empty(body('Get_items')?['value'])` is equal to `true`; in *If yes* **Create item**: Title =
   `Person`, Email, Training, PassedOn = `Passed`, Reference and Notes = the trigger's *Subject*.
7. Optionally **Move email (V2)** to a *Processed* folder of the mailbox (same mailbox address), and a
   **Send an email (V2)** to an administrator with *Configure run after* = *has failed* on the Create item step.

Test it by passing a training whose **To** is the mailbox (or forwarding an earlier access mail to it): the flow's
run history shows each step's output. A changed column name or the label text in the block above breaks the
flow, so change both together.

### Person profiles

Off unless `LOGDB_PERSON_PROFILE=1` (`profiles.py`). A profile holds role, department or section (from the list,
or typed), supervisors (people whose role can supervise, or an external name), expected end date or Indefinite,
student/employee number and an optional GitHub username. Each role limits the end date: `max_years` (the date is at
most that many years ahead; empty: no limit) and `allow_indefinite` (may tick Indefinite). The default roles start
with Bachelor Student 1, Master Student 2 and PhD Student 5 years, and only Staff and Extern may be indefinite; a
database made before these settings gets the same values once for those role names. The profile form caps the date and
hides Indefinite accordingly, and the person's save refuses what is outside the limits. An admin (People editor or
profile CSV import) may set an end date outside them for odd cases: `user_profiles.end_date_by_admin` then marks it,
the person is not asked to correct it and may save the rest of the form while it stays unchanged; when the person
changes the date, the limits hold again. The import preview flags such dates (*Outside the role's limits*). While role, department,
end date or number is missing, a required supervisor is missing, the end date has passed or is outside its role's
limits (unless an admin set it), every page a signed-in browser opens redirects to
`/profile?next=…`. The API and API keys keep working, so uploads and scripts are never blocked. The GitHub name is
only checked to exist (`GET /api/profile/github`, GitHub's public users API, cached an hour; `FIRMWARE_GITHUB_TOKEN`
is used when set).

Who sees a profile: the person, admins, and the person's supervisors (without the number); nobody else.
Where it shows:
- the person's page (`/people/<id>`): a Profile card, with the people that person supervises and the projects
  of each (only projects the viewer may open), and **Edit** for the person;
- My account: the person's own profile and the people they supervise;
- People: the profile columns, filters and CSV download of the table ([People page](#people-page)) and the
  **Supervision** tab, right after People (people per card by role in the order of Profile options, then the furthest
  expected end; one card per supervisor, external ones by name; its search and Include ended in the URL as
  `p_*`). Admins see everyone; a supervisor sees the people they supervise. Both come from `GET /api/profiles`;
- People: the person's Profile tab, where admins use **Edit profile** ( `PUT /api/users/<id>/profile`):
  like the import, they may fill in only what they know (empty fields are allowed, a past end date too), but what is
  filled in must be valid; the person is asked for the rest at the next sign-in, and **Profile options** to edit the role and department lists
  (`PUT /api/admin/profile-options`; roles with *Max years* and *Indefinite*). The role list is seeded once when the tables are created; the department list starts empty, because it is
site-specific: admins add departments there or **Import CSV** (`name,group` per line), and people can also type one. Removing a list
entry empties that field in the profiles that used it, so those people are asked again.

**Manage ▾ → Import profiles (CSV)…** on People (admins, `POST /api/admin/profiles/import` with `csv` and `apply`) reads
`username, role, department, supervisors, end_date, number, github` (only `username` is required; it may be an
email). It fills in only empty fields, so what people entered stays, and rows may leave fields empty. Roles and
listed departments match by name; other departments are stored as typed. Supervisors (`;`-separated) link to someone
who can supervise by name or username, otherwise they are external. `end_date` is `YYYY-MM-DD` or `indefinite`.
A preview lists per row what would be filled in and what stays empty; nothing is written while any row has an
error. Profiles filled in by an import, or created by an admin, are marked `to_confirm`: the person is asked to
check them at the next sign-in (even when complete), and saving their own form clears it. An admin's later edit
keeps the mark and the person's "last confirmed" date.


### Welcome message

`welcome.py`. Only the mechanism is in this repository: administrators write the text in the site, and it is
stored in the database (`welcome_message`), so each installation has its own. It is shown as a card at the top of the
Home page (`/home`) until the person dismisses it (✕); no page is ever redirected for it, so the
Operations Manual links and the profile prompt work as before. Dismissing records the version
(`POST /api/home/messages/dismiss` with `{id, version}`, table `welcome_seen`); a new version shows again. Dismissing is
blocked while an admin views as someone (`VIEW_AS_BLOCKED`) and needs a browser session (API keys get 403).

**Home messages** are one list: `/api/home` returns `messages`, each `{id, kind, version, title, html, dismissible,
until}` with `html` built and escaped on the server (`welcome.home_messages`). Notices that cannot be closed come
first, then the welcome message (`id` `welcome`), then the other notices (`notice-<id>`), newest first. Dismissing is
per `id` and `version`; another kind of message only needs adding to that list.

**Notices** (tables `notices`, `notice_roles`, `notice_dismissals`): admins add, edit and delete them on the same page
as the welcome message (`/welcome`; `GET`/`POST /api/admin/notices`, `PUT`/`DELETE /api/admin/notices/<id>`, admins
with a browser session). Each has a title and text written like the welcome message, an audience (`all`, or `roles`
with one or more profile roles: only people whose profile has one of them; people without a role, or all people when
person profiles are off, see only notices for everyone), an optional *Show until* date (shown up to and including that
day, then gone by itself) and *Can't be closed* (`closable` false: no Dismiss, and dismissing gives 409; it stays until
its date or until it is deleted). Changing the title or text raises the version, so it shows again to who dismissed
it; changing only the audience, date or closing leaves dismissals as they are. The list shows each notice's audience,
date, and how many active accounts dismissed the current version. An account merge keeps the newest dismissal of either
account.

Admins open the editor at `/welcome` (Home → **Manage ▾** → *Welcome message and notices*; **Done** returns to Home; others are sent
to `/home`), which shows the message as it reads for them, how many active accounts dismissed it, and **Edit welcome
message** (a dialog, also opened by a click on the text; `PUT /api/admin/welcome` with `title`, `body`, `show_again`,
sent only when something changed). A click on a notice opens it in its dialog, with **Delete…** (a small confirmation). The text is written like a training's access mail and
filled in per person on the server: the tags `{name} {email} {username} {department} {role} {supervisor} {end_date}`
(from the account and profile, `-` when unknown; other tags stay as typed), blocks per profile role
(`{if role: A, B}` … `{elif role: C}` … `{else}` … `{end}`, each on its own line, checked when saving) and the same
markup: `**bold**`, `- ` bullets, two leading spaces indent, an empty line starts a paragraph, and `https://`
addresses and site paths (`/training`) become links. Everything typed is escaped. Saving a changed text keeps its version, so a
fixed typo is not shown again; *Show it again to everyone* raises the version. The first message, or one written
after it was emptied, is new to everybody, including people who signed in before; an empty text means no message.
An account merge keeps the newest version either account saw.

### Booking agenda

Off unless `LOGDB_BOOKINGS=1`; session-only (`bookings.py`, `static/agenda.html`). Administrators set up the
bookable items ([UI rule](#ui-rule): **⋯** → *Add bookable item…*, **Edit item** for the
open tab with **⋯** → *Delete item…*; they always see closed items and the tabs, also with a single item;
`/api/admin/booking-things`, table `booking_things`): name, description, a colour (required: its bookings and
the dot on its tab; without one the first of `db.ITEM_COLOURS` no item uses yet, also for items from before,
at start) and **Open for bookings** (active); in All a booking shows only its description, the colour says the item; they order the tabs by dragging them
(`PUT /api/admin/booking-things-order`). The place link is no longer shown or set. An item may need a qualification type, for
`booking` only (everyone sees its agenda) or for `viewing` and booking; it counts when an approved submission of
that type is valid today. Without it the API answers 403 with `thing_id` and `training_id` (an active training
that grants the type), and the page shows a dialog that links to it. Administrators may always view and change
or delete any booking, but booking for themselves still needs the qualification. They can also book for, or hand a
booking to, another active non-external account (`user_id` in `POST`/`PUT /api/bookings`; `GET` lists `people` for
them); that person gets the booking mail. Others change only their own and delete only
where they may view. Each item has up to 10 options (`booking_flags`: label, optional colour,
`blocks_overlap`); a booking stores the ones it ticks in `booking_flag_values`. Two bookings of the same item
may overlap unless either has a blocking option (409 otherwise); the other options only label a booking. An item
with `booking_things.exclusive` (Edit item → Overlapping bookings, `"exclusive": true`) keeps the stricter rule: two
bookings overlap only when they share a non-blocking option and neither has a blocking one. Items never conflict
with each other. The week view numbers overlapping bookings of an item 2nd, 3rd, … by `created_at`; who may book
keeps 15% of each day free next to the bookings, and a drag that starts on a booking they may not move selects a new
time (a click still opens it). `GET /api/bookings?from=&to=` (at most two months) returns the items and the
bookings of the items the person may view. Times are local wall-clock time (`YYYY-MM-DDTHH:MM`), at most 7 days
per booking. The item tab (`item=`), the view (`view=`: `day`, `3d`, `work` or `week`; also kept in
`localStorage` `agenda.view`; the default is `work`, `day` below 700 px) and the first day shown (`week=`, a Monday in
`work` and `week`) are kept in the URL. The grid always covers 00:00–24:00 and opens at 08:00 (or at the earliest
booking when none falls between 08:00 and 20:00); switching tabs keeps the time at the top. An item with bookings
cannot be deleted; make it inactive instead.

**Approval.** An item with `needs_approval` (item dialog: **Bookings need approval**) has optional approvers
(`booking_approvers`, `approver_ids` in the item body, active non-external accounts) besides the administrators.
A booking by anyone else gets `bookings.status='pending'` (`POST`/`PUT /api/bookings` answer `status`); a pending
booking holds its time like an approved one (it counts in the overlap check) and is shown striped as Pending to
everyone who sees the agenda. `POST /api/bookings/<id>/review` (`action` approve or reject, optional `reason` of at
most 500 characters; browser session; administrators and that item's approvers, 403 otherwise; 409 unless pending)
sets `approved` or `rejected` with `decided_by`, `decided_at` and `review_note`. A rejected booking frees the time:
it is left out of the overlap check, the calendar feeds and Home, and only its booker still sees it in the agenda
(faded, with the reason) to ask again (Save) or delete it. When the booker (not an administrator or approver) moves a
booking of such an item, or saves a rejected one, it is pending again. Turning approval off for an item approves what
waits. `GET /api/bookings` adds `needs_approval`, `can_approve` (and `approver_ids` for administrators) per item and
`status`, `can_decide` (and `review_note` when rejected) per booking. Administrators find all pending bookings in
the [Approval queue](#people-approval-queue) (`GET /api/admin/booking-pending`, filter **Bookings**); Home shows
administrators and approvers "N bookings to approve". Mails (kind `booking_requests`, Booking approvals): a new or
moved request to the administrators and the item's approvers, and approved/not approved (with the reason) to the
booker; the booker's own booking mail says it waits for approval.

Bookings are drawn in the item's colour; the options a booking ticks show as tags in their own colour, and in the
booking dialog as toggle chips (exclusive ones marked). Dragging in an empty part of a day picks the start and end in
quarter hours; a click books an hour. Dragging a booking that may be changed (mouse or pen) shifts it by days and
quarter hours with the same length; nothing is saved until **Save** in the booking dialog that then opens, so a move
sends only the usual mails. With a single item the page shows that item without tabs.

**Calendar feeds.** Each person can make a private link (`calendar_tokens`, `GET`/`POST`/`DELETE
/api/bookings/calendar`, **Subscribe** on the agenda): `/calendar/<token>/all.ics` and `/calendar/<token>/<item id>.ics`
are iCalendar feeds of the bookings that person may see, 30 days back to a year ahead, in floating (local) time.
The feed is reachable without a session (`calendar_feed` in `PUBLIC_ENDPOINTS`); the token is the key, so replacing
or turning off the link stops the old one. Calendar apps fetch it themselves, so they need to reach the site (VPN);
web calendars such as Google Calendar fetch from their own servers.

#### Time zones

Planning is in one local time zone, `LOGDB_TIME_ZONE` (default `Europe/Amsterdam`); flights stay in UTC as the logs
record them. Local (that zone, whatever the browser's): agenda bookings and slots (wall-clock times, `YYYY-MM-DDTHH:MM`,
no zone stored), Plan flight (its default start and whether a booking deadline has passed), the forecast hours (Open-Meteo
asked for that zone), the flight times listed in a slot (`takeoff_local`), the slot's requested and reminder times, the
booking mails (with a `Time zone:` line only for a location in another country than that zone's, from the system's
`zone.tab`) and the *Flight plan* row of a flight. When the browser's zone differs, the
agenda says which zone its times are in. UTC: the Logbook, Statistics and a flight's date and takeoff/landing times, which
show the local time next to them (`time_zone` in `/api/flights/<id>/detail`). The calendar feeds keep floating local times.

#### Outdoor operations

Off unless `LOGDB_OPERATIONS=1` with `LOGDB_BOOKINGS=1` (`operations.py`; plan in issue 134). The fixed agenda item
**Outdoor operations** (`booking_things.kind='operations'`) is created the first time the agenda is opened with the
switch on (an existing item with that name becomes it); it cannot be deleted while the switch is on, and with the
switch off it is hidden everywhere (agenda, Home, calendar feeds). Its **Edit item** has no single qualification,
approval tick or options; instead a list of qualification types (`booking_thing_rules`), each *Books directly* or
*Needs approval*. Someone may plan or join when they hold a current approved qualification of a listed type (one that
books directly is enough to skip approval); without rules everyone may. Administrators and the item's approvers
plan without approval and decide the rest. **Who sees it** (`qualification_for`): `booking` (everyone, the default) or
`viewing` (only who may plan, administrators and its approvers; `bookings.access`). In the agenda an item someone may
not see has no tab.

Only locations with **Bookable for outdoor operations** (`places.bookable`, Locations, admins; default off; never
indoors) can be planned. A booking of the item is a *slot* (`operation_slots`: the booking and its place) shared by
*teams* (`operation_teams`: PIC, project, optional aircraft, goal, approval status, and the preparation as JSON;
`operation_crew`). The slot's `bookings` row keeps its time; its status stays `approved` while a team is not rejected
(`rejected` when all are), so the ordinary booking queue and Home count never list it, and the generic booking
endpoints refuse the item (409; administrators may still delete a slot). Slots may overlap.

API (browser session): `GET /api/operations/options` (bookable places, your projects, people, aircraft not
discarded, your `rule`: `direct`, `approval` or null, `notam_url`, and `last_project_id`: the project of your
latest real flight as PIC, PAC or GCS in the logbook, else your default project, if it is one of yours, else null); `POST /api/operations/prepare` (`place_id`,
`start`, `end`, `pic_id`, `crew`, `drones`: the forecast, the checks and the overlapping slots at that place,
nothing stored); `POST /api/operations` (Plan flight: place, start, end, at most one day, and the team fields
`project_id` (one of your projects), `pic_id` (default you), `crew`, `drones` (aircraft ids, at most 10; table
`operation_drones`), `goal`, `notam: {checked,
reference}`); `GET /api/operations/<booking id>` (the slot with its teams); `POST /api/operations/<id>/teams` (join
for the whole slot; one team per PIC); `PUT`/`DELETE /api/operations/teams/<id>` (edit or leave: the team's PIC, whoever
added it, or an administrator; the slot is removed with its last team); `PUT /api/operations/<id>` (change the time:
administrators, approvers or a slot's only team); `POST /api/operations/teams/<id>/review` (approve or reject with an
optional reason). The booker and the PIC both need a rule qualification (403 otherwise); when either only has one that
needs approval the team is `pending`. An edited team stays approved while its PIC is the same.

**Flight area and ground risk buffer** (`ground_risk.py`, the operations manual's 1:1 rule; SORA 2.5 terms such as
the contingency volume come later). A bookable location may have a maximum height (`places.max_height_m`, Edit
location) and a default flight area (`places.flight_area`, a polygon of `[lat, lon]` points drawn or typed as corners with **⋯** → *Draw
or enter flight area…*; *Remove flight area…*; `PUT /api/admin/places/<id>` with `max_height_m` and `flight_area`,
`null` removes). Locations shows the area with its buffer and whether it stays inside (`flight_area_inside` in
`GET /api/places`). In Plan flight a team uses the location's area and height, or draws its own area
(**Change the area…** / **Draw or enter an area…**: the editor in `static/area_editor.js`, shared with Locations) and sets a lower height (`height_m`, at most the location's maximum, 400 otherwise;
`flight_area`). The buffer is as wide as the height (`buffer_m`, `buffer_method` `1:1`); the server checks in metres on a
local flat projection whether the area with its buffer stays inside the location's boundary (polygon or circle) and
the dialog shows the map (Leaflet, Turf for the buffer outline, loaded only when needed) with the verdict. A buffer
outside only warns. The team stores `height_m`, `flight_area`, `buffer_m`, `buffer_method` and `buffer_inside` in its
own columns and in the preparation (`area`), which the slot dialog shows with the map.

**External booking** (step 2 of issue 134). A bookable location may be booked elsewhere: Edit location → *External
booking* (`places.booking_mode` `none` or `mail`, later a SuperSaaS API; `booking_provider`, e.g. Unmanned Valley;
`booking_lead_hours`, how many hours before the slot it must be booked; `booking_mail_to`, the mailbox that books it;
`booking_mail_text` with the tags `{place}` `{provider}` `{date}` `{start}` `{end}` `{book_by}` `{teams}` `{booker}`
`{reference}` `{request}` and the access mail's markup). Plan flight then says where the request goes and the deadline
(the slot start minus the hours; warning only when it has passed). Once a slot there has an approved team
(`operations.external_event`), it mails the request to that mailbox (`mail_outbox.kind` `booking`, Reply-To the
booker, `mail.EXTERNAL`) and the slot is *Requested* (`operation_slots.external`, `external_status`, `requested_at`,
`book_by`); the mailbox also gets a mail when an approved team joins or leaves, when the time changes (*Requested*
again) and when the slot is cancelled (its last team leaves, or an administrator deletes it). Every mail has the
location's text (request, change, reminder) and a fixed *Request details* block (type, location, booked at, date, times,
book by, teams, booked by, email, booking number, reference `S<slot id>`). An administrator enters the external booking
number in the slot (`POST /api/operations/<id>/external` with `ref`; empty: requested again), and the slot shows
*Booked · number* in the week. The mail thread (`operations.queue_booking_reminders`) reminds the mailbox and the
administrators (`admin` notices) once when a slot is still *Requested* 12 h before its deadline (before its start without
one).

**Flights.** A slot's flights are matched when they are shown (`operations.slot_flights`, from the viewer's logbook
read model, so only flights they may see): telemetry flights that took off or landed at the slot's location within its
time (local times in `LOGDB_TIME_ZONE`, 30 minutes before and after), each to the first team whose PIC or crew is the
flight's PIC, PAC or GCS operator, or whose aircraft flew it. Flights there that match no team are listed as *Other
flights here*. The team's PIC, whoever added it, administrators and approvers link a flight to a team by hand or keep
it out (`POST /api/operations/teams/<id>/flights`, `flight_id`, `action` link or unlink; a link takes the flight from
any other team, also of another slot). These decisions (`operation_flight_links`) address a flight by log, ac_id and
parser flight number with its takeoff time, like the paper-record links, so they survive a re-parse and a renumbered
flight drops its stale link. The slot dialog lists each team's flights (✕ keeps one out) and the other flights with
*Link to …*; the flight page (`operation` in `/api/flights/<id>/detail`) shows **Flight plan**: the slot (a link that opens
it, `slot=` in the agenda URL), the team, its height and buffer and whether it was matched or linked by hand, or
*Not planned* for a flight at a bookable location without one.

The preparation is taken when a team books, joins or is edited, and stored with it: the weather forecast for the slot
(`weather.forecast_window`, Open-Meteo hourly, up to 16 days ahead, cached 30 minutes per position; later slots say so),
the checks (the PIC's and crew's approved qualifications of the rule types valid on the slot's date, and the aircraft's
fleet status: `Ready` or not set is fine) and the NOTAM check (`LOGDB_NOTAM_URL`, a tick and a reference). Fleet
aircraft have no registration or insurance data, so these are not checked yet.

`GET /api/bookings` adds `kind` per item (`rules` for administrators) and `operation` (`place`, `teams`, `pending`,
`pics`) per slot; the week shows a slot as its location with its teams and "N waiting", and opens the slot dialog
(teams, their preparation, Join, Edit, Leave, Approve/Reject, Change time). Plan flight replaces New booking on that
tab and offers to join an overlapping slot at the same place. Mails: a waiting team to the administrators and
approvers (`booking_requests`, the [Approval queue](#people-approval-queue) filter **Flight plans** from
`GET /api/admin/operation-pending`, ids `-(2e9 + team id)`, `kind=operation`), the decision to the team, and a join or
leave to the other teams' PICs (`bookings`). Home shows administrators "N flight plans to approve". The Decided view
lists teams that waited and were decided (`kind=operation`).

### Email notifications

Off unless `LOGDB_SMTP_HOST` is set (settings in the **Email** group of `config.py example`; use the organisation's
mail relay and a functional sender address in `LOGDB_MAIL_FROM`, and set `LOGDB_PUBLIC_URL` so mails can link to
pages). `LOGDB_MAIL_FROM_NAME` is the sender name (default: `LOGDB_SITE_NAME`) and `LOGDB_MAIL_FOOTER` the text above the settings link at the
bottom of every mail (`\n` for a new line; default the sender name). Mails are HTML (header with the sender
name, a button for the link, the footer with a link to the email settings) with the same text as plain-text part. `config.py check` reports a missing sender. Kinds (`mail.KINDS`):

| Kind | Who | Default | What |
|---|---|---|---|
| Account | everyone | immediately (fixed) | the one-time link to set a password, when an admin ticks **Email the sign-in link** (Add user, New setup link) |
| Expiry reminders | everyone | immediately | 30 and 7 days before an approved qualification ends (not when a newer approved or pending one of the type runs longer) and before the profile's expected end date (with person profiles on; it links to Request extension… of the trainings that offer one) |
| Bookings | everyone | immediately | own new booking; someone else changed or removed yours; a new or moved booking overlaps yours |
| Aircraft requests | everyone | immediately | your aircraft request was approved or rejected (with the reason); project managers: a new request for their project |
| Others' expiry reminders | admins | off | a copy of everyone's expiry reminders |
| Administrator notices | admins | immediately | a qualification waits for approval (submitted by a non-admin); a log failed to parse; a new aircraft request |
| Security notice | admins | immediately (fixed) | an account became an administrator (who, by whom, when; `mail.notify_security`): to `LOGDB_MAIL_ADMIN_TO` when set, else every active admin except the new one; cannot be turned off or put in the daily summary |
| Training access requests | admins | off | a copy of each training access request mail (see [Training questionnaires](#training-questionnaires)) |

Each person chooses **Immediately**, **Daily summary** or **Off** per kind under **My account**, tab **Email** (`GET/PUT
/api/account/email`, table `mail_prefs`); admins can send themselves a test mail there. When `LOGDB_MAIL_ADMIN_TO`
is set, administrator notices go only to that shared mailbox (no greeting, no per-person choice) instead of each
admin.

Mails are queued in `mail_outbox` in the transaction that causes them, so a rolled-back change sends nothing, and a
background thread sends them within about 15 s. The address is read at sending time; people without an address, with
a disabled account or external crew get nothing. Daily summaries and the reminder check run once a day after
`LOGDB_MAIL_DAILY_HOUR` (local time). A relay that cannot be reached is retried; a mail is marked failed after five
attempts or at once when the relay refuses the address. Once-only mails (reminders, overlap and parse-error notices)
are recorded in `mail_sent_keys`, so a reminder comes once per qualification, end date and step. Rows older than 90
days are deleted, and the body of a sent account mail is cleared because it holds a one-time link. Admins see the
last 90 days on **Statistics**, **Sent emails** (`GET /api/admin/mail`, `?status=sent|waiting|failed`; 10 per page with the pager below; a row opens
the mail). The tables are in `db.LOGBOOK_UNRELATED`.

### Private licences and qualifications

Users upload and manage their own qualifications on **My account**, alongside password settings.
The People table shows PIC flight count, hours and last-flight date per person ([People page](#people-page)).
Totals use the visible merged telemetry and paper-record read model, including daily paper counts,
and follow the shared browser flight filters. A single bulk `/api/people-pic-flights` request supplies
only the fields needed for those totals and filters; there are no per-person flight requests.
Usernames, aliases and sign-in information
are hidden; search still matches name, username, available email and alias.
Approved, non-expired licence chips remain visible to the person and administrators.
The whole directory card links to the pilot page; its licence chips are not evidence links and retain
date tooltips. Other viewers do not receive private qualification data.
Cards show up to two lines of licences, with a **More licences...** indication
when the list exceeds the available space. Names reserve space for the PIC totals and truncate
with an ellipsis instead of pushing the name and licences below the statistics; hover for the full name.
PIC statistics use compact 11px text and their natural width. Overflowing licence lists reserve
space for the More link so it never covers a chip.
The pilot page shows all project memberships as compact links without permission labels, followed by
current qualification chips. Approved expired records are under **Past qualifications**; username,
other names, account dates and default project are under **Account details**, both collapsed initially.
On this detail page, qualification chips link to evidence only when evidence is available to the viewer;
validity dates remain available on hover. Explanations are under collapsed Help controls. Existing
flight totals, map, charts, breakdowns and tables retain their filtering and access rules.
Person pages show PIC, PAC and GCS flight counts and hours in an always-visible strip below the
summary totals. Each flight counts once per matching role, including daily paper flight counts.
The breakdown cards are Aircraft, Projects and Locations; there are no separate Roles or Flew with cards.
Entity detail pages use a single wrapping title bar for the name, badges and Help (the active flight filters are on
the Flight filters button, with the full summary as its tooltip). For who may change the item, the title bar has
**Edit <item>** (person, location: admins; project: admins and its managers; aircraft: who may change its details):
it opens the list page with the item and `edit=1`, and `theme.js` opens that page's Edit dialog once. Breakdown cards show name, flights and hours, with the top five entries by flight time
visible initially and Show all / Show fewer controls. Cards share a 320px minimum height for
short lists; expanded cards grow independently. Use the main navigation or browser Back to leave a detail page.

**List navigation.** Logbook and Logs use query parameters `page`, `size` and `q`;
Logbook also stores `group=none` (the default is Group by day) and
`hidden=1`, while Logs stores `status` and `log`. Legacy Logs `#log123` links
still open that log. Entity detail pages use independent `flights_page` / `flights_size`
and `logs_page` / `logs_size`, plus the chart's `step`. Defaults are omitted.
The shared flight-filter snapshot uses `period` (always explicit), custom `from` / `to`
and enabled `indoor`, `nonflights`, `unknown_dates` flags. This prevents later changes to
browser-wide preferences from altering an older history entry; restoring an entry does
not overwrite the saved browser-wide preference. Logs still lists logs independently
of the flight filters. Page changes and applied view options create history entries;
typing a search replaces the current entry. Polling and page clamping never add entries.
The shared helpers in `static/theme.js` restore controls on Back/Forward and keep scroll,
expanded logbook/upload rows and detail breakdowns in `history.state`, not URLs.
Bulk selection is cleared; unsaved edits are not stored by the helper. Numbered controls retain
the first and last page numbers and a row range; detail pagers remain hidden for ten or fewer rows.
All these pages share outlined chevron-only Previous/Next buttons with accessible labels and
tooltips, compact page numbers and a blue editable current-page field. Enter or leaving the
field applies a valid page number; Escape cancels the edit. Separate First/Last buttons and
the separate page-jump field are omitted.
Upload pagination and its page-size selector sit below the log table.
Directory searches and editor panels are outside this navigation-state scheme.

Project details use compact member and default-aircraft links, with five shown initially and the rest
expandable. Membership permission labels are omitted, and creation dates sit under Project details.
Location type remains a title badge; circle/polygon information is under collapsed Area details.
Enable Edit, then select someone to open their
**Account**, **API keys**, **Aliases** or **Qualifications** tab. The editor shows **Active · TU Delft SSO**
for enabled accounts linked to TU Delft SSO (also a local account that has since signed in with SSO), **Active · Local**
for enabled accounts with a password, and **Active · TU Delft SSO + Local** for both; external, disabled and accounts
without either have no Active badge. The Sign-in column of the People table shows the same text without "Active", with **Admin** first for administrators. This is sign-in capability, not recent
activity or qualification validity. The Details tab shows the account and profile, with explanations under Help;
removal and password actions are under ⋯. API key details and revocation load only in the API keys tab.
See [People page](#people-page) for how admins change account settings, aliases and qualifications. Type settings
are in the Qualification types tab (admins).

<a id="people-approval-queue"></a>**Approval queue** (`/people?tab=approvals`, admins; always there for admins) lists
pending submissions from `GET /api/admin/qualification-pending` and, when the agenda is on, pending bookings from
`GET /api/admin/booking-pending` (shown with negative ids, posted to `/api/bookings/<id>/review`, filter **Bookings**,
`kind=booking`; see [Booking agenda](#booking-agenda)) and access extensions from `GET /api/admin/extension-pending`
(ids `-(1e9 + id)`, posted to `/api/training-extensions/<id>/review`, filter **Extensions**, `kind=extension`: new and
current end date, note; see [Access extensions](#access-extensions)): training passes (score and briefer parsed from the
pass note, the access answer, and the end date when the pass is a retake for an extension) and certificates (number, dates, evidence name and type, the approved submission of that type it would
replace, profile role and department when profiles are on). `can_approve`/`blocked_reason` come from
`qualifications.approval_problem`, the same rules the review endpoint enforces (type active, required dates, number
and stored evidence). Each row has Approve and Reject; ▸ opens the evidence (image inline, PDF in a small frame, via
the evidence view endpoint) and details in place. Approve sends nothing at first: the rows disappear and an Undo bar
shows for 5 s, after which the approvals are posted one by one (failures stay in the queue with their error). A new
approval sends the previous batch at once, and `pagehide`/hidden visibility posts any held approvals with
`fetch(..., {keepalive: true})`, so leaving the page does not lose them. Checkboxes and **Approve selected** do
the same for several rows; a blocked row has no checkbox and Edit (the person's Qualifications tab) instead of
Approve. Reject opens a dialog with an optional reason: `POST /api/qualification-submissions/<id>/review` takes
`reason` (≤ 500 characters, rejections only) stored in `qualification_submissions.review_note`, shown after the
rejected item in the person's qualifications and in the audit entry, and mailed to the person as kind `reviews`
(**Qualification reviews**, on by default) when email is set up. The filter (`kind=training|qualification|booking|extension`), search
(`q=`) and open row (`open=`) are in the URL; the Trainings badge links to `kind=training`. Keys while a row has focus:
↑/↓ move, Space selects, Enter opens, A approves, R rejects.
**Waiting | Decided** switches to the history (`view=decided`): `GET /api/admin/approval-history` (`approval_history.py`;
admins, browser session) with `kind` (`training`, `qualification`, `booking`, `extension`, `operation`), `q` (person, what, reason,
decider, number, description, note), `period` (`90d`, the default; `year`; `all`) and `page` (50 per page), newest decision first,
with `total`. It lists qualification submissions and training passes that were approved (also later superseded),
rejected or withdrawn (`reviewed_at` set; a withdrawal by the person says *By the person*), and bookings of agendas that
need approval that were approved or rejected (`decided_at` set), and access extensions that were sent, rejected or
withdrawn while waiting for approval (extensions sent without approval are *Automatic*). **Automatic** approvals are included and marked (for submissions as the audit log records them): a
training pass granted without approval (decided by the person at the moment of the pass), an imported certificate (the
importing administrator, *Imported*), a booking an administrator or approver made on such an agenda (approved by them
when it was made: `decided_by`/`decided_at` are stored for it) and bookings approved when approval was switched off
(no decider). Bookings deleted while pending, and submissions deleted by an administrator, leave no history. A row opens
its details in place (evidence preview, dates, training pass, booking time and options, the full reason); the period,
page and open row (`open=q<id>`, `b<id>`, `e<id>` or `o<id>`) are in the URL.
Admins edit username, display name, email and default project with **Edit account**; these four are saved
atomically in one request; previous unambiguous display names are preserved as log aliases. Display names are optional
and at most 80 characters. Usernames must be unique (case-insensitive),
1-40 characters and contain no spaces or control characters. Username changes do not create log aliases.
Passwords, SSO identity links, project memberships and existing records stay attached to the same person.
Display names conflicting with another person's log alias must be resolved first.

Log ingestion, startup and opening People do not create profiles from crew names. Unmatched PIC/PAC/GCS
names stay as logged text and appear under **Unlinked crew names** for admins (tick them and use the selection bar). Link them
to an existing person using aliases, or explicitly use **Add user** when a profile is needed.
**Add as external** (browser session only) creates an external person for crew of another operator, e.g.
flights under their operational authorisation: display name = the logged name, no password or email, and
`users.is_external=1`. Their flights link and count like anyone's, but they cannot sign in (password,
SAML, setup links and API keys are refused), cannot be project members (UI and project CSV reject them),
and stay hidden on People until **Show external** is ticked. The External checkbox in Edit account converts
a person either way (not admins or yourself); marking someone external removes their project memberships.
Log crew matching uses case-insensitive explicit aliases first, then unique display names. Login usernames
are not implicit log aliases; admins may add them deliberately. Duplicate display names stay unresolved
unless an explicit alias selects the person. Existing aliases are preserved and should be reviewed if
ambiguous. Explicit crew assignments override name matching; the existing unnamed-PIC uploader fallback
is unchanged. Paper CSV imports use the same crew matching rules, rejecting unknown or ambiguous names;
manual paper entry keeps direct person selection. SAML account linking is unaffected. Previously generated
`provider='log'` profiles are preserved, including their data and password-setup workflow.
Removing a profile no longer recreates it from log names. Deletion is not a merge: historical records
and private qualifications still block deletion, and explicit digital crew assignments are cleared,
not transferred. Move aliases and review references before removing duplicates.

Pilot pages are read-only and show only each type's current approved qualification, including expired
approvals clearly marked as expired. Validity dates appear on hover; qualification names link to
available evidence using the private view (or download) endpoints. These summaries and links remain
visible only to the person and admins. The People overview wraps names, aliases and qualification
chips to avoid horizontal scrolling; it continues to show only non-expired approvals.
Pending claims and previous submissions remain in My account and the admin per-person management
panel, not the pilot page. Management-panel dates are also available on hover and in the submission
details tooltip, with expired status kept visible.
**Qualification history (the audit of submission, review and deletion actions) is admin-only**,
available in the admin per-person panel, never on My account. Its API requires an admin browser session.
Private submission records and evidence are visible only to that person and administrators.
Sharing a project never grants access to these private records; approved summaries on pilot pages
remain subject to the existing page access rules. Use private uploads, not flight/log attachments, for licence evidence.

An admin first adds types under **Qualification types** on People, for example `A1/A3`, `A2`,
`RT licence` or an internal qualification. Each type has a short description (at most 300 characters;
shown under the type when someone adds a qualification and as tooltip on its labels) and evidence-required,
start-date, expiry, number, internal and active switches. Internal is a label for in-house qualifications, not a licence rule.
The list shows each type with its description and labels for the switches that are on; **Add type**
and a click on a type open a dialog (`POST /api/qualification-types`, `PUT /api/qualification-types/<id>`).
Qualification chips in the People overview and current account qualification lists follow the saved
type order, not submission order; multiple submissions of one type are newest first.
Types keep a `sort_order`; dragging a type's handle saves the new order at once
(`PUT /api/admin/qualification-types`, which keeps a description and group that are not sent). Types can be disabled.
A type can have a **group** (`grp`, at most 60 characters, typed or picked from the existing groups in the type
dialog; empty: none). Wherever types are listed (the type choice and *Also covers* when adding a qualification, the
qualification limits of Documents, the qualification of an agenda item and of a training, the Qualification filter on
People), they are shown under their group, in `<optgroup>`s or headings (`typeGroups` and `typeOptions` in `theme.js`).
A person's qualifications are grouped the same way: Documents → My qualifications, My account and People → person →
Qualifications (headings above the current ones; older ones stay one list) and the person page (one line per group).
Each qualification carries its type's `grp` for this (`inTypeGroups` in `theme.js`). Groups follow the order of
their first type; types without a group come last under *Other*. On People a type is dragged only within its group,
and a whole group by the handle on its heading. A type that joins a group (a new type, or one whose group is changed
with **Edit**) moves to the end of that group, so the groups keep their order; a group typed in another case joins the
existing one ("drone pilots" joins "Drone pilots"). The *Also covers* search also matches group names. While no
type has a group, the lists look as before. The group names are site data in the database, not part of the code.
Types have no hard-coded proof rules. Disabling a type stops submissions and approvals, without erasing
previous approvals. Changing either requirement also applies to pending submissions at approval time.
Types also have a **Valid from** requirement (`requires_start`); submissions store `valid_from`
alongside `valid_until`. Both date requirements default on for newly created types and can be disabled
independently. Existing types retain their expiry setting and get an optional start date; existing
submissions keep a null start. Migrations are idempotent. Dates stay blank in the form until entered.
Both dates accept past periods, and when both are supplied the start cannot follow the end.
Enabling the start requirement also applies to pending approvals, requiring withdrawal/resubmission
if a start date is missing. Existing approvals are not changed. Dates are retained in private history,
audit events and approved summaries; no historical flight-eligibility checking is performed.
Types can also require a **number** (`requires_number`): the licence or certificate number, e.g.
`NLD-RP-xxxxxxxxxxxx` for EASA remote pilot certificates. Submissions store it in `number` (up to 40
letters, digits, spaces and `. / - _`). When it is left empty and the evidence is a PDF, it is read from
the PDF text: a remote pilot certificate number (`[A-Z]{3}-RP-[A-Za-z0-9]{12}`) or an EASA licence number
(`NL.66.12345`, `BE.FCL.123456.A`: state, then 66, FCL, ATCO or 147). Plain and Flate-compressed text is
searched, and text in embedded subset fonts is decoded with their ToUnicode maps (as in RDW certificates).
Scanned PDFs hold only images: **Fill numbers from PDFs…** reads those with OCR when `pdftoppm`
(poppler-utils) and `tesseract` (tesseract-ocr) are installed (`install.sh` installs them). It renders the
first four pages at 200 dpi, turns each page upright (tesseract's orientation detection, and upside down as a
second try) and takes a few seconds per scan; one run stops reading new scans after 90 s and reports the rest
as not read yet. Uploads run OCR only in the upload dialog, within a time limit (see *Reading at upload* below). The reviewer checks the number and
approval is refused while a required number is missing. Admins correct it with **Edit** (with the dates,
audited as `number-changed`). **Fill numbers from PDFs…** under Qualification types opens a dialog that
(`POST /api/admin/qualifications/fill-numbers`) fills the empty numbers of approved submissions of such
types from their evidence (the PDFs are read before the database is locked) and lists what it filled and what it
could not read, each with a link to the person. The number is private like the evidence:
the person, admins and `/api/pilots`.

**QR codes** (`licence_qr.py`, issue 189). The QR code the issuing authority prints on a certificate is a better
source than the text, which can hide a sample number. After an upload a background thread reads the codes of the
evidence: the first two pages of a PDF rendered with `pdftoppm` at 150 dpi (and at 300 dpi when that finds no code), or the image itself, decoded with
`zbarimg` (packages `poppler-utils` and `zbar-tools`, installed by `install.sh`; without them nothing is read).
The codes are stored in `qualification_submissions.qr_json` (cleared when the evidence is deleted) and the
recognised parts are compared at display time with what was entered: a certificate number (same patterns as
above), dates (`YYYY-MM-DD`, `DD/MM/YYYY`, `DD-MM-YYYY`, `YYYYMMDD`) and links. An empty number is filled from the
code (audited `number-changed`, source `qr`). On the certificates seen so far the code holds only a link: RDW (NL),
DGAC AlphaTango (FR), ENAC (IT), LBA (DE) and DAC Luxembourg via the Eurocontrol learning zone link to their check of that
certificate; the UK CAA links to its general website (a Flyer ID to its *check a registration* page). The authority is
recognised by the link's host (`licence_qr.AUTHORITIES`) and must match the country of the entered number
(`NLD-RP-…` with RDW). Administrators see one line per licence: *Verification link: <authority>* with *check this
licence*, *QR code links to the <authority> website, not to this licence*, *QR code differs* (with what differs: the
country, or a number or date in the code), *QR code matches* (a number or dates in the code equal the entered ones),
*unknown link or text*, or *No QR code found*, and the raw content. They open the link themselves: the server never
contacts outside services. **Check QR codes…** under Qualification types reads the approved and pending licences not read yet (*Read
new*) or all of them (*Read all again*) and lists them by result. `POST /api/admin/qualifications/check-qr` reads for
about `seconds` (default 20, at most 90; `0` only counts) and returns the results with `total` and `later` (left); the
dialog first counts, then calls it in rounds of 6 s with a counter (read of total, elapsed seconds) and **Stop**, until
none are left. *Read all again* sends `before` (the start of the run), so every licence last read before then is read
once. A file that cannot be read is stored as read without codes, so it does not stop the others. A licence with
evidence that was not read yet shows *QR code not read yet* to administrators. What each authority's code holds differs, so the raw content is kept as is.

**Reading at upload** (`read_evidence`, issue 226). Choosing a file in the upload dialog sends it, with the chosen
type, to `POST /api/users/<id>/qualifications/read` (same access as submitting; browser session only; the file is
deleted after reading and nothing is stored). The server reads the QR code, the PDF text and, when a field the type asks
for is still missing and the PDF text gave no number (so there is no text layer to read), OCR: pages of a scanned PDF
(turned upright first) or a JPEG/PNG as it is. It returns what it found per field with its source (`qr`, `text`, `ocr`;
that order of preference), the dates seen and where the document differs from the typed values; people who are not
administrators get only the QR check's status, authority and issues, not the decoded codes. The PDF is decompressed
once per read, with one budget for all its streams together (`PDF_TEXT_BUDGET`, 80 MiB). Dates and their labels come
from `pdftotext` (poppler-utils) when installed, which also decodes subset fonts; else from the built-in PDF reader.
Labels are recognised in English, Dutch, French, German, Spanish, Italian, Portuguese, Polish, Swedish, Danish,
Norwegian, Czech and Finnish (`DATE_LABELS`, `MONTHS`). A complete read is kept in memory for 30 minutes per person
and file (SHA-256) and used on submit of that same file instead of reading it again (then OCR results count too).

**Also covers** (issue 255, part 1). The read also returns `suggestions`, `[{type_id, reason}]` of other active types the
certificate probably covers: `number` when records of that type (anyone's: pending, approved or archived) carry the same
certificate number, `text` when a distinctive part of the type name (`type_tokens`: a token with a digit and a letter
such as A2, A1/A3, STS-01, or capitals only of three letters or more such as STS) appears as a whole word in the read text
(QR code, `pdftotext`/PDF text, OCR). Plain names (First aid) are never suggested from text; only type ids are returned.
The dialog shows them as one-click chips next to the added ones and a search over all other active types. On submit
`also_type_ids` (JSON list, at most `ALSO_TYPES_MAX` 20; the chosen type and repeats are dropped) makes one pending record
per type in one transaction, with the same number, dates and `read_from`, each with its own copy of the evidence (so
deleting one record's file keeps the others') and its own audit entry (`certificate_covers` lists the types). Every
covered type is checked like the chosen one (active, required evidence, dates and number, no pending one of that type);
one failing refuses the whole certificate and names the type. The response adds `ids`; admins get one notice.

The whole read has one time limit (`QUALIFICATION_READ_SECONDS`, default `READ_SECONDS`, 25 s), shared by the QR code
and OCR: every `pdftoppm`, `zbarimg` and `tesseract` call gets at most the time left. When it runs out, what was found so
far is returned with a note in `notes` (*Reading the QR code took too long* or *OCR took too long; fill in the
fields*); a QR code not read in time is read in the background after submit as usual. One read per person runs at a
time; another one meanwhile gets 429 (the server runs one process, so an in-process set is enough). The dialog sends one
read at a time: a file chosen during a read is read when that one is done, and after a 429 it tries again.

The dialog fills empty, shown fields and remembers which ones it filled; when another file is chosen, those it filled
and the person did not change are replaced (or cleared, also when reading the new file fails), typed values stay. With a file chosen, the date fields are no
longer required in the browser. On submit the dialog sends `read_from` (JSON, field: source) for the values it filled
that are still unchanged. The server then reads the QR code and the PDF text (no OCR) when a field the type asks for is
empty or `read_from` is sent: empty fields are filled from it, a claim whose value the QR code or PDF text read there
confirms (also an `ocr` claim the text confirms) is recorded in `read_from` with the source that confirmed it, and any
other claim in `read_unchecked` (shown as what the upload dialog reports it read, not confirmed). Typed values are
never replaced; a Valid from after a read Valid until gives the order error naming the read date, and a field missing
from both form and document gives the usual error. The `submitted` audit event records `read_from`, `read_unchecked` and
`differs`. When the QR code was read at submit it is stored in `qr_json` and the background read is skipped.

Dates are recognised as `YYYY-MM-DD`, `D.M.YYYY` and `D-M-YYYY` (day first), `D/M/YYYY` only when it is unambiguous (the
day is above 12, or day and month are equal; `03/12/2026` could be 3 December or 12 March and is skipped), and
`D Month YYYY` (English, Dutch, French, German month names, also `mrt` and `März`). A label in the 80 characters before
a date, and after the previous date, decides its meaning (*valid until*, *expiry*, *geldig tot*… → Valid until;
*valid from*, *date of issue*, *afgifte*… → Valid from; *birth*, *geboorte*… → ignored). Without labels, the latest pair
of dates a whole number of years apart (1–10, or one day less) is taken as Valid from and Valid until; anything else is
left for the person to type. At most 500 dates per format are read from one document (`TEXT_DATES_MAX`).

Internal ratings such as FI (flight instructor) and CTR are qualification types with Internal on and
Evidence off; admins grant them by submitting and approving for the person, or by import rows without a
file.

`GET /api/pilots` lists people with their approved qualifications valid today, for documents such as the
Operations Manual: per person `id`, `name` and `qualifications` (`type`, `number`, `valid_from`,
`valid_until`, `internal`), no contact details. `active=1` (default) leaves out deactivated accounts;
`qualification=FI` (comma-separated types) keeps only people with one of those. Admins only, by session or
API key; give a document build an admin account's key with only the `read` scope.

### Documents

Off unless `LOGDB_DOCUMENTS=1` (`documents.py`, `static/documents.html`); it needs no person profiles. Any API key
reads (`GET /api/documents`, view, download); the page and every `/api/admin/` route below need a browser session or
an admin's key with the `documents:upload` or `documents:manage` scope (see API key scopes); an old full key gets 403,
so a leaked one cannot change or delete documents (issue 235). The ZIP import needs a browser session for every key.
`GET /api/documents` returns `intro` (the text at the top, `document_settings`), `groups` (`document_groups`: name,
description, sort order; one level, shown as page tabs with All, General for documents without a group and My
qualifications; the tab is kept in the URL, `?tab=`; empty groups only for admins), `documents` the person may see
(`documents`: title, description, optional group, limits, `valid_from`/`valid_until`, `current`, file), `qualifications` (the person's own approved
submissions valid today, with evidence `view`/`download` links when there is a file; also for admins, only their own)
and, for admins, `types` (all, with `active`, so a limit to a type that was deactivated later stays in the edit
dialog), `profiles` (person profiles on), `roles` (`profile_roles`) and `max_bytes`.

<a id="documents-page"></a>**Documents page** ([UI rule](#ui-rule)). The page reads the same
for everyone; admins additionally see every group and tab (also empty ones, unless searching) and a small 🔒 on a
limited document (its limits on hover), so the page stays calm to show someone. Admins have **Add document** and **Manage ▾** (Add group…, Edit text at the top…, Import ZIP…,
Download ZIP manifest template) in the title row; a click on the text at the top opens its dialog too. Every document
line has **✎** for admins beside the download arrow, shown on hover (on touch screens always; the line itself still opens the document; no tick boxes): it
opens **Edit document**, one dialog with every field (also the group) and an optional replacement file; Save sends the
details with `PUT /api/admin/documents/<id>` only when one changed, and the file with `POST …/file`. **Delete…** at the
end of that dialog asks once and sends `DELETE`.
A group's heading has **Edit group** and **⋯** → *Delete group…*: for a group that still holds documents the dialog
only explains how to move or delete them first, since the server refuses (409).

Admins see every document. Anyone else must meet every limit set on it: `admin_only` (administrators only); `type_ids`
(`document_qualification_types`: a current approved qualification of one of these types, for example A1/A3, A2 or
STS; the old single `documents.type_id` is copied there once, when the table is created, and is no longer read or written; a request
that still sends `type_id` gets 400); `roles_limited` with the roles in
`document_profile_roles` (the person's profile role, `profiles.py`). Role limits fail closed: with
`LOGDB_PERSON_PROFILE` off, or when every chosen role was removed from the profile lists, only admins see the document.
A document with `valid_from`/`valid_until` (both optional, `YYYY-MM-DD`) is hidden from everyone but admins outside
those dates (`current` false; marked expired or not valid yet for admins), so last year's certificate never shows.

Files are stored under `<LOG_DIR>/documents` (mode 0700; gzip when it saves at least 5%, at most 100 MiB each; counted
as Documents on Statistics) and served by `GET /api/documents/<id>/view` (inline, only the safe types of flight
attachments) and `/download`. Admins: `PUT /api/admin/documents/intro`, `POST|PUT|DELETE
/api/admin/document-groups[/<id>]` (a group that still holds documents cannot be deleted), `POST /api/admin/documents`
(multipart: `file`, `title`, `description`, `group_id`, `type_ids` repeated, `admin_only`, `roles_limited`, `role_ids`
repeated, `sort_order`, `valid_from`, `valid_until`), `PUT /api/admin/documents/<id>` (the same fields without the
file, as JSON; `role_ids` and `type_ids` lists), `POST /api/admin/documents/<id>/file` (multipart `file`, optional `valid_from` and
`valid_until`: replaces the file, for example a renewed certificate, sets the dates that are sent and keeps the other
fields) and `DELETE` (also removes the file). Reading with a key in `$KEY`:

```sh
curl -H "Authorization: Bearer $KEY" https://<server>/api/documents                       # list (ids, dates)
curl -H "Authorization: Bearer $KEY" -OJ https://<server>/api/documents/<id>/download    # download
```

**ZIP import** (`document_import.py`, **Manage ▾** → *Import ZIP…*; browser session only, API keys get 403). A ZIP holds
`manifest.csv` (`GET /api/admin/documents/import/template`) and the files. Columns, in any order: `title` and `file`
(required), `group` (empty, or `General` when no group has that name: no group), `description`, `valid_from`,
`valid_until`, `admin_only` (yes/no), `qualification` (qualification type names separated by `;`, stored in
`document_qualification_types`) and `roles` (profile role names separated by `;`). A file path with a folder must match
that folder; a bare file name may match one file in a subfolder. Two ZIP entries with the same path, a malformed CSV
line, or more than 500 rows are refused.

`POST /api/admin/documents/import` (multipart `file`, at most 200 MiB, else 413) checks every row and returns it with an
outcome: *add*; *replace* when the title and group (case-insensitive) match an existing document, whose file and
settings are then replaced; *skip* when its file and settings are the same, so a ZIP can be imported again. A column
left out of the header keeps the existing document's value (a minimal `title,group,file` manifest replaces only files).
For a replace the preview lists what changes (`changes`) and the limits it removes or widens (`removes`: administrators
only, qualification, roles). Unknown groups are added on commit; an unknown qualification or role, a missing file, a bad
date or flag, or the same title and group twice is an error and blocks the import.

The checked preview is staged under `DOCUMENT_IMPORT_DIR` (default `<storage>/../tmp/doc-import`) for 30 minutes. `POST
/api/admin/documents/import/commit` (`{"token"}`, same session) stores the files first, then checks every row again in
one transaction and imports it all or nothing: when a row's outcome or document differs from the preview (edited,
deleted, or its group renamed meanwhile), or a replaced document's values that the import overwrites (the columns in
the header and the file) were edited since the preview, it returns 409 and imports nothing. The old files of replaced documents are
removed afterwards.

Read-only People chips show only approved, non-expired submissions and omit the approval label;
management views retain all submission states.
An existing submission without a newly required expiry must be withdrawn and resubmitted; approved records
are not changed retroactively. Existing types keep expiry optional after upgrading.

Users submit claims for themselves; **admins can also upload evidence and submit claims for any user**.
Admins can bulk-import from People (**Manage ▾ → Import qualifications (ZIP)…**): a ZIP with `manifest.csv` and evidence files
(`person,type,valid_from,valid_until,file`, optionally `number`; an empty number is read from a PDF
certificate). Rows without a file are allowed for types that do not require evidence. Preview matches unique display names or aliases and
active types; Import and approve stores private evidence and sets the dates. Rows may repeat a person
and type for different periods: per person/type the newest period (latest `valid_until`, blank counting
as newest) becomes the approval if it is newer than the current one, which is then superseded. Other
periods are stored as `superseded` (archived) records. Periods already recorded as approved or superseded,
or repeated in the file, are skipped, so a ZIP can be re-imported. This import is the one deliberate
exception to never creating profiles from names: on confirmation, a person not matched by display name,
alias or username is added (username = name without spaces, no password, email or admin rights), labelled
**New user** in the preview. Unknown types are likewise created on confirmation, labelled **New type**:
active, external, no start date required, and evidence or Valid until required only if every row of that
type in the file has them; adjust them afterwards under Qualification types. A row with only a person
(no type, dates or file) adds that person without a qualification, e.g. project members without a
licence, so initial setup needs no manual Add user step. Ambiguous names block the import. The qualifications panel (`qualifications.js`) has a header row (title, count, a search
box from 7 records on, **Add qualification**) and one line per record (name opening the evidence, number and end date,
QR and status chips, actions; dates, file and review in the tooltip). Pending and non-expired approvals come first;
expired approvals, superseded, rejected and withdrawn records are under **Show N older** (`qual_older=1` in the URL).
API keys cannot import. The original ZIP is not stored as a shared attachment.
Attach one PDF, JPEG or PNG per submission, up to **20 MiB**. Required-evidence types cannot be submitted
or approved without a file. Optional-evidence types still require approval. An expiry date is required only when configured for the type;
blank means unknown/not applicable, not indefinitely valid. File extensions and file signatures are checked,
but this is not antivirus scanning. Downloads are forced attachments, not embedded previews.

All submissions start pending. Only admins can approve or reject them, and **admins may approve themselves**.
Users can withdraw their own pending submission and resubmit. A submitted claim and its evidence cannot be
edited in place, so approval always applies to the exact submission reviewed. Only one pending submission
and one current approval per user/type are allowed. A current approval remains active during renewal review;
approving a renewal supersedes it. Rejection or withdrawal leaves the current approval unchanged.
Approved claims can still be expired: approval is not a determination of current operational eligibility.

Older evidence stays available privately after renewal, rejection or withdrawal until an admin deletes it.
Admins can delete evidence alone (keeping the record) or delete the entire submission. Deleting evidence
does not revoke an existing approval; removing that approval requires deleting its record. Files are removed
from application storage, while minimal submission/review/deletion events are retained in the audit table
(latest 100 shown per person to admins only). Remove private records before deleting a user to avoid orphaned evidence.
Admins correct the validity dates of a submission in any status with `PUT /api/qualification-submissions/<id>`
(`valid_from` and/or `valid_until`, validated like a submission); the audit keeps the old and new dates.
API keys can read permitted records, but uploads, reviews, date corrections, deletions and type changes require a browser session.

Evidence is stored separately from shared attachments under `<LOG_DIR>/qualification-evidence/`, with opaque
filenames and lossless compression when worthwhile. It is served only through permission-checked endpoints,
with private/no-store caching and download hardening headers. Do not expose this folder through nginx or a
public file share. Serve the application over HTTPS and restrict filesystem/share/backup access to trusted
operators. Back up this folder along with the logs and the SQLite database; the `backup` command alone copies
the database and keys, **not evidence files**. Backups contain sensitive information, and deleting live records
does not erase retained backups or snapshots.

### Safety

Off unless `LOGDB_SAFETY=1` (`safety.py`, `static/safety.html`). The page is **Safety** in the menu and lives at
`/safety`: the Operations Manual links that path (see `AGENTS.md`), so it works without a query string and the switch
stays on in production; `app.py check-config` (also run by `install.sh`) warns when it, `LOGDB_DOCUMENTS` or
`LOGDB_TRAINING` is off. Since the page has no list of reports, the mail is the only way a report is seen:
`check-config` also warns when Safety is on without `LOGDB_SMTP_HOST` (reports reach nobody) or without
`LOGDB_MAIL_ADMIN_TO` (reports only reach administrators who kept Safety reports on). Every route needs a browser
session (API keys get 403).

The page shows the text above a prominent **Report incident** button (`reporting_text` in `safety_settings`, from `GET
/api/safety`, for admins **Edit text** in the card (or a click on the text; one dialog, [UI rule](#ui-rule)) and **⋯** → *Use the default text…* (a small confirmation; saves it empty), both with `PUT /api/admin/safety/settings`: who to contact,
where else to report, so organisation details live only in that setting; while it is empty,
`safety.DEFAULT_REPORTING_TEXT`, general wording, is returned, and saving an empty or the default text stores it empty
again). The danger/incident/accident definitions (following the Operations Manual) are in the form, behind a ? next to
Type. There is no list of reports. **Reports** (`safety_reports`, never deleted) have the fields of the paper registration form:
`kind` (`danger`, `incident`, `accident`), `pic`, `rpas`, `location`, `occurred_at` (required; `YYYY-MM-DDTHH:MM`
local time, or a date; not in the future, with a margin for time zones), `conditions`, `flight_phase`, `description`
(required) and an optional sketch (PDF, JPEG or PNG with matching content, at most 20 MiB, stored under
`<LOG_DIR>/safety`, mode 0700, gzip when it saves at least 5%). Only `kind`, `occurred_at` and `description` are
required; the mail leaves out empty fields (also the location in its subject). Single-line fields refuse control characters and line
breaks, so a value cannot add lines to a mail subject. `POST /api/safety/reports` (multipart) stores one with the
reporter (`reporter_id`, and `reporter_name` as it was then, kept when the account is deleted) and mails the whole
report to the administrators (`mail.notify_admins` with mail kind `safety`: separate from the other administrator
notices, immediate by default; to `LOGDB_MAIL_ADMIN_TO` when set), with a link to the sketch. With `leave_name_out`
("Leave my name out of the notification email") the mail says "a reporter who asked to leave their name out" (the
name is still stored) and the form no longer fills in Pilot in command with the reporter's name.
`GET /api/safety/reports/<id>/sketch` serves the sketch to the reporter and administrators, with an ASCII `filename`
and the full name in `filename*` (as attachments), so any file name works.

**Emergency flowchart** (who to call after an incident). An administrator uploads one self-contained HTML page (its
styles and scripts inline, UTF-8, `.html`, at most 2 MiB) with **⋯** → *Upload / Replace emergency flowchart…*
(`POST /api/admin/safety/emergency`, multipart `file`); *Remove emergency flowchart…* (`DELETE`) stops showing it. It
holds contact details from the Operations Manual, so it lives in the data folder (`<LOG_DIR>/safety/emergency`), never
in the repository. Every upload is a row in `safety_emergency` (file, name, size, when, by whom) and its file is kept;
the newest row is shown unless `removed_at` is set. While there is one, the page has **Emergency: who to call** next
to Report incident (`emergency` in `GET /api/safety`: the date for everyone, file name, size and uploader for
admins). It opens `/safety/emergency` (`static/safety_emergency.html`: the menu, ← Safety, the date and *Open alone ↗*)
with the file in a sandboxed `<iframe>`; `/safety/emergency/file` serves the file with a sandbox
`Content-Security-Policy` (`safety.EMERGENCY_CSP`: scripts and styles only inline, no network, no outside files, an
opaque origin without the logbook's session; `tel:` and `mailto:` links work after a click), also when opened on its
own. The logbook's night mode is passed as `?night=1`; the page follows it only if it reads that parameter (the
sandbox cannot read the logbook's saved setting), else its own Night button works. The frame starts loading as the
page is parsed (not after `/api/me` and `/api/safety`, which `theme.js` starts at once for `/safety/emergency`), is
shown once `/api/safety` says there is a flowchart, and fills the window below the bar until the page says how tall it
is. Two optional messages fit it into the logbook; the uploaded page has to send them itself, else it scrolls inside
the frame: it posts `{type: 'emergency-height', height}` (its content height in CSS pixels, again whenever that
changes, for example from a `ResizeObserver` on its body; the frame then gets exactly that height, so the logbook
page scrolls as one instead of a scroll area inside it), and receives
`{type: 'logbook-night', night}` when the logbook's Night button is used. Only messages from that frame are read and
only the height is used. A page can tell it is inside the logbook from `window.top !== window.self` (for example to
leave out its own header).

## Uploading with a script

Click your name at the top right, create an API key on the account page (it is shown once) and download
[upload_logs.py](upload_logs.py) there. It needs only Python 3. It searches a folder recursively for `.data`/`.log`
pairs and uploads the ones the server does not have yet (compared by SHA-256):

    python3 upload_logs.py ~/paparazzi/var/logs --server https://logs.example.org --key logdb_...
    python3 upload_logs.py ~/paparazzi/var/logs -n      # dry run
    python3 upload_logs.py ~/paparazzi/var/logs --bulk  # controlled initial import

`--server` and `--key` can also be set as `LOGDB_SERVER` and `LOGDB_API_KEY`; add `--insecure` for a self-signed
certificate. The key works on every `/api/...` URL as `Authorization: Bearer <key>` and acts as you.

The script first lists all matching pairs to establish an accurate total, without reading their contents.
It reports every 1,000 pairs found during discovery; listing a large network folder can still take time.
Hashing remains incremental: each batch is checked and missing pairs are uploaded before
hashing the next batch, so server-side processing can start without hashing the entire source tree.
Hashing batches are capped at 32 `.data` files and 256 MiB of `.data` contents by default.
Use `--hash-batch-files` and `--hash-batch-mib` to change these limits; a single larger file is
hashed alone. These are separate from the server backlog limits below. Identical content is
deduplicated across batches, including in dry runs. Files still need to be read for hashing and
again for upload; batching reduces hashing startup delay, not total source reads.
Progress shows completed/total, percentage, remaining, checked, skipped, accepted and failed counts.
Skipped counts include existing server content and repeated input content. Without `--bulk`, accepted
uploads count as complete for this script, not as successfully parsed. With `--bulk`, accepted uploads
remain outstanding until their processing result is checked; processed and failed outcomes are reported
separately. Failed pairs count as finished attempts; unattempted pairs remain outstanding after an abort.
Dry runs show planned uploads instead of accepted/processed counts. Unpaired `.data` files are reported
separately and excluded from the total. Directory listing errors stop the scan explicitly.

For initial imports, use `--bulk` on **each user's script**. Compression remains server-side.
Bulk runs automatically maintain a `.upload_logs-resume-<scope>.json` file next to the script,
separate for each source folder, server URL and account. Each hash and accepted upload receipt is
saved with an atomic replacement. After Ctrl+C, rerun the same command: unchanged pairs reuse their
hashes and pending upload IDs are checked before submission continues. Both files' sizes and
nanosecond modification times must match. Server hash checks still run, so resetting the server
does not silently skip previously completed files. Files modified without changing size/time need
`--no-resume` to force fresh hashing. Dry runs do not read or write checkpoints; `--no-resume`
also disables checkpoint use/writes. Keep the script directory writable and on local disk.
Run only one uploader for a given folder/server/account at a time. Checkpoints contain filenames,
hashes and upload IDs, not API keys. Moving the script without its checkpoint starts fresh.
On resume, terminal server failures (for example an empty log or a parser error) are reported
and counted as failures, but do not block the rest of the batch. Their completed receipts are
removed; hashes remain cached. A failed resumed pair is not resubmitted in the same run and the
script exits nonzero after processing the remaining files. Later runs may retry it; fix/remove
invalid source pairs or resolve server parsing errors as appropriate. Timeouts, network failures
and unexpected responses retain pending receipts and stop resume rather than claiming completion.

The script uploads sequentially, polls the global pending ingestion/parsing workload, and reserves
capacity atomically before transferring each pair. Concurrent bulk scripts share the same server budget:
`LOGDB_BULK_MAX_PENDING=8` pending jobs and `LOGDB_BULK_MAX_BYTES=17179869184` (16 GiB estimated).
Reservations count immediately and are exchanged for queued jobs atomically, so multiple users cannot
all claim the same free slot. A pair larger than the byte budget is allowed only when no work/reservations
remain. Abandoned reservations expire after one hour; an upload must start and finish within that window.
Retrying the same reservation after a lost response returns the existing receipt, not a second upload.

The script can use stricter local thresholds with `--max-pending` (default 8) and `--max-pending-gib`
(default 16). Estimates count staged pair bytes and queued/parsing `.data` sizes; ZIP jobs use archive sizes.
They do not reserve physical disk space or account for every temporary copy. Browser uploads and scripts
without `--bulk` remain unchanged and do not acquire reservations, although their queued work counts
towards the bulk budget. For the controlled initial import, have all importers use `--bulk`.

The defaults target a small server (2 vCPUs / 4 GiB RAM): one parser and one ingestion worker.
Uploads now stage under `<WEB_DATA>/incoming` on local disk, rather than writing uncompressed files
to `LOG_DIR`. Hashing and gzip compression run locally; only the finished compressed files are copied
to a temporary file on the share and atomically published. Legacy queued uploads under
`<LOG_DIR>/.incoming` are still processed after upgrading; no manual queue migration is needed.
Active parsing decompresses a pair under `<WEB_DATA>/tmp`. Keep `WEB_DATA` on persistent local storage,
not a RAM-backed `/tmp`. The local incoming directory is pending work, not disposable cache while
uploads are queued. Stop uploaders and let ingestion drain before moving an installation.

`LOGDB_LOCAL_MIN_FREE_BYTES` defaults to 2147483648 (2 GiB). New bulk reservations check local disk
space for twice the incoming pair size plus outstanding reservations and this headroom, returning
429 when space is insufficient. Upload staging and parser decompression check free space during
streaming; local compression checks space before starting. Storage failures are reported explicitly,
not accepted as successful uploads. Additional space is needed for concurrent work, multipart
temporary files, ZIP expansion and parser JSON output; these checks are not a disk quota or a hard
guarantee. Set queue/worker counts with that in mind; a backlog budget is not physical disk capacity.

When source logs live on a macOS-mounted Samba share exposed to a VM through VMware shared folders,
running `upload_logs.py` on macOS avoids the VMware layer for source hashing/reads. Point `--server`
to `http://<VM-IP>:8000` (or the configured HTTPS URL), and use the host's source folder path.
The standalone script needs only Python 3. Do not run old and new uploader instances on the same
source tree simultaneously just to switch versions; finish or stop the previous run first.
On a 20 GiB system disk with roughly 12 GiB free, start with a representative large log and monitor
disk/memory use before the full import. Increase worker counts only after adding or verifying capacity.

Bulk mode follows accepted receipts through storage and parsing, reports errors, and exits nonzero on
failure. An upload/capacity failure stops further submissions; already accepted receipts are still checked.
Use `--poll-seconds` (default 5) and `--max-wait-seconds` (default 3600 per wait) to tune waiting.
Without `--bulk`, completion means accepted uploads only; parsing continues on the server.
Dry runs do not upload, reserve capacity or wait.

Transient network failures and HTTP 408/429/500/502/503/504 retry with exponential backoff and honor
`Retry-After`. `--retries` defaults to 3 (0 disables request retries); bulk capacity waits continue up to
their wait limit. Upload retries reopen the original files instead of keeping them in memory.
Rerunning skips already stored pairs; existing parser-error logs are not automatically reparsed
(use the Logs page). A timed-out upload may still complete on the server.

## Script login and the Python/MATLAB clients

The [mavlab-logbook-client](../python/mavlab-logbook-client/README.md) package and the [MATLAB tools](../matlab/README.md) (`pprz_connect`,
`pprz_fetch`, `pprz_upload`) sign in with an OAuth 2.0 device authorization flow (RFC 8628), so no key has to be
copied from the account page:

1. `POST /api/cli-login` (no login; `{"client": "Python on laptop"}`) returns a secret `device_code`, a short
   `user_code` such as `BCDF-GHJK`, `verification_uri_complete` (`/cli-login?code=<user_code>`), `expires_in`
   (600 s) and `interval` (5 s). At most eight logins start per address in five minutes.
2. The client prints the link and the code and opens the browser. `/cli-login` needs a signed-in browser (login,
   also TU Delft sign-in, returns to it through `?next=`, which accepts only paths of this site). The page shows
   the code, the client's description and where and when the login started (the requesting address, stored in
   `cli_logins.address`, and its age), with a warning to allow only a login the user started just now. The user
   compares the code and presses **Allow** or **Deny**
   (`POST /api/cli-login/<user_code>` with JSON `{"approve": true}`, browser session only). Approval is never
   automatic on opening the link, so a link sent by someone else cannot obtain a key.
3. The client polls `POST /api/cli-login/token` with `device_code` and gets the RFC 8628 errors
   `authorization_pending`, `slow_down`, `access_denied` or `expired_token` until then. Once approved it receives,
   exactly once, a new API key of the approving user with the scopes `read logs:upload logs:edit`
   (`access_token`, `expires_at`).

The device code never appears in a URL, and only its SHA-256 is stored (`cli_logins`); keys are not stored at
all, as for other keys. A key from a script login is named `Login: <client>` and has `api_keys.expires_at`, seven
days after the login; the server refuses it afterwards and deletes it when keys are listed or created. A new login
of the same client replaces its earlier key, so they do not fill the 20-key limit. Such keys appear on the
account page with their end date and can be revoked there. `POST /api/keys/revoke-all` (also with an API key that
has a scope beyond `read`, as `revoke_all` uses it) revokes the user's script-login keys; `{"include_manual": true}` (all of the
user's keys) needs a browser session, so a leaked script key cannot delete long-lived keys. It returns the number
revoked. Both clients refuse `http://` servers except `localhost`/`127.0.0.1`, so keys never travel unencrypted.

Both clients fetch flights by number with the same `scope` (`selected`, `all`, `log` or `flight`): MATLAB's
`pprz_fetch` and Python's `fetch` download exports through `POST /api/flights/matlab` (one per log) or
`POST /api/flights/<id>/matlab` (`flight`), into the same cache folder. Python's `fetch_raw` downloads the original
log files through `GET /api/flights/<id>/files` (the flight's log, the SHA-256 of its data file and the
`/api/logs/<log>/download/<kind>` URLs of its files). Uploads go through `POST /api/upload` as for
`upload_logs.py`. `GET /api/ingest/<id>` lists the parsed log's `flight_ids`.
Both clients keep the login and the cache in the same per-user folders (see the
[package README](../python/mavlab-logbook-client/README.md#where-files-are-kept)); `python/mavlab-logbook-client/mavlab_logbook_client/settings.py` and
`matlab/+pprz/config.m` must keep the same rules. The package is tested and released to PyPI by
`.github/workflows/client.yml`: when master has a `mavlab_logbook_client.__version__` that is not on PyPI yet, the
publish job waits for a maintainer to approve the `pypi` environment, publishes that version by trusted publishing
(PyPI publisher: this repository, `client.yml`, environment `pypi`) and then creates the tag `client-v<version>`
(a tag ruleset keeps these tags from being moved or deleted; a tag pushed by hand neither publishes nor skips a release). Merging without a version
bump publishes nothing; a rejected or expired run is retried by re-running it. Client tags are separate from Web UI
and parser tags.
Run its tests with `python -m unittest discover -s python/mavlab-logbook-client/tests`.

## How it works

**Uploads.** A `.data` file is paired with the `.log` of the same name in the same folder.
Single-file PX4 `.ulg`, ArduPilot `.bin`, MAVLink `.tlog` and Betaflight/INDIFlight `.bfl`/`.bbl` logs are stored
on their own. An ArduPilot text `.log` is accepted without a `.data` companion.
The upload returns as soon as the files are on disk; hashing, duplicate checks, unzipping and parsing run in the
background. The SHA-256 of the `.data` file identifies a log, so a known log is not stored twice (a missing `.log` is
added later). Files are kept in `<LOG_DIR>/<sha[:2]>/<sha>/` with the flight paths in `paths-<id>.json.gz`; uploads
are staged in `<WEB_DATA>/incoming`. Each log records its parser version; **Re-parse outdated** re-runs the current
parser on older and failed logs.
While a log waits for or is in a re-parse (`queued`/`parsing`), the logbook read model (and so Logbook, Statistics,
detail pages and flight pages) keeps its previous flights; the new ones replace them in one transaction. A log
whose parse fails (`error`) is not shown. Automatic matching, date suggestions, the bulk MATLAB export and the
Logs counters still use only finished logs. The Logbook shows a notice while logs are waiting or being parsed
(from `/api/status`, checked every 10 s) and reloads when the queue is empty.

The Logs **Filename** field adds `q`, a case-insensitive literal filename substring search across
all accessible logs (and ingestion receipts in the failures view). It combines with status
and project filters; changing the search returns to the first page and leaves single-log mode.

The Logs **Status** dropdown filters `/api/logs` before counting and pagination:
`status=all` (default), `errors` (parser errors), `unfinished` (queued/parsing/error), or
`failures` (parser errors plus error/rejected ingestion receipts). The combined failures view
sorts by upload timestamp, with stable ID/type tie-breaks; ordinary log views retain descending
log-ID order. Receipts use `row_type=ingest`, distinct from `row_type=log`, and expose no storage
paths or stored-log controls. Only the uploader or an admin can see a receipt, regardless of
project membership. A receipt is omitted when its linked failed log is already visible in the
results. Receipts have no project, so an explicit numeric `project` filter excludes them;
`project=none` includes eligible receipts. The recent ingestion counters remain limited to the
last hour, while the failure filter includes all retained receipts. Failures rejected before
a receipt is created remain upload-response errors, not persisted results.

### Same file from another person: co-uploaders

Logs are deduplicated by the SHA-256 of the `.data` file, so a file is stored once. When someone other than
the first uploader uploads the same file (also under another name, also an admin), they are added to
`log_uploaders` as **co-uploader** instead of being refused: having the file is evidence enough that they may
see it. The ingest receipt is `duplicate` with `CO_UPLOADED` as message (plus ", missing .log added" when their
upload supplies a `.log` the log lacked).

- **Access.** Every uploader, first or co-, sees that log and its flights, also when the log or a flight is in a
  project they are not a member of (`uploader_clause` in `own_clause` / `flight_own_clause`). Before, the first
  uploader lost sight of a log once it was assigned to another project. Access covers that one log only, not the
  project.
- **Editing** follows the existing rules: project roles; for flights without a project, any uploader
  (`is_uploader`). Deleting a log stays with the first uploader (when it has no project), full project members
  and admins, so a co-uploader cannot remove the file for everyone.
- **Shown** in the log details ("Uploaded … by A, also uploaded by B · date") and on B's person page.
- **Logs page and upload dialog.** `/api/status` lists the logs the user became co-uploader of in the last hour
  (`co_uploaded`); the page shows them with a link that opens that one log (`/api/logs?log=<id>`, also used by
  every `/uploads#log<id>` link, with *Show all logs* to return).
- **Scripts.** `/api/logs/check` reports `mine` per known hash. `upload_logs.py` skips only files the user
  already uploaded; others are uploaded once more, which proves possession (a hash alone does not) and adds the
  co-uploader. The file is still stored once.
  For logs the user may see (the rule of `GET /api/logs/<id>`), each entry also has the log's `id`, `name` and
  `status`, and its `flights` (`id`, `idx`, `takeoff_utc`, `duration_s`, `hidden`, `comment`; in takeoff order,
  hidden flights included; `[]` until `status` is `done`). A script that knows a file only by its hash uses them to
  attach files to the log or comment on its flights. Other logs keep only `has_log` and `mine`.

**Logbook.** The parser tells onboard SD logs from ground station logs. The logbook (`/logbook`) has one row per
flight and merges the SD and GCS recordings of the same flight: flight data from the SD log, PIC/PAC/GCS operator and
goal from whichever log has them. Named takeoff areas are listed on the Locations page; everyone sees the ones their
flights used, and admins add, change or delete them there ([UI rule](#ui-rule): Add location, Edit location and ⋯ in
the selected location's panel; an area can be marked indoors; indoor flights are hidden by default). Logs without a valid date (missing or 1980 clock) are flagged so the
real day can be entered.

`GET /api/places` groups endpoint dots at three decimal places (about 100 m).
Each dot has a total `count` and an `unnamed_count`, determined from the endpoints'
resolved place IDs before rounding, not from whether the rounded dot lies inside a shape.
<a id="locations-page"></a>**Locations page** ([UI rule](#ui-rule)). The title row has the count, search,
country and, for admins, **Only unnamed dots**, **Add location** and **Manage ▾** (Import CSV…, CSV template). The
list has Name, Flights and Time columns (no flights: "—"); the headers sort (`sort=name|flights|time-asc|desc`). It is
as tall as the map, with the pager at its bottom: the rows per page are what fits (measured; 12 on a phone). The map
first fits the most-flown places that together hold 90% of the flights (all places and dots when nothing was flown),
so a few far-away places do not zoom it out to the whole world. The
selected location (`place=<id>` in the URL) opens as a card on the map, top left (under the map on a phone; hidden
while a boundary is drawn or changed): Country, Indoors, Operator, Category,
Boundary and flights; admins get **Edit location** (one dialog; a click on a value opens it there; Save sends only what
changed with `PUT /api/admin/places/<id>`) and **⋯** → *Change boundary on the map…* (the shape editor on the map,
`PUT` with only `shape`) and *Delete location…* (a small confirmation). **Add location** shows a bar on the map to
pick polygon, rectangle or circle and draws one; after drawing, the same dialog names it (`POST /api/admin/places`) and
the new location is selected. Leaflet's own edit/delete toolbar is not used. While drawing or changing a boundary, all
boundaries are shown.
The Locations unnamed-dot review is available only to admins. It filters on `unnamed_count`, orders by that count, and
keeps named boundaries visible as context. It uses the same accessible flights and
non-flight toggle as the normal map. Saving a place refreshes the review without changing
flight records or requiring reparse.

The Locations map groups named-place centres into 64-pixel projected grid cells below
zoom 13, using built-in Leaflet markers (no additional map dependency). Counts represent
places, not flights; clicking a group zooms in up to three levels. At zoom 13 or above,
or while an admin draws or changes a boundary, the actual boundaries replace the overview markers. The selected
place always retains its boundary and name label; other names appear on hover.
Selection and the case-insensitive table name search are browser-only and do not merge
places, change access rules or change the smallest-containing-shape resolution rule.

Replacing a named boundary uses the same `PUT /api/admin/places/<id>` shape update
as resizing it; it never deletes/recreates the place. Circle, polygon and rectangle
redraws are previews until saved (rectangles are stored as polygons). The place ID,
name, indoor flag and historical-record references remain intact. Cancel restores the original
boundary; a failed save retains the preview for retry.

The browser requests `GET /api/logbook?compact=1` for its overview. This opt-in projection
keeps all rows and the fields needed for filtering, totals, grouping, CSV export and permitted
crew/project/date edits. It omits full telemetry events, assessments and source/uploader metadata;
source IDs/types and resolved crew IDs/display names/edit provenance remain available.
Without `compact=1`, the existing full response schema is unchanged. Both representations use
the same access checks, hidden/unlinked rules, source merging and paper-record read model.
Comments remain searchable and exportable, but are read-only here; edit them on the flight page.

Paths are fetched from the existing permission-checked `/api/flights/<id>/path` endpoint only
when a flight row is expanded, not once per overview row. Successful paths are reused during
the current overview load. Reloading the overview (including after date/project/crew edits)
clears that cache. Failed requests show an explicit error and a retry button instead of being
cached as an empty path; retrying leaves the rest of the row unchanged. Further telemetry,
assessments and simulation evidence remain on the flight page.

Logbook JSON responses negotiate gzip using `Accept-Encoding` (including quality values);
responses under 1 KiB or with no size benefit stay uncompressed. This works with direct Flask
or gunicorn access and behind nginx/Caddy. Proxies must forward `Accept-Encoding` and pass
already encoded responses through rather than recompressing them. No proxy compression
configuration is required for this endpoint. Responses carry `Vary: Accept-Encoding` and
`Cache-Control: private, no-store`; this is transport compression, not shared response caching.
To repeat the synthetic benchmark without production data or workers, run from `webui/`:
`python -m unittest tests.test_logbook_overview.LogbookOverviewTests.test_synthetic_payload_before_after_measurement -q`.
It compares full JSON, compact JSON and compact gzip on the same 500 merged flights plus
a paper record, reporting response sizes and median in-process request time (three requests).
Actual gains depend on record content, server and connection; database/read-model work is
still performed for every request.

Source merging sorts recordings by numeric takeoff time and keeps an active window of recordings
whose landing could overlap the next takeoff (including the 60-second slack). Aircraft-name/GPS
comparisons only run inside that window. Every compatible pair is scored (actual interval overlap
first, then the smallest sum of takeoff and landing time differences) and pairs are joined best
first, so a recording is not taken by an earlier, worse partner (e.g. an SD flight by a short GCS
flight 25 s earlier in the same GCS log while the matching GCS flight takes off 0.4 s after it).
Ties keep the earlier recording. A group never holds two flights of one log or two fleet aircraft.
Two recordings of the **same type** (SD+SD, GCS+GCS, PX4+PX4) join only when they cover the same
time, at least half of the shorter one: back-to-back logs (restarts, battery swaps, consecutive PX4
files) stay separate flights instead of chaining through the slack. The longest onboard recording
normally leads a merged entry (duration, verdict, track). For SD+GCS groups only, GCS becomes the
default when its valid timed GPS samples cover at least 90% of the SD flight interval, its sample
density is at least four times higher, its median gap improves by at least one second, and its
largest gap is no worse. If SD has fewer than two usable timed positions, a GCS track with at least
three samples, 90% coverage and no gap exceeding 10% of the SD duration can win directly.
Unknown quality, ties, modest improvements and short dense fragments leave SD preferred.
This changes source selection, not grouping, simulation separation, identity or crew precedence.
The selected source supplies the default flight ID/link, track, replay and distance; all originals
remain available under **Log sources and upload details**, with an explanation when GCS wins.
Explicitly opening another source still shows that recording's path and measurements; its summary
remains the merged default-source summary.

Track summaries are stored during parsing; valid finite GPS coordinates and strictly increasing,
finite timestamps within the flight are used. Duplicate/backward times and untimed or invalid
positions do not increase density. On upgrade, the existing startup GPS backfill also computes
missing track summaries once (including legacy path blobs), committing per log so interruptions
can resume. This can delay the first startup while existing path files are read; no reparse is
needed. `python app.py backfill-gps-positions` can run it before starting the service.
Subsequent startups and overview requests do not read paths to choose a source.

Statistics sends only accessible contributing source-log IDs and stored original `size_bytes`,
not filesystem sizes. The browser applies the same shared UTC date-range helper as Logbook and
deduplicates source IDs after filtering. GB uses decimal units (1 GB = 1,000,000,000 bytes).
An entire source log counts when any of its flights qualifies; this is not a byte estimate for
just that flight interval. Unknown sizes are explicitly reported, and paper records contribute
no log bytes. Period/filter changes require no file reads or additional Statistics API requests.

**Shared aircraft names.** On the Aircraft page, admins mark a generic log name as ambiguous with ⋯ →
*Mark an alias as shared…* of an aircraft or **Manage ▾ → Mark a name as shared…**, and link its boards and
recordings under **Shared names**. For example, an SD airframe name
such as `RotatingWing25Kg` can be shared by several physical aircraft even though GCS
configuration names are specific. Ambiguity is explicit, case-insensitive and applies to that
exact trimmed logged name; it is not guessed from spelling or automatically enabled for
particular aircraft.
A recording under a shared name that is not linked to an aircraft takes the aircraft of the other recording of
the same flight (e.g. its GCS log) in the logbook, and its project too: the combined entry shows the project of
any recording that has one, and the stored project of the unlinked recording is filled from its best-overlapping
linked recording of another log type (so its log, and its files, follow the project). A split decision or a
project set by hand (including "No project") is kept. Existing recordings are filled at the next start.

Removing an aircraft alias recomputes only the logged names that used that alias
(including embedded board-ID matches), without relearning aliases during the operation.
The button shows progress and reports request failures. Removing an alias does not prohibit
other identity evidence: fleet names/serials, remaining aliases, unique folder matches and
explicit per-log assignments can still keep an aircraft linked. A later upload may learn its
identity again through these rules. Use **Mark ambiguous** for a generic name that must not be
automatically assigned by name or folder.

**Upload-path folder matching.** When the logged name does not identify a fleet aircraft, the
upload path is a hint. Folders are tried from the file's own folder upward, skipping generic
dump names (`logs`, `uploads`, …) and `YYYY-MM-DD` date folders. The first (deepest) folder
that uniquely overlaps one fleet name wins, so `Hex 1/2026-10-13/LOG00030.BFL`
links to Hex 1. An ambiguous token such as `Quadrotor` is skipped and a higher unique
folder may still match. Select folder includes the chosen folder name in the stored path (drop
and zip already did). A logged name that is only the log's file name is not stored as an alias and not listed
under *Names in logs*: the parser uses the file name when a log has no craft name (Betaflight/INDIFlight without
one, PX4 without an airframe), so `LOG00030`, `LOG00159_manualShakedown` or `flight_a` (from `flight_a.bbl`) say
nothing about the aircraft, and sequential Blackbox names on another aircraft are not stolen
(`is_file_name`). Aliases of that kind learned before are removed at start (`remove_file_name_aliases`; only an
alias equal to a logged name of that aircraft's own logs, so hand-made aliases stay). Existing unlinked recordings are retried at
each start; no reparse is needed.

Marking a name removes its global alias and recomputes the affected logs' automatic fleet
links. Neither the name nor upload-folder hints may assign that shared name to a fleet aircraft.
Other aliases, raw logs, existing flight project assignments and paper records are unchanged.
Existing global alias assignments were automatic; they were not per-log confirmations.

An unresolved SD source with an ambiguous name is considered separately after normal source
grouping. It may join exactly one candidate group containing a linked GCS source when their
intervals overlap within the existing 60-second slack, autopilot IDs agree and both takeoff
positions are valid and within 2 km. Candidate groups are rejected by interval and log ID before
checking their clocks, avoiding repeated timestamp parsing for unrelated recordings.
If any qualifying groups actually overlap, slack-only
candidates are discarded. Exactly one remaining group is required: multiple genuine overlaps
remain unresolved even when one has closer timestamps, as do multiple slack-only candidates.
Missing evidence leaves it unresolved; matching generic names alone is insufficient. Specifically linked conflicting
aircraft still cannot merge. Identity is inherited only in the merged read model, with an
explanation on the flight page; no inferred global alias or permanent SD assignment is created.
With `HIDE_UNLINKED_AIRCRAFT=1`, uniquely resolved merged flights remain visible and standalone
unresolved recordings remain hidden from overviews.

The shared-name panel also lists recordings, with an explicit per-log aircraft selector.
Those assignments are stored by log and autopilot ID, survive reparsing, and take precedence
over aliases, ambiguity and folder matching. Clearing one restores automatic/unresolved matching.
Removing ambiguity resumes normal name/folder matching but does not restore a deleted global alias: add it explicitly if the name
really identifies one aircraft. These management actions require an admin browser session,
not an API key. No raw-log reparse is needed to mark names or update assignments.

**Paper flight records.** In the Logbook, choose **Manage ▾ → Import CSV…** (admins) or **Add paper record…**. Admins and members
with **full** access to the selected project can create, edit and delete these records; other project
members can read them. Select an existing aircraft, PIC and project, then enter the date, total flight
time in minutes and flight count. An individual flight has count 1 and an optional UTC takeoff time.
A daily summary has a count and a combined duration, with no fabricated individual takeoff times; its
optional takeoff time is the **first takeoff of the session** (used for sorting and for suggesting logs).
Location, PAC, GCS operator, goal, notes and book/page reference are optional.

For admins only, the same page imports UTF-8 comma-separated CSV (up to 1000 records / 1 MB) and offers a template.
Non-admins with full project access can enter records manually, but cannot use the CSV import API.
Required headers: `aircraft,pic,project,date,duration_minutes,flight_count`. Optional headers:
`kind,takeoff_time,location,pac,gcs,goal,comment,reference`. Use existing aircraft/project/location names
(case-insensitive) and unique crew display names or explicit aliases, just like log crew matching.
Usernames match only if added as aliases. Unknown or ambiguous crew names reject the entire import
with a row error; add an alias or use a unique display name before retrying.
No accounts, aircraft or places are created by import. Dates are
`YYYY-MM-DD`; `takeoff_time`, when known, is `HH:MM[:SS]` UTC. `kind` is `flight` or `daily`; if blank,
it is inferred from the count. A count of 1 can also be explicitly marked `daily`. All rows are validated
before saving; an invalid row rejects the entire file with a line error.

Importing a corrected CSV again updates records instead of adding them twice. Each row is matched, in order, to:
an identical record (left **unchanged**); the record with the same `reference`, e.g. a unique flight number
such as `F00001` (a reference shared by several records, such as a book page, only matches the one with the same
date and takeoff time); or an individual flight with the same aircraft, date and takeoff time and no other
reference. A matched record is **updated** with all values of the row; a row without reference keeps the
record's reference. Other rows **create** a record. Daily summaries and flights without takeoff time are only
recognised by reference or when identical, because several can share an aircraft and day. A row that matches
several records, two lines for the same record, and two identical lines reject the whole file (409).
Hidden records are matched and stay hidden. The page shows this per row before importing
(`preview=1` form field: the same validation, nothing is written). Manual entry still rejects exact duplicates.

Successful CSV imports store the original file once as an attachment linked to every created or updated paper
record (up to the CSV limit of 1000 records, not the interactive attachment selection limit of 100); an import
that changes nothing stores no attachment. The response lists `ids` (created), `updated_ids` and
`unchanged_ids`; it is 201 when records were created, otherwise 200.
Any reader of a linked record can download the entire CSV, intentionally including other projects' rows.
Validation, record writes and attachment registration are atomic; storage failures roll back records,
and failed imports remove staged/source files. Normal attachment permissions and last-link cleanup apply.

Paper records contribute their flight counts and total duration to the logbook, statistics and entity
overviews, but have no telemetry, synthetic log files, paths, distance or software version. Unknown times
remain date-only. Referenced aircraft, people, projects and locations cannot be deleted until their paper records
are reassigned or removed.

### Paper records linked to logs

Possible-duplicate matching prepares visible telemetry takeoff timestamps once per request,
grouped by aircraft. Paper records reuse these candidates with the existing inclusive time
windows and manual exclusions; no persistent cache or matching-rule change is involved.

A paper record and the logged flight(s) of the same flight can be **linked** (`links.py`, table
`flight_links`). Nothing is merged or changed: a link row says "same flight" (`same=1`) or "not the same
flight" (`same=0`), with `origin` `auto` or `manual`, who and when.

- **Stable keys.** A logged flight is addressed by `log_id` + `ac_id` (-1 when none) + parser flight number
  `idx`, which survive re-parsing (flight ids are kept too, see [Install on a server](#install-on-a-server), but links do not rely on them). The takeoff time at decision time is stored too;
  if a re-parse moves that flight by more than 60 s the link is ignored as stale rather than attached to
  another flight. Deleting the record or the log deletes its links.
- **Automatic links** are made after a log is parsed, after paper records are created, edited, imported or
  hidden/unhidden, and at startup. A record is linked when its aircraft has exactly one logged flight
  within 5 min of the paper takeoff (several recordings of that one flight, such as SD and GCS, count as
  one when they cover the same time), and no other paper record of that aircraft is within 5 min of it.
  Only real flights take part: not ground runs, simulations, hidden flights, logs without a fleet
  aircraft or with a corrected date. Records or flights with any stored decision are skipped, so a later
  upload, re-parse or import never replaces a link or undoes an unlink. Editing a paper record removes
  its automatic links and matches it again; manual decisions stay.
- **Manual decisions:** `POST /api/links {record_id, flight_ids, same}` from the logbook (Same flight /
  Not the same on a paper record's possible duplicates, Unlink on a linked entry for who may change its crew). Needs
  full access to the record's project and edit rights on every flight (admins always).
- **Counting.** `logbook_rows` folds linked records into the logged entry (`links.combine`), for the
  logbook, statistics, detail pages and places alike. The entry counts as its logs: flight count and
  logged time, because logs are more complete and precise than paper written afterwards. A record linked to
  several logged entries (one paper line for a flight with a battery swap) makes one entry counting all of
  them. The record adds `reference` (searchable in the logbook) and its comment when the log has none,
  and is listed in `linked_records`. Only rows the user can see are combined.
- **Conflicts.** Where a linked record names another aircraft, project, place or PIC/PAC/GCS person than
  the log, the log value is shown and `conflicts` lists both values for a person to resolve.
- **Possible duplicates.** An unlinked record lists logged flights of its aircraft within 15 min
  (`possible_duplicates`), except pairs marked not the same. For a daily summary with a first takeoff the
  window runs from 15 min before it until 15 min after its total duration (`links.paper_window`), so the
  logs of the whole session are suggested. Daily summaries are never linked automatically: one row stands
  for several flights; link its logs by hand (the entry then counts as those logs).
- **Import preview.** *Check CSV* (`preview=1`) runs the import and the automatic linking in one transaction,
  records per row what happened (`logs.linked`: the logged flights it will be linked to, else `logs.possible`:
  the possible duplicates, `links.record_matches`) and rolls everything back. The preview therefore shows exactly
  what the import does, from the same code. A corrected re-import keeps the record id: its automatic links are
  re-matched, and manual links and "not the same" decisions stay.

### Review page

`/review` (Logbook → **Manage ▾** → *Review*) walks through the paper ↔ log cases a person has to decide, one at a time,
from `GET /api/review` (same read model and visibility as the logbook):

- **possible duplicate**: an unlinked paper record with logs of its aircraft nearby (`possible_duplicates`);
- **other aircraft**: no log of the record's aircraft nearby, but another aircraft's unlinked log in the same
  window (`links.paper_window`), unless already decided;
- **conflict**: a linked entry whose paper record differs and nobody accepted the log value yet.

Other-aircraft matching builds a lazy, per-request time index of visible unlinked telemetry entries,
parsing each takeoff once and using binary searches for each paper window. Window endpoints remain
inclusive, candidates retain logbook order, and stored decisions still exclude every source of a
merged entry. The index is not cached across requests; no matching or permission rules change.

Each case shows the paper record next to the candidate logs (takeoff/landing, offset to the paper takeoff,
logged duration, paper/logged duration ratio, verdict, aircraft, crew, place, log types). Actions use the
normal decisions: tick logs and **Same flight** (several for one paper line), **Not the same flight**, or
**Skip for now** (browser only). Conflicts: **Keep log value** stores the acknowledgement in
`conflict_reviews` (record, field and both values as shown, who and when; other values reopen it and the
logbook still lists it with who accepted it), **Use paper value** copies the paper's project or PIC/PAC/GCS
onto the flights with the existing flight edit endpoints, or **Edit paper record** / **Unlink**.
*Recent decisions* lists the last manual decisions with **Undo** (`DELETE /api/links`, which removes the
decision so automatic matching applies again). Keys: `j`/`k`, `1`-`9`, `l`, `n`, `s`. Buttons only appear
with edit rights on the record and the logs; the endpoints check them again.

### Manual join and split of logged recordings

When the automatic SD/GCS merge is wrong, a person corrects it; the decision is stored in `source_links`
(two recordings by `log_id` + `ac_id` + `idx`, stored in sorted order, with their takeoff times for the
same stale check as `flight_links`) and passed to `merge_sources(rows, decisions)` by `logbook_rows`.

- **Join** (`same=1`): the recordings form one entry whatever their timing or names: joins are applied
  before the scored pairs. Two flights of one log, or recordings of two different fleet aircraft, cannot
  be joined (400).
- **Split** (`same=0`): the two recordings never end up in one entry, also not through a third one.
- `POST /api/sources/links {flight_ids, same}`: with `same=true` all listed flights are one flight; with
  `same=false` the first is not the same flight as each of the others. The latest decision on a pair
  wins. Needs edit rights on every flight.
- In the logbook, **Same flight** in the selection bar joins the ticked logged entries; with one
  ticked paper record it also links that record. An expanded merged entry lists its recordings with
  **Not the same flight**.

Admins can select paper and telemetry records together in the Logbook and apply (**Change…**) PIC, PAC,
GCS and project changes in one transaction. Paper records still require a PIC and project:
leave these fields on **Keep** or select a person/project rather than clearing them. Duplicate
paper-record checks still apply, including hidden records; an invalid selection rolls back the whole change.
Paper crew/project cells also support individual inline edits for admins.

**Hide selected** is reversible removal from the logbook and its statistics/entity totals, not file deletion.
It hides all selected telemetry sources (including both SD/GCS sources of a merged row) or entire paper
records/daily summaries. Unselected flights in the same uploaded log are unaffected. Admins choose
**Manage ▾ → Hidden records only**, tick entries and choose **Restore** in the selection bar.
Source logs, CSV attachments and record data stay on disk; uploaded-log views retain their source summaries.
The hidden state is preserved across re-parsing using the same aircraft/flight-index matching as crew edits.
Permanent deletion from existing record/upload detail pages remains a separate operation.

### Operator per flight

Not every flight is flown under our own UAS operator registration: some are part of a partner's, a test centre's
or a foreign authority's operation. `operators.py` resolves the operator of every logbook entry, in this order:

1. set on the flight (`flights.operator_id`, kept across a re-parse like crew edits) or on the paper record
   (`flight_records.operator_id`); a paper record linked to logged flights also sets it for that entry;
2. the project (`projects.operator_id`);
3. the takeoff place (`places.operator_id`), for a site that runs its own operation;
4. the operator the PIC flies for (`users.operator_id`, set for external crew);
5. our own operator (`operators.own`, at most one); with no own operator set up, every flight counts as own.

Only step 1 is stored per flight; the rest is resolved when the logbook is read. Existing flights therefore need
no migration: mark our own operator, then set the operator of each external person (Projects → Operators →
External crew, or **Edit account** of the person on People), and their flights follow.

Logbook rows carry `operator_id`, `operator_name`, `operator_registration`, `operator_own` and
`operator_source` (`flight`, `project`, `place`, `pic` or `default`). `operator_check` marks an entry on the
default whose PIC is external crew without an operator. The shared flight filters (`operator=all|own|other`) apply it to the Logbook,
Statistics, Locations (`/api/places?operator=`) and overview pages.

- `GET /api/operators`: the list (any signed-in user). `POST /api/operators`: anyone signed in adds the operator
  of a new partner (not our own); the Logbook bulk bar, the paper-record form and the People page offer
  **New operator…** for it. Admins: `POST /api/admin/operators`, `PUT`/`DELETE /api/admin/operators/<id>` (name,
  registration, country (an ISO code from `countries.py`, as for locations; older free text was converted to the
  code it names, or cleared), own, note; a `PUT` keeps the fields it leaves out). An operator still used by flights,
  paper records, projects, places or people cannot be deleted.
- `PUT /api/admin/users/<id>/operator` sets the operator a person flies for; `POST /api/admin/people/external`
  takes an optional `operator_id`.
- `PUT /api/logbook/records` with `operator_id` sets it on the selected entries; `null` returns them to the
  default. `PUT /api/admin/projects/<id>` and `PUT /api/admin/places/<id>` take `operator_id` too.
- Paper-record CSV: optional `operator` column, matched by name or registration number; blank means the
  default. A CSV without that column leaves the operator of existing records as it is. The operator is not
  part of a record's fingerprint, so changing it updates the record instead of creating another one.

### Operation category per flight

`categories.py` records the rules each flight was flown under: `national` (ROC / ROC-light, before Regulation
(EU) 2019/947), `open`, `specific`, `luc` or `indoor`. The `authorisations` table lists the ROCs, operational
authorisations and LUCs (name, category, holder operator or NULL for our own, `valid_from`/`valid_until`
inclusive, link, note); choosing an authorisation sets its category. The category of a logbook entry is, in this
order:

1. set on the flight (`flights.category`/`authorisation_id`, kept across a re-parse) or on the paper record; a
   paper record linked to logged flights also sets it for that entry;
2. `indoor` for an indoor flight;
3. the project (`projects.category`/`authorisation_id`);
4. the takeoff place (`places.category`/`authorisation_id`), e.g. a site that needs an authorisation;
5. the date: before 31 Dec 2020 every flight was under national rules. For our own operations (the resolved
   operator is ours) with authorisations in the catalog, a national authorisation valid that day (the newest if
   several) gives `national` with it, and a day without any valid authorisation of ours gives `open`; on a day with
   a valid EU authorisation the flight could be OPEN or SPECIFIC, so it stays unknown.

Only step 1 is stored per flight. Logbook rows carry `category`, `category_label`, `category_source` (`flight`,
`indoor`, `project`, `place`, `date` or null), `authorisation_id`, `authorisation_name` and `category_check`
(own entries: SPECIFIC without an authorisation, or a date outside the authorisation's validity). The shared
flight filters (`category=all|national|open|specific|luc|indoor|unknown|check`) apply it like the operator filter.

- `GET /api/authorisations`: the catalog and category labels. Admins: `POST /api/admin/authorisations`,
  `PUT`/`DELETE /api/admin/authorisations/<id>`; a `PUT` keeps the fields it leaves out and moves the category of
  everything that names it along; one still in use cannot be deleted.
- An authorisation with `exemption: true` (such as a one-off BVLOS or Remote-ID exemption) only applies where it is
  set on a flight, paper record, project or location. The date rule ignores it, both for the national default and
  for deciding that a day had no valid authorisation of ours.
- `PUT /api/logbook/records`, `PUT /api/admin/projects/<id>`, `POST`/`PUT /api/admin/places` and the paper-record
  API take `category` and/or `authorisation_id`; both empty return to the default.
- An administrator's API key may call `PUT /api/logbook/records` when the body only sets `operator_id`,
  `category` and/or `authorisation_id`, for scripted bulk annotation. Crew, project and hide/restore still need a
  browser session.
- Paper-record CSV: optional `category` (key or label) and `authorisation` (name) columns. Like the operator, they
  are not part of a record's fingerprint, and a CSV without them leaves existing records as they are.

<a id="ui-rule"></a>**UI rule: changes without an Edit mode.** Every page follows the same few patterns:
1. The read view is the same for everyone; there is no global Edit mode.
2. One item, one **Edit <item>** button (Edit flight, Edit aircraft, Edit account / profile, Edit project, …) in that item's
   header, shown only to who may change something of it. It opens one dialog with every field the user may change
   (fields they may not change are not shown), a read-only context line on top, a compact grid and a blue
   (`button.primary`) **Save** with Cancel. Save sends only what changed, through the existing endpoints, shows an
   error at each field that failed and stays open then. Clicking an editable value (or its label) opens the same
   dialog at that field; links in values stay links.
3. Rare or destructive actions of the item (re-parse, delete, discard, merge, reset, …) are in one small **⋯** next
   to the Edit button, shown only when the user may do one of them, each with a small confirmation dialog.
4. Lists and tables: tick rows for the **selection bar** (as on the Logbook) for changes to one or more rows; no
   per-row menus or inline dropdowns.
5. Page-level tools: the primary **Add …** button and **Manage ▾** (imports, templates, review queues, rarely used
   settings) at the end of the toolbar, only for who can use them. Admin-only sections and tabs are always shown to
   admins and never to others.
6. Pages with a list (Aircraft, People, Projects, Locations) open the selected item beside the list, with its tabs
   and Edit button; the layout does not switch.

Permissions and APIs stay as they are; the server checks every change. There is no global Edit switch any more (the
header's former Edit button and its `logdbEdit` browser setting are gone; `theme.js` clears the old setting). Admins see
the names in the logs that are not linked yet on the Unlinked tabs of People and Aircraft, always.

The pages that follow it: the [Logbook](#logbook-changes), the [flight page](#flight-page-changes), the
[Aircraft page](#aircraft-page), the [People page](#people-page), the [Projects page](#projects-page), the
[Locations page](#locations-page), [Training](#training-questionnaires), the [Logs page](#logs-page), the
[Safety page](#safety), the [Documents page](#documents-page), the [Agenda](#booking-agenda) and the welcome
message and notices editor (`/welcome`).

<a id="logbook-changes"></a>**Logbook changes (no Edit mode).** The Logbook looks the same for everyone and offers each change
only where the user may make it, from the flags the logbook API already sends per entry (`can_edit_project`,
`can_edit_crew`, `can_edit_date`, `can_edit_record`) and the user's project roles; the server checks every change
again as before.
- Rows have no menu of their own: changes go through the selection bar (below), also for a single row or a day row
  (all its flights). For who may set it (`can_edit_date`), the *date unknown* chip of an undated log is a button that
  opens a small date dialog (`PUT /api/logs/<id>/date`). Changing an existing date and the classification are done
  on the [flight page](#flight-page-changes).
- Every row has a tick box, for everyone (paper records too); **Select** under **Columns** turns the column off (a
  per-browser column choice like the others), and then no selection bar appears. The **selection bar** at the
  bottom offers what applies to all ticked entries and disables the rest with the reason in its tooltip: Copy
  numbers (all have a flight number), MATLAB ▾ (all have a Paparazzi log), **Change…** (project, PIC, PAC, GCS,
  operator, category; fields on Keep stay) and **Same flight** (shown to who may change entries; enabled when they
  may change every ticked one), and **Hide** or **Restore** for admins.
- <a id="logs-without-flights"></a>**Logs without flights** have their own flight filter: shown unless *Hide logs
  without flights* is ticked (state `nologs`; in the URL only as `nologs=0` when hidden, so older links keep showing
  them; a browser selection saved before the option existed takes its value from `nonflights`). The Logbook loads
  `GET /api/logbook/logs-without-flights` and shows parsed logs (status `done`) without any flight as rows of their own
  with a *no flight* chip: the log number (as on the Logs page; outlined with a *log* label so it is not taken for a
  flight number, with ⧉ to copy it), date (set date, else the log start; *date unknown* chip otherwise), start and end, duration,
  type, aircraft names, project and the log name linking to `/logs#log<id>`. Failed, queued and parsing logs are left to
  the Logs page. The endpoint uses the Logs page's scope (`list_clause`: admins all; others their projects' logs and
  logs they uploaded or co-uploaded; `?project=`) and takes optional `from`/`to`, `unknown_dates=0` and `q`; the Logbook
  applies the period, unknown dates and search itself, sorts the rows by date with the flights and leaves them out with
  a Files filter or Hidden records only. With *Group by day* the dated ones of one aircraft (names) and UTC day form one
  grey-barred row (count ×, ⧉ copies their log numbers space-separated) that opens to the single logs. Ticked, they offer only what applies: MATLAB of the whole log (one log on its
  own), the date chip for undated logs (`can_edit_date`), and **Change…** with only the project for administrators
  (`PUT /api/logs/<id>/project`); Copy numbers, Same flight and Hide are disabled with the reason.
- **Files ▾** filters entries with files (any, or one category); while on it shows as a chip (*Files: Photos*) with ✕.
  The URL parameter `files` is unchanged (`any` or `cat:<category>`).
- **Manage ▾** (admins and members with edit, full or manager rights in a project): **Import CSV…**/**Add paper
  record…**, **Review**, and for admins **Hidden records only** (shown as a chip with ✕ while on).
- An opened entry shows the map beside the comment and a compact list of its recordings, linked paper record, media
  and files, then one row of actions. For those who may change it, **⋯** has *Split … off this entry…* (merged
  recordings, "Not the same flight") and *Unlink paper record…*, each confirmed in a small dialog.

**Overview pages.** Every person, aircraft, project and location has its own page (`/people/<id>`, `/aircraft/<id>`,
`/projects/<id>`, `/locations/<id>`; click a name anywhere). It shows totals (flights, flight time, distance, flying
days, log files) next to a map of takeoff spots, a flight time chart (monthly by default;
the step from day to year can be changed), breakdowns per aircraft/project/location/crew, and paged lists of flights
and log files. Admins see everything; others only see the pages and the flights and logs they have
access to.

**Responsible for** (issue 225). A drone's responsible person is `drones.manager_id`. A person's page lists the drones
they are responsible for (`responsible_for` in `GET /api/detail/people/<id>`: name, serial, status), all of them for the
person themself and admins, otherwise only those of the viewer's projects (default or timeline project). Everyone
sees the drones they are responsible for in the Aircraft list (`GET /api/drones`) and can open their pages, also when
the drone is on a project they are not in; the flights and logs shown stay limited to their own projects. **My aircraft**
on `/aircraft` (`?mine=1`) keeps only those, including ones without logs.

**Tags (#368, `tags.py`).** One list (`tags`: name unique without case, `hidden`) for all projects. A flight and a paper
record keep a JSON list of tag ids (`flights.tags`, kept across a re-parse like the other flight edits;
`flight_records.tags`, not part of the fingerprint); a logbook entry has the tags of all its sources and linked paper
records (`tags`, names) and `tags_hidden` (those hidden by default), also in `/api/logbook`, the detail pages and
Export CSV. Who may edit a flight sets its tags (`PUT /api/flights/<id>/tags` `{"tags": [names]}`, `logs:edit` for
keys); `PUT /api/logbook/records` takes `tags_add` (names) and `tags_remove` (ids); a paper record's form sends
`tags`. New names create tags (1–40 characters, no commas, at most 20 per entry). `GET /api/tags` lists them with
their number of entries; administrators rename or set `hidden` (`PUT /api/admin/tags/<id>`), merge
(`POST /api/admin/tags/<id>/merge` `{"into": id}`) and delete (`DELETE`; removed from every entry). An entry with a
hidden tag is left out of the shared filter (below), `/api/places` and the Home totals (`home.real_flight`) unless the
filter asks for that tag with ✓.
Tags at upload: `POST /api/upload` (also the bulk path) takes form fields `tags` (several, or comma-separated; new
names create tags). They are kept on the `ingest` row (a zip passes them to the logs in it) and, once the log is
stored or found to be a duplicate, added to `logs.upload_tags` and to the flights the log already has
(`set_ingest`). `assign_log_flights` gives them to every flight a parse creates that has no kept edits; a flight kept
across a re-parse keeps its own tags. Clients: the Upload dialog's Tags field, `upload_logs.py --tag`,
`upload(..., tags=)` and `logbook(tags=)` in the Python client, `pprz_upload(..., 'tags', ...)` and
`pprz_logbook('tags', ...)` in MATLAB (both list a flight with a tag hidden by default only when that tag is asked
for).

**Shared flight filters.** The menu's **Flight filters** popover stores its applied selection in
browser `localStorage` (`logdbFlightFilters`): period preset or inclusive custom UTC dates, tags (`[{name, out}]`: only
entries with any tag that is not `out`, none with an `out` one; in the URL as `tags` and `not_tags`, comma-separated),
indoor/no-GPS visibility, non-flight visibility, logs-without-flights visibility and unknown-date visibility; operator
and category are under More filters, open when one is set. Defaults are last three years with all
visibility options off. Apply commits changes; Cancel/Escape/outside-click discard the draft;
Reset fills the defaults for the next Apply. Other tabs update on storage changes. Storage
failures are reported in the popover instead of claiming the selection was saved.
Logbook, Statistics and all four entity-detail kinds use the same predicate before building
their flight totals, maps, chart buckets, breakdowns, pagination and exports. Unknown dates
are hidden unless **Show unknown dates** is enabled, in which case they are included alongside
any selected period; other visibility filters still apply. Older saved selections default this new
option to off. Corrected dates and dated paper records follow the selected period. Single-flight pages, entity
metadata/qualifications/memberships, directory administration and Uploads remain unfiltered.
Detail log inventories and log-data totals count each accessible contributing source once;
parsed zero-flight logs are additionally included only with logs without flights shown and a matching
recording/corrected date (or unknown dates enabled). Upload time is not substituted for a missing recording date.
The Locations overview sends `from`, `to`, `indoor=0/1`, `nonflights=0/1` and `unknown_dates=0/1` to `/api/places`.
The endpoint validates ISO dates/order and applies the selection to both counters and endpoint
dots (including unnamed-dot review), without changing place access or hiding accessible
location boundaries. Omitting the date arguments retains all-time API behavior; omitting
`indoor` retains the API's existing inclusion of indoor records.

<a id="flight-page-changes"></a>**Changes on the flight page (no Edit mode).** The page reads the same for everyone
and follows the [UI rule](#ui-rule). **Edit flight** (next to MATLAB) is shown when the user may change any field and
opens one dialog with only those fields: aircraft, goal and comment (`can_edit_comment`), PIC/PAC/GCS operator
(`can_edit_crew`, one `PUT /api/flights/crew`), project (`can_edit_project`, `PUT /api/flights/<id>/project`),
operator, category and classification (`can_edit_crew`, `PUT /api/logbook/records` with the entry's `log:<id>` keys)
and the date of the shown recording's log (`can_edit_date` of `GET /api/logs/<id>`, `PUT /api/logs/<id>/date`;
emptying a set date removes it). Save sends only the changed fields, one request each and the aircraft last; a failed
field shows its error and the dialog stays open. After a date or aircraft change the page reloads (times and the
aircraft's project move), otherwise only the summary. Goal, comment, crew, project, operator, category and
classification are saved on every recording of the flight. Clicking an editable value or its label opens the dialog at
that field. The ⋯ menu next to Edit flight holds what is not tied to one value, per log of the flight:
**Re-parse** (admins and who may change the flight; the page waits for the parser and reloads, and flight numbers stay
when the takeoff moved at most a minute) and **Delete** (`can_delete` of the log; afterwards the Logbook opens). The ⋯
is hidden when neither applies. The server checks every change as before.

**Goal and aircraft on the flight page.** Whoever may edit the flight (as for the comment) can:
- type the **flight goal** (`PUT /api/flights/<id>/goal`, at most 300 characters, saved on every recording of the
  flight): stored in `flights.goal_override`, kept across a re-parse; empty returns to the logged goal
  (`aircraft.goal`, given as `goal_logged`, while `goal` in the logbook rows is the override or else the logged one);
- link the opened recording to a **fleet aircraft** (`PUT /api/flights/<id>/aircraft` with `drone_id`, or `null` for
  automatic by name): the same per-log assignment as **Shared names** on the Aircraft page (`log_aircraft_links`), for every flight of
  that recording and kept across a re-parse. Non-admins choose from the aircraft whose timeline or default project
  is one they may upload to (`GET /api/flights/<id>/aircraft-choices`, `drone_choices`), so a recording cannot be
  moved into someone else's project; admins choose from all.

**Real flight or not.** The autopilot's in-flight flag also toggles on the ground (arming, motor tests, carrying).
The parser scores each takeoff/landing on duration, climb, distance, speed, tilt and acceleration and gives a verdict
`flight`, `uncertain` or `ground` with the reasons. **Hide non-flights** leaves out the last two. Whoever may edit
a flight's crew can set its classification with Edit flight on the flight page (`verdict` `flight`, `ground` or `null` for automatic in
`PUT /api/logbook/records`, logged flights only): it is stored in `flights.verdict_override`, kept across a re-parse
like the other flight edits, and counts everywhere instead of the parser's verdict (the logbook rows give `verdict`,
`verdict_auto` and `verdict_override`). Blackbox logs without GPS are scored from the local NED position and velocity. When `ENERGY` reports current, a flight of at least two minutes that draws on average 1 A or more while
its voltage varies by less than 0.1 V loses 2 points ("battery voltage steady under load"): that points to an
external power supply (bench or tether test). The rule only lowers the score, so on its own it can turn a flight
into `uncertain` but not into `ground`. The taxi rule ("moved on the ground without climbing": 3 m or more
horizontally with less than 1.5 m height change) does not apply when motion capture (OptiTrack) is used: indoor
flights stay low while moving a few metres (parser 1.12.1). Nor does it apply when the log has `LIDAR` height
above ground and at least 10% of the flight's readings (and at least 5) are 0.5 m or more above the lowest one:
the aircraft left the ground, so it was a low hover rather than a taxi. Hybrids can also roll, so the aircraft type
alone does not decide it (parser 1.12.3; `lidar_rise_m` in the metrics).

**Flight page** (`/flight/<id>`). Path on a map with the flight plan waypoints, a time slider, charts,
takeoff/landing details, setting changes, messages, checklist and autopilot version; switch between the SD
and GCS log when both exist. Missing `AUTOPILOT_VERSION` falls back to the GCS `.log` header.
**← / →** next to the MATLAB menu (and the arrow keys, except while typing, on the tabs, the map or the slider) open the
previous/next flight in the same project among the flights the viewer may see, by takeoff time (else the set date or
the log's start), then log file name in natural order and flight number (`neighbour_flights`; `previous_flight_id`
and `next_flight_id` in `/api/flights/<id>/detail`). Without a project both are greyed out. The open tab is kept.
*Open flight page* in an expanded Logbook row opens the flight in the same tab; Back returns to the logbook with its
page, filters and opened rows (kept in the URL).
Photos, videos and sound files (by extension) open in a media viewer over the page instead of a new tab, wherever a file
name is shown (Files lists, the Logbook row, Home): `theme.js` `openMedia` for any link with `data-media` (`mediaAttrs`);
← / → step through the media links in the same list (`data-media-group`), a video starts playing at once and stops when
the viewer closes. Ctrl/Cmd-click still opens the file in a new tab; PDFs and text files still open in a new tab.
Logs the MATLAB export does not cover (PX4, ArduPilot, MAVLink, Betaflight/INDIFlight) get a **Download <type> log**
button next to the MATLAB menu, on the flight page and in an expanded Logbook row (`/api/logs/<id>/download`, the
original file).
Charts list their series as chips (× removes); **+ Add** offers only series with data in this log, grouped by kind,
and **+ Add chart** adds a preset (height and speed, attitude, acceleration, wind, battery) or an empty chart.
Without a recent position (no GPS fix or motion capture for 2 s) the Paparazzi parser (1.11.0+) still stores
samples at the path rate with null latitude/longitude, so GPS-less flights have charts (barometric height,
attitude, battery) although the map stays empty.

**Datalink quality** (parser 1.12.0+). The ground station's `LINK_REPORT` (ping, `rx_max_gap` or else
`rx_lost_time`, frame and error counters) and the `LINK_STATS` of the nodes on the link path (`rssi`, `rssi_ac`;
127 = not available, the weakest node counts) are added to each path point as `link` and summarised per flight
(`link`: mean/max ping, longest gap, bad-frame percentage, weakest signal; reports after the landing time are
ignored because the ground station keeps reporting the silence at the end of a log). The summary is stored in
`flights.link_json`. The flight page shows it as one short **Datalink** line (mean ping · longest gap, ground or uplink · weakest
signal) with the full summary on hover, and offers Ping, Longest link gap and Signal series
and Datalink and Signal strength chart presets.
Actuator commands (`ACTUATORS`, raw values, named after the airframe's servos or `ACT_<n>`) appear as an
**Actuators** series group and chart preset when the log has them. The logbook marks a flight with **(link)** when the longest gap is at least 3 s or at
least 5% of the frames were bad. Parser 1.12.2 adds the aircraft's own `DATALINK_REPORT`, so SD logs have
link data too: `uplink_lost_time` per point (**Uplink silence**) and per flight the longest uplink gap (counted once
the silence ends, so a flight without any ground link is no problem) and the bad uplink frames (`uplink_err` +
`uplink_ovrn` against `uplink_nb_msgs`, counters unwrapped). The logbook also marks an uplink gap of at least 10 s
(the ground can be quiet for several seconds) or 5% bad uplink frames. When a flight has both an SD and a GCS log,
the logbook combines their summaries (worst value of each), and each flight page fills the datalink values of
its points from the other log, matched by UTC within 2.5 s. The flight page also combines the rest: the other log's
times are moved onto this log's clock (from both takeoff times in UTC); its info messages and setting changes are
added to the lists unless the same text or setting appears within 2 s, labelled with their log; points without a
position get the other log's nearest position within 2.5 s (position source "SD log"/"GCS log"), and between
takeoff and landing the other log's points fill gaps of more than 1.5 s. The map says how many points came from the
other log; distances stay this log's own. Wind speed and
direction come from the per-point `wind` estimate. Hovering shows the exact values at that time; axes use four
rounded steps (wind direction 0–360°). Click moves the map marker; drag or scroll zooms.

**Database.** `logs` (hash, files, status, parser version) → `aircraft` (crew, goal, versions, messages, config) →
`flights` (takeoff/landing, index into the paths file) and `setting_changes`.
Independent `flight_records` hold paper provenance and totals, projected into the same logbook read model.
Future telemetry formats can provide that read model without treating paper records as uploaded logs.

**API** (all JSON, login or API key): `POST /api/upload`, `POST /api/logs/check`, `GET /api/logs`,
`GET|DELETE /api/logs/<id>`, `POST /api/logs/<id>/reparse`, `POST /api/reparse-outdated`,
`POST /api/reparse-unknown-dates` (administrator),
`GET /api/logs/<id>/download/{data,log}`, `GET /api/logbook`, `GET /api/flights/<id>/detail`,
`GET /api/flights/<id>/path`, `GET /api/aircraft/<id>/config`, `GET /api/status`, `GET /api/me`.
