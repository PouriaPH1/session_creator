import hashlib
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class DeviceProfile:
    device_model: str
    system_version: str
    app_version: str
    lang_code: str = "en"
    system_lang_code: str = "en-US"


class DeviceProfileFactory:
    """
    Factory Pattern for generating realistic Telegram device parameters.
    Helps avoid instant bans by simulating real mobile or desktop environments.
    """

    PROFILES: List[DeviceProfile] = [
        DeviceProfile(
            device_model="Samsung Galaxy S23 Ultra",
            system_version="Android 14, One UI 6.0",
            app_version="10.8.2 (4492)",
            lang_code="en",
            system_lang_code="en-US"
        ),
        DeviceProfile(
            device_model="Xiaomi 13 Pro",
            system_version="Android 13, HyperOS 1.0",
            app_version="10.7.1 (4451)",
            lang_code="en",
            system_lang_code="en-US"
        ),
        DeviceProfile(
            device_model="Google Pixel 8 Pro",
            system_version="Android 14",
            app_version="10.8.0 (4480)",
            lang_code="en",
            system_lang_code="en-US"
        ),
        DeviceProfile(
            device_model="Desktop",
            system_version="Windows 11 Pro 64-bit",
            app_version="4.16.8 x64",
            lang_code="en",
            system_lang_code="en-US"
        ),
        DeviceProfile(
            device_model="iPhone 15 Pro",
            system_version="iOS 17.4.1",
            app_version="10.9.1 (28471)",
            lang_code="en",
            system_lang_code="en-US"
        )
    ]

    @classmethod
    def get_deterministic_profile(cls, identifier: str) -> DeviceProfile:
        """
        Returns a deterministic device profile based on a hash of the identifier
        (phone number or session name), ensuring consistent profile usage for the same account.
        """
        hash_val = int(hashlib.sha256(identifier.encode("utf-8")).hexdigest(), 16)
        index = hash_val % len(cls.PROFILES)
        return cls.PROFILES[index]

    @classmethod
    def get_default_profile(cls) -> DeviceProfile:
        """Returns standard Samsung profile."""
        return cls.PROFILES[0]
