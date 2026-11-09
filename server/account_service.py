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
from typing import Any, Dict, List, Optional, Tuple, Union

from telethon import TelegramClient
from telethon.errors import (
    FreshResetAuthorisationForbiddenError,
    PasswordHashInvalidError
)
from telethon.tl.functions.account import (
    GetAuthorizationsRequest,
    ResetAuthorizationRequest,
    UpdateProfileRequest
)
from telethon.tl.functions.auth import ResetAuthorizationsRequest
from telethon.tl.functions.photos import UploadProfilePhotoRequest
from telethon.tl.types.account import Authorizations

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
        self.scheduled_file = self.settings.base_dir / "storage" / "scheduled_terminations.json"
        self._ephemeral_clients: Dict[str, TelegramClient] = {}
        self._ensure_storage()

    def _ensure_storage(self) -> None:
        self.passwords_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.passwords_file.exists():
            self.passwords_file.write_text("{}", encoding="utf-8")
        if not self.scheduled_file.exists():
            self.scheduled_file.write_text("{}", encoding="utf-8")

    def _load_passwords(self) -> Dict[str, Dict[str, Any]]:
        try:
            return json.loads(self.passwords_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_password(self, phone: str, password: str, note: str = "") -> None:
        passwords = self._load_passwords()
        clean_phone = phone.strip().replace(" ", "")
        no_plus = clean_phone.lstrip("+")
        with_plus = f"+{no_plus}"
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        record = {
            "password": password,
            "note": note,
            "updated_at": now_str
        }
        passwords[clean_phone] = record
        passwords[no_plus] = record
        passwords[with_plus] = record
        self.passwords_file.write_text(json.dumps(passwords, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_password(self, identifier: str) -> Optional[str]:
        clean_id = identifier.strip().replace(" ", "")
        passwords = self._load_passwords()
        for k in [clean_id, clean_id.lstrip("+"), f"+{clean_id.lstrip('+')}"]:
            if k in passwords:
                item = passwords[k]
                return item.get("password") if isinstance(item, dict) else item
        return None

    def set_stored_password(self, identifier: str, password: str, note: str = "") -> Dict[str, Any]:
        self._save_password(identifier, password, note=note or "ثبت دستی از پنل")
        return {
            "status": "success",
            "identifier": identifier,
            "password": password
        }

    def delete_stored_password(self, identifier: str) -> Dict[str, Any]:
        passwords = self._load_passwords()
        clean_id = identifier.strip().replace(" ", "")
        keys_to_del = [clean_id, clean_id.lstrip("+"), f"+{clean_id.lstrip('+')}"]
        deleted = False
        for k in keys_to_del:
            if k in passwords:
                del passwords[k]
                deleted = True
        if deleted:
            self.passwords_file.write_text(json.dumps(passwords, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"status": "deleted", "identifier": identifier}

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
        delay: float = 2.0,
        telegram_update: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Generates and assigns unique secure 2FA passwords to all (or selected) accounts.
        If telegram_update is True, connects to Telegram and modifies the 2FA setting on Telegram servers
        (using existing password if available to authorize the change).
        If telegram_update is False, generates and saves passwords locally into storage/passwords.json only.
        """
        # Ensure pool has finished loading
        if hasattr(self.pool, "_lock"):
            async with self.pool._lock:
                detailed = self.pool.get_detailed_sessions()
        else:
            detailed = self.pool.get_detailed_sessions()

        target_items = []
        if identifiers:
            for ident in identifiers:
                clean_id = ident.strip().replace(" ", "")
                match = next((s for s in detailed if s.get("phone") == clean_id or s.get("session_name") == clean_id), None)
                target_items.append({
                    "identifier": clean_id,
                    "session_name": match["session_name"] if match else clean_id,
                    "is_active": (match.get("is_authorized") and match.get("status") == "active") if match else False
                })
        else:
            seen_identifiers = set()
            # Prioritize active sessions over inactive duplicates
            sorted_detailed = sorted(
                detailed,
                key=lambda s: 1 if (s.get("is_authorized") and s.get("status") == "active") else 0,
                reverse=True
            )
            for s in sorted_detailed:
                phone = s.get("phone")
                sess_name = s.get("session_name")
                ident = phone if (phone and phone not in ["Unknown", "N/A", "Deactivated", "Error", "Loading..."]) else sess_name
                if not ident:
                    continue
                clean_key = ident.strip().replace(" ", "").lstrip("+")
                if clean_key in seen_identifiers:
                    continue
                seen_identifiers.add(clean_key)

                target_items.append({
                    "identifier": ident,
                    "session_name": sess_name,
                    "is_active": bool(s.get("is_authorized") and s.get("status") == "active")
                })

        results = []
        logger.info(f"Starting batch 2FA assignment for {len(target_items)} account(s) (telegram_update={telegram_update})...")

        for idx, item in enumerate(target_items, start=1):
            ident = item["identifier"]
            sess_name = item.get("session_name", ident)
            new_pass = self.generate_secure_password(length=length)

            if telegram_update:
                if not item["is_active"]:
                    results.append({
                        "identifier": ident,
                        "success": False,
                        "error": "سشن غیرفعال یا منقضی شده است (امکان تغییر در تلگرام وجود ندارد)"
                    })
                    continue

                client = self.pool.get_client(ident) or self.pool.get_client(sess_name)
                if not client:
                    results.append({"identifier": ident, "success": False, "error": "سشن در استخر یافت نشد"})
                    continue

                try:
                    if not client.is_connected():
                        await client.connect()

                    # Look up existing password if available
                    curr_pass = self.get_password(ident) or self.get_password(sess_name)

                    try:
                        if curr_pass:
                            await client.edit_2fa(current_password=curr_pass, new_password=new_pass)
                        else:
                            await client.edit_2fa(new_password=new_pass)
                    except Exception as edit_err:
                        # Fallback: if it failed with curr_pass, maybe 2FA was not active on Telegram
                        if curr_pass:
                            await client.edit_2fa(new_password=new_pass)
                        else:
                            raise edit_err

                    self._save_password(ident, new_pass, note="Auto random 2FA (Telegram)")
                    results.append({
                        "identifier": ident,
                        "success": True,
                        "password": new_pass
                    })
                    logger.info(f"[{idx}/{len(target_items)}] Successfully set random 2FA on Telegram for {ident}")

                except Exception as e:
                    logger.error(f"Failed to set 2FA on Telegram for {ident}: {e}")
                    results.append({
                        "identifier": ident,
                        "success": False,
                        "error": str(e)
                    })

                if idx < len(target_items):
                    await asyncio.sleep(delay)
            else:
                # Storage only (system database passwords.json)
                self._save_password(ident, new_pass, note="Auto random 2FA (System)")
                results.append({
                    "identifier": ident,
                    "success": True,
                    "password": new_pass
                })
                logger.info(f"[{idx}/{len(target_items)}] Generated random 2FA for storage: {ident}")

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

    # ==========================================
    # Active Sessions, 24h Security & Auto-Termination
    # ==========================================
    def _find_session_path(self, identifier: str) -> Optional[Path]:
        """Finds .session file for an identifier in source or target directory."""
        clean = identifier.strip().replace(" ", "")
        candidates = [clean, clean.lstrip("+"), f"+{clean.lstrip('+')}"]

        # Check source_sessions
        for c in candidates:
            p = self.settings.source_sessions_dir / f"{c}.session"
            if p.exists() and p.stat().st_size > 0:
                return p
            p_raw = self.settings.source_sessions_dir / c
            if p_raw.exists() and p_raw.is_file() and p_raw.stat().st_size > 0:
                return p_raw

        # Check target_sessions
        for c in candidates:
            p = self.settings.target_sessions_dir / f"{c}.session"
            if p.exists() and p.stat().st_size > 0:
                return p
            p_raw = self.settings.target_sessions_dir / c
            if p_raw.exists() and p_raw.is_file() and p_raw.stat().st_size > 0:
                return p_raw

        return None

    async def get_client_safely(self, identifier: str) -> TelegramClient:
        """
        Retrieves a connected TelegramClient for an identifier.
        Checks active pool first, then scans storage (source & target).
        """
        client = self.pool.get_client(identifier)
        if client:
            if not client.is_connected():
                await client.connect()
            return client

        clean_id = identifier.strip().replace(" ", "")
        if clean_id in self._ephemeral_clients:
            c = self._ephemeral_clients[clean_id]
            if not c.is_connected():
                await c.connect()
            return c

        session_path = self._find_session_path(identifier)
        if not session_path:
            raise SessionNotFoundError(f"Session '{identifier}' not found in pool or storage.")

        from core.device_profiler import DeviceProfileFactory
        profile = DeviceProfileFactory.get_deterministic_profile(session_path.stem)

        proxy_dict = None
        target_meta_file = self.settings.base_dir / "storage" / "target_sessions_metadata.json"
        if target_meta_file.exists():
            try:
                target_meta = json.loads(target_meta_file.read_text(encoding="utf-8"))
                item_meta = target_meta.get(session_path.name, {}) or target_meta.get(session_path.stem, {})
                proxy_url = item_meta.get("proxy_url")
                if proxy_url:
                    from server.proxy_service import parse_telethon_proxy
                    proxy_dict = parse_telethon_proxy(proxy_url)
            except Exception:
                pass

        new_client = TelegramClient(
            str(session_path),
            api_id=self.settings.api_id,
            api_hash=self.settings.api_hash,
            device_model=profile.device_model,
            system_version=profile.system_version,
            app_version=profile.app_version,
            lang_code=profile.lang_code,
            system_lang_code=profile.system_lang_code,
            proxy=proxy_dict
        )
        await new_client.connect()
        if not await new_client.is_user_authorized():
            await new_client.disconnect()
            raise AuthenticationFailedError(f"Session file {session_path.name} is not authorized.")

        self._ephemeral_clients[clean_id] = new_client
        self._ephemeral_clients[clean_id.lstrip("+")] = new_client
        self._ephemeral_clients[f"+{clean_id.lstrip('+')}"] = new_client
        return new_client

    async def get_session_authorizations(self, identifier: str) -> Dict[str, Any]:
        """
        Retrieves all active authorizations for the given session.
        Calculates:
        - 24-hour security countdown for the current session.
        - Identifies our local sessions vs foreign / previous sessions.
        - Returns structured details for display in the admin panel.
        """
        client = await self.get_client_safely(identifier)
        if not client.is_connected():
            await client.connect()

        try:
            res: Authorizations = await client(GetAuthorizationsRequest())
        except Exception as e:
            logger.error(f"Failed to fetch authorizations for {identifier}: {e}")
            raise e

        # Find current authorization
        current_auth = next((a for a in res.authorizations if getattr(a, "current", False)), None)
        if not current_auth and res.authorizations:
            current_auth = res.authorizations[0]

        # Calculate 24-hour rule based on current authorization's creation date
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        if current_auth and getattr(current_auth, "date_created", None):
            dt_created = current_auth.date_created
            if dt_created.tzinfo is None:
                dt_created = dt_created.replace(tzinfo=datetime.timezone.utc)
            elapsed_seconds = (now_utc - dt_created).total_seconds()
        else:
            dt_created = now_utc
            elapsed_seconds = 24 * 3600

        hours_elapsed = max(0.0, elapsed_seconds / 3600.0)
        hours_remaining = max(0.0, 24.0 - hours_elapsed)
        is_eligible = (elapsed_seconds >= 24 * 3600)

        remaining_h = int(hours_remaining)
        remaining_m = int((hours_remaining % 1) * 60)
        if not is_eligible:
            remaining_text = f"{remaining_h} ساعت و {remaining_m} دقیقه"
        else:
            remaining_text = "آماده حذف نشست‌های قبلی (بیش از ۲۴ ساعت گذشته)"

        # Whitelist our sessions in session manager
        our_hashes = set()
        if current_auth:
            our_hashes.add(current_auth.hash)

        # Check if another client for this phone exists in the pool
        try:
            me = await client.get_me()
            clean_phone = (me.phone or "").strip().replace(" ", "") if me and me.phone else ""
        except Exception:
            clean_phone = identifier.strip().replace(" ", "").lstrip("+")

        if clean_phone and hasattr(self.pool, "_clients"):
            for other_key, other_client in self.pool._clients.items():
                if other_client is not client and other_client.is_connected():
                    try:
                        other_info = getattr(self.pool, "_session_info", {}).get(other_key, {})
                        other_phone = (other_info.get("phone") or "").strip().replace(" ", "")
                        if other_phone.lstrip("+") == clean_phone.lstrip("+"):
                            other_res = await other_client(GetAuthorizationsRequest())
                            for oa in other_res.authorizations:
                                if getattr(oa, "current", False):
                                    our_hashes.add(oa.hash)
                    except Exception:
                        pass

        auth_list = []
        for a in res.authorizations:
            is_current = bool(getattr(a, "current", False))
            is_our_session = is_current or (a.hash in our_hashes)

            created_str = a.date_created.strftime("%Y-%m-%d %H:%M:%S") if getattr(a, "date_created", None) else "-"
            active_str = a.date_active.strftime("%Y-%m-%d %H:%M:%S") if getattr(a, "date_active", None) else "-"

            auth_list.append({
                "hash": str(a.hash),
                "hash_raw": a.hash,
                "device_model": getattr(a, "device_model", "") or "دستگاه ناشناس",
                "platform": getattr(a, "platform", "") or "-",
                "system_version": getattr(a, "system_version", "") or "",
                "app_name": getattr(a, "app_name", "") or "",
                "app_version": getattr(a, "app_version", "") or "",
                "ip": getattr(a, "ip", "") or "-",
                "country": getattr(a, "country", "") or "-",
                "region": getattr(a, "region", "") or "",
                "date_created": created_str,
                "date_active": active_str,
                "is_current": is_current,
                "is_our_session": is_our_session,
                "official_app": bool(getattr(a, "official_app", False)),
                "can_terminate": (not is_our_session) and is_eligible
            })

        our_count = sum(1 for a in auth_list if a["is_our_session"])
        foreign_count = sum(1 for a in auth_list if not a["is_our_session"])
        is_scheduled = self.is_task_scheduled(identifier)

        return {
            "status": "success",
            "identifier": identifier,
            "phone": f"+{clean_phone.lstrip('+')}" if clean_phone else identifier,
            "is_eligible": is_eligible,
            "hours_elapsed": round(hours_elapsed, 2),
            "hours_remaining": round(hours_remaining, 2),
            "remaining_text": remaining_text,
            "created_at": dt_created.strftime("%Y-%m-%d %H:%M:%S"),
            "total_count": len(auth_list),
            "our_sessions_count": our_count,
            "foreign_sessions_count": foreign_count,
            "authorizations": auth_list,
            "is_scheduled": is_scheduled
        }

    async def terminate_foreign_sessions(
        self,
        identifier: str,
        target_hash: Optional[Union[int, str]] = None,
        force: bool = False
    ) -> Dict[str, Any]:
        """
        Safely terminates previous/foreign authorizations:
        - Protects our own sessions in SessionManager from termination.
        - Enforces 24-hour Telegram restriction unless force=True.
        - Supports deleting a specific foreign authorization or all foreign authorizations at once.
        """
        client = await self.get_client_safely(identifier)
        auth_data = await self.get_session_authorizations(identifier)

        # 24-hour restriction check
        if not auth_data["is_eligible"] and not force:
            return {
                "status": "forbidden",
                "identifier": identifier,
                "hours_remaining": auth_data["hours_remaining"],
                "remaining_text": auth_data["remaining_text"],
                "message": (
                    f"طبق پروتکل امنیتی تلگرام، برای حذف نشست‌های دیگر باید حداقل ۲۴ ساعت از ساخت این سشن گذشته باشد. "
                    f"زمان باقی‌مانده: {auth_data['remaining_text']}."
                )
            }

        # Single target hash termination
        if target_hash is not None:
            t_hash = int(target_hash)
            matching = next((a for a in auth_data["authorizations"] if a["hash_raw"] == t_hash), None)
            if matching and matching["is_our_session"]:
                return {
                    "status": "error",
                    "identifier": identifier,
                    "message": "امکان حذف نشست فعال خودِ سشن‌منیجر وجود ندارد."
                }

            try:
                await client(ResetAuthorizationRequest(hash=t_hash))
                logger.info(f"Terminated foreign authorization {t_hash} for {identifier}")
                return {
                    "status": "success",
                    "identifier": identifier,
                    "target_hash": str(t_hash),
                    "message": "نشست مورد نظر با موفقیت لغو شد."
                }
            except FreshResetAuthorisationForbiddenError:
                return {
                    "status": "forbidden",
                    "identifier": identifier,
                    "message": "تلگرام اجازه لغو این نشست را نداد (محدودیت ۲۴ ساعته هنوز برقرار است)."
                }
            except Exception as e:
                logger.error(f"Error terminating authorization {t_hash} for {identifier}: {e}")
                return {
                    "status": "error",
                    "identifier": identifier,
                    "message": f"خطا در لغو نشست: {e}"
                }

        # Bulk foreign termination
        foreign_auths = [a for a in auth_data["authorizations"] if not a["is_our_session"]]
        if not foreign_auths:
            return {
                "status": "success",
                "identifier": identifier,
                "terminated_count": 0,
                "preserved_count": auth_data["our_sessions_count"],
                "message": "هیچ نشست قبلی یا متفرقه‌ای روی این اکانت وجود ندارد. تمام سشن‌های موجود متعلق به سشن‌منیجر هستند."
            }

        terminated_count = 0
        failed_count = 0
        terminated_details = []

        for fa in foreign_auths:
            f_hash = int(fa["hash_raw"])
            try:
                await client(ResetAuthorizationRequest(hash=f_hash))
                terminated_count += 1
                terminated_details.append({
                    "device_model": fa["device_model"],
                    "ip": fa["ip"],
                    "country": fa["country"]
                })
            except FreshResetAuthorisationForbiddenError:
                return {
                    "status": "forbidden",
                    "identifier": identifier,
                    "message": "تلگرام اجازه لغو سایر نشست‌ها را نداد (محدودیت ۲۴ ساعته فعال است)."
                }
            except Exception as e:
                logger.warning(f"Failed to reset auth {f_hash} for {identifier}: {e}")
                failed_count += 1

        # If only 1 local session exists and some failed individually, fallback to ResetAuthorizationsRequest
        if failed_count > 0 and auth_data["our_sessions_count"] == 1:
            try:
                await client(ResetAuthorizationsRequest())
                terminated_count += failed_count
                failed_count = 0
            except Exception as e:
                logger.warning(f"Fallback ResetAuthorizationsRequest failed for {identifier}: {e}")

        # Mark any pending scheduled task completed
        self.mark_task_completed(identifier, {
            "terminated_count": terminated_count,
            "preserved_count": auth_data["our_sessions_count"]
        })

        return {
            "status": "success",
            "identifier": identifier,
            "terminated_count": terminated_count,
            "preserved_count": auth_data["our_sessions_count"],
            "details": terminated_details,
            "message": (
                f"تعداد {terminated_count} نشست قبلی با موفقیت لغو شد. "
                f"تعداد {auth_data['our_sessions_count']} سشن متعلق به سشن‌منیجر بدون تغییر حفظ شدند."
            )
        }

    async def terminate_other_sessions(self, identifier: str) -> Dict[str, Any]:
        """Backward-compatible alias pointing to safe terminate_foreign_sessions."""
        return await self.terminate_foreign_sessions(identifier)

    # ==========================================
    # Scheduled Auto-Terminations Management
    # ==========================================
    def _read_scheduled(self) -> Dict[str, Any]:
        if self.scheduled_file.exists():
            try:
                return json.loads(self.scheduled_file.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _write_scheduled(self, data: Dict[str, Any]) -> None:
        try:
            self.scheduled_file.parent.mkdir(parents=True, exist_ok=True)
            self.scheduled_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Error saving scheduled terminations: {e}")

    def is_task_scheduled(self, identifier: str) -> bool:
        clean = identifier.strip().replace(" ", "")
        data = self._read_scheduled()
        for k in [clean, clean.lstrip("+"), f"+{clean.lstrip('+')}"]:
            if k in data and data[k].get("status") == "pending":
                return True
        return False

    def mark_task_completed(self, identifier: str, result: Dict[str, Any]) -> None:
        clean = identifier.strip().replace(" ", "")
        data = self._read_scheduled()
        changed = False
        for k in [clean, clean.lstrip("+"), f"+{clean.lstrip('+')}"]:
            if k in data and data[k].get("status") == "pending":
                data[k]["status"] = "completed"
                data[k]["completed_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                data[k]["result"] = result
                changed = True
        if changed:
            self._write_scheduled(data)

    def set_scheduled_task(self, identifier: str, enable: bool = True) -> Dict[str, Any]:
        clean = identifier.strip().replace(" ", "")
        canonical = f"+{clean.lstrip('+')}" if clean.lstrip("+") else clean
        data = self._read_scheduled()

        if not enable:
            for k in [clean, clean.lstrip("+"), f"+{clean.lstrip('+')}"]:
                data.pop(k, None)
            self._write_scheduled(data)
            return {"status": "cancelled", "identifier": identifier, "is_scheduled": False}

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        record = {
            "identifier": canonical,
            "scheduled_at": now_str,
            "status": "pending"
        }
        data[canonical] = record
        self._write_scheduled(data)
        return {"status": "scheduled", "identifier": identifier, "is_scheduled": True, "record": record}

    async def run_scheduled_auto_terminations(self) -> List[Dict[str, Any]]:
        """Scans pending scheduled terminations and executes termination if 24h passed."""
        data = self._read_scheduled()
        completed_list = []
        for ident, task in list(data.items()):
            if task.get("status") != "pending":
                continue
            try:
                auth_data = await self.get_session_authorizations(ident)
                if auth_data.get("is_eligible"):
                    logger.info(f"Auto-terminating foreign sessions for {ident} as 24h passed...")
                    res = await self.terminate_foreign_sessions(ident)
                    task["status"] = "completed"
                    task["completed_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    task["result"] = res
                    completed_list.append({"identifier": ident, "result": res})
                    logger.info(f"Auto-termination result for {ident}: {res}")
            except Exception as e:
                logger.warning(f"Could not execute auto-termination check for {ident}: {e}")
        if completed_list:
            self._write_scheduled(data)
        return completed_list

    async def start_auto_terminator_loop(self, check_interval_seconds: int = 600) -> None:
        """Periodic background task loop checking for eligible auto-terminations."""
        logger.info("Auto-terminator background loop started.")
        while True:
            try:
                await asyncio.sleep(check_interval_seconds)
                await self.run_scheduled_auto_terminations()
            except asyncio.CancelledError:
                logger.info("Auto-terminator background loop stopped.")
                break
            except Exception as e:
                logger.warning(f"Error in auto-terminator background loop: {e}")

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

