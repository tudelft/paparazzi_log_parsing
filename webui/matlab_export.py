"""On-demand MATLAB v7.3 (HDF5) export of a Paparazzi .data/.log pair, written without MATLAB.

The protocol embedded in the companion .log is read here; the C++ fastreader (``--export-columns``) then decodes
every .data row into plain binary column files, and this module writes those into a MATLAB v7.3 file with h5py
in large blocks. Three scopes exist:

* ``log``: the whole log, every aircraft and message, with the original log timestamps
  (``s.aircrafts(i).data.MSG.field``, close to ``matlab/tools/parselog.m``).
* ``flights``: one element of ``s.flights`` per detected flight of the log (ground records excluded),
  each holding every aircraft's rows from 10 s before takeoff until 10 s after landing.
* ``flight``: the same for a single flight.

Inside a flight element timestamps count from that flight's takeoff (``time_offset_s`` converts back to log
time). Rows that cannot be decoded are kept as text in ``raw`` and counted in ``s.warnings``; nothing is dropped
silently. See TECHNICAL.md ("MATLAB export") for the layout.
"""
import concurrent.futures
import datetime
import gzip
import json
import math
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

import h5py
import numpy as np

import storage

EXPORTER_VERSION = "1"
MARGIN_S = 10.0
MESSAGE_CLASSES = ("telemetry", "ground", "datalink", "alert")  # lookup order, as in parselog.m
TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\S+')
KEYWORDS = {"break", "case", "catch", "classdef", "continue", "else", "elseif", "end", "for", "function",
            "global", "if", "otherwise", "parfor", "persistent", "return", "spmd", "switch", "try", "while"}


class ExportError(Exception):
    """A log that cannot be exported (no protocol, unreadable file); the message is shown to the user."""


def matlab_name(name, taken=()):
    """A valid, unique MATLAB identifier (like matlab.lang.makeValidName)."""
    out = re.sub(r"\W", "_", str(name)) or "x"
    if not out[0].isalpha():
        out = "x" + out
    if out in KEYWORDS:
        out += "_"
    out = out[:63]
    base, n = out, 1
    while out in taken:
        n += 1
        out = f"{base[:60]}_{n}"
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------------------------------------------------
def _attr(node, name):
    return node.get(name) or node.get(name.upper()) or ""


def field_kind(type_):
    """'uint' | 'int' | 'float' | 'text' | 'array' for a messages.xml field type (see messages.m)."""
    t = type_.lower()
    if t in ("char[]", "string", "char"):
        return "text"
    if re.fullmatch(r"[a-z0-9]+\[[0-9]*\]", t):
        return "array"
    if t.startswith("uint"):
        return "uint"
    if t.startswith("int"):
        return "int"
    if t in ("float", "double"):
        return "float"
    return "text"


def parse_protocol(log_text):
    """Message definitions and aircraft names embedded in a Paparazzi .log; ExportError when absent."""
    start, end = log_text.find("<protocol"), log_text.find("</protocol>")
    if start < 0 or end < 0:
        raise ExportError("The companion .log file has no embedded message protocol, so the rows cannot be "
                          "decoded. Upload the .log written together with this .data file.")
    try:
        root = ET.fromstring(log_text[start:end + len("</protocol>")])
    except ET.ParseError as error:
        raise ExportError(f"The message protocol in the .log file is not valid XML ({error}).") from None
    messages = {}
    classes = {_attr(c, "name"): c for c in root.iter("msg_class")}
    for class_name in [c for c in MESSAGE_CLASSES if c in classes] + [c for c in classes if c not in MESSAGE_CLASSES]:
        for message in classes[class_name].iter("message"):
            name = _attr(message, "name")
            if not name or name in messages:
                continue
            fields, taken = [], {"timestamp"}
            for field in message.iter("field"):
                fname = _attr(field, "name")
                coef = _attr(field, "alt_unit_coef")
                key = matlab_name(fname, taken)
                taken.add(key)
                taken.add(key + "_alt")
                fields.append(dict(name=fname, key=key, type=_attr(field, "type"), kind=field_kind(_attr(field, "type")),
                                   unit=_attr(field, "unit"), alt_unit=_attr(field, "alt_unit"),
                                   alt_unit_coef=float(coef) if coef else 1.0))
            messages[name] = dict(name=name, key=matlab_name(name), msg_class=class_name, id=_attr(message, "id"),
                                  fields=fields)
    names = {}
    for match in re.finditer(r"<aircraft\b[^>]*>", log_text):
        tag = match.group(0)
        ac_id, name = re.search(r'ac_id="([^"]*)"', tag), re.search(r'\bname="([^"]*)"', tag)
        if ac_id and ac_id.group(1).isdigit() and name:
            names[int(ac_id.group(1))] = name.group(1)
    return messages, names


