# Flight log database

A web app to upload Paparazzi logs (`.data` + `.log` pairs, `.zip` archives or whole folders). Every log is stored,
parsed by the fast C++ parser (`../cpp`) and summarised in a SQLite database: a logbook of all flights with crew,
goal, takeoff place and flight time, plus a flight page with the path on a map.

- [Try it locally](#try-it-locally)
- [Install on a server](#install-on-a-server)
- [Storage and backup](#storage-and-backup)
- [Settings](#settings)
- [Accounts and TU Delft login](#accounts-and-tu-delft-login)
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

**Flight pages.** The Overview opens first, with takeoff/landing details and log sources folded
away. Autopilot software is shown in Overview with the decoded version, Git commit and base tag links,
and warnings for local (dirty) changes. Multiple reported builds remain visible; raw version messages
can be expanded. Commit links use `paparazzi/paparazzi` and may not resolve for private forks.
Tabs separate the path/charts, weather, settings, messages and pre-flight checks.
Messages and settings show 25 rows per page, scoped to the flight by default; their checkboxes include
the whole log. Tab counts reflect the flight scope (messages also include 30 seconds either side).
Tabs support keyboard arrows and can be linked directly, for example `/flight/42#messages`.

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
| `.data`/`.log` files and flight paths (large) | `LOGDB_LOG_DIR` | local disk or network share | the share's own backup |

A Samba share can be mounted with the service user as owner, in `/etc/fstab`:

    //fileserver/share  /mnt/flight-logs  cifs  credentials=/etc/flight-logs/smb.cred,uid=logdb,gid=logdb,file_mode=0660,dir_mode=0770,_netdev,nofail  0 0

with `LOGDB_LOG_DIR=/mnt/flight-logs/logs` and `LOGDB_BACKUP_DIR=/mnt/flight-logs/backup`. The credentials file has
`username=` and `password=` lines (mode 600).

**Backup.** `app.py backup` copies the database while the app keeps running, checks the copy, and writes `logs.db`,
`secret_key` and `saml/*.pem` to `LOGDB_BACKUP_DIR` (via a temporary file and rename, so it never leaves a half
written copy). It keeps one copy and overwrites it each time; older versions come from the share's snapshots. The
backup contains the SAML private key, so keep that directory private.

**Restore.** Stop the service, copy `logs.db`, `secret_key` and `saml/` from the backup into `/var/lib/flight-logs`
(owner `logdb`, mode 600) and start it again. On a new server `install.sh` offers to do this. Logs uploaded after the
last backup are not in the restored database: run the [upload script](#uploading-with-a-script) again, it only
uploads what the server does not know.

## Settings

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
| `WEB_DATA` | `webui/data` | Database and keys; local disk only |
| `LOG_DIR` | `<WEB_DATA>/logs` | Uploaded logs and flight paths; may be a share |
| `BACKUP_DIR` | (empty) | Destination of `app.py backup` |
| `SAML_ENABLED` | `1` | `0` hides *Sign in with TU Delft* |
| `SAML_METADATA_URL` | TU Delft IdP | Identity provider metadata |
| `SAML_ENTITY_ID` | `<PUBLIC_URL>/saml/metadata` | This service's SAML entity ID |
| `THREADS` | `8` | Gunicorn threads (always one gunicorn process: the parse queue lives in it) |
| `WORKERS` | 2–4, from CPU count | Logs parsed in parallel |
| `INGEST_WORKERS` | `2` | Threads that hash, deduplicate and unzip uploads |
| `PATH_RATE` | `5` | Flight path points per second (`0`: no map path) |
| `FASTREADER` | `../cpp/build/paparazzi_log_parsing` | Parser binary |
| `SESSION_HOURS` | `12` | How long a login lasts |
| `SECRET_KEY` | generated in `<WEB_DATA>/secret_key` | Session signing key |

Relative paths are relative to `webui/`.

## Accounts and TU Delft login

Click your name in the top bar to open **My account**. Local users can change their password
by entering their current password and a new password of at least 10 characters. On **People**, admins
can enable **Edit** and use **Reset password** (or **Set password**) in the Password column to
generate a one-time, 48-hour link for a local account and share it privately.
Generating a link does not change the password until it is used; a new link replaces older links.
SAML/TU Delft passwords remain managed by the identity provider. API keys cannot manage passwords.
Admins can also **Remove password** in the People page's Password column without deleting the profile,
flights or other linked information. This clears unused setup/reset links and prevents new password
sign-ins; existing sessions and API keys are not revoked. **Set password** can restore local sign-in
later. The last administrator's login method cannot be removed.

Everything requires a login. Admins create accounts on the People page or on the command line (on a server prefix
`sudo -u logdb /opt/flight-logs/app/webui/venv/bin/`):

    python3 app.py create-user alice --admin --name "Alice"    # asks for a password (min. 10 characters)
    python3 app.py set-password alice
    python3 app.py list-users
    python3 app.py deactivate-user alice                       # or activate-user

**Sign in with TU Delft** uses SAML 2.0 against `https://login.tudelft.nl/sso/saml2/idp/metadata.php`. Register
`<PUBLIC_URL>/saml/metadata` with TU Delft ICT (`middleware-ictfm@tudelft.nl`) once; the service's signing keys are
generated in `<WEB_DATA>/saml/` and are part of the backup (losing them means registering again). A first SSO login
creates a non-admin account, linked to an existing local account with the same email. Give people access to projects
on the Projects page.

Pilot licences come from the MAVLab Teams personnel list (CSV import on People, in Edit mode) and the
certificate PDFs in the Operations GitHub repo. Importing the list creates an account for each pilot;
they attach to that account when they later sign in with TU Delft. Admins see everyone's qualifications;
other users only see people who share a project with them. Set `LOGDB_OPERATIONS_TOKEN` so the app can
open the PDFs (a fine-grained token with read access to `tudelft/Operations` is enough).

## Uploading with a script

Click your name at the top right, create an API key on the account page (it is shown once) and download
[upload_logs.py](upload_logs.py) there. It needs only Python 3. It searches a folder recursively for `.data`/`.log`
pairs and uploads the ones the server does not have yet (compared by SHA-256):

    python3 upload_logs.py ~/paparazzi/var/logs --server https://logs.example.org --key logdb_...
    python3 upload_logs.py ~/paparazzi/var/logs -n      # dry run

`--server` and `--key` can also be set as `LOGDB_SERVER` and `LOGDB_API_KEY`; add `--insecure` for a self-signed
certificate. The key works on every `/api/...` URL as `Authorization: Bearer <key>` and acts as you.

## How it works

**Uploads.** A `.data` file is paired with the `.log` of the same name in the same folder; other files are ignored.
The upload returns as soon as the files are on disk; hashing, duplicate checks, unzipping and parsing run in the
background. The SHA-256 of the `.data` file identifies a log, so a known log is not stored twice (a missing `.log` is
added later). Files are kept in `<LOG_DIR>/<sha[:2]>/<sha>/` with the flight paths in `paths-<id>.json.gz`; uploads
are staged in `<LOG_DIR>/.incoming`. Each log records its parser version; **Re-parse outdated** re-runs the current
parser on older and failed logs.

**Logbook.** The parser tells onboard SD logs from ground station logs. The logbook (`/logbook`) has one row per
flight and merges the SD and GCS recordings of the same flight: flight data from the SD log, PIC/PAC/GCS operator and
goal from whichever log has them. Named takeoff areas are listed on the Locations page; everyone sees the ones their
flights used, and admins add, change or delete them there in edit mode (an area can be marked indoors; indoor flights
are hidden by default). Logs without a valid date (missing or 1980 clock) are flagged so the
real day can be entered.

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
(case-insensitive) and crew usernames; no accounts, aircraft or places are created by import. Dates are
`YYYY-MM-DD`; `takeoff_time`, when known, is `HH:MM[:SS]` UTC. `kind` is `flight` or `daily`; if blank,
it is inferred from the count. A count of 1 can also be explicitly marked `daily`. All rows are validated
before saving; an invalid row or exact normalized duplicate rejects the entire file with a line error.
Exact duplicate detection also applies to manual entry.

Paper records contribute their flight counts and total duration to the logbook, statistics and entity
overviews, but have no telemetry, synthetic log files, paths, distance or software version. Unknown times
remain date-only. They are never automatically merged with digital logs: check for overlap to avoid
double counting. Referenced aircraft, people, projects and locations cannot be deleted until their paper records
are reassigned or removed.

**Edit mode.** The Edit button in the menu bar shows the forms and menus to change things on the Logbook, People,
Projects, Aircraft and Locations pages; it stays on until you turn it off. With edit mode on, admins also see the names in the
logs that are not yet linked to a person or aircraft, if there are any. With it off, a one-line reminder says how many
are left.

**Overview pages.** Every person, aircraft, project and location has its own page (`/people/<id>`, `/aircraft/<id>`,
`/projects/<id>`, `/locations/<id>`; click a name anywhere). It shows totals (flights, flight time, distance, flying
days, log files) next to a map of takeoff spots, a flight time chart (last year per month by default; the period and
the step from day to year can be changed), breakdowns per aircraft/project/location/crew, and paged lists of flights
and log files. Admins see everything; others only see the pages and the flights and logs they have
access to.

**Real flight or not.** The autopilot's in-flight flag also toggles on the ground (arming, motor tests, carrying).
The parser scores each takeoff/landing on duration, climb, distance, speed, tilt and acceleration and gives a verdict
`flight`, `uncertain` or `ground` with the reasons. The logbook hides the last two unless **Show uncertain/non-flights**
is ticked.

**Flight page** (`/flight/<id>`). Path on a map with the flight plan waypoints, a time slider, altitude/speed chart,
takeoff/landing details, setting changes, messages, pre-flight checks and autopilot version; switch between the SD
and GCS log when both exist.

**Database.** `logs` (hash, files, status, parser version) → `aircraft` (crew, goal, versions, messages, config) →
`flights` (takeoff/landing, index into the paths file) and `setting_changes`.
Independent `flight_records` hold paper provenance and totals, projected into the same logbook read model.
Future telemetry formats can provide that read model without treating paper records as uploaded logs.

**API** (all JSON, login or API key): `POST /api/upload`, `POST /api/logs/check`, `GET /api/logs`,
`GET|DELETE /api/logs/<id>`, `POST /api/logs/<id>/reparse`, `POST /api/reparse-outdated`,
`GET /api/logs/<id>/download/{data,log}`, `GET /api/logbook`, `GET /api/flights/<id>/detail`,
`GET /api/flights/<id>/path`, `GET /api/aircraft/<id>/config`, `GET /api/status`, `GET /api/me`.
