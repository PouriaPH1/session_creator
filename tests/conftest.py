import pytest
from fastapi.testclient import TestClient

from server.auth_service import AuthService
from server.main import app


@pytest.fixture
def auth_service(tmp_path, monkeypatch):
    service = AuthService(tmp_path / "admin.sqlite3")
    service.set_credentials("administrator", "Strong-test-password-123")
    monkeypatch.setitem(app.state._state, "auth", service)
    return service


@pytest.fixture
def client(auth_service):
    client = TestClient(app, base_url="https://testserver")
    response = client.post("/api/auth/login", json={
        "username": "administrator", "password": "Strong-test-password-123"
    })
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    yield client
    client.close()