# ---------------------------------------------------------------------------------------------------------------------
# MATLAB v7.3 writer helpers
# ---------------------------------------------------------------------------------------------------------------------
def _class(obj, cls):
    obj.attrs.create("MATLAB_class", np.bytes_(cls))


def _fields_attr(group, names):
    value = np.empty(len(names), dtype=h5py.vlen_dtype(np.dtype("S1")))
    for i, n in enumerate(names):  # filled one by one: equal-length names must not become a 2-D array
        value[i] = np.array(list(n), dtype="S1")
    group.attrs.create("MATLAB_fields", value)


MATLAB_CLASS = {np.dtype("float64"): "double", np.dtype("uint32"): "uint32", np.dtype("int32"): "int32",
                np.dtype("uint8"): "uint8"}


class Writer:
    """Minimal MATLAB v7.3 writer: scalar structs, struct arrays, numeric arrays, char and cellstr."""

    def __init__(self, path):
        self.path = path
        self.file = h5py.File(path, "w", userblock_size=512, libver="earliest")
        self.refs = self.file.create_group("#refs#")
        self.counter = 0

    def ref_name(self):
        self.counter += 1
        return f"r{self.counter}"

    def struct(self, parent, name):
        group = parent.create_group(name)
        _class(group, "struct")
        return group

    def number(self, parent, name, value, dtype="float64"):
        value = np.nan if value is None else value
        ds = parent.create_dataset(name, data=np.array([[value]], dtype=dtype))
        _class(ds, MATLAB_CLASS[np.dtype(dtype)])
        return ds

    def vector(self, parent, name, values, dtype="float64"):
        """MATLAB N×1 column (HDF5 stores it transposed as 1×N)."""
        values = np.asarray(values, dtype=dtype)
        if not values.size:
            return self.empty(parent, name, MATLAB_CLASS[np.dtype(dtype)])
        ds = parent.create_dataset(name, data=values.reshape(1, -1))
        _class(ds, MATLAB_CLASS[np.dtype(dtype)])
        return ds

    def empty(self, parent, name, cls="double"):
        ds = parent.create_dataset(name, data=np.zeros(2, dtype=np.uint64))
        _class(ds, cls)
        ds.attrs.create("MATLAB_empty", np.uint8(1))
        return ds

    def char(self, parent, name, text):
        text = str(text or "")
        if not text:
            ds = self.empty(parent, name, "char")
        else:
            ds = parent.create_dataset(name, data=np.array([ord(c) if ord(c) < 0x10000 else 0xFFFD for c in text],
                                                            dtype=np.uint16).reshape(-1, 1))
            _class(ds, "char")
        ds.attrs.create("MATLAB_int_decode", np.int32(2))
        return ds

    def cellstr(self, parent, name, texts):
        """MATLAB N×1 cell array of char rows."""
        texts = list(texts)
        if not texts:
            return self.empty(parent, name, "cell")
        refs = [self.char(self.refs, self.ref_name(), t).ref for t in texts]
        ds = parent.create_dataset(name, data=np.array(refs, dtype=h5py.ref_dtype).reshape(1, -1))
        _class(ds, "cell")
        return ds

    def struct_array(self, parent, name, elements):
        """MATLAB 1×K struct array from K groups that share field names (each field is a column of references)."""
        if not elements:
            return self.empty(parent, name, "struct")
        group = self.struct(parent, name)
        keys = list(elements[0].keys())
        _fields_attr(group, keys)
        for key in keys:
            group.create_dataset(key, data=np.array([e[key].ref for e in elements], dtype=h5py.ref_dtype).reshape(-1, 1))
        return group

    def element(self):
        """A struct stored under #refs#, to become one element of a struct array."""
        group = self.refs.create_group(self.ref_name())
        _class(group, "struct")
        return group

    def close(self):
        self.file.close()
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%a %b %d %H:%M:%S %Y")
        text = f"MATLAB 7.3 MAT-file, Platform: GLNXA64, Created on: {stamp} HDF5 schema 1.00 .".ljust(116)
        header = text.encode("ascii") + b"\x00" * 8 + b"\x00\x02" + b"IM"
        with open(self.path, "r+b") as f:
            f.write(header.ljust(512, b"\x00"))


