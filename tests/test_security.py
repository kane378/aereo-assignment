"""Security tests: each one simulates a real attack and asserts it is neutralised."""

import io
import zipfile

import pytest

from tests.helpers import frame, kml_bytes, kml_polygon, shapefile_zip, to_lonlat, utm_square


def upload(client, name, content, headers=None):
    return client.post(
        "/api/files/", files={"file": (name, content, "application/octet-stream")}, headers=headers or {}
    )


def make_zip(
    entries: dict[str, bytes] | None = None, infos: list[tuple[zipfile.ZipInfo, bytes]] | None = None
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in (entries or {}).items():
            zf.writestr(name, data)
        for info, data in infos or []:
            zf.writestr(info, data)
    return buffer.getvalue()


# ------------------------------------------------------------------- zip attacks
@pytest.mark.parametrize(
    "evil_name",
    [
        "../../evil.shp",
        "/etc/cron.d/evil.shp",
        "..\\..\\evil.shp",
        "C:\\Windows\\evil.shp",
        "ok/../../evil.shp",
    ],
)
def test_zip_slip_is_rejected(client, evil_name):
    response = upload(client, "evil.zip", make_zip({evil_name: b"x"}))
    assert response.status_code == 422
    assert client.get("/api/files/").json() == []  # nothing was stored


def test_zip_slip_never_writes_outside_extraction_dir(client, tmp_path):
    upload(client, "evil.zip", make_zip({"../escaped.shp": b"x"}))
    assert not list(tmp_path.glob("**/escaped.shp"))
    assert not (tmp_path.parent / "escaped.shp").exists()


def test_zip_bomb_by_compression_ratio(client):
    bomb = make_zip({"huge.dbf": b"\x00" * (30 * 1024 * 1024)})  # ~30 KB -> 30 MB
    assert len(bomb) < 100_000
    response = upload(client, "bomb.zip", bomb)
    assert response.status_code == 422
    assert "ratio" in response.json()["detail"]


def test_zip_bomb_by_total_size(make_client):
    client = make_client(max_unzipped_mb=1, max_compression_ratio=100_000)
    response = upload(client, "big.zip", make_zip({"a.dbf": b"\x00" * (3 * 1024 * 1024)}))
    assert response.status_code == 422
    assert "allowed size" in response.json()["detail"]


def test_zip_with_too_many_entries(make_client):
    client = make_client(max_zip_entries=5)
    response = upload(client, "many.zip", make_zip({f"f{i}.txt": b"x" for i in range(10)}))
    assert response.status_code == 422
    assert "too many entries" in response.json()["detail"]


def test_zip_with_symlink_entry(client):
    link = zipfile.ZipInfo("link.shp")
    link.create_system = 3  # unix
    link.external_attr = 0o120777 << 16
    response = upload(client, "link.zip", make_zip(infos=[(link, b"/etc/passwd")]))
    assert response.status_code == 422
    assert "symbolic link" in response.json()["detail"]


def test_non_shapefile_payloads_are_never_extracted(client, monkeypatch):
    """A script hidden in the archive is ignored: only allow-listed extensions are written."""
    from app.services import safe_zip

    seen = []
    original = safe_zip.extract_shapefile_zip

    def spy(zip_path, dest, **kwargs):
        try:
            return original(zip_path, dest, **kwargs)
        finally:
            seen.extend(sorted(p.name for p in dest.rglob("*") if p.is_file()))

    monkeypatch.setattr("app.api.files.extract_shapefile_zip", spy)
    gdf = frame([to_lonlat(utm_square(1000))])
    archive = make_zip({"run.sh": b"rm -rf /", "payload.exe": b"MZ", "x.py": b"import os"})
    # combine with a real shapefile
    real = zipfile.ZipFile(io.BytesIO(shapefile_zip(("p", gdf))))
    buffer = io.BytesIO(archive)
    with zipfile.ZipFile(buffer, "a") as zf:
        for name in real.namelist():
            zf.writestr(name, real.read(name))
    response = upload(client, "mixed.zip", buffer.getvalue())
    assert response.status_code == 201
    assert response.json()["status"] == "COMPLETED"
    assert not any(n.endswith((".sh", ".exe", ".py")) for n in seen)
    assert "p.shp" in seen


# ------------------------------------------------------------------ XML attacks
XXE = b"""<?xml version="1.0"?>
<!DOCTYPE kml [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>&xxe;</name>
<Point><coordinates>78,17,0</coordinates></Point></Placemark></Document></kml>"""

BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">]>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>&lol3;</name></Document></kml>"""

BARE_DOCTYPE = b"""<?xml version="1.0"?><!DOCTYPE kml>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document/></kml>"""


@pytest.mark.parametrize(
    "payload", [XXE, BILLION_LAUGHS, BARE_DOCTYPE], ids=["xxe", "billion-laughs", "doctype"]
)
def test_xml_entity_attacks_are_rejected(client, payload):
    response = upload(client, "evil.kml", payload)
    assert response.status_code == 422
    assert "DTD" in response.json()["detail"]
    assert client.get("/api/files/").json() == []


# --------------------------------------------------------------- size and abuse
def test_oversized_upload_is_rejected(make_client):
    client = make_client(max_upload_mb=1)
    response = upload(client, "big.kml", b"<kml>" + b"A" * (2 * 1024 * 1024))
    assert response.status_code == 413


def test_upload_without_content_length_is_refused(client):
    def chunks():
        yield b"--b\r\n"

    response = client.post(
        "/api/files/", content=chunks(), headers={"Content-Type": "multipart/form-data; boundary=b"}
    )
    assert response.status_code == 411


def test_rate_limit_returns_429_with_retry_after(make_client):
    client = make_client(rate_limit_per_minute=5)
    codes = [client.get("/api/files/").status_code for _ in range(7)]
    assert codes[:5] == [200] * 5
    assert codes[5:] == [429, 429]
    limited = client.get("/api/files/")
    assert int(limited.headers["Retry-After"]) >= 1


def test_upload_has_its_own_stricter_limit(make_client):
    client = make_client(upload_rate_limit_per_minute=2)
    body = kml_bytes(kml_polygon(to_lonlat(utm_square())))
    codes = [upload(client, "a.kml", body).status_code for _ in range(3)]
    assert codes == [201, 201, 429]
    assert client.get("/api/files/").status_code == 200  # reads are unaffected


def test_health_endpoint_is_not_rate_limited(make_client):
    client = make_client(rate_limit_per_minute=1)
    assert all(client.get("/health").status_code == 200 for _ in range(5))


# ---------------------------------------------------------------- authentication
def test_api_key_required_when_configured(make_client):
    client = make_client(api_keys="alpha-key,beta-key")
    assert client.get("/api/files/").status_code == 401
    assert client.get("/api/files/", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/files/", headers={"X-API-Key": ""}).status_code == 401
    assert client.get("/api/files/", headers={"X-API-Key": "alpha-key"}).status_code == 200
    assert client.get("/api/files/", headers={"X-API-Key": "beta-key"}).status_code == 200
    assert client.get("/health").status_code == 200  # probes stay open
    assert client.get("/api/files/").headers["WWW-Authenticate"] == "ApiKey"


def test_non_ascii_api_key_does_not_crash(make_client):
    client = make_client(api_keys="secret")
    response = client.get("/api/files/", headers={"X-API-Key": "séçret".encode("latin-1")})
    assert response.status_code == 401


def test_upload_requires_key_before_any_processing(make_client):
    client = make_client(api_keys="secret")
    assert upload(client, "a.kml", b"<kml/>").status_code == 401


# ------------------------------------------------------------------- hardening
def test_security_headers_on_every_response(client):
    for path in ("/health", "/api/files/", "/api/files/00000000-0000-0000-0000-000000000000/"):
        headers = client.get(path).headers
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["Referrer-Policy"] == "no-referrer"
        assert "default-src 'none'" in headers["Content-Security-Policy"]
        assert headers["Cache-Control"] == "no-store"
        assert len(headers["X-Request-ID"]) == 32


def test_docs_can_be_disabled_in_production(make_client):
    client = make_client(enable_docs=False)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_docs_available_by_default_with_relaxed_csp(client):
    response = client.get("/docs")
    assert response.status_code == 200
    assert "cdn.jsdelivr.net" in response.headers["Content-Security-Policy"]


def test_host_header_allow_list(make_client):
    client = make_client(allowed_hosts="api.example.com")
    assert client.get("/health", headers={"Host": "evil.example.net"}).status_code == 400
    assert client.get("/health", headers={"Host": "api.example.com"}).status_code == 200


def test_cors_is_off_unless_configured(client, make_client):
    assert (
        "access-control-allow-origin"
        not in client.get("/health", headers={"Origin": "https://evil.com"}).headers
    )
    cors = make_client(cors_origins="https://app.example.com")
    allowed = cors.get("/health", headers={"Origin": "https://app.example.com"})
    assert allowed.headers["access-control-allow-origin"] == "https://app.example.com"
    denied = cors.get("/health", headers={"Origin": "https://evil.com"})
    assert "access-control-allow-origin" not in denied.headers


def test_internal_errors_do_not_leak_details(make_client, monkeypatch):
    client = make_client()
    from app.api import files

    def boom(*_a, **_k):
        raise RuntimeError("SECRET /srv/internal/path password=hunter2")

    monkeypatch.setattr(files, "save_upload", boom)
    quiet = type(client)(client.app, raise_server_exceptions=False)
    response = upload(quiet, "a.kml", b"<kml/>")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert "hunter2" not in response.text
