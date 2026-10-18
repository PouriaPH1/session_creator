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
            err_str = str(e)
            # If httpx lacks socksio, fallback to PySocks / socket test
            if "socksio" in err_str or "PySocks" in err_str or "SOCKS proxy" in err_str or "pip install" in err_str:
                fallback_res = await asyncio.to_thread(self._test_with_pysocks, cleaned_url)
                if fallback_res.get("success"):
                    return fallback_res

            latency = round((time.time() - t0) * 1000)
            self._update_proxy_status(cleaned_url, "failed", None, latency)
            return {
                "success": False,
                "error": f"خطا در اتصال به پروکسی: {err_str}"
            }

    def _test_with_pysocks(self, url: str) -> Dict[str, Any]:
        """Fallback test using raw socket with PySocks or direct TCP."""
        t0 = time.time()
        parsed = urlparse(url)

        # 1. Try PySocks direct HTTP request
        try:
            import socks
            s = socks.socksocket()
            proxy_type = socks.SOCKS5
            scheme = (parsed.scheme or "socks5").lower()
            if scheme == "socks4":
                proxy_type = socks.SOCKS4
            elif scheme in ("http", "https"):
                proxy_type = socks.HTTP

            port = parsed.port or (1080 if "socks" in scheme else 8080)
            s.set_proxy(proxy_type, parsed.hostname, port, username=parsed.username, password=parsed.password)
            s.settimeout(6.0)
            s.connect(("api.ipify.org", 80))
            req = b"GET / HTTP/1.1\r\nHost: api.ipify.org\r\nUser-Agent: Mozilla/5.0\r\nConnection: close\r\n\r\n"
            s.sendall(req)
            resp = s.recv(1024).decode('utf-8', errors='ignore')
            s.close()
            latency = round((time.time() - t0) * 1000)
            parts = resp.split("\r\n\r\n", 1)
            body = parts[1].strip() if len(parts) > 1 else ""
            if body:
                self._update_proxy_status(url, "active", body, latency)
                return {
                    "success": True,
                    "ip": body,
                    "ping_ms": latency,
                    "message": f"اتصال برقرار شد! آی‌پی خروجی: {body} (تاخیر: {latency}ms)"
                }
        except Exception:
            pass

        # 2. Try simple TCP ping to the proxy server port
        try:
            import socket
            port = parsed.port or 1080
            sock = socket.create_connection((parsed.hostname, port), timeout=4.0)
            sock.close()
            latency = round((time.time() - t0) * 1000)
            self._update_proxy_status(url, "active", parsed.hostname, latency)
            return {
                "success": True,
                "ip": parsed.hostname,
                "ping_ms": latency,
                "message": f"پروکسی فعال است! پورت {port} باز است (تاخیر: {latency}ms)"
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"عدم برقراری ارتباط با پورت پروکسی ({parsed.hostname}:{port}): {e}"
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
