import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_settings(tmp_path, **overrides) -> Settings:
    values = dict(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        api_keys="",
        rate_limit_per_minute=0,
        upload_rate_limit_per_minute=0,
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def make_client(tmp_path):
    clients = []

    def _make(**overrides) -> TestClient:
        client = TestClient(create_app(make_settings(tmp_path, **overrides)))
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()
