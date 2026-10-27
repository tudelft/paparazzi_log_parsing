"""On-demand MATLAB v7.3 (HDF5) export of a Paparazzi .data/.log pair, written without MATLAB.

The exporter reads the original stored files, decodes each row with the protocol embedded in the companion
.log and streams the columns into a MATLAB v7.3 file with h5py. Three scopes exist:

* ``log``: the whole log, every aircraft and message, with the original log timestamps
  (``s.aircrafts(i).data.MSG.field``, close to ``matlab/tools/parselog.m``).
* ``flights``: one element of ``s.flights`` per detected flight of the log (ground records excluded),
  each holding every aircraft's rows from 10 s before takeoff until 10 s after landing.
* ``flight``: the same for a single flight.

Inside a flight element timestamps count from that flight's takeoff (``time_offset_s`` converts back to log
time). Rows that cannot be decoded are kept as text in ``raw`` and counted in ``s.warnings``; nothing is dropped
silently. See TECHNICAL.md ("MATLAB export") for the layout.
"""
import datetime
import math
import re
import xml.etree.ElementTree as ET

import h5py
import numpy as np

import storage

EXPORTER_VERSION = "1"
MARGIN_S = 10.0
FLUSH_ROWS = 8192
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
# Streaming column stores
# ---------------------------------------------------------------------------------------------------------------------
NUMERIC = {"uint": "uint32", "int": "int32", "float": "float64"}


class Column:
    """A growing MATLAB N×1 (or N×k for arrays) dataset, appended in chunks with bounded memory."""

    def __init__(self, writer, parent, name, dtype, width=None):
        self.writer, self.parent, self.name, self.dtype, self.width = writer, parent, name, dtype, width
        self.buffer, self.rows, self.ds = [], 0, None

    def add(self, value):
        self.buffer.append(value)
        if len(self.buffer) >= FLUSH_ROWS:
            self.flush()

    def extend(self, values):
        """Append a numpy vector of scalar values at once."""
        self.flush()
        self._append(np.asarray(values, dtype=self.dtype).reshape(1, -1))

    def flush(self):
        if not self.buffer:
            return
        if self.width is None:
            block = np.asarray(self.buffer, dtype=self.dtype).reshape(1, -1)
        else:
            width = max(max((len(v) for v in self.buffer), default=0), 1)
            block = np.full((width, len(self.buffer)), np.nan)
            for i, v in enumerate(self.buffer):
                block[:len(v), i] = v
        self.buffer = []
        self._append(block)

    def _append(self, block):
        if not block.shape[1]:
            return
        if self.ds is None:
            self.ds = self.parent.create_dataset(
                self.name, shape=(block.shape[0], 0), maxshape=(None, None), dtype=self.dtype,
                chunks=(block.shape[0], 4096) if self.width is None else (min(block.shape[0], 64), 512),
                compression="gzip", compression_opts=1, fillvalue=np.nan if self.dtype == "float64" else 0)
            _class(self.ds, MATLAB_CLASS[np.dtype(self.dtype)])
        rows = self.ds.shape[0]
        if block.shape[0] > rows:
            self.ds.resize(block.shape[0], axis=0)  # longer array rows: earlier rows are padded with NaN
        self.ds.resize(self.rows + block.shape[1], axis=1)
        self.ds[:block.shape[0], self.rows:] = block
        self.rows += block.shape[1]

    def finish(self):
        self.flush()
        if self.ds is None:
            self.writer.empty(self.parent, self.name, MATLAB_CLASS[np.dtype(self.dtype)])


class TextColumn:
    """Strings, written as one cellstr at the end (text fields are low-rate in practice)."""

    def __init__(self, writer, parent, name):
        self.writer, self.parent, self.name, self.values = writer, parent, name, []

    def add(self, value):
        self.values.append(value)

    def finish(self):
        self.writer.cellstr(self.parent, self.name, self.values)


