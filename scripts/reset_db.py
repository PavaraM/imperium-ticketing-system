"""Wipe the ticket database and re-seed the admin account.

Usage:
    uv run python -m scripts.reset_db            # asks for confirmation
    uv run python -m scripts.reset_db --yes      # no prompt
    uv run python -m scripts.reset_db --tickets-only --yes
    uv run python -m scripts.reset_db --no-backup --yes

Stop the server first: the script refuses while the database file is open.
"""

from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.services.reset import ResetRefused, database_in_use, reset_database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reset the QR ticketing database.")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument(
        "--tickets-only",
        action="store_true",
        help="delete tickets but keep staff accounts",
    )
    parser.add_argument("--no-backup", action="store_true", help="skip the automatic backup")
    args = parser.parse_args(argv)

    settings = get_settings()
    path = settings.db_path
    scope = (
        "tickets only — staff accounts are kept"
        if args.tickets_only
        else "everything — tickets and staff accounts are deleted"
    )

    print(f"Database: {path}")
    print(f"Scope:    {scope}")
    print("The admin account is re-seeded from ADMIN_USERNAME / ADMIN_PASSWORD.")

    holders = database_in_use(path)
    if holders:
        print(f"Refused: the database is open by PID {', '.join(map(str, holders))}.")
        print("Stop the server (Ctrl+C) and run this again.")
        return 1

    if not args.yes:
        if not sys.stdin.isatty():
            print("Refused: pass --yes when running without a terminal.")
            return 2
        answer = input('Type "reset" to continue: ').strip().lower()
        if answer != "reset":
            print("Cancelled. Nothing was changed.")
            return 1

    try:
        summary = reset_database(
            settings,
            keep_users=args.tickets_only,
            backup=not args.no_backup,
        )
    except ResetRefused as exc:
        print(f"Refused: {exc}")
        return 1

    if summary.backup is not None:
        print(f"Backup:  {summary.backup}")
    else:
        print("Backup:  skipped")
    staff = "account" if summary.users_deleted == 1 else "accounts"
    print(f"Deleted: {summary.tickets_deleted} tickets, {summary.users_deleted} staff {staff}")
    print(f"Admin:   {summary.admin} (password from ADMIN_PASSWORD)")
    print("Done. Start the server again when you are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
