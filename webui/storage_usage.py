"""Disk use for the Statistics page (admins): what the stored logs, flight paths, attachments and qualification
evidence take on disk, next to the database file and the free space.

Walking the log storage can take a while on a network share, so it runs in a background thread at most every
SCAN_HOURS and the page shows the latest result with its time. Sizes that the database already knows (original
upload sizes) and the live ones (database file, free space) are added at request time.
"""
import os
import shutil
import threading
import time
from pathlib import Path

SCAN_HOURS = 6
_state = {"result": None, "measured_at": None, "running": False, "error": None}
_lock = threading.Lock()


def _walk(root):
    """{category: [bytes, files]} under the log storage: logs, flight paths, attachments (with their PDF previews),
    qualification evidence, shared documents (Documents page), uploads waiting to be stored and other files."""
    totals = {}
    root = Path(root)

    def add(kind, size):
        t = totals.setdefault(kind, [0, 0])
        t[0] += size
        t[1] += 1

    stack = [(root, None)]
    while stack:
        folder, kind = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    sub = kind
                    if folder == root:
                        sub = {"qualification-evidence": "evidence", ".incoming": "waiting", "attachments": "attachments",
                               "previews": "attachments", "documents": "documents"}.get(e.name, kind)
                    stack.append((Path(e.path), sub))
                elif e.is_file(follow_symlinks=False):
                    size = e.stat(follow_symlinks=False).st_size
                    if kind:
                        add(kind, size)
                    elif e.name.startswith("paths-"):
                        add("paths", size)
                    elif folder == root:
                        add("other", size)
                    else:
                        add("logs", size)  # the .data and .log files in the per-log folders
            except OSError:
                continue
    return totals


def _scan(root):
    started = time.time()
    try:
        result = _walk(root)
        error = None
    except Exception as exc:  # a broken share must not take the page down
        result, error = None, str(exc)
    with _lock:
        if result is not None:
            _state["result"], _state["measured_at"] = result, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            _state["seconds"] = round(time.time() - started, 1)
        _state["error"], _state["running"] = error, False


def scanned(root, refresh=False):
    """The latest scan (None until the first one finishes); starts a new one in the background when it is older
    than SCAN_HOURS or refresh is asked."""
    with _lock:
        stale = _state["measured_at"] is None or time.time() - _state.get("finished", 0) > SCAN_HOURS * 3600
        if (stale or refresh) and not _state["running"]:
            _state["running"], _state["finished"] = True, time.time()
            threading.Thread(target=_scan, args=(root,), daemon=True, name="storage-usage").start()
        return dict(_state)


def disk(path):
    try:
        u = shutil.disk_usage(path)
        return {"total": u.total, "free": u.free}
    except OSError:
        return None


def file_size(*paths):
    return sum(os.path.getsize(p) for p in paths if os.path.isfile(p))
