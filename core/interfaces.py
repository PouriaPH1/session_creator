from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from telethon import TelegramClient


class ISessionPool(ABC):
    """Abstract interface for managing a pool of master Telegram sessions."""

    @abstractmethod
    async def start(self) -> None:
        """Start and connect all sessions in the pool."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully disconnect all sessions in the pool."""
        pass

    @abstractmethod
    def get_client(self, identifier: str) -> Optional[TelegramClient]:
        """Retrieve a TelegramClient by phone number or session name."""
        pass

    @abstractmethod
    def list_sessions(self) -> List[str]:
        """List all active session identifiers."""
        pass


class IOTPService(ABC):
    """Abstract interface for extracting Telegram login OTP codes."""

    @abstractmethod
    async def get_latest_otp(
        self,
        identifier: str,
        since_timestamp: Optional[float] = None
    ) -> Optional[str]:
        """
        Fetch the most recent login code for the given session identifier.
        Optionally filter by arrival timestamp after since_timestamp.
        """
        pass


class IOTPClient(ABC):
    """Abstract interface for worker client querying OTP from master server."""

    @abstractmethod
    async def fetch_otp(
        self,
        phone: str,
        since_timestamp: float,
        timeout: float,
        interval: float
    ) -> str:
        """Poll the master server until OTP is found or timeout expires."""
        pass
