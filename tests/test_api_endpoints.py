


def test_admin_panel_get(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Telegram Session Manager" in response.text
    assert "text/html" in response.headers["content-type"]


def test_stats_endpoint(client):
    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()
    assert "source_sessions_count" in data
    assert "target_sessions_count" in data
    assert "status" in data


def test_sessions_source_endpoint(client):
    response = client.get("/api/sessions/source")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_sessions_target_endpoint(client):
    response = client.get("/api/sessions/target")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_migration_status_endpoint(client):
    response = client.get("/api/migrate/status")
    assert response.status_code == 200
    data = response.json()
    assert "is_running" in data
    assert "logs" in data


def test_delete_all_source_endpoint(tmp_path, monkeypatch, client):
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


def test_delete_faulty_source_endpoint(tmp_path, monkeypatch, client):
    from config.settings import get_settings
    settings = get_settings()
    test_src = tmp_path / "source_faulty"
    test_src.mkdir(parents=True, exist_ok=True)
    
    # Create empty / corrupt session file
    corrupt_file = test_src / "corrupt_test.session"
    corrupt_file.write_text("invalid database header")
    monkeypatch.setattr(settings, "source_sessions_dir", test_src)

    res = client.delete("/api/sessions/source-faulty")
    assert res.status_code == 200
    assert res.json()["status"] == "deleted"
    assert "corrupt_test" in res.json()["deleted_sessions"]
    assert not corrupt_file.exists()


def test_delete_all_target_endpoint(tmp_path, monkeypatch, client):
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


def test_batch_migrate_skip_existing_when_all_exist(tmp_path, monkeypatch, client):
    from config.settings import get_settings
    from server.main import pool, migration_manager
    settings = get_settings()

    test_tgt = tmp_path / "target"
    test_tgt.mkdir(parents=True, exist_ok=True)
    (test_tgt / "123456789.session").write_text("test")
    monkeypatch.setattr(settings, "target_sessions_dir", test_tgt)

    # Mock detailed sessions with the same phone
    monkeypatch.setattr(
        pool,
        "get_detailed_sessions",
        lambda: [{"phone": "+123456789", "status": "active", "is_authorized": True}]
    )
    monkeypatch.setattr(migration_manager, "is_running", False)

    res = client.post("/api/migrate/batch", json={"delay": 1.0, "skip_existing": True})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "already_completed"
    assert data["skipped_existing_count"] == 1
    assert data["total_accounts"] == 0


def test_passwords_set_and_delete_endpoints(client):
    # Set stored password
    phone = "+989999999999"
    res = client.post("/api/accounts/passwords/set", json={
        "identifier": phone,
        "password": "MySecret2FA123!",
        "note": "Unit test note"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["has_2fa"] is True
    assert "MySecret2FA123!" not in res.text

    # Verify in passwords list
    res_list = client.get("/api/accounts/password-status", params={"identifier": phone})
    assert res_list.status_code == 200
    assert res_list.json() == {"has_2fa": True}

    # Delete password
    res_del = client.delete(f"/api/accounts/passwords/{phone}")
    assert res_del.status_code == 200

    # Verify deleted
    res_after = client.get("/api/accounts/password-status", params={"identifier": phone})
    assert res_after.json() == {"has_2fa": False}


def test_migrate_provide_and_skip_2fa_endpoints(client):
    from server.main import migration_manager
    import asyncio

    # When not waiting, should return 400
    res = client.post("/api/migrate/provide-2fa", json={
        "phone": "+989999999999",
        "password": "test",
        "save_to_storage": False
    })
    assert res.status_code == 400

    # Simulate waiting state
    migration_manager.waiting_2fa = True
    migration_manager.waiting_2fa_phone = "+989999999999"
    migration_manager._2fa_event = asyncio.Event()

    # Provide 2FA password
    res = client.post("/api/migrate/provide-2fa", json={
        "phone": "+989999999999",
        "password": "provided_password",
        "save_to_storage": True
    })
    assert res.status_code == 200
    assert migration_manager._2fa_input_password == "provided_password"
    assert migration_manager._2fa_event.is_set()

    # Reset
    migration_manager.reset()
    # Clean up test password
    client.delete("/api/accounts/passwords/+989999999999")


def test_login_api_invalid_requests(client):
    # Invalid phone format
    res = client.post("/api/sessions/login/send-code", json={"phone": "123"})
    assert res.status_code == 400
    assert "نامعتبر" in res.json()["detail"]

    # Unknown login_id for verify-code
    res = client.post("/api/sessions/login/verify-code", json={"login_id": "nonexistent_id", "code": "12345"})
    assert res.status_code == 404

    # Unknown login_id for verify-password
    res = client.post("/api/sessions/login/verify-password", json={"login_id": "nonexistent_id", "password": "pass"})
    assert res.status_code == 404

    # Cancel nonexistent login_id
    res = client.post("/api/sessions/login/cancel", json={"login_id": "nonexistent_id"})
    assert res.status_code == 200
    assert res.json()["status"] == "not_found"




