/*
 * Fast Paparazzi .data log summary extractor.
 *
 * Memory maps the .data file, scans it line by line with memchr and only parses
 * the handful of messages that are needed. No XML/pprzlink/boost needed.
 */

#include <algorithm>
#include <cctype>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <thread>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

namespace
{

// WIND_INFO_RET: wind vector (direction the wind blows to), valid when bit 0 of the flags is set
struct Wind {
  bool valid = false, has_up = false, has_temp = false;
  double east = 0, north = 0, up = 0, temp = 0;
};

// INS message: local NED position [m], velocity [m/s] and acceleration [m/s2]
struct InsState {
  bool valid = false;
  double pos[3] = {0, 0, 0}, vel[3] = {0, 0, 0}, acc[3] = {0, 0, 0};
};

// Horizontal metres on the WGS84 sphere, then 3D with altitude.
double earth_distance_m(double lat1, double lon1, double lat2, double lon2)
{
  const double r = M_PI / 180.0;
  const double p1 = lat1 * r, p2 = lat2 * r, dp = (lat2 - lat1) * r, dl = (lon2 - lon1) * r;
  const double a = std::sin(dp / 2) * std::sin(dp / 2) +
                   std::cos(p1) * std::cos(p2) * std::sin(dl / 2) * std::sin(dl / 2);
  return 2 * 6371000.0 * std::atan2(std::sqrt(a), std::sqrt(std::max(0.0, 1.0 - a)));
}

double step_distance_m(double lat1, double lon1, double alt1, double lat2, double lon2, double alt2)
{
  return std::hypot(earth_distance_m(lat1, lon1, lat2, lon2), alt2 - alt1);
}

// WGS84 ECEF metres -> lat/lon degrees and height above the ellipsoid.
bool ecef_to_lla(double x, double y, double z, double &lat_deg, double &lon_deg, double &alt_m)
{
  const double a = 6378137.0, e2 = 6.69437999014e-3;
  double r = std::hypot(x, y);
  if (r < 1000 && std::fabs(z) < 1000) { return false; }
  lon_deg = std::atan2(y, x) * 180.0 / M_PI;
  double phi = std::atan2(z, r * (1 - e2));
  double alt = 0;
  for (int i = 0; i < 8; i++) {
    double s = std::sin(phi);
    double N = a / std::sqrt(1 - e2 * s * s);
    alt = r / std::cos(phi) - N;
    phi = std::atan2(z, r * (1 - e2 * N / (N + alt)));
  }
  lat_deg = phi * 180.0 / M_PI;
  alt_m = alt;
  return lat_deg >= -90.0 && lat_deg <= 90.0;
}

// WGS84 UTM metres (northern hemisphere) -> lat/lon degrees. Fixed-wing GCS logs use GPS, not GPS_INT.
bool utm_to_lla(double east_m, double north_m, int zone, double &lat_deg, double &lon_deg)
{
  if (zone < 1 || zone > 60) { return false; }
  constexpr double a = 6378137.0, e2 = 6.69437999014e-3, k0 = 0.9996;
  const double e1 = (1 - std::sqrt(1 - e2)) / (1 + std::sqrt(1 - e2));
  const double x = east_m - 500000.0, y = north_m;
  const double m = y / k0;
  const double mu = m / (a * (1 - e2 / 4 - 3 * e2 * e2 / 64 - 5 * e2 * e2 * e2 / 256));
  const double phi1 = mu + (3 * e1 / 2 - 27 * e1 * e1 * e1 / 32) * std::sin(2 * mu)
      + (21 * e1 * e1 / 16 - 55 * e1 * e1 * e1 * e1 / 32) * std::sin(4 * mu)
      + (151 * e1 * e1 * e1 / 96) * std::sin(6 * mu);
  const double e2p = e2 / (1 - e2);
  const double sinp = std::sin(phi1), cosp = std::cos(phi1), tanp = std::tan(phi1);
  const double n = a / std::sqrt(1 - e2 * sinp * sinp);
  const double tt = tanp * tanp, c = e2p * cosp * cosp;
  const double r = a * (1 - e2) / std::pow(1 - e2 * sinp * sinp, 1.5);
  const double d = x / (n * k0);
  const double lat = phi1 - (n * tanp / r) * (d * d / 2 - (5 + 3 * tt + 10 * c - 4 * c * c - 9 * e2p) * d * d * d * d / 24
      + (61 + 90 * tt + 298 * c + 45 * tt * tt - 252 * e2p - 3 * c * c) * d * d * d * d * d * d / 720);
  const double lon0 = (zone - 1) * 6.0 - 180.0 + 3.0;
  lon_deg = lon0 + ((d - (1 + 2 * tt + c) * d * d * d / 6
      + (5 - 2 * c + 28 * tt - 3 * c * c + 8 * e2p + 24 * tt * tt) * d * d * d * d * d / 120) / cosp) * 180.0 / M_PI;
  lat_deg = lat * 180.0 / M_PI;
  return lat_deg >= -90.0 && lat_deg <= 90.0;
}

struct PathPoint {
  double t, lat, lon, alt;
  double distance_m;       // cumulative 3D distance flown along the path [m]
  double from_takeoff_m;   // 3D range from the takeoff location [m]
  bool has_baro;
  double pressure_pa, baro_alt_m;  // AIR_DATA static pressure and barometric altitude (AMSL)
  bool has_volt, has_as;
  double vsupply, airspeed, tas, diff_p;  // diff_p: AIR_DATA differential pressure [Pa]
  bool has_euler, has_quat;
  double roll_deg, pitch_deg, yaw_deg;  // ROTORCRAFT_FP
  double qi, qx, qy, qz;                // body quaternion (AHRS_REF_QUAT)
  int ap_mode, h_mode, v_mode;          // ROTORCRAFT_STATUS mode ids, -1 unknown
  int block, stage;                     // ROTORCRAFT_NAV_STATUS, -1 unknown
  std::vector<int16_t> actuators;       // ACTUATORS
  Wind wind;
  InsState ins;
  bool has_gs = false;
  double gndspeed = 0;  // GPS 2D ground speed [m/s]
};

struct Range {
  bool any = false;
  double min = 0, max = 0;
  void add(double v) {
    if (!any) { min = max = v; any = true; }
    else { min = std::min(min, v); max = std::max(max, v); }
  }
  double span() const { return any ? max - min : 0; }
};

// Streaming statistics of one flight (every INS/AIR_DATA/GPS_INT/ROTORCRAFT_FP message, not only the stored path
// points) used to judge whether the aircraft really flew.
struct FlightStats {
  Range ins_n, ins_e, ins_up, baro, gps_alt, gps_lat, gps_lon;
  double acc_count = 0, acc_mean[3] = {0, 0, 0}, acc_m2[3] = {0, 0, 0};  // Welford, INS acceleration
  double max_speed = 0, max_vspeed = 0, max_tilt_deg = 0;
  int status_msgs = 0, motors_on_msgs = 0;  // ROTORCRAFT_STATUS messages during the flight
  double gps_lat_ref = 0;

  void add_ins(const InsState &n) {
    ins_n.add(n.pos[0]); ins_e.add(n.pos[1]); ins_up.add(-n.pos[2]);
    max_speed = std::max(max_speed, std::sqrt(n.vel[0] * n.vel[0] + n.vel[1] * n.vel[1] + n.vel[2] * n.vel[2]));
    max_vspeed = std::max(max_vspeed, std::fabs(n.vel[2]));
    acc_count += 1;
    for (int i = 0; i < 3; i++) {
      double d = n.acc[i] - acc_mean[i];
      acc_mean[i] += d / acc_count;
      acc_m2[i] += d * (n.acc[i] - acc_mean[i]);
    }
  }
  void add_gps(double lat, double lon, double alt) {
    gps_lat_ref = lat;
    gps_lat.add(lat); gps_lon.add(lon); gps_alt.add(alt);
  }
  double acc_rms() const {  // standard deviation of the 3D INS acceleration [m/s2]
    if (acc_count < 2) { return 0; }
    return std::sqrt((acc_m2[0] + acc_m2[1] + acc_m2[2]) / acc_count);
  }
  double ins_horizontal_m() const { return std::hypot(ins_n.span(), ins_e.span()); }
  double gps_horizontal_m() const {
    return std::hypot(gps_lat.span() * 111320.0, gps_lon.span() * 111320.0 * std::cos(gps_lat_ref * M_PI / 180));
  }
};

struct Flight {
  FlightStats stats;
  std::vector<PathPoint> path;  // GPS points at the configured rate while in flight
  double takeoff_time = 0, landing_time = 0;
  double lat = 0, lon = 0, alt = 0;  // deg, deg, m (WGS84)
  bool has_pos = false;
  bool has_optitrack = false;  // EXTERNAL_POSE / OptiTrack-as-GPS (3D fix, 0 satellites)
  bool has_takeoff_volt = false, has_land_volt = false;
  double takeoff_volt = 0, land_volt = 0;
  Wind takeoff_wind, land_wind;
  InsState takeoff_ins, land_ins;
  double land_lat = 0, land_lon = 0, land_alt = 0;
  bool has_land_pos = false;
  bool complete = false;  // false if the log ended while in flight
  double distance_m = 0;            // 3D path length flown; GPS when available, else INS
  double from_takeoff_m = 0;        // 3D range from takeoff at the latest sample
  double max_from_takeoff_m = 0;    // farthest 3D range from takeoff during the flight
  bool has_prev_gps = false, has_prev_ins = false, has_gps_origin = false, has_ins_origin = false;
  double prev_lat = 0, prev_lon = 0, prev_alt = 0;
  double prev_n = 0, prev_e = 0, prev_d = 0;
  double origin_lat = 0, origin_lon = 0, origin_alt = 0;
  double origin_n = 0, origin_e = 0, origin_d = 0;

  double duration() const { return landing_time - takeoff_time; }

  void set_from_takeoff(double range) {
    from_takeoff_m = range;
    if (range > max_from_takeoff_m) { max_from_takeoff_m = range; }
  }

  static bool plausible_ll(double lat, double lon) {
    return !(lat == 0.0 && lon == 0.0) && lat >= -90.0 && lat <= 90.0 && lon >= -180.0 && lon <= 180.0;
  }

