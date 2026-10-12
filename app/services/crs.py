"""CRS helpers: label formatting, user-supplied CRS validation, UTM zone selection."""

import re

from pyproj import CRS
from pyproj.exceptions import CRSError

# Only plain "EPSG:<code>" strings are accepted from clients. Arbitrary PROJ
# strings / WKT could reference grid files or be used to probe the server.
_EPSG_PATTERN = re.compile(r"^EPSG:(\d{4,6})$", re.IGNORECASE)

UPS_NORTH = 32661  # WGS 84 / UPS North (lat >= 84)
UPS_SOUTH = 32761  # WGS 84 / UPS South (lat < -80)


def parse_assume_crs(value: str) -> CRS:
    match = _EPSG_PATTERN.match(value.strip())
    if not match:
        raise ValueError("assume_crs must look like 'EPSG:4326'")
    try:
        return CRS.from_epsg(int(match.group(1)))
    except CRSError as exc:
        raise ValueError(f"Unknown EPSG code: {match.group(1)}") from exc


def crs_label(crs: CRS | None) -> str | None:
    """'EPSG:4326' when the CRS maps to an authority code, otherwise its name."""
    if crs is None:
        return None
    authority = crs.to_authority(min_confidence=70)
    if authority:
        return f"{authority[0]}:{authority[1]}"
    return crs.name


def utm_zone_number(lon: float) -> int:
    zone = int((lon + 180.0) // 6) + 1
    return min(max(zone, 1), 60)  # lon == 180 would otherwise give zone 61


def utm_epsg_for(lon: float, lat: float) -> int:
    """EPSG code of the WGS 84 / UTM zone that contains (lon, lat).

    UTM is only defined between 80°S and 84°N, so the poles fall back to the
    Universal Polar Stereographic projections.
    """
    if lat >= 84.0:
        return UPS_NORTH
    if lat < -80.0:
        return UPS_SOUTH
    base = 32600 if lat >= 0 else 32700
    return base + utm_zone_number(lon)
