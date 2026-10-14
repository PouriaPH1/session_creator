import argparse
import sys
import threading
import webbrowser
import uvicorn

from config.settings import get_settings
from core.port_selector import find_available_port


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    settings = get_settings()

    parser = argparse.ArgumentParser(description="Run the Master Telegram Session OTP Server")
    parser.add_argument(
        "--host",
        type=str,
        default=settings.server_host,
        help=f"Host to bind (default: {settings.server_host})"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=settings.server_port,
        help=f"Port to bind (default: {settings.server_port})"
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload on code change (dev mode)"
    )
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Automatically open the web panel in browser on launch"
    )

    args = parser.parse_args()

    # Automatic Free Port Detection
    actual_port, was_changed = find_available_port(
        preferred_port=args.port,
        host=args.host
    )

    # Save active port so other tools or scripts know the exact port
    port_file = settings.base_dir / "storage" / "active_port.txt"
    try:
        port_file.parent.mkdir(parents=True, exist_ok=True)
        port_file.write_text(str(actual_port), encoding="utf-8")
    except Exception:
        pass

    print("=" * 65)
    if was_changed:
        print(f"⚠️  توجه: پورت اولیه ({args.port}) توسط سرویس دیگری اشغال شده بود!")
        print(f"🔄 برنامه به صورت خودکار به پورت آزاد [{actual_port}] منتقل شد تا با ارور مواجه نشوید.")
        print("-" * 65)
    else:
        print(f"✅ پورت انتخابی ({actual_port}) آزاد است و با موفقیت رزرو شد.")
        print("-" * 65)

    print("🚀 Master Telegram Session OTP Server & Web Panel")
    print(f"📡 آدرس اتصال شبکه:    http://{args.host}:{actual_port}")
    print(f"🌐 لینک ورود به پنل وب: http://localhost:{actual_port}")
    print(f"📘 مستندات API:         http://localhost:{actual_port}/docs")
    print(f"📂 پوشه سشن‌های مبدأ:   {settings.source_sessions_dir}")
    print("=" * 65)

    # Optional auto-opening browser on the correct dynamically selected port
    if args.open_browser:
        target_url = f"http://localhost:{actual_port}"
        print(f"🌍 در حال باز کردن مرورگر روی {target_url} ...")
        threading.Timer(1.5, lambda: webbrowser.open(target_url)).start()

    uvicorn.run(
        "server.main:app",
        host=args.host,
        port=actual_port,
        reload=args.reload
    )


if __name__ == "__main__":
    main()
