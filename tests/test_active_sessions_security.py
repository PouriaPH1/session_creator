import datetime
from unittest.mock import AsyncMock, MagicMock
import pytest

from config.settings import Settings
from server.account_service import AccountManagementService



def create_mock_authorization(
    auth_hash: int,
    device_model: str,
    ip: str,
    created_delta_hours: float,
    current: bool = False,
    system_version: str = "14.0",
    app_version: str = "10.8.1",
    api_id: int = 2040,
    official_app: bool = True
):
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    date_created = now_utc - datetime.timedelta(hours=created_delta_hours)
    
    mock_auth = MagicMock()
    mock_auth.hash = auth_hash
    mock_auth.device_model = device_model
    mock_auth.platform = "Android" if "Android" in device_model else "Windows"
    mock_auth.system_version = system_version
    mock_auth.api_id = api_id
    mock_auth.app_name = "Telegram"
    mock_auth.app_version = app_version
    mock_auth.date_created = date_created
    mock_auth.date_active = now_utc
    mock_auth.ip = ip
    mock_auth.country = "Germany"
    mock_auth.region = "Bavaria"
    mock_auth.current = current
    mock_auth.official_app = official_app
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


def test_api_authorizations_not_found(client):
    res = client.get("/api/accounts/authorizations?identifier=non_existent_session_12345")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Regression tests: our own sessions must never be revoked
# ---------------------------------------------------------------------------
from telethon.tl.functions.account import ResetAuthorizationRequest
from telethon.tl.functions.auth import ResetAuthorizationsRequest

from core.device_profiler import DeviceProfileFactory

PHONE = "989123456789"


def _make_client(authorizations, connected=True):
    mock_client = AsyncMock()
    state = {"connected": connected}
    mock_client.is_connected = MagicMock(side_effect=lambda: state["connected"])

    async def _connect():
        state["connected"] = True

    async def _disconnect():
        state["connected"] = False

    mock_client.connect = AsyncMock(side_effect=_connect)
    mock_client.disconnect = AsyncMock(side_effect=_disconnect)
    mock_client.is_user_authorized = AsyncMock(return_value=True)
    me = MagicMock()
    me.phone = PHONE
    mock_client.get_me = AsyncMock(return_value=me)
    res = MagicMock()
    res.authorizations = authorizations
    mock_client.return_value = res
    return mock_client, state


def _our_profile_auth(auth_hash, created_delta_hours=30.0):
    """Looks exactly like one of OUR clients for PHONE (e.g. the migrated target session)."""
    p = DeviceProfileFactory.get_deterministic_profile(PHONE)
    return create_mock_authorization(
        auth_hash, p.device_model, "3.3.3.3", created_delta_hours,
        system_version=p.system_version, app_version=p.app_version
    )


def _sent_requests(mock_client):
    return [c.args[0] for c in mock_client.call_args_list if c.args]


@pytest.mark.asyncio
async def test_bulk_terminate_preserves_our_other_sessions_and_never_resets_all(tmp_path):
    settings = Settings(base_dir=tmp_path)
    current = create_mock_authorization(101, "Pixel 8", "1.1.1.1", 30.0, current=True)
    ours_target = _our_profile_auth(303)
    seller = create_mock_authorization(202, "Seller Phone", "2.2.2.2", 90.0)
    mock_client, _ = _make_client([current, ours_target, seller])

    pool = MagicMock()
    pool.get_client.return_value = mock_client
    pool._session_info = {PHONE: {"phone": f"+{PHONE}"}}

    svc = AccountManagementService(pool=pool, settings=settings)
    res = await svc.terminate_foreign_sessions(f"+{PHONE}")

    assert res["status"] == "success"
    assert res["terminated_count"] == 1
    assert res["preserved_count"] == 2

    sent = _sent_requests(mock_client)
    resets = [r for r in sent if isinstance(r, ResetAuthorizationRequest)]
    assert [r.hash for r in resets] == [202]
    assert not any(isinstance(r, ResetAuthorizationsRequest) for r in sent)


@pytest.mark.asyncio
async def test_failed_reset_does_not_fall_back_to_reset_all(tmp_path):
    settings = Settings(base_dir=tmp_path)
    current = create_mock_authorization(101, "Pixel 8", "1.1.1.1", 30.0, current=True)
    seller = create_mock_authorization(202, "Seller Phone", "2.2.2.2", 90.0)
    mock_client, _ = _make_client([current, seller])
    auth_res = mock_client.return_value

    async def _call(req):
        if isinstance(req, ResetAuthorizationRequest):
            raise RuntimeError("boom")
        return auth_res

    mock_client.side_effect = _call
    pool = MagicMock()
    pool.get_client.return_value = mock_client

    svc = AccountManagementService(pool=pool, settings=settings)
    res = await svc.terminate_foreign_sessions(f"+{PHONE}")

    assert res["failed_count"] == 1
    assert res["terminated_count"] == 0
    assert not any(isinstance(r, ResetAuthorizationsRequest) for r in _sent_requests(mock_client))


