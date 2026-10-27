"""On-demand MATLAB v7.3 (HDF5) export of a Paparazzi .data/.log pair, written without MATLAB.

The protocol embedded in the companion .log is read here; the C++ fastreader (``--export-columns``) then decodes
every .data row into plain binary column files, and this module writes those into a MATLAB v7.3 file with h5py
in large blocks. Three scopes exist:

* ``log``: the whole log, every aircraft and message, with the original log timestamps
  (``s.aircraft(i).data.MSG.field``).
* ``flights``: one element of ``s.aircraft(i).flights`` per detected flight of the log (ground records excluded),
  each holding that aircraft's rows from 10 s before takeoff until 10 s after landing.
* ``flight``: the same for a single flight.

Inside a flight element timestamps count from that flight's takeoff (``time.offset_s`` converts back to log
time). Rows that cannot be decoded are kept as text in ``unparsed`` and counted in ``s.log.warnings``; nothing is
dropped silently. The layout (format 4) is described in matlab/README.md.

Run as a script to convert local files without the Web UI: ``python3 matlab_export.py LOG.data [--flights]``.
"""
import collections
import contextlib
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
import zipfile
import zlib
from pathlib import Path

import h5py
import numpy as np

import storage

EXPORTER_VERSION = "4"
MARGIN_S = 10.0
MESSAGE_CLASSES = ("telemetry", "ground", "datalink", "alert")  # lookup order of message names
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
_SCALAR = h5py.h5s.create(h5py.h5s.SCALAR)
_TYPES = {}


def _attr_fast(obj_id, name, value):
    """Write a scalar attribute with the low-level API; h5py's attrs.create dominated exports with many fields."""
    value = np.asarray(value)
    key = value.dtype.str if value.dtype.kind != "S" else ("S", value.dtype.itemsize)
    tid = _TYPES.get(key)
    if tid is None:
        if value.dtype.kind == "S":
            tid = h5py.h5t.C_S1.copy()
            tid.set_size(value.dtype.itemsize)
            tid.set_strpad(h5py.h5t.STR_NULLPAD)  # as h5py writes numpy bytes
        else:
            tid = h5py.h5t.py_create(value.dtype)
        _TYPES[key] = tid
    h5py.h5a.create(obj_id, name, tid, _SCALAR).write(np.ascontiguousarray(value))


def _class(obj, cls):
    _attr_fast(obj.id, b"MATLAB_class", np.bytes_(cls))


