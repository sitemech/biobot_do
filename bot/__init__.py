"""Telegram bot package for interacting with a DigitalOcean AI Agent."""

# Compatibility shim for python-telegram-bot Updater __slots__ issue.
# Create a small runtime subclass that adds the missing private slot so the
# library can assign the attribute without requiring edits to site-packages.
try:
    from telegram.ext import _updater as _ptb_updater

    if "__polling_cleanup_cb" not in getattr(_ptb_updater.Updater, "__slots__", ()):  # type: ignore[attr-defined]
        base_slots = tuple(getattr(_ptb_updater.Updater, "__slots__", ()))

        class _PatchedUpdater(_ptb_updater.Updater):  # type: ignore[misc]
            __slots__ = base_slots + ("__polling_cleanup_cb",)

        _ptb_updater.Updater = _PatchedUpdater  # type: ignore[attr-defined]
except Exception:
    # Don't fail import if PTB isn't available yet; error will surface later.
    pass

from .config import BotConfig
from .do_agent import DigitalOceanAgentClient, DigitalOceanAgentError
from .handlers import build_application

__all__ = [
    "BotConfig",
    "DigitalOceanAgentClient",
    "DigitalOceanAgentError",
    "build_application",
]
