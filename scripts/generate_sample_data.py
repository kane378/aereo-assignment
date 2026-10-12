"""Regenerates the files in sample_data/ (deterministic, no randomness).

python scripts/generate_sample_data.py
"""

import io
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
from pyproj import Transformer
from shapely.geometry import LineString, Polygon, box

OUT = Path(__file__).resolve().parent.parent / "sample_data"
UTM = "EPSG:32644"  # WGS 84 / UTM zone 44N: Hyderabad
to_utm = Transformer.from_crs("EPSG:4326", UTM, always_xy=True)
to_ll = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)


def utm_box(lon: float, lat: float, width: float, height: float) -> Polygon:
    x, y = to_utm.transform(lon, lat)
    return box(x, y, x + width, y + height)


def utm_to_ll(geom):
    return gpd.GeoSeries([geom], crs=UTM).to_crs("EPSG:4326").iloc[0]


def ring(poly: Polygon) -> str:
    return " ".join(f"{x:.7f},{y:.7f},0" for x, y in poly.exterior.coords)


def write_kml() -> None:
    plots = [
        ("Plot A - Gachibowli", utm_box(78.3489, 17.4401, 400, 250), "Commercial"),
        ("Plot B - Madhapur", utm_box(78.3910, 17.4480, 300, 300), "Residential"),
        ("Plot C - Kondapur", utm_box(78.3600, 17.4650, 500, 120), "Mixed use"),
    ]
    road = LineString(
        [
            to_utm.transform(78.3480, 17.4390),
            to_utm.transform(78.3900, 17.4470),
            to_utm.transform(78.4300, 17.4400),
        ]
    )
    placemarks = []
    for name, geom, use in plots:
        placemarks.append(
            f"<Placemark><name>{name}</name><description>Land use: {use}</description>"
            f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{ring(utm_to_ll(geom))}"
            "</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
        )
    road_ll = utm_to_ll(road)
    road_coords = " ".join(f"{x:.7f},{y:.7f},0" for x, y in road_ll.coords)
    placemarks.append(
        f"<Placemark><name>Access Road</name><LineString><coordinates>{road_coords}"
        "</coordinates></LineString></Placemark>"
    )
    for name, (lon, lat) in {"Benchmark 1": (78.3500, 17.4410), "Water Tank": (78.3925, 17.4495)}.items():
        placemarks.append(
            f"<Placemark><name>{name}</name><Point><coordinates>{lon},{lat},0</coordinates></Point></Placemark>"
        )
    kml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Hyderabad Site Survey</name>\n'
        + "\n".join(placemarks)
        + "\n</Document></kml>\n"
    )
    (OUT / "hyderabad_survey.kml").write_text(kml, encoding="utf-8")


def write_shapefile_zip() -> None:
    parcels = gpd.GeoDataFrame(
        {
            "parcel_id": ["HYD-001", "HYD-002", "HYD-003"],
            "land_use": ["Residential", "Commercial", "Park"],
            "ward": [104, 108, 112],
        },
        geometry=[
            utm_box(78.4744, 17.3850, 1000, 1000),  # exactly 1 km x 1 km = 1,000,000 m2
            utm_box(78.4900, 17.4000, 250, 400),
            utm_box(78.5100, 17.3900, 600, 600),
        ],
        crs=UTM,  # a projected CRS on purpose: the API must still re-measure correctly
    )
    canals = gpd.GeoDataFrame(
        {"canal": ["Musi Link"]},
        geometry=[LineString([to_utm.transform(78.47, 17.38), to_utm.transform(78.49, 17.39)])],
        crs=UTM,
    )
    with tempfile.TemporaryDirectory() as tmp:
        parcels.to_file(Path(tmp) / "parcels.shp")
        canals.to_file(Path(tmp) / "canals.shp")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(Path(tmp).iterdir()):
                zf.write(path, path.name)
    (OUT / "hyderabad_parcels_utm.zip").write_bytes(buffer.getvalue())


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    write_kml()
    write_shapefile_zip()
    print("Wrote", *sorted(p.name for p in OUT.iterdir()))
