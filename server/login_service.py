import asyncio
from dataclasses import dataclass, field
import datetime
import logging
from pathlib import Path
import re
import secrets
import shutil
import time
from typing import Dict, Optional

from fastapi import HTTPException
from telethon import TelegramClient
from telethon.errors import (
    ApiIdInvalidError,
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeEmptyError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
    PhoneNumberUnoccupiedError,
    SessionPasswordNeededError,
)

from config.settings import Settings
from core.device_profiler import DeviceProfileFactory
from core.exceptions import AuthenticationFailedError
from server.account_service import AccountManagementService
from server.metadata_service import MetadataService
from server.proxy_service import ProxyService, parse_telethon_proxy
from server.session_pool import TelethonSessionPool

logger = logging.getLogger(__name__)


async def _safe_disconnect(client: TelegramClient) -> None:
    """Helper to disconnect client safely if connected."""
    try:
        is_conn = client.is_connected()
        if asyncio.iscoroutine(is_conn):
            is_conn = await is_conn
        if is_conn:
            await client.disconnect()
    except Exception:
        pass


def normalize_phone(raw_phone: str) -> str:
    """
    Cleans and normalizes phone numbers into international E.164 format.
    E.g.:
      09121234567 -> +989121234567
      989121234567 -> +989121234567
      +1 (406) 719-4320 -> +14067194320
      00447123456789 -> +447123456789
    """
    cleaned = re.sub(r"[^\d+]", "", raw_phone.strip())
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    elif cleaned.startswith("09") and len(cleaned) == 11:
        # Standard Iranian mobile format
        cleaned = "+98" + cleaned[1:]
    elif not cleaned.startswith("+"):
        cleaned = "+" + cleaned

    digits_only = cleaned.lstrip("+")
    if not digits_only.isdigit() or len(digits_only) < 7:
        raise HTTPException(
            status_code=400,
            detail="فرمت شماره تلفن نامعتبر است. لطفاً شماره را همراه با پیش‌شماره کشور (مانند 989123456789+ یا 14067194320+) وارد نمایید."
        )

    return cleaned


@dataclass
class LoginSessionState:
    login_id: str
    phone: str
    client: TelegramClient
    temp_session_path: Path
    phone_code_hash: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    category: Optional[str] = None
    remark: Optional[str] = None
    proxy_url: Optional[str] = None
    two_fa_required: bool = False