class MessageStore:
    """One message of one aircraft: timestamp plus a column (and _alt column) per field.

    Rows are buffered undecoded. A message with only scalar numeric fields is decoded a block at a time with
    numpy (much faster than per row); a block with any row that does not fit falls back to per-row decoding,
    which sends just the bad rows to ``raw``."""

    def __init__(self, writer, data_group, definition, raw, rejected):
        self.definition, self.raw, self.rejected, self.pending = definition, raw, rejected, []
        self.fast = all(f["kind"] in NUMERIC for f in definition["fields"])
        self.group = writer.struct(data_group, definition["key"])
        self.time = Column(writer, self.group, "timestamp", "float64")
        self.columns = []
        for f in definition["fields"]:
            if f["kind"] == "text":
                col = TextColumn(writer, self.group, f["key"])
            elif f["kind"] == "array":
                col = Column(writer, self.group, f["key"], "float64", width=True)
            else:
                col = Column(writer, self.group, f["key"], NUMERIC[f["kind"]])
            alt = None
            if f["alt_unit_coef"] != 1 and f["kind"] != "text":
                alt = Column(writer, self.group, f["key"] + "_alt", "float64", width=True if f["kind"] == "array" else None)
            self.columns.append((f, col, alt))

    def pend(self, t, content, line):
        self.pending.append((t, content, line))
        if len(self.pending) >= FLUSH_ROWS:
            self.decode_pending()

    def decode_pending(self):
        rows, self.pending = self.pending, []
        if not rows:
            return
        if self.fast and self.columns:
            block = self._fast_block(rows)
            if block is not None:
                self.time.extend([t for t, _, _ in rows])
                for j, (f, col, alt) in enumerate(self.columns):
                    col.extend(block[:, j])
                    if alt is not None:
                        alt.extend(block[:, j] * f["alt_unit_coef"])
                return
        for t, content, line in rows:
            values = decode(self.definition, TOKEN.findall(content))
            if values is None:
                name = self.definition["name"]
                self.rejected[name] = self.rejected.get(name, 0) + 1
                self.raw.add(t, line)
            else:
                self.add(t, values)

    def _fast_block(self, rows):
        """rows × fields float array, or None when any row has the wrong field count or a bad value."""
        tokens = " ".join(content for _, content, _ in rows).split()
        if len(tokens) != len(rows) * len(self.columns):
            return None
        try:
            block = np.array(tokens, dtype=np.float64).reshape(len(rows), len(self.columns))
        except ValueError:
            return None
        for j, (f, _, _) in enumerate(self.columns):
            if f["kind"] != "float":
                col = block[:, j]
                low, high = (0, 2 ** 32) if f["kind"] == "uint" else (-2 ** 31, 2 ** 31)
                if not (np.all(col == np.round(col)) and col.min() >= low and col.max() < high):
                    return None
        return block

    def add(self, t, values):
        self.time.add(t)
        for (f, col, alt), v in zip(self.columns, values):
            col.add(v)
            if alt is not None:
                alt.add([x * f["alt_unit_coef"] for x in v] if f["kind"] == "array" else float(v) * f["alt_unit_coef"])

    def finish(self):
        self.decode_pending()
        self.time.finish()
        for _, col, alt in self.columns:
            col.finish()
            if alt is not None:
                alt.finish()
        finish_struct(self.group)


class RawStore:
    """Undecodable rows of one aircraft, kept losslessly as timestamps plus newline-separated text."""

    def __init__(self, writer, group):
        self.writer, self.group, self.count = writer, group, 0
        self.time = Column(writer, group, "timestamp", "float64")
        self.text = []

    def add(self, t, line):
        self.count += 1
        self.time.add(t)
        self.text.append(line)

    def finish(self):
        self.time.finish()
        self.writer.char(self.group, "lines", "\n".join(self.text))
        finish_struct(self.group)


class AircraftStore:
    def __init__(self, writer, group, ac_id, name, rejected):
        self.writer, self.group, self.ac_id, self.name, self.rejected = writer, group, ac_id, name, rejected
        self.data = writer.struct(group, "data")
        self.raw = RawStore(writer, writer.struct(group, "raw"))
        self.messages = {}

    def add(self, t, definition, content, line):
        store = self.messages.get(definition["name"])
        if store is None:
            store = self.messages[definition["name"]] = MessageStore(self.writer, self.data, definition,
                                                                     self.raw, self.rejected)
        store.pend(t, content, line)

    def finish(self, version):
        w = self.writer
        w.number(self.group, "AC_ID", self.ac_id)
        w.char(self.group, "name", self.name or "")
        w.number(self.group, "version", version[0] if version else None)
        w.char(self.group, "version_desc", version[1] if version else "")
        for store in self.messages.values():
            store.finish()  # may still add rejected rows to raw, so raw is finished after the messages
        finish_struct(self.data)
        self.raw.finish()
        finish_struct(self.group)


# ---------------------------------------------------------------------------------------------------------------------
# Decoding
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


def lines(path):
    """Decoded text lines of a stored gzip file, with the number of decompressed bytes read so far."""
    rest, done = b"", 0
    for chunk in storage.chunks(path, compressed=True):
        done += len(chunk)
        parts = (rest + chunk).split(b"\n")
        rest = parts.pop()
        for part in parts:
            yield part.decode("utf-8", "replace").rstrip("\r"), done
    if rest:
        yield rest.decode("utf-8", "replace").rstrip("\r"), done


