import json
from pathlib import Path
from unittest.mock import AsyncMock

from server.main import account_service, migration_manager, pool


PHONE = "+15550000001"
SECRET = "Private-stored-2FA-123!"


def test_source_list_exposes_only_presence_with_legacy_storage(client, monkeypatch):
    account_service.passwords_file.write_text(json.dumps({PHONE.lstrip("+"): SECRET}))
    monkeypatch.setattr(pool, "get_detailed_sessions", lambda: [
        {"phone": PHONE, "session_name": PHONE.lstrip("+"), "status": "active", "is_authorized": True},
        {"phone": "N/A", "session_name": "unknown"},
    ])
    response = client.get("/api/sessions/source")
    assert response.status_code == 200
    assert response.json()[0]["has_2fa"] is True
    assert response.json()[1]["has_2fa"] is False
    assert SECRET not in response.text and "saved_2fa" not in response.text
    status = client.get("/api/accounts/password-status", params={"identifier": PHONE})
    assert status.json() == {"has_2fa": True}


def test_migration_reads_stored_password_inside_server(client, monkeypatch):
    account_service.set_stored_password(PHONE, SECRET)
    monkeypatch.setattr(pool, "get_detailed_sessions", lambda: [
        {"phone": PHONE, "session_name": PHONE.lstrip("+"), "is_authorized": True}
    ])
    monkeypatch.setattr(migration_manager, "is_running", False)
    start = AsyncMock(return_value=True)
    monkeypatch.setattr(migration_manager, "start_batch_migration", start)
    response = client.post("/api/migrate/single", json={"phone": PHONE})
    assert response.status_code == 200 and SECRET not in response.text
    assert start.call_args.args[1] == {PHONE: SECRET}


def test_telegram_password_change_uses_saved_current_password(client, monkeypatch):
    account_service.set_stored_password(PHONE, SECRET)
    telegram_client = AsyncMock()
    telegram_client.is_connected = lambda: True
    monkeypatch.setattr(pool, "get_client", lambda _: telegram_client)
    new_password = "New-private-password-456!"
    response = client.post("/api/accounts/2fa", json={"identifier": PHONE, "new_password": new_password})
    assert response.status_code == 200
    assert SECRET not in response.text and new_password not in response.text
    telegram_client.edit_2fa.assert_awaited_once_with(
        current_password=SECRET, new_password=new_password, hint=""
    )
    assert account_service.get_password(PHONE) == new_password


def test_batch_generated_passwords_never_return_to_browser(client, monkeypatch):
    monkeypatch.setattr(pool, "get_detailed_sessions", lambda: [
        {"phone": PHONE, "session_name": PHONE.lstrip("+"), "is_authorized": True, "status": "active"}
    ])
    response = client.post("/api/accounts/2fa/batch-random", json={"telegram_update": False, "delay": 0})
    assert response.status_code == 200
    saved = account_service.get_password(PHONE)
    assert saved and saved not in response.text
    assert response.json()["results"][0]["has_2fa"] is True
    assert "password" not in response.json()["results"][0]


def test_password_cli_reads_one_account_and_handles_missing(client, monkeypatch, capsys):
    from cli import account_password
    import pytest
    account_service.set_stored_password(PHONE, SECRET)
    monkeypatch.setattr(account_password, "AccountManagementService", lambda **kwargs: account_service)
    monkeypatch.setattr("sys.argv", ["account_password", PHONE])
    account_password.main()
    assert capsys.readouterr().out.strip() == SECRET
    monkeypatch.setattr("sys.argv", ["account_password", "+15550000099"])
    with pytest.raises(SystemExit) as failure:
        account_password.main()
    assert failure.value.code == 1
    output = capsys.readouterr()
    assert SECRET not in output.out and SECRET not in output.err


def test_frontend_has_no_bulk_or_stored_password_rendering():
    template = (Path(__file__).parents[1] / "server/templates/index.html").read_text(encoding="utf-8")
    for removed in ("saved_2fa", "openPasswordsModal", "passwords-table-body", "export-csv", "matchedPwd", "r.password"):
        assert removed not in template