def finish_struct(group):
    """Set MATLAB_fields on a scalar struct once all its members exist."""
    _fields_attr(group, list(group.keys()))


# ---------------------------------------------------------------------------------------------------------------------
# Decoding (the bulk is done by the C++ fastreader; Python only decodes single payloads such as the version)
# ---------------------------------------------------------------------------------------------------------------------
def decode(definition, tokens):
    """Field values of one row, or None when the row does not match the definition."""
    fields = definition["fields"]
    if len(tokens) != len(fields):
        return None
    out = []
    try:
        for f, tok in zip(fields, tokens):
            kind = f["kind"]
            if kind == "text":
                out.append(tok[1:-1].replace('\\"', '"') if len(tok) >= 2 and tok[0] == tok[-1] == '"' else tok)
            elif kind == "array":
                out.append([float(x) for x in tok.strip('"').split(",") if x != ""])
            elif kind == "float":
                out.append(float(tok))
            else:
                value = int(tok)
                if kind == "uint" and not 0 <= value < 2 ** 32 or kind == "int" and not -2 ** 31 <= value < 2 ** 31:
                    return None
                out.append(value)
    except ValueError:
        return None
    return out


KIND_CHAR = {"uint": "u", "int": "i", "float": "f", "array": "a", "text": "t"}
CHUNK_ROWS = 1 << 20
NUMERIC_DTYPE = {"uint": "uint32", "int": "int32", "float": "float64"}


def write_schema(path, messages):
    with open(path, "w", encoding="utf-8") as f:
        for m in messages.values():
            f.write(f"{m['name']}\t{''.join(KIND_CHAR[x['kind']] for x in m['fields'])}\n")


def read_manifest(path):
    streams, arrays, raws, out = {}, {}, {}, dict(top=0, unknown={}, rejected={}, versions={})
    complete = False
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            part = line.rstrip("\n").split("\t")
            if part[0] == "stream":
                streams[int(part[1])] = dict(id=int(part[1]), window=int(part[2]), ac=int(part[3]), msg=part[4],
                                             rows=int(part[5]))
            elif part[0] == "array":
                arrays[(int(part[1]), int(part[2]))] = int(part[3])
            elif part[0] == "raw":
                raws[(int(part[1]), int(part[2]))] = int(part[3])
            elif part[0] == "top":
                out["top"] = int(part[1])
            elif part[0] in ("unknown", "rejected"):
                out[part[0]][part[1]] = int(part[2])
            elif part[0] == "version":
                out["versions"][int(part[1])] = part[2] if len(part) > 2 else ""
            elif part[0] == "done":
                complete = True
    if not complete:
        raise ExportError("The column export did not finish; try again.")
    return streams, arrays, raws, out


