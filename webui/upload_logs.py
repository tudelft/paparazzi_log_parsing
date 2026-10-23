#!/usr/bin/env python3
"""Upload Paparazzi .data/.log pairs from a folder to the log database through its API.

    python3 upload_logs.py ~/paparazzi/var/logs --server https://logs.example.org --key logdb_...

Create the key on your account page (click your name at the top right). The folder is searched recursively. A pair is
only uploaded if the SHA-256 of its .data file is not on the server yet (or if the server has the .data without a .log).
Needs only the Python standard library.
"""
import argparse
import hashlib
import http.client
import json
import math
import os
import ssl
import sys
import time
import uuid
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

CHUNK = 1 << 20
TRANSIENT = {408, 429, 500, 502, 503, 504}


class HTTPError(RuntimeError):
    def __init__(self, status, message, retry_after=None):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.retry_after = retry_after


def retry_delay(header, fallback):
    if header:
        try:
            seconds = float(header)
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                return fallback
        if math.isfinite(seconds):
            return max(0, seconds)
    return fallback


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(CHUNK):
            h.update(block)
    return h.hexdigest()


def find_pairs(root):
    """Yield (relative name, data path, log path) for every .data with a .log of the same name in the same folder."""
    root = os.path.abspath(root)
    missing = []

    def scan_error(error):
        raise error

    for folder, _, files in os.walk(root, onerror=scan_error):
        by_stem = {}
        for f in files:
            stem, ext = os.path.splitext(f)
            if ext.lower() in (".data", ".log"):
                by_stem.setdefault(stem, {})[ext.lower()] = os.path.join(folder, f)
        for stem, parts in sorted(by_stem.items()):
            if ".data" not in parts:
                continue
            if ".log" not in parts:
                missing.append(parts[".data"])
                continue
            yield os.path.relpath(parts[".data"], root), parts[".data"], parts[".log"]
    for m in missing:
        print(f"skip (no .log next to it): {m}", file=sys.stderr)


class Resume:
    def __init__(self, folder, server, user):
        scope = [os.path.realpath(folder), server.rstrip("/"), user]
        tag = hashlib.sha256(json.dumps(scope).encode()).hexdigest()[:20]
        self.path = Path(__file__).resolve().with_name(f".upload_logs-resume-{tag}.json")
        self.entries = {}
        if self.path.exists():
            saved = json.loads(self.path.read_text())
            if (not isinstance(saved, dict) or saved.get("version") != 1
                    or saved.get("scope") != scope or not isinstance(saved.get("entries"), dict)):
                raise RuntimeError(f"Invalid resume file: {self.path}; move it aside or use --no-resume.")
            self.entries = saved["entries"]
        self.scope = scope
        for entry in self.entries.values():
            if (not isinstance(entry, dict) or not isinstance(entry.get("signature"), list)
                    or ("digest" in entry and (not isinstance(entry["digest"], str)
                        or len(entry["digest"]) != 64
                        or any(c not in "0123456789abcdef" for c in entry["digest"])))
                    or ("ingest_id" in entry and type(entry["ingest_id"]) is not int)):
                raise RuntimeError(f"Invalid resume entries: {self.path}; move it aside or use --no-resume.")

    def save(self):
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, prefix=self.path.name,
                                             suffix=".tmp", delete=False) as handle:
                temp = Path(handle.name)
                json.dump({"version": 1, "scope": self.scope, "entries": self.entries}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)

    def entry(self, pair):
        signature = []
        for path in pair[1:]:
            stat = os.stat(path)
            signature.append([stat.st_size, stat.st_mtime_ns])
        entry = self.entries.get(pair[0])
        if not isinstance(entry, dict) or entry.get("signature") != signature:
            entry = {"signature": signature}
            self.entries[pair[0]] = entry
        return entry

    def digest(self, pair):
        entry = self.entry(pair)
        if "digest" not in entry:
            before = entry["signature"]
            digest = sha256_file(pair[1])
            if self.entry(pair)["signature"] != before:
                raise RuntimeError(f"Files changed while hashing {pair[0]}; retry when the files are stable.")
            entry["digest"] = digest
            self.save()
        else:
            print(f"Resuming: cached hash for {pair[0]}", flush=True)
        return entry["digest"]

    def accepted(self, pair, ingest_id):
        self.entry(pair)["ingest_id"] = ingest_id
        self.save()

    def completed(self, name):
        self.entries[name].pop("ingest_id", None)
        self.save()


