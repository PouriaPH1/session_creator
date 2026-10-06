from fastapi.testclient import TestClient
from server.main import app

client = TestClient(app)


def test_admin_panel_get():
    response = client.get("/")
    assert response.status_code == 200
    assert "Telegram Session Manager" in response.text
    assert "text/html" in response.headers["content-type"]


def test_stats_endpoint():
    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()
    assert "source_sessions_count" in data
    assert "target_sessions_count" in data
    assert "status" in data


def test_sessions_source_endpoint():
    response = client.get("/api/sessions/source")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_sessions_target_endpoint():
    response = client.get("/api/sessions/target")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_migration_status_endpoint():
    response = client.get("/api/migrate/status")
    assert response.status_code == 200
    data = response.json()
    assert "is_running" in data
    assert "logs" in data