class Columns:
    """Writes MATLAB N×1 / N×k columns (stored transposed, k×N) as deflate-compressed chunks.

    HDF5 compresses on one core, which dominated large exports. The chunks are therefore compressed with zlib
    on a thread pool (zlib releases the GIL) and stored with write_direct_chunk; the file is the same as with
    h5py's own gzip filter."""

    def __init__(self, writer):
        self.w = writer
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(8, os.cpu_count() or 1)))

    def close(self):
        self.pool.shutdown()

    def create(self, parent, name, dtype, width, rows):
        ds = parent.create_dataset(name, shape=(width, rows), dtype=dtype, compression="gzip", compression_opts=1,
                                   chunks=(width, min(rows, 65536)) if width <= 16 else (min(width, 64), min(rows, 4096)),
                                   fillvalue=np.nan if dtype == "float64" else 0)
        _class(ds, MATLAB_CLASS[np.dtype(dtype)])
        return ds

    def put(self, ds, block, col0):
        """Write ``block`` (all dataset rows × some columns, starting at a chunk boundary ``col0``)."""
        ch_r, ch_c = ds.chunks
        fill = np.nan if ds.dtype == np.float64 else 0

        def tile(r0, c0):
            part = np.ascontiguousarray(block[r0:r0 + ch_r, c0:c0 + ch_c], dtype=ds.dtype)
            if part.shape != (ch_r, ch_c):  # edge chunks are stored full size
                full = np.full((ch_r, ch_c), fill, dtype=ds.dtype)
                full[:part.shape[0], :part.shape[1]] = part
                part = full
            return zlib.compress(part.tobytes(), 1)

        jobs = [((r0, col0 + c0), self.pool.submit(tile, r0, c0))
                for r0 in range(0, block.shape[0], ch_r) for c0 in range(0, block.shape[1], ch_c)]
        for offset, job in jobs:
            ds.id.write_direct_chunk(offset, job.result())

    def scalars(self, group, stream_path, rows, fields, shift):
        """Timestamp plus the scalar numeric fields from rows of [time, field...] doubles."""
        scalar = [f for f in fields if f["kind"] in NUMERIC_DTYPE]
        data = np.memmap(stream_path, dtype=np.float64, mode="r", shape=(rows, 1 + len(scalar)))
        targets = [(self.create(group, "timestamp", "float64", 1, rows), 0, None, 1.0)]
        for j, f in enumerate(scalar, start=1):
            targets.append((self.create(group, f["key"], NUMERIC_DTYPE[f["kind"]], 1, rows), j, f["kind"], 1.0))
            if f["alt_unit_coef"] != 1:
                targets.append((self.create(group, f["key"] + "_alt", "float64", 1, rows), j, "float", f["alt_unit_coef"]))
        for i0 in range(0, rows, CHUNK_ROWS):
            block = np.asarray(data[i0:i0 + CHUNK_ROWS])
            for ds, j, kind, coef in targets:
                col = block[:, j] - shift if j == 0 else block[:, j] * coef if coef != 1 else block[:, j]
                self.put(ds, col.reshape(1, -1), i0)
        del data

    def array(self, group, f, len_path, val_path, rows, width):
        """A numeric array field as an N×width matrix, shorter rows padded with NaN."""
        if width == 0:
            self.w.empty(group, f["key"])
            if f["alt_unit_coef"] != 1:
                self.w.empty(group, f["key"] + "_alt")
            return
        lens = np.fromfile(len_path, dtype=np.uint32)
        values = np.memmap(val_path, dtype=np.float64, mode="r") if lens.sum() else np.zeros(0)
        starts = np.concatenate(([0], np.cumsum(lens, dtype=np.int64)))
        out = [(self.create(group, f["key"], "float64", width, rows), 1.0)]
        if f["alt_unit_coef"] != 1:
            out.append((self.create(group, f["key"] + "_alt", "float64", width, rows), f["alt_unit_coef"]))
        for i0 in range(0, rows, CHUNK_ROWS):
            n = min(CHUNK_ROWS, rows - i0)
            seg_lens = lens[i0:i0 + n].astype(np.int64)
            vals = np.asarray(values[starts[i0]:starts[i0 + n]])
            block = np.full((width, n), np.nan)
            if np.all(seg_lens == width):
                block[:] = vals.reshape(n, width).T
            elif vals.size:
                row = np.repeat(np.arange(n), seg_lens)
                col = np.arange(vals.size) - np.repeat(starts[i0:i0 + n] - starts[i0], seg_lens)
                block[col, row] = vals
            for ds, coef in out:
                self.put(ds, block * coef if coef != 1 else block, i0)
        del values


