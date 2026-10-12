"""KML pre-flight validation.

KML is XML, so it inherits XML attacks: XXE (external entities that read local
files / call internal URLs) and "billion laughs" (entity expansion). Before the
file ever reaches GDAL we parse its prolog with `defusedxml`, which refuses any
DTD or entity declaration. Legitimate KML never needs them.
"""

from pathlib import Path

# Only the exception class is imported from the stdlib parser; all parsing goes through defusedxml.
from xml.etree.ElementTree import ParseError  # nosec B405

from defusedxml import DTDForbidden, EntitiesForbidden, ExternalReferenceForbidden
from defusedxml import ElementTree as SafeET

from app.core.errors import InvalidUploadError, UnsafeArchiveError


def validate_kml(path: Path) -> None:
    root_tag: str | None = None
    try:
        with open(path, "rb") as handle:
            # DOCTYPE/ENTITY declarations come before the root element, so reading
            # up to the first start tag is enough to catch every XXE payload.
            for _event, element in SafeET.iterparse(handle, events=("start",), forbid_dtd=True):
                root_tag = element.tag
                break
    except (DTDForbidden, EntitiesForbidden, ExternalReferenceForbidden) as exc:
        raise UnsafeArchiveError("KML files may not contain DTD or entity declarations") from exc
    except ParseError as exc:
        raise InvalidUploadError("File is not well-formed KML/XML") from exc

    if root_tag is None:
        raise InvalidUploadError("KML file is empty")
    if root_tag.rsplit("}", 1)[-1].lower() != "kml":
        raise InvalidUploadError("Root element must be <kml>")
