import argparse
import asyncio
import logging
import sys
from pathlib import Path
from urllib.parse import urlparse
import httpx

from config.settings import get_settings
from worker.otp_client import HttpOTPClient
from worker.session_creator import TelegramSessionCreator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("worker_cli")


def parse_proxy(proxy_str: str):
    """Parses proxy string like socks5://user:pass@host:port into telethon format."""
    if not proxy_str:
        return None
    try:
        import socks
    except ImportError:
        logger.warning("PySocks not installed. Run `pip install PySocks` for proxy support.")

    parsed = urlparse(proxy_str)
    scheme = parsed.scheme.lower()
    proxy_type = 2  # SOCKS5 default
    if scheme in ("http", "https"):
        proxy_type = 3
    elif scheme == "socks4":
        proxy_type = 1

    return (
        proxy_type,
        parsed.hostname,
        parsed.port,
        True,
        parsed.username,
        parsed.password
    )


async def async_main():
    settings = get_settings()

    parser = argparse.ArgumentParser(
        description="Run the Telegram Session Creator / Migration Worker"
    )
    parser.add_argument(
        "--server-url",
        type=str,
        default=settings.server_url,
        help=f"Master OTP server URL (default: {settings.server_url})"
    )
    parser.add_argument(
        "--phone",
        type=str,
        help="Migrate a single phone number (e.g. +1234567890)"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Query Master server and migrate all active sessions found there"
    )
    parser.add_argument(
        "--file",
        type=str,
        help="Path to text file containing phone numbers (one per line, format: phone or phone:2fa_password)"
    )
    parser.add_argument(
        "--password",
        type=str,
        default=None,
        help="Default 2FA password to use if needed"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=settings.batch_sleep_delay,
        help=f"Delay in seconds between batch account creations (default: {settings.batch_sleep_delay}s)"
    )
    parser.add_argument(
        "--proxy",
        type=str,
        default=None,
        help="Proxy in URL format (e.g. socks5://user:pass@host:port)"
    )

    args = parser.parse_args()

    # Apply override delay if provided
    settings.batch_sleep_delay = args.delay

    proxy_cfg = parse_proxy(args.proxy) if args.proxy else None
    otp_client = HttpOTPClient(base_url=args.server_url)
    creator = TelegramSessionCreator(
        settings=settings,
        otp_client=otp_client,
        proxy=proxy_cfg
    )

    phones_to_process = []
    passwords_dict = {}

    if args.phone:
        phones_to_process.append(args.phone)
        if args.password:
            passwords_dict[args.phone] = args.password

    elif args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            logger.error(f"Phones file not found: {file_path}")
            sys.exit(1)
        for line in file_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                phone_part, pwd_part = line.split(":", 1)
                phones_to_process.append(phone_part.strip())
                passwords_dict[phone_part.strip()] = pwd_part.strip()
            else:
                phones_to_process.append(line)
                if args.password:
                    passwords_dict[line] = args.password

    elif args.all:
        logger.info(f"Querying Master Server ({args.server_url}) for all active sessions...")
        async with httpx.AsyncClient(timeout=10.0) as http:
            try:
                res = await http.get(f"{args.server_url.rstrip('/')}/sessions")
                if res.status_code != 200:
                    logger.error(f"Failed to fetch sessions from server: {res.text}")
                    sys.exit(1)
                data = res.json()
                phones_to_process = data.get("sessions", [])
                for p in phones_to_process:
                    if args.password:
                        passwords_dict[p] = args.password
            except Exception as e:
                logger.error(f"Cannot connect to master server: {e}")
                sys.exit(1)

    else:
        parser.print_help()
        sys.exit(0)

    if not phones_to_process:
        logger.warning("No phone numbers to process.")
        return

    print("=" * 60)
    print(f"📦 Total sessions to create: {len(phones_to_process)}")
    print(f"🎯 Target output directory: {settings.target_sessions_dir}")
    print(f"🌐 Master OTP Server: {args.server_url}")
    print("=" * 60)

    results = await creator.create_batch_sessions(
        phone_list=phones_to_process,
        two_fa_passwords=passwords_dict
    )

    # Print summary table
    print("\n" + "=" * 60)
    print("📊 MIGRATION SUMMARY REPORT")
    print("=" * 60)
    success_count = sum(1 for _, ok, _ in results if ok)
    fail_count = len(results) - success_count

    for phone, ok, detail in results:
        status_symbol = "✅" if ok else "❌"
        print(f"{status_symbol} {phone:<20} -> {detail}")

    print("-" * 60)
    print(f"Total: {len(results)} | Success: {success_count} | Failed: {fail_count}")
    print("=" * 60)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
