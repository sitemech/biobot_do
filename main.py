"""Entry point for the Telegram bot that proxies to a DigitalOcean AI Agent."""

from __future__ import annotations

import logging
import sys
from typing import Optional

from bot import BotConfig, build_application
import atexit
import fcntl
import os

# PID file used to prevent multiple local instances from running in the same
# checkout. This is a lightweight local guard against double-processing when
# the same token is accidentally started twice on one machine.
PIDFILE = ".bot.pid"


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

    # Acquire PID file lock to avoid starting two processes on the same host.
    pid_fd = None
    try:
        pid_fd = open(PIDFILE, "w")
        # Try to acquire an exclusive non-blocking lock
        fcntl.flock(pid_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        pid_fd.write(str(os.getpid()))
        pid_fd.flush()
    except BlockingIOError:
        logging.error("Another bot process appears to be running (could not acquire %s). Exiting.", PIDFILE)
        sys.exit(1)
    except Exception:
        logging.warning("Could not create pidfile %s; continuing without lock", PIDFILE)

    def _release_pidfile() -> None:
        try:
            if pid_fd:
                try:
                    fcntl.flock(pid_fd.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                pid_fd.close()
            try:
                if os.path.exists(PIDFILE):
                    os.remove(PIDFILE)
            except Exception:
                pass
        except Exception:
            pass

    atexit.register(_release_pidfile)

    application = build_application(config)
    logging.info("Starting polling loop")
    application.run_polling()


if __name__ == "__main__":
    env_path = sys.argv[1] if len(sys.argv) > 1 else None
    run_bot(env_path)
