"""Builders for test data. All geometries are constructed in UTM metres first, so
the expected area/length is known exactly and the lon/lat file is derived from it."""

import io
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
from pyproj import Transformer
from shapely.geometry import LineString, Point, Polygon, box

HYD_LON, HYD_LAT = 78.4744, 17.3850  # Hyderabad, UTM zone 44N (EPSG:32644)
UTM = "EPSG:32644"

_to_utm = Transformer.from_crs("EPSG:4326", UTM, always_xy=True)
ORIGIN_E, ORIGIN_N = _to_utm.transform(HYD_LON, HYD_LAT)


def utm_square(side: float = 1000.0) -> Polygon:
    return box(ORIGIN_E, ORIGIN_N, ORIGIN_E + side, ORIGIN_N + side)


def utm_line(length: float = 1000.0) -> LineString:
    return LineString([(ORIGIN_E, ORIGIN_N), (ORIGIN_E + length, ORIGIN_N)])


def to_lonlat(geom):
    return gpd.GeoSeries([geom], crs=UTM).to_crs("EPSG:4326").iloc[0]


def frame(geoms, crs="EPSG:4326", **columns) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(columns or None, geometry=list(geoms), crs=crs)


def shapefile_zip(*frames: tuple[str, gpd.GeoDataFrame], skip_ext: tuple[str, ...] = ()) -> bytes:
    """Zip one Shapefile per (name, frame). `skip_ext` drops files, e.g. ('.prj',)."""
    with tempfile.TemporaryDirectory() as tmp:
        for name, gdf in frames:
            gdf.to_file(Path(tmp) / f"{name}.shp")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(Path(tmp).iterdir()):
                if path.suffix.lower() not in skip_ext:
                    zf.write(path, path.name)
        return buffer.getvalue()


def kml_bytes(placemarks: str, name: str = "Test") -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"<name>{name}</name>{placemarks}</Document></kml>"
    ).encode()


def coords_kml(geom) -> str:
    """'lon,lat,0 lon,lat,0 ...' for a shapely Polygon ring / LineString / Point."""
    if isinstance(geom, Polygon):
        pts = geom.exterior.coords
    elif isinstance(geom, Point):
        pts = geom.coords
    else:
        pts = geom.coords
    return " ".join(f"{x},{y},0" for x, y, *_ in pts)


def kml_polygon(poly_ll: Polygon, name="Plot") -> str:
    return (
        f"<Placemark><name>{name}</name><Polygon><outerBoundaryIs><LinearRing>"
        f"<coordinates>{coords_kml(poly_ll)}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
    )


def kml_line(line_ll: LineString, name="Road") -> str:
    return (
        f"<Placemark><name>{name}</name><LineString>"
        f"<coordinates>{coords_kml(line_ll)}</coordinates></LineString></Placemark>"
    )


def kml_point(pt_ll: Point, name="Well") -> str:
    return (
        f"<Placemark><name>{name}</name><Point>"
        f"<coordinates>{coords_kml(pt_ll)}</coordinates></Point></Placemark>"
    )
