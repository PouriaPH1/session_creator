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
        self.status_text = "آماده به کار"
        self.current_phone: Optional[str] = None
        self.progress: int = 0
        self.total: int = 0
        self.results: List[Dict] = []
        self.logs: List[str] = []
        self._lock = asyncio.Lock()

    def get_status(self) -> Dict:
        return {
            "is_running": self.is_running,
            "status_text": self.status_text,
            "progress": self.progress,
            "total": self.total,
            "current_phone": self.current_phone,
            "results": self.results,
            "logs": self.logs[-50:]  # last 50 log messages
        }

    def reset(self) -> None:
        """Force resets job state if stuck."""
        self.is_running = False
        self.current_phone = None
        self.status_text = "آماده به کار"
        self._add_log("⚠️ وضعیت عملیات به صورت دستی بازنشانی (ریست) شد.")

    def _add_log(self, message: str) -> None:
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        entry = f"[{now_str}] {message}"
        self.logs.append(entry)
        logger.info(entry)

    async def start_batch_migration(
        self,
        phones: List[str],
        two_fa_passwords: Optional[Dict[str, str]] = None,
        delay: float = 15.0,
        proxy: Optional[str] = None
    ) -> bool:
        """Starts batch migration in the background."""
        async with self._lock:
            if self.is_running:
                return False
            self.is_running = True
            self.status_text = "شروع عملیات..."
            self.progress = 0
            self.total = len(phones)
            self.results = []
            self.logs = []
            self.current_phone = None

        asyncio.create_task(self._run_batch_worker(phones, two_fa_passwords or {}, delay, proxy))
        return True

    async def _run_batch_worker(
        self,
        phones: List[str],
        passwords: Dict[str, str],
        delay: float,
        proxy: Optional[str] = None
    ) -> None:
        try:
            from server.proxy_service import parse_telethon_proxy
            parsed_proxy = parse_telethon_proxy(proxy) if proxy else None

            creator = TelegramSessionCreator(
                settings=self.settings,
                otp_client=self.local_otp_client,
                proxy=parsed_proxy
            )
            if proxy:
                self._add_log(f"🌐 اتصال از طریق پروکسی: {proxy}")
            self._add_log(f"Starting batch migration for {len(phones)} accounts...")

            for idx, phone in enumerate(phones, start=1):
                self.current_phone = phone
                self.status_text = f"در حال پردازش {phone} ({idx}/{len(phones)})..."
                pwd = (
                    passwords.get(phone)
                    or passwords.get(phone.lstrip("+"))
                    or passwords.get(f"+{phone.lstrip('+')}")
                )
                if not pwd:
                    passwords_file = self.settings.base_dir / "storage" / "passwords.json"
                    if passwords_file.exists():
                        try:
                            import json
                            saved_data = json.loads(passwords_file.read_text(encoding="utf-8"))
                            for k in [phone, phone.lstrip("+"), f"+{phone.lstrip('+')}"]:
                                if k in saved_data:
                                    item = saved_data[k]
                                    pwd = item.get("password") if isinstance(item, dict) else item
                                    if pwd:
                                        break
                        except Exception as e:
                            logger.warning(f"Failed to read 2FA password from storage: {e}")

                try:
                    target_file = await creator.create_single_session(
                        phone=phone,
                        two_fa_password=pwd
                    )

                    # Determine proxy info for metadata
                    proxy_name = "مستقیم (سرور اول)"
                    proxy_ip = "آی‌پی سرور اول"
                    if proxy:
                        proxy_name = "پروکسی"
                        proxy_ip = proxy
                        try:
                            from server.proxy_service import ProxyService
                            ps = ProxyService(self.settings)
                            for p in ps.get_all():
                                if p.get("url") == proxy:
                                    proxy_name = p.get("name") or "پروکسی"
                                    proxy_ip = p.get("ip") or proxy
                                    break
                        except Exception:
                            pass

                    # Record metadata in storage/target_sessions_metadata.json
                    meta_file = self.settings.base_dir / "storage" / "target_sessions_metadata.json"
                    try:
                        import json
                        meta_data = {}
                        if meta_file.exists():
                            try:
                                meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
                            except Exception:
                                meta_data = {}
                        meta_data[target_file.name] = {
                            "phone": phone,
                            "session_name": target_file.stem,
                            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "created_via": "proxy" if proxy else "direct",
                            "proxy_name": proxy_name,
                            "proxy_ip": proxy_ip,
                            "proxy_url": proxy
                        }
                        meta_file.write_text(json.dumps(meta_data, ensure_ascii=False, indent=2), encoding="utf-8")
                    except Exception as meta_err:
                        logger.warning(f"Failed to save target session metadata: {meta_err}")

                    self.results.append({
                        "phone": phone,
                        "success": True,
                        "detail": f"Saved {target_file.name}",
                        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    })
                    self._add_log(f"[{idx}/{len(phones)}] ✅ Success for {phone} ({proxy_name})")

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
                    self.status_text = f"مکث {delay} ثانیه‌ای..."
                    self._add_log(f"Waiting {delay}s cooldown before next session...")
                    await asyncio.sleep(delay)

            self._add_log("🎉 Batch migration finished!")
            self.status_text = "تکمیل شد"
        except Exception as e:
            self._add_log(f"💥 Fatal error during migration: {e}")
            logger.error(f"Fatal error in _run_batch_worker: {e}", exc_info=True)
            self.status_text = f"خطا: {e}"
        finally:
            self.is_running = False
            self.current_phone = None