def hash_batches(pairs, max_files, max_bytes, resume=None):
    batch, size = [], 0
    for pair in pairs:
        data_size = os.path.getsize(pair[1])
        if batch and (len(batch) >= max_files or size + data_size > max_bytes):
            yield batch
            batch, size = [], 0
        print(f"hashing {pair[0]} ({data_size / 1e6:.1f} MB) ...", flush=True)
        batch.append((pair, resume.digest(pair) if resume else sha256_file(pair[1])))
        size += data_size
        if len(batch) >= max_files or size >= max_bytes:
            yield batch
            batch, size = [], 0
    if batch:
        yield batch


class Progress:
    def __init__(self, total, bulk=False, dry_run=False):
        self.total = total
        self.bulk = bulk
        self.dry_run = dry_run
        self.checked = self.skipped = self.accepted = self.processed = self.failed = self.planned = 0
        self.errors = 0
        self.last_report = None

    def report(self):
        done = self.skipped + self.failed + (self.planned if self.dry_run else
                                           self.processed if self.bulk else self.accepted)
        percent = 100 * done / self.total if self.total else 100
        action = (f"{self.planned} planned" if self.dry_run else
                  f"{self.accepted} accepted, {self.processed} processed" if self.bulk else
                  f"{self.accepted} accepted")
        line = (f"Progress: {done}/{self.total} ({percent:.1f}%) | {self.total - done} remaining | "
                f"{self.checked} checked | {self.skipped} skipped | {action} | {self.failed} failed")
        if self.errors:
            line += f" | {self.errors} discovery/hash/check errors"
        if line != self.last_report:
            print(line, flush=True)
            self.last_report = line


def missing_pairs(client, pairs, max_files, max_bytes, progress=None, resume=None):
    seen = set()
    for number, batch in enumerate(hash_batches(pairs, max_files, max_bytes, resume), 1):
        known = client.check([digest for _, digest in batch])
        todo = []
        existing = duplicates = 0
        for pair, digest in batch:
            if digest in seen:
                duplicates += 1
            elif digest in known and known[digest]["has_log"]:
                existing += 1
            else:
                todo.append(pair)
            seen.add(digest)
        print(f"Batch {number}: {len(batch)} pairs checked, {existing} already on the server, "
              f"{duplicates} repeated, {len(todo)} to upload", flush=True)
        if progress is not None:
            progress.checked += len(batch)
            progress.skipped += existing + duplicates
            progress.report()
        yield from todo


