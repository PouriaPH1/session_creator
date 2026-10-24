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