  // Every GPS_INT with a 3D fix. Jumps over 500 m in one sample are ignored (glitches).
  void add_gps_step(double lat, double lon, double alt) {
    bool use = true;
    if (has_prev_gps) {
      const double d = step_distance_m(prev_lat, prev_lon, prev_alt, lat, lon, alt);
      if (d < 500) { distance_m += d; }
      else { use = false; }
    }
    if (use && plausible_ll(lat, lon)) {
      if (!has_gps_origin) {
        if (has_pos && plausible_ll(this->lat, this->lon)) {
          origin_lat = this->lat; origin_lon = this->lon; origin_alt = this->alt;
        } else {
          origin_lat = lat; origin_lon = lon; origin_alt = alt;
        }
        has_gps_origin = true;
        from_takeoff_m = 0;
        max_from_takeoff_m = 0;
      }
      set_from_takeoff(step_distance_m(origin_lat, origin_lon, origin_alt, lat, lon, alt));
    }
    has_prev_gps = true;
    prev_lat = lat; prev_lon = lon; prev_alt = alt;
  }
  // INS NED steps only until the first GPS fix, so indoor flights still get a length and range.
  void add_ins_step(const InsState &n) {
    if (has_prev_gps) { return; }
    if (has_prev_ins) {
      distance_m += std::hypot(n.pos[0] - prev_n, n.pos[1] - prev_e, n.pos[2] - prev_d);
    } else if (takeoff_ins.valid) {
      origin_n = takeoff_ins.pos[0]; origin_e = takeoff_ins.pos[1]; origin_d = takeoff_ins.pos[2];
      has_ins_origin = true;
    } else {
      origin_n = n.pos[0]; origin_e = n.pos[1]; origin_d = n.pos[2];
      has_ins_origin = true;
    }
    has_prev_ins = true;
    prev_n = n.pos[0]; prev_e = n.pos[1]; prev_d = n.pos[2];
    if (has_ins_origin) {
      set_from_takeoff(std::hypot(n.pos[0] - origin_n, n.pos[1] - origin_e, n.pos[2] - origin_d));
    }
  }
};

struct Assessment {
  const char *verdict;  // flight | uncertain | ground
  double score;
  std::string reasons;  // JSON array body
};

// Estimates whether the "in flight" flag of the autopilot corresponds to a real flight, from the duration, the
// altitude change (INS, else barometer, else GPS), horizontal movement, speed, attitude and the dynamic INS
// acceleration. Indoor/tethered flights have little horizontal movement, so no single criterion is decisive.
Assessment assess(const Flight &f) {
  const FlightStats &st = f.stats;
  double score = 0;
  std::string why;
  auto add = [&](double pts, const std::string &text) {
    score += pts;
    why += (why.empty() ? "\"" : ", \"") + text + "\"";
  };
  const double dur = f.duration();
  if (dur < 5) { add(-2, "shorter than 5 s"); }
  else if (dur < 10) { add(-1.5, "shorter than 10 s"); }
  else if (dur >= 30) { add(1, "longer than 30 s"); }

  // A constant barometric altitude means the message carries no usable value.
  // Skip the altitude penalty when there is no INS, GPS or changing baro — GPS-denied logs
  // would otherwise be hidden from the logbook as "uncertain" / "ground".
  const bool has_climb = st.ins_up.any || st.baro.span() > 0 || st.gps_alt.any;
  double climb = st.ins_up.any ? st.ins_up.span() : st.baro.span() > 0 ? st.baro.span() : st.gps_alt.span();
  if (has_climb) {
    if (climb >= 1.5) { add(2.5, "altitude changed by more than 1.5 m"); }
    else if (climb >= 0.5) { add(1, "altitude changed by more than 0.5 m"); }
    else { add(-1.5, "altitude hardly changed"); }
  } else {
    add(0, "no GPS or altitude to judge movement");
  }

  const bool has_horiz = st.ins_n.any || st.gps_lat.any;
  double horiz = st.ins_n.any ? st.ins_horizontal_m() : st.gps_horizontal_m();
  if (has_horiz) {
    if (horiz >= 3) { add(1.5, "moved more than 3 m horizontally"); }
    else if (horiz >= 1) { add(0.5, "moved more than 1 m horizontally"); }
  }

  // Taxi / ground test: motors on and rolling, but height hardly changes.
  // Known taxis climb < 0.5 m while rolling 16–66 m; real flights in the set climb ≥ 26 m.
  bool ground_roll = has_climb && has_horiz && climb < 1.5 && horiz >= 3;
  if (ground_roll) { add(-4, "moved on the ground without climbing"); }

  if (st.max_speed >= 1) { add(1, "speed above 1 m/s"); }
  if (st.max_tilt_deg >= 10) { add(0.5, "tilted more than 10 degrees"); }

  double rms = st.acc_rms();
  if (st.acc_count >= 5) {
    if (rms >= 0.5) { add(1.5, "clear dynamic acceleration"); }
    else if (rms < 0.15) { add(-1, "almost no acceleration"); }
  }
  bool motors_off = false;  // decisive: a flight needs running motors
  if (st.status_msgs >= 1) {
    const double on = static_cast<double>(st.motors_on_msgs) / st.status_msgs;
    if (on < 0.5) { add(-3, "motors off for most of the flight"); motors_off = true; }
    else if (on < 0.9 && st.status_msgs >= 3) { add(-1, "motors off for part of the flight"); }
    else if (on >= 0.9) { add(0.5, "motors on throughout"); }
  }
  // GPS-denied: duration + motors is the evidence we have. Without this, indoor / no-GPS
  // logs stay "uncertain" and the logbook hides them by default.
  if (!has_climb && !has_horiz && !motors_off && dur >= 30) {
    add(2, "in flight for over 30 s without GPS");
  }
  // Under 2 s the flag most likely flickered, whatever the movement
  const char *verdict = motors_off || ground_roll ? "ground"
      : score >= 2.5 && dur >= 2 ? "flight" : score <= 0 ? "ground" : "uncertain";
  return {verdict, score, why};
}

struct InfoMsg {
  double time;
  std::string text;
};

// One DL_VALUE report of a setting value
struct SettingSample {
  double t;
  int index;
  double value;
  bool in_flight;
};

struct AircraftState {
  bool in_flight = false;
  bool seen_status = false;
  double first_time = -1, last_time = 0;
  long last_path_bucket = -1;
  std::set<std::string> versions;
  std::vector<InfoMsg> infos;
  bool has_gps = false;
  bool has_optitrack = false;  // motion capture / OptiTrack on this aircraft in this log
  bool has_baro = false;
  double pressure = 0, baro_alt = 0;
  bool has_volt = false, has_as = false, has_gs = false;
  double vsupply = 0, airspeed = 0, tas = 0, diff_p = 0, gndspeed = 0;
  bool has_euler = false, has_quat = false;
  double roll = 0, pitch = 0, yaw = 0;
  double qi = 0, qx = 0, qy = 0, qz = 0;
  int ap_mode = -1, h_mode = -1, v_mode = -1, block = -1, stage = -1;
  std::string_view actuators_raw;  // latest ACTUATORS payload, parsed lazily when a path point is stored
  double lat = 0, lon = 0, alt = 0;
  Wind wind;
  InsState ins;
  std::vector<Flight> flights;
  std::vector<SettingSample> setting_samples;  // DL_VALUE reports, in log order
};

// Split off next space separated token
inline std::string_view next_token(const char *&p, const char *end)
{
  while (p < end && *p == ' ') { ++p; }
  const char *s = p;
  while (p < end && *p != ' ') { ++p; }
  return std::string_view(s, p - s);
}

template <typename T>
inline bool to_num(std::string_view sv, T &out)
{
  auto r = std::from_chars(sv.data(), sv.data() + sv.size(), out);
  return r.ec == std::errc();
}

std::vector<int16_t> parse_int_list(std::string_view v)
{
  std::vector<int16_t> out;
  const char *c = v.data(), *ce = c + v.size();
  while (c < ce) {
    int val;
    auto rr = std::from_chars(c, ce, val);
    if (rr.ec != std::errc()) { break; }
    out.push_back(static_cast<int16_t>(val));
    c = rr.ptr;
    if (c < ce && *c == ',') { ++c; }
  }
  return out;
}

std::string trim(std::string s)
{
  size_t b = s.find_first_not_of(" \t\r\n");
  if (b == std::string::npos) { return ""; }
  size_t e = s.find_last_not_of(" \t\r\n");
  return s.substr(b, e - b + 1);
}

// Names from the aircraft configuration in the .log file
struct AcConf {
  std::vector<std::string> blocks;        // flight plan block names (index = block id)
  std::vector<std::vector<std::string>> stages;  // per block: stage descriptions (index = stage id)
  std::vector<std::string> servos;        // actuator names in ACTUATORS order
  std::string airframe_xml, flight_plan_xml, settings_xml, radio_xml, telemetry_xml;  // raw XML as embedded in the .log
};

struct LogHeader {
  std::map<int, AcConf> confs;       // ac_id (-1 = airframe without ac_id) -> configuration
  std::map<std::string, std::vector<std::string>> mode_names;  // ROTORCRAFT_STATUS enum field -> values
  std::map<std::string, std::vector<std::string>> telemetry;   // telemetry message -> field names in order
  std::map<int, std::string> names;  // ac_id -> name
  std::string default_name;          // airframe without ac_id
  double start_epoch = -1;  // unix time (UTC) of log time 0, -1 if unknown
  bool has_aircraft_tag = false;   // <aircraft ac_id=...> entries are written by the ground station only
  bool has_logged_with = false;    // "<!-- logged with ... -->" comment, also only written by the ground station
};

// Paparazzi XML mixes name= / NAME= and ac_id= / AC_ID=. Match the attribute name ignoring case.
std::string xml_attr(const std::string &line, size_t from, const char *key)
{
  const size_t klen = std::strlen(key);
  if (klen == 0 || from >= line.size()) { return ""; }
  for (size_t i = from; i + klen + 2 <= line.size(); i++) {
    if (line[i + klen] != '=' || line[i + klen + 1] != '"') { continue; }
    if (i > 0) {
      unsigned char prev = static_cast<unsigned char>(line[i - 1]);
      if (prev != ' ' && prev != '\t' && prev != '<') { continue; }
    }
    bool match = true;
    for (size_t j = 0; j < klen; j++) {
      if (std::tolower(static_cast<unsigned char>(line[i + j])) !=
          std::tolower(static_cast<unsigned char>(key[j]))) {
        match = false;
        break;
      }
    }
    if (!match) { continue; }
    size_t v = i + klen + 2;
    size_t e = line.find('"', v);
    return e == std::string::npos ? "" : line.substr(v, e - v);
  }
  return "";
}

bool xml_contains_ci(const std::string &xml, const char *needle)
{
  if (!needle || !*needle || xml.empty()) { return false; }
  const size_t n = std::strlen(needle);
  for (size_t i = 0; i + n <= xml.size(); i++) {
    bool ok = true;
    for (size_t j = 0; j < n; j++) {
      if (std::tolower(static_cast<unsigned char>(xml[i + j])) !=
          std::tolower(static_cast<unsigned char>(needle[j]))) {
        ok = false;
        break;
      }
    }
    if (ok) { return true; }
  }
  return false;
}

// Read aircraft names, configuration and start time from the .log file
LogHeader read_log_header(const std::string &log_file)
{
  LogHeader h;
  std::ifstream f(log_file);
  std::string line;
  int cur_conf = -1;
  bool in_blocks = false, in_protocol = false, in_telemetry_class = false;
  std::vector<std::string> *cur_fields = nullptr;
  std::string cur_msg;
  size_t block_indent = 0, cap_indent = 0;
  std::string cap_tag, cap_buf;
  while (std::getline(f, line)) {
    size_t ind = line.find_first_not_of(' ');
    if (ind == std::string::npos) { continue; }
    if (h.start_epoch < 0) {
      size_t t = line.find("time_of_day=\"");
      if (t != std::string::npos) { h.start_epoch = atof(line.c_str() + t + 13); }
    }
    const char *l = line.c_str() + ind;
    if (strncmp(l, "<aircraft ", 10) == 0) { h.has_aircraft_tag = true; }
    if (strncmp(l, "<!-- logged with", 16) == 0) { h.has_logged_with = true; }

    // Capture the raw airframe / flight_plan / settings XML blocks
    if (!cap_tag.empty()) {
      cap_buf += line;
      cap_buf += '\n';
      if (ind == cap_indent && l[0] == '<' && l[1] == '/' && cap_tag.compare(0, std::string::npos, l + 2, cap_tag.size()) == 0) {
        AcConf &c = h.confs[cur_conf];
        if (cap_tag == "airframe") { c.airframe_xml = cap_buf; }
        else if (cap_tag == "flight_plan") { c.flight_plan_xml = cap_buf; }
        else if (cap_tag == "radio") { c.radio_xml = cap_buf; }
        else if (cap_tag == "telemetry") { c.telemetry_xml = cap_buf; }
        else {
          // Older logs dump one <settings> file per module; GCS logs also leave a trailing
          // <dl_settings> tree. Keep every block so DL_VALUE indices can be named.
          if (!c.settings_xml.empty()) { c.settings_xml += "\n<!-- SETTINGS_BLOCK -->\n"; }
          c.settings_xml += cap_buf;
        }
        cap_tag.clear();
      }
    } else if (l[0] == '<') {
      std::string tag(l + 1, strcspn(l + 1, " >/"));
      if (tag == "airframe" || tag == "flight_plan" || tag == "settings" || tag == "radio" ||
          tag == "telemetry" || tag == "dl_settings") {
        cap_tag = tag;
        cap_indent = ind;
        cap_buf = line + "\n";
        if (line.size() >= 2 && line.compare(line.size() - 2, 2, "/>") == 0) { cap_tag.clear(); }
      }
    }

    // Message definitions of the telemetry class: field order of each message
    if (strncmp(l, "<protocol>", 10) == 0) { in_protocol = true; continue; }
    if (in_protocol) {
      if (strncmp(l, "</protocol>", 11) == 0) {
        in_protocol = in_telemetry_class = false;
        cur_fields = nullptr;
      } else if (strncmp(l, "<msg_class ", 11) == 0) {
        in_telemetry_class = xml_attr(line, 0, "name") == "telemetry";
      } else if (strncmp(l, "</msg_class>", 12) == 0) {
        in_telemetry_class = false;
        cur_fields = nullptr;
      } else if (in_telemetry_class && strncmp(l, "<message ", 9) == 0) {
        cur_msg = xml_attr(line, 0, "name");
        cur_fields = &h.telemetry[cur_msg];
      } else if (strncmp(l, "</message>", 10) == 0) {
        cur_fields = nullptr;
      } else if (cur_fields && strncmp(l, "<field ", 7) == 0) {
        std::string fname = xml_attr(line, 0, "name");
        cur_fields->push_back(fname);
        // Enumerated values of ROTORCRAFT_STATUS fields (ap_mode, ap_h_mode, ap_v_mode)
        std::string vals = xml_attr(line, 0, "values");
        if (cur_msg == "ROTORCRAFT_STATUS" && !vals.empty()) {
          std::vector<std::string> v;
          std::stringstream ss(vals);
          std::string item;
          while (std::getline(ss, item, '|')) { v.push_back(item); }
          h.mode_names[fname] = v;
        }
      }
      continue;
    }

    if (strncmp(l, "<aircraft ", 10) == 0 || strncmp(l, "<airframe ", 10) == 0) {
      std::string name = xml_attr(line, 0, "name");
      std::string id = xml_attr(line, 0, "ac_id");
      if (id.empty()) { id = xml_attr(line, 0, "AC_ID"); }
      bool is_aircraft = strncmp(l, "<aircraft ", 10) == 0;
      // an <airframe> inside an <aircraft> keeps the id of its parent
      if (is_aircraft || !id.empty()) { cur_conf = id.empty() ? -1 : atoi(id.c_str()); }
      if (name.empty()) { continue; }
      if (cur_conf < 0) {
        if (h.default_name.empty()) { h.default_name = name; }
      } else if (is_aircraft || !h.names.count(cur_conf)) {
        h.names[cur_conf] = name;
      }
    } else if (strncmp(l, "<servo ", 7) == 0) {
      h.confs[cur_conf].servos.push_back(xml_attr(line, 0, "name"));
    } else if (strncmp(l, "<blocks>", 8) == 0) {
      in_blocks = true;
    } else if (strncmp(l, "</blocks>", 9) == 0) {
      in_blocks = false;
    } else if (in_blocks) {
      AcConf &c = h.confs[cur_conf];
      if (strncmp(l, "<block ", 7) == 0) {
        c.blocks.push_back(xml_attr(line, 0, "name"));
        c.stages.emplace_back();
        block_indent = ind;
      } else if (!c.stages.empty() && ind == block_indent + 2 && l[0] == '<' && l[1] != '/' &&
                 strncmp(l, "<exception", 10) != 0) {
        // direct child of a block: one stage (exceptions are not stages)
        std::string tag(l + 1, strcspn(l + 1, " />"));
        std::string wp = xml_attr(line, 0, "wp");
        c.stages.back().push_back(wp.empty() ? tag : tag + " " + wp);
      }
    }
  }
  return h;
}

std::string json_escape(const std::string &s)
{
  std::string o;
  for (unsigned char c : s) {
    switch (c) {
      case '"': o += "\\\""; break;
      case '\\': o += "\\\\"; break;
      case '\n': o += "\\n"; break;
      case '\r': o += "\\r"; break;
      case '\t': o += "\\t"; break;
      default:
        if (c < 0x20) { char b[8]; snprintf(b, sizeof(b), "\\u%04x", c); o += b; } else { o += static_cast<char>(c); }
    }
  }
  return "\"" + o + "\"";
}

std::string xml_decode(std::string_view v)
{
  std::string o;
  for (size_t i = 0; i < v.size(); i++) {
    if (v[i] != '&') { o += v[i]; continue; }
    size_t e = v.find(';', i);
    if (e == std::string_view::npos) { o += v[i]; continue; }
    std::string_view ent = v.substr(i + 1, e - i - 1);
    if (ent == "lt") { o += '<'; }
    else if (ent == "gt") { o += '>'; }
    else if (ent == "amp") { o += '&'; }
    else if (ent == "quot") { o += '"'; }
    else if (ent == "apos") { o += '\''; }
    else if (!ent.empty() && ent[0] == '#') {
      unsigned long cp = ent.size() > 1 && (ent[1] == 'x' || ent[1] == 'X') ? strtoul(std::string(ent.substr(2)).c_str(), nullptr, 16)
                                                                               : strtoul(std::string(ent.substr(1)).c_str(), nullptr, 10);
      if (cp < 0x80) { o += static_cast<char>(cp); }
      else if (cp < 0x800) { o += static_cast<char>(0xC0 | (cp >> 6)); o += static_cast<char>(0x80 | (cp & 0x3F)); }
      else if (cp < 0x10000) { o += static_cast<char>(0xE0 | (cp >> 12)); o += static_cast<char>(0x80 | ((cp >> 6) & 0x3F)); o += static_cast<char>(0x80 | (cp & 0x3F)); }
      else { o += static_cast<char>(0xF0 | (cp >> 18)); o += static_cast<char>(0x80 | ((cp >> 12) & 0x3F)); o += static_cast<char>(0x80 | ((cp >> 6) & 0x3F)); o += static_cast<char>(0x80 | (cp & 0x3F)); }
    } else { o += v[i]; continue; }
    i = e;
  }
  return o;
}

using XmlAttrs = std::map<std::string, std::string>;

// Older Paparazzi XML uses VAR=/NAME=/SHORTNAME=; newer dumps use var=/name=/shortname=.
std::string attr_ci(const XmlAttrs &at, const char *key)
{
  const size_t n = std::strlen(key);
  for (const auto &kv : at) {
    if (kv.first.size() != n) { continue; }
    bool ok = true;
    for (size_t i = 0; i < n; i++) {
      if (std::tolower(static_cast<unsigned char>(kv.first[i])) !=
          std::tolower(static_cast<unsigned char>(key[i]))) {
        ok = false;
        break;
      }
    }
    if (ok) { return kv.second; }
  }
  return "";
}

bool has_attr_ci(const XmlAttrs &at, const char *key)
{
  const size_t n = std::strlen(key);
  for (const auto &kv : at) {
    if (kv.first.size() != n) { continue; }
    bool ok = true;
    for (size_t i = 0; i < n; i++) {
      if (std::tolower(static_cast<unsigned char>(kv.first[i])) !=
          std::tolower(static_cast<unsigned char>(key[i]))) {
        ok = false;
        break;
      }
    }
    if (ok) { return true; }
  }
  return false;
}

// Calls cb(tag, attributes, closing, selfclosing) for every element tag in the XML (comments and declarations skipped)
template <typename F>
void for_each_tag(const std::string &x, F cb)
{
  size_t p = 0;
  while ((p = x.find('<', p)) != std::string::npos) {
    if (x.compare(p, 4, "<!--") == 0) { p = x.find("-->", p); if (p == std::string::npos) { break; } p += 3; continue; }
    if (x.compare(p, 2, "<?") == 0 || x.compare(p, 2, "<!") == 0) { p = x.find('>', p); if (p == std::string::npos) { break; } continue; }
    size_t e = p + 1;
    if (e < x.size() && x[e] == '/') {
      cb(std::string(), XmlAttrs(), true, false);
      p = x.find('>', p);
      if (p == std::string::npos) { break; }
      continue;
    }
    size_t ns = e;
    while (e < x.size() && !isspace(static_cast<unsigned char>(x[e])) && x[e] != '>' && x[e] != '/') { ++e; }
    std::string tag = x.substr(ns, e - ns);
    XmlAttrs at;
    bool selfclose = false;
    for (;;) {
      while (e < x.size() && isspace(static_cast<unsigned char>(x[e]))) { ++e; }
      if (e >= x.size()) { break; }
      if (x[e] == '/') { selfclose = true; ++e; continue; }
      if (x[e] == '>') { ++e; break; }
      size_t as = e;
      while (e < x.size() && x[e] != '=' && !isspace(static_cast<unsigned char>(x[e])) && x[e] != '>') { ++e; }
      std::string an = x.substr(as, e - as);
      if (e >= x.size() || x[e] != '=' || e + 1 >= x.size()) { continue; }
      char qc = x[e + 1];
      size_t ve = x.find(qc, e + 2);
      if (ve == std::string::npos) { break; }
      at[an] = xml_decode(std::string_view(x).substr(e + 2, ve - e - 2));
      e = ve + 1;
    }
    p = e;
    cb(tag, at, false, selfclose);
  }
}

std::string attr_json(const XmlAttrs &at, const char *k)
{
  auto i = at.find(k);
  return i == at.end() ? std::string("null") : json_escape(i->second);
}

struct SettingInfo {
  std::string var, shortname, unit, parent;
  bool has_unit = false;
};

// Settings of the <settings> XML in DL_VALUE index order (depth first order of <dl_setting>), with the name of the
// nearest named parent <dl_settings> group.
std::vector<SettingInfo> settings_entries(const std::string &x)
{
  std::vector<std::string> stack;  // group name for dl_settings, "" for other open elements
  std::vector<SettingInfo> out;
  for_each_tag(x, [&](const std::string &tag, const XmlAttrs &at, bool closing, bool selfclose) {
    if (closing) {
      if (!stack.empty()) { stack.pop_back(); }
      return;
    }
    if (tag == "dl_setting") {
      SettingInfo si;
      for (auto it = stack.rbegin(); it != stack.rend(); ++it) {
        if (!it->empty()) { si.parent = *it; break; }
      }
      si.var = attr_ci(at, "var");
      si.shortname = attr_ci(at, "shortname");
      si.unit = attr_ci(at, "unit");
      si.has_unit = has_attr_ci(at, "unit");
      out.push_back(std::move(si));
    }
    if (!selfclose) {
      std::string n = attr_ci(at, "name");
      stack.push_back(tag == "dl_settings" && !n.empty() ? n : "");
    }
  });
  return out;
}

std::vector<SettingInfo> merge_settings_entries(const std::string &x);

// Flat list of the settings: index, var, shortname, unit and parent group name.
std::string settings_list_json(const std::string &x)
{
  std::string out;
  int index = 0;
  for (const auto &si : merge_settings_entries(x)) {
    out += std::string(out.empty() ? "" : ",") + "\n        {\"index\": " + std::to_string(index++) + ", \"var\": " +
           json_escape(si.var) + ", \"shortname\": " + json_escape(si.shortname) + ", \"unit\": " +
           (si.has_unit ? json_escape(si.unit) : "null") + ", \"parent\": " +
           (si.parent.empty() ? "null" : json_escape(si.parent)) + "}";
  }
  return out.empty() ? "[]" : "[" + out + "\n      ]";
}

// Several <settings> / <dl_settings> dumps of the same aircraft: keep one copy if a later
// block already contains an earlier one (GCS), otherwise concatenate (one XML file per module).
std::vector<SettingInfo> merge_settings_entries(const std::string &x)
{
  static const std::string mark = "<!-- SETTINGS_BLOCK -->";
  auto prefix_of = [](const std::vector<SettingInfo> &a, const std::vector<SettingInfo> &b) {
    if (a.size() > b.size()) { return false; }
    for (size_t i = 0; i < a.size(); i++) {
      if (a[i].var != b[i].var || a[i].shortname != b[i].shortname) { return false; }
    }
    return true;
  };
  std::vector<SettingInfo> info;
  size_t pos = 0;
  while (pos < x.size()) {
    size_t n = x.find(mark, pos);
    std::string piece = x.substr(pos, n == std::string::npos ? std::string::npos : n - pos);
    pos = n == std::string::npos ? x.size() : n + mark.size();
    std::vector<SettingInfo> p = settings_entries(piece);
    if (p.empty()) { continue; }
    if (info.empty()) { info = std::move(p); continue; }
    if (prefix_of(info, p)) { info = std::move(p); }
    else if (prefix_of(p, info)) { continue; }
    else { info.insert(info.end(), p.begin(), p.end()); }
  }
  return info;
}

std::string setting_var_json(const std::vector<SettingInfo> &info, int i)
{
  if (i < 0 || static_cast<size_t>(i) >= info.size()) { return "null"; }
  const SettingInfo &si = info[static_cast<size_t>(i)];
  if (!si.var.empty()) { return json_escape(si.var); }
  if (!si.shortname.empty()) { return json_escape(si.shortname); }
  return "null";
}

// Flight plan reduced to its reference point, waypoints (with lat/lon) and blocks (with index).
// Waypoints given as local x (east) / y (north) in meters are converted to lat/lon around lat0/lon0.
std::string flight_plan_summary_json(const std::string &x)
{
  double lat0 = 0, lon0 = 0, default_alt = 0, ground_alt = 0;
  bool has_ref = false;
  std::string name, wps, blocks;
  int wp_index = 0, block_index = 0;
  bool in_wps = false, in_blocks = false;
  int block_depth = 0;

  auto num_attr = [](const XmlAttrs &at, const char *k, double &out) {
    auto i = at.find(k);
    if (i == at.end()) { return false; }
    char *endp;
    out = strtod(i->second.c_str(), &endp);
    return endp != i->second.c_str();
  };

  // WGS84 local tangent plane approximation (accurate to cm over a few km)
  auto enu_to_geo = [&](double east, double north, double &lat, double &lon) {
    const double a = 6378137.0, e2 = 6.69437999014e-3;
    double phi = lat0 * M_PI / 180.0, s = sin(phi);
    double w = sqrt(1 - e2 * s * s);
    double rn = a / w, rm = a * (1 - e2) / (w * w * w);
    lat = lat0 + (north / rm) * 180.0 / M_PI;
    lon = lon0 + (east / (rn * cos(phi))) * 180.0 / M_PI;
  };

  for_each_tag(x, [&](const std::string &tag, const XmlAttrs &at, bool closing, bool selfclose) {
    if (closing) {
      if (in_blocks && block_depth > 0) { block_depth--; if (block_depth == 0) { in_blocks = false; } }
      else if (in_wps) { in_wps = false; }
      return;
    }
    if (tag == "flight_plan") {
      has_ref = num_attr(at, "lat0", lat0) && num_attr(at, "lon0", lon0);
      num_attr(at, "alt", default_alt);
      num_attr(at, "ground_alt", ground_alt);
      auto n = at.find("name");
      if (n != at.end()) { name = n->second; }
    } else if (tag == "waypoints") {
      in_wps = !selfclose;
    } else if (tag == "blocks") {
      in_blocks = !selfclose;
      block_depth = in_blocks ? 1 : 0;
    } else if (in_wps && tag == "waypoint") {
      double wx, wy, wlat, wlon, walt = default_alt;
      bool has_xy = num_attr(at, "x", wx) && num_attr(at, "y", wy);
      bool has_geo = num_attr(at, "lat", wlat) && num_attr(at, "lon", wlon);
      if (!has_geo && has_xy && has_ref) { enu_to_geo(wx, wy, wlat, wlon); has_geo = true; }
      if (!has_geo) { wlat = wlon = 0; }
      num_attr(at, "alt", walt);
      char buf[200];
      std::string pos;
      if (has_xy) {
        snprintf(buf, sizeof(buf), ", \"x\": %.3f, \"y\": %.3f", wx, wy);
        pos += buf;
      }
      if (has_geo) {
        snprintf(buf, sizeof(buf), ", \"lat\": %.7f, \"lon\": %.7f", wlat, wlon);
        pos += buf;
      } else {
        pos += ", \"lat\": null, \"lon\": null";
      }
      snprintf(buf, sizeof(buf), ", \"alt_m\": %.1f", walt);
      wps += std::string(wps.empty() ? "" : ",") + "\n          {\"index\": " + std::to_string(wp_index++) + ", \"name\": " +
             attr_json(at, "name") + pos + buf + "}";
    } else if (in_blocks && block_depth == 1 && tag == "block") {
      blocks += std::string(blocks.empty() ? "" : ",") + "\n          {\"index\": " + std::to_string(block_index++) +
                ", \"name\": " + attr_json(at, "name") + "}";
      if (!selfclose) { block_depth++; }
    } else if (in_blocks && !selfclose) {
      block_depth++;
    }
  });

  char buf[160];
  snprintf(buf, sizeof(buf), "\"lat0\": %.7f, \"lon0\": %.7f, \"default_alt_m\": %.1f, \"ground_alt_m\": %.1f", lat0, lon0,
           default_alt, ground_alt);
  return "{\n        \"name\": " + json_escape(name) + ",\n        " + (has_ref ? std::string(buf) : "\"lat0\": null, \"lon0\": null") +
         ",\n        \"waypoints\": [" + wps + "\n        ],\n        \"blocks\": [" + blocks + "\n        ]\n      }";
}

// JSON string with the UTC time (ISO 8601) or null when unknown
std::string utc_iso(double epoch)
{
  if (epoch < 0) { return "null"; }
  time_t sec = static_cast<time_t>(epoch);
  int ms = static_cast<int>((epoch - sec) * 1000 + 0.5);
  if (ms == 1000) { sec++; ms = 0; }
  struct tm tm;
  gmtime_r(&sec, &tm);
  char buf[40];
  snprintf(buf, sizeof(buf), "\"%04d-%02d-%02dT%02d:%02d:%02d.%03dZ\"", tm.tm_year + 1900, tm.tm_mon + 1,
           tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec, ms);
  return buf;
}

std::string num(double v)
{
  char buf[32];
  snprintf(buf, sizeof(buf), "%.7g", v);
  return buf;
}

// Compares the DL_VALUE reports of every setting with the first value reported (the "default" of this log).
// A change is detected at the report that shows a different value than the previous report, so the real change
// happened between previous_report_s and time_s.
template <typename F>
std::string settings_changes_json(const AircraftState &s, const std::vector<SettingInfo> &info, F utc)
{
  struct Change { double prev_t, t, from, to; bool in_flight; int index; };
  struct Track { bool seen = false; double first = 0, last = 0, last_t = 0; std::vector<Change> changes; };
  std::map<int, Track> tracks;
  std::vector<Change> timeline;
  for (const auto &sm : s.setting_samples) {
    Track &tr = tracks[sm.index];
    if (!tr.seen) {
      tr.seen = true; tr.first = tr.last = sm.value; tr.last_t = sm.t;
      continue;
    }
    if (fabs(sm.value - tr.last) > 1e-5 * std::max(1.0, fabs(tr.last))) {
      Change c{tr.last_t, sm.t, tr.last, sm.value, sm.in_flight, sm.index};
      tr.changes.push_back(c);
      timeline.push_back(c);
    }
    tr.last = sm.value; tr.last_t = sm.t;
  }
  auto name_of_idx = [&](int i) { return setting_var_json(info, i); };

  std::ostringstream o;
  size_t changed = 0;
  for (const auto &kv : tracks) { changed += !kv.second.changes.empty(); }
  o << "{\n        \"default_source\": \"first DL_VALUE reported for each setting in this log\""
    << ",\n        \"num_settings_reported\": " << tracks.size()
    << ",\n        \"num_settings_changed\": " << changed << ",\n        \"timeline\": [";
  bool f = true;
  for (const auto &c : timeline) {
    o << (f ? "" : ",") << "\n          {\"time_s\": " << num(c.t) << ", \"utc\": " << utc(c.t)
      << ", \"previous_report_s\": " << num(c.prev_t) << ", \"index\": " << c.index
      << ", \"var\": " << name_of_idx(c.index) << ", \"from\": " << num(c.from) << ", \"to\": " << num(c.to)
      << ", \"in_flight\": " << (c.in_flight ? "true" : "false") << "}";
    f = false;
  }
  o << (timeline.empty() ? "" : "\n        ") << "],\n        \"by_setting\": [";
  f = true;
  for (const auto &kv : tracks) {
    const Track &tr = kv.second;
    if (tr.changes.empty()) { continue; }
    const SettingInfo *si = kv.first >= 0 && (size_t)kv.first < info.size() ? &info[kv.first] : nullptr;
    o << (f ? "" : ",") << "\n          {\"index\": " << kv.first << ", \"var\": " << name_of_idx(kv.first)
      << ", \"shortname\": " << (si ? json_escape(si->shortname) : "null")
      << ", \"parent\": " << (si && !si->parent.empty() ? json_escape(si->parent) : "null")
      << ", \"unit\": " << (si && si->has_unit ? json_escape(si->unit) : "null")
      << ", \"default\": " << num(tr.first) << ", \"final\": " << num(tr.last)
      << ", \"num_changes\": " << tr.changes.size() << ", \"changes\": [";
    bool g = true;
    for (const auto &c : tr.changes) {
      o << (g ? "" : ", ") << "{\"time_s\": " << num(c.t) << ", \"utc\": " << utc(c.t)
        << ", \"from\": " << num(c.from) << ", \"to\": " << num(c.to)
        << ", \"in_flight\": " << (c.in_flight ? "true" : "false") << "}";
      g = false;
    }
    o << "]}";
    f = false;
  }
  o << (changed ? "\n        " : "") << "]\n      }";
  return o.str();
}

// Wind vector plus derived speed and the compass direction the wind comes from; null when not valid
std::string wind_json(const Wind &w)
{
  if (!w.valid) { return "null"; }
  double speed = hypot(w.east, w.north);
  double from = fmod(atan2(-w.east, -w.north) * 180.0 / M_PI + 360.0, 360.0);
  return "{\"speed_ms\": " + num(round(speed * 100) / 100) + ", \"from_deg\": " + num(round(from * 10) / 10) +
         ", \"east_ms\": " + num(round(w.east * 100) / 100) + ", \"north_ms\": " + num(round(w.north * 100) / 100) +
         ", \"up_ms\": " + (w.has_up ? num(round(w.up * 100) / 100) : "null") +
         ", \"temperature_c\": " + (w.has_temp ? num(round(w.temp * 10) / 10) : "null") + "}";
}

std::string ins_json(const InsState &n)
{
  if (!n.valid) { return "null"; }
  char buf[400];
  double gs = hypot(n.vel[0], n.vel[1]);
  snprintf(buf, sizeof(buf),
           "{\"position_ned_m\": {\"north\": %.3f, \"east\": %.3f, \"down\": %.3f}, "
           "\"velocity_ned_ms\": {\"north\": %.3f, \"east\": %.3f, \"down\": %.3f}, \"groundspeed_ms\": %.3f, "
           "\"acceleration_ned_ms2\": {\"north\": %.3f, \"east\": %.3f, \"down\": %.3f}}",
           n.pos[0], n.pos[1], n.pos[2], n.vel[0], n.vel[1], n.vel[2], gs, n.acc[0], n.acc[1], n.acc[2]);
  return buf;
}

// Bump when the content or meaning of the JSON output changes so stored results can be recognised as outdated
constexpr const char *PARSER_NAME = "fastreader";
constexpr const char *PARSER_VERSION = "1.5.0";

struct GpsClock {
  size_t count = 0;
  double first = 0, last = 0, low = 0, high = 0, mean = 0;

