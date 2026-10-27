// Column export for the Web UI's MATLAB download (webui/matlab_export.py).
//
//   paparazzi_log_parsing --export-columns <out_dir> --schema <file> [--log <file.log>] [--size <bytes>]
//                         [--window <start_s> <end_s>]... <file.data | ->
//
// "-" reads the .data from stdin (the Web UI pipes it straight out of gzip storage; --size gives the expected
// length for progress). Decodes every row of a Paparazzi .data file with a schema written by the Web UI from the
// protocol in the companion .log, and writes plain binary column files plus manifest.tsv into out_dir. The MATLAB file itself
// is written by Python (h5py). This is a separate entry point: the normal parser output is not affected.
//
// Schema lines: "<MESSAGE>\t<kinds>", one kind character per field: u (unsigned), i (signed), f (float),
// a (numeric array), t (text). Times are unwrapped like the normal parser (onboard uint32 microsecond clock).
// Without --window the whole log is one window; with windows a row goes to every window that contains it.
//
// Files per (window, aircraft, message) stream <id>: s<id>.f64 rows of [time, scalar numeric fields...];
// s<id>_a<j>.len (uint32 per row) + s<id>_a<j>.f64 for array field j; s<id>_t<j>.txt one value per line for
// text field j. With --log, the manifest also lists flight plan block/stage and actuator names.
// Rows that do not decode: r<w>_<ac>.f64 (times) + r<w>_<ac>.txt (original lines); rows without
// a time or aircraft id: top.f64 + top.txt. Progress goes to stderr as "progress <fraction>".
// The clock-wrap correction only applies when the time jumps back by more than 2000 s (old onboard logs);
// other logs keep their timestamps unchanged.
#include <charconv>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <string>
#include <string_view>
#include <tuple>
#include <unordered_map>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

void write_config_names(const std::string &log_file, std::ostream &out);  // fastreader.cpp

namespace {

constexpr double WRAP_S = 4294.967296;  // 2^32 us, as SYS_TIME_WRAP_S in fastreader.cpp
constexpr size_t FLUSH_BYTES = 256 * 1024;

// Append-only file with a small buffer; reopened per flush so thousands of streams need no open descriptors.
struct Out {
  std::string path;
  std::string buf;
  bool failed = false;
  void write(const void *p, size_t n) {
    buf.append(static_cast<const char *>(p), n);
    if (buf.size() >= FLUSH_BYTES) { flush(); }
  }
  void flush() {
    if (buf.empty()) { return; }
    FILE *f = fopen(path.c_str(), "ab");
    if (!f || fwrite(buf.data(), 1, buf.size(), f) != buf.size()) { failed = true; }
    if (f) { fclose(f); }
    buf.clear();
  }
};

struct Stream {
  int id = 0, window = 0, ac = 0;
  std::string msg;
  uint64_t rows = 0;
  Out scalar;
  std::vector<std::unique_ptr<Out>> lens, arrays, texts;  // per field index (null when not that kind)
  std::vector<uint32_t> max_len;
};

struct Raw {
  uint64_t rows = 0;
  Out times, lines;
};

// Next field token: quoted text may contain spaces ("..." with \" escapes).
std::string_view token(const char *&p, const char *end) {
  while (p < end && *p == ' ') { ++p; }
  const char *s = p;
  if (p < end && *p == '"') {
    ++p;
    while (p < end && *p != '"') { p += (*p == '\\' && p + 1 < end) ? 2 : 1; }
    if (p < end) { ++p; }
  } else {
    while (p < end && *p != ' ') { ++p; }
  }
  return std::string_view(s, p - s);
}

bool number(std::string_view v, char kind, double &out) {
  if (kind == 'f') {
    auto r = std::from_chars(v.data(), v.data() + v.size(), out);
    return r.ec == std::errc() && r.ptr == v.data() + v.size();
  }
  long long n;
  auto r = std::from_chars(v.data(), v.data() + v.size(), n);
  if (r.ec != std::errc() || r.ptr != v.data() + v.size()) { return false; }
  if (kind == 'u' ? (n < 0 || n >= 4294967296LL) : (n < -2147483648LL || n > 2147483647LL)) { return false; }
  out = static_cast<double>(n);
  return true;
}

bool array(std::string_view v, std::vector<double> &out) {
  out.clear();
  if (v.size() >= 2 && v.front() == '"' && v.back() == '"') { v = v.substr(1, v.size() - 2); }
  const char *c = v.data(), *e = c + v.size();
  while (c < e) {
    double x;
    auto r = std::from_chars(c, e, x);
    if (r.ec != std::errc()) { return false; }
    out.push_back(x);
    c = r.ptr;
    if (c < e) {
      if (*c != ',') { return false; }
      ++c;
    }
  }
  return true;
}

std::string text(std::string_view v) {
  if (v.size() >= 2 && v.front() == '"' && v.back() == '"') { v = v.substr(1, v.size() - 2); }
  std::string out;
  out.reserve(v.size());
  for (size_t i = 0; i < v.size(); ++i) {
    if (v[i] == '\\' && i + 1 < v.size() && v[i + 1] == '"') { continue; }
    out += v[i];
  }
  return out;
}

}  // namespace