class Column:
    """What Columns.put needs of a dataset created with the low-level API."""
    __slots__ = ("id", "chunks", "dtype")

    def __init__(self, dsid, chunks, dtype):
        self.id, self.chunks, self.dtype = dsid, chunks, dtype


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
    h5py's own gzip filter. Chunks are queued across columns and written in order once compressed, so the
    threads stay busy also for the many small columns of a flight export."""

    PENDING = 256  # compressed chunks waiting to be written (bounded memory)

    def __init__(self, writer):
        self.w = writer
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(8, os.cpu_count() or 1)))
        self.pending = collections.deque()
        self.plists = {}

    def flush(self, keep=0):
        while len(self.pending) > keep:
            ds, offset, job = self.pending.popleft()
            ds.id.write_direct_chunk(offset, job.result())

    def close(self, flush=True):
        try:
            if flush:
                self.flush()
        finally:
            self.pending.clear()
            self.pool.shutdown(cancel_futures=True)

    def create(self, parent, name, dtype, width, rows):
        """A deflate-chunked width×rows dataset (MATLAB rows×width), created with cached property lists."""
        dtype = np.dtype(dtype)
        chunks = (width, min(rows, 65536)) if width <= 16 else (min(width, 64), min(rows, 4096))
        dcpl = self.plists.get((dtype, chunks))
        if dcpl is None:
            dcpl = h5py.h5p.create(h5py.h5p.DATASET_CREATE)
            dcpl.set_chunk(chunks)
            dcpl.set_deflate(1)
            fill = np.nan if dtype == np.float64 else 32 if dtype == np.uint16 else 0
            dcpl.set_fill_value(np.array(fill, dtype=dtype))
            dcpl.set_fill_time(h5py.h5d.FILL_TIME_IFSET)
            self.plists[(dtype, chunks)] = dcpl
        space = h5py.h5s.create_simple((width, rows))
        dsid = h5py.h5d.create(parent.id, name.encode(), h5py.h5t.py_create(dtype), space, dcpl=dcpl)
        _attr_fast(dsid, b"MATLAB_class", np.bytes_(MATLAB_CLASS[dtype]))
        if dtype == np.uint16:
            _attr_fast(dsid, b"MATLAB_int_decode", np.int32(2))
        return Column(dsid, chunks, dtype)

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

        for r0 in range(0, block.shape[0], ch_r):
            for c0 in range(0, block.shape[1], ch_c):
                self.pending.append((ds, (r0, col0 + c0), self.pool.submit(tile, r0, c0)))
        self.flush(self.PENDING)

    def scalars(self, group, stream_path, rows, fields, shift):
        """Timestamp plus the scalar numeric fields from rows of [time, field...] doubles. A field with an
        alternate unit is stored converted (e.g. deg) as double; enum fields stay numeric (names in s.messages)."""
        scalar = [f for f in fields if f["kind"] in NUMERIC_DTYPE]
        data = np.memmap(stream_path, dtype=np.float64, mode="r", shape=(rows, 1 + len(scalar)))
        targets = [(self.create(group, "timestamp", "float64", 1, rows), 0, 1.0)]
        for j, f in enumerate(scalar, start=1):
            coef = f["alt_unit_coef"]
            targets.append((self.create(group, f["key"], "float64" if coef != 1 else NUMERIC_DTYPE[f["kind"]], 1,
                                        rows), j, coef))
        for i0 in range(0, rows, CHUNK_ROWS):
            block = np.asarray(data[i0:i0 + CHUNK_ROWS])
            for ds, j, coef in targets:
                col = block[:, j] - shift if j == 0 else block[:, j] * coef if coef != 1 else block[:, j]
                self.put(ds, col.reshape(1, -1), i0)
        del data

    def array(self, group, f, len_path, val_path, rows, width, names=None):
        """A numeric array field as an N×width matrix in its converted unit, shorter rows padded with NaN. With
        ``names`` (one per column, e.g. actuator names) each column is stored as its own N×1 field instead."""
        if width == 0:
            self.w.empty(group, f["key"])
            return
        lens = np.fromfile(len_path, dtype=np.uint32)
        values = np.memmap(val_path, dtype=np.float64, mode="r") if lens.sum() else np.zeros(0)
        starts = np.concatenate(([0], np.cumsum(lens, dtype=np.int64)))
        coef = f["alt_unit_coef"]
        if names and len(names) == width:
            taken, out = set(group.keys()) | {"timestamp"}, []
            for k, name in enumerate(names):
                key = matlab_name(name, taken)
                taken.add(key)
                out.append((self.create(group, key, "float64", 1, rows), slice(k, k + 1)))
        else:
            out = [(self.create(group, f["key"], "float64", width, rows), slice(None))]
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
            if coef != 1:
                block *= coef
            for ds, part in out:
                self.put(ds, block[part], i0)
        del values


def _open(path):
    """A stored log file: gzip-compressed (Web UI storage) or plain (local conversion)."""
    return gzip.open(path, "rb") if str(path).endswith(".gz") else open(path, "rb")


def _read_lines(path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8", errors="replace", newline="\n") as f:
        return f.read().split("\n")[:-1]


def _times(path, rows, shift=0.0):
    return (np.fromfile(path, dtype=np.float64)[:rows] - shift) if rows else np.zeros(0)


def with_window(win):
    """A flight dict plus its export window in log time: 10 s before takeoff to 10 s after landing."""
    return dict(win, start=win["takeoff_s"] - MARGIN_S,
                end=(win["landing_s"] if win["landing_s"] is not None else math.inf) + MARGIN_S)


class DecodedLog:
    """One log decoded by the fastreader (``--export-columns``) into column files under ``work``.

    ``spans`` (flights with windows, see with_window) limits the rows to those flights, each to its own
    aircraft, with DL_VALUE kept over the whole log; None decodes the whole log."""

    def __init__(self, fastreader, data_gz, log_gz, work, size_bytes, spans=None, progress=lambda f: None,
                 minimum_free=0):
        if not log_gz:
            raise ExportError("This log has no companion .log file with the message protocol, so it cannot be "
                              "exported. Upload the .data together with its .log.")
        work = Path(work)
        self.cols, self.spans = work / "columns", spans
        self.cols.mkdir()
        with _open(log_gz) as src:
            log_bytes = src.read()
        self.messages, self.names = parse_protocol(log_bytes.decode("utf-8", "replace"))
        (work / "export.log").write_bytes(log_bytes)  # small; the parser reads block/stage/actuator names from it
        del log_bytes
        write_schema(work / "schema.tsv", self.messages)
        storage.ensure_space(work, 0, minimum_free)
        cmd = [fastreader, "--export-columns", str(self.cols), "--schema", str(work / "schema.tsv"),
               "--log", str(work / "export.log"), "--size", str(int(size_bytes or 0))]
        if spans is not None:
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
                    with _open(data_gz) as src:  # zlib releases the GIL: this overlaps with decoding
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
                    progress(float(line.split()[1]))
                elif line.strip():
                    errors.append(line.strip())
            feeder.join()
        if feed_error and not isinstance(feed_error[0], BrokenPipeError):
            raise ExportError(f"Could not read the stored .data file ({feed_error[0]}).")
        if proc.returncode != 0:
            raise ExportError("; ".join(errors)[-500:] or f"Column export failed (exit code {proc.returncode}).")
        self.streams, self.arrays, self.raws, self.extra = read_manifest(self.cols / "manifest.tsv")
        self.versions = {}
        definition = self.messages.get("AUTOPILOT_VERSION")
        for ac, content in self.extra["versions"].items():
            values = decode(definition, TOKEN.findall(content)) if definition else None
            if values is not None:
                fv = dict(zip((f["name"] for f in definition["fields"]), values))
                self.versions[ac] = (fv.get("version"), str(fv.get("desc", "")))
        self.by_window = {}
        for st in self.streams.values():
            self.by_window.setdefault(st["window"], {}).setdefault(st["ac"], []).append(st)
        for (window, ac) in self.raws:
            self.by_window.setdefault(window, {}).setdefault(ac, [])
        self.settings_window = 0 if spans is None else len(spans)  # where the whole-log DL_VALUE rows are

    def warnings(self):
        extra, out = self.extra, []
        if extra["top"]:
            out.append(f"{extra['top']} rows without a valid timestamp or aircraft id are kept as text in "
                       "s.log.unparsed.")
        for name, n in sorted(extra["unknown"].items()):
            out.append(f"{n} rows of {name}: message not in the embedded protocol; kept as text in unparsed.")
        for name, n in sorted(extra["rejected"].items()):
            out.append(f"{n} rows of {name}: fields did not match the protocol; kept as text in unparsed.")
        return out


class LogWriter:
    """Writes the parts of one DecodedLog into a MATLAB file: aircraft, settings, checklist, config, flights."""

    def __init__(self, w, c, log, extras, progress=lambda f: None):
        self.w, self.c, self.log, self.extras, self.progress = w, c, log, extras or {}, progress
        self.done, self.total = 0, max(len(log.streams), 1)

    def config_names(self, ac):
        extra = self.log.extra
        conf = ac if ac in extra["blocks"] or ac in extra["servos"] else -1  # airframe without ac_id
        return extra["blocks"].get(conf, []), extra["stages"].get(conf, []), extra["servos"].get(conf, [])

    def write_data(self, g, window, ac, shift):
        """data.<MSG> and the rows kept as text (unparsed) of one aircraft in one window, time minus ``shift``."""
        w, c, log = self.w, self.c, self.log
        servos = self.config_names(ac)[2]
        data = w.struct(g, "data")
        for st in sorted(log.by_window.get(window, {}).get(ac, []), key=lambda x: x["msg"]):
            m = log.messages[st["msg"]]
            mg = w.struct(data, m["key"])
            stem = log.cols / f"s{st['id']}"
            c.scalars(mg, f"{stem}.f64", st["rows"], m["fields"], shift)
            for j, f in enumerate(m["fields"]):
                if f["kind"] == "array":
                    names = servos if m["name"] == "ACTUATORS" else None  # ACTUATORS.FRONT_MOT
                    c.array(mg, f, f"{stem}_a{j}.len", f"{stem}_a{j}.f64", st["rows"],
                            log.arrays.get((st["id"], j), 0), names)
                elif f["kind"] == "text":
                    w.cellstr(mg, f["key"], _read_lines(Path(f"{stem}_t{j}.txt"))[:st["rows"]])
            finish_struct(mg)
            self.done += 1
            self.progress(self.done / self.total)
        finish_struct(data)
        rows = log.raws.get((window, ac), 0)
        unparsed(w, g, _times(log.cols / f"r{window}_{ac}.f64", rows, shift),
                 _read_lines(log.cols / f"r{window}_{ac}.txt"))

    def setting_values(self, ac):
        """Every DL_VALUE report of this aircraft (log time), split by setting index."""
        log = self.log
        m = log.messages.get("DL_VALUE")
        st = next((x for x in log.by_window.get(log.settings_window, {}).get(ac, []) if x["msg"] == "DL_VALUE"), None)
        if not m or st is None:
            return {}
        scalar = [f["name"] for f in m["fields"] if f["kind"] in NUMERIC_DTYPE]
        if "index" not in scalar or "value" not in scalar:
            return {}
        data = np.fromfile(log.cols / f"s{st['id']}.f64", dtype=np.float64).reshape(st["rows"], 1 + len(scalar))
        index, value = data[:, 1 + scalar.index("index")], data[:, 1 + scalar.index("value")]
        return {int(i): (data[index == i, 0], value[index == i]) for i in np.unique(index)}

    def flight(self, g, span, window):
        """One flights(k) element: index, time, summary; with ``window`` also its data (time 0 at takeoff)."""
        w = self.w
        w.number(g, "index", span["index"])
        tg = w.struct(g, "time")
        w.char(tg, "takeoff_utc", span.get("takeoff_utc"))
        w.char(tg, "landing_utc", span.get("landing_utc"))
        w.number(tg, "takeoff_posix", span.get("takeoff_posix"))
        w.number(tg, "offset_s", span["takeoff_s"])  # log time of takeoff
        w.vector(tg, "window_s", [span["start"] - span["takeoff_s"], span["end"] - span["takeoff_s"]])
        w.number(tg, "start_s", span["start"])  # log time of the window, e.g. to slice a whole-log export
        w.number(tg, "end_s", span["end"])
        finish_struct(tg)
        sg = w.struct(g, "summary")
        for key, value in (span.get("summary") or {}).items():
            w.char(sg, key, value) if isinstance(value, str) else w.number(sg, key, value)
        assessment = span.get("assessment") or {}
        w.char(sg, "verdict", assessment.get("verdict") or "")
        w.cellstr(sg, "reasons", assessment.get("reasons") or [])
        finish_struct(sg)
        if window is not None:
            self.write_data(g, window, span["ac_id"], span["takeoff_s"])

    def aircraft(self, g, ac):
        """id, name, version, crew, simulation, settings, checklist and config of one aircraft (log time)."""
        w, x, log = self.w, self.extras.get("aircraft", {}).get(ac, {}), self.log
        blocks, stages, servos = self.config_names(ac)
        w.number(g, "id", ac)
        w.char(g, "name", log.names.get(ac, ""))
        version = log.versions.get(ac)
        vg = w.struct(g, "version")
        w.number(vg, "number", version[0] if version else None)
        w.char(vg, "desc", version[1] if version else "")
        finish_struct(vg)
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

        settings, items, reports = w.struct(g, "settings"), x.get("settings") or [], self.setting_values(ac)
        w.table(settings, "list", dict(
            index=[i.get("index") for i in items], var=[i.get("var") for i in items],
            shortname=[i.get("shortname") for i in items], unit=[i.get("unit") for i in items],
            parent=[i.get("parent") for i in items]))
        values, taken = w.struct(settings, "values"), set()
        for item in items:  # settings.values.<name>: every reported value of that setting
            key = matlab_name(item.get("var") or f"setting_{item.get('index')}", taken)
            taken.add(key)
            sg = w.struct(values, key)
            times, vals = reports.get(item.get("index"), (np.zeros(0), np.zeros(0)))
            w.vector(sg, "timestamp", times)
            w.vector(sg, "value", vals)
            finish_struct(sg)
        finish_struct(values)
        changes = x.get("setting_changes") or []
        w.table(settings, "changes", {
            "timestamp": [r.get("time_s") for r in changes], "index": [r.get("idx") for r in changes],
            "name": [r.get("var") or "" for r in changes], "from": [r.get("from_value") for r in changes],
            "to": [r.get("to_value") for r in changes], "in_flight": [1 if r.get("in_flight") else 0 for r in changes]})
        finish_struct(settings)

        checklist, checks = w.struct(g, "checklist"), x.get("checklist") or []
        w.table(checklist, "items", dict(
            timestamp=[r.get("time_s") for r in checks],
            item=[r.get("checklist_label") or r.get("text", "").split(": ", 1)[0] for r in checks],
            answer=[r.get("checklist_value") if r.get("checklist_label") else
                    (r.get("text", "").split(": ", 1) + [""])[1] for r in checks],
            message=[r.get("text") or "" for r in checks]))
        answers, keys = w.struct(checklist, "answers"), {}
        for r in checks:  # the last answer per logged key, e.g. checklist.answers.pic
            key, _, answer = (r.get("text") or "").partition(": ")
            keys[matlab_name(key.strip() or "item")] = answer
        for key, answer in keys.items():
            w.char(answers, key, answer)
        finish_struct(answers)
        finish_struct(checklist)

        config = x.get("config") or {}
        cg = w.struct(g, "config")
        xg = w.struct(cg, "xml")
        for key in ("airframe", "flight_plan", "settings", "radio", "telemetry"):
            w.char(xg, key, (config.get("xml") or {}).get(key) or "")
        finish_struct(xg)
        plan = config.get("flight_plan") or {}
        pg = w.struct(cg, "flight_plan")
        w.char(pg, "name", plan.get("name") or "")
        w.number(pg, "lat0", plan.get("lat0"))
        w.number(pg, "lon0", plan.get("lon0"))
        w.cellstr(pg, "blocks", blocks or [b.get("name") or "" for b in plan.get("blocks") or []])  # cur_block + 1
        flat = [(b, k, name) for b, per_block in enumerate(stages) for k, name in enumerate(per_block)]
        w.table(pg, "stages", dict(block=[b for b, _, _ in flat], stage=[k for _, k, _ in flat],
                                   name=[name for _, _, name in flat]))
        wg, taken = w.struct(pg, "waypoints"), set()  # waypoints.<NAME>.lat / lon / alt
        for p in plan.get("waypoints") or []:
            key = matlab_name(p.get("name") or f"wp_{p.get('index')}", taken)
            taken.add(key)
            pt = w.struct(wg, key)
            w.number(pt, "index", p.get("index"))
            w.char(pt, "name", p.get("name") or "")
            w.number(pt, "lat", p.get("lat"))
            w.number(pt, "lon", p.get("lon"))
            w.number(pt, "alt", p.get("alt_m"))
            finish_struct(pt)
        finish_struct(wg)
        finish_struct(pg)
        w.cellstr(cg, "actuators", servos)
        finish_struct(cg)


def unparsed(w, parent, timestamps, lines):
    """Rows kept as text: unparsed.timestamp and unparsed.lines (one cell per row)."""
    ug = w.struct(parent, "unparsed")
    w.vector(ug, "timestamp", timestamps)
    w.cellstr(ug, "lines", lines)
    finish_struct(ug)


def write_messages(w, s, messages):
    defs = w.struct(s, "messages")
    for m in messages.values():
        g = w.struct(defs, m["key"])
        w.char(g, "name", m["name"])
        w.char(g, "class", m["msg_class"])
        w.char(g, "id", m["id"])
        w.cellstr(g, "fields", [f["key"] for f in m["fields"]])
        w.cellstr(g, "types", [f["type"] for f in m["fields"]])
        # units of the stored columns (the alternate unit where converted); raw value = value / coef
        w.cellstr(g, "units", [f["alt_unit"] if f["alt_unit_coef"] != 1 else f["unit"] for f in m["fields"]])
        w.cellstr(g, "raw_units", [f["unit"] for f in m["fields"]])
        w.vector(g, "coef", [f["alt_unit_coef"] for f in m["fields"]])
        enums = [f for f in m["fields"] if f["values"]]
        if enums:
            vg = w.struct(g, "enums")
            for f in enums:
                w.cellstr(vg, f["key"], f["values"])  # value v is named enums.<field>{v + 1}
            finish_struct(vg)
        finish_struct(g)
    finish_struct(defs)


def export(fastreader, data_gz, log_gz, out_path, info, work_dir, windows=None, progress=lambda fraction: None,
           minimum_free=0, extras=None):
    """Write the MATLAB file ``out_path`` for one log (format 4, see TECHNICAL.md).

    ``windows`` is None for the whole log, else a list of flight dicts with ``index``, ``ac_id``, ``takeoff_s``,
    ``landing_s`` (None: until the end of the log), ``takeoff_utc`` and ``landing_utc`` in log time as stored by
    the parser, optionally with ``summary``, ``assessment`` and ``takeoff_posix``. The layout is
    ``s.aircraft(i).flights(k)``: a flight export puts each flight's own data in its element. ``extras`` holds
    what the Web UI stored per aircraft (``aircraft``: ac_id -> crew, simulation, settings, setting_changes,
    checklist, config), ``flights`` (the same dicts as ``windows``, listed without data in a whole-log export)
    and ``time0_utc`` / ``time0_posix`` (UTC of log time 0). ``work_dir`` is a private temporary directory on local
    disk; the caller removes it."""
    extras = extras or {}
    spans = None if windows is None else [with_window(x) for x in windows]
    log = DecodedLog(fastreader, data_gz, log_gz, work_dir, info.get("size_bytes"), spans,
                     lambda f: progress(0.65 * f), minimum_free)
    progress(0.7)
    w = Writer(out_path)
    c = Columns(w)
    try:
        lw = LogWriter(w, c, log, extras, lambda f: progress(0.7 + 0.29 * f))
        s = w.struct(w.file, "s")
        meta = w.struct(s, "info")
        w.char(meta, "exporter_version", EXPORTER_VERSION)
        w.char(meta, "created_utc", datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        w.char(meta, "scope", info.get("scope"))
        w.number(meta, "margin_s", MARGIN_S if windows is not None else 0)
        finish_struct(meta)
        warnings = log.warnings()
        lg = w.struct(s, "log")
        w.char(lg, "name", info.get("name"))
        w.char(lg, "sha256", info.get("sha256"))
        w.char(lg, "type", info.get("log_type"))
        w.char(lg, "time0_utc", extras.get("time0_utc") or "")  # UTC of log time 0 (whole-log timestamps)
        w.number(lg, "time0_posix", extras.get("time0_posix"))
        w.cellstr(lg, "warnings", warnings)
        unparsed(w, lg, _times(log.cols / "top.f64", log.extra["top"]), _read_lines(log.cols / "top.txt"))
        finish_struct(lg)
        write_messages(w, s, log.messages)
        listed = [with_window(f) for f in (windows if windows is not None else extras.get("flights") or [])]
        ids = sorted(log.by_window.get(0, {})) if spans is None else sorted({sp["ac_id"] for sp in spans})
        elements = []
        for ac in ids:  # in a flight export only aircraft with an exported flight
            g = w.element()
            lw.aircraft(g, ac)
            if spans is None:
                lw.write_data(g, 0, ac, 0.0)
            flights = []
            for k, span in enumerate(listed):
                if span["ac_id"] == ac:
                    fg = w.element()
                    lw.flight(fg, span, k if spans is not None else None)
                    finish_struct(fg)
                    flights.append(fg)
            w.struct_array(g, "flights", flights)
            finish_struct(g)
            elements.append(g)
        w.struct_array(s, "aircraft", elements)
        finish_struct(s)
    except BaseException:
        c.close(flush=False)
        w.file.close()
        raise
    c.close()
    w.close()
    progress(1.0)
    return warnings


class _Sink:
    """A write-only stream collecting what zipfile writes, to send it on in pieces."""

    def __init__(self):
        self.parts = []

    def write(self, data):
        self.parts.append(bytes(data))
        return len(data)

    def flush(self):
        pass

    def take(self):
        out = b"".join(self.parts)
        self.parts.clear()
        return out


def zip_stream(files):
    """Yield an uncompressed .zip of ``files`` (path, name) piece by piece; .mat files are compressed already."""
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
        for path, name in files:
            with open(path, "rb") as src, archive.open(name, "w", force_zip64=True) as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
                    yield sink.take()
    yield sink.take()


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
        for bundle in self.folder.glob("*.bundle"):
            with contextlib.suppress(OSError):
                if bundle.stat().st_mtime < limit:
                    bundle.unlink()

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

    def submit(self, job_id, log_id, filename, task, limit=MAX_PENDING):
        """Start (or join) the export ``job_id``; ``task(out_path, progress)`` writes the file."""
        self.cleanup()
        current = self.status(job_id)
        if current and current["status"] in ("queued", "running", "ready"):
            return current
        with self.lock:
            pending = sum(1 for j in self.state.values() if j["status"] in ("queued", "running"))
            if pending >= limit:
                raise ExportError("Too many MATLAB exports are waiting; try again in a few minutes.")
            self.state[job_id] = dict(id=job_id, log_id=log_id, filename=filename, status="queued", progress=0.0,
                                      error=None)
        self.queue.put((job_id, task))
        return self.status(job_id)

    def path(self, job_id):
        return self.folder / f"{job_id}.mat"

    def bundle(self, bundle_id, log_ids, filename, parts):
        """A .zip of several exports (``parts``: job id and name in the zip), kept like the exports themselves."""
        path = self.folder / f"{bundle_id}.bundle"
        tmp = self.folder / f".{bundle_id}.bundle.tmp"
        tmp.write_text(json.dumps(dict(log_id=log_ids, filename=filename, parts=parts)))
        os.replace(tmp, path)  # also refreshes its age
        return self.bundle_status(bundle_id)

    def bundle_status(self, bundle_id):
        """Combined status of a bundle, or None when unknown or expired."""
        try:
            info = json.loads((self.folder / f"{bundle_id}.bundle").read_text())
        except (OSError, ValueError):
            return None
        jobs = [self.status(job_id) for job_id, _ in info["parts"]]
        ready = sum(1 for j in jobs if j and j["status"] == "ready")
        out = dict(id=bundle_id, log_id=info["log_id"], filename=info["filename"], parts=len(jobs), ready=ready,
                   progress=round(sum(j["progress"] if j else 0 for j in jobs) / max(1, len(jobs)), 3),
                   error=None, size_bytes=None, bundle=True)
        failed = next((j for j in jobs if j is None or j["status"] == "failed"), False)
        if failed is not False:
            out.update(status="failed", error=failed["error"] if failed else
                       "Part of this export expired; start it again.")
        elif ready == len(jobs):
            out.update(status="ready", progress=1.0, size_bytes=sum(j["size_bytes"] for j in jobs))
        else:
            out["status"] = "running" if any(j["status"] == "running" for j in jobs) else "queued"
        return out

    def bundle_files(self, bundle_id):
        info = json.loads((self.folder / f"{bundle_id}.bundle").read_text())
        return [(self.path(job_id), name) for job_id, name in info["parts"]]

    def _set(self, job_id, **values):
        with self.lock:
            if job_id in self.state:
                self.state[job_id].update(values)

    def _worker(self):
        while True:
            job_id, task = self.queue.get()
            tmp = self.folder / f".{job_id}.tmp"
            work = failure = None
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
                failure = str(error)
            except Exception as error:  # keep the worker alive; the message is shown to the user
                failure = f"Export failed: {error}"
            finally:
                if work:
                    shutil.rmtree(work, ignore_errors=True)
                tmp.unlink(missing_ok=True)
                if failure is not None:  # reported once the partial files are gone
                    self._set(job_id, status="failed", error=failure)
                self.queue.task_done()


# ---------------------------------------------------------------------------------------------------------------------
# Local conversion: python3 matlab_export.py LOG.data [--flights] [-o OUT]
# ---------------------------------------------------------------------------------------------------------------------
SUMMARY_KEYS = ("duration_s", "distance_m", "max_from_takeoff_m", "from_takeoff_m", "landed")


def _posix(utc):
    try:
        return datetime.datetime.fromisoformat(utc.replace("Z", "+00:00")).timestamp() if utc else None
    except ValueError:
        return None


def parsed_inputs(doc):
    """Flights (as export() windows) and extras from the C++ parser's JSON, as the Web UI stores them, but without
    the logbook's later edits (crew corrections, date corrections)."""
    flights, aircraft = [], {}
    for ac in doc.get("aircraft", []):
        mission = ac.get("mission") or {}
        aircraft[ac.get("ac_id")] = dict(
            pic=mission.get("pic"), pac=mission.get("pac"), gcs=mission.get("gcs"), goal=mission.get("goal"),
            simulation=ac.get("simulation"), checklist=ac.get("preflight_checks") or [],
            settings=ac.get("settings") or [],
            config=dict(xml=ac.get("xml") or {}, flight_plan=ac.get("flight_plan") or {}),
            setting_changes=[dict(time_s=c.get("time_s"), idx=c.get("index"), var=c.get("var"),
                                  from_value=c.get("from"), to_value=c.get("to"), in_flight=c.get("in_flight"))
                             for c in (ac.get("settings_changes") or {}).get("timeline") or []])
        for f in ac.get("flights", []):
            to, ld = f.get("takeoff") or {}, f.get("landing") or {}
            if to.get("time_s") is None:
                continue
            summary = {k: f.get(k) for k in SUMMARY_KEYS}
            for prefix, point in (("takeoff", to), ("landing", ld)):
                loc = point.get("location") or {}
                summary.update({f"{prefix}_lat": loc.get("lat"), f"{prefix}_lon": loc.get("lon"),
                                f"{prefix}_alt_m": loc.get("alt_m"), f"{prefix}_voltage_v": point.get("voltage_v")})
            summary["landed"] = 1 if summary["landed"] else 0
            assessment = f.get("assessment") or {}
            flights.append(dict(index=(f.get("flight") or 0) + 1, ac_id=ac.get("ac_id"), takeoff_s=to["time_s"],
                                landing_s=ld.get("time_s"), takeoff_utc=to.get("utc"), landing_utc=ld.get("utc"),
                                takeoff_posix=_posix(to.get("utc")), summary=summary,
                                assessment=dict(verdict=assessment.get("verdict"),
                                                reasons=assessment.get("reasons") or [])))
    flights.sort(key=lambda x: x["takeoff_s"])
    time0 = (doc.get("log") or {}).get("start_utc")
    return flights, dict(aircraft=aircraft, time0_utc=time0, time0_posix=_posix(time0))


