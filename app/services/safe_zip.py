"""Hardened extraction of a Shapefile ZIP.

Threats handled (see SECURITY.md):
  * zip-slip / path traversal ("../../etc/cron.d/x", absolute paths, backslashes)
  * zip bombs (entry count, declared size, *actual* bytes written, compression ratio)
  * symlink entries, encrypted entries
  * unexpected payloads: only an allow-list of Shapefile extensions is ever written to disk
"""

import re
import stat
import zipfile
from pathlib import Path, PurePosixPath

from app.core.errors import InvalidUploadError, UnsafeArchiveError

ALLOWED_EXTENSIONS = {".shp", ".shx", ".dbf", ".prj", ".cpg"}
REQUIRED_COMPANIONS = (".shx", ".dbf")
_RATIO_CHECK_MIN_BYTES = 1024 * 1024  # tiny files may legitimately compress very well
_CHUNK = 64 * 1024
_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")


def _validate_member_name(name: str) -> PurePosixPath:
    if "\x00" in name:
        raise UnsafeArchiveError("ZIP contains an entry with an invalid name")
    normalised = name.replace("\\", "/")
    if normalised.startswith("/") or _DRIVE_LETTER.match(normalised):
        raise UnsafeArchiveError("ZIP contains an entry with an absolute path")
    path = PurePosixPath(normalised)
    if ".." in path.parts:
        raise UnsafeArchiveError("ZIP contains an entry that tries to escape the extraction folder")
    return path


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def extract_shapefile_zip(
    zip_path: Path,
    dest: Path,
    *,
    max_entries: int,
    max_total_bytes: int,
    max_ratio: int,
) -> list[Path]:
    """Extract only the Shapefile components and return the list of `.shp` paths."""
    with open(zip_path, "rb") as fh:
        if fh.read(4) != b"PK\x03\x04":
            raise InvalidUploadError("File is not a valid ZIP archive")

    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise InvalidUploadError("File is not a valid ZIP archive") from exc

    dest.mkdir(parents=True, exist_ok=True)
    dest_root = dest.resolve()

    with archive:
        infos = archive.infolist()
        if len(infos) > max_entries:
            raise UnsafeArchiveError(f"ZIP contains too many entries (limit {max_entries})")
        if sum(i.file_size for i in infos) > max_total_bytes:
            raise UnsafeArchiveError("ZIP expands to more than the allowed size")

        written_total = 0
        for info in infos:
            member = _validate_member_name(info.filename)
            if info.is_dir():
                continue
            if _is_symlink(info):
                raise UnsafeArchiveError("ZIP contains a symbolic link")
            if info.flag_bits & 0x1:
                raise UnsafeArchiveError("Encrypted ZIP archives are not supported")
            if (
                info.file_size > _RATIO_CHECK_MIN_BYTES
                and info.compress_size > 0
                and info.file_size / info.compress_size > max_ratio
            ):
                raise UnsafeArchiveError("ZIP compression ratio is suspiciously high")

            # Skip macOS resource forks, hidden files and anything that is not a Shapefile part.
            if "__MACOSX" in member.parts or member.name.startswith("."):
                continue
            suffix = member.suffix.lower()
            if suffix not in ALLOWED_EXTENSIONS:
                continue

            target = (dest / member.parent / f"{member.stem}{suffix}").resolve()
            if not target.is_relative_to(dest_root):  # belt and braces
                raise UnsafeArchiveError("ZIP contains an entry that tries to escape the extraction folder")
            target.parent.mkdir(parents=True, exist_ok=True)

            with archive.open(info) as src, open(target, "wb") as out:
                while chunk := src.read(_CHUNK):
                    written_total += len(chunk)
                    if written_total > max_total_bytes:  # trust bytes written, not the header
                        raise UnsafeArchiveError("ZIP expands to more than the allowed size")
                    out.write(chunk)

    shapefiles = sorted(dest.rglob("*.shp"))
    if not shapefiles:
        raise InvalidUploadError("ZIP archive does not contain a .shp file")

    for shp in shapefiles:
        missing = [ext for ext in REQUIRED_COMPANIONS if not shp.with_suffix(ext).exists()]
        if missing:
            raise InvalidUploadError(
                f"Shapefile '{shp.stem}' is missing required file(s): {', '.join(missing)}"
            )
    return shapefiles
