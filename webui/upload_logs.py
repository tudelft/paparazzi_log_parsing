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
import os
import ssl
import sys
import uuid
from urllib.parse import urlsplit

CHUNK = 1 << 20


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
    for folder, _, files in os.walk(root):
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


class Client:
    def __init__(self, server, key, insecure=False, timeout=600):
        u = urlsplit(server if "//" in server else "//" + server, scheme="http")
        self.https = u.scheme == "https"
        self.host, self.port = u.hostname, u.port or (443 if self.https else 80)
        self.prefix = u.path.rstrip("/")
        self.key, self.timeout = key, timeout
        self.ctx = ssl._create_unverified_context() if insecure else None

    def _conn(self):
        if self.https:
            return http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout, context=self.ctx)
        return http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)

    def _request(self, method, path, body=None, headers=None):
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
        except ValueError:
            data = {"error": raw[:200].decode("utf-8", "replace")}
        if r.status >= 400:
            raise RuntimeError(f"HTTP {r.status}: {data.get('error', data)}")
        return data

    def check(self, hashes):
        known = {}
        for i in range(0, len(hashes), 1000):
            body = json.dumps({"sha256": hashes[i:i + 1000]}).encode()
            known.update(self._request("POST", "/api/logs/check", body, {"Content-Type": "application/json"})["known"])
        return known

    def upload(self, files):
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

        return self._request("POST", "/api/upload", body(), {
            "Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(length)})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="folder to search recursively for .data/.log pairs")
    ap.add_argument("--server", default=os.environ.get("LOGDB_SERVER"), help="server URL (or env LOGDB_SERVER)")
    ap.add_argument("--key", default=os.environ.get("LOGDB_API_KEY"), help="API key (or env LOGDB_API_KEY)")
    ap.add_argument("-n", "--dry-run", action="store_true", help="only list what would be uploaded")
    ap.add_argument("--insecure", action="store_true", help="do not verify the https certificate")
    a = ap.parse_args()
    if not a.server or not a.key:
        ap.error("--server and --key (or LOGDB_SERVER / LOGDB_API_KEY) are required")
    if not os.path.isdir(a.folder):
        ap.error(f"not a folder: {a.folder}")

    client = Client(a.server, a.key, a.insecure)
    try:
        who = client._request("GET", "/api/me")["user"]
    except (RuntimeError, OSError) as e:
        print(f"cannot log in to the server: {e}", file=sys.stderr)
        return 1
    print(f"logged in as {who['display_name']}")
    pairs = list(find_pairs(a.folder))
    print(f"{len(pairs)} .data/.log pairs found")
    if not pairs:
        return 0

    print("hashing ...")
    hashes = []
    for i, (name, data, _) in enumerate(pairs, 1):
        hashes.append(sha256_file(data))
        print(f"\r  {i}/{len(pairs)}", end="", flush=True)
    print()

    try:
        known = client.check(hashes)
    except (RuntimeError, OSError) as e:
        print(f"cannot reach the server: {e}", file=sys.stderr)
        return 1

    need = [(p, h) for p, h in zip(pairs, hashes) if h not in known or not known[h]["has_log"]]
    unique = {}
    for p, h in need:  # identical .data content twice in the folder: upload once
        unique.setdefault(h, p)
    todo = list(unique.values())
    print(f"{len(pairs) - len(need)} already on the server, {len(todo)} to upload")

    failed = 0
    for i, (name, data, log) in enumerate(todo, 1):
        size_mb = os.path.getsize(data) / 1e6
        if a.dry_run:
            print(f"[{i}/{len(todo)}] would upload {name} ({size_mb:.1f} MB)")
            continue
        print(f"[{i}/{len(todo)}] uploading {name} ({size_mb:.1f} MB) ...", end=" ", flush=True)
        try:
            res = client.upload([(name, data), (os.path.splitext(name)[0] + ".log", log)])
            print(", ".join(r.get("status", "?") + (f" ({r['reason']})" if r.get("reason") else "") for r in res["results"]))
        except (RuntimeError, OSError) as e:
            failed += 1
            print(f"FAILED: {e}")
    if not a.dry_run:
        print(f"done, {len(todo) - failed} uploaded, {failed} failed. Parsing continues on the server.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