def convert(fastreader, data_path, out_path, scope="log", progress=lambda fraction: None):
    """Export a local .data (with its .log next to it, plain or .gz) to ``out_path``; returns the warnings."""
    data_path = Path(data_path)
    stem = data_path.name[:-3] if data_path.name.endswith(".gz") else data_path.name
    stem = stem[:-5] if stem.endswith(".data") else stem
    log_path = next((p for p in (data_path.with_name(stem + ".log"), data_path.with_name(stem + ".log.gz"))
                     if p.exists()), None)
    with tempfile.TemporaryDirectory(prefix="matlab-") as work:
        work = Path(work)
        plain = data_path
        if data_path.name.endswith(".gz"):  # the parser reads plain files; the .log is found next to the .data
            plain = work / f"{stem}.data"
            with gzip.open(data_path, "rb") as src, open(plain, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            if log_path and log_path.name.endswith(".gz"):
                with gzip.open(log_path, "rb") as src, open(work / f"{stem}.log", "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
            elif log_path:
                shutil.copyfile(log_path, work / f"{stem}.log")
        proc = subprocess.run([fastreader, str(plain)], capture_output=True)
        if proc.returncode != 0:
            raise ExportError(proc.stderr.decode(errors="replace").strip() or f"parser exit code {proc.returncode}")
        flights, extras = parsed_inputs(json.loads(proc.stdout))
        windows = None
        if scope == "flights":
            windows = [f for f in flights if f["assessment"]["verdict"] != "ground"]
            if not windows:
                raise ExportError("No flights detected in this log.")
        else:
            extras["flights"] = flights
        (work / "export").mkdir()
        info = dict(name=data_path.name, sha256="", log_type="onboard_sd" if stem.endswith("_SD") else "gcs",
                    size_bytes=plain.stat().st_size, scope=scope)
        return export(fastreader, data_path, log_path, out_path, info, work / "export", windows, progress,
                      extras=extras)


def main(argv=None):
    import argparse
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Convert Paparazzi .data/.log files to MATLAB (format 4), as the "
                                     "Web UI export does. The .log must be next to its .data (plain or .gz).")
    parser.add_argument("data", nargs="+", help=".data files (or .data.gz)")
    parser.add_argument("--flights", action="store_true", help="only the detected flights, each from 10 s before "
                        "takeoff to 10 s after landing (default: the whole log)")
    parser.add_argument("-o", "--out", help="output .mat (one input) or folder (default: next to the .data)")
    parser.add_argument("--parser", default=os.environ.get("LOGDB_FASTREADER", str(
        here.parent / "cpp" / "build" / "paparazzi_log_parsing")), help="path of the built C++ parser")
    args = parser.parse_args(argv)
    if not Path(args.parser).is_file():
        parser.error(f"parser not found at {args.parser}; build cpp/ first (see matlab/README.md)")
    failed = 0
    for name in args.data:
        data = Path(name)
        stem = re.sub(r"(\.data)?(\.gz)?$", "", data.name)
        target = f"{stem}_{'flights' if args.flights else 'whole_log'}.mat"
        out = Path(args.out) if args.out else data.parent
        if args.out and (len(args.data) > 1 or out.is_dir()):
            out.mkdir(parents=True, exist_ok=True)
            out = out / target
        elif not args.out:
            out = out / target
        try:
            warnings = convert(args.parser, data, out, "flights" if args.flights else "log",
                               lambda f: print(f"\r{data.name}: {f * 100:3.0f}%", end="", flush=True))
            print(f"\r{data.name} -> {out}" + (f" ({len(warnings)} warnings)" if warnings else ""))
        except (ExportError, OSError) as error:
            failed += 1
            print(f"\r{data.name}: {error}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
