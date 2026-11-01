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

    def __init__(self, settings: Settings, metadata_service: Optional[object] = None):
        self.settings = settings
        self.metadata_service = metadata_service
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

        logger.info(f"Found {len(session_files)} session file(s). Initializing concurrently...")

        # Pre-populate session_info so UI can display placeholder right away
        for session_path in session_files:
            s_name = session_path.stem
            if s_name not in self._session_info:
                self._session_info[s_name] = {
                    "session_name": s_name,
                    "phone": s_name,
                    "user_id": None,
                    "first_name": "Connecting...",
                    "username": None,
                    "device_model": "Unknown",
                    "is_authorized": False,
                    "status": "loading"
                }

        sem = asyncio.Semaphore(10)

        async def _init_worker(path: Path):
            async with sem:
                name = path.stem
                try:
                    await self._init_single_session(path, name)
                except Exception as e:
                    logger.error(f"Unexpected error loading session file {path.name}: {e}")

        await asyncio.gather(*[_init_worker(p) for p in session_files])
        logger.info(f"Finished initializing all {len(session_files)} session files.")

    async def _init_single_session(self, session_path: Path, session_name: str) -> None:
        client: Optional[TelegramClient] = None
        profile = DeviceProfileFactory.get_deterministic_profile(session_name)

        try:
            # Pre-validate file existence and SQLite format
            if not session_path.exists():
                logger.error(f"Session file does not exist: {session_path}")
                return

            if session_path.stat().st_size == 0:
                raise ValueError("Session file is empty (0 bytes).")

            with open(session_path, "rb") as f:
                header = f.read(16)
                if header != b"SQLite format 3\x00":
                    raise ValueError("File is not a valid SQLite database (missing SQLite header).")

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

            # Idle disconnect: disconnect from Telegram to avoid dozens of persistent sockets from 1 datacenter IP.
            # Telethon sessions stay authorized on disk and will reconnect seamlessly on-demand.
            await client.disconnect()

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
            if client and client.is_connected():
                try:
                    await client.disconnect()
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Failed to initialize session {session_name} ({session_path.name}): {e}")
            self._session_info[session_name] = {
                "session_name": session_name,
                "phone": "Error",
                "user_id": None,
                "first_name": "Corrupted/Error",
                "username": None,
                "device_model": profile.device_model,
                "is_authorized": False,
                "status": "error"
            }
            if client and client.is_connected():
                try:
                    await client.disconnect()
                except Exception:
                    pass

    def get_client(self, identifier: str) -> Optional[TelegramClient]:
        """Lookup by session filename, formatted phone, or digits-only phone."""
        clean_id = identifier.strip().replace(" ", "")
        client = self._clients.get(clean_id)
        if not client:
            client = self._clients.get(clean_id.lstrip("+"))
        if not client:
            client = self._clients.get(f"+{clean_id}")
        return client

    async def disconnect_client(self, identifier: str) -> bool:
        """
        Gracefully disconnects an individual client by phone or session name.
        Prevents simultaneous dual-IP connections when target sessions are active.
        """
        client = self.get_client(identifier)
        if client and client.is_connected():
            try:
                await client.disconnect()
                logger.info(f"Disconnected client: {identifier}")
                return True
            except Exception as e:
                logger.warning(f"Error disconnecting client {identifier}: {e}")
        return False

    async def archive_session(self, identifier: str) -> Optional[Path]:
        """
        Disconnects and moves a source session file to storage/migrated_sources
        so that it won't be reloaded or conflict with target sessions in the future.
        """
        import shutil

        clean_id = identifier.strip().replace(" ", "").lstrip("+")
        await self.disconnect_client(identifier)

        # Locate file in source_sessions_dir
        source_files = list(self.settings.source_sessions_dir.glob("*.session"))
        target_file = None
        for f in source_files:
            stem_clean = f.stem.lstrip("+")
            if stem_clean == clean_id or f.stem == identifier:
                target_file = f
                break

        if not target_file or not target_file.exists():
            return None

        archive_dir = self.settings.base_dir / "storage" / "migrated_sources"
        archive_dir.mkdir(parents=True, exist_ok=True)

        dest_file = archive_dir / target_file.name
        shutil.move(str(target_file), str(dest_file))

        # Also move journal if exists
        journal = target_file.with_name(f"{target_file.name}-journal")
        if journal.exists():
            try:
                shutil.move(str(journal), str(archive_dir / journal.name))
            except Exception:
                pass

        # Remove from active clients mapping
        for k in [identifier, clean_id, f"+{clean_id}", target_file.stem]:
            self._clients.pop(k, None)

        # Update cached session info status
        for s_name, info in self._session_info.items():
            if info.get("phone") in (identifier, clean_id, f"+{clean_id}") or s_name == target_file.stem:
                info["status"] = "migrated"
                info["is_authorized"] = False

        logger.info(f"Archived source session {target_file.name} to {dest_file}")
        return dest_file

    def list_sessions(self) -> List[str]:
        """List distinct session names loaded."""
        distinct_names = set(self._phone_to_id.values()) | {
            k for k in self._clients.keys() if not k.startswith("+") and not k.isdigit()
        }
        return sorted(list(distinct_names))

    def get_detailed_sessions(self) -> List[dict]:
        """Returns list of all session metadata dictionaries."""
        sessions = []
        for info in self._session_info.values():
            s = dict(info)
            if self.metadata_service:
                meta = self.metadata_service.get_source_metadata(s.get("phone") or s.get("session_name"))
                s["category"] = meta.get("category", "")
                s["remark"] = meta.get("remark", "")
            else:
                s["category"] = ""
                s["remark"] = ""
            sessions.append(s)
        return sessions

    async def add_session(self, session_path: Path) -> dict:
        """
        Dynamically initializes and registers a newly added session file
        into the active pool without needing a full reload.
        """
        async with self._lock:
            session_name = session_path.stem
            await self._init_single_session(session_path, session_name)
            info = dict(self._session_info.get(session_name, {}))
            if self.metadata_service:
                meta = self.metadata_service.get_source_metadata(info.get("phone") or session_name)
                info["category"] = meta.get("category", "")
                info["remark"] = meta.get("remark", "")
            else:
                info["category"] = ""
                info["remark"] = ""
            return info

    async def reload(self) -> None:
        """Reload sessions from directory."""
        await self.stop()
        await self.start()
