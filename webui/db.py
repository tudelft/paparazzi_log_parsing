"""SQLite storage for parsed Paparazzi logs."""
import gzip
import json
import math
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS bulk_reservations (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    size_bytes INTEGER NOT NULL,
    expires_at REAL NOT NULL,
    uploading INTEGER NOT NULL DEFAULT 0,
    ingest_id INTEGER REFERENCES ingest(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL UNIQUE,
    compressed INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    stored_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    uploaded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attachment_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attachment_id INTEGER NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
    flight_id INTEGER REFERENCES flights(id) ON DELETE CASCADE,
    log_id INTEGER REFERENCES logs(id) ON DELETE CASCADE,
    record_id INTEGER REFERENCES flight_records(id) ON DELETE CASCADE,
    CHECK((flight_id IS NOT NULL) + (log_id IS NOT NULL) + (record_id IS NOT NULL) = 1),
    UNIQUE(attachment_id, flight_id),
    UNIQUE(attachment_id, log_id),
    UNIQUE(attachment_id, record_id)
);
CREATE INDEX IF NOT EXISTS ix_attachment_flight ON attachment_links(flight_id);
CREATE INDEX IF NOT EXISTS ix_attachment_log ON attachment_links(log_id);
CREATE INDEX IF NOT EXISTS ix_attachment_record ON attachment_links(record_id);
CREATE TABLE IF NOT EXISTS flight_records (
    id INTEGER PRIMARY KEY,
    hidden INTEGER NOT NULL DEFAULT 0,
    source_type TEXT NOT NULL DEFAULT 'paper' CHECK(source_type='paper'),
    drone_id INTEGER NOT NULL REFERENCES drones(id),
    pic_user_id INTEGER NOT NULL REFERENCES users(id),
    pac_user_id INTEGER REFERENCES users(id),
    gcs_user_id INTEGER REFERENCES users(id),
    project_id INTEGER NOT NULL REFERENCES projects(id),
    date TEXT NOT NULL,
    takeoff_time TEXT,
    kind TEXT NOT NULL CHECK(kind IN ('flight','daily')),
    flight_count INTEGER NOT NULL CHECK(flight_count BETWEEN 1 AND 1000),
    duration_s REAL NOT NULL CHECK(duration_s > 0 AND duration_s <= 86400),
    place_id INTEGER REFERENCES places(id),
    goal TEXT, comment TEXT, reference TEXT,
    fingerprint TEXT NOT NULL UNIQUE,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS flight_links (     -- paper record <-> telemetry flight decisions (links.py)
    id INTEGER PRIMARY KEY,
    record_id INTEGER NOT NULL REFERENCES flight_records(id) ON DELETE CASCADE,
    log_id INTEGER NOT NULL REFERENCES logs(id) ON DELETE CASCADE,
    ac_id INTEGER NOT NULL,             -- aircraft.ac_id, -1 when the log has none
    idx INTEGER NOT NULL,               -- flights.idx: log_id + ac_id + idx survive re-parsing
    takeoff_utc TEXT,                   -- takeoff when decided; a re-parse that moves it marks the link stale
    same INTEGER NOT NULL,              -- 1 = same flight, 0 = not the same flight (blocks automatic linking)
    origin TEXT NOT NULL CHECK (origin IN ('auto', 'manual')),
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    UNIQUE (record_id, log_id, ac_id, idx)
);
CREATE TABLE IF NOT EXISTS source_links (    -- manual join/split of two logged recordings (links.py)
    id INTEGER PRIMARY KEY,
    a_log_id INTEGER NOT NULL REFERENCES logs(id) ON DELETE CASCADE,
    a_ac_id INTEGER NOT NULL, a_idx INTEGER NOT NULL, a_takeoff_utc TEXT,
    b_log_id INTEGER NOT NULL REFERENCES logs(id) ON DELETE CASCADE,
    b_ac_id INTEGER NOT NULL, b_idx INTEGER NOT NULL, b_takeoff_utc TEXT,   -- (a) < (b), keys as in flight_links
    same INTEGER NOT NULL,              -- 1 = one flight (join), 0 = different flights (split)
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    UNIQUE (a_log_id, a_ac_id, a_idx, b_log_id, b_ac_id, b_idx)
);
CREATE TABLE IF NOT EXISTS weather_cache (
    cache_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT,
    email TEXT,
    password_hash TEXT,                -- NULL for accounts that only sign in through an external provider
    is_admin INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    provider TEXT NOT NULL DEFAULT 'local',   -- 'local' or 'saml' (TU Delft NetID)
    subject TEXT,                      -- id of the user at the external provider
    created_at TEXT NOT NULL,
    last_login TEXT,
    default_project_id INTEGER,        -- used when a flight has no aircraft default
    is_external INTEGER NOT NULL DEFAULT 0 CHECK(is_external IN (0,1)),  -- crew from another operator; cannot sign in
    UNIQUE (provider, subject)
);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,                -- original file name / relative path
    sha256 TEXT NOT NULL UNIQUE,       -- sha256 of the .data file (dedupe key)
    size_bytes INTEGER,
    data_path TEXT NOT NULL,           -- stored .data file, relative to the log dir
    log_path TEXT,                     -- stored companion .log file (NULL if not uploaded)
    paths_file TEXT,                   -- gzip JSON list of flight paths next to the log (flights.path_idx)
    status TEXT NOT NULL,              -- queued | parsing | done | error
    error TEXT,
    uploaded_at TEXT NOT NULL,
    uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    project_id INTEGER,                 -- projects.id; cleared when that project is deleted
    parser_name TEXT,                  -- which parser (and version) produced the stored result
    parser_version TEXT,
    parser_path_rate_hz REAL,
    log_type TEXT,                     -- onboard_sd | gcs | unknown (detected by the parser)
    parsed_at TEXT,
    parse_seconds REAL,
    start_utc TEXT, first_data_utc TEXT, end_utc TEXT, duration_s REAL,
    timing TEXT,                       -- JSON clock evidence; original clock is never overwritten
    date_override TEXT                 -- YYYY-MM-DD typed in when the file clock is missing or 1980; kept across re-parse
);
CREATE TABLE IF NOT EXISTS drones (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    unique_id TEXT NOT NULL UNIQUE COLLATE NOCASE,  -- fleet ID you assign; stays the same if the name changes
    default_project_id INTEGER,        -- projects.id; used when a matching log has no project yet
    manager TEXT,                      -- post holder / responsible person
    current_user TEXT,                 -- who currently has the aircraft
    status TEXT,                       -- Ready | Maintenance | Development | Discard
    bought_on TEXT,                    -- YYYY-MM-DD
    inventory_project TEXT,            -- project name from the organisation fleet list
    comments TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS drone_aliases (
    id INTEGER PRIMARY KEY,
    drone_id INTEGER NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    alias TEXT NOT NULL UNIQUE COLLATE NOCASE   -- name as it appears in the log aircraft field
);
CREATE TABLE IF NOT EXISTS ambiguous_drone_names (
    name TEXT PRIMARY KEY COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS log_aircraft_links (
    log_id INTEGER NOT NULL REFERENCES logs(id) ON DELETE CASCADE,
    ac_id INTEGER NOT NULL,
    drone_id INTEGER NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    PRIMARY KEY(log_id, ac_id)
);
CREATE TABLE IF NOT EXISTS drone_projects (
    id INTEGER PRIMARY KEY,
    drone_id INTEGER NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    start_date TEXT NOT NULL,          -- YYYY-MM-DD this airframe joined the project
    end_date TEXT                      -- YYYY-MM-DD last day, NULL = still on this project
);
CREATE INDEX IF NOT EXISTS ix_drone_projects_drone ON drone_projects(drone_id, start_date);
CREATE TABLE IF NOT EXISTS aircraft (
    id INTEGER PRIMARY KEY,
    log_id INTEGER NOT NULL REFERENCES logs(id) ON DELETE CASCADE,
    drone_id INTEGER,                  -- drones.id when the logged name matches a drone or alias
    ac_id INTEGER,
    name TEXT,
    pic TEXT, pac TEXT, gcs TEXT, goal TEXT,
    autopilot_versions TEXT,           -- JSON list
    first_utc TEXT, last_utc TEXT,
    num_flights INTEGER,
    total_flight_time_s REAL,
    total_flight_distance_m REAL,
    preflight_checks TEXT,             -- JSON
    info_messages TEXT,                -- JSON
    simulation TEXT,                   -- JSON detection/evidence; NULL = not assessed
    config_gz BLOB                     -- gzip JSON: flight_plan, settings, xml
);
CREATE TABLE IF NOT EXISTS flights (
    id INTEGER PRIMARY KEY,
    hidden INTEGER NOT NULL DEFAULT 0,
    aircraft_id INTEGER NOT NULL REFERENCES aircraft(id) ON DELETE CASCADE,
    idx INTEGER,
    duration_s REAL,
    distance_m REAL,                   -- horizontal path length flown from the parser [m]
    from_takeoff_m REAL,               -- horizontal range from takeoff at landing / last sample [m]
    max_from_takeoff_m REAL,           -- farthest horizontal range from takeoff during the flight [m]
    landed INTEGER,
    takeoff_time_s REAL, takeoff_utc TEXT, takeoff_lat REAL, takeoff_lon REAL, takeoff_alt_m REAL, takeoff_voltage_v REAL,
    landing_time_s REAL, landing_utc TEXT, landing_lat REAL, landing_lon REAL, landing_alt_m REAL, landing_voltage_v REAL,
    takeoff_json TEXT, landing_json TEXT,   -- full takeoff/landing objects (wind, ins, ...)
    num_points INTEGER,
    has_gps_position INTEGER CHECK(has_gps_position IN (0,1)),
    verdict TEXT,                      -- parser estimate: flight | uncertain | ground
    assessment_json TEXT,              -- score, reasons and metrics behind the verdict
    project_id INTEGER,                -- projects.id; auto from aircraft / uploader / GCS / PIC, then editable
    project_source TEXT,               -- timeline | manual; NULL preserves legacy assignments of unknown origin
    pic_user_id INTEGER,               -- override PIC/PAC/GCS with a linked user; NULL keeps the name from the log
    pac_user_id INTEGER,
    gcs_user_id INTEGER,
    comment TEXT,                      -- notes added in the logbook / flight page
    path_idx INTEGER                   -- index of this flight's path in logs.paths_file
);
CREATE TABLE IF NOT EXISTS setting_changes (
    id INTEGER PRIMARY KEY,
    aircraft_id INTEGER NOT NULL REFERENCES aircraft(id) ON DELETE CASCADE,
    time_s REAL, utc TEXT, previous_report_s REAL,
    idx INTEGER, var TEXT, from_value REAL, to_value REAL, in_flight INTEGER
);
CREATE TABLE IF NOT EXISTS password_setup_tokens (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,   -- sha256 of the one-time link token; the token itself is not stored
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_members (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL,                 -- view | add | full
    PRIMARY KEY (project_id, user_id)
);
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,     -- sha256 of the key; the key itself is shown once and never stored
    prefix TEXT NOT NULL,              -- first characters, to recognise the key in the list
    created_at TEXT NOT NULL,
    last_used_at TEXT
);
CREATE TABLE IF NOT EXISTS user_aliases (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    alias TEXT NOT NULL UNIQUE COLLATE NOCASE   -- name as it appears in the [PFC] pic/pac/gcs messages
);
CREATE TABLE IF NOT EXISTS places (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    indoor INTEGER NOT NULL DEFAULT 0,  -- 1 for indoor locations (hidden from the logbook by default)
    shape TEXT NOT NULL                 -- JSON: {"type":"polygon","points":[[lat,lon],...]} or {"type":"circle","center":[lat,lon],"radius_m":r}
);
CREATE TABLE IF NOT EXISTS ingest (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,                 -- pair | zip
    name TEXT NOT NULL,                 -- original .data path or zip name
    stage_dir TEXT NOT NULL,            -- directory under <log dir>/.incoming
    data_name TEXT NOT NULL,            -- filename in stage_dir (.data or .zip)
    log_name TEXT,                      -- companion .log filename; null for a zip
    size_bytes INTEGER,
    status TEXT NOT NULL,               -- accepted | hashing | duplicate | rejected | error | stored
    error TEXT,
    log_id INTEGER,                     -- logs.id once stored
    uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    uploaded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ingest_status ON ingest(status);
CREATE INDEX IF NOT EXISTS ix_aircraft_log ON aircraft(log_id);
CREATE INDEX IF NOT EXISTS ix_flights_ac ON flights(aircraft_id);
CREATE INDEX IF NOT EXISTS ix_changes_ac ON setting_changes(aircraft_id);
CREATE INDEX IF NOT EXISTS ix_changes_var ON setting_changes(var);
CREATE TABLE IF NOT EXISTS qualification_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    requires_evidence INTEGER NOT NULL CHECK(requires_evidence IN (0,1)),
    requires_expiry INTEGER NOT NULL DEFAULT 0 CHECK(requires_expiry IN (0,1)),
    requires_start INTEGER NOT NULL DEFAULT 0 CHECK(requires_start IN (0,1)),
    internal INTEGER NOT NULL DEFAULT 0 CHECK(internal IN (0,1)),
    sort_order INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
);
CREATE TABLE IF NOT EXISTS qualification_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type_id INTEGER NOT NULL REFERENCES qualification_types(id),
    valid_until TEXT,
    valid_from TEXT,
    status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','superseded','withdrawn')),
    submitted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    submitted_at TEXT NOT NULL,
    reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at TEXT,
    evidence_path TEXT,
    filename TEXT,
    mime TEXT,
    compressed INTEGER NOT NULL DEFAULT 0,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    evidence_deleted_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_qualification_pending ON qualification_submissions(user_id,type_id) WHERE status='pending';
CREATE UNIQUE INDEX IF NOT EXISTS ix_qualification_approved ON qualification_submissions(user_id,type_id) WHERE status='approved';
CREATE TABLE IF NOT EXISTS qualification_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_qualification_audit_subject ON qualification_audit(subject_id,id);
"""

_local = threading.local()
# Only one worker stores a parse result at a time; others wait here instead of failing with "database is locked".
write_lock = threading.Lock()
BUSY_TIMEOUT_MS = 300_000  # 5 minutes


def is_locked(exc):
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def retry_locked(fn, con=None, attempts=8, wait=0.5):
    """Run fn(), waiting and retrying if SQLite is busy (parallel parse jobs or a long write)."""
    delay = wait
    for i in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if con is not None:
                try:
                    con.rollback()
                except sqlite3.Error:
                    pass
            if not is_locked(e) or i == attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 16)


def connect(path):
    """One connection per thread."""
    con = getattr(_local, "con", None)
    if con is None or getattr(_local, "path", None) != path:
        con = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=%d" % BUSY_TIMEOUT_MS)
        con.execute("PRAGMA foreign_keys=ON")
        _local.con, _local.path = con, path
    return con


def init(path):
    con = connect(path)
    con.executescript(SCHEMA)
    if "requires_start" not in {r["name"] for r in con.execute("PRAGMA table_info(qualification_types)")}:
        con.execute("ALTER TABLE qualification_types ADD COLUMN requires_start"
                    " INTEGER NOT NULL DEFAULT 0 CHECK(requires_start IN (0,1))")
    if "valid_from" not in {r["name"] for r in con.execute("PRAGMA table_info(qualification_submissions)")}:
        con.execute("ALTER TABLE qualification_submissions ADD COLUMN valid_from TEXT")
    if "requires_expiry" not in {r["name"] for r in con.execute("PRAGMA table_info(qualification_types)")}:
        con.execute("ALTER TABLE qualification_types ADD COLUMN requires_expiry"
                    " INTEGER NOT NULL DEFAULT 0 CHECK(requires_expiry IN (0,1))")
    type_cols = {r["name"] for r in con.execute("PRAGMA table_info(qualification_types)")}
    if "internal" not in type_cols:
        con.execute("ALTER TABLE qualification_types ADD COLUMN internal"
                    " INTEGER NOT NULL DEFAULT 0 CHECK(internal IN (0,1))")
    if "sort_order" not in type_cols:
        con.execute("ALTER TABLE qualification_types ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
        for i, row in enumerate(con.execute("SELECT id FROM qualification_types ORDER BY name"), 1):
            con.execute("UPDATE qualification_types SET sort_order=? WHERE id=?", (i, row["id"]))
    if "is_external" not in {r["name"] for r in con.execute("PRAGMA table_info(users)")}:
        con.execute("ALTER TABLE users ADD COLUMN is_external INTEGER NOT NULL DEFAULT 0 CHECK(is_external IN (0,1))")
    cols = {r["name"] for r in con.execute("PRAGMA table_info(logs)")}
    if "timing" not in cols:
        con.execute("ALTER TABLE logs ADD COLUMN timing TEXT")
    if "simulation" not in {r["name"] for r in con.execute("PRAGMA table_info(aircraft)")}:
        con.execute("ALTER TABLE aircraft ADD COLUMN simulation TEXT")
    if "uploaded_by" not in cols:  # database created before accounts existed
        con.execute("ALTER TABLE logs ADD COLUMN uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL")
    fcols = {r[1] for r in con.execute("PRAGMA table_info(flights)")}
    if "has_gps_position" not in fcols:
        con.execute("ALTER TABLE flights ADD COLUMN has_gps_position INTEGER CHECK(has_gps_position IN (0,1))")
    if "project_source" not in fcols:
        con.execute("ALTER TABLE flights ADD COLUMN project_source TEXT")
    for table in ("flights", "flight_records"):
        if "hidden" not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
            con.execute(f"ALTER TABLE {table} ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
    for col in ("verdict", "assessment_json"):
        if col not in fcols:
            con.execute(f"ALTER TABLE flights ADD COLUMN {col} TEXT")
    if "project_id" not in fcols:
        con.execute("ALTER TABLE flights ADD COLUMN project_id INTEGER")
        con.execute(
            "UPDATE flights SET project_id = ("
            " SELECT l.project_id FROM aircraft a JOIN logs l ON l.id = a.log_id"
            " WHERE a.id = flights.aircraft_id) WHERE project_id IS NULL")
    ucols = {r[1] for r in con.execute("PRAGMA table_info(users)")}
    if "default_project_id" not in ucols:
        con.execute("ALTER TABLE users ADD COLUMN default_project_id INTEGER")
    fcols = {r[1] for r in con.execute("PRAGMA table_info(flights)")}
    for col in ("pic_user_id", "pac_user_id", "gcs_user_id"):
        if col not in fcols:
            con.execute(f"ALTER TABLE flights ADD COLUMN {col} INTEGER")
    if "comment" not in {r[1] for r in con.execute("PRAGMA table_info(flights)")}:
        con.execute("ALTER TABLE flights ADD COLUMN comment TEXT")
    fcols = {r[1] for r in con.execute("PRAGMA table_info(flights)")}
    if "distance_m" not in fcols:
        con.execute("ALTER TABLE flights ADD COLUMN distance_m REAL")
    if "from_takeoff_m" not in fcols:
        con.execute("ALTER TABLE flights ADD COLUMN from_takeoff_m REAL")
    if "max_from_takeoff_m" not in fcols:
        con.execute("ALTER TABLE flights ADD COLUMN max_from_takeoff_m REAL")
    if "total_flight_distance_m" not in {r[1] for r in con.execute("PRAGMA table_info(aircraft)")}:
        con.execute("ALTER TABLE aircraft ADD COLUMN total_flight_distance_m REAL")
    con.execute("CREATE INDEX IF NOT EXISTS ix_flights_project ON flights(project_id)")
    if "indoor" not in {r[1] for r in con.execute("PRAGMA table_info(places)")}:
        con.execute("ALTER TABLE places ADD COLUMN indoor INTEGER NOT NULL DEFAULT 0")
    if "log_type" not in cols:
        con.execute("ALTER TABLE logs ADD COLUMN log_type TEXT")
    if "project_id" not in cols:
        con.execute("ALTER TABLE logs ADD COLUMN project_id INTEGER")
    if "date_override" not in cols:
        con.execute("ALTER TABLE logs ADD COLUMN date_override TEXT")
    if "paths_file" not in cols:
        con.execute("ALTER TABLE logs ADD COLUMN paths_file TEXT")
    if "path_idx" not in {r[1] for r in con.execute("PRAGMA table_info(flights)")}:
        con.execute("ALTER TABLE flights ADD COLUMN path_idx INTEGER")
    acols = {r[1] for r in con.execute("PRAGMA table_info(aircraft)")}
    if "drone_id" not in acols:
        con.execute("ALTER TABLE aircraft ADD COLUMN drone_id INTEGER")
    con.execute("CREATE INDEX IF NOT EXISTS ix_aircraft_drone ON aircraft(drone_id)")
    dcols = {r[1] for r in con.execute("PRAGMA table_info(drones)")}
    if "unique_id" not in dcols:
        con.execute("ALTER TABLE drones ADD COLUMN unique_id TEXT")
        for d in con.execute("SELECT id FROM drones"):
            con.execute("UPDATE drones SET unique_id=? WHERE id=?", (str(d["id"]), d["id"]))
    for col in ("manager", "current_user", "status", "bought_on", "inventory_project", "comments"):
        if col not in dcols:
            con.execute(f"ALTER TABLE drones ADD COLUMN {col} TEXT")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_drones_unique_id ON drones(unique_id COLLATE NOCASE)")
    con.execute(
        "CREATE TABLE IF NOT EXISTS drone_projects ("
        " id INTEGER PRIMARY KEY,"
        " drone_id INTEGER NOT NULL REFERENCES drones(id) ON DELETE CASCADE,"
        " project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,"
        " start_date TEXT NOT NULL,"
        " end_date TEXT)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_drone_projects_drone ON drone_projects(drone_id, start_date)")
    con.commit()


def pack(obj):
    return gzip.compress(json.dumps(obj, separators=(",", ":")).encode(), 5)


def unpack(blob):
    return json.loads(gzip.decompress(blob)) if blob else None


def _loc(d):
    p = (d or {}).get("location") or {}
    return p.get("lat"), p.get("lon"), p.get("alt_m")


def flight_paths(doc):
    """All flight paths of a parse result, in the order store_parsed numbers them (path_idx)."""
    return [f.get("path") or [] for ac in doc.get("aircraft", []) for f in ac.get("flights", [])]


def valid_gps_location(lat, lon):
    return (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
            and math.isfinite(lat) and math.isfinite(lon)
            and -90 <= lat <= 90 and -180 <= lon <= 180 and (lat != 0 or lon != 0))


def path_has_gps_position(path):
    return any(valid_gps_location((p.get("position") or {}).get("lat"),
                                  (p.get("position") or {}).get("lon")) for p in path)


def store_parsed(con, log_id, doc, paths_file):
    """Replace the parsed content of a log by the output of the C++ parser (caller commits).

    The paths themselves are in paths_file (written by the caller from flight_paths(doc))."""
    con.execute("DELETE FROM aircraft WHERE log_id=?", (log_id,))
    lg = doc.get("log", {})
    pr = doc.get("parser", {})
    con.execute(
        "UPDATE logs SET start_utc=?, first_data_utc=?, end_utc=?, duration_s=?, log_type=?,"
        " parser_name=?, parser_version=?, parser_path_rate_hz=?, paths_file=?, timing=? WHERE id=?",
        (lg.get("start_utc"), lg.get("first_data_utc"), lg.get("end_utc"), lg.get("duration_s"), lg.get("type"),
         pr.get("name"), pr.get("version"), pr.get("path_rate_hz"), paths_file,
         json.dumps(lg["timing"]) if lg.get("timing") else None, log_id),
    )
    path_idx = 0
    for ac in doc.get("aircraft", []):
        m = ac.get("mission") or {}
        span = ac.get("data_span") or {}
        summ = ac.get("summary") or {}
        config = {"flight_plan": ac.get("flight_plan"), "settings": ac.get("settings"), "xml": ac.get("xml"),
                  "invalid_setting_reports": (ac.get("settings_changes") or {}).get("invalid_reports", [])}
        cur = con.execute(
            "INSERT INTO aircraft (log_id, ac_id, name, pic, pac, gcs, goal, autopilot_versions, first_utc, last_utc,"
            " num_flights, total_flight_time_s, total_flight_distance_m, preflight_checks, info_messages, config_gz, simulation)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (log_id, ac.get("ac_id"), ac.get("name"), m.get("pic"), m.get("pac"), m.get("gcs"), m.get("goal"),
             json.dumps(ac.get("autopilot_versions", [])), span.get("first_utc"), span.get("last_utc"),
             summ.get("num_flights"), summ.get("total_flight_time_s"), summ.get("total_flight_distance_m"),
             json.dumps(ac.get("preflight_checks", [])), json.dumps(ac.get("info_messages", [])), pack(config),
             json.dumps(ac["simulation"]) if ac.get("simulation") is not None else None),
        )
        ac_pk = cur.lastrowid
        for f in ac.get("flights", []):
            to, ld = f.get("takeoff") or {}, f.get("landing") or {}
            tlat, tlon, talt = _loc(to)
            llat, llon, lalt = _loc(ld)
            path = f.get("path") or []
            con.execute(
                "INSERT INTO flights (aircraft_id, idx, duration_s, distance_m, from_takeoff_m, max_from_takeoff_m, landed,"
                " takeoff_time_s, takeoff_utc, takeoff_lat, takeoff_lon, takeoff_alt_m, takeoff_voltage_v,"
                " landing_time_s, landing_utc, landing_lat, landing_lon, landing_alt_m, landing_voltage_v,"
                " takeoff_json, landing_json, num_points, verdict, assessment_json, path_idx, has_gps_position)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ac_pk, f.get("flight"), f.get("duration_s"), f.get("distance_m"), f.get("from_takeoff_m"),
                 f.get("max_from_takeoff_m"), int(bool(f.get("landed"))),
                 to.get("time_s"), to.get("utc"), tlat, tlon, talt, to.get("voltage_v"),
                 ld.get("time_s"), ld.get("utc"), llat, llon, lalt, ld.get("voltage_v"),
                 json.dumps(to), json.dumps(ld), len(path),
                 (f.get("assessment") or {}).get("verdict"), json.dumps(f.get("assessment")), path_idx,
                 int(valid_gps_location(tlat, tlon) or valid_gps_location(llat, llon)
                     or path_has_gps_position(path))),
            )
            path_idx += 1
        changes = (ac.get("settings_changes") or {}).get("timeline") or []
        con.executemany(
            "INSERT INTO setting_changes (aircraft_id, time_s, utc, previous_report_s, idx, var, from_value, to_value,"
            " in_flight) VALUES (?,?,?,?,?,?,?,?,?)",
            [(ac_pk, c.get("time_s"), c.get("utc"), c.get("previous_report_s"), c.get("index"), c.get("var"),
              c.get("from"), c.get("to"), int(bool(c.get("in_flight")))) for c in changes],
        )