def export(data_path, log_path, out_path, info, windows=None, progress=lambda fraction: None):
    """Write the MATLAB file. ``windows`` is None for the whole log, else a list of flight dicts with
    ``takeoff_s``, ``landing_s`` (may be None), ``ac_id``, ``index``, ``takeoff_utc``, ``landing_utc``."""
    if not log_path:
        raise ExportError("This log has no companion .log file with the message protocol, so it cannot be "
                          "exported. Upload the .data together with its .log.")
    log_text = b"".join(storage.chunks(log_path, compressed=True)).decode("utf-8", "replace")
    messages, names = parse_protocol(log_text)
    total = max(int(info.get("size_bytes") or 0), 1)
    w = Writer(out_path)
    warnings, unknown, rejected, versions, other = [], {}, {}, {}, None
    try:
        s = w.struct(w.file, "s")
        if windows is None:
            aircraft_group = {}
            targets = None
        else:
            spans = []
            for win in windows:
                start = win["takeoff_s"] - MARGIN_S
                end = (win["landing_s"] if win["landing_s"] is not None else math.inf) + MARGIN_S
                spans.append(dict(win, start=start, end=end, group=w.element(), aircraft={}))
        rows = 0
        for line, done in lines(data_path):
            rows += 1
            if rows % 20000 == 0:
                progress(min(done / total, 0.99))
            if not line.strip():
                continue
            head = line.split(None, 3)
            try:
                t = float(head[0])
            except (ValueError, IndexError):
                if other is None:
                    other = RawStore(w, w.struct(s, "raw"))
                other.add(math.nan, line)
                continue
            if len(head) < 3 or not head[1].isdigit():
                if other is None:
                    other = RawStore(w, w.struct(s, "raw"))
                other.add(t, line)
                continue
            ac_id, name = int(head[1]), head[2]
            content = head[3] if len(head) > 3 else ""
            definition = messages.get(name)
            if definition is None:
                unknown[name] = unknown.get(name, 0) + 1
            elif name == "AUTOPILOT_VERSION" and ac_id not in versions:
                values = decode(definition, TOKEN.findall(content))
                if values is not None:
                    fv = dict(zip((f["name"] for f in definition["fields"]), values))
                    versions[ac_id] = (fv.get("version"), str(fv.get("desc", "")))
            if windows is None:
                store = aircraft_group.get(ac_id)
                if store is None:
                    store = aircraft_group[ac_id] = AircraftStore(w, w.element(), ac_id, names.get(ac_id), rejected)
                store.add(t, definition, content, line) if definition else store.raw.add(t, line)
            else:
                for span in spans:
                    if span["start"] <= t <= span["end"]:
                        store = span["aircraft"].get(ac_id)
                        if store is None:
                            store = span["aircraft"][ac_id] = AircraftStore(w, w.element(), ac_id, names.get(ac_id),
                                                                            rejected)
                        rel = t - span["takeoff_s"]
                        store.add(rel, definition, content, line) if definition else store.raw.add(rel, line)
        progress(0.99)

        def finish_aircraft(stores):
            for store in stores.values():
                store.finish(versions.get(store.ac_id))
            return [stores[k].group for k in sorted(stores)]

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

        if windows is None:
            w.struct_array(s, "aircrafts", finish_aircraft(aircraft_group))
        else:
            elements = []
            for span in spans:
                g = span["group"]
                w.number(g, "index", span["index"])
                w.number(g, "AC_ID", span["ac_id"])
                w.char(g, "aircraft", names.get(span["ac_id"], ""))
                w.char(g, "takeoff_utc", span.get("takeoff_utc"))
                w.char(g, "landing_utc", span.get("landing_utc"))
                w.number(g, "time_offset_s", span["takeoff_s"])
                w.vector(g, "window_s", [-MARGIN_S, (span["end"] - span["takeoff_s"]) if math.isfinite(span["end"]) else math.inf])
                w.struct_array(g, "aircrafts", finish_aircraft(span["aircraft"]))
                finish_struct(g)
                elements.append(g)
            w.struct_array(s, "flights", [dict((k, e[k]) for k in e.keys()) for e in elements])
        if other is not None:
            other.finish()
            warnings.append(f"{other.count} rows without a valid timestamp or aircraft id are kept as text in s.raw.")
        for name, n in sorted(unknown.items()):
            warnings.append(f"{n} rows of {name}: message not in the embedded protocol; kept as text in raw.")
        for name, n in sorted(rejected.items()):
            warnings.append(f"{n} rows of {name}: fields did not match the protocol; kept as text in raw.")
        w.cellstr(s, "warnings", warnings)
        finish_struct(s)
    except BaseException:
        w.file.close()
        raise
    w.close()
    return warnings
