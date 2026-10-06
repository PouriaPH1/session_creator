import argparse
import sys
import uvicorn

from config.settings import get_settings


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

    args = parser.parse_args()

    print("=" * 60)
    print("🚀 Starting Master Telegram Session OTP Server")
    print(f"📡 Listening on: http://{args.host}:{args.port}")
    print(f"📂 Source sessions folder: {settings.source_sessions_dir}")
    print("=" * 60)

    uvicorn.run(
        "server.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload
    )


if __name__ == "__main__":
    main()
