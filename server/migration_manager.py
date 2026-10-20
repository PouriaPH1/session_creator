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

    def __init__(self, settings: Settings, otp_service: IOTPService, metadata_service: Optional[object] = None):
        self.settings = settings
        self.otp_service = otp_service
        self.metadata_service = metadata_service
        self.local_otp_client = LocalOTPClient(otp_service)
        self.is_running = False
        self.status_text = "آماده به کار"
        self.current_phone: Optional[str] = None
        self.progress: int = 0
        self.total: int = 0
        self.results: List[Dict] = []
        self.logs: List[str] = []
        self._lock = asyncio.Lock()
        self._current_task: Optional[asyncio.Task] = None
        self.waiting_2fa: bool = False
        self.waiting_2fa_phone: Optional[str] = None
        self.waiting_2fa_is_retry: bool = False
        self._2fa_event: Optional[asyncio.Event] = None
        self._2fa_input_password: Optional[str] = None
        self._2fa_skip: bool = False

    def get_status(self) -> Dict:
        return {
            "is_running": self.is_running,
            "status_text": self.status_text,
            "progress": self.progress,
            "total": self.total,
            "current_phone": self.current_phone,
            "waiting_2fa": self.waiting_2fa,
            "waiting_2fa_phone": self.waiting_2fa_phone,
            "waiting_2fa_is_retry": self.waiting_2fa_is_retry,
            "results": self.results,
            "logs": self.logs[-50:]  # last 50 log messages
        }

    def reset(self) -> None:
        """Force resets job state if stuck."""
        if self._2fa_event and not self._2fa_event.is_set():
            self._2fa_skip = True
            self._2fa_event.set()
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
        self.is_running = False
        self.current_phone = None
        self.waiting_2fa = False
        self.waiting_2fa_phone = None
        self.waiting_2fa_is_retry = False
        self._2fa_event = None
        self._2fa_input_password = None
        self._2fa_skip = False
        self.status_text = "آماده به کار"
        self._add_log("⚠️ وضعیت عملیات به صورت دستی بازنشانی (ریست) شد.")

    async def request_2fa_password(self, phone: str, is_retry: bool = False) -> Optional[str]:
        event = asyncio.Event()
        self.waiting_2fa = True
        self.waiting_2fa_phone = phone
        self.waiting_2fa_is_retry = is_retry
        self._2fa_event = event
        self._2fa_input_password = None
        self._2fa_skip = False

        if is_retry:
            self.status_text = f"⚠️ رمز ۲FA برای {phone} نادرست بود! در انتظار ورود رمز صحیح در پنل..."
            self._add_log(f"⚠️ رمز دو مرحله‌ای ذخیره‌شده یا واردشده برای {phone} اشتباه بود! لطفاً رمز صحیح را در پنل وارد کنید.")
        else:
            self.status_text = f"⏳ در انتظار دریافت رمز دو مرحله‌ای (۲FA) برای {phone}..."
            self._add_log(f"⏳ اکانت {phone} به رمز دو مرحله‌ای نیاز دارد اما در سیستم ثبت نشده است. منتظر ورود رمز در پنل...")

        try:
            await asyncio.wait_for(event.wait(), timeout=180.0)
            if self._2fa_skip:
                self._add_log(f"⏭️ ساخت سشن برای {phone} به درخواست کاربر رد شد (Skip).")
                return None
            if self._2fa_input_password:
                self._add_log(f"🔑 رمز دو مرحله‌ای برای {phone} دریافت شد. در حال ورود به اکانت...")
                return self._2fa_input_password
            return None
        except asyncio.TimeoutError:
            self._add_log(f"⏰ زمان انتظار برای ورود رمز {phone} به پایان رسید (۳ دقیقه). سشن رد شد.")
            return None
        finally:
            self.waiting_2fa = False
            self.waiting_2fa_phone = None
            self.waiting_2fa_is_retry = False
            self._2fa_event = None
            self._2fa_input_password = None
            self._2fa_skip = False

    def provide_2fa(self, phone: str, password: str) -> bool:
        if not self.waiting_2fa or not self._2fa_event:
            return False
        clean_req = phone.strip().replace(" ", "").lstrip("+")
        clean_curr = (self.waiting_2fa_phone or "").strip().replace(" ", "").lstrip("+")
        if clean_req and clean_curr and clean_req != clean_curr:
            return False

        self._2fa_input_password = password
        self._2fa_skip = False
        self._2fa_event.set()
        return True

    def skip_2fa(self, phone: str) -> bool:
        if not self.waiting_2fa or not self._2fa_event:
            return False
        clean_req = phone.strip().replace(" ", "").lstrip("+")
        clean_curr = (self.waiting_2fa_phone or "").strip().replace(" ", "").lstrip("+")
        if clean_req and clean_curr and clean_req != clean_curr:
            return False

        self._2fa_skip = True
        self._2fa_input_password = None
        self._2fa_event.set()
        return True

    async def stop(self) -> None:
        """Gracefully cancels and awaits the migration background worker."""
        if self._current_task and not self._current_task.done():
            logger.info("Cancelling ongoing migration job...")
            self._current_task.cancel()
            try:
                await self._current_task
            except asyncio.CancelledError:
                pass
        self.is_running = False
        self.current_phone = None

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
        proxy: Optional[str] = None,
        skip_existing: bool = False,
        category: Optional[str] = None,
        remark: Optional[str] = None
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

        self._current_task = asyncio.create_task(
            self._run_batch_worker(phones, two_fa_passwords or {}, delay, proxy, skip_existing, category, remark)
        )
        return True

    async def _run_batch_worker(
        self,
        phones: List[str],
        passwords: Dict[str, str],
        delay: float,
        proxy: Optional[str] = None,
        skip_existing: bool = False,
        category: Optional[str] = None,
        remark: Optional[str] = None
    ) -> None:
        try:
            from server.proxy_service import parse_telethon_proxy
            parsed_proxy = parse_telethon_proxy(proxy) if proxy else None

            creator = TelegramSessionCreator(
                settings=self.settings,
                otp_client=self.local_otp_client,
                proxy=parsed_proxy,
                on_2fa_required=self.request_2fa_password
            )
            if proxy:
                self._add_log(f"🌐 اتصال از طریق پروکسی: {proxy}")
            self._add_log(f"Starting batch migration for {len(phones)} accounts...")

            for idx, phone in enumerate(phones, start=1):
                clean_phone = phone.strip().replace(" ", "")
                session_name = clean_phone.lstrip("+")

                if skip_existing:
                    target_file_no_plus = self.settings.target_sessions_dir / f"{session_name}.session"
                    target_file_plus = self.settings.target_sessions_dir / f"+{session_name}.session"
                    if target_file_no_plus.exists() or target_file_plus.exists():
                        target_name = target_file_no_plus.name if target_file_no_plus.exists() else target_file_plus.name
                        self.results.append({
                            "phone": phone,
                            "success": True,
                            "detail": f"از قبل موجود بود ({target_name})",
                            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        })
                        self._add_log(f"[{idx}/{len(phones)}] ⏭️ سشن {phone} از قبل ساخته شده بود ({target_name})، رد شد.")
                        self.progress = idx
                        continue

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

                    # Determine category and remark (inherit from source session if not explicitly set)
                    tgt_cat = category
                    tgt_rem = remark
                    if self.metadata_service:
                        src_meta = self.metadata_service.get_source_metadata(phone)
                        if not tgt_cat:
                            tgt_cat = src_meta.get("category", "")
                        if not tgt_rem:
                            tgt_rem = src_meta.get("remark", "")

                    if self.metadata_service:
                        self.metadata_service.record_target_creation(
                            filename=target_file.name,
                            phone=phone,
                            created_via="proxy" if proxy else "direct",
                            proxy_name=proxy_name,
                            proxy_ip=proxy_ip,
                            proxy_url=proxy,
                            category=tgt_cat,
                            remark=tgt_rem
                        )
                    else:
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
                                "proxy_url": proxy,
                                "category": (tgt_cat or "").strip(),
                                "remark": (tgt_rem or "").strip()
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
        except asyncio.CancelledError:
            self._add_log("⚠️ عملیات ساخت سشن متوقف شد (Cancelled).")
            logger.info("Batch migration task was cancelled.")
            self.status_text = "متوقف شد"
        except Exception as e:
            self._add_log(f"💥 Fatal error during migration: {e}")
            logger.error(f"Fatal error in _run_batch_worker: {e}", exc_info=True)
            self.status_text = f"خطا: {e}"
        finally:
            self.is_running = False
            self.current_phone = None
            self._current_task = None
