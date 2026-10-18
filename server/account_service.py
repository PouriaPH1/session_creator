import asyncio
import csv
import datetime
import io
import json
import logging
from pathlib import Path
import random
import secrets
import string
from typing import Any, Dict, List, Optional, Tuple

from telethon.errors import (
    FreshResetAuthorisationForbiddenError,
    PasswordHashInvalidError
)
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.auth import ResetAuthorizationsRequest
from telethon.tl.functions.photos import UploadProfilePhotoRequest

from config.settings import Settings
from core.exceptions import AuthenticationFailedError, SessionNotFoundError
from server.session_pool import TelethonSessionPool

logger = logging.getLogger(__name__)


# Realistic Persona Name Lists
PERSIAN_FIRST_NAMES = [
    "علی", "رضا", "محمد", "امیر", "حسین", "مهدی", "سارا", "مریم", "فاطمه", "زهرا",
    "نیما", "آرمین", "پوریا", "سامان", "سینا", "پویا", "الناز", "نرگس", "بهار", "یاسمن",
    "نوید", "شاهین", "آرش", "کیان", "امید", "پریسا", "الهام", "شیما", "رویا", "مهسا"
]

PERSIAN_LAST_NAMES = [
    "محمدی", "رضایی", "حسینی", "عباسی", "احمدی", "کریمی", "موسوی", "جعفری", "قاسمی",
    "مرادی", "رحیمی", "ابراهیمی", "صادقی", "طاهری", "کاظمی", "نجفی", "حیدری", "فراهانی",
    "باقری", "فتحی", "سلیمانی", "شریفی", "خسروی", "مقدم", "امینی", "صالحی", "اکبری"
]

ENGLISH_FIRST_NAMES = [
    "Alex", "David", "Michael", "James", "Daniel", "Robert", "John", "Thomas",
    "Emma", "Olivia", "Sophia", "Ava", "Mia", "Emily", "Charlotte", "Amelia",
    "Lucas", "Liam", "Noah", "Oliver", "Ethan", "Mason", "Logan", "Leo"
]

ENGLISH_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Davis", "Wilson",
    "Anderson", "Taylor", "Thomas", "Moore", "Jackson", "Martin", "Lee", "Harris",
    "Clark", "Lewis", "Robinson", "Walker", "Young", "Allen", "King", "Wright"
]


