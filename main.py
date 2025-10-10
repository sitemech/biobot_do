"""Entry point for the Telegram bot that proxies to a DigitalOcean AI Agent."""

from __future__ import annotations

import logging
import sys
from typing import Optional

from bot import BotConfig, build_application


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )


def run_bot(env_file: Optional[str] = None) -> None:
    """Load configuration, build the bot application, and run polling."""

    configure_logging()
    try:
        config = BotConfig.load(env_file)
    except RuntimeError as exc:
        logging.error("Configuration error: %s", exc)
        sys.exit(1)

    application = build_application(config)
    logging.info("Starting polling loop")
    application.run_polling()


if __name__ == "__main__":
    env_path = sys.argv[1] if len(sys.argv) > 1 else None
    run_bot(env_path)
