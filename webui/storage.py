"""Streaming, lossless storage for uploads."""
import errno
import gzip
import hashlib
import os
import shutil
import stat
import tempfile
import time
from pathlib import Path


CHUNK = 1 << 20

# Pauses (seconds) before looking up a stored file again that seemed absent. A network share mounted "soft" can
# briefly report a stored file as absent, or fail the lookup, and find it again a moment later.
STORED_FILE_RETRIES = (0.5, 1.5)
UNAVAILABLE = "The file could not be read from storage just now. Try again in a minute; " \
              "if it keeps failing, contact an administrator."


def stored_file_problem(path):
    """None when the stored file at path is there; else why not, for the log: "not found" or the error of the
    lookup (e.g. a timeout of the share). A file that seems absent is looked up again after STORED_FILE_RETRIES."""
    problem = None
    for pause in (0, *STORED_FILE_RETRIES):
        time.sleep(pause)
        try:
            if stat.S_ISREG(os.stat(path).st_mode):
                return None
            problem = "not a regular file"
        except FileNotFoundError:
            problem = "not found"
        except OSError as exc:
            problem = f"{type(exc).__name__}: {exc}"
    return problem


def ensure_space(folder, required=0, minimum_free=0):
    free = shutil.disk_usage(folder).free
    if free < required + minimum_free:
        raise OSError(errno.ENOSPC, "Insufficient local temporary disk space; "
                      f"need {(required + minimum_free) / (1 << 30):.1f} GiB free.")


def copy_local(source, destination, minimum_free):
    """Bounded streaming copy with periodic local-disk headroom checks."""
    destination = Path(destination)
    with open(destination, "wb") as dst:
        checked = 64 << 20
        while chunk := source.read(CHUNK):
            if checked >= 64 << 20:
                ensure_space(destination.parent, 64 << 20, minimum_free)
                checked = 0
            dst.write(chunk)
            checked += len(chunk)


def gzip_file(source, destination, work_dir=None, minimum_free=0):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if work_dir is not None:
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        ensure_space(work_dir, Path(source).stat().st_size + (1 << 20), minimum_free)
    fd, temporary = tempfile.mkstemp(dir=work_dir or destination.parent, prefix=".compress-")
    shared_temporary = None
    try:
        with os.fdopen(fd, "wb") as raw:
            with open(source, "rb") as src, gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=1,
                                                       mtime=0) as dst:
                shutil.copyfileobj(src, dst, CHUNK)
            raw.flush()
            os.fsync(raw.fileno())
        if work_dir is not None:
            fd, shared_temporary = tempfile.mkstemp(dir=destination.parent, prefix=".upload-")
            with os.fdopen(fd, "wb") as dst, open(temporary, "rb") as src:
                shutil.copyfileobj(src, dst, CHUNK)
                dst.flush()
                os.fsync(dst.fileno())
            os.replace(shared_temporary, destination)
        else:
            os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
        if shared_temporary:
            Path(shared_temporary).unlink(missing_ok=True)


def chunks(path, compressed=False):
    with (gzip.open(path, "rb") if compressed else open(path, "rb")) as source:
        while chunk := source.read(CHUNK):
            yield chunk


def send_stored(path, compressed, mimetype):
    """Response with a stored file. A gzip-stored file goes out as is with Content-Encoding: gzip when the client
    accepts it (browsers decompress while saving, so the transfer is the compressed size); other clients get it
    decompressed. Range requests are only answered for files stored uncompressed."""
    from flask import Response, request, send_file  # only the server needs Flask (matlab_export.py runs without)
    if not compressed:
        return send_file(path, mimetype=mimetype, conditional=True)
    if request.accept_encodings["gzip"]:
        response = send_file(path, mimetype=mimetype, conditional=False, etag=False)
        response.headers["Content-Encoding"] = "gzip"
    else:
        response = Response(chunks(path, compressed=True), mimetype=mimetype)
    response.headers["Vary"] = "Accept-Encoding"
    return response


def save_attachment(stream, folder, limit, compress=True):
    """Stage a bounded upload, retaining gzip only when it saves at least 5% (never with compress=False: video and
    audio stay as they are, so browsers can seek in them with range requests)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=folder, prefix=".upload-")
    raw_path = Path(temporary)
    gz_path = raw_path.with_suffix(".gz")
    digest, size = hashlib.sha256(), 0
    selected = None
    try:
        with os.fdopen(fd, "wb") as dst:
            while chunk := stream.read(CHUNK):
                size += len(chunk)
                if size > limit:
                    raise ValueError(f"File exceeds the {limit / (1 << 30):g} GiB limit.")
                digest.update(chunk)
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        if not size:
            raise ValueError("Empty attachments are not supported.")
        with open(raw_path, "rb") as src:
            sample = src.read(CHUNK)
        compressed = compress and len(gzip.compress(sample, compresslevel=1)) < len(sample) * 0.95
        if compressed:
            gzip_file(raw_path, gz_path)
            compressed = gz_path.stat().st_size < size * 0.95
        selected = gz_path if compressed else raw_path
        return selected, compressed, size, selected.stat().st_size, digest.hexdigest()
    finally:
        # The selected file is owned by the caller on success.
        if selected != raw_path:
            raw_path.unlink(missing_ok=True)
        if selected != gz_path:
            gz_path.unlink(missing_ok=True)


# Types the browser may show inline, chosen by extension only. HTML, SVG, XML and other active
# content are deliberately absent: they stay downloads so a stored file can never run script here.
# Flask adds "charset=utf-8" to text/plain.
# Video and audio play in the browser's own player. MOV (QuickTime) goes out as video/mp4: Chrome and Firefox download
# video/quicktime but play the same file as MP4 (H.264 MOVs from phones and cameras); Safari plays either.
VIEWABLE = {".pdf": "application/pdf", ".txt": "text/plain", ".csv": "text/plain",
            ".md": "text/plain", ".log": "text/plain", ".png": "image/png",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
            ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/mp4", ".webm": "video/webm",
            ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav", ".ogg": "audio/ogg"}


def view_type(filename):
    return VIEWABLE.get(Path(filename).suffix.lower())
