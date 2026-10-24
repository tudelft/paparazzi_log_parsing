#!/usr/bin/env bash
# Install or update the flight log database on Ubuntu/Debian.
#
#   sudo bash install.sh               first install, or update an existing install (keeps the settings)
#   sudo bash install.sh --reconfigure update and answer all questions again
#
# Layout:
#   /opt/flight-logs/app            code (git checkout), parser build and Python venv, owned by root
#   /etc/flight-logs/logdb.env      settings (see logdb.env.example); install.conf holds the installer's own answers
#   /var/lib/flight-logs            local data: SQLite database, session key, SAML keys
#   <log dir>                       uploaded logs and flight paths, e.g. on a mounted Samba share
#   <backup dir>                    copy of the database and keys, written by flight-logs-backup.timer
set -euo pipefail

APP_ROOT=/opt/flight-logs
APP=$APP_ROOT/app
WEBUI=$APP/webui
VENV=$WEBUI/venv
ETC=/etc/flight-logs
ENV_FILE=$ETC/logdb.env
CONF=$ETC/install.conf
WEB_DATA=/var/lib/flight-logs
SVC_USER=logdb
PORT=8000
DEFAULT_REPO=https://github.com/tudelft/paparazzi_log_parsing.git

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33mWarning: %s\033[0m\n' "$*" >&2; }
die() { printf '\033[31mError: %s\033[0m\n' "$*" >&2; exit 1; }

# ask VAR "question" [default]
ask() {
    local answer
    read -r -p "$2${3:+ [$3]}: " answer </dev/tty
    printf -v "$1" '%s' "${answer:-${3:-}}"
}

# ask_yn "question" y|n  -> exit status 0 for yes
ask_yn() {
    local answer
    while true; do
        read -r -p "$1 [$( [ "$2" = y ] && echo Y/n || echo y/N )]: " answer </dev/tty
        case "${answer:-$2}" in
            [Yy]*) return 0 ;;
            [Nn]*) return 1 ;;
        esac
    done
}

# ask_choice VAR "question" default option...
ask_choice() {
    local var=$1 question=$2 default=$3 i answer
    shift 3
    echo "$question"
    for i in $(seq 1 $#); do echo "  $i) ${!i}"; done
    while true; do
        read -r -p "Choice [$default]: " answer </dev/tty
        answer=${answer:-$default}
        if [[ $answer =~ ^[0-9]+$ ]] && [ "$answer" -ge 1 ] && [ "$answer" -le $# ]; then
            printf -v "$var" '%s' "$answer"
            return
        fi
    done
}

# Value of LOGDB_<name> in the settings file (empty when missing or commented out)
get_setting() {
    [ -f "$ENV_FILE" ] || return 0
    sed -n "s/^LOGDB_$1=//p" "$ENV_FILE" | tail -n 1
}

# Set LOGDB_<name>=value in the settings file, replacing an active or commented-out line; other lines are kept
set_setting() {
    local tmp
    tmp=$(mktemp)
    awk -v key="LOGDB_$1" -v val="$2" '
        BEGIN { done = 0 }
        !done && ($0 ~ "^" key "=" || $0 ~ "^#[ ]*" key "=") { print key "=" val; done = 1; next }
        $0 ~ "^" key "=" { next }
        { print }
        END { if (!done) print key "=" val }
    ' "$ENV_FILE" >"$tmp"
    cat "$tmp" >"$ENV_FILE"
    rm -f "$tmp"
}

as_user() { sudo -u "$SVC_USER" -- "$@"; }
app_cmd() { (cd "$WEBUI" && as_user "$VENV/bin/python" app.py "$@"); }

[ "$(id -u)" -eq 0 ] || die "run this script with sudo"
# shellcheck disable=SC1091
[ -r /etc/os-release ] && . /etc/os-release
case "${ID:-}${ID_LIKE:-}" in
    *debian*|*ubuntu*) ;;
    *) die "this script supports Ubuntu/Debian only" ;;
