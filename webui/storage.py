"""Streaming, lossless storage for uploads."""
import gzip
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

CHUNK = 1 << 20


def gzip_file(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=destination.parent, prefix=".compress-")
    try:
        with os.fdopen(fd, "wb") as raw:
            with open(source, "rb") as src, gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=1,
                                                       mtime=0) as dst:
                shutil.copyfileobj(src, dst, CHUNK)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


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
