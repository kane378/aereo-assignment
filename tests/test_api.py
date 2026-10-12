import uuid

import pytest
from shapely.geometry import Point

from tests.helpers import (
    UTM,
    frame,
    kml_bytes,
    kml_line,
    kml_point,
    kml_polygon,
    shapefile_zip,
    to_lonlat,
    utm_line,
    utm_square,
)


def upload(client, name, content, content_type="application/octet-stream", **form):
    return client.post("/api/files/", files={"file": (name, content, content_type)}, data=form)


@pytest.fixture
def survey_kml():
    return kml_bytes(
        kml_polygon(to_lonlat(utm_square(1000)), "Plot A")
        + kml_line(to_lonlat(utm_line(2000)), "Road 1")
        + kml_point(to_lonlat(Point(utm_square().centroid.x, utm_square().centroid.y)), "Well")
    )


# ------------------------------------------------------------------ happy path: KML
def test_upload_kml_end_to_end(client, survey_kml):
    response = upload(client, "survey.kml", survey_kml)
    assert response.status_code == 201
    info = response.json()
    assert info["filename"] == "survey.kml"
    assert info["status"] == "COMPLETED"
    assert info["feature_count"] == 3
    assert info["crs"] == "EPSG:4326"
    assert info["file_type"] == "kml"
    assert info["geometry_summary"] == {"Polygon": 1, "LineString": 1, "Point": 1}
    uuid.UUID(info["id"])  # ids are UUIDs, not guessable counters

    assert client.get(f"/api/files/{info['id']}/").json() == info

    data = client.get(f"/api/files/{info['id']}/measurements/").json()
    by_type = {m["geometry_type"]: m for m in data["measurements"]}
    assert by_type["Polygon"]["area_sq_m"] == pytest.approx(1_000_000, rel=1e-5)
    assert by_type["Polygon"]["area_hectares"] == pytest.approx(100, rel=1e-5)
    assert by_type["LineString"]["length_m"] == pytest.approx(2000, rel=1e-5)
    assert by_type["Point"]["status"] == "NOT_APPLICABLE"
    assert data["summary"]["measured"] == 2
    assert data["summary"]["not_applicable"] == 1
    assert data["summary"]["total_area_sq_m"] == pytest.approx(1_000_000, rel=1e-5)
    assert data["pagination"] == {"limit": 100, "offset": 0, "total": 3}


def test_features_endpoint_returns_geometry_crs_and_properties(client, survey_kml):
    file_id = upload(client, "survey.kml", survey_kml).json()["id"]
    features = client.get(f"/api/files/{file_id}/features/").json()["features"]
    polygon = next(f for f in features if f["geometry_type"] == "Polygon")
    assert polygon["geometry"]["type"] == "Polygon"
    assert polygon["crs"] == "EPSG:4326"
    assert polygon["properties"]["Name"] == "Plot A"
    assert polygon["measurement"]["status"] == "MEASURED"

    lean = client.get(f"/api/files/{file_id}/features/?include_geometry=false").json()["features"]
    assert all(f["geometry"] is None for f in lean)


def test_measurements_pagination(client, survey_kml):
    file_id = upload(client, "survey.kml", survey_kml).json()["id"]
    page = client.get(f"/api/files/{file_id}/measurements/?limit=1&offset=1").json()
    assert [m["feature_index"] for m in page["measurements"]] == [1]
    assert page["pagination"] == {"limit": 1, "offset": 1, "total": 3}
    assert client.get(f"/api/files/{file_id}/measurements/?limit=0").status_code == 422
    assert client.get(f"/api/files/{file_id}/measurements/?limit=5000").status_code == 422


# ------------------------------------------------------------- happy path: Shapefile
def test_upload_multi_layer_shapefile_zip(client):
    polygons = frame([to_lonlat(utm_square(1000))], name=["Parcel 1"])
    lines = frame([to_lonlat(utm_line(750))], name=["Canal"])
    points = frame([Point(78.47, 17.38)], name=["Gauge"])
    archive = shapefile_zip(("parcels", polygons), ("canals", lines), ("gauges", points))

    info = upload(client, "survey.zip", archive).json()
    assert info["status"] == "COMPLETED", info
    assert info["feature_count"] == 3
    assert info["file_type"] == "shapefile"
    assert info["crs"] == "EPSG:4326"

    rows = client.get(f"/api/files/{info['id']}/measurements/").json()["measurements"]
    assert {r["layer"] for r in rows} == {"parcels", "canals", "gauges"}
    polygon = next(r for r in rows if r["layer"] == "parcels")
    assert polygon["area_sq_m"] == pytest.approx(1_000_000, rel=1e-5)


def test_shapefile_in_projected_crs(client):
    utm_frame = frame([utm_square(1000)], crs=UTM, name=["utm plot"])
    info = upload(client, "utm.zip", shapefile_zip(("plot", utm_frame))).json()
    assert info["status"] == "COMPLETED"
    assert info["crs"] == "EPSG:32644"
    row = client.get(f"/api/files/{info['id']}/measurements/").json()["measurements"][0]
    assert row["area_sq_m"] == pytest.approx(1_000_000, rel=1e-6)