esac

RECONFIGURE=0
[ "${1:-}" = "--reconfigure" ] && RECONFIGURE=1

# Answers of a previous run
REPO=$DEFAULT_REPO BRANCH=master PROXY=1 HOSTNAME_PUBLIC=$(hostname -f 2>/dev/null || hostname) CERT='' KEY='' BACKUP_SCHEDULE=hourly
# shellcheck disable=SC1090
[ -f "$CONF" ] && . "$CONF"
FIRST_RUN=1
[ -f "$CONF" ] && [ -f "$ENV_FILE" ] && FIRST_RUN=0
CONFIGURE=$(( FIRST_RUN || RECONFIGURE ))

if [ "$FIRST_RUN" -eq 1 ]; then
    say "Installing the flight log database in $APP_ROOT"
else
    say "Updating the flight log database in $APP_ROOT"
    [ "$CONFIGURE" -eq 1 ] || echo "Keeping the current settings (run with --reconfigure to change them)."
fi

# ---------------------------------------------------------------- questions
if [ "$CONFIGURE" -eq 1 ]; then
    say "Code"
    ask REPO "Git repository" "$REPO"
    ask BRANCH "Branch" "$BRANCH"

    say "Web address"
    ask HOSTNAME_PUBLIC "Host name users open (DNS name of this server, without http://)" "$HOSTNAME_PUBLIC"

    say "Web server / https"
    ask_choice PROXY "How should the site be served?" "$PROXY" \
        "https with an automatic Let's Encrypt certificate (Caddy; needs ports 80/443 reachable from the internet)" \
        "https with your own certificate files (nginx), e.g. from your IT department" \
        "plain http on port 80 (nginx; only on a trusted network)" \
        "no web server: gunicorn on 127.0.0.1:$PORT, I set up a proxy myself"
    if [ "$PROXY" -eq 2 ]; then
        while true; do
            ask CERT "Certificate file (PEM, including the intermediate chain)" "$CERT"
            ask KEY "Private key file (PEM)" "$KEY"
            if [ ! -r "$CERT" ] || [ ! -r "$KEY" ]; then
                warn "cannot read $CERT or $KEY"
            elif [ "$(openssl x509 -noout -pubkey -in "$CERT" 2>/dev/null | sha256sum)" != \
                   "$(openssl pkey -pubout -in "$KEY" 2>/dev/null | sha256sum)" ]; then
                warn "the key does not belong to the certificate (or one of them is not PEM)"
            else
                openssl x509 -noout -subject -enddate -in "$CERT"
                break
            fi
        done
        echo "The files are used where they are; after renewing them run: sudo systemctl reload nginx"
    fi
    case $PROXY in
        1|2) SCHEME=https ;;
        *) SCHEME=http ;;
    esac
    PUBLIC_URL=$SCHEME://$HOSTNAME_PUBLIC
    [ "$PROXY" -eq 4 ] && PUBLIC_URL=$(get_setting PUBLIC_URL) && ask PUBLIC_URL "Address users open (e.g. https://$HOSTNAME_PUBLIC)" "${PUBLIC_URL:-https://$HOSTNAME_PUBLIC}"

    say "Storage"
    echo "Uploaded logs (large) and the database backup can go on a network share, e.g. a Samba share mounted"
    echo "via /etc/fstab. The database itself stays in $WEB_DATA on this server's disk."
    echo "Mount the share before continuing, with the service user '$SVC_USER' as owner, e.g. in /etc/fstab:"
    echo "  //fileserver/share /mnt/flight-logs cifs credentials=/etc/flight-logs/smb.cred,uid=$SVC_USER,gid=$SVC_USER,file_mode=0660,dir_mode=0770,_netdev,nofail 0 0"
    LOG_DIR=$(get_setting LOG_DIR)
    BACKUP_DIR=$(get_setting BACKUP_DIR)
    ask LOG_DIR "Directory for the uploaded logs" "${LOG_DIR:-/mnt/flight-logs/logs}"
    ask BACKUP_DIR "Directory for the database backup (empty: no backup)" "${BACKUP_DIR:-/mnt/flight-logs/backup}"
    if [ -n "$BACKUP_DIR" ]; then
        echo "Backups use UTC date-time folders; the latest 14 complete backup sets are retained."
        ask BACKUP_SCHEDULE "How often (systemd OnCalendar value: hourly, daily, *-*-* 02:00, ...)" "$BACKUP_SCHEDULE"
        systemd-analyze calendar "$BACKUP_SCHEDULE" >/dev/null 2>&1 || die "'$BACKUP_SCHEDULE' is not a valid OnCalendar value"
    fi

    say "TU Delft login (SAML)"
    SAML_ENABLED=$(get_setting SAML_ENABLED)
    if ask_yn "Offer 'Sign in with TU Delft'?" "$( [ "${SAML_ENABLED:-1}" = 0 ] && echo n || echo y )"; then
        SAML_ENABLED=1
        SAML_METADATA_URL=$(get_setting SAML_METADATA_URL)
        SAML_ENTITY_ID=$(get_setting SAML_ENTITY_ID)
        ask SAML_METADATA_URL "Identity provider metadata URL" "${SAML_METADATA_URL:-https://login.tudelft.nl/sso/saml2/idp/metadata.php}"
        ask SAML_ENTITY_ID "Entity ID of this service" "${SAML_ENTITY_ID:-$PUBLIC_URL/saml/metadata}"
    else
        SAML_ENABLED=0
    fi
