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
- [Private licences and qualifications](#private-licences-and-qualifications)
- [Uploading with a script](#uploading-with-a-script)
- [How it works](#how-it-works)

## Try it locally

    cmake -S ../cpp -B ../cpp/build -DCMAKE_BUILD_TYPE=Release && cmake --build ../cpp/build   # the parser
    pip install -r requirements.txt
    python3 app.py create-user alice --admin
    python3 app.py                                  # http://127.0.0.1:8000

Data goes to `webui/data/`. Always build the parser as `Release`; a Debug build is many times slower.

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

After an update that changes the parser version, click **Re-parse outdated** on the Uploads page.

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
are separate from parser versions; create an annotated tag on the tested release commit and push
that tag with the code. Deployments must fetch tags to display release names; the installer does
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
project/location details exclude them from displayed flights, totals and charts unless **Show
non-flights** is selected, which includes simulations, uncertain flights and ground records.
Locations overview counts follow the same default (`GET /api/places?nonflights=1` includes
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

**Missing telemetry locations.** The shared logbook read model copies the known endpoint's GPS
latitude/longitude for display and named-place resolution when the other endpoint is missing.
The flight Overview labels the inferred location; no event, time, altitude or raw coordinates are
written or fabricated. Historical weather still requires a logged takeoff position, not a landing
position copied for display. Upload details use the same coordinate fallback. When neither endpoint has
valid coordinates after checking endpoints from every merged source, the entry is
classified as indoors for display/filtering (labelled as inferred on the flight page). Valid positions
exclude zero/zero, non-finite and out-of-range coordinates. This is a no-GPS heuristic, not proof of
indoor operation; a partial GPS path does not override this endpoint-based classification and
remains available on the map. Existing named-indoor-place and OptiTrack rules still apply. Paper records are
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

Uploads details and the flight Overview's **Log sources and upload details** show the original
companion start, effective start, parser correction in seconds, source and any warning for the selected
recording. Manual calendar-date overrides remain authoritative and survive reparsing; the parser's
original clock evidence remains visible. Old results have no clock evidence until reparsed. All absolute
timestamps in newly parsed results, including paths and messages, use the chosen clock; relative times
and flight durations are unchanged. Normal source merging then uses the corrected absolute times;
there is no track-similarity matching across dates.

**Flight pages.** The Overview opens first, with takeoff/landing details and log sources folded
away. Autopilot software is shown in Overview with the decoded version, Git commit and base tag links,
and warnings for local (dirty) changes. Multiple reported builds remain visible; raw version messages
can be expanded. Commit links use `paparazzi/paparazzi` and may not resolve for private forks.
When the aircraft did not send `AUTOPILOT_VERSION`, the GCS `runtime paparazzi_version` comment at
the top of the companion `.log` is shown instead and labelled as coming from the GCS log.
Merged SD/GCS entries retain the fleet name, aircraft link and TUD/serial number from a linked
source even if the main telemetry source is unlinked. Sources linked to different fleet aircraft
are kept separate rather than choosing an identity.
Tabs separate the path/charts, weather, settings, messages and pre-flight checks.
Messages and settings show 25 rows per page, scoped to the flight by default; their checkboxes include
the whole log. Tab counts reflect the flight scope (messages also include 30 seconds either side).
Tabs support keyboard arrows and can be linked directly, for example `/flight/42#messages`.

**Additional files.** Use the **Files** tab on a flight, **Additional files** on a paper record,
or **Additional files** in an expanded upload. Photos, videos, wind-tunnel measurements, offboard
computer logs and other files can be uploaded there. Search by aircraft, date, filename or numeric
ID to select up to 100 flights/logs (including paper records); one uploaded file is stored once
and linked to every selected entry. Files linked to a whole log also appear on its flights, provided
the user can see that log. The management page lists existing files and lets you link them to more entries.

Admins and users with full access to each selected target can upload/link files. Anyone who can
read **any** linked target can download the file: linking across projects deliberately shares its
contents. Inaccessible link names/IDs are not disclosed. Sharing an existing file further requires
full access to all its existing links as well as the new targets. A user with full access can remove
an individual link without affecting other links; the stored file is removed only after its last link
is removed. Deleting a flight's parent log or a paper record removes the corresponding links.
Re-parsing retains links to flights with the same aircraft ID, flight index and takeoff time; if that
flight no longer exists, its file links move to the parent log instead of losing the file.
API keys can upload files (`POST /api/attachments`) and link an existing file to more targets
(`POST /api/attachments/<id>/links`) with the same permission checks; the upload returns the file `id`.
Removing links (and so deleting files) requires a browser session.

Each file is limited to **10 GiB** by default (`LOGDB_ATTACHMENT_MAX_BYTES`).
Uploads and downloads stream in bounded chunks; files upload sequentially with progress and errors.
Keep your reverse proxy's body-size limits and timeouts large enough for your recordings.
A compressibility sample avoids recompressing typical JPEG/video/archive files. Other files use
fast gzip compression only if the stored result saves at least 5%. No resizing, transcoding or other
lossy conversion is done; downloads reproduce the original filename and exact bytes.

`GET /api/attachments/<id>/view` serves the same bytes with `Content-Disposition: inline`, with the same
access checks as the download. The type comes only from an extension allowlist (`VIEWABLE` in
`attachments.py`): PDF, `.txt`/`.csv`/`.md`/`.log` as `text/plain`, and PNG/JPEG/GIF/WebP. Any other
file (HTML, SVG, XML, other Office documents, no extension) gets 415 and stays a download, so stored files
never run script in the site's origin. `nosniff` and `Cache-Control: private, no-store` apply to both
routes; inline text and images also get `Content-Security-Policy: sandbox` (not PDF, whose built-in
browser viewers break under a sandbox).

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
Uncompressed legacy log storage is **not supported** by this test-environment change: delete those
test uploads and re-upload their originals. There is no automatic conversion or destructive reset.
The database, accounts and paper records do not need to be deleted. Attachments live under
`LOG_DIR/attachments`; include them in the log-directory backup, alongside the compressed source logs.

A Samba share can be mounted with the service user as owner, in `/etc/fstab`:

    //fileserver/share  /mnt/flight-logs  cifs  credentials=/etc/flight-logs/smb.cred,uid=logdb,gid=logdb,file_mode=0660,dir_mode=0770,_netdev,nofail  0 0

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
explanation. `python3 app.py check-config` shows the effective values and where they come from, and catches typos,
a missing parser, unwritable directories and http/https mismatches. `install.sh` sets the first group for you.

| Setting | Default | Meaning |
|---|---|---|
| `PUBLIC_URL` | (empty) | Address users open, e.g. `https://logs.example.org` (used for SAML) |
| `COOKIE_SECURE` | `0` | `1` when served over https |
| `PROXIES` | `0` | `1` behind nginx/Caddy (trust its `X-Forwarded-*` headers) |
| `BIND` | `127.0.0.1:8000` | Address gunicorn listens on |
| `HIDE_UNLINKED_AIRCRAFT` | `0` | `1` excludes unlinked telemetry flights from Logbook, Statistics, person details and Locations, and hides the Aircraft notice outside Edit mode |
| `WEB_DATA` | `webui/data` | Database and keys; local disk only |
| `LOG_DIR` | `<WEB_DATA>/logs` | Uploaded logs and flight paths; may be a share |
| `BACKUP_DIR` | (empty) | Destination of `app.py backup` |
| `BACKUP_EXTERNAL_PERMISSIONS` | `0` | Opt in to externally managed backup ACLs when chmod is denied/unsupported; warns instead of failing on those chmod errors |
| `SAML_ENABLED` | `1` | `0` hides *Sign in with TU Delft* |
| `SAML_METADATA_URL` | TU Delft IdP | Identity provider metadata |
| `SAML_ENTITY_ID` | `<PUBLIC_URL>/saml/metadata` | This service's SAML entity ID |
| `THREADS` | `8` | Gunicorn threads (always one gunicorn process: the parse queue lives in it) |
| `WORKERS` | `1` | Logs parsed in parallel; increase only with sufficient RAM and temporary disk |
| `INGEST_WORKERS` | `1` | Threads that hash, deduplicate and unzip uploads |
| `PATH_RATE` | `5` | Flight path points per second (`0`: no map path) |
| `ATTACHMENT_MAX_BYTES` | `10737418240` | Maximum original bytes per attachment (10 GiB) |
| `BULK_MAX_PENDING` | `8` | Shared pending-job budget for bulk scripts |
| `BULK_MAX_BYTES` | `17179869184` | Shared estimated-byte budget for bulk scripts (16 GiB) |
| `LOCAL_MIN_FREE_BYTES` | `2147483648` | Local staging/decompression headroom (2 GiB; parser output needs extra space) |
| `FASTREADER` | `../cpp/build/paparazzi_log_parsing` | Parser binary |
| `SESSION_HOURS` | `12` | How long a login lasts |
| `SECRET_KEY` | generated in `<WEB_DATA>/secret_key` | Session signing key |

Relative paths are relative to `webui/`.

Set `LOGDB_HIDE_UNLINKED_AIRCRAFT=1` and restart the server to hide telemetry flights whose
merged aircraft identity has no fleet link. Lists, counts, flight time, charts and location
map hints use only the remaining flights, for all users including admins. A merged SD/GCS flight
stays visible if either source is linked. Paper records are unchanged (they already require a
fleet aircraft). The Aircraft page keeps unlinked names available to admins in Edit mode for
linking, but hides its read-only unlinked-name notice. Direct flight/upload details remain
accessible under the existing permissions; this is a display setting, not an access restriction.
Person detail flight lists, charts and role totals use the same filter as Statistics, so PIC/PAC
totals agree when the indoor/non-flight filters match. Person headline flight time includes
participation in any crew role, not just PIC/PAC. Project detail pages and uploads are unchanged.
Set the value back to `0` to restore
the previous overviews; no records are deleted or reparsed.

## Accounts and TU Delft login

Click your name in the top bar to open **My account**. Local users can change their password
by entering their current password and a new password of at least 10 characters. On **People**, admins
can enable **Edit**, select a person and use **Reset password** (or **Set password**) in their Account tab to
generate a one-time, 48-hour link for a local account and share it privately.
Generating a link does not change the password until it is used; a new link replaces older links.
SAML/TU Delft passwords remain managed by the identity provider. API keys cannot manage passwords.
Admins can also **Remove password** in that Account tab without deleting the profile,
flights or other linked information. This clears unused setup/reset links and prevents new password
sign-ins; existing sessions and API keys are not revoked. **Set password** can restore local sign-in
later. The last administrator's login method cannot be removed.

In **People → Edit → Account**, an administrator can **Make administrator** or **Remove admin rights**
for another person, with confirmation. Only active, non-external accounts can be promoted.
Self-demotion is blocked: ask another administrator to remove your rights. The last active
administrator cannot be demoted. These changes preserve ordinary project memberships and take
effect on subsequent requests, including requests using existing API keys.
The Account tab also lists the selected person's API-key names, prefixes, creation times and last-use
times (or Never). In Edit mode, admins can revoke individual keys after confirmation; scripts using
that key immediately lose access. The selected user's other keys remain unchanged.
Secrets and hashes are never returned; full secrets cannot be recovered.
Both role changes (`PUT /api/admin/users/<id>/admin`) and key-detail access
(`GET /api/admin/users/<id>/keys`) or revocation (`DELETE /api/admin/users/<id>/keys/<key_id>`)
require an administrator browser session, not an API key.

Everything requires a login. Admins create accounts on the People page or on the command line (on a server prefix
`sudo -u logdb /opt/flight-logs/app/webui/venv/bin/`):

    python3 app.py create-user alice --admin --name "Alice"    # asks for a password (min. 10 characters)
    python3 app.py set-password alice
    python3 app.py list-users
    python3 app.py deactivate-user alice                       # or activate-user

Email is optional for admin-created accounts and can be cleared in the People Account tab.
Non-empty addresses must be valid and unique (case-insensitive). Without an email, automatic SSO
linking by email is unavailable; password setup/reset links can still be shared privately.

**Sign in with TU Delft** uses SAML 2.0 against `https://login.tudelft.nl/sso/saml2/idp/metadata.php`. Register
`<PUBLIC_URL>/saml/metadata` with TU Delft ICT (`middleware-ictfm@tudelft.nl`) once; the service's signing keys are
generated in `<WEB_DATA>/saml/` and are part of the backup (losing them means registering again). A first SSO login
creates a non-admin account, linked to an existing local account with the same email. Give people access to projects
on the Projects page.
SAML identity mapping accepts both `uid` / `UID` and `displayName` / `Displayname`, alongside
the supported standard OID/URN names. NameID remains the preferred subject identifier; `mail` supplies email.

### Tests

Regression tests live in `tests/`. Run only the smallest relevant test with the project's Python environment:

```sh
cd webui
python -m unittest tests.test_people.PeopleDirectoryTests -q
```

Choose the test class/method or module that covers your change. Do not run the full suite unless requested.
When explicitly needed, full discovery is `python -m unittest discover -s tests -t . -v`.
App tests use temporary storage and in-memory databases, without starting ingestion workers or using real logs.

### Interface layout

Pages use a compact heading hierarchy with regular-weight supporting text. Longer instructions are
available through expandable **Help** disclosures; field requirements, errors and sharing warnings
remain visible alongside the relevant controls.

Aircraft keeps its overview table with Edit off. In Edit mode, use the filtered aircraft list and
the selected aircraft's **Details**, **Project history** and **Aliases** tabs. Add aircraft and CSV import
open compact dialogs; unlinked aircraft names remain collapsible. Their link menu always lists every
aircraft, including Discard (Show discarded only filters the list); its search box at the top of the
menu filters the list below, names starting with the typed text first (Enter picks the first match). Add user also opens a dialog.
Dialogs can be closed with Close or Escape. Details are saved together; project
periods keep their separate Save timeline action.
Aircraft without uploaded logs or paper records, and people without any log, paper record or approved
qualification, stay hidden until **Show aircraft without logs** / **Show people without logs or licences**
is ticked. A person counts as having logs as uploader or as linked PIC/PAC/GCS; the aircraft or person
selected in Edit mode stays listed. Non-admins only count qualifications they may see.
Logbook has no top-level project filter and no longer applies a previously saved project filter.
Project reassignment controls in Edit mode remain available.
The menu puts Logbook (`/`) first, followed by Uploads (`/uploads`), Statistics, Aircraft, People, Projects and Locations.
`/logbook` remains a working alias. Upload log-detail anchors use `/uploads#log<id>`.
Linked aircraft, crew, projects and named locations in the logbook open their detail pages.
Coordinates without a linked location still open the external map; Edit mode keeps its reassignment menus.
Aircraft serial numbers in the logbook appear on hover over the aircraft name rather than inline.
Overview table links use the theme's blue accent colour, with an underline on hover or keyboard focus.

### Project and aircraft CSV imports

For initial setup, import the qualification ZIP on People first (it can create accounts and types), then the
project CSV on Projects, then the aircraft CSV on Aircraft. Both catalog imports are admin-only,
available in Edit mode, with downloadable templates. Files are comma-separated UTF-8 (optional BOM);
Latin-1 uploads are also accepted for older fleet spreadsheets. Limit: 1 MB / 5000 rows. All changes
from one file are committed together; any invalid row leaves the database unchanged.

Project columns are `project,person,role`, one row per member. `role` is optional and defaults to
`add` (view/upload); other values are `view` and `full` (view/upload/delete). Person matching uses
unique display names or explicit aliases, case-insensitively, not implicit usernames. Unknown,
ambiguous or inactive people and conflicting roles reject the file with a row error.
Projects match existing names case-insensitively; reimport keeps project IDs and linked data.
Each included project's **entire member list is replaced**, including changed permissions and removal
of omitted members. Projects absent from the file stay untouched. A row with a blank person and role
creates or retains a project without members. Changing a project's name in the CSV creates a new project;
use the existing Rename control for a rename that preserves identity.

Aircraft retain the fleet columns `AC Nice Name`, `Serial Unique`, `Manager (Post Holder)`,
`Current User`, `Status`, `Date Bought`, `Project`, `Comments`. `name` and `unique_id`/`serial`
are accepted alternatives for the first two columns. Reimports use the existing serial/name matching,
update provided fields and preserve omitted optional columns. Empty Project cells preserve existing
project/default/history values; a non-empty Project must match an existing project.
Changing an existing aircraft's project requires `project_effective_date` (or `Project Effective Date`),
strictly YYYY-MM-DD. This starts a new inclusive project period on that date, truncating any period
crossing it and retaining earlier history and later scheduled periods. The new period ends the day
before the next existing period, if any. Repeating the same import does not duplicate aircraft or periods.
For new aircraft the effective date is optional: without one, the existing purchase-date/first-flight/
today fallback seeds the initial period.

Flights now record whether their project came from a timeline or a manual edit. A CSV period change
reassigns known automatic flights in that date range, including hidden flights, but preserves manual
assignments (including a manually cleared project). Existing non-empty assignments of unknown origin
are deliberately preserved; no migration guesses which were manual. Unassigned flights can still be
filled from the timeline. Assignment origin is retained across re-parsing alongside crew edits.
Paper records are not reassigned by an aircraft CSV import.

Successfully parsed logs with no flight records inherit a project from their linked aircraft's
timeline on the recording date (including a corrected date). Every aircraft in the log must resolve
to the same project; missing dates, unlinked aircraft and timeline gaps leave the log unassigned.
Existing non-empty log project assignments are preserved. Eligible existing zero-flight logs are
also filled at startup and when aircraft assignments are updated, without reparsing telemetry.
These logs appear in the aircraft/project detail pages' Log files section but add no flight time.

### Places CSV import

`POST /api/admin/places/import` (admin; button on Locations in Edit mode) reads the same CSV limits as
the catalog imports. Columns: `place`, `lat`, `lon`, `radius_m`, optional `indoor` (yes/no, 1/0,
true/false; blank is no). The extra columns of `logbooks/places.csv` (`timezone`, `log_names`, `source`,
`confirmed`) are accepted and ignored; any other column is an error. Every row becomes a circle.
Places match by name, case-insensitively: no match creates a place, a match with the same name,
indoor flag and circle is `unchanged`, anything else updates it (keeping its ID, so flights and paper
records stay linked). A name that matches more than one place, a repeated name in the file, or an invalid
row rejects the whole file. With form field `preview=1` the response lists each row's action without
writing; the page shows that first. Replacing a drawn polygon by a circle is flagged in the preview.

### Private licences and qualifications

Users upload and manage their own qualifications on **My account**, alongside password settings.
The read-only People overview shows names, usernames, aliases and qualifications, including for normal
users within their existing directory access. Admins also see sign-in information with Edit off.
Enable Edit to search by name, username, email or alias, then select someone to open their
**Account**, **Aliases** or **Qualifications** tab. Enable Edit to change account settings, manage aliases,
upload evidence for that person, or review/delete submissions. The people list is searchable, without
an additional Show filter. The approval queue on People opens the relevant person's
Qualifications tab, enabling Edit if needed. The queue is hidden when empty; type settings are
in a separate People section visible in Edit mode.
Admins can edit username, display name, email and default project together with **Save account** in the Account tab.
All four fields are saved atomically; previous unambiguous display names are preserved as log aliases. Display names are optional
and at most 80 characters. Usernames must be unique (case-insensitive),
1-40 characters and contain no spaces or control characters. Username changes do not create log aliases.
Passwords, SSO identity links, project memberships and existing records stay attached to the same person.
Display names conflicting with another person's log alias must be resolved first.

Log ingestion, startup and opening People do not create profiles from crew names. Unmatched PIC/PAC/GCS
names stay as logged text and appear under **Unlinked crew names** for admins in Edit mode. Link them
to an existing person using aliases, or explicitly use **Add user** when a profile is needed.
**Add as external** (browser session only) creates an external person for crew of another operator, e.g.
flights under their operational authorisation: display name = the logged name, no password or email, and
`users.is_external=1`. Their flights link and count like anyone's, but they cannot sign in (password,
SAML, setup links and API keys are refused), cannot be project members (UI and project CSV reject them),
and stay hidden on People until **Show external** is ticked. The Account tab's External checkbox converts
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
available evidence using the existing private download endpoint. These summaries and links remain
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
`RT licence` or an internal qualification. Each type has evidence-required, start-date, expiry,
internal and active switches. Internal is a label for in-house qualifications, not a licence rule.
Qualification chips in the People overview and current account qualification lists follow the saved
type order, not submission order; multiple submissions of one type are newest first.
Types keep a `sort_order`; the People editor reorders them by drag-and-drop and writes every type
with one Save (`PUT /api/admin/qualification-types`). Types can be disabled.
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
Read-only People chips show only approved, non-expired submissions and omit the approval label;
management views retain all submission states.
An existing submission without a newly required expiry must be withdrawn and resubmitted; approved records
are not changed retroactively. Existing types keep expiry optional after upgrading.

Users submit claims for themselves; **admins can also upload evidence and submit claims for any user**.
Admins can bulk-import from People in Edit mode: a ZIP with `manifest.csv` and evidence files
(`person,type,valid_from,valid_until,file`). Preview matches unique display names or aliases and
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
licence, so initial setup needs no manual Add user step. Ambiguous names block the import. The qualifications panel splits Current (pending and non-expired approvals) from
Archive (expired approvals and superseded records); rejected and withdrawn submissions stay under Current.
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
API keys can read permitted records, but uploads, reviews, deletions and type changes require a browser session.

Evidence is stored separately from shared attachments under `<LOG_DIR>/qualification-evidence/`, with opaque
filenames and lossless compression when worthwhile. It is served only through permission-checked endpoints,
with private/no-store caching and download hardening headers. Do not expose this folder through nginx or a
public file share. Serve the application over HTTPS and restrict filesystem/share/backup access to trusted
operators. Back up this folder along with the logs and the SQLite database; the `backup` command alone copies
the database and keys, **not evidence files**. Backups contain sensitive information, and deleting live records
does not erase retained backups or snapshots.

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
(use the Uploads page). A timed-out upload may still complete on the server.

## How it works

**Uploads.** A `.data` file is paired with the `.log` of the same name in the same folder; other files are ignored.
The upload returns as soon as the files are on disk; hashing, duplicate checks, unzipping and parsing run in the
background. The SHA-256 of the `.data` file identifies a log, so a known log is not stored twice (a missing `.log` is
added later). Files are kept in `<LOG_DIR>/<sha[:2]>/<sha>/` with the flight paths in `paths-<id>.json.gz`; uploads
are staged in `<WEB_DATA>/incoming`. Each log records its parser version; **Re-parse outdated** re-runs the current
parser on older and failed logs.

The Uploads **Status** dropdown filters `/api/logs` before counting and pagination:
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

**Logbook.** The parser tells onboard SD logs from ground station logs. The logbook (`/logbook`) has one row per
flight and merges the SD and GCS recordings of the same flight: flight data from the SD log, PIC/PAC/GCS operator and
goal from whichever log has them. Named takeoff areas are listed on the Locations page; everyone sees the ones their
flights used, and admins add, change or delete them there in edit mode (an area can be marked indoors; indoor flights
are hidden by default). Logs without a valid date (missing or 1980 clock) are flagged so the
real day can be entered.

`GET /api/places` groups endpoint dots at three decimal places (about 100 m).
Each dot has a total `count` and an `unnamed_count`, determined from the endpoints'
resolved place IDs before rounding, not from whether the rounded dot lies inside a shape.
The Locations unnamed-dot review is available only to admins in Edit mode. Leaving
Edit mode clears the filter and returns to all dots. It filters on `unnamed_count`, orders by that count, and
keeps named boundaries visible as context. It uses the same accessible flights and
non-flight toggle as the normal map. Saving a place refreshes the review without changing
flight records or requiring reparse.

The Locations map groups named-place centres into 64-pixel projected grid cells below
zoom 13, using built-in Leaflet markers (no additional map dependency). Counts represent
places, not flights; clicking a group zooms in up to three levels. At zoom 13 or above,
or in admin Edit mode, the actual boundaries replace the overview markers. The selected
place always retains its boundary and name label; other names appear on hover.
Selection and the case-insensitive table name search are browser-only and do not merge
places, change access rules or change the smallest-containing-shape resolution rule.

Replacing a named boundary uses the same `PUT /api/admin/places/<id>` shape update
as resizing it; it never deletes/recreates the place. Circle, polygon and rectangle
redraws are previews until saved (rectangles are stored as polygons). The place ID,
name, indoor flag and historical-record references remain intact. Cancel or leaving
Edit mode restores the original boundary; a failed save retains the preview for retry.

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
leads a merged entry (duration, verdict, track). Simulation separation and SD/GCS field selection
are unchanged.

Statistics sends only accessible contributing source-log IDs and stored original `size_bytes`,
not filesystem sizes. The browser applies the same shared UTC date-range helper as Logbook and
deduplicates source IDs after filtering. GB uses decimal units (1 GB = 1,000,000,000 bytes).
An entire source log counts when any of its flights qualifies; this is not a byte estimate for
just that flight interval. Unknown sizes are explicitly reported, and paper records contribute
no log bytes. Period/filter changes require no file reads or additional Statistics API requests.

**Shared aircraft names.** In Aircraft Edit mode, mark a generic log name as ambiguous using
the Aliases tab or **Ambiguous / shared aircraft names**. For example, an SD airframe name
such as `RotatingWing25Kg` can be shared by several physical aircraft even though GCS
configuration names are specific. Ambiguity is explicit, case-insensitive and applies to that
exact trimmed logged name; it is not guessed from spelling or automatically enabled for
particular aircraft.

Removing an aircraft alias recomputes only the logged names that used that alias
(including embedded board-ID matches), without relearning aliases during the operation.
The button shows progress and reports request failures. Removing an alias does not prohibit
other identity evidence: fleet names/serials, remaining aliases, unique folder matches and
explicit per-log assignments can still keep an aircraft linked. A later upload may learn its
identity again through these rules. Use **Mark ambiguous** for a generic name that must not be
automatically assigned by name or folder.

Marking a name removes its global alias and recomputes the affected logs' automatic fleet
links. Neither the name nor upload-folder hints may assign that shared name to a fleet aircraft.
Other aliases, raw logs, existing flight project assignments and paper records are unchanged.
Existing global alias assignments were automatic; they were not per-log confirmations.

An unresolved SD source with an ambiguous name is considered separately after normal source
grouping. It may join exactly one candidate group containing a linked GCS source when their
intervals overlap within the existing 60-second slack, autopilot IDs agree and both takeoff
positions are valid and within 2 km. If any qualifying groups actually overlap, slack-only
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

**Paper flight records.** In the Logbook, choose **Add paper records / import CSV**. Admins and members
with **full** access to the selected project can create, edit and delete these records; other project
members can read them. Select an existing aircraft, PIC and project, then enter the date, total flight
time in minutes and flight count. An individual flight has count 1 and an optional UTC takeoff time.
A daily summary has a count and a combined duration, with no fabricated individual takeoff times.
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

A paper record and the logged flight(s) of the same flight can be **linked** (`links.py`, table
`flight_links`). Nothing is merged or changed: a link row says "same flight" (`same=1`) or "not the same
flight" (`same=0`), with `origin` `auto` or `manual`, who and when.

- **Stable keys.** A logged flight is addressed by `log_id` + `ac_id` (-1 when none) + parser flight number
  `idx`, which survive re-parsing (flight ids do not). The takeoff time at decision time is stored too;
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
  Not the same on a paper record's possible duplicates, Unlink on a linked entry in Edit mode). Needs
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
  (`possible_duplicates`), except pairs marked not the same.

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
- In the logbook (Edit mode), **Same flight** in the selection bar joins the ticked logged entries; with one
  ticked paper record it also links that record. An expanded merged entry lists its recordings with
  **Not the same flight**.

Admins can select paper and telemetry records together in Logbook Edit mode and apply PIC, PAC,
GCS and project changes in one transaction. Paper records still require a PIC and project:
leave these fields on **Keep** or select a person/project rather than clearing them. Duplicate
paper-record checks still apply, including hidden records; an invalid selection rolls back the whole change.
Paper crew/project cells also support individual inline edits for admins.

**Hide selected** is reversible removal from the logbook and its statistics/entity totals, not file deletion.
It hides all selected telemetry sources (including both SD/GCS sources of a merged row) or entire paper
records/daily summaries. Unselected flights in the same uploaded log are unaffected. In Edit mode,
admins can select **Hidden records only**, select entries and choose **Restore selected**.
Source logs, CSV attachments and record data stay on disk; uploaded-log views retain their source summaries.
The hidden state is preserved across re-parsing using the same aircraft/flight-index matching as crew edits.
Permanent deletion from existing record/upload detail pages remains a separate operation.

**Edit mode.** The Edit button in the menu bar shows the forms and menus to change things on the Logbook, People,
Projects, Aircraft and Locations pages; it stays on until you turn it off. With edit mode on, admins also see the names in the
logs that are not yet linked to a person or aircraft, if there are any. With it off, a one-line reminder says how many
are left.

**Overview pages.** Every person, aircraft, project and location has its own page (`/people/<id>`, `/aircraft/<id>`,
`/projects/<id>`, `/locations/<id>`; click a name anywhere). It shows totals (flights, flight time, distance, flying
days, log files) next to a map of takeoff spots, a flight time chart (monthly by default;
the step from day to year can be changed), breakdowns per aircraft/project/location/crew, and paged lists of flights
and log files. Admins see everything; others only see the pages and the flights and logs they have
access to.

**Shared flight filters.** The menu's **Flight filters** popover stores its applied selection in
browser `localStorage` (`logdbFlightFilters`): period preset or inclusive custom UTC dates,
indoor/no-GPS visibility, non-flight visibility and unknown-date visibility. Defaults are last three years with all
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
parsed zero-flight logs are additionally included only with non-flights enabled and a matching
recording/corrected date (or unknown dates enabled). Upload time is not substituted for a missing recording date.
The Locations overview sends `from`, `to`, `indoor=0/1`, `nonflights=0/1` and `unknown_dates=0/1` to `/api/places`.
The endpoint validates ISO dates/order and applies the selection to both counters and endpoint
dots (including unnamed-dot review), without changing place access or hiding accessible
location boundaries. Omitting the date arguments retains all-time API behavior; omitting
`indoor` retains the API's existing inclusion of indoor records.

**Real flight or not.** The autopilot's in-flight flag also toggles on the ground (arming, motor tests, carrying).
The parser scores each takeoff/landing on duration, climb, distance, speed, tilt and acceleration and gives a verdict
`flight`, `uncertain` or `ground` with the reasons. The logbook hides the last two unless **Show non-flights**
is ticked.

**Flight page** (`/flight/<id>`). Path on a map with the flight plan waypoints, a time slider, altitude/speed chart,
takeoff/landing details, setting changes, messages, pre-flight checks and autopilot version; switch between the SD
and GCS log when both exist. Missing `AUTOPILOT_VERSION` falls back to the GCS `.log` header.

**Database.** `logs` (hash, files, status, parser version) → `aircraft` (crew, goal, versions, messages, config) →
`flights` (takeoff/landing, index into the paths file) and `setting_changes`.
Independent `flight_records` hold paper provenance and totals, projected into the same logbook read model.
Future telemetry formats can provide that read model without treating paper records as uploaded logs.

**API** (all JSON, login or API key): `POST /api/upload`, `POST /api/logs/check`, `GET /api/logs`,
`GET|DELETE /api/logs/<id>`, `POST /api/logs/<id>/reparse`, `POST /api/reparse-outdated`,
`GET /api/logs/<id>/download/{data,log}`, `GET /api/logbook`, `GET /api/flights/<id>/detail`,
`GET /api/flights/<id>/path`, `GET /api/aircraft/<id>/config`, `GET /api/status`, `GET /api/me`.
