"""Measurement engine.

Why this module exists: latitude/longitude are angles, not metres. Calling
`.area` on an EPSG:4326 polygon returns "square degrees", which is meaningless.
So for every feature we

  1. normalise the geometry to WGS 84 (lon/lat),
  2. pick the UTM zone that contains the feature's centroid,
  3. re-project into that zone (units = metres) and measure there,
  4. also compute the ellipsoidal (geodesic) result as an independent check.

Anything we cannot measure is reported with a status + note instead of raising.
"""

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
import shapely
from pyproj import CRS, Geod, Transformer
from shapely.geometry.base import BaseGeometry

from app.models import MeasureStatus
from app.services.crs import crs_label, utm_epsg_for, utm_zone_number

WGS84 = CRS.from_epsg(4326)
_GEOD = Geod(ellps="WGS84")
_COLLECTIONS = {"MultiPolygon", "MultiLineString", "MultiPoint", "GeometryCollection"}


@dataclass(frozen=True)
class Measurement:
    status: MeasureStatus
    area_sq_m: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    geodesic_area_sq_m: float | None = None
    geodesic_length_m: float | None = None
    projected_crs: str | None = None
    note: str | None = None


def _flatten(geom: BaseGeometry) -> Iterator[BaseGeometry]:
    """Yield the simple (non-Multi, non-Collection) parts of a geometry."""
    if geom.geom_type in _COLLECTIONS:
        for part in geom.geoms:
            yield from _flatten(part)
    else:
        yield geom


def _reproject(geom: BaseGeometry, transformer: Transformer) -> BaseGeometry:
    """Apply a pyproj Transformer to every vertex of a (2D) geometry."""

    def _apply(coords: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack((x, y))

    return shapely.transform(geom, _apply)


def _repair(polygon: BaseGeometry) -> list[BaseGeometry]:
    fixed = shapely.make_valid(polygon)
    return [p for p in _flatten(fixed) if p.geom_type == "Polygon" and not p.is_empty]


def _geodesic_polygon_area(polygon: BaseGeometry) -> float:
    xs, ys = polygon.exterior.xy
    area = abs(_GEOD.polygon_area_perimeter(list(xs), list(ys))[0])
    for ring in polygon.interiors:
        rx, ry = ring.xy
        area -= abs(_GEOD.polygon_area_perimeter(list(rx), list(ry))[0])
    return area


class GeometryMeasurer:
    """Measures geometries that all share one source CRS (i.e. one layer)."""

    def __init__(self, source_crs: CRS):
        self.source_crs = source_crs
        self._identity = source_crs.equals(WGS84) or source_crs.to_epsg() == 4326
        self._to_wgs84 = None if self._identity else Transformer.from_crs(source_crs, WGS84, always_xy=True)
        self._utm: dict[int, Transformer] = {}

    # ------------------------------------------------------------------ helpers
    def _wgs84(self, geom: BaseGeometry) -> BaseGeometry:
        return geom if self._to_wgs84 is None else _reproject(geom, self._to_wgs84)

    def _utm_transformer(self, epsg: int) -> Transformer:
        if epsg not in self._utm:
            self._utm[epsg] = Transformer.from_crs(WGS84, CRS.from_epsg(epsg), always_xy=True)
        return self._utm[epsg]

    @staticmethod
    def _unsupported(note: str) -> Measurement:
        return Measurement(status=MeasureStatus.UNSUPPORTED, note=note)

    # ------------------------------------------------------------------ public
    def measure(self, geom: BaseGeometry | None) -> Measurement:
        if geom is None or geom.is_empty:
            return self._unsupported("Empty or missing geometry")

        geom = shapely.force_2d(geom)  # KML often carries an altitude (Z) value
        parts = list(_flatten(geom))
        polygons = [p for p in parts if p.geom_type == "Polygon" and not p.is_empty]
        lines = [p for p in parts if p.geom_type in ("LineString", "LinearRing") and not p.is_empty]

        if not polygons and not lines:
            if parts and all(p.geom_type == "Point" for p in parts):
                return Measurement(
                    status=MeasureStatus.NOT_APPLICABLE,
                    note="Point geometries have no area or length",
                )
            return self._unsupported(f"Geometry type '{geom.geom_type}' is not supported for measurement")

        notes: list[str] = []

        # Self-intersecting polygons give nonsense areas; repair them first.
        repaired: list[BaseGeometry] = []
        for polygon in polygons:
            if polygon.is_valid:
                repaired.append(polygon)
            else:
                repaired.extend(_repair(polygon))
                notes.append("Invalid polygon geometry was repaired before measuring")
        polygons = repaired
        if not polygons and not lines:
            return self._unsupported("Geometry is invalid and could not be repaired")

        try:
            polygons_ll = [self._wgs84(p) for p in polygons]
            lines_ll = [self._wgs84(line) for line in lines]
        except Exception:  # noqa: BLE001 - any PROJ failure means "cannot measure"
            return self._unsupported("Coordinate transformation to WGS 84 failed")

        all_ll = shapely.GeometryCollection(polygons_ll + lines_ll)
        coords = shapely.get_coordinates(all_ll)
        if coords.size == 0 or not np.isfinite(coords).all():
            return self._unsupported("Coordinates could not be transformed (outside the CRS area of use?)")
        if (np.abs(coords[:, 0]) > 180.0 + 1e-6).any() or (np.abs(coords[:, 1]) > 90.0 + 1e-6).any():
            return self._unsupported("Coordinates fall outside valid longitude/latitude ranges")

        centroid = all_ll.centroid
        epsg = utm_epsg_for(centroid.x, centroid.y)
        minx, _, maxx, _ = all_ll.bounds
        if epsg not in (32661, 32761) and utm_zone_number(minx) != utm_zone_number(maxx):
            notes.append(
                "Feature spans several UTM zones; the projected value is approximate, "
                "prefer the geodesic value"
            )

        utm = self._utm_transformer(epsg)
        polygons_m = [_reproject(p, utm) for p in polygons_ll]
        lines_m = [_reproject(line, utm) for line in lines_ll]

        area = perimeter = length = geo_area = geo_length = None
        if polygons_m:
            area = sum(p.area for p in polygons_m)
            perimeter = sum(p.length for p in polygons_m)
            geo_area = sum(_geodesic_polygon_area(p) for p in polygons_ll)
        if lines_m:
            length = sum(line.length for line in lines_m)
            geo_length = sum(_GEOD.line_length(*[list(c) for c in line.xy]) for line in lines_ll)

        return Measurement(
            status=MeasureStatus.MEASURED,
            area_sq_m=_r(area),
            perimeter_m=_r(perimeter),
            length_m=_r(length),
            geodesic_area_sq_m=_r(geo_area),
            geodesic_length_m=_r(geo_length),
            projected_crs=crs_label(CRS.from_epsg(epsg)),
            note="; ".join(dict.fromkeys(notes)) or None,
        )


def _r(value: float | None) -> float | None:
    return None if value is None else round(float(value), 4)
