"""Maintenance commands.

    voice-maintenance purge-raw [--days 30]   null out unredacted transcript text

Run daily (EventBridge Scheduler -> ECS task in AWS; cron locally).
"""

import argparse
import asyncio

from voice_agent.config import get_settings
from voice_agent.storage.db import Database
from voice_agent.storage.repository import CallRepository


async def _purge_raw(days: int) -> int:
    db = Database(get_settings().async_database_url)
    try:
        return await CallRepository(db).purge_raw_text(days)
    finally:
        await db.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="voice-maintenance")
    sub = parser.add_subparsers(dest="command", required=True)
    purge = sub.add_parser("purge-raw", help="remove unredacted transcript text past retention")
    purge.add_argument("--days", type=int, default=30)
    args = parser.parse_args(argv)

    if args.command == "purge-raw":
        count = asyncio.run(_purge_raw(args.days))
        print(f"purged raw text from {count} transcript segments older than {args.days} days")


if __name__ == "__main__":
    main()
