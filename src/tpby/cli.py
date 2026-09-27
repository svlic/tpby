from __future__ import annotations

import argparse
import asyncio
import logging

from .config import Settings
from .telegram import TelegramApplication


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram media routing service")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="replace the local database with state scanned from UP and BLACKLIST",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    application = TelegramApplication(Settings.from_env())
    asyncio.run(application.rebuild() if args.rebuild else application.run())


if __name__ == "__main__":
    main()