class TelegramLoginService:
    """
    Manages interactive login flow for adding new Telegram sessions
    directly to the panel using Phone + OTP Code + 2FA Password.
    No pre-existing .session file is needed.
    """

    def __init__(
        self,
        settings: Settings,
        pool: TelethonSessionPool,
        account_service: AccountManagementService,
        metadata_service: MetadataService,
        proxy_service: ProxyService,
    ):
        self.settings = settings
        self.pool = pool
        self.account_service = account_service
        self.metadata_service = metadata_service
        self.proxy_service = proxy_service

        self.temp_dir: Path = self.settings.base_dir / "storage" / "temp_sessions"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        self._active_logins: Dict[str, LoginSessionState] = {}
        self._lock = asyncio.Lock()

    def _cleanup_expired(self) -> None:
        """Removes pending login attempts older than 10 minutes."""
        now = time.time()
        expired_ids = [
            lid for lid, state in self._active_logins.items()
            if now - state.created_at > 600
        ]
        for lid in expired_ids:
            state = self._active_logins.pop(lid, None)
            if state:
                if state.client:
                    asyncio.create_task(_safe_disconnect(state.client))
                self._cleanup_temp_files(state.temp_session_path)

    def _cleanup_temp_files(self, base_path: Path) -> None:
        """Deletes any temporary SQLite session and journal files."""
        for suffix in [".session", ".session-journal", ".session-wal", ".session-shm"]:
            p = Path(f"{base_path}{suffix}")
            if p.exists():
                try:
                    p.unlink()
                except Exception as e:
                    logger.debug(f"Could not delete temp file {p}: {e}")

    async def start_login(
        self,
        phone: str,
        category: Optional[str] = None,
        remark: Optional[str] = None,
        proxy: Optional[str] = None,
    ) -> dict:
        """
        Step 1: Initiates Telegram login by sending an OTP code to the given phone number.
        """
        async with self._lock:
            self._cleanup_expired()
            clean_phone = normalize_phone(phone)

            # If there is already an active login for this phone, cancel it first
            existing_ids = [
                lid for lid, state in self._active_logins.items()
                if state.phone == clean_phone
            ]
            for lid in existing_ids:
                old = self._active_logins.pop(lid, None)
                if old and old.client:
                    await _safe_disconnect(old.client)
                    self._cleanup_temp_files(old.temp_session_path)

            login_id = secrets.token_hex(8)
            temp_path = self.temp_dir / f"login_{login_id}"

            # Profile for consistent realistic fingerprint
            profile = DeviceProfileFactory.get_deterministic_profile(clean_phone)

            # Determine proxy
            effective_proxy = None
            if proxy == "default":
                effective_proxy = self.proxy_service.get_default_proxy()
            elif proxy and proxy.strip() and proxy.strip() != "direct":
                effective_proxy = proxy.strip()

            parsed_proxy = parse_telethon_proxy(effective_proxy) if effective_proxy else None

            client = TelegramClient(
                str(temp_path),
                api_id=self.settings.api_id,
                api_hash=self.settings.api_hash,
                device_model=profile.device_model,
                system_version=profile.system_version,
                app_version=profile.app_version,
                lang_code=profile.lang_code,
                system_lang_code=profile.system_lang_code,
                proxy=parsed_proxy,
            )

            try:
                await client.connect()
                logger.info(f"Requesting Telegram login code for {clean_phone} (Login ID: {login_id})...")
                sent_code = await client.send_code_request(clean_phone)
                logger.info(f"Telegram login code successfully requested for {clean_phone}.")

                delivery_text = "تلگرام"
                if hasattr(sent_code, "type"):
                    t_name = type(sent_code.type).__name__
                    if "Sms" in t_name:
                        delivery_text = "پیامک (SMS)"
                    elif "App" in t_name:
                        delivery_text = "اپلیکیشن رسمی تلگرام"
                    elif "Call" in t_name or "Flash" in t_name or "Missed" in t_name:
                        delivery_text = "تماس تلفنی / پیامک تلگرام"

                self._active_logins[login_id] = LoginSessionState(
                    login_id=login_id,
                    phone=clean_phone,
                    client=client,
                    temp_session_path=temp_path,
                    phone_code_hash=sent_code.phone_code_hash,
                    created_at=time.time(),
                    category=category.strip() if category else None,
                    remark=remark.strip() if remark else None,
                    proxy_url=effective_proxy,
                    two_fa_required=False,
                )

                timeout_seconds = getattr(sent_code, "timeout", 120) or 120

                return {
                    "status": "code_sent",
                    "login_id": login_id,
                    "phone": clean_phone,
                    "delivery_type": delivery_text,
                    "timeout": timeout_seconds,
                    "message": f"کد تأیید با موفقیت به {delivery_text} برای شماره {clean_phone} ارسال شد.",
                }

            except PhoneNumberBannedError:
                await _safe_disconnect(client)
                self._cleanup_temp_files(temp_path)
                raise HTTPException(
                    status_code=400,
                    detail=f"شماره تلفن {clean_phone} توسط تلگرام مسدود (Banned) شده است."
                )

            except PhoneNumberInvalidError:
                await _safe_disconnect(client)
                self._cleanup_temp_files(temp_path)
                raise HTTPException(
                    status_code=400,
                    detail=f"فرمت شماره تلفن {clean_phone} از نظر تلگرام نامعتبر است."
                )

            except PhoneNumberUnoccupiedError:
                await _safe_disconnect(client)
                self._cleanup_temp_files(temp_path)
                raise HTTPException(
                    status_code=400,
                    detail=f"شماره {clean_phone} هنوز در تلگرام حسابی ثبت نکرده است."
                )

            except FloodWaitError as e:
                await _safe_disconnect(client)
                self._cleanup_temp_files(temp_path)
                raise HTTPException(
                    status_code=429,
                    detail=f"محدودیت موقت تلگرام (FloodWait): لطفاً {e.seconds} ثانیه دیگر مجدداً تلاش کنید."
                )

            except ApiIdInvalidError:
                await _safe_disconnect(client)
                self._cleanup_temp_files(temp_path)
                raise HTTPException(
                    status_code=500,
                    detail="کلیدهای API تلگرام (API_ID / API_HASH) معتبر نیستند."
                )

            except Exception as e:
                logger.error(f"Error requesting code for {clean_phone}: {e}")
                await _safe_disconnect(client)
                self._cleanup_temp_files(temp_path)
                err_msg = str(e)
                if "Connection refused" in err_msg or "Failed to connect" in err_msg or "SOCKS" in err_msg or "timed out" in err_msg.lower():
                    raise HTTPException(
                        status_code=502,
                        detail="خطا در اتصال به سرورهای تلگرام. لطفاً اتصال اینترنت سرور یا تنظیمات پروکسی را بررسی کنید."
                    )
                raise HTTPException(status_code=400, detail=f"خطا در ارسال کد تلگرام: {e}")

    async def verify_code(self, login_id: str, code: str) -> dict:
        """
        Step 2: Verifies the OTP code received by the user.
        If 2FA password is required, returns status: '2fa_required'.
        Otherwise, finalizes the session and saves it directly to the panel.
        """
        async with self._lock:
            state = self._active_logins.get(login_id)
            if not state:
                raise HTTPException(
                    status_code=404,
                    detail="نشست لاگین یافت نشد یا منقضی شده است. لطفاً مجدداً شماره را وارد کنید."
                )

            clean_code = str(code).strip().replace(" ", "").replace("-", "")
            if not clean_code:
                raise HTTPException(status_code=400, detail="لطفاً کد تأیید تلگرام را وارد کنید.")

            try:
                await state.client.sign_in(
                    phone=state.phone,
                    code=clean_code,
                    phone_code_hash=state.phone_code_hash,
                )
            except SessionPasswordNeededError:
                logger.info(f"2FA password needed for {state.phone} (Login ID: {login_id})")
                state.two_fa_required = True
                return {
                    "status": "2fa_required",
                    "login_id": login_id,
                    "phone": state.phone,
                    "message": "این حساب دارای رمز عبور تایید دو مرحله‌ای (2FA) است. لطفاً رمز عبور را وارد کنید."
                }
            except PhoneCodeInvalidError:
                raise HTTPException(status_code=400, detail="کد تأیید وارد شده اشتباه یا نامعتبر است.")
            except PhoneCodeExpiredError:
                raise HTTPException(status_code=400, detail="کد تأیید منقضی شده است. لطفاً مجدداً درخواست ارسال کد دهید.")
            except PhoneCodeEmptyError:
                raise HTTPException(status_code=400, detail="کد تأیید نمی‌تواند خالی باشد.")
            except FloodWaitError as e:
                raise HTTPException(
                    status_code=429,
                    detail=f"محدودیت موقت تلگرام (FloodWait): لطفاً {e.seconds} ثانیه دیگر مجدداً تلاش کنید."
                )
            except Exception as e:
                logger.error(f"Error verifying code for {state.phone}: {e}")
                raise HTTPException(status_code=400, detail=f"خطا در بررسی کد تأیید: {e}")

            # Sign in completed without 2FA! Finalize session.
            return await self._finalize_login(state)

    async def verify_password(self, login_id: str, password: str, save_password: bool = True) -> dict:
        """
        Step 3: Authenticates with the 2FA password and finalizes the session.
        """
        async with self._lock:
            state = self._active_logins.get(login_id)
            if not state:
                raise HTTPException(
                    status_code=404,
                    detail="نشست لاگین یافت نشد یا منقضی شده است. لطفاً مجدداً شماره را وارد کنید."
                )

            if not password or not password.strip():
                raise HTTPException(status_code=400, detail="لطفاً رمز عبور دو مرحله‌ای را وارد نمایید.")

            clean_pwd = password.strip()

            try:
                await state.client.sign_in(password=clean_pwd)
            except PasswordHashInvalidError:
                raise HTTPException(
                    status_code=400,
                    detail="رمز عبور دو مرحله‌ای (2FA) وارد شده اشتباه است. لطفاً دوباره تلاش کنید."
                )
            except FloodWaitError as e:
                raise HTTPException(
                    status_code=429,
                    detail=f"محدودیت موقت تلگرام (FloodWait): لطفاً {e.seconds} ثانیه دیگر مجدداً تلاش کنید."
                )
            except Exception as e:
                logger.error(f"Error verifying 2FA for {state.phone}: {e}")
                raise HTTPException(status_code=400, detail=f"خطا در تأیید رمز عبور دو مرحله‌ای: {e}")

            return await self._finalize_login(state, password=clean_pwd, save_password=save_password)

    async def resend_code(self, login_id: str) -> dict:
        """Resends the OTP code via Telegram or SMS."""
        async with self._lock:
            state = self._active_logins.get(login_id)
            if not state:
                raise HTTPException(
                    status_code=404,
                    detail="نشست لاگین یافت نشد یا منقضی شده است. لطفاً مجدداً شماره را وارد کنید."
                )

            try:
                sent_code = await state.client.send_code_request(state.phone)
                state.phone_code_hash = sent_code.phone_code_hash
                timeout_seconds = getattr(sent_code, "timeout", 120) or 120
                return {
                    "status": "code_resent",
                    "login_id": login_id,
                    "phone": state.phone,
                    "timeout": timeout_seconds,
                    "message": f"کد جدید با موفقیت برای شماره {state.phone} ارسال شد.",
                }
            except Exception as e:
                logger.error(f"Error resending code for {state.phone}: {e}")
                raise HTTPException(status_code=400, detail=f"خطا در ارسال مجدد کد: {e}")

    async def cancel_login(self, login_id: str) -> dict:
        """Cancels an active login attempt and cleans up temporary files."""
        async with self._lock:
            state = self._active_logins.pop(login_id, None)
            if state:
                if state.client:
                    await _safe_disconnect(state.client)
                self._cleanup_temp_files(state.temp_session_path)
                return {"status": "cancelled", "login_id": login_id}
            return {"status": "not_found", "login_id": login_id}

    async def _finalize_login(
        self,
        state: LoginSessionState,
        password: Optional[str] = None,
        save_password: bool = True
    ) -> dict:
        """
        Completes authorization, moves the generated .session file to storage/source_sessions,
        saves metadata & passwords, and hot-loads it into the active session pool.
        """
        client = state.client
        if not await client.is_user_authorized():
            raise AuthenticationFailedError("احراز هویت اکانت با موفقیت کامل نشد.")

        me = await client.get_me()
        user_id = me.id
        first_name = me.first_name or ""
        last_name = me.last_name or ""
        username = me.username or ""

        # Normalize phone from Telegram Me object or state
        me_phone = me.phone or ""
        if me_phone:
            canonical_phone = f"+{me_phone}" if not me_phone.startswith("+") else me_phone
        else:
            canonical_phone = state.phone

        # Disconnect client first to release SQLite file lock before moving
        await _safe_disconnect(client)

        # If an older client exists in the pool with this phone, disconnect it
        await self.pool.disconnect_client(canonical_phone)
        await self.pool.disconnect_client(state.phone)

        # Target session path in storage/source_sessions/
        dest_session_path = self.settings.source_sessions_dir / f"{canonical_phone}.session"

        # Locate actual temp file created by Telethon
        temp_file = Path(f"{state.temp_session_path}.session")
        if not temp_file.exists():
            temp_file = state.temp_session_path.with_suffix(".session")

        if not temp_file.exists():
            raise HTTPException(
                status_code=500,
                detail="فایل سشن موقت یافت نشد. لطفاً مجدداً فرآیند لاگین را انجام دهید."
            )

        # Overwrite destination file if already present
        if dest_session_path.exists():
            try:
                dest_session_path.unlink()
            except Exception as e:
                logger.warning(f"Failed to remove previous session file {dest_session_path}: {e}")

        shutil.move(str(temp_file), str(dest_session_path))
        logger.info(f"Successfully moved new session file to {dest_session_path}")

        # Clean up any leftover temporary journal/wal files
        self._cleanup_temp_files(state.temp_session_path)

        # Save 2FA password to storage if provided
        if password and save_password:
            self.account_service.set_stored_password(
                identifier=canonical_phone,
                password=password,
                note="ثبت شده هنگام افزودن سشن مستقیم (بدون فایل)"
            )
            if state.phone != canonical_phone:
                self.account_service.set_stored_password(
                    identifier=state.phone,
                    password=password,
                    note="ثبت شده هنگام افزودن سشن مستقیم (بدون فایل)"
                )

        # Save category & remark to metadata service
        if state.category or state.remark:
            self.metadata_service.set_source_metadata(
                identifier=canonical_phone,
                category=state.category,
                remark=state.remark
            )
            if state.phone != canonical_phone:
                self.metadata_service.set_source_metadata(
                    identifier=state.phone,
                    category=state.category,
                    remark=state.remark
                )

        # Hot-load session into active Telethon pool
        try:
            await self.pool.add_session(dest_session_path)
            logger.info(f"Hot-loaded new session {dest_session_path.name} into pool successfully.")
        except Exception as e:
            logger.warning(f"Error in pool.add_session, falling back to pool.reload(): {e}")
            await self.pool.reload()

        # Remove from active login sessions
        self._active_logins.pop(state.login_id, None)

        display_name = f"{first_name} {last_name}".strip() or canonical_phone

        return {
            "status": "success",
            "phone": canonical_phone,
            "user_id": user_id,
            "first_name": first_name,
            "last_name": last_name,
            "display_name": display_name,
            "username": username,
            "message": f"اکانت «{display_name}» با شماره {canonical_phone} با موفقیت به پنل اضافه شد.",
        }

    async def stop(self) -> None:
        """Disconnects all pending login clients and cleans up."""
        async with self._lock:
            for lid, state in list(self._active_logins.items()):
                if state.client:
                    await _safe_disconnect(state.client)
                self._cleanup_temp_files(state.temp_session_path)
            self._active_logins.clear()