  void add(double t, int week, double itow, int fix) {
    if (fix < 3 || week < 0 || !std::isfinite(itow) || itow < 0 || itow >= 604800000) { return; }
    const double gps = 315964800.0 + week * 604800.0 + itow / 1000;
    // UTC instants at which GPS-UTC increased, from July 1981 through January 2017.
    static const double leaps[] = {362793600, 394329600, 425865600, 489024000, 567993600,
      631152000, 662688000, 709948800, 741484800, 773020800, 820454400, 867715200,
      915148800, 1136073600, 1230768000, 1341100800, 1435708800, 1483228800};
    int offset = 0;
    for (double leap : leaps) {
      if (gps >= leap + offset + 1) { ++offset; }
    }
    const double utc = gps - offset;
    if (utc < 946684800 || utc > std::time(nullptr) + 86400.0 || !std::isfinite(t)) { return; }
    const double start = utc - t;
    if (!count) { first = last = t; low = high = mean = start; }
    low = std::min(low, start);
    high = std::max(high, start);
    last = std::max(last, t);
    mean += (start - mean) / ++count;
  }
};

struct ClockDecision {
  double start;
  const char *source;
  std::string warning;
};

ClockDecision choose_clock(double companion, const std::map<int, GpsClock> &clocks) {
  ClockDecision out{companion, companion < 0 ? "unknown" : "companion", ""};
  double low = 0, high = 0, sum = 0;
  size_t aircraft = 0;
  for (const auto &entry : clocks) {
    const auto &c = entry.second;
    if (!c.count) { continue; }
    if (c.count < 3 || c.last - c.first < 30 || c.high - c.low > 5) {
      out.warning = "GPS clock evidence is insufficient or inconsistent; companion timing retained.";
      return out;
    }
    low = aircraft ? std::min(low, c.low) : c.low;
    high = aircraft ? std::max(high, c.high) : c.high;
    sum += c.mean;
    ++aircraft;
  }
  if (!aircraft) { return out; }
  if (high - low > 5) {
    out.warning = "Aircraft GPS clocks conflict; companion timing retained.";
    return out;
  }
  const double gps_start = sum / aircraft;
  const double delta = gps_start - companion;
  const double days = std::round(delta / 86400);
  if (companion < 0 || (std::fabs(days) >= 1 && std::fabs(delta - days * 86400) <= 60)) {
    out.start = gps_start;
    out.source = "gps_corrected";
    out.warning = "Timing corrected using consistent GPS week/time-of-week samples spanning at least 30 seconds.";
  } else if (std::fabs(delta) > 5) {
    out.warning = "GPS and companion clocks disagree by " + std::to_string(delta)
      + " seconds; not a clear whole-day error, so companion timing retained.";
  }
  return out;
}

// Payload positions of the wanted fields of one telemetry message, looked up by name in the
// <protocol> of the .log once, so the per-line work stays a plain token split.
struct MsgFields {
  static constexpr int MAX_WANTED = 12, MAX_POS = 32;
  int pos[MAX_WANTED];  // position of wanted field i, -1 if this protocol does not have it
  int n = 0;
  int split = 0;        // tokens to split: highest wanted position + 1, 0 = message not usable
};

struct Picked {
  std::string_view v[MsgFields::MAX_WANTED];  // empty if the field is absent
  std::string_view operator[](int i) const { return v[i]; }
};

inline Picked pick(const MsgFields &m, const char *q, const char *eol)
{
  std::string_view tok[MsgFields::MAX_POS];
  for (int i = 0; i < m.split; i++) { tok[i] = next_token(q, eol); }
  Picked f;
  for (int i = 0; i < m.n; i++) {
    if (m.pos[i] >= 0) { f.v[i] = tok[m.pos[i]]; }
  }
  return f;
}

// Field names ending in '?' are optional. A missing required field disables the message.
MsgFields resolve_fields(const LogHeader &h, const char *msg, std::initializer_list<const char *> wanted)
{
  MsgFields m;
  auto it = h.telemetry.find(msg);
  if (it == h.telemetry.end()) {
    std::cerr << " - " << msg << " is not in the protocol of the LOG file, ignored\n";
    return m;
  }
  int split = 0;
  for (const char *w : wanted) {
    std::string name(w);
    const bool optional = !name.empty() && name.back() == '?';
    if (optional) { name.pop_back(); }
    auto f = std::find(it->second.begin(), it->second.end(), name);
    int p = f == it->second.end() ? -1 : static_cast<int>(f - it->second.begin());
    if (p >= MsgFields::MAX_POS) { p = -1; }
    if (p < 0 && !optional) {
      std::cerr << " - " << msg << " has no field " << name << " in the protocol of the LOG file, ignored\n";
      return MsgFields{};
    }
    m.pos[m.n++] = p;
    split = std::max(split, p + 1);
  }
  m.split = split;
  return m;
}

// Onboard logs often stamp each line with get_sys_time_usec()/1e6. That counter is
// uint32 microseconds, so the time column wraps every 2^32 us (~71.58 min). Long
// flights then look like 13 minutes with the graphs drawn on top of themselves.
constexpr double SYS_TIME_WRAP_S = 4294.967296;  // 2^32 * 1e-6

// Portable SHA-256 of a memory block (runs on its own thread next to the parser)
std::string sha256_hex(const unsigned char *p, size_t n)
{
  static const uint32_t K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};
  uint32_t h[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
  auto rotr = [](uint32_t x, int k) { return (x >> k) | (x << (32 - k)); };
  auto block = [&](const unsigned char *b) {
    uint32_t w[64];
    for (int i = 0; i < 16; i++) {
      w[i] = (uint32_t)b[4 * i] << 24 | (uint32_t)b[4 * i + 1] << 16 | (uint32_t)b[4 * i + 2] << 8 | b[4 * i + 3];
    }
    for (int i = 16; i < 64; i++) {
      uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
      uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
      w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = h[0], b_ = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
    for (int i = 0; i < 64; i++) {
      uint32_t t1 = hh + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i];
      uint32_t t2 = (rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b_) ^ (a & c) ^ (b_ & c));
      hh = g; g = f; f = e; e = d + t1; d = c; c = b_; b_ = a; a = t1 + t2;
    }
    h[0] += a; h[1] += b_; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
  };
  size_t full = n / 64;
  for (size_t i = 0; i < full; i++) { block(p + 64 * i); }
  unsigned char tail[128] = {0};
  size_t rem = n - full * 64;
  memcpy(tail, p + full * 64, rem);
  tail[rem] = 0x80;
  size_t tl = rem < 56 ? 64 : 128;
  uint64_t bits = (uint64_t)n * 8;
  for (int i = 0; i < 8; i++) { tail[tl - 1 - i] = (unsigned char)(bits >> (8 * i)); }
  for (size_t i = 0; i < tl; i += 64) { block(tail + i); }
  char out[65];
  for (int i = 0; i < 8; i++) { snprintf(out + 8 * i, 9, "%08x", h[i]); }
  return out;
}

}  // namespace