def test_shapefile_without_prj_fails_gracefully_then_works_with_assume_crs(client):
    gdf = frame([to_lonlat(utm_square(1000))], name=["x"])
    archive = shapefile_zip(("plot", gdf), skip_ext=(".prj",))

    failed = upload(client, "noprj.zip", archive)
    assert failed.status_code == 201
    body = failed.json()
    assert body["status"] == "FAILED"
    assert "assume_crs" in body["error_message"]
    assert client.get(f"/api/files/{body['id']}/").json()["status"] == "FAILED"
    conflict = client.get(f"/api/files/{body['id']}/measurements/")
    assert conflict.status_code == 409

    ok = upload(client, "noprj.zip", archive, assume_crs="EPSG:4326").json()
    assert ok["status"] == "COMPLETED"
    assert ok["crs"] == "EPSG:4326"


def test_invalid_assume_crs_is_rejected(client):
    gdf = frame([to_lonlat(utm_square(1000))])
    for bad in ["+proj=longlat +datum=WGS84", "nonsense", "EPSG:0"]:
        response = upload(client, "a.zip", shapefile_zip(("p", gdf)), assume_crs=bad)
        assert response.status_code == 400


# ----------------------------------------------------------------- input validation
def test_unsupported_extension(client):
    response = upload(client, "notes.txt", b"hello")
    assert response.status_code == 415


def test_empty_upload(client):
    assert upload(client, "empty.kml", b"").status_code == 400


def test_zip_that_is_not_a_zip(client):
    response = upload(client, "fake.zip", b"definitely not a zip file")
    assert response.status_code == 400
    assert "ZIP" in response.json()["detail"]


def test_zip_without_shapefile(client):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("readme.txt", "no shapefile here")
    response = upload(client, "empty.zip", buffer.getvalue())
    assert response.status_code == 400
    assert ".shp" in response.json()["detail"]


def test_shapefile_missing_companion_files(client):
    gdf = frame([to_lonlat(utm_square(1000))])
    response = upload(client, "partial.zip", shapefile_zip(("p", gdf), skip_ext=(".dbf",)))
    assert response.status_code == 400
    assert ".dbf" in response.json()["detail"]


def test_kml_that_is_not_xml(client):
    assert upload(client, "bad.kml", b"this is not xml at all").status_code == 400


def test_xml_that_is_not_kml(client):
    assert upload(client, "other.kml", b"<?xml version='1.0'?><html></html>").status_code == 400


def test_corrupt_shapefile_fails_gracefully(client):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for ext in (".shp", ".shx", ".dbf"):
            zf.writestr(f"broken{ext}", b"garbage" * 20)
    response = upload(client, "broken.zip", buffer.getvalue())
    assert response.status_code == 201
    assert response.json()["status"] == "FAILED"
    assert response.json()["error_message"]


def test_kml_without_features_is_failed_not_crashed(client):
    response = upload(client, "empty.kml", kml_bytes(""))
    assert response.status_code == 201
    assert response.json()["status"] == "FAILED"


def test_feature_limit_is_enforced(make_client, survey_kml):
    client = make_client(max_features=2)
    body = upload(client, "survey.kml", survey_kml).json()
    assert body["status"] == "FAILED"
    assert "more than 2 features" in body["error_message"]


# ------------------------------------------------------------------ resource routes
def test_unknown_and_malformed_ids(client):
    assert client.get(f"/api/files/{uuid.uuid4()}/").status_code == 404
    assert client.get(f"/api/files/{uuid.uuid4()}/measurements/").status_code == 404
    assert client.get("/api/files/not-a-uuid/").status_code == 422
    assert client.get("/api/files/1%27%20OR%20%271%27=%271/").status_code == 422  # SQLi probe


def test_list_and_delete(client, survey_kml):
    first = upload(client, "a.kml", survey_kml).json()["id"]
    second = upload(client, "b.kml", survey_kml).json()["id"]
    listed = client.get("/api/files/").json()
    assert [f["id"] for f in listed] == [second, first]  # newest first

    assert client.delete(f"/api/files/{first}/").status_code == 204
    assert client.get(f"/api/files/{first}/").status_code == 404
    assert client.get(f"/api/files/{first}/measurements/").status_code == 404
    assert client.get(f"/api/files/{second}/measurements/").status_code == 200


def test_filename_is_sanitised(client, survey_kml):
    info = upload(client, "../../etc/passwd.kml", survey_kml).json()
    assert info["filename"] == "passwd.kml"  # directory parts are dropped


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("..\\..\\evil.kml", "evil.kml"),
        ("nul\x00byte\x07.kml", "nulbyte.kml"),
        ("  spaced.kml  ", "spaced.kml"),
        ("a" * 400 + ".kml", ("a" * 400 + ".kml")[:255]),
    ],
)
def test_sanitize_filename_unit(raw, expected):
    from app.services.uploads import sanitize_filename

    assert sanitize_filename(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "..", "/", "\x00\x01"])
def test_sanitize_filename_rejects_unusable_names(raw):
    from app.core.errors import InvalidUploadError
    from app.services.uploads import sanitize_filename

    with pytest.raises(InvalidUploadError):
        sanitize_filename(raw)


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"
