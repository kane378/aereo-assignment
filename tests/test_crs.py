import pytest
from pyproj import CRS

from app.services.crs import crs_label, parse_assume_crs, utm_epsg_for, utm_zone_number


@pytest.mark.parametrize(
    "lon,lat,expected",
    [
        (78.4744, 17.3850, 32644),  # Hyderabad, northern hemisphere
        (151.2093, -33.8688, 32756),  # Sydney, southern hemisphere
        (-0.1276, 51.5072, 32630),  # London (zone 30)
        (180.0, 10.0, 32660),  # antimeridian must clamp to zone 60
        (-180.0, 10.0, 32601),
        (10.0, 85.0, 32661),  # polar fallback: UPS North
        (10.0, -85.0, 32761),  # polar fallback: UPS South
    ],
)
def test_utm_epsg_selection(lon, lat, expected):
    assert utm_epsg_for(lon, lat) == expected


def test_zone_number_bounds():
    assert utm_zone_number(-180) == 1
    assert utm_zone_number(179.999) == 60
    assert utm_zone_number(180) == 60


def test_crs_label_prefers_authority_code():
    assert crs_label(CRS.from_epsg(4326)) == "EPSG:4326"
    assert crs_label(None) is None


@pytest.mark.parametrize("bad", ["", "4326", "+proj=longlat +datum=WGS84", "EPSG:abc", "EPSG:99999999"])
def test_assume_crs_rejects_anything_but_epsg_codes(bad):
    with pytest.raises(ValueError):
        parse_assume_crs(bad)


def test_assume_crs_accepts_epsg():
    assert parse_assume_crs(" epsg:4326 ").to_epsg() == 4326