class Client:
    def __init__(self, server, key, insecure=False, timeout=600, retries=3):
        u = urlsplit(server if "//" in server else "//" + server, scheme="http")
        self.https = u.scheme == "https"
        self.host, self.port = u.hostname, u.port or (443 if self.https else 80)
        self.prefix = u.path.rstrip("/")
        self.key, self.timeout = key, timeout
        self.retries = retries
        self.ctx = ssl._create_unverified_context() if insecure else None

    def _conn(self):
        if self.https:
            return http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout, context=self.ctx)
        return http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)

    def _request(self, method, path, body=None, headers=None):
        for attempt in range(self.retries + 1):
            try:
                return self._request_once(method, path, body() if callable(body) else body, headers)
            except (OSError, http.client.HTTPException, HTTPError) as exc:
                if isinstance(exc, ssl.SSLCertVerificationError):
                    raise
                if isinstance(exc, HTTPError) and exc.status not in TRANSIENT:
                    raise
                if attempt == self.retries:
                    raise
                delay = retry_delay(exc.retry_after if isinstance(exc, HTTPError) else None,
                                    min(60, 2 ** attempt))
                print(f"\nTemporary request failure: {exc}; retrying in {delay:g}s.", file=sys.stderr)
                time.sleep(delay)

    def _request_once(self, method, path, body=None, headers=None):
        h = {"Authorization": f"Bearer {self.key}", **(headers or {})}
        c = self._conn()
        try:
            c.request(method, self.prefix + path, body=body, headers=h)
            r = c.getresponse()
            raw = r.read()
        finally:
            c.close()
        try:
            data = json.loads(raw)
        except ValueError as exc:
            if r.status < 400:
                raise RuntimeError(f"Expected JSON from server, received HTTP {r.status}.") from exc
            data = {"error": raw[:200].decode("utf-8", "replace")}
        if r.status >= 400:
            raise HTTPError(r.status, data.get("error", data) if isinstance(data, dict) else data,
                            r.getheader("Retry-After"))
        if not isinstance(data, dict):
            raise RuntimeError("Server returned an invalid JSON response.")
        return data

    def check(self, hashes):
        known = {}
        for i in range(0, len(hashes), 1000):
            body = json.dumps({"sha256": hashes[i:i + 1000]}).encode()
            known.update(self._request("POST", "/api/logs/check", body, {"Content-Type": "application/json"})["known"])
        return known

    def upload(self, files, reservation=None):
        """files: list of (relative name, path). Streams the files instead of loading them into memory."""
        boundary = uuid.uuid4().hex
        parts = []  # bytes or file paths, in order
        for name, _ in files:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="names"\r\n\r\n{name}\r\n'.encode())
        for name, path in files:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{os.path.basename(path)}"'
                         "\r\nContent-Type: application/octet-stream\r\n\r\n".encode())
            parts.append(path)
            parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        length = sum(len(p) if isinstance(p, bytes) else os.path.getsize(p) for p in parts)

        def body():
            for p in parts:
                if isinstance(p, bytes):
                    yield p
                else:
                    with open(p, "rb") as f:
                        while block := f.read(CHUNK):
                            yield block

        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(length)}
        if reservation:
            headers["X-Bulk-Reservation"] = reservation
        return self._request("POST", "/api/upload", body, headers)


def wait_capacity(client, pair_bytes, max_pending, max_bytes, poll, max_wait):
    started = time.monotonic()
    while True:
        status = client._request("GET", "/api/status")
        backlog = status.get("backlog")
        if (not isinstance(backlog, dict) or any(
                type(backlog.get(key)) is not int or backlog[key] < 0 for key in ("count", "bytes"))):
            raise RuntimeError("Server lacks bulk backlog information; update the server before using --bulk.")
        count, size = backlog["count"], backlog["bytes"]
        if count == 0 or count < max_pending and size + pair_bytes <= max_bytes:
            return
        if time.monotonic() - started >= max_wait:
            raise RuntimeError("Timed out waiting for server capacity; rerun the script to resume.")
        print(f"Waiting: server has {count} pending jobs ({size / (1 << 30):.2f} GiB estimated).", flush=True)
        time.sleep(poll)


def wait_result(client, ingest_id, poll, max_wait):
    started = time.monotonic()
    last = None
    while True:
        result = client._request("GET", f"/api/ingest/{ingest_id}")
        state, log_state = result.get("status"), result.get("log_status")
        if state in ("error", "rejected"):
            raise RuntimeError(result.get("error") or f"Upload {state}.")
        if state in ("stored", "duplicate"):
            if not result.get("log_id"):
                raise RuntimeError(result.get("error") or "Upload has no stored log.")
            if log_state == "done":
                return
            if log_state == "error":
                raise RuntimeError("Parsing failed: " + (result.get("log_error") or "unknown error"))
            if log_state is None:
                raise RuntimeError("Stored log was deleted before completion.")
        if state not in ("accepted", "hashing", "stored", "duplicate"):
            raise RuntimeError(f"Unexpected upload status: {state!r}")
        if time.monotonic() - started >= max_wait:
            raise RuntimeError("Timed out waiting for processing; the server may still be working.")
        current = (state, log_state)
        if current != last:
            print(f"Upload {ingest_id}: {state}" + (f", parser {log_state}" if log_state else ""), flush=True)
            last = current
        time.sleep(poll)