fi

# ---------------------------------------------------------------- packages
say "Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
PKGS=(git build-essential cmake python3 python3-venv python3-pip sqlite3 openssl ca-certificates sudo curl)
case $PROXY in
    2|3) PKGS+=(nginx) ;;
esac
apt-get install -y -q "${PKGS[@]}"
if [ "$PROXY" -eq 1 ] && ! command -v caddy >/dev/null; then
    if ! apt-cache policy caddy 2>/dev/null | grep -q 'Candidate: [0-9]'; then
        apt-get install -y -q debian-keyring debian-archive-keyring apt-transport-https curl gnupg
        curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
        curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt >/etc/apt/sources.list.d/caddy-stable.list
        apt-get update -q
    fi
    apt-get install -y -q caddy
fi

# ---------------------------------------------------------------- user and directories
id "$SVC_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_ROOT" --shell /usr/sbin/nologin "$SVC_USER"
mkdir -p "$APP_ROOT" "$ETC"
install -d -o "$SVC_USER" -g "$SVC_USER" -m 750 "$WEB_DATA"

# ---------------------------------------------------------------- code, parser, Python
say "Getting the code"
if [ -d "$APP/.git" ]; then
    git -C "$APP" remote set-url origin "$REPO"
    git -C "$APP" fetch -q origin "$BRANCH"
    git -C "$APP" checkout -q "$BRANCH"
    git -C "$APP" merge -q --ff-only "origin/$BRANCH" || die "local changes in $APP; resolve them or reset with: git -C $APP reset --hard origin/$BRANCH"
else
    git clone -q --branch "$BRANCH" "$REPO" "$APP"
fi
git -C "$APP" log -1 --format='Version: %h %s (%cd)' --date=short

# Continue with the installer from the updated code, so a newer script also writes newer service files
SELF=$(readlink -f "$0" 2>/dev/null || true)
if [ -z "${LOGDB_INSTALL_REEXEC:-}" ] && [ -f "$WEBUI/install.sh" ] && [ "$SELF" != "$WEBUI/install.sh" ] \
   && ! cmp -s "$SELF" "$WEBUI/install.sh"; then
    if [ "$CONFIGURE" -eq 1 ]; then
        echo "Note: the downloaded installer differs from $WEBUI/install.sh; using the answers above with this copy."
    else
        echo "Continuing with the updated installer from the repository."
        LOGDB_INSTALL_REEXEC=1 exec bash "$WEBUI/install.sh" "$@"
    fi
