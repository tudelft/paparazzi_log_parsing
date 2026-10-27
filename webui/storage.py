"""Streaming, lossless storage for uploads."""
import errno
import gzip
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

CHUNK = 1 << 20


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


def save_attachment(stream, folder, limit):
    """Stage a bounded upload, retaining gzip only when it saves at least 5%."""
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
        compressed = len(gzip.compress(sample, compresslevel=1)) < len(sample) * 0.95
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
VIEWABLE = {".pdf": "application/pdf", ".txt": "text/plain", ".csv": "text/plain",
            ".md": "text/plain", ".log": "text/plain", ".png": "image/png",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


def view_type(filename):
    return VIEWABLE.get(Path(filename).suffix.lower())