def reserve_capacity(client, pair_bytes, poll, max_wait):
    token = uuid.uuid4().hex
    body = json.dumps(dict(token=token, size_bytes=pair_bytes)).encode()
    started = time.monotonic()
    while True:
        try:
            result = client._request("POST", "/api/upload-reservations", body, {"Content-Type": "application/json"})
            if result.get("token") != token:
                raise RuntimeError("Invalid bulk reservation response.")
            return token
        except HTTPError as exc:
            if exc.status != 429:
                raise
            if time.monotonic() - started >= max_wait:
                raise RuntimeError("Timed out waiting for a shared bulk upload slot.") from exc
            delay = max(poll, retry_delay(exc.retry_after, poll))
            print(f"Other importers are using the bulk budget; waiting {delay:g}s.", flush=True)
            time.sleep(delay)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="folder to search recursively for .data/.log pairs")
    ap.add_argument("--server", default=os.environ.get("LOGDB_SERVER"), help="server URL (or env LOGDB_SERVER)")
    ap.add_argument("--key", default=os.environ.get("LOGDB_API_KEY"), help="API key (or env LOGDB_API_KEY)")
    ap.add_argument("-n", "--dry-run", action="store_true", help="only list what would be uploaded")
    ap.add_argument("--insecure", action="store_true", help="do not verify the https certificate")
    ap.add_argument("--bulk", action="store_true", help="throttle initial imports and wait for processing results")
    ap.add_argument("--no-resume", action="store_true", help="ignore and do not write local resume checkpoints")
    ap.add_argument("--max-pending", type=int, default=8, help="bulk server backlog count threshold (default: 8)")
    ap.add_argument("--max-pending-gib", type=float, default=16, help="bulk estimated backlog GiB threshold (default: 16)")
    ap.add_argument("--poll-seconds", type=float, default=5, help="bulk polling interval (default: 5)")
    ap.add_argument("--max-wait-seconds", type=float, default=3600, help="maximum wait per bulk stage (default: 3600)")
    ap.add_argument("--retries", type=int, default=3, help="retries per transient request failure (default: 3)")
    ap.add_argument("--hash-batch-files", type=int, default=32, help="maximum files hashed before checking/uploading (default: 32)")
    ap.add_argument("--hash-batch-mib", type=float, default=256, help="maximum .data MiB per hashing batch; larger files run alone (default: 256)")
    a = ap.parse_args()
    if not a.server or not a.key:
        ap.error("--server and --key (or LOGDB_SERVER / LOGDB_API_KEY) are required")
    if not os.path.isdir(a.folder):
        ap.error(f"not a folder: {a.folder}")
    if a.max_pending <= 0 or a.hash_batch_files <= 0 or a.retries < 0 or any(not math.isfinite(v) or v <= 0 for v in
            (a.max_pending_gib, a.poll_seconds, a.max_wait_seconds, a.hash_batch_mib)):
        ap.error("queue limits and wait intervals must be positive; retries must be nonnegative")

    client = Client(a.server, a.key, a.insecure, retries=a.retries)
    try:
        who = client._request("GET", "/api/me")["user"]
    except (RuntimeError, OSError, http.client.HTTPException) as e:
        print(f"cannot log in to the server: {e}", file=sys.stderr)
        return 1
    print(f"logged in as {who['display_name']}")
    print("Scanning folders for .data/.log pairs (file contents are not read) ...", flush=True)
    try:
        pairs = []
        for pair in find_pairs(a.folder):
            pairs.append(pair)
            if len(pairs) % 1000 == 0:
                print(f"Scanning: {len(pairs)} pairs found ...", flush=True)
    except OSError as e:
        print(f"FAILED scanning folders: {e}", file=sys.stderr)
        return 1
    print(f"Found {len(pairs)} pairs. Hashing and uploading in batches.", flush=True)
    progress = Progress(len(pairs), a.bulk, a.dry_run)
    resume = None
    if a.bulk and not a.no_resume and not a.dry_run:
        try:
            resume = Resume(a.folder, a.server, who.get("id", who["display_name"]))
            # Check writability before accepting any uploads.
            resume.save()
            print(f"Resume file: {resume.path}", flush=True)
            for pair in pairs:
                entry = resume.entry(pair)
                if "ingest_id" not in entry:
                    continue
                print(f"Resuming processing check: {pair[0]} (upload {entry['ingest_id']})", flush=True)
                try:
                    wait_result(client, entry["ingest_id"], a.poll_seconds, a.max_wait_seconds)
                except (RuntimeError, OSError, http.client.HTTPException) as exc:
                    if not isinstance(exc, HTTPError) or exc.status != 404:
                        print(f"Previous upload could not be confirmed: {exc}; checkpoint retained. "
                              "Retry later or use --no-resume for a fresh server check.", file=sys.stderr)
                        return 1
                    print(f"Previous receipt no longer exists: {exc}; checking file hash again.", file=sys.stderr)
                resume.completed(pair[0])
        except (RuntimeError, OSError, ValueError) as exc:
            print(f"FAILED loading/saving resume checkpoint: {exc}", file=sys.stderr)
            return 1
    progress.report()
    todo = iter(missing_pairs(client, iter(pairs), a.hash_batch_files,
                              a.hash_batch_mib * (1 << 20), progress, resume))

    failed = 0
    pending = []
    i = 0
    while True:
        try:
            name, data, log = next(todo)
            size_mb = os.path.getsize(data) / 1e6
        except StopIteration:
            break
        except (RuntimeError, OSError, http.client.HTTPException) as e:
            failed += 1
            progress.errors += 1
            print(f"FAILED discovering, hashing or checking logs: {e}", file=sys.stderr)
            break
        i += 1
        if a.dry_run:
            print(f"[{i}] would upload {name} ({size_mb:.1f} MB)")
            progress.planned += 1
            progress.report()
            continue
        print(f"[{i}] uploading {name} ({size_mb:.1f} MB) ...", end=" ", flush=True)
        try:
            reservation = None
            if a.bulk:
                pair_bytes = os.path.getsize(data) + os.path.getsize(log)
                wait_capacity(client, pair_bytes, a.max_pending,
                              a.max_pending_gib * (1 << 30), a.poll_seconds, a.max_wait_seconds)
                reservation = reserve_capacity(client, pair_bytes, a.poll_seconds, a.max_wait_seconds)
            res = client.upload([(name, data), (os.path.splitext(name)[0] + ".log", log)], reservation=reservation)
            results = res.get("results")
            if not isinstance(results, list) or len(results) != 1:
                raise RuntimeError("Server returned an invalid upload receipt.")
            receipt = results[0]
            if not isinstance(receipt, dict):
                raise RuntimeError("Server returned an invalid upload receipt.")
            if receipt.get("status") != "accepted" or type(receipt.get("ingest_id")) is not int:
                raise RuntimeError(receipt.get("reason") or f"Upload not accepted: {receipt}")
            print(f"accepted (upload {receipt['ingest_id']})")
            if resume:
                resume.accepted((name, data, log), receipt["ingest_id"])
            progress.accepted += 1
            progress.report()
            if a.bulk:
                pending.append((name, receipt["ingest_id"]))
                if len(pending) >= a.max_pending:
                    first_name, first_id = pending.pop(0)
                    try:
                        wait_result(client, first_id, a.poll_seconds, a.max_wait_seconds)
                        if resume:
                            resume.completed(first_name)
                        print(f"Processed: {first_name}")
                        progress.processed += 1
                    except (RuntimeError, OSError, http.client.HTTPException) as e:
                        failed += 1
                        print(f"FAILED processing {first_name}: {e}")
                        progress.failed += 1
                    progress.report()
        except (RuntimeError, OSError, http.client.HTTPException) as e:
            failed += 1
            print(f"FAILED: {e}")
            progress.failed += 1
            progress.report()
            if a.bulk:
                print("Stopping bulk submission after a failure; checking previously accepted uploads.")
                break
    for name, ingest_id in pending:
        try:
            wait_result(client, ingest_id, a.poll_seconds, a.max_wait_seconds)
            if resume:
                resume.completed(name)
            print(f"Processed: {name}")
            progress.processed += 1
        except (RuntimeError, OSError, http.client.HTTPException) as e:
            failed += 1
            print(f"FAILED processing {name}: {e}")
            progress.failed += 1
        progress.report()
    progress.report()
    if not a.dry_run:
        print(f"Finished: {failed} failure(s)." + (
            " Accepted bulk uploads checked through parsing." if a.bulk else
            " Uploads were accepted only; storage and parsing continue on the server."))
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped. Any saved bulk checkpoint will be reused on the next run.", file=sys.stderr)
        sys.exit(130)
