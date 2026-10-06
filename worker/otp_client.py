import asyncio
import logging
import time
from typing import Optional
import httpx

from core.exceptions import MasterServerError, OTPTimeoutError
from core.interfaces import IOTPClient

logger = logging.getLogger(__name__)


class HttpOTPClient(IOTPClient):
    """
    HTTP client for querying the Master OTP server with retry & polling logic.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def fetch_otp(
        self,
        phone: str,
        since_timestamp: float,
        timeout: float = 60.0,
        interval: float = 2.5
    ) -> str:
        """
        Polls the master server until OTP is received or timeout is reached.
        """
        clean_phone = phone.strip().replace(" ", "")
        endpoint = f"{self.base_url}/otp/{clean_phone}"
        params = {"since": since_timestamp}

        start_time = time.time()
        logger.info(f"Polling Master server for OTP on {clean_phone} (Timeout: {timeout}s)...")

        async with httpx.AsyncClient(timeout=10.0) as client:
            while (time.time() - start_time) < timeout:
                try:
                    response = await client.get(endpoint, params=params)

                    if response.status_code == 200:
                        data = response.json()
                        code = data.get("code")
                        if code:
                            logger.info(f"OTP received for {clean_phone}: {code}")
                            return code

                    elif response.status_code == 404:
                        # 404 means either session not found or code not arrived yet
                        detail = response.json().get("detail", "")
                        if "not found in master pool" in detail:
                            raise MasterServerError(f"Session {clean_phone} is not loaded on Master server.")
                        # Waiting for code...
                        logger.debug(f"Code not yet arrived for {clean_phone}. Retrying in {interval}s...")

                    else:
                        logger.warning(
                            f"Unexpected status from master server ({response.status_code}): {response.text}"
                        )

                except httpx.ConnectError as e:
                    raise MasterServerError(f"Cannot connect to master server at {self.base_url}: {e}")
                except httpx.RequestError as e:
                    logger.warning(f"Network error while polling master server: {e}")

                await asyncio.sleep(interval)

        raise OTPTimeoutError(
            f"Timed out after {timeout} seconds waiting for OTP for {clean_phone}."
        )
