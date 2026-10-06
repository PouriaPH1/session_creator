import logging
import re
from datetime import datetime, timezone
from typing import Optional, Tuple

from core.constants import TELEGRAM_SERVICE_NOTIFICATIONS_ID, CONTEXTUAL_OTP_REGEX, OTP_REGEX
from core.exceptions import SessionNotFoundError, OTPNotFoundError
from core.interfaces import ISessionPool, IOTPService

logger = logging.getLogger(__name__)


class TelethonOTPService(IOTPService):
    """
    Extracts OTP login codes from official Telegram service notifications (777000).
    """

    def __init__(self, session_pool: ISessionPool):
        self.session_pool = session_pool

    async def get_latest_otp(
        self,
        identifier: str,
        since_timestamp: Optional[float] = None
    ) -> Optional[Tuple[str, float]]:
        """
        Retrieves the latest OTP code.
        Returns a tuple of (otp_code, message_timestamp_float) or raises an error.
        """
        client = self.session_pool.get_client(identifier)
        if not client:
            raise SessionNotFoundError(f"Session '{identifier}' not found in master pool.")

        # Ensure client is still connected
        if not client.is_connected():
            await client.connect()

        # Fetch recent messages from official Telegram notifications
        try:
            messages = await client.get_messages(
                TELEGRAM_SERVICE_NOTIFICATIONS_ID,
                limit=5
            )
        except Exception as e:
            logger.error(f"Error fetching messages from Telegram service for {identifier}: {e}")
            raise OTPNotFoundError(f"Failed to access service notifications: {e}")

        for msg in messages:
            if not msg.message:
                continue

            msg_time = msg.date.replace(tzinfo=timezone.utc).timestamp()

            # If a cutoff timestamp was provided, skip older messages
            if since_timestamp is not None and msg_time < since_timestamp:
                continue

            code = self._extract_code(msg.message)
            if code:
                logger.info(f"Successfully extracted OTP {code} for {identifier}")
                return code, msg_time

        return None

    @staticmethod
    def _extract_code(text: str) -> Optional[str]:
        """Extract 5-digit Telegram OTP code from message text."""
        # Try contextual match first (e.g. "Login code: 12345")
        context_match = CONTEXTUAL_OTP_REGEX.search(text)
        if context_match:
            return context_match.group(1)

        # Fallback to general 5-digit number
        generic_match = OTP_REGEX.search(text)
        if generic_match:
            return generic_match.group(1)

        return None