fi

say "Building the parser"
cmake -S "$APP/cpp" -B "$APP/cpp/build" -DCMAKE_BUILD_TYPE=Release >/dev/null
cmake --build "$APP/cpp/build" -j "$(nproc)" >/dev/null
"$APP/cpp/build/paparazzi_log_parsing" --version

say "Python environment"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$WEBUI/requirements.txt" gunicorn

# ---------------------------------------------------------------- settings
if [ "$CONFIGURE" -eq 1 ]; then
    say "Writing $ENV_FILE"
    [ -f "$ENV_FILE" ] || cp "$WEBUI/logdb.env.example" "$ENV_FILE"
    set_setting PUBLIC_URL "$PUBLIC_URL"
    set_setting BIND "127.0.0.1:$PORT"
    set_setting PROXIES 1
    set_setting COOKIE_SECURE "$( [[ $PUBLIC_URL == https://* ]] && echo 1 || echo 0 )"
    set_setting WEB_DATA "$WEB_DATA"
    set_setting LOG_DIR "$LOG_DIR"
    set_setting BACKUP_DIR "$BACKUP_DIR"
    set_setting SAML_ENABLED "$SAML_ENABLED"
    if [ "$SAML_ENABLED" = 1 ]; then
        set_setting SAML_METADATA_URL "$SAML_METADATA_URL"
        set_setting SAML_ENTITY_ID "$SAML_ENTITY_ID"
    fi
    cat >"$CONF" <<EOF
# Answers of install.sh that are not app settings (those are in logdb.env)
REPO='$REPO'
BRANCH='$BRANCH'
PROXY=$PROXY
HOSTNAME_PUBLIC='$HOSTNAME_PUBLIC'
CERT='$CERT'
KEY='$KEY'
BACKUP_SCHEDULE='$BACKUP_SCHEDULE'
EOF
fi
chown root:"$SVC_USER" "$ENV_FILE" && chmod 640 "$ENV_FILE"
chmod 644 "$CONF"
LOG_DIR=$(get_setting LOG_DIR)
BACKUP_DIR=$(get_setting BACKUP_DIR)
LOG_DIR=${LOG_DIR:-$WEB_DATA/logs}

# The log and backup directories must exist and be writable by the service user
for dir in "$LOG_DIR" ${BACKUP_DIR:+"$BACKUP_DIR"}; do
    while true; do
        mkdir -p "$dir" 2>/dev/null && chown "$SVC_USER:" "$dir" 2>/dev/null || true
        if as_user test -w "$dir"; then
            echo "$dir: $(findmnt -no FSTYPE,SOURCE -T "$dir" | tr -s ' ') (writable by $SVC_USER)"
            break
        fi
        warn "$dir is not writable by $SVC_USER. Mount the share with uid=$SVC_USER,gid=$SVC_USER (or fix the permissions)."
        ask_yn "Try again?" y || die "$dir is not writable by $SVC_USER"
    done
done

# ---------------------------------------------------------------- restore
RESTORE_DIR=""
if [ -n "$BACKUP_DIR" ]; then
    [ -f "$BACKUP_DIR/logs.db" ] && RESTORE_DIR="$BACKUP_DIR"
    for snapshot in "$BACKUP_DIR"/backup-????-??-??T??-??-??.??????Z; do
        if [ -d "$snapshot" ] && [ ! -L "$snapshot" ] && [ -f "$snapshot/logs.db" ]; then
            RESTORE_DIR="$snapshot"
        fi
    done
