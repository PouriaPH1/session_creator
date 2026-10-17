import asyncio
import logging
import time
from pathlib import Path
from typing import List, Optional, Tuple, Union

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)

from config.settings import Settings
from core.device_profiler import DeviceProfileFactory
from core.exceptions import (
    AccountBannedError,
    AuthenticationFailedError,
    SessionCreatorError,
)
from core.interfaces import IOTPClient

logger = logging.getLogger(__name__)


class TelegramSessionCreator:
    """
    Handles logging in and generating new Telethon .session files on the target server.
    """

    def __init__(
        self,
        settings: Settings,
        otp_client: IOTPClient,
        proxy: Optional[Union[tuple, dict]] = None
    ):
        self.settings = settings
        self.otp_client = otp_client
        self.proxy = proxy

    async def create_single_session(
        self,
        phone: str,
        two_fa_password: Optional[str] = None,
        custom_session_name: Optional[str] = None
    ) -> Path:
        """
        Creates and authorizes a new session for a given phone number.
        Returns the Path to the generated .session file.
        """
        clean_phone = phone.strip().replace(" ", "")
        session_name = custom_session_name or clean_phone.lstrip("+")
        target_path = self.settings.target_sessions_dir / session_name

        profile = DeviceProfileFactory.get_deterministic_profile(session_name)

        logger.info(
            f"Initiating session creation for {clean_phone} "
            f"(Profile: {profile.device_model}, OS: {profile.system_version})"
        )

        client = TelegramClient(
            str(target_path),
            api_id=self.settings.api_id,
            api_hash=self.settings.api_hash,
            device_model=profile.device_model,
            system_version=profile.system_version,
            app_version=profile.app_version,
            lang_code=profile.lang_code,
            system_lang_code=profile.system_lang_code,
            proxy=self.proxy
        )

        try:
            await client.connect()

            # If already authorized on target machine, finish early
            if await client.is_user_authorized():
                logger.info(f"Session {session_name} is already authorized!")
                me = await client.get_me()
                logger.info(f"Logged in as User: {me.first_name} ({me.id})")
                await client.disconnect()
                return Path(f"{target_path}.session")

            # 1. Request OTP Code from Telegram
            request_timestamp = time.time() - 120.0  # safe 2-minute grace period to prevent clock skew
            logger.info(f"Sending login code request for {clean_phone}...")

            sent_code = await client.send_code_request(clean_phone)
            logger.info(f"Code requested successfully (Hash: {sent_code.phone_code_hash})")

            # 2. Fetch OTP from Master Server via Polling
            otp_code = await self.otp_client.fetch_otp(
                phone=clean_phone,
                since_timestamp=request_timestamp,
                timeout=self.settings.otp_poll_timeout,
                interval=self.settings.otp_poll_interval
            )

            # 3. Complete Sign-In
            try:
                await client.sign_in(
                    phone=clean_phone,
                    code=otp_code,
                    phone_code_hash=sent_code.phone_code_hash
                )
            except SessionPasswordNeededError:
                logger.info("2FA password required. Attempting password authentication...")
                if not two_fa_password:
                    raise AuthenticationFailedError(
                        f"Account {clean_phone} requires a 2FA password, but none was provided."
                    )
                try:
                    await client.sign_in(password=two_fa_password)
                except PasswordHashInvalidError:
                    raise AuthenticationFailedError(f"Invalid 2FA password for {clean_phone}.")

            # 4. Verify Authorization
            if not await client.is_user_authorized():
                raise AuthenticationFailedError(f"Sign-in completed but user not authorized for {clean_phone}.")

            me = await client.get_me()
            logger.info(
                f"Successfully created session for {clean_phone} "
                f"(Telegram ID: {me.id}, Username: @{me.username or 'N/A'})"
            )

            await client.disconnect()
            session_file = Path(f"{target_path}.session")
            logger.info(f"Saved new session file: {session_file}")
            return session_file

        except PhoneNumberBannedError:
            logger.error(f"Account {clean_phone} is BANNED by Telegram!")
            if client.is_connected():
                await client.disconnect()
            raise AccountBannedError(f"Phone {clean_phone} is banned.")

        except PhoneNumberInvalidError:
            logger.error(f"Invalid phone number format: {clean_phone}")
            if client.is_connected():
                await client.disconnect()
            raise SessionCreatorError(f"Invalid phone number: {clean_phone}")

        except PhoneCodeInvalidError:
            logger.error(f"Invalid login code supplied for {clean_phone}")
            if client.is_connected():
                await client.disconnect()
            raise AuthenticationFailedError(f"Invalid OTP code for {clean_phone}")

        except PhoneCodeExpiredError:
            logger.error(f"Login code expired for {clean_phone}")
            if client.is_connected():
                await client.disconnect()
            raise AuthenticationFailedError(f"OTP code expired for {clean_phone}")

        except FloodWaitError as e:
            logger.error(f"Telegram FloodWait for {clean_phone}: wait {e.seconds} seconds.")
            if client.is_connected():
                await client.disconnect()
            raise

        except Exception as e:
            logger.error(f"Unexpected error while creating session for {clean_phone}: {e}")
            if client.is_connected():
                await client.disconnect()
            # Clean up incomplete session file if creation failed
            session_file = Path(f"{target_path}.session")
            if session_file.exists():
                try:
                    session_file.unlink()
                except Exception:
                    pass
            raise

    async def create_batch_sessions(
        self,
        phone_list: List[str],
        two_fa_passwords: Optional[dict] = None
    ) -> List[Tuple[str, bool, str]]:
        """
        Processes a list of phones with safety pacing to prevent rate limits.
        Returns a summary report: [(phone, is_success, details)]
        """
        results = []
        passwords = two_fa_passwords or {}

        logger.info(f"Starting batch session migration for {len(phone_list)} account(s)...")

        for idx, phone in enumerate(phone_list, start=1):
            logger.info(f"[{idx}/{len(phone_list)}] Processing {phone}...")
            password = passwords.get(phone)

            try:
                session_path = await self.create_single_session(
                    phone=phone,
                    two_fa_password=password
                )
                results.append((phone, True, str(session_path)))
                logger.info(f"[{idx}/{len(phone_list)}] SUCCESS: {phone}")

            except Exception as e:
                logger.error(f"[{idx}/{len(phone_list)}] FAILED: {phone} -> {e}")
                results.append((phone, False, str(e)))

            # Pacing delay between migrations
            if idx < len(phone_list):
                delay = self.settings.batch_sleep_delay
                logger.info(f"Waiting {delay}s before next session to avoid rate limits...")
                await asyncio.sleep(delay)

        return results