int main(int argc, char *argv[])
{
  // Number of flight path points per second (0 disables the path)
  double path_rate = 5.0;
  std::string data_file, known_sha256;  // known_sha256: precomputed by the caller, skips hashing here
  for (int i = 1; i < argc; i++) {
    std::string a = argv[i];
    if (a == "--sha256" && i + 1 < argc) {
      known_sha256 = argv[++i];
      continue;
    }
    if (a == "--version") {
      std::cout << PARSER_VERSION << "\n";
      return 0;
    }
    if ((a == "-r" || a == "--path-rate") && i + 1 < argc) {
      char *endp;
      path_rate = strtod(argv[++i], &endp);
      if (*endp != '\0' || path_rate < 0) {
        std::cerr << "Invalid path rate: " << argv[i] << "\n";
        return 1;
      }
    } else if (data_file.empty()) {
      data_file = a;
    } else {
      data_file.clear();
      break;
    }
  }
  if (data_file.empty()) {
    std::cerr << "Usage: " << argv[0] << " [-r|--path-rate <points per second, default 5, 0 = off>] [--sha256 <hex of file, skips hashing>] [--version] <file.data>\n";
    return 1;
  }

  std::string log_file = data_file;
  if (log_file.size() > 4) { log_file.replace(log_file.size() - 4, 4, "log"); }
  if (access(log_file.c_str(), R_OK) != 0) {
    std::cerr << " - Cannot open LOG file " << log_file << "\n";
    return 1;
  }
  const LogHeader hdr = read_log_header(log_file);
  enum { FP_PHI, FP_THETA, FP_PSI };
  const MsgFields fp_f = resolve_fields(hdr, "ROTORCRAFT_FP", {"phi", "theta", "psi"});
  enum { NAV_BLOCK, NAV_STAGE };
  const MsgFields nav_f = resolve_fields(hdr, "ROTORCRAFT_NAV_STATUS", {"cur_block", "cur_stage"});
  enum { ST_MODE, ST_IN_FLIGHT, ST_MOTORS, ST_H_MODE, ST_V_MODE, ST_VSUPPLY };
  const MsgFields st_f = resolve_fields(hdr, "ROTORCRAFT_STATUS", {"ap_mode", "ap_in_flight", "ap_motors_on?",
                                                                   "ap_h_mode?", "ap_v_mode?", "vsupply?"});
  enum { GPS_EX, GPS_EY, GPS_EZ, GPS_LAT, GPS_LON, GPS_ALT, GPS_NUMSV, GPS_FIX };
  const MsgFields gps_f = resolve_fields(hdr, "GPS_INT", {"ecef_x", "ecef_y", "ecef_z", "lat", "lon", "alt",
                                                          "numsv", "fix"});
  enum { DL_INDEX, DL_VALUE_ };
  const MsgFields dl_f = resolve_fields(hdr, "DL_VALUE", {"index", "value"});
  enum { WI_FLAGS, WI_EAST, WI_NORTH, WI_UP, WI_TEMP };
  const MsgFields wind_f = resolve_fields(hdr, "WIND_INFO_RET", {"flags", "east", "north", "up?", "temp?"});
  const MsgFields ins_f = resolve_fields(hdr, "INS", {"ins_x", "ins_y", "ins_z", "ins_xd", "ins_yd", "ins_zd",
                                                      "ins_xdd", "ins_ydd", "ins_zdd"});
  enum { Q_I, Q_X, Q_Y, Q_Z };
  const MsgFields quat_f = resolve_fields(hdr, "AHRS_REF_QUAT", {"body_qi", "body_qx", "body_qy", "body_qz"});
  enum { AD_PRESS, AD_DIFF_P, AD_AMSL, AD_AIRSPEED, AD_TAS };
  const MsgFields air_f = resolve_fields(hdr, "AIR_DATA", {"pressure", "diff_p?", "amsl_baro", "airspeed?", "tas?"});
  enum { FW_NAV_BLOCK, FW_NAV_STAGE, FW_NAV_FT };
  const MsgFields fw_nav_f = resolve_fields(hdr, "NAVIGATION", {"cur_block", "cur_stage", "flight_time"});
  enum { PPRZ_AP };
  const MsgFields pprz_f = resolve_fields(hdr, "PPRZ_MODE", {"ap_mode"});
  enum { GPSU_MODE, GPSU_EAST, GPSU_NORTH, GPSU_ALT, GPSU_SPEED, GPSU_ZONE };
  const MsgFields gpsu_f = resolve_fields(hdr, "GPS", {"mode", "utm_east", "utm_north", "alt", "speed?", "utm_zone"});
  const MsgFields gps_clock_f = resolve_fields(hdr, "GPS", {"mode?", "week?", "itow?"});
  const MsgFields lla_clock_f = resolve_fields(hdr, "GPS_LLA", {"mode?", "week?", "itow?"});
  enum { AT_PHI, AT_THETA, AT_PSI };
  const MsgFields att_f = resolve_fields(hdr, "ATTITUDE", {"phi", "theta", "psi"});
  enum { AS_EAS };
  const MsgFields as_f = resolve_fields(hdr, "AIRSPEED", {"airspeed"});
  enum { EN_VOLT };
  const MsgFields energy_f = resolve_fields(hdr, "ENERGY", {"voltage"});

  int fd = open(data_file.c_str(), O_RDONLY);
  struct stat st;
  if (fd < 0 || fstat(fd, &st) != 0) {
    std::cerr << " - Cannot open DATA file\n";
    return 1;
  }
  if (st.st_size == 0) {
    std::cerr << " - Empty DATA file\n";
    return 1;
  }
  const size_t size = st.st_size;
  void *map = mmap(nullptr, size, PROT_READ, MAP_PRIVATE | MAP_POPULATE, fd, 0);
  if (map == MAP_FAILED) {
    map = mmap(nullptr, size, PROT_READ, MAP_PRIVATE, fd, 0);
  }
  if (map == MAP_FAILED) {
    std::cerr << " - mmap failed\n";
    return 1;
  }
  madvise(map, size, MADV_SEQUENTIAL);
  const char *base = static_cast<const char *>(map);
  const char *end = base + size;
  std::string sha256;
  std::thread hasher;
  if (known_sha256.empty()) {
    hasher = std::thread([&] { sha256 = sha256_hex(reinterpret_cast<const unsigned char *>(base), size); });
  } else {
    sha256 = known_sha256;
  }

  std::map<int, AircraftState> acs;
  std::map<int, GpsClock> gps_clocks;
  double first_time = -1, last_time = 0;
  double prev_raw_time = -1, time_offset = 0;

  auto touch = [&](int ac, double t) -> AircraftState & {
    AircraftState &s = acs[ac];
    if (s.first_time < 0) { s.first_time = t; }
    s.last_time = t;
    return s;
  };

  auto apply_in_flight = [&](AircraftState &s, double t, bool now) {
    if (now && !s.in_flight) {
      Flight f;
      f.takeoff_time = t;
      f.has_takeoff_volt = s.has_volt;
      f.takeoff_volt = s.vsupply;
      f.takeoff_wind = s.wind;
      f.takeoff_ins = s.ins;
      f.has_pos = s.has_gps;
      f.has_optitrack = s.has_optitrack;
      f.lat = s.lat; f.lon = s.lon; f.alt = s.alt;
      s.flights.push_back(f);
    } else if (!now && s.in_flight && !s.flights.empty()) {
      Flight &f = s.flights.back();
      f.landing_time = t;
      f.complete = true;
      f.has_land_volt = s.has_volt;
      f.land_volt = s.vsupply;
      f.land_wind = s.wind;
      f.land_ins = s.ins;
      f.has_land_pos = s.has_gps;
      f.land_lat = s.lat; f.land_lon = s.lon; f.land_alt = s.alt;
    }
    s.in_flight = now;
    s.seen_status = true;
  };

  auto use_gps = [&](int ac, double t, double lat_d, double lon_d, double alt_d) {
    if (!Flight::plausible_ll(lat_d, lon_d)) { return; }
    AircraftState &s = touch(ac, t);
    s.has_gps = true;
    s.lat = lat_d; s.lon = lon_d; s.alt = alt_d;
    if (s.in_flight && !s.flights.empty()) {
      Flight &f = s.flights.back();
      f.stats.add_gps(s.lat, s.lon, s.alt);
      f.add_gps_step(s.lat, s.lon, s.alt);
    }
    if (path_rate > 0 && s.in_flight && !s.flights.empty() &&
        static_cast<long>(t * path_rate) != s.last_path_bucket) {
      s.last_path_bucket = static_cast<long>(t * path_rate);
      s.flights.back().path.push_back({t, s.lat, s.lon, s.alt, s.flights.back().distance_m,
                                          s.flights.back().from_takeoff_m, s.has_baro, s.pressure, s.baro_alt,
                                          s.has_volt, s.has_as, s.vsupply, s.airspeed, s.tas, s.diff_p,
                                          s.has_euler, s.has_quat, s.roll, s.pitch, s.yaw, s.qi, s.qx,
                                          s.qy, s.qz, s.ap_mode, s.h_mode, s.v_mode, s.block, s.stage,
                                          parse_int_list(s.actuators_raw), s.wind, s.ins, s.has_gs, s.gndspeed});
    }
  };

  uint64_t ground_msgs = 0;
  const char *p = base;
  while (p < end) {
    const char *eol = static_cast<const char *>(memchr(p, '\n', end - p));
    if (!eol) { eol = end; }
    const char *line = p;
    p = eol + 1;

    // "<time> <ac_id> <NAME> <payload>"
    const char *q = line;
    double t;
    auto r = std::from_chars(q, eol, t);
    if (r.ec != std::errc()) { continue; }
    q = r.ptr;
    if (prev_raw_time >= 0 && t + 2000.0 < prev_raw_time) { time_offset += SYS_TIME_WRAP_S; }
    prev_raw_time = t;
    t += time_offset;
    int ac;
    if (!to_num(next_token(q, eol), ac)) { continue; }
    std::string_view name = next_token(q, eol);
    if (name.empty()) { continue; }

    if (first_time < 0) { first_time = t; }
    last_time = t;

    // Messages that only exist on the ground (link statistics, setting requests)
    if ((name[0] == 'L' && name == "LINK_REPORT") || (name[0] == 'P' && name == "PONG") ||
        (name[0] == 'G' && name == "GET_SETTING")) {
      ++ground_msgs;
    }

    // Only look at messages of interest, dispatch on first char first
    switch (name[0]) {
      case 'R':
        if (name == "ROTORCRAFT_FP") {
          if (!fp_f.split) { break; }
          // phi, theta, psi: int32 with 12 fractional bits, rad
          const Picked f = pick(fp_f, q, eol);
          int32_t phi, theta, psi;
          if (!to_num(f[FP_PHI], phi) || !to_num(f[FP_THETA], theta) || !to_num(f[FP_PSI], psi)) { break; }
          constexpr double k = 180.0 / M_PI / 4096.0;
          AircraftState &s = touch(ac, t);
          s.has_euler = true;
          s.roll = phi * k; s.pitch = theta * k; s.yaw = psi * k;
          if (s.in_flight && !s.flights.empty()) {
            FlightStats &fs = s.flights.back().stats;
            fs.max_tilt_deg = std::max(fs.max_tilt_deg, std::hypot(s.roll, s.pitch));
          }
          break;
        }
        if (name == "ROTORCRAFT_NAV_STATUS") {
          if (!nav_f.split) { break; }
          const Picked f = pick(nav_f, q, eol);
          int block, stage;
          if (!to_num(f[NAV_BLOCK], block) || !to_num(f[NAV_STAGE], stage)) { break; }
          AircraftState &s = touch(ac, t);
          s.block = block; s.stage = stage;
          break;
        }
        if (name == "ROTORCRAFT_STATUS") {
          if (!st_f.split) { break; }
          const Picked f = pick(st_f, q, eol);
          int ap_mode, in_flight, h_mode, v_mode;
          if (!to_num(f[ST_MODE], ap_mode) || !to_num(f[ST_IN_FLIGHT], in_flight)) { break; }
          int motors_on = -1;
          to_num(f[ST_MOTORS], motors_on);
          bool has_hv = to_num(f[ST_H_MODE], h_mode) && to_num(f[ST_V_MODE], v_mode);
          float vsupply;
          bool has_v = to_num(f[ST_VSUPPLY], vsupply);
          AircraftState &s = touch(ac, t);
          s.ap_mode = ap_mode;
          if (has_hv) { s.h_mode = h_mode; s.v_mode = v_mode; }
          if (has_v) { s.has_volt = true; s.vsupply = vsupply; }
          bool now = in_flight != 0;
          if (now && motors_on >= 0 && !s.flights.empty() && s.in_flight) {
            FlightStats &fs = s.flights.back().stats;
            ++fs.status_msgs;
            fs.motors_on_msgs += motors_on != 0;
          }
          apply_in_flight(s, t, now);
        }
        break;
      case 'N':
        if (name == "NAVIGATION") {
          if (!fw_nav_f.split) { break; }
          const Picked f = pick(fw_nav_f, q, eol);
          int block, stage, flight_time;
          if (!to_num(f[FW_NAV_BLOCK], block) || !to_num(f[FW_NAV_STAGE], stage) ||
              !to_num(f[FW_NAV_FT], flight_time)) { break; }
          AircraftState &s = touch(ac, t);
          s.block = block;
          s.stage = stage;
          apply_in_flight(s, t, flight_time > 0);
        }
        break;
      case 'P':
        if (name == "PPRZ_MODE") {
          if (!pprz_f.split) { break; }
          const Picked f = pick(pprz_f, q, eol);
          int ap_mode;
          if (!to_num(f[PPRZ_AP], ap_mode)) { break; }
          touch(ac, t).ap_mode = ap_mode;
        }
        break;
      case 'G':
        if (name == "GPS" || name == "GPS_LLA") {
          const Picked clock = pick(name == "GPS" ? gps_clock_f : lla_clock_f, q, eol);
          int fix, week;
          double itow;
          if (to_num(clock[0], fix) && to_num(clock[1], week) && to_num(clock[2], itow)) {
            gps_clocks[ac].add(t, week, itow, fix);
          }
        }
        if (name == "GPS_INT") {
          if (!gps_f.split) { break; }
          // ecef (cm), lat/lon (1e-7 deg), alt (mm)
          const Picked f = pick(gps_f, q, eol);
          int32_t ex, ey, ez, lat, lon, alt;
          if (!to_num(f[GPS_EX], ex) || !to_num(f[GPS_EY], ey) || !to_num(f[GPS_EZ], ez) ||
              !to_num(f[GPS_LAT], lat) || !to_num(f[GPS_LON], lon) || !to_num(f[GPS_ALT], alt)) { break; }
          int numsv = 0, fix = 0;
          to_num(f[GPS_NUMSV], numsv);
          to_num(f[GPS_FIX], fix);
          if (fix < 3) { break; }
          // OptiTrack injected as GPS typically has a 3D fix and no satellites.
          if (numsv == 0) { touch(ac, t).has_optitrack = true; }
          double lat_d = lat * 1e-7, lon_d = lon * 1e-7, alt_d = alt * 1e-3;
          // Some onboard dumps drop a digit of lat/lon (528654232 -> 52865423), which the 1e-7
          // scale turns into West Africa. ECEF in the same message is still the real position.
          double elat, elon, ealt;
          if (ecef_to_lla(ex / 100.0, ey / 100.0, ez / 100.0, elat, elon, ealt)) {
            const double R = std::hypot(ex / 100.0, std::hypot(ey / 100.0, ez / 100.0));
            const bool dummy = std::fabs(R - 6378137.0) < 50 && std::abs(ey) < 100 && std::abs(ez) < 100;
            if (!dummy && R > 6.3e6 && R < 6.5e6 && earth_distance_m(lat_d, lon_d, elat, elon) > 500) {
              const double lat6 = lat * 1e-6, lon6 = lon * 1e-6;
              if (earth_distance_m(lat6, lon6, elat, elon) < 500) {
                lat_d = lat6; lon_d = lon6; alt_d = alt * 1e-2;
              } else {
                lat_d = elat; lon_d = elon; alt_d = ealt;
              }
            }
          }
          if (!Flight::plausible_ll(lat_d, lon_d)) { break; }
          use_gps(ac, t, lat_d, lon_d, alt_d);
        } else if (name == "GPS") {
          if (!gpsu_f.split) { break; }
          const Picked f = pick(gpsu_f, q, eol);
          int mode = 0, zone = 0, speed_cm = 0;
          int32_t east = 0, north = 0, alt = 0;
          if (!to_num(f[GPSU_MODE], mode) || !to_num(f[GPSU_EAST], east) || !to_num(f[GPSU_NORTH], north) ||
              !to_num(f[GPSU_ALT], alt) || !to_num(f[GPSU_ZONE], zone)) { break; }
          to_num(f[GPSU_SPEED], speed_cm);
          if (mode < 3) { break; }
          double lat_d = 0, lon_d = 0;
          if (!utm_to_lla(east / 100.0, north / 100.0, zone, lat_d, lon_d)) { break; }
          AircraftState &st = touch(ac, t);
          st.has_gs = true;
          st.gndspeed = speed_cm / 100.0;
          use_gps(ac, t, lat_d, lon_d, alt * 1e-3);
        }
        break;
      case 'D':
        if (name == "DL_VALUE") {
          if (!dl_f.split) { break; }
          const Picked f = pick(dl_f, q, eol);
          int idx;
          double val;
          if (!to_num(f[DL_INDEX], idx) || !to_num(f[DL_VALUE_], val)) { break; }
          AircraftState &s = touch(ac, t);
          s.setting_samples.push_back({t, idx, val, s.in_flight});
        }
        break;
      case 'E':
        if (name == "EXTERNAL_POSE" || name == "EXTERNAL_POSE_SMALL" || name == "EXTERNAL_POSE_DOWN") {
          touch(ac, t).has_optitrack = true;
        } else if (name == "ENERGY") {
          if (!energy_f.split) { break; }
          const Picked f = pick(energy_f, q, eol);
          float vsupply;
          if (to_num(f[EN_VOLT], vsupply)) {
            AircraftState &s = touch(ac, t);
            s.has_volt = true;
            s.vsupply = vsupply;
          }
        }
        break;
      case 'W':
        if (name == "WIND_INFO_RET") {
          if (!wind_f.split) { break; }
          const Picked f = pick(wind_f, q, eol);
          int flags;
          double e, n, u = 0, tmp = 0;
          if (!to_num(f[WI_FLAGS], flags) || !to_num(f[WI_EAST], e) || !to_num(f[WI_NORTH], n)) { break; }
          const bool has_u = to_num(f[WI_UP], u), has_tmp = to_num(f[WI_TEMP], tmp);
          Wind &w = touch(ac, t).wind;
          w.valid = flags & 1;
          w.east = e; w.north = n;
          w.has_up = (flags & 2) && has_u; w.up = u;
          w.has_temp = (flags & 8) && has_tmp; w.temp = tmp;
        }
        break;
      case 'I':
        if (name == "INS") {
          if (!ins_f.split) { break; }
          // x y z (north, east, down; 8 fractional bits), xd yd zd (19 bits), xdd ydd zdd (10 bits)
          const Picked f = pick(ins_f, q, eol);
          int32_t v[9];
          bool ok = true;
          for (int i = 0; i < 9 && ok; i++) { ok = to_num(f[i], v[i]); }
          if (!ok) { break; }
          InsState &n = touch(ac, t).ins;
          n.valid = true;
          for (int i = 0; i < 3; i++) {
            n.pos[i] = v[i] / 256.0;
            n.vel[i] = v[3 + i] / 524288.0;
            n.acc[i] = v[6 + i] / 1024.0;
          }
          {
            AircraftState &s = touch(ac, t);
            if (s.in_flight && !s.flights.empty()) {
              Flight &f = s.flights.back();
              f.stats.add_ins(n);
              f.add_ins_step(n);
            }
          }
          break;
        }
        if (name == "INFO_MSG") {
          std::string text(q, eol);
          text = trim(text);
          if (text.size() >= 2 && text.front() == '"' && text.back() == '"') {
            text = text.substr(1, text.size() - 2);
          }
          touch(ac, t).infos.push_back({t, std::move(text)});
        }
        break;
      case 'A':
        if (name == "ACTUATORS") {
          AircraftState &s = touch(ac, t);
          s.actuators_raw = next_token(q, eol);
        } else if (name == "AHRS_REF_QUAT") {
          if (!quat_f.split) { break; }
          // body quaternion: int32 with 15 fractional bits
          const Picked f = pick(quat_f, q, eol);
          int32_t b[4];
          if (!to_num(f[Q_I], b[0]) || !to_num(f[Q_X], b[1]) || !to_num(f[Q_Y], b[2]) || !to_num(f[Q_Z], b[3])) {
            break;
          }
          AircraftState &s = touch(ac, t);
          s.has_quat = true;
          s.qi = b[0] / 32768.0; s.qx = b[1] / 32768.0; s.qy = b[2] / 32768.0; s.qz = b[3] / 32768.0;
        } else if (name == "ATTITUDE") {
          if (!att_f.split) { break; }
          const Picked f = pick(att_f, q, eol);
          float phi, theta, psi;
          if (!to_num(f[AT_PHI], phi) || !to_num(f[AT_THETA], theta) || !to_num(f[AT_PSI], psi)) { break; }
          AircraftState &s = touch(ac, t);
          s.has_euler = true;
          s.roll = phi * 180.0 / M_PI;
          s.pitch = theta * 180.0 / M_PI;
          s.yaw = psi * 180.0 / M_PI;
        } else if (name == "AIRSPEED") {
          if (!as_f.split) { break; }
          const Picked f = pick(as_f, q, eol);
          float eas;
          if (!to_num(f[AS_EAS], eas)) { break; }
          AircraftState &s = touch(ac, t);
          s.has_as = true;
          s.airspeed = eas;
        } else if (name == "AIR_DATA") {
          if (!air_f.split) { break; }
          // pressure [Pa], amsl_baro [m]
          const Picked f = pick(air_f, q, eol);
          float press, baro_alt, diff_p = 0;
          if (!to_num(f[AD_PRESS], press)) { break; }
          to_num(f[AD_DIFF_P], diff_p);
          if (!to_num(f[AD_AMSL], baro_alt)) { break; }
          float airspeed = 0, tas = 0;
          bool has_as = to_num(f[AD_AIRSPEED], airspeed);
          has_as = has_as && to_num(f[AD_TAS], tas);
          AircraftState &s = touch(ac, t);
          if (has_as) { s.has_as = true; s.airspeed = airspeed; s.tas = tas; s.diff_p = diff_p; }
          s.has_baro = true;
          s.pressure = press;
          s.baro_alt = baro_alt;
          if (s.in_flight && !s.flights.empty()) { s.flights.back().stats.baro.add(baro_alt); }
        } else if (name == "AUTOPILOT_VERSION") {
          touch(ac, t).versions.insert(trim(std::string(q, eol)));
        }
        break;
      default:
        break;
    }
  }
  if (hasher.joinable()) { hasher.join(); }
  munmap(map, size);
  close(fd);

  // Close flights still ongoing at end of log
  for (auto &kv : acs) {
    AircraftState &s = kv.second;
    if (s.in_flight && !s.flights.empty() && !s.flights.back().complete) {
      Flight &f = s.flights.back();
      f.landing_time = s.last_time;
      f.has_land_volt = s.has_volt;
      f.land_volt = s.vsupply;
      f.land_wind = s.wind;
      f.land_ins = s.ins;
      f.has_land_pos = s.has_gps;
      f.land_lat = s.lat; f.land_lon = s.lon; f.land_alt = s.alt;
    }
  }

  // Report (JSON)
  const ClockDecision clock = choose_clock(hdr.start_epoch, gps_clocks);
  auto utc = [&](double t) { return utc_iso(clock.start < 0 ? -1 : clock.start + t); };
  const double t0 = first_time < 0 ? 0 : first_time;

  // Aircraft without a name entry fall back to the single unnamed airframe of the log
  std::vector<int> report_ids;
  for (const auto &kv : acs) {
    if (kv.second.seen_status) { report_ids.push_back(kv.first); }
  }
  auto name_of = [&](int id) {
    auto it = hdr.names.find(id);
    if (it != hdr.names.end()) { return it->second; }
    if (report_ids.size() == 1 && hdr.names.size() == 1) { return hdr.names.begin()->second; }
    if (!hdr.default_name.empty() && report_ids.size() == 1) { return hdr.default_name; }
    return std::string("unknown");
  };
  auto conf_of = [&](int id) -> const AcConf * {
    auto it = hdr.confs.find(id);
    if (it == hdr.confs.end()) { it = hdr.confs.find(-1); }
    return it == hdr.confs.end() ? nullptr : &it->second;
  };
  for (int id : report_ids) {
    AircraftState &s = acs[id];
    const AcConf *cf = conf_of(id);
    if (cf && (xml_contains_ci(cf->airframe_xml, "optitrack") || xml_contains_ci(cf->flight_plan_xml, "optitrack"))) {
      s.has_optitrack = true;
    }
    if (s.has_optitrack) {
      for (auto &fl : s.flights) { fl.has_optitrack = true; }
    }
  };
  auto mode_name = [&](const char *field, int v) {
    if (v < 0) { return std::string("null"); }
    auto it = hdr.mode_names.find(field);
    if (it != hdr.mode_names.end() && static_cast<size_t>(v) < it->second.size()) { return json_escape(it->second[v]); }
    return json_escape(std::to_string(v));
  };
  auto block_name = [&](int id, int b) {
    const AcConf *c = conf_of(id);
    if (!c || b < 0 || static_cast<size_t>(b) >= c->blocks.size()) { return std::string("null"); }
    return json_escape(c->blocks[b]);
  };
  auto stage_name = [&](int id, int b, int st) {
    const AcConf *c = conf_of(id);
    if (!c || b < 0 || st < 0 || static_cast<size_t>(b) >= c->stages.size() ||
        static_cast<size_t>(st) >= c->stages[b].size()) { return std::string("null"); }
    return json_escape(c->stages[b][st]);
  };
  auto loc = [&](bool has, double lat, double lon, double alt) {
    if (!has) { return std::string("null"); }
    char buf[96];
    snprintf(buf, sizeof(buf), "{\"lat\": %.7f, \"lon\": %.7f, \"alt_m\": %.1f}", lat, lon, alt);
    return std::string(buf);
  };

  // Onboard SD logs are named *_SD.data; ground station (GCS) logs have <aircraft> entries and link messages
  const char *log_type = "unknown", *type_basis = "none";
  {
    const std::string &f = data_file;
    if (f.size() >= 8 && f.compare(f.size() - 8, 8, "_SD.data") == 0) {
      log_type = "onboard_sd"; type_basis = "file name";
    } else if (hdr.has_aircraft_tag || hdr.has_logged_with) {
      log_type = "gcs"; type_basis = ".log header";
    } else if (ground_msgs > 0) {
      log_type = "gcs"; type_basis = "ground-only messages";
    } else if (hdr.start_epoch >= 0 || !hdr.default_name.empty()) {
      log_type = "onboard_sd"; type_basis = ".log header";
    }
  }

  double log_flight_dist = 0;
  for (int id : report_ids) {
    for (const auto &f : acs[id].flights) { log_flight_dist += f.distance_m; }
  }

  std::ostringstream o;
  o << "{\n  \"parser\": {\"name\": \"" << PARSER_NAME << "\", \"version\": \"" << PARSER_VERSION
    << "\", \"path_rate_hz\": " << num(path_rate) << "},\n  \"log\": {\n    \"file\": " << json_escape(data_file)
    << ",\n    \"type\": \"" << log_type << "\",\n    \"type_basis\": \"" << type_basis
    << "\",\n    \"sha256\": \"" << sha256 << "\",\n    \"size_bytes\": " << size << ",\n    \"start_utc\": " << utc(0)
    << ",\n    \"timing\": {\"source\": " << json_escape(clock.source)
    << ", \"original_start_utc\": " << utc_iso(hdr.start_epoch)
    << ", \"effective_start_utc\": " << utc(0)
    << ", \"correction_s\": " << (hdr.start_epoch < 0 || clock.start < 0 ? "null" : num(clock.start - hdr.start_epoch))
    << ", \"warning\": " << json_escape(clock.warning) << "}"
    << ",\n    \"first_data_utc\": " << utc(t0) << ",\n    \"end_utc\": " << utc(last_time)
    << ",\n    \"duration_s\": " << num(last_time - t0)
    << ",\n    \"total_flight_distance_m\": " << num(log_flight_dist) << "\n  },\n  \"aircraft\": [";

  // {"time_s", "utc", "location", "voltage_v"} describing a takeoff or landing
  auto event = [&](double t, bool has_pos, double lat, double lon, double alt, bool has_v, double v, const Wind &w, const InsState &ins) {
    return "{\"time_s\": " + num(t) + ", \"utc\": " + utc(t) + ", \"location\": " + loc(has_pos, lat, lon, alt) +
           ", \"voltage_v\": " + (has_v ? num(v) : "null") + ", \"wind\": " + wind_json(w) + ", \"ins\": " + ins_json(ins) + "}";
  };

  auto rng = [&](const Range &r) { return r.any ? num(r.span()) : std::string("null"); };
  auto assessment_json = [&](const Flight &f) {
    const Assessment a = assess(f);
    const FlightStats &st = f.stats;
    std::string m = "{\"verdict\": \"" + std::string(a.verdict) + "\", \"score\": " + num(a.score) +
                    ", \"reasons\": [" + a.reasons + "], \"metrics\": {\"ins_up_range_m\": " + rng(st.ins_up) +
                    ", \"baro_range_m\": " + rng(st.baro) + ", \"gps_alt_range_m\": " + rng(st.gps_alt) +
                    ", \"ins_horizontal_m\": " + (st.ins_n.any ? num(st.ins_horizontal_m()) : "null") +
                    ", \"gps_horizontal_m\": " + (st.gps_lat.any ? num(st.gps_horizontal_m()) : "null") +
                    ", \"max_speed_ms\": " + num(st.max_speed) + ", \"max_vertical_speed_ms\": " + num(st.max_vspeed) +
                    ", \"motors_on_fraction\": " + (st.status_msgs ? num(static_cast<double>(st.motors_on_msgs) / st.status_msgs) : "null") +
                    ", \"max_tilt_deg\": " + num(st.max_tilt_deg) + ", \"acc_rms_ms2\": " + num(st.acc_rms()) +
                    "}, \"optitrack\": " + std::string(f.has_optitrack ? "true" : "false") + "}";
    return m;
  };

  bool first_ac = true;
  for (int id : report_ids) {
    const AircraftState &s = acs[id];
    const AcConf *cf = conf_of(id);

    // Split INFO_MSGs of this aircraft
    std::string pic, pac, gcs, goal;
    std::vector<const InfoMsg *> pfc, other;
    for (const auto &m : s.infos) {
      if (m.text.rfind("[PFC]", 0) == 0) {
        std::string body = trim(m.text.substr(5));
        auto starts = [&](const char *k, std::string &dst) {
          size_t n = strlen(k);
          if (body.compare(0, n, k) == 0) { dst = trim(body.substr(n)); return true; }
          return false;
        };
        if (starts("pic:", pic) || starts("pac:", pac) || starts("gcs:", gcs) || starts("goal:", goal)) { continue; }
        pfc.push_back(&m);
      } else {
        other.push_back(&m);
      }
    }

    double total = 0, total_dist = 0;
    for (const auto &f : s.flights) { total += f.duration(); total_dist += f.distance_m; }

    // Identification, crew and summary
    o << (first_ac ? "" : ",") << "\n    {\n      \"ac_id\": " << id << ",\n      \"name\": "
      << json_escape(name_of(id)) << ",\n      \"autopilot_versions\": [";
    first_ac = false;
    bool fv = true;
    for (const auto &v : s.versions) { o << (fv ? "" : ", ") << json_escape(v); fv = false; }
    o << "],\n      \"data_span\": {\"first_utc\": " << utc(s.first_time) << ", \"last_utc\": " << utc(s.last_time)
      << "},\n      \"mission\": {\"pic\": " << json_escape(pic) << ", \"pac\": " << json_escape(pac)
      << ", \"gcs\": " << json_escape(gcs) << ", \"goal\": " << json_escape(goal)
      << "},\n      \"summary\": {\"num_flights\": " << s.flights.size() << ", \"total_flight_time_s\": " << num(total)
      << ", \"total_flight_distance_m\": " << num(total_dist)
      << "},\n      \"flights\": [";

    int i = 1;
    for (const auto &f : s.flights) {
      o << (i == 1 ? "" : ",") << "\n        {\n          \"flight\": " << i++ << ",\n          \"duration_s\": "
        << num(f.duration()) << ",\n          \"distance_m\": " << num(f.distance_m)
        << ",\n          \"from_takeoff_m\": " << num(f.from_takeoff_m)
        << ",\n          \"max_from_takeoff_m\": " << num(f.max_from_takeoff_m)
        << ",\n          \"landed\": " << (f.complete ? "true" : "false")
        << ",\n          \"assessment\": " << assessment_json(f)
        << ",\n          \"takeoff\": " << event(f.takeoff_time, f.has_pos, f.lat, f.lon, f.alt, f.has_takeoff_volt, f.takeoff_volt, f.takeoff_wind, f.takeoff_ins)
        << ",\n          \"landing\": "
        << event(f.landing_time, f.has_land_pos, f.land_lat, f.land_lon, f.land_alt, f.has_land_volt, f.land_volt, f.land_wind, f.land_ins)
        << ",\n          \"path\": [";
      bool fp = true;
      for (const auto &pt : f.path) {
        char buf[192];
        // time, position (GPS + barometric)
        o << (fp ? "" : ",") << "\n            {\"time_s\": " << num(pt.t) << ", \"utc\": " << utc(pt.t)
          << ", \"distance_m\": " << num(pt.distance_m)
          << ", \"from_takeoff_m\": " << num(pt.from_takeoff_m);
        snprintf(buf, sizeof(buf), ", \"position\": {\"lat\": %.7f, \"lon\": %.7f, \"gps_alt_m\": %.1f", pt.lat, pt.lon,
                 pt.alt);
        o << buf;
        if (pt.has_baro) {
          snprintf(buf, sizeof(buf), ", \"baro_alt_m\": %.2f, \"pressure_pa\": %.0f}", pt.baro_alt_m, pt.pressure_pa);
          o << buf;
        } else {
          o << ", \"baro_alt_m\": null, \"pressure_pa\": null}";
        }
        // attitude
        o << ",\n             \"ins\": " << ins_json(pt.ins);
        if (pt.has_gs) {
          snprintf(buf, sizeof(buf), ", \"groundspeed_ms\": %.2f", pt.gndspeed);
          o << buf;
        }
        o << ",\n             \"attitude\": {\"euler_deg\": ";
        if (pt.has_euler) {
          snprintf(buf, sizeof(buf), "{\"roll\": %.2f, \"pitch\": %.2f, \"yaw\": %.2f}", pt.roll_deg, pt.pitch_deg,
                   pt.yaw_deg);
          o << buf;
        } else {
          o << "null";
        }
        o << ", \"quaternion\": ";
        if (pt.has_quat) {
          snprintf(buf, sizeof(buf), "{\"qi\": %.5f, \"qx\": %.5f, \"qy\": %.5f, \"qz\": %.5f}", pt.qi, pt.qx, pt.qy,
                   pt.qz);
          o << buf;
        } else {
          o << "null";
        }
        o << "}";
        // air data and battery
        o << ",\n             \"airspeed\": ";
        if (pt.has_as) {
          snprintf(buf, sizeof(buf), "{\"eas_ms\": %.2f, \"tas_ms\": %.2f, \"diff_pressure_pa\": %.2f}", pt.airspeed, pt.tas, pt.diff_p);
          o << buf;
        } else {
          o << "null";
        }
        if (pt.has_volt) {
          snprintf(buf, sizeof(buf), ", \"battery_voltage_v\": %.2f", pt.vsupply);
          o << buf;
        } else {
          o << ", \"battery_voltage_v\": null";
        }
        // autopilot state
        o << ",\n             \"wind\": " << wind_json(pt.wind);
        o << ",\n             \"flight_mode\": {\"ap_mode\": " << mode_name("ap_mode", pt.ap_mode)
          << ", \"h_mode\": " << mode_name("ap_h_mode", pt.h_mode)
          << ", \"v_mode\": " << mode_name("ap_v_mode", pt.v_mode) << "}"
          << ",\n             \"nav\": {\"block_id\": " << pt.block << ", \"block\": " << block_name(id, pt.block)
          << ", \"stage_id\": " << pt.stage << ", \"stage\": " << stage_name(id, pt.block, pt.stage) << "}";
        o << ",\n             \"actuators\": ";
        if (pt.actuators.empty()) {
          o << "null";
        } else {
          o << "{";
          for (size_t k = 0; k < pt.actuators.size(); k++) {
            std::string an = cf && cf->servos.size() == pt.actuators.size() ? cf->servos[k] : "ACT_" + std::to_string(k);
            o << (k ? ", " : "") << json_escape(an) << ": " << pt.actuators[k];
          }
          o << "}";
        }
        o << "}";
        fp = false;
      }
      o << (f.path.empty() ? "" : "\n          ") << "]\n        }";
    }
    o << "\n      ]";

    // Messages
    auto emit = [&](const std::vector<const InfoMsg *> &v, size_t skip) {
      bool f1 = true;
      for (const auto *m : v) {
        o << (f1 ? "" : ",") << "\n        {\"time_s\": " << num(m->time) << ", \"utc\": " << utc(m->time)
          << ", \"text\": " << json_escape(trim(m->text.substr(skip))) << "}";
        f1 = false;
      }
      o << (v.empty() ? "" : "\n      ") << "]";
    };
    o << ",\n      \"preflight_checks\": [";
    emit(pfc, 5);
    o << ",\n      \"info_messages\": [";
    emit(other, 0);

    // Configuration: parsed summaries, then the raw XML as embedded in the log
    auto xml_or_null = [&](const std::string &x) { return x.empty() ? std::string("null") : json_escape(x); };
    o << ",\n      \"flight_plan\": "
      << (cf && !cf->flight_plan_xml.empty() ? flight_plan_summary_json(cf->flight_plan_xml) : "null")
      << ",\n      \"settings\": " << (cf && !cf->settings_xml.empty() ? settings_list_json(cf->settings_xml) : "null")
      << ",\n      \"settings_changes\": " << settings_changes_json(s, cf ? merge_settings_entries(cf->settings_xml) : std::vector<SettingInfo>(), utc)
      << ",\n      \"xml\": {\n        \"airframe\": " << (cf ? xml_or_null(cf->airframe_xml) : "null")
      << ",\n        \"flight_plan\": " << (cf ? xml_or_null(cf->flight_plan_xml) : "null")
      << ",\n        \"settings\": " << (cf ? xml_or_null(cf->settings_xml) : "null")
      << ",\n        \"radio\": " << (cf ? xml_or_null(cf->radio_xml) : "null")
      << ",\n        \"telemetry\": " << (cf ? xml_or_null(cf->telemetry_xml) : "null") << "\n      }\n    }";
  }
  o << "\n  ]\n}\n";
  std::cout << o.str();
  return 0;
}
