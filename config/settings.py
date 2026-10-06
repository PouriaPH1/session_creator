from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Telegram API
    api_id: int = 12345678
    api_hash: str = "your_api_hash_here"

    # Server Configuration
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    server_url: str = "http://127.0.0.1:8000"

    # Directory Paths
    base_dir: Path = Path(__file__).resolve().parent.parent
    source_sessions_dir: Path = base_dir / "storage" / "source_sessions"
    target_sessions_dir: Path = base_dir / "storage" / "target_sessions"

    # Worker Timing & Limits
    otp_poll_timeout: float = 60.0
    otp_poll_interval: float = 2.5
    batch_sleep_delay: float = 15.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


@lru_cache()
def get_settings() -> Settings:
    settings = Settings()
    settings.source_sessions_dir.mkdir(parents=True, exist_ok=True)
    settings.target_sessions_dir.mkdir(parents=True, exist_ok=True)
    return settings
