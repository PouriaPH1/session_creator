import datetime
from unittest.mock import AsyncMock, MagicMock
import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from server.account_service import AccountManagementService
from server.main import app

client = TestClient(app)


def create_mock_authorization(
    auth_hash: int,
    device_model: str,
    ip: str,
    created_delta_hours: float,
    current: bool = False
):
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    date_created = now_utc - datetime.timedelta(hours=created_delta_hours)
    
    mock_auth = MagicMock()
    mock_auth.hash = auth_hash
    mock_auth.device_model = device_model
    mock_auth.platform = "Android" if "Android" in device_model else "Windows"
    mock_auth.system_version = "14.0"
    mock_auth.api_id = 2040
    mock_auth.app_name = "Telegram"
    mock_auth.app_version = "10.8.1"
    mock_auth.date_created = date_created
    mock_auth.date_active = now_utc
    mock_auth.ip = ip
    mock_auth.country = "Germany"
    mock_auth.region = "Bavaria"
    mock_auth.current = current
    mock_auth.official_app = True
    return mock_auth


@pytest.mark.asyncio
async def test_get_session_authorizations_under_24_hours(tmp_path):
    settings = Settings(base_dir=tmp_path)
    mock_pool = MagicMock()
    mock_client = AsyncMock()
    mock_client.is_connected = MagicMock(return_value=True)
    
    # User object
    mock_me = MagicMock()
    mock_me.phone = "989123456789"
    mock_client.get_me = AsyncMock(return_value=mock_me)

    # Authorizations:
    # 1. Current session created 5 hours ago (FRESH)
    # 2. Foreign old session created 72 hours ago (seller session)
    auth1 = create_mock_authorization(101, "Google Pixel 8", "1.1.1.1", created_delta_hours=5.0, current=True)
    auth2 = create_mock_authorization(202, "Samsung Galaxy S20", "2.2.2.2", created_delta_hours=72.0, current=False)

    mock_res = MagicMock()
    mock_res.authorizations = [auth1, auth2]
    mock_client.return_value = mock_res

    mock_pool.get_client.return_value = mock_client
    mock_pool._clients = {"+989123456789": mock_client}

    svc = AccountManagementService(pool=mock_pool, settings=settings)
    data = await svc.get_session_authorizations("+989123456789")

    assert data["status"] == "success"
    assert data["is_eligible"] is False
    assert 18.0 <= data["hours_remaining"] <= 19.5
    assert data["total_count"] == 2
    assert data["our_sessions_count"] == 1
    assert data["foreign_sessions_count"] == 1
    
    # Check that our session is marked properly
    current_auth = next(a for a in data["authorizations"] if a["is_current"])
    assert current_auth["is_our_session"] is True
    assert current_auth["can_terminate"] is False

    foreign_auth = next(a for a in data["authorizations"] if not a["is_current"])
    assert foreign_auth["is_our_session"] is False
    # Because < 24h, can_terminate should be False
    assert foreign_auth["can_terminate"] is False


@pytest.mark.asyncio
async def test_get_session_authorizations_over_24_hours(tmp_path):
    settings = Settings(base_dir=tmp_path)
    mock_pool = MagicMock()
    mock_client = AsyncMock()
    mock_client.is_connected = MagicMock(return_value=True)
    
    mock_me = MagicMock()
    mock_me.phone = "989123456789"
    mock_client.get_me = AsyncMock(return_value=mock_me)

    # Current session created 30 hours ago (mature)
    auth1 = create_mock_authorization(101, "Google Pixel 8", "1.1.1.1", created_delta_hours=30.0, current=True)
    auth2 = create_mock_authorization(202, "Samsung Galaxy S20", "2.2.2.2", created_delta_hours=90.0, current=False)

    mock_res = MagicMock()
    mock_res.authorizations = [auth1, auth2]
    mock_client.return_value = mock_res

    mock_pool.get_client.return_value = mock_client
    mock_pool._clients = {"+989123456789": mock_client}

    svc = AccountManagementService(pool=mock_pool, settings=settings)
    data = await svc.get_session_authorizations("+989123456789")

    assert data["status"] == "success"
    assert data["is_eligible"] is True
    assert data["hours_remaining"] == 0.0

    foreign_auth = next(a for a in data["authorizations"] if not a["is_current"])
    assert foreign_auth["is_our_session"] is False
    assert foreign_auth["can_terminate"] is True


@pytest.mark.asyncio
async def test_terminate_foreign_sessions_guard_and_execution(tmp_path):
    settings = Settings(base_dir=tmp_path)
    mock_pool = MagicMock()
    mock_client = AsyncMock()
    mock_client.is_connected = MagicMock(return_value=True)

    mock_me = MagicMock()
    mock_me.phone = "989123456789"
    mock_client.get_me = AsyncMock(return_value=mock_me)

    # Fresh session (< 24h): terminate should return forbidden
    auth1_fresh = create_mock_authorization(101, "Pixel 8", "1.1.1.1", created_delta_hours=10.0, current=True)
    auth2_old = create_mock_authorization(202, "Old Phone", "2.2.2.2", created_delta_hours=50.0, current=False)

    mock_res_fresh = MagicMock()
    mock_res_fresh.authorizations = [auth1_fresh, auth2_old]
    mock_client.return_value = mock_res_fresh

    mock_pool.get_client.return_value = mock_client
    mock_pool._clients = {"+989123456789": mock_client}

    svc = AccountManagementService(pool=mock_pool, settings=settings)

    # Attempt terminate when fresh
    res_forbidden = await svc.terminate_foreign_sessions("+989123456789")
    assert res_forbidden["status"] == "forbidden"
    assert res_forbidden["hours_remaining"] > 0

    # Now mature session (> 24h)
    auth1_mature = create_mock_authorization(101, "Pixel 8", "1.1.1.1", created_delta_hours=26.0, current=True)
    mock_res_mature = MagicMock()
    mock_res_mature.authorizations = [auth1_mature, auth2_old]
    mock_client.return_value = mock_res_mature

    res_success = await svc.terminate_foreign_sessions("+989123456789")
    assert res_success["status"] == "success"
    assert res_success["terminated_count"] == 1
    assert res_success["preserved_count"] == 1


def test_schedule_terminate_lifecycle(tmp_path):
    settings = Settings(base_dir=tmp_path)
    mock_pool = MagicMock()
    svc = AccountManagementService(pool=mock_pool, settings=settings)

    ident = "+989111222333"
    assert svc.is_task_scheduled(ident) is False

    # Schedule
    res = svc.set_scheduled_task(ident, enable=True)
    assert res["status"] == "scheduled"
    assert svc.is_task_scheduled(ident) is True

    # Cancel
    res2 = svc.set_scheduled_task(ident, enable=False)
    assert res2["status"] == "cancelled"
    assert svc.is_task_scheduled(ident) is False


def test_api_schedule_terminate():
    res = client.post("/api/accounts/schedule-terminate", json={"identifier": "+989998887766", "enable": True})
    assert res.status_code == 200
    assert res.json()["status"] == "scheduled"

    res_cancel = client.post("/api/accounts/schedule-terminate", json={"identifier": "+989998887766", "enable": False})
    assert res_cancel.status_code == 200
    assert res_cancel.json()["status"] == "cancelled"


def test_api_authorizations_not_found():
    res = client.get("/api/accounts/authorizations?identifier=non_existent_session_12345")
    assert res.status_code == 404