def _read_lines(path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8", errors="replace", newline="\n") as f:
        return f.read().split("\n")[:-1]


def _times(path, rows, shift=0.0):
    return (np.fromfile(path, dtype=np.float64)[:rows] - shift) if rows else np.zeros(0)


def export(fastreader, data_gz, log_gz, out_path, info, work_dir, windows=None, progress=lambda fraction: None,
           minimum_free=0):
    """Write the MATLAB file ``out_path``.

    ``windows`` is None for the whole log, else a list of flight dicts with ``index``, ``ac_id``, ``takeoff_s``,
    ``landing_s`` (None: until the end of the log), ``takeoff_utc`` and ``landing_utc`` in log time as stored by
    the parser. ``work_dir`` is a private temporary directory on local disk; the caller removes it."""
    if not log_gz:
        raise ExportError("This log has no companion .log file with the message protocol, so it cannot be "
                          "exported. Upload the .data together with its .log.")
    work = Path(work_dir)
    cols = work / "columns"
    cols.mkdir()
    with gzip.open(log_gz, "rb") as src:
        messages, names = parse_protocol(src.read().decode("utf-8", "replace"))
    write_schema(work / "schema.tsv", messages)
    storage.ensure_space(work, 0, minimum_free)
    spans = None
    cmd = [fastreader, "--export-columns", str(cols), "--schema", str(work / "schema.tsv"),
           "--size", str(int(info.get("size_bytes") or 0))]
    if windows is not None:
        spans = [dict(win, start=win["takeoff_s"] - MARGIN_S,
                      end=(win["landing_s"] if win["landing_s"] is not None else math.inf) + MARGIN_S)
                 for win in windows]
        for span in spans:
            cmd += ["--window", repr(span["start"]), "inf" if math.isinf(span["end"]) else repr(span["end"])]
    cmd.append("-")  # the .data is piped straight out of gzip storage: no uncompressed copy on disk
    errors, feed_error = [], []
    with subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          text=False) as proc:
        def feed():
            try:
                with gzip.open(data_gz, "rb") as src:  # zlib releases the GIL: this overlaps with decoding
                    while chunk := src.read(storage.CHUNK * 4):
                        proc.stdin.write(chunk)
            except (OSError, EOFError) as error:
                feed_error.append(error)
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
        feeder = threading.Thread(target=feed, daemon=True)
        feeder.start()
        for raw in proc.stderr:
            line = raw.decode("utf-8", "replace")
            if line.startswith("progress "):
                progress(0.65 * float(line.split()[1]))
            elif line.strip():
                errors.append(line.strip())
        feeder.join()
    if feed_error and not isinstance(feed_error[0], BrokenPipeError):
        raise ExportError(f"Could not read the stored .data file ({feed_error[0]}).")
    if proc.returncode != 0:
        raise ExportError("; ".join(errors)[-500:] or f"Column export failed (exit code {proc.returncode}).")
    streams, arrays, raws, extra = read_manifest(cols / "manifest.tsv")
    progress(0.7)

    w = Writer(out_path)
    c = Columns(w)
    warnings = []
    try:
        s = w.struct(w.file, "s")
        versions = {}
        definition = messages.get("AUTOPILOT_VERSION")
        for ac, content in extra["versions"].items():
            values = decode(definition, TOKEN.findall(content)) if definition else None
            if values is not None:
                fv = dict(zip((f["name"] for f in definition["fields"]), values))
                versions[ac] = (fv.get("version"), str(fv.get("desc", "")))

        by_window = {}
        for st in streams.values():
            by_window.setdefault(st["window"], {}).setdefault(st["ac"], []).append(st)
        for (window, ac) in raws:
            by_window.setdefault(window, {}).setdefault(ac, [])
        done, total = 0, max(len(streams), 1)

        def aircraft_elements(window, shift):
            nonlocal done
            out = []
            for ac in sorted(by_window.get(window, {})):
                g = w.element()
                w.number(g, "AC_ID", ac)
                w.char(g, "name", names.get(ac, ""))
                version = versions.get(ac)
                w.number(g, "version", version[0] if version else None)
                w.char(g, "version_desc", version[1] if version else "")
                data = w.struct(g, "data")
                for st in sorted(by_window[window][ac], key=lambda x: x["msg"]):
                    m = messages[st["msg"]]
                    mg = w.struct(data, m["key"])
                    stem = cols / f"s{st['id']}"
                    c.scalars(mg, f"{stem}.f64", st["rows"], m["fields"], shift)
                    for j, f in enumerate(m["fields"]):
                        if f["kind"] == "array":
                            c.array(mg, f, f"{stem}_a{j}.len", f"{stem}_a{j}.f64", st["rows"], arrays.get((st["id"], j), 0))
                        elif f["kind"] == "text":
                            w.cellstr(mg, f["key"], _read_lines(Path(f"{stem}_t{j}.txt"))[:st["rows"]])
                    finish_struct(mg)
                    done += 1
                    progress(0.7 + 0.29 * done / total)
                finish_struct(data)
                raw = w.struct(g, "raw")
                rows = raws.get((window, ac), 0)
                w.vector(raw, "timestamp", _times(cols / f"r{window}_{ac}.f64", rows, shift))
                w.char(raw, "lines", "\n".join(_read_lines(cols / f"r{window}_{ac}.txt")))
                finish_struct(raw)
                finish_struct(g)
                out.append(g)
            return out

        meta = w.struct(s, "log")
        w.char(meta, "name", info.get("name"))
        w.char(meta, "sha256", info.get("sha256"))
        w.char(meta, "log_type", info.get("log_type"))
        w.char(meta, "scope", info.get("scope"))
        w.char(meta, "exporter_version", EXPORTER_VERSION)
        w.char(meta, "created_utc", datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        w.number(meta, "margin_s", MARGIN_S if windows is not None else 0)
        finish_struct(meta)

        defs = w.struct(s, "messages")
        for m in messages.values():
            g = w.struct(defs, m["key"])
            w.char(g, "name", m["name"])
            w.char(g, "msg_class", m["msg_class"])
            w.char(g, "id", m["id"])
            w.cellstr(g, "fields", [f["key"] for f in m["fields"]])
            w.cellstr(g, "types", [f["type"] for f in m["fields"]])
            w.cellstr(g, "units", [f["unit"] for f in m["fields"]])
            w.cellstr(g, "alt_units", [f["alt_unit"] for f in m["fields"]])
            w.vector(g, "alt_unit_coef", [f["alt_unit_coef"] for f in m["fields"]])
            finish_struct(g)
        finish_struct(defs)

        if spans is None:
            w.struct_array(s, "aircrafts", aircraft_elements(0, 0.0))
        else:
            elements = []
            for k, span in enumerate(spans):
                g = w.element()
                w.number(g, "index", span["index"])
                w.number(g, "AC_ID", span["ac_id"])
                w.char(g, "aircraft", names.get(span["ac_id"], ""))
                w.char(g, "takeoff_utc", span.get("takeoff_utc"))
                w.char(g, "landing_utc", span.get("landing_utc"))
                w.number(g, "time_offset_s", span["takeoff_s"])
                w.vector(g, "window_s", [-MARGIN_S, span["end"] - span["takeoff_s"]])
                w.struct_array(g, "aircrafts", aircraft_elements(k, span["takeoff_s"]))
                finish_struct(g)
                elements.append(g)
            w.struct_array(s, "flights", elements)
        if extra["top"]:
            raw = w.struct(s, "raw")
            w.vector(raw, "timestamp", _times(cols / "top.f64", extra["top"]))
            w.char(raw, "lines", "\n".join(_read_lines(cols / "top.txt")))
            finish_struct(raw)
            warnings.append(f"{extra['top']} rows without a valid timestamp or aircraft id are kept as text in s.raw.")
        for name, n in sorted(extra["unknown"].items()):
            warnings.append(f"{n} rows of {name}: message not in the embedded protocol; kept as text in raw.")
        for name, n in sorted(extra["rejected"].items()):
            warnings.append(f"{n} rows of {name}: fields did not match the protocol; kept as text in raw.")
        w.cellstr(s, "warnings", warnings)
        finish_struct(s)
    except BaseException:
        c.close()
        w.file.close()
        raise
    c.close()
    w.close()
    progress(1.0)
    return warnings


# ---------------------------------------------------------------------------------------------------------------------
# Jobs: one export per (log content, scope, windows, exporter version), reused for two weeks
# ---------------------------------------------------------------------------------------------------------------------
KEEP_S = 14 * 24 * 3600
MAX_PENDING = 20


class Jobs:
    """Background export queue with a local file cache.

    Finished files are published atomically as ``<id>.mat`` with a ``<id>.json`` side file (log id, download
    name), so a ready export survives a restart. Queued and running jobs live in memory only; after a restart
    the browser asks again and the export is redone. Files older than two weeks are removed, and the oldest go
    first when the folder exceeds ``max_bytes``."""

    def __init__(self, folder, work_root, workers=1, max_bytes=50 << 30):
        self.folder, self.work_root, self.max_bytes = Path(folder), Path(work_root), max_bytes
        self.lock = threading.Lock()
        self.state = {}
        self.queue = queue.Queue()
        self.folder.mkdir(parents=True, exist_ok=True)
        for stale in list(self.folder.glob(".*.tmp")):  # a restart interrupted these
            stale.unlink(missing_ok=True)
        self.cleanup()
        for _ in range(max(1, workers)):
            threading.Thread(target=self._worker, daemon=True).start()

    def cleanup(self):
        limit = datetime.datetime.now().timestamp() - KEEP_S
        files = []
        for path in self.folder.glob("*.mat"):
            try:
                files.append((path.stat().st_mtime, path.stat().st_size, path))
            except OSError:
                pass
        total = sum(size for _, size, _ in files)
        for mtime, size, path in sorted(files):  # oldest first
            if mtime >= limit and total <= self.max_bytes:
                break
            self._remove(path.stem)
            total -= size
        for meta in self.folder.glob("*.json"):
            if not (self.folder / f"{meta.stem}.mat").exists() and self.status_of(meta.stem) is None:
                meta.unlink(missing_ok=True)

    def _remove(self, job_id):
        for suffix in (".mat", ".json"):
            (self.folder / f"{job_id}{suffix}").unlink(missing_ok=True)
        with self.lock:
            if self.state.get(job_id, {}).get("status") == "ready":
                self.state.pop(job_id, None)

    def status_of(self, job_id):
        with self.lock:
            return self.state.get(job_id)

    def status(self, job_id):
        """Job dict (status queued/running/ready/failed) or None when unknown or expired."""
        with self.lock:
            job = dict(self.state[job_id]) if job_id in self.state else None
        if job is None or job["status"] == "ready":
            meta = self.folder / f"{job_id}.json"
            if not (self.folder / f"{job_id}.mat").is_file() or not meta.is_file():
                return None if job is None or job["status"] == "ready" else job
            info = json.loads(meta.read_text())
            job = dict(info, id=job_id, status="ready", progress=1.0,
                       size_bytes=(self.folder / f"{job_id}.mat").stat().st_size)
        return job

    def submit(self, job_id, log_id, filename, task):
        """Start (or join) the export ``job_id``; ``task(out_path, progress)`` writes the file."""
        self.cleanup()
        current = self.status(job_id)
        if current and current["status"] in ("queued", "running", "ready"):
            return current
        with self.lock:
            pending = sum(1 for j in self.state.values() if j["status"] in ("queued", "running"))
            if pending >= MAX_PENDING:
                raise ExportError("Too many MATLAB exports are waiting; try again in a few minutes.")
            self.state[job_id] = dict(id=job_id, log_id=log_id, filename=filename, status="queued", progress=0.0,
                                      error=None)
        self.queue.put((job_id, task))
        return self.status(job_id)

    def path(self, job_id):
        return self.folder / f"{job_id}.mat"

    def _set(self, job_id, **values):
        with self.lock:
            if job_id in self.state:
                self.state[job_id].update(values)

    def _worker(self):
        while True:
            job_id, task = self.queue.get()
            tmp = self.folder / f".{job_id}.tmp"
            work = None
            try:
                self._set(job_id, status="running")
                self.work_root.mkdir(parents=True, exist_ok=True)
                work = tempfile.mkdtemp(dir=self.work_root, prefix="matlab-")
                warnings = task(tmp, work, lambda f: self._set(job_id, progress=round(f, 3)))
                with self.lock:
                    job = dict(self.state[job_id])
                (self.folder / f"{job_id}.json").write_text(json.dumps(dict(
                    log_id=job["log_id"], filename=job["filename"], warnings=len(warnings or []))))
                os.replace(tmp, self.path(job_id))  # published only when complete
                self._set(job_id, status="ready", progress=1.0)
            except ExportError as error:
                self._set(job_id, status="failed", error=str(error))
            except Exception as error:  # keep the worker alive; the message is shown to the user
                self._set(job_id, status="failed", error=f"Export failed: {error}")
            finally:
                if work:
                    shutil.rmtree(work, ignore_errors=True)
                tmp.unlink(missing_ok=True)
                self.queue.task_done()
