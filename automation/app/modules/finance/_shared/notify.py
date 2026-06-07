"""Shared Telegram notification helper for the finance domain.

All finance submodule scheduled jobs (alerts, reports, signals) use this
to send proactive messages without a user context (no update object).

Usage:
    from modules.finance._shared.notify import notify, notify_photo

    await notify("📊 BTC crossed $70,000!")
    await notify_photo(png_path, caption="AAPL — Daily Chart")
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def notify(text: str, parse_mode: str = "Markdown") -> None:
    """Send a text message to the configured Telegram chat."""
    from core.bot import bot_app
    from config import config
    try:
        await bot_app.bot.send_message(
            chat_id=config.telegram_chat_id,
            text=text,
            parse_mode=parse_mode,
        )
    except Exception as e:
        logger.error("[finance.notify] Failed to send message: %s", e, exc_info=True)


async def notify_photo(
    photo_path: str,
    caption: Optional[str] = None,
    parse_mode: str = "Markdown",
) -> None:
    """Send a photo file to the configured Telegram chat, then delete the temp file."""
    import os
    from core.bot import bot_app
    from config import config
    try:
        with open(photo_path, "rb") as f:
            await bot_app.bot.send_photo(
                chat_id=config.telegram_chat_id,
                photo=f,
                caption=caption,
                parse_mode=parse_mode,
            )
    except Exception as e:
        logger.error("[finance.notify] Failed to send photo: %s", e, exc_info=True)
    finally:
        try:
            os.unlink(photo_path)
        except OSError:
            pass
