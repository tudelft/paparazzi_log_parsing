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

## Server installation (Ubuntu/Debian)

The C++ parser is a single file without external libraries (the `tinyxml2`/`pprzlink` submodules are not needed),
so only a compiler and CMake are required. Commands assume Ubuntu 22.04/24.04 or Debian 12 and a user with `sudo`.

**1. Packages**

    sudo apt update
    sudo apt install -y git build-essential cmake python3 python3-venv python3-pip

**2. Get the code and build the parser**

    sudo mkdir -p /opt/pprz-logs && sudo chown $USER: /opt/pprz-logs
    git clone https://github.com/tudelft/paparazzi_log_parsing.git /opt/pprz-logs/app
    cd /opt/pprz-logs/app/cpp
    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
    cmake --build build -j4
    ./build/paparazzi_log_parsing --version          # should print the parser name and version

Always build `Release`; a Debug build is many times slower.

**3. Python environment**

    cd /opt/pprz-logs/app/webui
    python3 -m venv venv
    ./venv/bin/pip install --upgrade pip
    ./venv/bin/pip install -r requirements.txt gunicorn

**4. Storage and first account**

The database and all uploaded logs go in one directory; back it up regularly (stop the service or use
`sqlite3 logs.db ".backup copy.db"`).

    sudo mkdir -p /var/lib/pprz-logs && sudo chown $USER: /var/lib/pprz-logs
    PPRZ_WEB_DATA=/var/lib/pprz-logs ./venv/bin/python app.py create-user alice --admin --name "Alice"

**5. Try it**

    PPRZ_WEB_DATA=/var/lib/pprz-logs ./venv/bin/gunicorn -w 1 --threads 8 -t 0 -b 0.0.0.0:8000 app:app

Open `http://<server>:8000`. Use **one** gunicorn process with several threads: the parse queue and its worker
threads live inside the process, so multiple worker processes would each re-queue and parse the same logs. The
timeout is disabled (`-t 0`) because uploads can be large and slow. Stop it with Ctrl+C.

**6. Run it as a service**

Create a dedicated user and give it the data directory:

    sudo useradd --system --home /opt/pprz-logs pprz
    sudo chown -R pprz: /var/lib/pprz-logs

Create `/etc/systemd/system/pprz-logs.service`:

    [Unit]
    Description=Paparazzi log database
    After=network.target

    [Service]
    User=pprz
    WorkingDirectory=/opt/pprz-logs/app/webui
    Environment=PPRZ_WEB_DATA=/var/lib/pprz-logs
    Environment=PPRZ_COOKIE_SECURE=0
    ExecStart=/opt/pprz-logs/app/webui/venv/bin/gunicorn -w 1 --threads 8 -t 0 -b 127.0.0.1:8000 app:app
    Restart=on-failure

    [Install]
    WantedBy=multi-user.target

Then:

    sudo systemctl daemon-reload
    sudo systemctl enable --now pprz-logs
    systemctl status pprz-logs                     # logs: journalctl -u pprz-logs -f

(`PPRZ_COOKIE_SECURE=0` is for plain http; with https set it to `1`.) Gunicorn only listens on localhost; a proxy on
port 80 (and 443) makes it reachable, see the next two sections.

**7. Updating**

    cd /opt/pprz-logs/app && git pull
    cmake --build cpp/build -j4
    webui/venv/bin/pip install -r webui/requirements.txt
    sudo systemctl restart pprz-logs

If the parser version changed, use **Re-parse outdated** in the upload overview afterwards.

## Port 80 without https

Plain http: passwords and logs travel unencrypted, so only do this on a trusted network (e.g. the university network
or a VPN). Pick one.

**Option A: nginx on port 80** (keeps gunicorn unprivileged on localhost)

    sudo apt install -y nginx
    sudo rm -f /etc/nginx/sites-enabled/default

Create `/etc/nginx/sites-available/pprz-logs`:

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

    sudo ln -s /etc/nginx/sites-available/pprz-logs /etc/nginx/sites-enabled/
    sudo nginx -t && sudo systemctl reload nginx
    sudo ufw allow 80/tcp                      # only if you use ufw

