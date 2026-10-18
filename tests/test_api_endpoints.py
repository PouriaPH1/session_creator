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


def test_delete_all_source_endpoint(tmp_path, monkeypatch):
    from config.settings import get_settings
    settings = get_settings()
    test_src = tmp_path / "source"
    test_src.mkdir(parents=True, exist_ok=True)
    dummy_file = test_src / "dummy123.session"
    dummy_file.write_text("test")
    monkeypatch.setattr(settings, "source_sessions_dir", test_src)

    res = client.delete("/api/sessions/source-all")
    assert res.status_code == 200
    assert res.json()["status"] == "deleted"
    assert res.json()["deleted_count"] >= 1
    assert not dummy_file.exists()


def test_delete_all_target_endpoint(tmp_path, monkeypatch):
    from config.settings import get_settings
    settings = get_settings()
    test_tgt = tmp_path / "target"
    test_tgt.mkdir(parents=True, exist_ok=True)
    dummy_file = test_tgt / "dummy456.session"
    dummy_file.write_text("test")
    monkeypatch.setattr(settings, "target_sessions_dir", test_tgt)

    res = client.delete("/api/sessions/target-all")
    assert res.status_code == 200
    assert res.json()["status"] == "deleted"
    assert res.json()["deleted_count"] >= 1
    assert not dummy_file.exists()