fi
if [ ! -f "$WEB_DATA/logs.db" ] && [ -n "$RESTORE_DIR" ]; then
    say "Database backup found"
    ls -l "$RESTORE_DIR/logs.db"
    if ask_yn "There is no database yet. Restore it from $RESTORE_DIR?" y; then
        install -o "$SVC_USER" -g "$SVC_USER" -m 600 "$RESTORE_DIR/logs.db" "$WEB_DATA/logs.db"
        [ -f "$RESTORE_DIR/secret_key" ] && install -o "$SVC_USER" -g "$SVC_USER" -m 600 "$RESTORE_DIR/secret_key" "$WEB_DATA/secret_key"
        if [ -d "$RESTORE_DIR/saml" ]; then
            install -d -o "$SVC_USER" -g "$SVC_USER" -m 700 "$WEB_DATA/saml"
            for pem in "$RESTORE_DIR"/saml/*.pem; do
                [ -f "$pem" ] && install -o "$SVC_USER" -g "$SVC_USER" -m 600 "$pem" "$WEB_DATA/saml/"
            done
        fi
        [ "$(sqlite3 "$WEB_DATA/logs.db" 'PRAGMA integrity_check')" = ok ] || die "the restored database is damaged"
        echo "Restored."
    fi
fi

say "Checking the settings"
app_cmd check-config || die "fix the settings in $ENV_FILE and run this script again"

# ---------------------------------------------------------------- services
say "Installing the services"
MOUNTS="$LOG_DIR${BACKUP_DIR:+ $BACKUP_DIR}"
cat >/etc/systemd/system/flight-logs.service <<EOF
[Unit]
Description=Flight log database
After=network-online.target remote-fs.target
Wants=network-online.target
RequiresMountsFor=$MOUNTS

[Service]
User=$SVC_USER
Group=$SVC_USER
WorkingDirectory=$WEBUI
Environment=LOGDB_CONFIG=$ENV_FILE
ExecStart=$VENV/bin/gunicorn
Restart=on-failure
RestartSec=5
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
EOF

if [ -n "$BACKUP_DIR" ]; then
    cat >/etc/systemd/system/flight-logs-backup.service <<EOF
[Unit]
Description=Back up the flight log database to $BACKUP_DIR
RequiresMountsFor=$MOUNTS

[Service]
Type=oneshot
User=$SVC_USER
Group=$SVC_USER
WorkingDirectory=$WEBUI
Environment=LOGDB_CONFIG=$ENV_FILE
ExecStart=$VENV/bin/python app.py backup
Nice=10
IOSchedulingClass=idle
EOF
    cat >/etc/systemd/system/flight-logs-backup.timer <<EOF
[Unit]
Description=Back up the flight log database ($BACKUP_SCHEDULE)

[Timer]
OnCalendar=$BACKUP_SCHEDULE
RandomizedDelaySec=120
Persistent=true

[Install]
WantedBy=timers.target
EOF
else
    systemctl disable --now flight-logs-backup.timer 2>/dev/null || true
    rm -f /etc/systemd/system/flight-logs-backup.service /etc/systemd/system/flight-logs-backup.timer
fi

NGINX_SITE=/etc/nginx/sites-available/flight-logs
nginx_proxy() {
    cat <<EOF
        client_max_body_size 0;
        proxy_request_buffering off;
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        location / {
            proxy_pass http://127.0.0.1:$PORT;
            proxy_set_header Host \$host;
            proxy_set_header X-Forwarded-For \$remote_addr;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
EOF
}
case $PROXY in
    1)
        [ -f /etc/caddy/Caddyfile ] && [ ! -f /etc/caddy/Caddyfile.orig ] && cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.orig
        cat >/etc/caddy/Caddyfile <<EOF
# Written by $WEBUI/install.sh
$HOSTNAME_PUBLIC {
    encode gzip
    reverse_proxy 127.0.0.1:$PORT {
        flush_interval -1
    }
}
EOF
        systemctl disable --now nginx 2>/dev/null || true
        ;;
    2)
        {
            echo "# Written by $WEBUI/install.sh"
            echo "server {"
            echo "    listen 80;"
            echo "    listen [::]:80;"
            echo "    server_name $HOSTNAME_PUBLIC;"
            echo "    return 301 https://\$host\$request_uri;"
            echo "}"
            echo "server {"
            echo "    listen 443 ssl http2;"
            echo "    listen [::]:443 ssl http2;"
            echo "    server_name $HOSTNAME_PUBLIC;"
            echo "    ssl_certificate $CERT;"
            echo "    ssl_certificate_key $KEY;"
            echo "    ssl_protocols TLSv1.2 TLSv1.3;"
            echo "    add_header Strict-Transport-Security \"max-age=31536000\" always;"
            nginx_proxy
            echo "}"
        } >"$NGINX_SITE"
        ;;
    3)
        {
            echo "# Written by $WEBUI/install.sh"
            echo "server {"
            echo "    listen 80 default_server;"
            echo "    listen [::]:80 default_server;"
            echo "    server_name _;"
            nginx_proxy
            echo "}"
        } >"$NGINX_SITE"
        ;;
esac
if [ "$PROXY" -eq 2 ] || [ "$PROXY" -eq 3 ]; then
    rm -f /etc/nginx/sites-enabled/default
    ln -sf "$NGINX_SITE" /etc/nginx/sites-enabled/flight-logs
    nginx -t -q || die "nginx rejected $NGINX_SITE"
    systemctl disable --now caddy 2>/dev/null || true
fi

systemctl daemon-reload
systemctl enable -q flight-logs
systemctl restart flight-logs
[ -n "$BACKUP_DIR" ] && systemctl enable -q --now flight-logs-backup.timer
case $PROXY in
    1) systemctl enable -q caddy && systemctl reload-or-restart caddy ;;
    2|3) systemctl enable -q nginx && systemctl reload-or-restart nginx ;;
esac
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
    case $PROXY in
        1|2) ufw allow 80,443/tcp >/dev/null ;;
        3) ufw allow 80/tcp >/dev/null ;;
    esac
fi

# The service needs a moment to open the database
for _ in $(seq 1 20); do
    curl -fs -o /dev/null "http://127.0.0.1:$PORT/login" && break
    sleep 1
done
curl -fs -o /dev/null "http://127.0.0.1:$PORT/login" || die "the service does not answer; see: journalctl -u flight-logs -e"

# ---------------------------------------------------------------- first account
if [ -z "$(app_cmd list-users)" ]; then
    say "First administrator account"
    ask ADMIN "User name" "admin"
    ask ADMIN_NAME "Full name (optional)" ""
    app_cmd create-user "$ADMIN" --admin ${ADMIN_NAME:+--name "$ADMIN_NAME"}
fi

if [ -n "$BACKUP_DIR" ] && [ -z "$RESTORE_DIR" ]; then
    say "First backup"
    systemctl start flight-logs-backup.service && ls -l "$BACKUP_DIR"
fi

say "Done"
PUBLIC_URL=$(get_setting PUBLIC_URL)
echo "Site:      $PUBLIC_URL"
echo "Status:    systemctl status flight-logs      logs: journalctl -u flight-logs -f"
echo "Settings:  $ENV_FILE  (after editing: sudo systemctl restart flight-logs)"
[ -n "$BACKUP_DIR" ] && echo "Backup:    $BACKUP_DIR ($BACKUP_SCHEDULE)   now: sudo systemctl start flight-logs-backup; next: systemctl list-timers flight-logs-backup.timer"
echo "Update:    sudo bash $WEBUI/install.sh"
if [ "$(get_setting SAML_ENABLED)" != 0 ]; then
    echo "SAML:      register $PUBLIC_URL/saml/metadata with TU Delft ICT (middleware-ictfm@tudelft.nl) if not done yet"
fi