class AccountManagementService:
    """
    Service Layer for managing Telegram accounts:
    - Profile update (first name, last name, bio/about)
    - Avatar/Photo upload
    - 2FA Password setup (Single & Batch Random Generation)
    - Session termination (Logout other sessions)
    - Account Health / SpamBot Check
    """

    def __init__(self, pool: TelethonSessionPool, settings: Settings):
        self.pool = pool
        self.settings = settings
        self.passwords_file = self.settings.base_dir / "storage" / "passwords.json"
        self._ensure_storage()

    def _ensure_storage(self) -> None:
        self.passwords_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.passwords_file.exists():
            self.passwords_file.write_text("{}", encoding="utf-8")

    def _load_passwords(self) -> Dict[str, Dict[str, Any]]:
        try:
            return json.loads(self.passwords_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_password(self, phone: str, password: str, note: str = "") -> None:
        passwords = self._load_passwords()
        clean_phone = phone.strip().replace(" ", "")
        passwords[clean_phone] = {
            "password": password,
            "note": note,
            "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self.passwords_file.write_text(json.dumps(passwords, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_all_passwords(self) -> Dict[str, Dict[str, Any]]:
        return self._load_passwords()

    @staticmethod
    def generate_secure_password(length: int = 12) -> str:
        """Generates a high-entropy, strong random password for 2FA."""
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        # Ensure at least 1 uppercase, 1 lowercase, 1 digit, 1 special char
        password = [
            secrets.choice(string.ascii_uppercase),
            secrets.choice(string.ascii_lowercase),
            secrets.choice(string.digits),
            secrets.choice("!@#$%^&*")
        ]
        password += [secrets.choice(alphabet) for _ in range(max(length - 4, 4))]
        random.shuffle(password)
        return "".join(password)

    async def update_profile(
        self,
        identifier: str,
        first_name: str,
        last_name: str = "",
        about: str = ""
    ) -> Dict[str, Any]:
        """Updates name and bio of an account."""
        client = self.pool.get_client(identifier)
        if not client:
            raise SessionNotFoundError(f"Session '{identifier}' not found in pool.")

        if not client.is_connected():
            await client.connect()

        await client(UpdateProfileRequest(
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            about=about.strip()
        ))

        # Update cache in pool if exists
        for info in self.pool._session_info.values():
            if info.get("phone") == identifier or info.get("session_name") == identifier:
                info["first_name"] = f"{first_name} {last_name}".strip()
                break

        logger.info(f"Updated profile for {identifier} -> {first_name} {last_name}")
        return {
            "status": "success",
            "identifier": identifier,
            "first_name": first_name,
            "last_name": last_name,
            "about": about
        }

    async def upload_avatar(
        self,
        identifier: str,
        photo_bytes: bytes,
        filename: str = "avatar.jpg"
    ) -> Dict[str, Any]:
        """Uploads and sets a new profile photo for an account."""
        client = self.pool.get_client(identifier)
        if not client:
            raise SessionNotFoundError(f"Session '{identifier}' not found in pool.")

        if not client.is_connected():
            await client.connect()

        # Telethon can upload file directly from bytes using BytesIO
        file_io = io.BytesIO(photo_bytes)
        file_io.name = filename

        uploaded_file = await client.upload_file(file_io)
        await client(UploadProfilePhotoRequest(file=uploaded_file))

        logger.info(f"Uploaded profile photo for {identifier}")
        return {
            "status": "success",
            "identifier": identifier,
            "message": "Avatar updated successfully"
        }

    async def set_2fa_password(
        self,
        identifier: str,
        new_password: str,
        current_password: Optional[str] = None,
        hint: str = ""
    ) -> Dict[str, Any]:
        """Sets or changes 2FA password on a single account."""
        client = self.pool.get_client(identifier)
        if not client:
            raise SessionNotFoundError(f"Session '{identifier}' not found in pool.")

        if not client.is_connected():
            await client.connect()

        try:
            await client.edit_2fa(
                current_password=current_password if current_password else None,
                new_password=new_password,
                hint=hint
            )
            # Save to storage
            self._save_password(identifier, new_password, note="Manual update")
            logger.info(f"2FA password updated successfully for {identifier}")
            return {
                "status": "success",
                "identifier": identifier,
                "password": new_password
            }
        except PasswordHashInvalidError:
            raise AuthenticationFailedError("Current 2FA password provided is incorrect.")
        except Exception as e:
            logger.error(f"Error setting 2FA on {identifier}: {e}")
            raise e

    async def batch_set_random_2fa(
        self,
        identifiers: Optional[List[str]] = None,
        length: int = 12,
        delay: float = 2.0
    ) -> List[Dict[str, Any]]:
        """
        Generates and assigns unique secure 2FA passwords to all (or selected) accounts.
        Saves all generated passwords into storage/passwords.json.
        """
        detailed = self.pool.get_detailed_sessions()
        authorized_phones = {
            s["phone"] for s in detailed if s.get("is_authorized") and s.get("status") == "active"
        }
        authorized_names = {
            s["session_name"] for s in detailed if s.get("is_authorized") and s.get("status") == "active"
        }
        valid_set = authorized_phones | authorized_names

        candidates = identifiers or [s["phone"] for s in detailed if s.get("is_authorized") and s.get("status") == "active"]
        target_identifiers = [
            ident for ident in candidates
            if ident in valid_set or ident.lstrip("+") in valid_set or f"+{ident.lstrip('+')}" in valid_set
        ]
        results = []

        logger.info(f"Starting batch 2FA assignment for {len(target_identifiers)} account(s)...")

        for idx, ident in enumerate(target_identifiers, start=1):
            client = self.pool.get_client(ident)
            if not client:
                results.append({"identifier": ident, "success": False, "error": "Not loaded"})
                continue

            try:
                if not client.is_connected():
                    await client.connect()

                new_pass = self.generate_secure_password(length=length)
                await client.edit_2fa(new_password=new_pass)
                self._save_password(ident, new_pass, note="Auto random 2FA")

                results.append({
                    "identifier": ident,
                    "success": True,
                    "password": new_pass
                })
                logger.info(f"[{idx}/{len(target_identifiers)}] Set random 2FA for {ident}")

            except Exception as e:
                logger.error(f"Failed to set 2FA for {ident}: {e}")
                results.append({
                    "identifier": ident,
                    "success": False,
                    "error": str(e)
                })

            if idx < len(target_identifiers):
                await asyncio.sleep(delay)

        return results

    async def batch_set_random_names(
        self,
        locale: str = "fa",
        identifiers: Optional[List[str]] = None,
        delay: float = 1.5
    ) -> List[Dict[str, Any]]:
        """Sets random natural personas (names) on accounts."""
        detailed = self.pool.get_detailed_sessions()
        authorized_phones = {
            s["phone"] for s in detailed if s.get("is_authorized") and s.get("status") == "active"
        }
        authorized_names = {
            s["session_name"] for s in detailed if s.get("is_authorized") and s.get("status") == "active"
        }
        valid_set = authorized_phones | authorized_names

        candidates = identifiers or [s["phone"] for s in detailed if s.get("is_authorized") and s.get("status") == "active"]
        target_identifiers = [
            ident for ident in candidates
            if ident in valid_set or ident.lstrip("+") in valid_set or f"+{ident.lstrip('+')}" in valid_set
        ]
        results = []

        first_names = PERSIAN_FIRST_NAMES if locale == "fa" else ENGLISH_FIRST_NAMES
        last_names = PERSIAN_LAST_NAMES if locale == "fa" else ENGLISH_LAST_NAMES

        for idx, ident in enumerate(target_identifiers, start=1):
            first = random.choice(first_names)
            last = random.choice(last_names)

            try:
                await self.update_profile(ident, first_name=first, last_name=last)
                results.append({
                    "identifier": ident,
                    "success": True,
                    "name": f"{first} {last}"
                })
            except Exception as e:
                results.append({
                    "identifier": ident,
                    "success": False,
                    "error": str(e)
                })

            if idx < len(target_identifiers):
                await asyncio.sleep(delay)

        return results

    async def terminate_other_sessions(self, identifier: str) -> Dict[str, Any]:
        """Terminates all other active Telegram sessions except the current one."""
        client = self.pool.get_client(identifier)
        if not client:
            raise SessionNotFoundError(f"Session '{identifier}' not found in pool.")

        if not client.is_connected():
            await client.connect()

        try:
            await client(ResetAuthorizationsRequest())
            logger.info(f"Successfully terminated other sessions for {identifier}")
            return {
                "status": "success",
                "identifier": identifier,
                "message": "All other sessions have been terminated."
            }
        except FreshResetAuthorisationForbiddenError:
            return {
                "status": "forbidden",
                "identifier": identifier,
                "message": "Telegram requires the session to be active for at least 24 hours before resetting other sessions."
            }
        except Exception as e:
            logger.error(f"Error terminating sessions for {identifier}: {e}")
            raise e

    async def check_spambot(self, identifier: str) -> Dict[str, Any]:
        """Checks account limitation status via official @SpamBot."""
        client = self.pool.get_client(identifier)
        if not client:
            raise SessionNotFoundError(f"Session '{identifier}' not found in pool.")

        if not client.is_connected():
            await client.connect()

        try:
            # Send /start to @SpamBot
            bot_entity = await client.get_input_entity("SpamBot")
            await client.send_message(bot_entity, "/start")

            # Wait for response
            await asyncio.sleep(2.0)
            messages = await client.get_messages("SpamBot", limit=1)

            if messages and messages[0].message:
                text = messages[0].message
                is_clean = ("Good news" in text or "no limits" in text or "هیچ محدودیتی" in text or "خبر خوب" in text)
                return {
                    "identifier": identifier,
                    "status": "clean" if is_clean else "limited",
                    "response": text
                }

            return {
                "identifier": identifier,
                "status": "unknown",
                "response": "No response received from SpamBot"
            }

        except Exception as e:
            logger.error(f"Error checking SpamBot for {identifier}: {e}")
            return {
                "identifier": identifier,
                "status": "error",
                "response": str(e)
            }

    def export_passwords_csv(self) -> str:
        """Exports all saved passwords as a CSV string."""
        passwords = self._load_passwords()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Phone / Identifier", "2FA Password", "Note", "Updated At"])

        for phone, info in passwords.items():
            writer.writerow([
                phone,
                info.get("password", ""),
                info.get("note", ""),
                info.get("updated_at", "")
            ])

        return output.getvalue()

    def import_passwords_csv(self, csv_content: str) -> int:
        """
        Imports 2FA passwords from CSV content into storage/passwords.json.
        Detects header columns for phone/identifier and password/2FA automatically.
        """
        imported_count = 0
        reader = csv.reader(io.StringIO(csv_content))
        rows = [r for r in reader if any(field.strip() for field in r)]
        if not rows:
            return 0

        header = [c.strip().lower() for c in rows[0]]
        phone_idx = -1
        pwd_idx = -1

        for idx, col in enumerate(header):
            if any(k in col for k in ["phone", "identifier", "شماره", "number"]):
                phone_idx = idx
            elif any(k in col for k in ["password", "2fa", "رمز", "pass"]):
                pwd_idx = idx

        start_row = 1 if (phone_idx != -1 and pwd_idx != -1) else 0
        if phone_idx == -1 or pwd_idx == -1:
            phone_idx = 0
            pwd_idx = 1

        passwords = self._load_passwords()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for row in rows[start_row:]:
            if len(row) <= max(phone_idx, pwd_idx):
                continue
            phone = row[phone_idx].strip()
            pwd = row[pwd_idx].strip()
            if not phone or not pwd:
                continue

            clean_phone = phone.replace(" ", "")
            passwords[clean_phone] = {
                "password": pwd,
                "note": "Imported from CSV",
                "updated_at": now_str
            }
            passwords[clean_phone.lstrip("+")] = {
                "password": pwd,
                "note": "Imported from CSV",
                "updated_at": now_str
            }
            imported_count += 1

        self.passwords_file.write_text(json.dumps(passwords, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Imported {imported_count} 2FA passwords from CSV into storage.")
        return imported_count

