"""Telegram handlers for the AI Agent bot."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CallbackContext,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import BotConfig
from .do_agent import DigitalOceanAgentClient, DigitalOceanAgentError

logger = logging.getLogger(__name__)

_SESSION_ID_KEY = "do_agent_session_id"


def build_application(config: BotConfig) -> Application:
    """Create and configure a :class:`telegram.ext.Application`."""

    agent_client = DigitalOceanAgentClient(
        api_key=config.do_api_key,
        agent_id=config.do_agent_id,
        base_url=config.do_api_base_url,
        timeout=config.request_timeout,
        agent_endpoint=config.agent_endpoint,
        agent_access_key=config.agent_access_key,
        max_retries=config.api_max_retries,
        base_backoff=config.api_base_backoff,
        max_backoff=config.api_max_backoff,
        rate_qps=config.api_rate_limit_qps,
        rate_burst=config.api_rate_limit_burst,
        rate_cooldown=config.api_rate_limit_cooldown,
    )

    application = (
        ApplicationBuilder()
        .token(config.telegram_bot_token)
        .rate_limiter(AIORateLimiter())
        .post_init(_on_startup)
        .post_shutdown(_on_shutdown)
        .build()
    )

    application.bot_data["agent_client"] = agent_client

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("new", new_conversation))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward_message))

    application.add_error_handler(error_handler)
    return application


async def _on_startup(application: Application) -> None:
    logger.info("Telegram bot started as @%s", application.bot.username)
    # Ensure any previously configured webhook is removed to avoid duplicate
    # deliveries when running in polling mode.
    try:
        await application.bot.delete_webhook()
        logger.info("Removed existing Telegram webhook (if any)")
    except Exception:
        logger.debug("Could not delete webhook (it may not exist).", exc_info=True)


async def _on_shutdown(application: Application) -> None:
    logger.info("Telegram bot is shutting down")
    agent_client: DigitalOceanAgentClient = application.bot_data.get("agent_client")
    if agent_client:
        await agent_client.close()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /start command."""

    user_first_name = update.effective_user.first_name if update.effective_user else "there"
    session_id = await _ensure_session(context)
    await update.message.reply_text(
        (
            "Привет, {name}! Я подключен к AI Agent на DigitalOcean.\n"
            "Напиши сообщение, и я передам его агенту.\n"
            "Используй /new чтобы начать новый диалог."
        ).format(name=user_first_name),
        parse_mode=ParseMode.MARKDOWN,
    )
    logger.info("Started new session %s for chat %s", session_id, update.effective_chat.id)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Отправь текстовое сообщение, и я переправлю его DigitalOcean AI Agent.\n"
        "Команда /new завершает текущую сессию и создаёт новую."
    )


async def new_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reset the conversation by creating a new session."""

    await _create_and_store_session(context)
    await update.message.reply_text(
        "Создана новая сессия. Можешь продолжить диалог с чистого листа!"
    )


async def forward_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward user messages to the DigitalOcean AI Agent."""

    agent_client: DigitalOceanAgentClient = context.application.bot_data["agent_client"]
    session_id = await _ensure_session(context)
    user_message = update.message.text.strip()
    if not user_message:
        await update.message.reply_text("Похоже, сообщение пустое. Попробуй ещё раз.")
        return

    try:
        response = await agent_client.send_message(session_id, user_message)
    except DigitalOceanAgentError as exc:
        logger.exception("DigitalOcean Agent error: %s", exc)
        await update.message.reply_text(
            "Не удалось получить ответ от AI Agent. Попробуй чуть позже."
        )
        return

    await update.message.reply_text(response.message)


async def error_handler(update: object, context: CallbackContext) -> None:
    logger.exception("Unhandled error while processing update %s", update, exc_info=context.error)


async def _ensure_session(context: ContextTypes.DEFAULT_TYPE) -> str:
    session_id = context.user_data.get(_SESSION_ID_KEY)
    if session_id:
        return session_id
    return await _create_and_store_session(context)


async def _create_and_store_session(context: ContextTypes.DEFAULT_TYPE) -> str:
    agent_client: DigitalOceanAgentClient = context.application.bot_data["agent_client"]
    session_id = await agent_client.create_session()
    context.user_data[_SESSION_ID_KEY] = session_id
    return session_id
