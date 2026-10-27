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

EXPORTER_VERSION = "3"
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
                taken.update((key, key + "_raw", key + "_name"))
                values = [v.strip() for v in _attr(field, "values").split("|")] if _attr(field, "values") else []
                fields.append(dict(name=fname, key=key, type=_attr(field, "type"), kind=field_kind(_attr(field, "type")),
                                   unit=_attr(field, "unit"), alt_unit=_attr(field, "alt_unit"),
                                   alt_unit_coef=float(coef) if coef else 1.0, values=values))
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
                np.dtype("uint8"): "uint8", np.dtype("uint16"): "char"}


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

    def table(self, parent, name, columns):
        """A scalar struct of equal-length columns: lists of numbers become N×1 double, lists of str a cellstr."""
        group = self.struct(parent, name)
        for key, values in columns.items():
            if values and all(isinstance(v, str) or v is None for v in values):
                self.cellstr(group, key, ["" if v is None else v for v in values])
            else:
                self.vector(group, key, [np.nan if v is None else v for v in values])
        finish_struct(group)
        return group

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
    streams, arrays, raws = {}, {}, {}
    out = dict(top=0, unknown={}, rejected={}, versions={}, blocks={}, stages={}, servos={})
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
            elif part[0] in ("block", "servo") and len(part) > 3:
                names = out[part[0] + "s"].setdefault(int(part[1]), [])
                names += [""] * (int(part[2]) + 1 - len(names))
                names[int(part[2])] = part[3]
            elif part[0] == "stage" and len(part) > 4:
                per_block = out["stages"].setdefault(int(part[1]), [])
                per_block += [[] for _ in range(int(part[2]) + 1 - len(per_block))]
                per_block[int(part[2])] += [""] * (int(part[3]) + 1 - len(per_block[int(part[2])]))
                per_block[int(part[2])][int(part[3])] = part[4]
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
                                   fillvalue=np.nan if dtype == "float64" else 32 if dtype == "uint16" else 0)
        _class(ds, MATLAB_CLASS[np.dtype(dtype)])
        if dtype == "uint16":
            ds.attrs.create("MATLAB_int_decode", np.int32(2))
        return ds

    def names(self, group, name, codes, table, rows, i0, created):
        """Per-row names as an N×L MATLAB char matrix (space padded): ``table[code]``, blank when out of range."""
        width = max((len(t) for t in table), default=0)
        if not width:
            return
        if name not in created:
            created[name] = self.create(group, name, "uint16", width, rows)
        lookup = np.full((len(table) + 1, width), 32, dtype=np.uint16)  # last row: blank
        for k, t in enumerate(table):
            lookup[k, :len(t)] = [ord(ch) if ord(ch) < 0x10000 else 0xFFFD for ch in t]
        idx = np.where((codes >= 0) & (codes < len(table)) & np.isfinite(codes), codes, len(table)).astype(np.int64)
        self.put(created[name], lookup[idx].T, i0)

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

    def scalars(self, group, stream_path, rows, fields, shift, nav=None):
        """Timestamp plus the scalar numeric fields from rows of [time, field...] doubles.

        A field with an alternate unit is stored converted (e.g. deg) under its own name, with the logged value as
        ``<field>_raw``. Enum fields (``values`` in the protocol) get ``<field>_name``; ``nav`` (block names and
        per-block stage names) adds ``cur_block_name`` / ``cur_stage_name``."""
        scalar = [f for f in fields if f["kind"] in NUMERIC_DTYPE]
        data = np.memmap(stream_path, dtype=np.float64, mode="r", shape=(rows, 1 + len(scalar)))
        targets = [(self.create(group, "timestamp", "float64", 1, rows), 0, 1.0)]
        position = {}
        for j, f in enumerate(scalar, start=1):
            position[f["name"]] = j
            if f["alt_unit_coef"] != 1:
                targets.append((self.create(group, f["key"], "float64", 1, rows), j, f["alt_unit_coef"]))
                targets.append((self.create(group, f["key"] + "_raw", NUMERIC_DTYPE[f["kind"]], 1, rows), j, 1.0))
            else:
                targets.append((self.create(group, f["key"], NUMERIC_DTYPE[f["kind"]], 1, rows), j, 1.0))
        blocks, stages = nav or ([], [])
        flat = [name for per_block in stages for name in per_block]
        offsets = np.cumsum([0] + [len(per_block) for per_block in stages])
        created = {}
        for i0 in range(0, rows, CHUNK_ROWS):
            block = np.asarray(data[i0:i0 + CHUNK_ROWS])
            for ds, j, coef in targets:
                col = block[:, j] - shift if j == 0 else block[:, j] * coef if coef != 1 else block[:, j]
                self.put(ds, col.reshape(1, -1), i0)
            for j, f in enumerate(scalar, start=1):
                if f["values"]:
                    self.names(group, f["key"] + "_name", block[:, j], f["values"], rows, i0, created)
            if blocks and "cur_block" in position:
                cur = block[:, position["cur_block"]]
                self.names(group, "cur_block_name", cur, blocks, rows, i0, created)
                if "cur_stage" in position and flat:
                    stage = block[:, position["cur_stage"]]
                    valid = (cur >= 0) & (cur < len(stages)) & np.isfinite(cur)
                    b = np.where(valid, cur, 0).astype(np.int64)
                    per = np.array([len(x) for x in stages] + [0])[np.where(valid, b, len(stages))]
                    code = np.where(valid & (stage >= 0) & (stage < per), offsets[b] + stage, -1)
                    self.names(group, "cur_stage_name", code, flat, rows, i0, created)
        del data

    def array(self, group, f, len_path, val_path, rows, width, names=None):
        """A numeric array field as an N×width matrix, shorter rows padded with NaN. With ``names`` (one per
        column, e.g. actuator names) every column is also stored as its own N×1 field."""
        if width == 0:
            self.w.empty(group, f["key"])
            if f["alt_unit_coef"] != 1:
                self.w.empty(group, f["key"] + "_raw")
            return
        lens = np.fromfile(len_path, dtype=np.uint32)
        values = np.memmap(val_path, dtype=np.float64, mode="r") if lens.sum() else np.zeros(0)
        starts = np.concatenate(([0], np.cumsum(lens, dtype=np.int64)))
        out = [(self.create(group, f["key"], "float64", width, rows), f["alt_unit_coef"])]  # converted unit
        if f["alt_unit_coef"] != 1:
            out.append((self.create(group, f["key"] + "_raw", "float64", width, rows), 1.0))
        named = []
        if names and len(names) == width:
            taken = set(group.keys()) | {"timestamp", "names", f["key"], f["key"] + "_raw"}
            for k, name in enumerate(names):
                key = matlab_name(name, taken)
                taken.add(key)
                named.append((self.create(group, key, "float64", 1, rows), k))
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
            for ds, k in named:
                self.put(ds, block[k:k + 1] * f["alt_unit_coef"] if f["alt_unit_coef"] != 1 else block[k:k + 1], i0)
        del values


