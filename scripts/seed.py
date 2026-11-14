"""Insert fake guests so you can click around without touching real data."""

from __future__ import annotations

import argparse

from app.config import get_settings
from app.services.seed import DEMO_PASSWORD, seed_demo_data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=50, help="fake guests to create")
    parser.add_argument("--ticketers", type=int, default=2, help="demo ticketer accounts")
    parser.add_argument("--password", default=DEMO_PASSWORD, help="password for the demo ticketers")
    parser.add_argument("--prefix", default="DEMO", help="bank reference prefix")
    args = parser.parse_args(argv)

    settings = get_settings()
    summary = seed_demo_data(
        settings,
        count=args.count,
        ticketer_count=args.ticketers,
        password=args.password,
        prefix=args.prefix,
    )

    print(f"Database: {settings.db_path}")
    print(f"Created:  {summary.created} fake tickets")
    if summary.skipped:
        print(f"Skipped:  {summary.skipped} (bank reference already present)")
    print(f"Staff:    {', '.join(summary.ticketers)}")
    print(f"Password: {args.password} (demo accounts only)")
    print("Fake data only. Never run this against a real event database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
