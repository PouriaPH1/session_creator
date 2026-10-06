import asyncio
import datetime
import logging
import time
from typing import Dict, List, Optional
from core.exceptions import OTPTimeoutError
from core.interfaces import IOTPClient, IOTPService
from worker.session_creator import TelegramSessionCreator
from config.settings import Settings

logger = logging.getLogger(__name__)


class LocalOTPClient(IOTPClient):
    """
    Adapter Pattern: In-memory OTP client connecting directly to the local OTP service
    without HTTP network overhead when running migrations from the Web Admin Panel.
    """

    def __init__(self, otp_service: IOTPService):
        self.otp_service = otp_service

    async def fetch_otp(
        self,
        phone: str,
        since_timestamp: float,
        timeout: float = 60.0,
        interval: float = 2.5
    ) -> str:
        start_time = time.time()
        while (time.time() - start_time) < timeout:
            res = await self.otp_service.get_latest_otp(phone, since_timestamp=since_timestamp)
            if res:
                code, _ = res
                return code
            await asyncio.sleep(interval)

        raise OTPTimeoutError(f"Timed out after {timeout}s waiting for OTP for {phone}")


class MigrationJobManager:
    """
    Manages background migration jobs triggered from the Web Admin Panel.
    """

    def __init__(self, settings: Settings, otp_service: IOTPService):
        self.settings = settings
        self.otp_service = otp_service
        self.local_otp_client = LocalOTPClient(otp_service)
        self.is_running = False
        self.current_phone: Optional[str] = None
        self.progress: int = 0
        self.total: int = 0
        self.results: List[Dict] = []
        self.logs: List[str] = []
        self._lock = asyncio.Lock()

    def get_status(self) -> Dict:
        return {
            "is_running": self.is_running,
            "progress": self.progress,
            "total": self.total,
            "current_phone": self.current_phone,
            "results": self.results,
            "logs": self.logs[-50:]  # last 50 log messages
        }

    def _add_log(self, message: str) -> None:
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        entry = f"[{now_str}] {message}"
        self.logs.append(entry)
        logger.info(entry)

    async def start_batch_migration(
        self,
        phones: List[str],
        two_fa_passwords: Optional[Dict[str, str]] = None,
        delay: float = 15.0
    ) -> bool:
        """Starts batch migration in the background."""
        async with self._lock:
            if self.is_running:
                return False
            self.is_running = True
            self.progress = 0
            self.total = len(phones)
            self.results = []
            self.logs = []
            self.current_phone = None

        asyncio.create_task(self._run_batch_worker(phones, two_fa_passwords or {}, delay))
        return True

    async def _run_batch_worker(
        self,
        phones: List[str],
        passwords: Dict[str, str],
        delay: float
    ) -> None:
        creator = TelegramSessionCreator(
            settings=self.settings,
            otp_client=self.local_otp_client
        )
        self._add_log(f"Starting batch migration for {len(phones)} accounts...")

        for idx, phone in enumerate(phones, start=1):
            self.current_phone = phone
            self._add_log(f"[{idx}/{len(phones)}] Requesting migration for {phone}...")
            pwd = passwords.get(phone)

            try:
                target_file = await creator.create_single_session(
                    phone=phone,
                    two_fa_password=pwd
                )
                self.results.append({
                    "phone": phone,
                    "success": True,
                    "detail": f"Saved {target_file.name}",
                    "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
                self._add_log(f"[{idx}/{len(phones)}] ✅ Success for {phone}")

            except Exception as e:
                self.results.append({
                    "phone": phone,
                    "success": False,
                    "detail": str(e),
                    "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
                self._add_log(f"[{idx}/{len(phones)}] ❌ Failed for {phone}: {e}")

            self.progress = idx

            if idx < len(phones):
                self._add_log(f"Waiting {delay}s cooldown before next session...")
                await asyncio.sleep(delay)

        self._add_log("🎉 Batch migration finished!")
        self.is_running = False
        self.current_phone = None
