# Flight log database web interface

Upload Paparazzi logs (`.data` + `.log` files, `.zip` archives or whole folders) in the browser. Every log is kept on
disk, parsed with the fast C++ parser (`../cpp`) and stored in a SQLite database.

## Run

    cd ../cpp && mkdir -p build && cd build && cmake -DCMAKE_BUILD_TYPE=Release .. && make   # builds the parser
    cd ../../webui
    pip install -r requirements.txt
    python3 app.py                                                  # http://127.0.0.1:8000

The defaults work for a local test. For other settings see [Settings](#settings).

## Settings

All settings are named `LOGDB_<NAME>`. They are read from the environment first, then from one config file:
`$LOGDB_CONFIG`, else `/etc/flight-logs/logdb.env`, else `webui/logdb.env`. The file has `LOGDB_NAME=value` lines and
`#` comments. The app, gunicorn (through [gunicorn.conf.py](gunicorn.conf.py)) and the command-line tools all read
the same file, so a server needs no other configuration.

[logdb.env.example](logdb.env.example) lists every setting with an explanation; copy it and edit it. To see the
effective values and where each comes from, and to catch typos, a missing parser, an unwritable data directory or an
http/https mismatch, run:

    python3 app.py check-config

| Setting | Default | Meaning |
|---|---|---|
| `LOGDB_PUBLIC_URL` | (empty) | Address users open, e.g. `https://logs.example.org`. Used for the SAML URLs |
| `LOGDB_BIND` | `127.0.0.1:8000` | Address:port gunicorn and `python3 app.py` listen on |
| `LOGDB_THREADS` | `8` | Gunicorn threads (always exactly one gunicorn process) |
| `LOGDB_PROXIES` | `0` | Number of reverse proxies in front of the app; `1` behind nginx/Caddy |
| `LOGDB_WEB_DATA` | `webui/data` | Database and all uploaded logs (back this up) |
| `LOGDB_FASTREADER` | `../cpp/build/paparazzi_log_parsing` | Parser binary |
| `LOGDB_WORKERS` | 2–4 from CPU count | Logs parsed in parallel |
| `LOGDB_INGEST_WORKERS` | `2` | Background hash/dedupe/unzip threads |
| `LOGDB_PATH_RATE` | `5` | Flight path points per second (0 disables the map path) |
| `LOGDB_COOKIE_SECURE` | `0` | `1` when served over https |
| `LOGDB_SESSION_HOURS` | `12` | How long a login lasts |
| `LOGDB_SECRET_KEY` | generated in `WEB_DATA/secret_key` | Session signing key |
| `LOGDB_SAML_ENABLED` | `1` | `0` hides *Sign in with TU Delft* |
| `LOGDB_SAML_METADATA_URL` | TU Delft IdP | Identity provider metadata |
| `LOGDB_SAML_ENTITY_ID` | `<PUBLIC_URL>/saml/metadata` | This service's SAML entity ID |

Relative paths are relative to `webui/`. Typical values per setup:

| Setup | `LOGDB_BIND` | `LOGDB_PROXIES` | `LOGDB_COOKIE_SECURE` | `LOGDB_PUBLIC_URL` |
|---|---|---|---|---|
| Local test | `127.0.0.1:8000` | `0` | `0` | (empty) |
| nginx/Caddy on port 80, http | `127.0.0.1:8000` | `1` | `0` | `http://<server>` |
| nginx/Caddy with https | `127.0.0.1:8000` | `1` | `1` | `https://<server>` |
| gunicorn directly on port 80 | `0.0.0.0:80` | `0` | `0` | `http://<server>` |

The upload script has its own client-side variables, `LOGDB_SERVER` and `LOGDB_API_KEY` (see below).

## Server installation (Ubuntu/Debian)

The C++ parser is a single file without external libraries (the `tinyxml2`/`pprzlink` submodules are not needed),
so only a compiler and CMake are required. Commands assume Ubuntu 22.04/24.04 or Debian 12 and a user with `sudo`.

**1. Packages**

    sudo apt update
    sudo apt install -y git build-essential cmake python3 python3-venv python3-pip

**2. Get the code and build the parser**

    sudo mkdir -p /opt/flight-logs && sudo chown $USER: /opt/flight-logs
    git clone https://github.com/tudelft/paparazzi_log_parsing.git /opt/flight-logs/app
    cd /opt/flight-logs/app/cpp
    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
    cmake --build build -j4
    ./build/paparazzi_log_parsing --version          # should print the parser name and version

Always build `Release`; a Debug build is many times slower.

**3. Python environment**

    cd /opt/flight-logs/app/webui
    python3 -m venv venv
    ./venv/bin/pip install --upgrade pip
    ./venv/bin/pip install -r requirements.txt gunicorn

**4. Service user, storage and settings**

The app runs as its own user. The database and all uploaded logs go in one directory; back it up regularly (stop the
service or use `sqlite3 logs.db ".backup copy.db"`).

    sudo useradd --system --home /opt/flight-logs logdb
    sudo mkdir -p /var/lib/flight-logs && sudo chown logdb: /var/lib/flight-logs
    sudo mkdir -p /etc/flight-logs
    sudo cp logdb.env.example /etc/flight-logs/logdb.env
    sudo chown root:logdb /etc/flight-logs/logdb.env && sudo chmod 640 /etc/flight-logs/logdb.env
    sudoedit /etc/flight-logs/logdb.env

The example is set up for https behind a proxy. Set `LOGDB_PUBLIC_URL` to your address and adjust the values to your
setup (see the table in [Settings](#settings); for plain http set `LOGDB_COOKIE_SECURE=0` and use `http://` in
`LOGDB_PUBLIC_URL`). Then check it, as the service user, and create the first account:

    sudo -u logdb ./venv/bin/python app.py check-config
    sudo -u logdb ./venv/bin/python app.py create-user alice --admin --name "Alice"

**5. Try it**

    sudo -u logdb LOGDB_BIND=0.0.0.0:8000 LOGDB_PROXIES=0 LOGDB_COOKIE_SECURE=0 ./venv/bin/gunicorn

Open `http://<server>:8000` (environment variables override the file, so this works before a proxy is set up). Stop
it with Ctrl+C. Gunicorn takes its settings from [gunicorn.conf.py](gunicorn.conf.py): **one** process with
`LOGDB_THREADS` threads (the parse queue lives inside the process, so more processes would parse logs twice) and no
timeout, because uploads can be large and slow.

**6. Run it as a service**

Create `/etc/systemd/system/flight-logs.service`:

    [Unit]
    Description=Flight log database
    After=network.target

    [Service]
    User=logdb
    WorkingDirectory=/opt/flight-logs/app/webui
    ExecStart=/opt/flight-logs/app/webui/venv/bin/gunicorn
    Restart=on-failure

    [Install]
    WantedBy=multi-user.target

Then:

    sudo systemctl daemon-reload
    sudo systemctl enable --now flight-logs
    systemctl status flight-logs                     # logs: journalctl -u flight-logs -f

All settings come from `/etc/flight-logs/logdb.env`; after changing it run `sudo systemctl restart flight-logs`.
With the default `LOGDB_BIND` gunicorn only listens on localhost; a proxy on port 80 (and 443) makes it reachable,
see the next two sections.

**7. Updating**

    cd /opt/flight-logs/app && git pull
    cmake --build cpp/build -j4
    webui/venv/bin/pip install -r webui/requirements.txt
    sudo -u logdb webui/venv/bin/python webui/app.py check-config   # new settings are listed in logdb.env.example
    sudo systemctl restart flight-logs

If the parser version changed, use **Re-parse outdated** in the upload overview afterwards.

## Port 80 without https

Plain http: passwords and logs travel unencrypted, so only do this on a trusted network (e.g. the university network
or a VPN). Pick one. In `/etc/flight-logs/logdb.env` set `LOGDB_COOKIE_SECURE=0` and
`LOGDB_PUBLIC_URL=http://<server>`, plus `LOGDB_PROXIES=1` for option A or B.

**Option A: nginx on port 80** (keeps gunicorn unprivileged on localhost)

    sudo apt install -y nginx
    sudo rm -f /etc/nginx/sites-enabled/default

Create `/etc/nginx/sites-available/flight-logs`:

    server {
        listen 80 default_server;
        server_name _;
        client_max_body_size 0;               # large log uploads
        proxy_request_buffering off;          # stream uploads to gunicorn
        proxy_read_timeout 3600s;
        location / {
            proxy_pass http://127.0.0.1:8000;
            proxy_set_header Host $host;
            proxy_set_header X-Forwarded-For $remote_addr;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
    }

Then:

    sudo ln -s /etc/nginx/sites-available/flight-logs /etc/nginx/sites-enabled/
    sudo nginx -t && sudo systemctl reload nginx
    sudo ufw allow 80/tcp                      # only if you use ufw

**Option B: Caddy on port 80** (install Caddy as described in the https section below), `/etc/caddy/Caddyfile`:

    :80 {
        reverse_proxy 127.0.0.1:8000 {
            flush_interval -1
        }
    }

then `sudo systemctl reload caddy`.

**Option C: gunicorn directly on port 80** (no proxy). Set `LOGDB_BIND=0.0.0.0:80` and `LOGDB_PROXIES=0` in
`/etc/flight-logs/logdb.env`, add `AmbientCapabilities=CAP_NET_BIND_SERVICE` under `[Service]` in the unit, then
`sudo systemctl daemon-reload && sudo systemctl restart flight-logs`. Uploads then go straight to gunicorn, which is fine
for a small trusted group but less robust against slow or broken clients than a proxy.

## Port 443 with https (recommended)

Complete the server installation above first (gunicorn on `127.0.0.1:8000`). The proxy listens on ports 80/443 and
handles the certificate. You need a DNS name that points at the server (e.g. `logs.example.org`) and ports 80 and 443
reachable from the internet for Let's Encrypt. On an internal network without public access, ask your IT department
for a certificate and use the nginx variant with `ssl_certificate`/`ssl_certificate_key` pointing at it.
Use either the proxy below or one from the previous section, not both.

1. In `/etc/flight-logs/logdb.env` set `LOGDB_PUBLIC_URL=https://<your host>`, `LOGDB_COOKIE_SECURE=1` and
   `LOGDB_PROXIES=1` (the example file already has these), run `check-config`, then
   `sudo systemctl restart flight-logs`.

2. Open the firewall: `sudo ufw allow 80,443/tcp` (if you use ufw).

**Option A: Caddy** (simplest, gets and renews the Let's Encrypt certificate automatically)

    sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
    sudo apt update && sudo apt install caddy

Put this in `/etc/caddy/Caddyfile` (replace the host name; no upload size limit by default):

    logs.example.org {
        encode gzip
        reverse_proxy 127.0.0.1:8000 {
            flush_interval -1
        }
    }

Then `sudo systemctl reload caddy`. Caddy redirects http to https on its own.

**Option B: nginx + certbot**

    sudo apt install -y nginx certbot python3-certbot-nginx

Create `/etc/nginx/sites-available/flight-logs` (replace the host name):

    server {
        listen 80;
        server_name logs.example.org;
        client_max_body_size 0;               # large log uploads
        proxy_request_buffering off;          # stream uploads to gunicorn
        proxy_read_timeout 3600s;
        location / {
            proxy_pass http://127.0.0.1:8000;
            proxy_set_header Host $host;
            proxy_set_header X-Forwarded-For $remote_addr;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
    }

Then:

    sudo ln -s /etc/nginx/sites-available/flight-logs /etc/nginx/sites-enabled/
    sudo nginx -t && sudo systemctl reload nginx
    sudo certbot --nginx -d logs.example.org      # obtains the certificate, adds the https server and the redirect

Certbot installs a timer that renews the certificate; check it with `sudo certbot renew --dry-run`.

With `LOGDB_PROXIES=1` the app takes the client address and https from the proxy's `X-Forwarded-*` headers (used for
the login rate limit and the SAML URLs). Only set it when gunicorn listens on localhost behind a proxy; otherwise
clients could fake those headers.

## API keys and the upload script

Click your name at the top right to open your account page (`/account`) and create an API key. The key is shown once
(only its SHA-256 is stored) and can be revoked there. Use it as `Authorization: Bearer <key>` on any `/api/...` URL;
it acts as you, works for the API only (not for pages), and cannot create or revoke keys.

    curl -H "Authorization: Bearer logdb_..." https://logs.example.org/api/me

`upload_logs.py` (standard library only) searches a folder recursively for `.data`/`.log` pairs, hashes each `.data`
file, asks the server which hashes it already has (`POST /api/logs/check`) and uploads only the rest. A log the server
has without a `.log` is uploaded again so the `.log` is added.

    python3 upload_logs.py ~/paparazzi/var/logs --server https://logs.example.org --key logdb_...
    python3 upload_logs.py ~/paparazzi/var/logs -n          # dry run; server and key can also be set with
                                                            # LOGDB_SERVER / LOGDB_API_KEY

Add `--insecure` for a server with a self-signed certificate. Parsing happens on the server after the upload.

## Accounts

Everything (pages, API, downloads) requires a login. Accounts are local for now and managed on the command line:

    python3 app.py create-user alice --admin --name "Alice"   # asks for a password (min. 10 characters)
    python3 app.py create-user bob
    python3 app.py set-password bob
    python3 app.py list-users
    python3 app.py deactivate-user bob / activate-user bob

Passwords are stored hashed, sessions last 12 hours (`LOGDB_SESSION_HOURS`), repeated failed logins are rate limited.
Serve it over https and set `LOGDB_COOKIE_SECURE=1`. On a server, run these commands as the service user
(`sudo -u logdb ./venv/bin/python app.py ...`) so they use `/etc/flight-logs/logdb.env`. Logs can be deleted by their uploader or an admin.

### TU Delft SAML 2.0 (NetID)

The sign-in page also offers **Sign in with TU Delft**. That uses SAML 2.0 against the TU Delft identity provider.

- **IdP metadata URL** (also the IdP entity ID): `https://login.tudelft.nl/sso/saml2/idp/metadata.php`
- Override with `LOGDB_SAML_METADATA_URL` if needed. Set `LOGDB_SAML_ENABLED=0` to hide the button.
- This app’s service-provider metadata is at `GET /saml/metadata`. Register that with TU Delft ICT (`middleware-ictfm@tudelft.nl`) before the button will complete a live login. Assertion consumer service: `POST /saml/acs`.
- Set `LOGDB_PUBLIC_URL` so the metadata contains the public https address. Optional: `LOGDB_SAML_ENTITY_ID` (defaults to `<PUBLIC_URL>/saml/metadata`). SP signing certs are generated in `data/saml/` (not in git).
- First SSO login creates a non-admin account (matched later by the SAML NameID, or by email if that person already has a local user). Give them project access on Admin → Projects.

## Behaviour

- `.data` files are paired with the `.log` of the same name (same folder / zip directory). Other files are ignored.
- Upload returns as soon as the files are on disk. SHA-256 fingerprinting, duplicate checks and parsing run in
  background workers (`LOGDB_INGEST_WORKERS`, `LOGDB_WORKERS`). Zips are unpacked there too, not during the HTTP request.
- The SHA-256 of the `.data` file identifies a log. A log that is already known is not stored or parsed again.
  (If the first upload had no `.log` and a later one has, the `.log` is added and the log is re-parsed.)
  The hash is passed to the parser (`--sha256`) and is also part of its JSON output (`log.sha256`).
- The Uploads page lists the newest 100 logs. `GET /api/logs?limit=100&offset=0` (max limit 200) returns
  `{logs, total, limit, offset}`.
- Each parsed log records which parser produced it (`parser_name`, `parser_version`, `parser_path_rate_hz`).
  After installing a newer parser (bump `PARSER_VERSION` in `cpp/fastreader.cpp`) use **Re-parse outdated**
  (`POST /api/reparse-outdated`) to re-run it on every log with another version, and on failed logs.
- Files are stored under `data/logs/<sha[:2]>/<sha>/`, the database is `data/logs.db`.

## Database

`logs` (hash, files, status, parser version) -> `aircraft` (crew, goal, versions, messages, config) ->
`flights` (takeoff/landing, gzip-compressed path) and `setting_changes` (settings changed from their default).

## API

`POST /api/upload` (returns `accepted` immediately), `GET /api/logs` (`{logs, total, limit, offset}`), `GET /api/logs/<id>`, `GET /api/aircraft/<id>/config`,
`GET /api/flights/<id>/path`, `GET /api/logs/<id>/download/{data,log}`, `POST /api/logs/<id>/reparse`,
`POST /api/reparse-outdated`, `DELETE /api/logs/<id>`, `GET /api/status`.

## Log types and the logbook

The parser detects whether a log is an onboard SD log (`onboard_sd`) or a ground station log (`gcs`) and reports it as
`log.type` / `log.type_basis` (file name `_SD`, `.log` header, or ground-only messages such as `LINK_REPORT`/`PONG`).
The logbook (`/logbook`) lists one row per flight and merges the SD and GCS recordings of the same flight (same
aircraft, overlapping takeoff-landing interval, 60 s slack) into one row: flight data come from the SD log, PIC/PAC/GCS
and goal from whichever log has them. Each row shows its sources and offers a download for each log. Admins manage
aliases (names linked to users) and named takeoff/landing areas drawn on a map at `/admin`. A named area can be
marked as indoors; the logbook hides indoor flights unless **Show indoor flights** is ticked, and combines
flights within 1 km by default.

## Real flight or not

The autopilot's "in flight" flag also toggles for short arming on the ground, motor tests or hand-carrying. For each
takeoff/landing the parser therefore adds `assessment` (`verdict`: `flight`, `uncertain` or `ground`, plus `score`,
`reasons` and the raw `metrics`). It is a score built from the duration, altitude change (INS, else barometer, else GPS),
horizontal movement, speed, tilt and the dynamic INS acceleration, so indoor and tethered flights (little horizontal
movement) still count when they climb and accelerate. Flights under 2 s can never be `flight`. If the motors were off for most of the
flight (`motors_on` in ROTORCRAFT_STATUS) it is always `ground`. The logbook hides `ground` and `uncertain` verdicts unless
**Show uncertain/non-flights** is ticked, marks them in the list and shows the reasons in the details. Re-parse existing logs (parser version 1.2.2) to get the verdicts.

## Flight page

Click a logbook row and choose *Open flight page* (`/flight/<id>`). It shows the flight path on a map (with a *Show flight plan* checkbox for the waypoints), a time slider and altitude/speed chart, takeoff/landing details, the settings changed during the flight, the text messages around it, the pre-flight checks and the autopilot version. When both an SD and a GCS log exist, you can switch between them. Data comes from `GET /api/flights/<id>/detail`. The path needs a non-zero `LOGDB_PATH_RATE`.
