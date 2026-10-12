"""Upload intake: filename sanitising, type detection, size-limited streaming."""

import re
from pathlib import Path
from typing import BinaryIO

from app.core.errors import InvalidUploadError, PayloadTooLargeError, UnsupportedFileTypeError

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_CHUNK = 1024 * 1024


def sanitize_filename(raw: str | None) -> str:
    """Return a display-safe base name. It is never used to build a path on disk."""
    if not raw:
        raise InvalidUploadError("Uploaded file has no filename")
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    name = _CONTROL_CHARS.sub("", name).strip()
    if not name or name in {".", ".."}:
        raise InvalidUploadError("Uploaded file has an invalid filename")
    return name[:255]


def detect_file_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".zip":
        return "shapefile"
    if suffix == ".kml":
        return "kml"
    raise UnsupportedFileTypeError("Unsupported file type. Upload a .zip (Shapefile) or a .kml file")


def save_upload(source: BinaryIO, target: Path, max_bytes: int) -> int:
    """Stream the upload to disk, aborting as soon as it exceeds `max_bytes`."""
    total = 0
    with open(target, "wb") as out:
        while chunk := source.read(_CHUNK):
            total += len(chunk)
            if total > max_bytes:
                raise PayloadTooLargeError(
                    f"File exceeds the maximum upload size of {max_bytes // (1024 * 1024)} MB"
                )
            out.write(chunk)
    if total == 0:
        raise InvalidUploadError("Uploaded file is empty")
    return total