int export_columns(int argc, char *argv[])
{
  std::string out_dir, schema_file, data_file, log_file;
  double expected_size = 0;
  std::vector<std::pair<double, double>> windows;
  for (int i = 1; i < argc; i++) {
    std::string a = argv[i];
    if (a == "--export-columns" && i + 1 < argc) { out_dir = argv[++i]; }
    else if (a == "--schema" && i + 1 < argc) { schema_file = argv[++i]; }
    else if (a == "--size" && i + 1 < argc) { expected_size = strtod(argv[++i], nullptr); }
    else if (a == "--log" && i + 1 < argc) { log_file = argv[++i]; }
    else if (a == "--window" && i + 2 < argc) {
      windows.emplace_back(strtod(argv[i + 1], nullptr), strtod(argv[i + 2], nullptr));
      i += 2;
    } else { data_file = a; }
  }
  if (out_dir.empty() || schema_file.empty() || data_file.empty()) {
    std::cerr << "Usage: " << argv[0] << " --export-columns <out_dir> --schema <file> [--window <start> <end>]... <file.data>\n";
    return 1;
  }
  const bool whole = windows.empty();
  if (whole) { windows.emplace_back(-INFINITY, INFINITY); }

  std::vector<std::string> msg_names, msg_kinds;
  std::unordered_map<std::string, int> schema;  // message name -> index
  {
    std::ifstream f(schema_file);
    std::string line;
    while (std::getline(f, line)) {
      size_t tab = line.find('\t');
      if (tab != std::string::npos) {
        schema[line.substr(0, tab)] = static_cast<int>(msg_names.size());
        msg_names.push_back(line.substr(0, tab));
        msg_kinds.push_back(line.substr(tab + 1));
      }
    }
  }

  const int fd = data_file == "-" ? STDIN_FILENO : open(data_file.c_str(), O_RDONLY);
  struct stat st;
  if (fd < 0 || fstat(fd, &st) != 0) {
    std::cerr << " - Cannot open DATA file\n";
    return 1;
  }
  if (S_ISREG(st.st_mode) && expected_size <= 0) { expected_size = static_cast<double>(st.st_size); }

  std::vector<std::unique_ptr<Stream>> owned;
  std::unordered_map<uint64_t, Stream *> streams;  // (window, aircraft, message) -> stream
  std::map<std::pair<int, int>, std::unique_ptr<Raw>> raws;
  Raw top;
  top.times.path = out_dir + "/top.f64";
  top.lines.path = out_dir + "/top.txt";
  std::map<std::string, uint64_t> unknown, rejected;
  std::map<int, std::string> versions;  // first AUTOPILOT_VERSION payload per aircraft
  int next_id = 0;
  double prev_raw = -1, offset = 0;
  uint64_t consumed = 0, next_progress = 64u << 20;
  std::string key;

  std::vector<std::string_view> tok;
  std::vector<double> scalars;
  std::vector<std::vector<double>> arrs;
  auto raw_for = [&](int w, int ac) -> Raw & {
    auto &r = raws[{w, ac}];
    if (!r) {
      r = std::make_unique<Raw>();
      const std::string stem = out_dir + "/r" + std::to_string(w) + "_" + std::to_string(ac);
      r->times.path = stem + ".f64";
      r->lines.path = stem + ".txt";
    }
    return *r;
  };
  auto keep_raw = [](Raw &r, double t, const char *line, const char *eol) {
    r.rows++;
    r.times.write(&t, sizeof t);
    r.lines.write(line, eol - line);
    r.lines.write("\n", 1);
  };

  auto row = [&](const char *line, const char *line_end) {
    const char *q = line;
    while (q < line_end && *q == ' ') { ++q; }
    if (q == line_end) { return; }
    double t;
    auto r = std::from_chars(q, line_end, t);
    if (r.ec != std::errc() || !std::isfinite(t)) {
      keep_raw(top, NAN, line, line_end);
      return;
    }
    q = r.ptr;
    if (prev_raw >= 0 && t + 2000.0 < prev_raw) { offset += WRAP_S; }
    prev_raw = t;
    t += offset;
    std::string_view ac_tok = token(q, line_end);
    int ac;
    auto ar = std::from_chars(ac_tok.data(), ac_tok.data() + ac_tok.size(), ac);
    if (ar.ec != std::errc() || ar.ptr != ac_tok.data() + ac_tok.size()) {
      keep_raw(top, t, line, line_end);
      return;
    }
    std::string_view name = token(q, line_end);

    // Which windows take this row (usually none or one)
    bool any = false;
    for (const auto &w : windows) {
      if (t >= w.first && t <= w.second) { any = true; break; }
    }
    if (name == "AUTOPILOT_VERSION" && !versions.count(ac)) {
      const char *c = q;
      while (c < line_end && *c == ' ') { ++c; }
      versions[ac] = std::string(c, line_end);
    }
    if (!any) { return; }

    key.assign(name);
    auto def = schema.find(key);
    bool ok = def != schema.end();
    if (!ok) { unknown[key]++; }
    const int msg = ok ? def->second : -1;
    const std::string *kinds = ok ? &msg_kinds[msg] : nullptr;
    tok.clear();
    if (ok) {
      const char *c = q;
      while (true) {
        std::string_view v = token(c, line_end);
        if (v.empty()) { break; }
        tok.push_back(v);
      }
      ok = tok.size() == kinds->size();
      scalars.clear();
      if (arrs.size() < tok.size()) { arrs.resize(tok.size()); }
      for (size_t j = 0; ok && j < tok.size(); ++j) {
        const char k = (*kinds)[j];
        if (k == 'u' || k == 'i' || k == 'f') {
          double x;
          ok = number(tok[j], k, x);
          scalars.push_back(x);
        } else if (k == 'a') {
          ok = array(tok[j], arrs[j]);
        }
      }
      if (!ok) { rejected[key]++; }
    }

    for (size_t w = 0; w < windows.size(); ++w) {
      if (t < windows[w].first || t > windows[w].second) { continue; }
      if (!ok) {
        keep_raw(raw_for(static_cast<int>(w), ac), t, line, line_end);
        continue;
      }
      const uint64_t sk = (static_cast<uint64_t>(w) << 48) | (static_cast<uint64_t>(static_cast<uint32_t>(ac) & 0xffff) << 32)
                          | static_cast<uint32_t>(msg);
      Stream *&s = streams[sk];
      if (!s) {
        owned.push_back(std::make_unique<Stream>());
        s = owned.back().get();
        s->id = next_id++;
        s->window = static_cast<int>(w);
        s->ac = ac;
        s->msg = msg_names[msg];
        const std::string stem = out_dir + "/s" + std::to_string(s->id);
        s->scalar.path = stem + ".f64";
        s->lens.resize(kinds->size());
        s->arrays.resize(kinds->size());
        s->texts.resize(kinds->size());
        s->max_len.assign(kinds->size(), 0);
        for (size_t j = 0; j < kinds->size(); ++j) {
          if ((*kinds)[j] == 'a') {
            s->lens[j] = std::make_unique<Out>();
            s->lens[j]->path = stem + "_a" + std::to_string(j) + ".len";
            s->arrays[j] = std::make_unique<Out>();
            s->arrays[j]->path = stem + "_a" + std::to_string(j) + ".f64";
          } else if ((*kinds)[j] == 't') {
            s->texts[j] = std::make_unique<Out>();
            s->texts[j]->path = stem + "_t" + std::to_string(j) + ".txt";
          }
        }
      }
      s->rows++;
      s->scalar.write(&t, sizeof t);
      if (!scalars.empty()) { s->scalar.write(scalars.data(), scalars.size() * sizeof(double)); }
      for (size_t j = 0; j < kinds->size(); ++j) {
        const char k = (*kinds)[j];
        if (k == 'a') {
          const std::vector<double> &arr = arrs[j];
          const uint32_t n = static_cast<uint32_t>(arr.size());
          if (n > s->max_len[j]) { s->max_len[j] = n; }
          s->lens[j]->write(&n, sizeof n);
          if (n) { s->arrays[j]->write(arr.data(), n * sizeof(double)); }
        } else if (k == 't') {
          const std::string v = text(tok[j]);
          s->texts[j]->write(v.data(), v.size());
          s->texts[j]->write("\n", 1);
        }
      }
    }
  };

  // Read in large blocks (from a file or a pipe) and hand complete lines to row()
  std::vector<char> buf(16u << 20);
  size_t have = 0;
  while (true) {
    const ssize_t n = read(fd, buf.data() + have, buf.size() - have);
    if (n < 0) {
      std::cerr << " - Cannot read DATA file\n";
      return 1;
    }
    have += static_cast<size_t>(n);
    consumed += static_cast<uint64_t>(n);
    if (consumed >= next_progress && expected_size > 0) {
      std::cerr << "progress " << std::min(1.0, static_cast<double>(consumed) / expected_size) << "\n";
      next_progress += 64u << 20;
    }
    const char *p = buf.data(), *end = buf.data() + have;
    while (true) {
      const char *eol = static_cast<const char *>(memchr(p, '\n', end - p));
      if (!eol) {
        if (n == 0 && p < end) { row(p, (end[-1] == '\r') ? end - 1 : end); p = end; }
        break;
      }
      row(p, (eol > p && eol[-1] == '\r') ? eol - 1 : eol);
      p = eol + 1;
    }
    have = static_cast<size_t>(end - p);
    if (have) { memmove(buf.data(), p, have); }
    if (n == 0) { break; }
    if (have == buf.size()) { buf.resize(buf.size() * 2); }  // a single line longer than the buffer
  }

  bool failed = false;
  auto done = [&](Out &o) { o.flush(); failed |= o.failed; };
  std::ofstream man(out_dir + "/manifest.tsv");
  for (auto &s : owned) {
    done(s->scalar);
    man << "stream\t" << s->id << "\t" << s->window << "\t" << s->ac << "\t" << s->msg << "\t" << s->rows << "\n";
    for (size_t j = 0; j < s->lens.size(); ++j) {
      if (s->lens[j]) {
        done(*s->lens[j]);
        done(*s->arrays[j]);
        man << "array\t" << s->id << "\t" << j << "\t" << s->max_len[j] << "\n";
      }
      if (s->texts[j]) { done(*s->texts[j]); }
    }
  }
  for (auto &[wa, r] : raws) {
    done(r->times);
    done(r->lines);
    man << "raw\t" << wa.first << "\t" << wa.second << "\t" << r->rows << "\n";
  }
  done(top.times);
  done(top.lines);
  man << "top\t" << top.rows << "\n";
  for (auto &[n, c] : unknown) { man << "unknown\t" << n << "\t" << c << "\n"; }
  for (auto &[n, c] : rejected) { man << "rejected\t" << n << "\t" << c << "\n"; }
  for (auto &[ac, v] : versions) { man << "version\t" << ac << "\t" << v << "\n"; }
  if (!log_file.empty()) { write_config_names(log_file, man); }
  man << "done\n";
  man.close();
  if (failed || !man) {
    std::cerr << " - Could not write the export files (disk full?)\n";
    return 1;
  }
  return 0;
}
