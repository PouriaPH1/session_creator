"""Set or recover panel credentials locally, without a public setup endpoint."""

import argparse
import getpass

from config.settings import get_settings
from server.auth_service import AuthService


def main():
    parser = argparse.ArgumentParser(description="Set panel administrator credentials locally")
    parser.add_argument("--username", help="Administrator username")
    parser.add_argument("--reset", action="store_true", help="Replace credentials and revoke all access")
    args = parser.parse_args()
    settings = get_settings()
    auth = AuthService(settings.base_dir / "storage/admin_auth.sqlite3")
    if auth.configured() and not args.reset:
        print("Administrator already configured. Use --reset for recovery.")
        return
    username = args.username or input("Administrator username: ")
    password = getpass.getpass("Password (at least 12 characters): ")
    if password != getpass.getpass("Confirm password: "):
        parser.error("Passwords do not match.")
    try:
        auth.set_credentials(username, password, replace=args.reset)
    except ValueError as exc:
        parser.error(str(exc))
    print("Administrator configured. Sign in at /login.")


if __name__ == "__main__":
    main()
