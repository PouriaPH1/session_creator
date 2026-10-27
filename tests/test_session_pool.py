import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from config.settings import Settings
from server.session_pool import TelethonSessionPool


@pytest.mark.asyncio
async def test_session_pool_handles_corrupt_and_empty_files(tmp_path):
    # Setup temporary directory with corrupt, empty, and non-sqlite files
    source_dir = tmp_path / "source_sessions"
    source_dir.mkdir(parents=True, exist_ok=True)

    # 1. Empty file (0 bytes)
    empty_file = source_dir / "empty.session"
    empty_file.write_bytes(b"")

    # 2. Corrupt / text file renamed to .session
    text_file = source_dir / "not_sqlite.session"
    text_file.write_bytes(b"This is just plain text, not a sqlite database")

    # 3. Corrupt sqlite header
    corrupt_file = source_dir / "corrupt_header.session"
    corrupt_file.write_bytes(b"SQLite format 3\x00corrupt garbage data")

    settings = MagicMock(spec=Settings)
    settings.source_sessions_dir = source_dir
    settings.api_id = 12345
    settings.api_hash = "abcdef"

    pool = TelethonSessionPool(settings=settings)

    # Calling start should NOT raise an exception or crash
    await pool.start()

    sessions = pool.get_detailed_sessions()
    assert len(sessions) == 3

    # All corrupt sessions should be categorized with status 'error'
    for s in sessions:
        assert s["status"] == "error"
        assert s["is_authorized"] is False

    await pool.stop()


@pytest.mark.asyncio
async def test_session_pool_disconnect_and_archive(tmp_path):
    source_dir = tmp_path / "source_sessions"
    source_dir.mkdir(parents=True, exist_ok=True)
    session_file = source_dir / "+989123456789.session"
    session_file.write_bytes(b"SQLite format 3\x00dummy")
    journal_file = source_dir / "+989123456789.session-journal"
    journal_file.write_bytes(b"dummy journal")

    settings = MagicMock(spec=Settings)
    settings.base_dir = tmp_path
    settings.source_sessions_dir = source_dir
    settings.api_id = 12345
    settings.api_hash = "abcdef"

    pool = TelethonSessionPool(settings=settings)

    # Mock client in pool
    mock_client = MagicMock()
    mock_client.disconnect = AsyncMock()
    mock_client.is_connected.return_value = True
    pool._clients["+989123456789"] = mock_client
    pool._clients["989123456789"] = mock_client
    pool._session_info["+989123456789"] = {
        "session_name": "+989123456789",
        "phone": "+989123456789",
        "status": "active",
        "is_authorized": True
    }

    # 1. Test disconnect_client
    res = await pool.disconnect_client("+989123456789")
    assert res is True
    mock_client.disconnect.assert_awaited_once()

    # 2. Test archive_session
    archived_path = await pool.archive_session("+989123456789")
    assert archived_path is not None
    assert archived_path.exists()
    assert not session_file.exists()
    assert (tmp_path / "storage" / "migrated_sources" / "+989123456789.session").exists()
    assert pool._session_info["+989123456789"]["status"] == "migrated"
    assert pool.get_client("+989123456789") is None

    await pool.stop()