def _read_lines(path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8", errors="replace", newline="\n") as f:
        return f.read().split("\n")[:-1]


def _times(path, rows, shift=0.0):
    return (np.fromfile(path, dtype=np.float64)[:rows] - shift) if rows else np.zeros(0)


def export(fastreader, data_gz, log_gz, out_path, info, work_dir, windows=None, progress=lambda fraction: None,
           minimum_free=0, extras=None):
    """Write the MATLAB file ``out_path``.

    ``windows`` is None for the whole log, else a list of flight dicts with ``index``, ``ac_id``, ``takeoff_s``,
    ``landing_s`` (None: until the end of the log), ``takeoff_utc`` and ``landing_utc`` in log time as stored by
    the parser, optionally with ``summary``, ``assessment`` and ``takeoff_posix``. The layout is
    ``s.aircrafts(i).flights(k)``: a flight export puts each flight's own data in its element. ``extras`` holds
    what the Web UI stored per aircraft (``aircraft``: ac_id -> crew, simulation, settings, setting_changes,
    checklist, config), ``flights`` (the same dicts as ``windows``, listed without data in a whole-log export)
    and ``time0_utc`` / ``time0_posix`` (UTC of log time 0). ``work_dir`` is a private temporary directory on local
    disk; the caller removes it."""
    extras = extras or {}
    if not log_gz:
        raise ExportError("This log has no companion .log file with the message protocol, so it cannot be "
                          "exported. Upload the .data together with its .log.")
    work = Path(work_dir)
    cols = work / "columns"
    cols.mkdir()
    with gzip.open(log_gz, "rb") as src:
        log_bytes = src.read()
    messages, names = parse_protocol(log_bytes.decode("utf-8", "replace"))
    (work / "export.log").write_bytes(log_bytes)  # small; the parser reads block/stage/actuator names from it
    del log_bytes
    write_schema(work / "schema.tsv", messages)
    storage.ensure_space(work, 0, minimum_free)
    spans = None
    cmd = [fastreader, "--export-columns", str(cols), "--schema", str(work / "schema.tsv"),
           "--log", str(work / "export.log"), "--size", str(int(info.get("size_bytes") or 0))]
    if windows is not None:
        spans = [dict(win, start=win["takeoff_s"] - MARGIN_S,
                      end=(win["landing_s"] if win["landing_s"] is not None else math.inf) + MARGIN_S)
                 for win in windows]
        for span in spans:  # a flight holds only its own aircraft's rows
            cmd += ["--window-ac", str(span["ac_id"]), repr(span["start"]),
                    "inf" if math.isinf(span["end"]) else repr(span["end"])]
        cmd += ["--always", "DL_VALUE"]  # setting values by name cover the whole log
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
        settings_window = 0 if spans is None else len(spans)  # where the whole-log DL_VALUE rows are

        def config_names(ac):
            conf = ac if ac in extra["blocks"] or ac in extra["servos"] else -1  # airframe without ac_id
            return (extra["blocks"].get(conf, []), extra["stages"].get(conf, [])), extra["servos"].get(conf, [])

        def write_data(g, window, ac, shift):
            nonlocal done
            nav, servos = config_names(ac)
            data = w.struct(g, "data")
            for st in sorted(by_window.get(window, {}).get(ac, []), key=lambda x: x["msg"]):
                m = messages[st["msg"]]
                mg = w.struct(data, m["key"])
                stem = cols / f"s{st['id']}"
                c.scalars(mg, f"{stem}.f64", st["rows"], m["fields"], shift, nav)
                for j, f in enumerate(m["fields"]):
                    if f["kind"] == "array":
                        width = arrays.get((st["id"], j), 0)
                        actuators = servos if m["name"] == "ACTUATORS" else None
                        c.array(mg, f, f"{stem}_a{j}.len", f"{stem}_a{j}.f64", st["rows"], width, actuators)
                        if actuators and len(actuators) == width and "names" not in mg:
                            w.cellstr(mg, "names", actuators)  # values(:, k) is names{k}, also stored by name
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

        def setting_values(ac):
            """Every DL_VALUE report of this aircraft (log time), split by setting index."""
            m = messages.get("DL_VALUE")
            st = next((x for x in by_window.get(settings_window, {}).get(ac, []) if x["msg"] == "DL_VALUE"), None)
            if not m or st is None:
                return {}
            scalar = [f["name"] for f in m["fields"] if f["kind"] in NUMERIC_DTYPE]
            if "index" not in scalar or "value" not in scalar:
                return {}
            data = np.fromfile(cols / f"s{st['id']}.f64", dtype=np.float64).reshape(st["rows"], 1 + len(scalar))
            index, value = data[:, 1 + scalar.index("index")], data[:, 1 + scalar.index("value")]
            return {int(i): (data[index == i, 0], value[index == i]) for i in np.unique(index)}

        def flight_element(span, k):
            g = w.element()
            w.number(g, "index", span["index"])
            w.char(g, "takeoff_utc", span.get("takeoff_utc"))
            w.char(g, "landing_utc", span.get("landing_utc"))
            w.number(g, "takeoff_posix", span.get("takeoff_posix"))
            w.number(g, "time_offset_s", span["takeoff_s"])  # log time of takeoff
            w.number(g, "start_s", span["start"])  # log time of the window, e.g. to slice a whole-log export
            w.number(g, "end_s", span["end"])
            w.vector(g, "window_s", [span["start"] - span["takeoff_s"], span["end"] - span["takeoff_s"]])
            sg = w.struct(g, "summary")
            for key, value in (span.get("summary") or {}).items():
                w.char(sg, key, value) if isinstance(value, str) else w.number(sg, key, value)
            finish_struct(sg)
            ag = w.struct(g, "assessment")
            assessment = span.get("assessment") or {}
            w.char(ag, "verdict", assessment.get("verdict") or "")
            w.cellstr(ag, "reasons", assessment.get("reasons") or [])
            finish_struct(ag)
            if k is not None:  # flight exports carry the data; time 0 is this flight's takeoff
                write_data(g, k, span["ac_id"], span["takeoff_s"])
            finish_struct(g)
            return g

        def with_window(win):
            return dict(win, start=win["takeoff_s"] - MARGIN_S,
                        end=(win["landing_s"] if win["landing_s"] is not None else math.inf) + MARGIN_S)

        def aircraft_extras(g, x, blocks, values):
            crew = w.struct(g, "crew")  # as written in the log, not account names
            for key in ("pic", "pac", "gcs", "goal"):
                w.char(crew, key, x.get(key) or "")
            finish_struct(crew)
            sim = w.struct(g, "simulation")
            simulation = x.get("simulation")
            w.number(sim, "assessed", 0 if simulation is None else 1)
            w.number(sim, "detected", 1 if simulation and simulation.get("detected") else 0)
            w.cellstr(sim, "evidence", (simulation or {}).get("evidence") or [])
            finish_struct(sim)
            items = x.get("settings") or []
            w.table(g, "settings_list", dict(
                index=[i.get("index") for i in items], var=[i.get("var") for i in items],
                shortname=[i.get("shortname") for i in items], unit=[i.get("unit") for i in items],
                parent=[i.get("parent") for i in items]))
            settings, taken = w.struct(g, "settings"), set()
            for item in items:  # a.settings.<name>: every reported value of that setting
                key = matlab_name(item.get("var") or f"setting_{item.get('index')}", taken)
                taken.add(key)
                sg = w.struct(settings, key)
                w.number(sg, "index", item.get("index"))
                w.char(sg, "var", item.get("var") or "")
                w.char(sg, "shortname", item.get("shortname") or "")
                w.char(sg, "unit", item.get("unit") or "")
                w.char(sg, "parent", item.get("parent") or "")
                times, vals = values.get(item.get("index"), (np.zeros(0), np.zeros(0)))
                w.vector(sg, "timestamp", times)
                w.vector(sg, "value", vals)
                finish_struct(sg)
            finish_struct(settings)
            changes = x.get("setting_changes") or []
            w.table(g, "setting_changes", dict(
                timestamp=[r.get("time_s") for r in changes], index=[r.get("idx") for r in changes],
                name=[r.get("var") or "" for r in changes], from_value=[r.get("from_value") for r in changes],
                to_value=[r.get("to_value") for r in changes], in_flight=[1 if r.get("in_flight") else 0 for r in changes]))
            checks = x.get("checklist") or []
            w.table(g, "checklist", dict(
                timestamp=[r.get("time_s") for r in checks],
                item=[r.get("checklist_label") or r.get("text", "").split(": ", 1)[0] for r in checks],
                answer=[r.get("checklist_value") if r.get("checklist_label") else
                        (r.get("text", "").split(": ", 1) + [""])[1] for r in checks],
                message=[r.get("text") or "" for r in checks]))
            answers, keys = w.struct(g, "checklist_answers"), {}
            for r in checks:  # the last answer per logged key, e.g. checklist_answers.pic
                key, _, answer = (r.get("text") or "").partition(": ")
                keys[matlab_name(key.strip() or "item")] = answer
            for key, answer in keys.items():
                w.char(answers, key, answer)
            finish_struct(answers)
            config = x.get("config") or {}
            cg = w.struct(g, "config")
            for key in ("airframe", "flight_plan", "settings", "radio", "telemetry"):
                w.char(cg, key + "_xml", (config.get("xml") or {}).get(key) or "")
            plan = config.get("flight_plan") or {}
            pg = w.struct(cg, "flight_plan")
            w.char(pg, "name", plan.get("name") or "")
            w.number(pg, "lat0", plan.get("lat0"))
            w.number(pg, "lon0", plan.get("lon0"))
            wps, wg, taken = plan.get("waypoints") or [], None, set()
            wg = w.struct(pg, "waypoints")  # waypoints.<NAME>.lat / lon / alt_m
            for p in wps:
                key = matlab_name(p.get("name") or f"wp_{p.get('index')}", taken)
                taken.add(key)
                pt = w.struct(wg, key)
                w.number(pt, "index", p.get("index"))
                w.char(pt, "name", p.get("name") or "")
                for field in ("lat", "lon", "alt_m"):
                    w.number(pt, field, p.get(field))
                finish_struct(pt)
            finish_struct(wg)
            w.cellstr(pg, "waypoint_names", [p.get("name") or "" for p in wps])
            w.cellstr(pg, "blocks", blocks or [b.get("name") or "" for b in plan.get("blocks") or []])
            finish_struct(pg)
            finish_struct(cg)

        def aircraft_elements():
            flights = [with_window(f) for f in (spans if spans is not None else extras.get("flights") or [])]
            if spans is None:
                ids = sorted(by_window.get(0, {}))
            else:  # only aircraft with an exported flight
                ids = sorted({sp["ac_id"] for sp in spans})
            out = []
            for ac in ids:
                g = w.element()
                w.number(g, "AC_ID", ac)
                w.char(g, "name", names.get(ac, ""))
                version = versions.get(ac)
                w.number(g, "version", version[0] if version else None)
                w.char(g, "version_desc", version[1] if version else "")
                nav, _ = config_names(ac)
                aircraft_extras(g, extras.get("aircraft", {}).get(ac, {}), nav[0], setting_values(ac))
                if spans is None:
                    write_data(g, 0, ac, 0.0)
                mine = [(k, f) for k, f in enumerate(flights) if f["ac_id"] == ac]
                w.struct_array(g, "flights", [flight_element(f, k if spans is not None else None) for k, f in mine])
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
        w.char(meta, "time0_utc", extras.get("time0_utc") or "")  # UTC of log time 0 (whole-log timestamps)
        w.number(meta, "time0_posix", extras.get("time0_posix"))
        finish_struct(meta)

        defs = w.struct(s, "messages")
        for m in messages.values():
            g = w.struct(defs, m["key"])
            w.char(g, "name", m["name"])
            w.char(g, "msg_class", m["msg_class"])
            w.char(g, "id", m["id"])
            w.cellstr(g, "fields", [f["key"] for f in m["fields"]])
            w.cellstr(g, "types", [f["type"] for f in m["fields"]])
            # units of the stored columns: the alternate unit where one is converted; *_raw keeps raw_units
            w.cellstr(g, "units", [f["alt_unit"] if f["alt_unit_coef"] != 1 else f["unit"] for f in m["fields"]])
            w.cellstr(g, "raw_units", [f["unit"] for f in m["fields"]])
            w.vector(g, "alt_unit_coef", [f["alt_unit_coef"] for f in m["fields"]])
            enums = [f for f in m["fields"] if f["values"]]
            if enums:
                vg = w.struct(g, "values")
                for f in enums:
                    w.cellstr(vg, f["key"], f["values"])  # value v is named values{v + 1}
                finish_struct(vg)
            finish_struct(g)
        finish_struct(defs)

        w.struct_array(s, "aircrafts", aircraft_elements())
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
