import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from fastapi import HTTPException
from telethon.errors import (
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
    PasswordHashInvalidError,
)

from config.settings import get_settings
from server.account_service import AccountManagementService
from server.login_service import TelegramLoginService, normalize_phone
from server.metadata_service import MetadataService
from server.proxy_service import ProxyService
from server.session_pool import TelethonSessionPool


def test_normalize_phone():
    assert normalize_phone("+989123456789") == "+989123456789"
    assert normalize_phone("09123456789") == "+989123456789"
    assert normalize_phone("989123456789") == "+989123456789"
    assert normalize_phone("+1 (406) 719-4320") == "+14067194320"
    assert normalize_phone("00447123456789") == "+447123456789"

    with pytest.raises(HTTPException):
        normalize_phone("123")  # Too short

    with pytest.raises(HTTPException):
        normalize_phone("invalid_phone")


@pytest.mark.asyncio
async def test_start_login_success(tmp_path):
    settings = get_settings()
    metadata_service = MetadataService(settings)
    pool = TelethonSessionPool(settings, metadata_service)
    account_service = AccountManagementService(pool, settings)
    proxy_service = ProxyService(settings)

    service = TelegramLoginService(
        settings=settings,
        pool=pool,
        account_service=account_service,
        metadata_service=metadata_service,
        proxy_service=proxy_service
    )
    service.temp_dir = tmp_path

    mock_client = AsyncMock()
    mock_sent_code = MagicMock()
    mock_sent_code.phone_code_hash = "mock_hash_123"
    mock_sent_code.timeout = 120
    mock_sent_code.type = MagicMock()
    mock_client.send_code_request.return_value = mock_sent_code

    with patch("server.login_service.TelegramClient", return_value=mock_client):
        result = await service.start_login(
            phone="+989123456789",
            category="TestCat",
            remark="TestRemark"
        )
        assert result["status"] == "code_sent"
        assert result["phone"] == "+989123456789"
        assert "login_id" in result
        login_id = result["login_id"]
        assert login_id in service._active_logins

        # Cancel login
        cancel_res = await service.cancel_login(login_id)
        assert cancel_res["status"] == "cancelled"
        assert login_id not in service._active_logins


@pytest.mark.asyncio
async def test_start_login_banned_error(tmp_path):
    settings = get_settings()
    metadata_service = MetadataService(settings)
    pool = TelethonSessionPool(settings, metadata_service)
    account_service = AccountManagementService(pool, settings)
    proxy_service = ProxyService(settings)

    service = TelegramLoginService(
        settings=settings,
        pool=pool,
        account_service=account_service,
        metadata_service=metadata_service,
        proxy_service=proxy_service
    )
    service.temp_dir = tmp_path

    mock_client = AsyncMock()
    mock_client.send_code_request.side_effect = PhoneNumberBannedError(request=None)

    with patch("server.login_service.TelegramClient", return_value=mock_client):
        with pytest.raises(HTTPException) as exc_info:
            await service.start_login(phone="+989123456789")
        assert exc_info.value.status_code == 400
        assert "مسدود" in exc_info.value.detail


@pytest.mark.asyncio
async def test_verify_code_success_without_2fa(tmp_path):
    settings = get_settings()
    metadata_service = MetadataService(settings)
    pool = TelethonSessionPool(settings, metadata_service)
    account_service = AccountManagementService(pool, settings)
    proxy_service = ProxyService(settings)

    service = TelegramLoginService(
        settings=settings,
        pool=pool,
        account_service=account_service,
        metadata_service=metadata_service,
        proxy_service=proxy_service
    )
    service.temp_dir = tmp_path

    mock_client = AsyncMock()
    mock_sent_code = MagicMock()
    mock_sent_code.phone_code_hash = "mock_hash_123"
    mock_client.send_code_request.return_value = mock_sent_code

    mock_client.is_user_authorized.return_value = True
    mock_me = MagicMock()
    mock_me.id = 123456789
    mock_me.first_name = "Reza"
    mock_me.last_name = "Tehrani"
    mock_me.phone = "989123456789"
    mock_me.username = "rezatest"
    mock_client.get_me.return_value = mock_me

    with patch("server.login_service.TelegramClient", return_value=mock_client):
        res = await service.start_login(phone="+989123456789")
        login_id = res["login_id"]

        # Create dummy temp session file
        temp_file = Path(f"{service._active_logins[login_id].temp_session_path}.session")
        temp_file.write_bytes(b"SQLite format 3\x00dummy")

        with patch.object(pool, "add_session", new_callable=AsyncMock) as mock_add_session:
            verify_res = await service.verify_code(login_id, "12345")
            assert verify_res["status"] == "success"
            assert verify_res["user_id"] == 123456789
            assert verify_res["first_name"] == "Reza"
            mock_add_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_code_requires_2fa_and_password_success(tmp_path):
    settings = get_settings()
    metadata_service = MetadataService(settings)
    pool = TelethonSessionPool(settings, metadata_service)
    account_service = AccountManagementService(pool, settings)
    proxy_service = ProxyService(settings)

    service = TelegramLoginService(
        settings=settings,
        pool=pool,
        account_service=account_service,
        metadata_service=metadata_service,
        proxy_service=proxy_service
    )
    service.temp_dir = tmp_path

    mock_client = AsyncMock()
    mock_sent_code = MagicMock()
    mock_sent_code.phone_code_hash = "mock_hash_123"
    mock_client.send_code_request.return_value = mock_sent_code

    # First sign_in call raises SessionPasswordNeededError
    mock_client.sign_in.side_effect = [
        SessionPasswordNeededError(request=None),
        None  # password sign_in succeeds
    ]

    mock_client.is_user_authorized.return_value = True
    mock_me = MagicMock()
    mock_me.id = 987654321
    mock_me.first_name = "Sara"
    mock_me.last_name = ""
    mock_me.phone = "989123456789"
    mock_me.username = "saratest"
    mock_client.get_me.return_value = mock_me

    with patch("server.login_service.TelegramClient", return_value=mock_client):
        res = await service.start_login(phone="+989123456789")
        login_id = res["login_id"]

        # Temp session file
        temp_file = Path(f"{service._active_logins[login_id].temp_session_path}.session")
        temp_file.write_bytes(b"SQLite format 3\x00dummy")

        # Step 2: verify code -> 2fa_required
        verify_res = await service.verify_code(login_id, "54321")
        assert verify_res["status"] == "2fa_required"

        # Step 3: verify password -> success
        with patch.object(pool, "add_session", new_callable=AsyncMock) as mock_add_session:
            pwd_res = await service.verify_password(login_id, "my_strong_2fa_pass", save_password=True)
            assert pwd_res["status"] == "success"
            assert pwd_res["user_id"] == 987654321
            mock_add_session.assert_awaited_once()

            # Verify password was saved to account_service
            saved_pwd = account_service.get_password("+989123456789")
            assert saved_pwd == "my_strong_2fa_pass"
