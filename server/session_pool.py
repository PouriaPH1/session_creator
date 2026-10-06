import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional
from telethon import TelegramClient
from telethon.errors import AuthKeyError, UserDeactivatedError

from config.settings import Settings
from core.device_profiler import DeviceProfileFactory
from core.interfaces import ISessionPool

logger = logging.getLogger(__name__)


class TelethonSessionPool(ISessionPool):
    """
    Manages a pool of active Telethon sessions loaded from .session files.
    Implements Object Pool and Repository patterns.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        # Map identifier (phone or filename stem) -> TelegramClient
        self._clients: Dict[str, TelegramClient] = {}
        # Keep track of active phone numbers
        self._phone_to_id: Dict[str, str] = {}
        self._session_info: Dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Scan directory and initialize all available session files."""
        async with self._lock:
            await self._load_all_sessions()

    async def stop(self) -> None:
        """Gracefully disconnect all active clients."""
        async with self._lock:
            for session_id, client in list(self._clients.items()):
                try:
                    if client.is_connected():
                        await client.disconnect()
                        logger.info(f"Disconnected session: {session_id}")
                except Exception as e:
                    logger.error(f"Error disconnecting {session_id}: {e}")
            self._clients.clear()
            self._phone_to_id.clear()
            self._session_info.clear()

    async def _load_all_sessions(self) -> None:
        session_files = list(self.settings.source_sessions_dir.glob("*.session"))
        if not session_files:
            logger.warning(
                f"No .session files found in {self.settings.source_sessions_dir}. "
                "Place source Telethon session files there."
            )
            return

        logger.info(f"Found {len(session_files)} session file(s). Initializing...")

        for session_path in session_files:
            session_name = session_path.stem
            await self._init_single_session(session_path, session_name)

    async def _init_single_session(self, session_path: Path, session_name: str) -> None:
        profile = DeviceProfileFactory.get_deterministic_profile(session_name)
        client = TelegramClient(
            str(session_path),
            api_id=self.settings.api_id,
            api_hash=self.settings.api_hash,
            device_model=profile.device_model,
            system_version=profile.system_version,
            app_version=profile.app_version,
            lang_code=profile.lang_code,
            system_lang_code=profile.system_lang_code
        )

        try:
            await client.connect()
            if not await client.is_user_authorized():
                logger.warning(f"Session {session_name} is not authorized! Skipping.")
                self._session_info[session_name] = {
                    "session_name": session_name,
                    "phone": "N/A",
                    "user_id": None,
                    "first_name": "Unauthorized",
                    "username": None,
                    "device_model": profile.device_model,
                    "is_authorized": False,
                    "status": "unauthorized"
                }
                await client.disconnect()
                return

            me = await client.get_me()
            phone = f"+{me.phone}" if me.phone and not me.phone.startswith("+") else (me.phone or "")

            self._clients[session_name] = client
            clean_phone = phone.replace(" ", "") if phone else ""
            if clean_phone:
                self._clients[clean_phone] = client
                no_plus_phone = clean_phone.lstrip("+")
                self._clients[no_plus_phone] = client
                self._phone_to_id[clean_phone] = session_name

            self._session_info[session_name] = {
                "session_name": session_name,
                "phone": clean_phone or "Unknown",
                "user_id": me.id,
                "first_name": me.first_name or "",
                "username": me.username or "",
                "device_model": profile.device_model,
                "is_authorized": True,
                "status": "active"
            }

            logger.info(f"Loaded session [{session_name}] (Phone: {clean_phone}, User ID: {me.id})")

        except (AuthKeyError, UserDeactivatedError) as e:
            logger.error(f"Session {session_name} is invalid/deactivated: {e}")
            self._session_info[session_name] = {
                "session_name": session_name,
                "phone": "Deactivated",
                "user_id": None,
                "first_name": "Error",
                "username": None,
                "device_model": profile.device_model,
                "is_authorized": False,
                "status": "deactivated"
            }
            if client.is_connected():
                await client.disconnect()
        except Exception as e:
            logger.error(f"Failed to initialize session {session_name}: {e}")
            self._session_info[session_name] = {
                "session_name": session_name,
                "phone": "Error",
                "user_id": None,
                "first_name": "Failed",
                "username": None,
                "device_model": profile.device_model,
                "is_authorized": False,
                "status": "error"
            }
            if client.is_connected():
                await client.disconnect()

    def get_client(self, identifier: str) -> Optional[TelegramClient]:
        """Lookup by session filename, formatted phone, or digits-only phone."""
        clean_id = identifier.strip().replace(" ", "")
        client = self._clients.get(clean_id)
        if not client:
            client = self._clients.get(clean_id.lstrip("+"))
        if not client:
            client = self._clients.get(f"+{clean_id}")
        return client

    def list_sessions(self) -> List[str]:
        """List distinct session names loaded."""
        distinct_names = set(self._phone_to_id.values()) | {
            k for k in self._clients.keys() if not k.startswith("+") and not k.isdigit()
        }
        return sorted(list(distinct_names))

    def get_detailed_sessions(self) -> List[dict]:
        """Returns list of all session metadata dictionaries."""
        return list(self._session_info.values())

    async def reload(self) -> None:
        """Reload sessions from directory."""
        await self.stop()
        await self.start()