@pytest.mark.asyncio
async def test_single_terminate_rejects_our_session_and_unknown_hash(tmp_path):
    settings = Settings(base_dir=tmp_path)
    current = create_mock_authorization(101, "Pixel 8", "1.1.1.1", 30.0, current=True)
    ours_target = _our_profile_auth(303)
    mock_client, _ = _make_client([current, ours_target])
    pool = MagicMock()
    pool.get_client.return_value = mock_client

    svc = AccountManagementService(pool=pool, settings=settings)
    r1 = await svc.terminate_foreign_sessions(f"+{PHONE}", target_hash="303")
    r2 = await svc.terminate_foreign_sessions(f"+{PHONE}", target_hash="999")
    r3 = await svc.terminate_foreign_sessions(f"+{PHONE}", target_hash="101")

    assert r1["status"] == r2["status"] == r3["status"] == "error"
    assert not any(isinstance(r, ResetAuthorizationRequest) for r in _sent_requests(mock_client))


@pytest.mark.asyncio
async def test_own_api_id_session_is_protected(tmp_path):
    settings = Settings(base_dir=tmp_path)
    current = create_mock_authorization(101, "Pixel 8", "1.1.1.1", 30.0, current=True)
    ours = create_mock_authorization(404, "Other", "4.4.4.4", 40.0, api_id=settings.api_id, official_app=False)
    mock_client, _ = _make_client([current, ours])
    pool = MagicMock()
    pool.get_client.return_value = mock_client

    svc = AccountManagementService(pool=pool, settings=settings)
    data = await svc.get_session_authorizations(f"+{PHONE}")
    assert data["foreign_sessions_count"] == 0


@pytest.mark.asyncio
async def test_pool_client_is_disconnected_again_after_use(tmp_path):
    settings = Settings(base_dir=tmp_path)
    current = create_mock_authorization(101, "Pixel 8", "1.1.1.1", 30.0, current=True)
    mock_client, state = _make_client([current], connected=False)
    pool = MagicMock()
    pool.get_client.return_value = mock_client

    svc = AccountManagementService(pool=pool, settings=settings)
    await svc.get_session_authorizations(f"+{PHONE}")

    mock_client.connect.assert_awaited()
    mock_client.disconnect.assert_awaited()
    assert state["connected"] is False


@pytest.mark.asyncio
async def test_pool_client_already_connected_is_left_connected(tmp_path):
    settings = Settings(base_dir=tmp_path)
    current = create_mock_authorization(101, "Pixel 8", "1.1.1.1", 30.0, current=True)
    mock_client, state = _make_client([current], connected=True)
    pool = MagicMock()
    pool.get_client.return_value = mock_client

    svc = AccountManagementService(pool=pool, settings=settings)
    await svc.get_session_authorizations(f"+{PHONE}")

    mock_client.disconnect.assert_not_awaited()
    assert state["connected"] is True


@pytest.mark.asyncio
async def test_target_kind_uses_target_file_not_pool_and_disconnects(tmp_path):
    settings = Settings(base_dir=tmp_path)
    settings.target_sessions_dir.mkdir(parents=True, exist_ok=True)
    (settings.target_sessions_dir / f"+{PHONE}.session").write_bytes(b"x")

    current = create_mock_authorization(101, "Pixel 8", "1.1.1.1", 30.0, current=True)
    file_client, state = _make_client([current], connected=False)
    pool_client, _ = _make_client([current])
    pool = MagicMock()
    pool.get_client.return_value = pool_client

    svc = AccountManagementService(pool=pool, settings=settings)
    svc._build_file_client = MagicMock(return_value=file_client)

    data = await svc.get_session_authorizations(f"+{PHONE}.session", kind="target")

    assert data["status"] == "success"
    svc._build_file_client.assert_called_once()
    pool_client.assert_not_called()
    assert state["connected"] is False


def test_no_background_auto_termination(client):
    from server.main import account_service
    assert not hasattr(account_service, "start_auto_terminator_loop")
    assert not hasattr(account_service, "run_scheduled_auto_terminations")
    res = client.post("/api/accounts/schedule-terminate", json={"identifier": "+989998887766", "enable": True})
    assert res.status_code in (404, 405)
