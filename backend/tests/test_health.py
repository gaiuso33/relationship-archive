from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_is_not_served_outside_the_versioned_prefix() -> None:
    assert client.get("/health").status_code == 404


def test_unknown_route_returns_not_found() -> None:
    assert client.get("/api/v1/does-not-exist").status_code == 404