**Option B: Caddy on port 80** (install Caddy as described in the https section below), `/etc/caddy/Caddyfile`:

    :80 {
        reverse_proxy 127.0.0.1:8000 {
            flush_interval -1
        }
    }

then `sudo systemctl reload caddy`.

**Option C: gunicorn directly on port 80** (no proxy). In the unit above, change `-b 127.0.0.1:8000` to
`-b 0.0.0.0:80` and add `AmbientCapabilities=CAP_NET_BIND_SERVICE` under `[Service]`, then
`sudo systemctl daemon-reload && sudo systemctl restart pprz-logs`. Uploads then go straight to gunicorn, which is fine
for a small trusted group but less robust against slow or broken clients than a proxy.

## Port 443 with https (recommended)

Complete the server installation above first (gunicorn on `127.0.0.1:8000`). The proxy listens on ports 80/443 and
handles the certificate. You need a DNS name that points at the server (e.g. `logs.example.org`) and ports 80 and 443
reachable from the internet for Let's Encrypt. On an internal network without public access, ask your IT department
for a certificate and use the nginx variant with `ssl_certificate`/`ssl_certificate_key` pointing at it.
Use either the proxy below or one from the previous section, not both.

1. Enable secure cookies: in `/etc/systemd/system/pprz-logs.service` set `Environment=PPRZ_COOKIE_SECURE=1`, then
   `sudo systemctl daemon-reload && sudo systemctl restart pprz-logs`.

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

Create `/etc/nginx/sites-available/pprz-logs` (replace the host name):

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

    sudo ln -s /etc/nginx/sites-available/pprz-logs /etc/nginx/sites-enabled/
    sudo nginx -t && sudo systemctl reload nginx
    sudo certbot --nginx -d logs.example.org      # obtains the certificate, adds the https server and the redirect

Certbot installs a timer that renews the certificate; check it with `sudo certbot renew --dry-run`.

Behind a proxy the app sees every request as coming from 127.0.0.1, so the login rate limit is applied per user name
instead of per client address.

## Accounts

Everything (pages, API, downloads) requires a login. Accounts are local for now and managed on the command line:

    python3 app.py create-user alice --admin --name "Alice"   # asks for a password (min. 10 characters)
    python3 app.py create-user bob
    python3 app.py set-password bob
    python3 app.py list-users
    python3 app.py deactivate-user bob / activate-user bob

Passwords are stored hashed, sessions last 12 hours (`PPRZ_SESSION_HOURS`), repeated failed logins are rate limited.
Serve it over https and set `PPRZ_COOKIE_SECURE=1`. Logs can be deleted by their uploader or an admin.

### TU Delft SAML 2.0 (NetID)

The sign-in page also offers **Sign in with TU Delft**. That uses SAML 2.0 against the TU Delft identity provider.

- **IdP metadata URL** (also the IdP entity ID): `https://login.tudelft.nl/sso/saml2/idp/metadata.php`
- Override with `PPRZ_SAML_METADATA_URL` if needed. Set `PPRZ_SAML_ENABLED=0` to hide the button.
- This app’s service-provider metadata is at `GET /saml/metadata`. Register that with TU Delft ICT (`middleware-ictfm@tudelft.nl`) before the button will complete a live login. Assertion consumer service: `POST /saml/acs`.
- Optional: `PPRZ_SAML_ENTITY_ID` (defaults to `<site>/saml/metadata`). SP signing certs are generated in `data/saml/` (not in git).
- First SSO login creates a non-admin account (matched later by the SAML NameID, or by email if that person already has a local user). Give them project access on Admin → Projects.

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

Click a logbook row and choose *Open flight page* (`/flight/<id>`). It shows the flight path on a map (with a *Show flight plan* checkbox for the waypoints), a time slider and altitude/speed chart, takeoff/landing details, the settings changed during the flight, the text messages around it, the pre-flight checks and the autopilot version. When both an SD and a GCS log exist, you can switch between them. Data comes from `GET /api/flights/<id>/detail`. The path needs a non-zero `PPRZ_PATH_RATE`.
