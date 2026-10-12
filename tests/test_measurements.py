import pytest
from pyproj import CRS
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPolygon,
    Point,
    Polygon,
    box,
)

from app.models import MeasureStatus
from app.services.measurements import WGS84, GeometryMeasurer
from tests.helpers import ORIGIN_E, ORIGIN_N, to_lonlat, utm_line, utm_square


@pytest.fixture
def measurer():
    return GeometryMeasurer(WGS84)


def test_polygon_area_is_in_square_metres_not_degrees(measurer):
    result = measurer.measure(to_lonlat(utm_square(1000)))
    assert result.status == MeasureStatus.MEASURED
    assert result.area_sq_m == pytest.approx(1_000_000, rel=1e-6)
    assert result.perimeter_m == pytest.approx(4_000, rel=1e-6)
    assert result.projected_crs == "EPSG:32644"
    assert result.length_m is None


def test_geodesic_value_agrees_with_projected_within_utm_distortion(measurer):
    result = measurer.measure(to_lonlat(utm_square(1000)))
    assert result.geodesic_area_sq_m == pytest.approx(result.area_sq_m, rel=0.005)


def test_linestring_length(measurer):
    result = measurer.measure(to_lonlat(utm_line(2500)))
    assert result.status == MeasureStatus.MEASURED
    assert result.length_m == pytest.approx(2500, rel=1e-6)
    assert result.geodesic_length_m == pytest.approx(2500, rel=0.005)
    assert result.area_sq_m is None


def test_point_is_not_applicable(measurer):
    result = measurer.measure(Point(78.47, 17.38))
    assert result.status == MeasureStatus.NOT_APPLICABLE
    assert result.area_sq_m is None and result.length_m is None


def test_polygon_with_hole_subtracts_the_hole(measurer):
    outer = utm_square(1000)
    hole = box(ORIGIN_E + 250, ORIGIN_N + 250, ORIGIN_E + 750, ORIGIN_N + 750)  # 500 x 500
    donut = to_lonlat(Polygon(outer.exterior.coords, [hole.exterior.coords]))
    result = measurer.measure(donut)
    assert result.area_sq_m == pytest.approx(1_000_000 - 250_000, rel=1e-6)
    assert result.geodesic_area_sq_m == pytest.approx(750_000, rel=0.005)


def test_multipolygon_sums_parts(measurer):
    a = utm_square(1000)
    b = box(ORIGIN_E + 5000, ORIGIN_N, ORIGIN_E + 5500, ORIGIN_N + 500)
    result = measurer.measure(to_lonlat(MultiPolygon([a, b])))
    assert result.area_sq_m == pytest.approx(1_250_000, rel=1e-6)


def test_multilinestring_sums_parts(measurer):
    a = utm_line(1000)
    b = LineString([(ORIGIN_E, ORIGIN_N + 300), (ORIGIN_E + 500, ORIGIN_N + 300)])
    result = measurer.measure(to_lonlat(MultiLineString([a, b])))
    assert result.length_m == pytest.approx(1500, rel=1e-6)


def test_geometry_collection_measures_its_parts(measurer):
    collection = to_lonlat(GeometryCollection([utm_square(1000), utm_line(1000), Point(ORIGIN_E, ORIGIN_N)]))
    result = measurer.measure(collection)
    assert result.status == MeasureStatus.MEASURED
    assert result.area_sq_m == pytest.approx(1_000_000, rel=1e-6)
    assert result.length_m == pytest.approx(1000, rel=1e-6)


def test_altitude_values_are_ignored(measurer):
    flat = to_lonlat(utm_square(1000))
    with_z = Polygon([(x, y, 450.0) for x, y in flat.exterior.coords])
    assert measurer.measure(with_z).area_sq_m == pytest.approx(1_000_000, rel=1e-6)


def test_self_intersecting_polygon_is_repaired_not_crashed(measurer):
    bowtie = to_lonlat(
        Polygon(
            [
                (ORIGIN_E, ORIGIN_N),
                (ORIGIN_E + 1000, ORIGIN_N + 1000),
                (ORIGIN_E + 1000, ORIGIN_N),
                (ORIGIN_E, ORIGIN_N + 1000),
            ]
        )
    )
    assert not bowtie.is_valid
    result = measurer.measure(bowtie)
    assert result.status == MeasureStatus.MEASURED
    assert "repaired" in result.note
    assert result.area_sq_m == pytest.approx(500_000, rel=1e-6)  # two triangles of 250,000


@pytest.mark.parametrize("geom", [None, Polygon(), Point()])
def test_empty_or_missing_geometry_is_unsupported(measurer, geom):
    result = measurer.measure(geom)
    assert result.status == MeasureStatus.UNSUPPORTED
    assert result.note


def test_projected_source_crs_is_reprojected_not_trusted():
    """Web Mercator inflates areas away from the equator; we must still get the true value."""
    import geopandas as gpd

    mercator_poly = gpd.GeoSeries([utm_square(1000)], crs="EPSG:32644").to_crs("EPSG:3857").iloc[0]
    assert mercator_poly.area > 1_100_000  # naive value in the file's CRS is badly inflated
    result = GeometryMeasurer(CRS.from_epsg(3857)).measure(mercator_poly)
    assert result.area_sq_m == pytest.approx(1_000_000, rel=1e-5)


def test_other_geographic_datum_is_handled():
    import geopandas as gpd

    nad83 = gpd.GeoSeries([utm_square(1000)], crs="EPSG:32644").to_crs("EPSG:4269").iloc[0]
    result = GeometryMeasurer(CRS.from_epsg(4269)).measure(nad83)
    assert result.area_sq_m == pytest.approx(1_000_000, rel=1e-4)


def test_out_of_range_coordinates_do_not_crash(measurer):
    result = measurer.measure(Polygon([(500, 500), (501, 500), (501, 501)]))
    assert result.status == MeasureStatus.UNSUPPORTED


def test_feature_spanning_utm_zones_gets_a_warning(measurer):
    wide = Polygon([(77.0, 17.0), (79.5, 17.0), (79.5, 18.0), (77.0, 18.0)])  # crosses 78°E
    result = measurer.measure(wide)
    assert result.status == MeasureStatus.MEASURED
    assert "UTM zones" in result.note


def test_polar_feature_uses_ups(measurer):
    result = measurer.measure(Polygon([(0, 85), (10, 85), (10, 86), (0, 86)]))
    assert result.status == MeasureStatus.MEASURED
    assert result.projected_crs == "EPSG:32661"
