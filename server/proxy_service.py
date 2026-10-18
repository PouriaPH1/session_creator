import asyncio
import datetime
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
import httpx

from config.settings import Settings

logger = logging.getLogger(__name__)


def parse_telethon_proxy(proxy_str: Optional[str]) -> Optional[Tuple]:
    """
    Parses a proxy string into Telethon's tuple format:
    (proxy_type, host, port, rdns_bool, username, password)
    """
    if not proxy_str or not proxy_str.strip():
        return None

    try:
        import socks
    except ImportError:
        logger.warning("PySocks not installed. Install PySocks for proxy support.")
        return None

    cleaned = proxy_str.strip()
    if "://" not in cleaned:
        cleaned = f"socks5://{cleaned}"

    parsed = urlparse(cleaned)
    scheme = (parsed.scheme or "socks5").lower()

    if scheme in ("http", "https"):
        proxy_type = socks.HTTP
    elif scheme == "socks4":
        proxy_type = socks.SOCKS4
    else:
        proxy_type = socks.SOCKS5

    return (
        proxy_type,
        parsed.hostname,
        parsed.port or (1080 if "socks" in scheme else 8080),
        True,
        parsed.username,
        parsed.password
    )


class ProxyService:
    """
    Manages user-configured SOCKS5/HTTP proxies for session creation.
    Allows testing proxies, checking outbound IP, and setting default proxies.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.proxies_file: Path = self.settings.base_dir / "storage" / "proxies.json"
        self._ensure_storage()

    def _ensure_storage(self) -> None:
        self.proxies_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.proxies_file.exists():
            self.proxies_file.write_text(json.dumps({"proxies": []}, indent=2), encoding="utf-8")

    def _load_data(self) -> List[Dict[str, Any]]:
        try:
            content = json.loads(self.proxies_file.read_text(encoding="utf-8"))
            return content.get("proxies", [])
        except Exception:
            return []

    def _save_data(self, proxies: List[Dict[str, Any]]) -> None:
        self.proxies_file.write_text(
            json.dumps({"proxies": proxies}, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def get_all(self) -> List[Dict[str, Any]]:
        return self._load_data()

    def get_default_proxy(self) -> Optional[str]:
        for p in self._load_data():
            if p.get("is_default"):
                return p.get("url")
        return None

    def add_proxy(self, name: str, url: str, is_default: bool = False) -> Dict[str, Any]:
        proxies = self._load_data()
        cleaned_url = url.strip()
        if "://" not in cleaned_url:
            cleaned_url = f"socks5://{cleaned_url}"

        proxy_id = f"prx_{int(time.time() * 1000)}"

        if is_default:
            for p in proxies:
                p["is_default"] = False

        new_entry = {
            "id": proxy_id,
            "name": name.strip() or f"Proxy {len(proxies) + 1}",
            "url": cleaned_url,
            "is_default": is_default or len(proxies) == 0,
            "status": "untested",
            "ip": None,
            "ping_ms": None,
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        proxies.append(new_entry)
        self._save_data(proxies)
        return new_entry

    def delete_proxy(self, proxy_id: str) -> bool:
        proxies = self._load_data()
        filtered = [p for p in proxies if p.get("id") != proxy_id]
        if len(filtered) != len(proxies):
            self._save_data(filtered)
            return True
        return False

    def set_default(self, proxy_id: str) -> bool:
        proxies = self._load_data()
        found = False
        for p in proxies:
            if p.get("id") == proxy_id:
                p["is_default"] = True
                found = True
            else:
                p["is_default"] = False
        if found:
            self._save_data(proxies)
        return found

    async def test_proxy(self, url: str) -> Dict[str, Any]:
        """
        Tests the proxy connection by fetching public IP and measuring latency.
        """
        cleaned_url = url.strip()
        if "://" not in cleaned_url:
            cleaned_url = f"socks5://{cleaned_url}"

        t0 = time.time()
        try:
            async with httpx.AsyncClient(proxy=cleaned_url, timeout=8.0) as client:
                res = await client.get("https://api.ipify.org?format=json")
                latency = round((time.time() - t0) * 1000)
                if res.status_code == 200:
                    outbound_ip = res.json().get("ip")
                    # Update status in saved proxies if matched
                    self._update_proxy_status(cleaned_url, "active", outbound_ip, latency)
                    return {
                        "success": True,
                        "ip": outbound_ip,
                        "ping_ms": latency,
                        "message": f"اتصال برقرار شد! آی‌پی خروجی: {outbound_ip} (تاخیر: {latency}ms)"
                    }
                else:
                    return {
                        "success": False,
                        "error": f"HTTP Error {res.status_code}"
                    }
        except Exception as e:
            latency = round((time.time() - t0) * 1000)
            self._update_proxy_status(cleaned_url, "failed", None, latency)
            return {
                "success": False,
                "error": f"خطا در اتصال به پروکسی: {str(e)}"
            }

    def _update_proxy_status(
        self,
        url: str,
        status: str,
        ip: Optional[str],
        ping_ms: Optional[int]
    ) -> None:
        proxies = self._load_data()
        for p in proxies:
            if p.get("url") == url:
                p["status"] = status
                if ip:
                    p["ip"] = ip
                p["ping_ms"] = ping_ms
        self._save_data(proxies)
