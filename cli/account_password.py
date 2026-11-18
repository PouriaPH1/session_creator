"""Read one stored Telegram 2FA password locally through an SSH session."""

import argparse

from config.settings import get_settings
from server.account_service import AccountManagementService


def main():
    parser = argparse.ArgumentParser(description="Read the stored 2FA password for one account locally")
    parser.add_argument("identifier", help="Account phone number or session identifier")
    args = parser.parse_args()
    service = AccountManagementService(pool=None, settings=get_settings())
    password = service.get_password(args.identifier)
    if not password:
        parser.exit(1, "No stored password found for this account.\n")
    print(password)


if __name__ == "__main__":
    main()
