"""Commands and registration for ops/health submodule.

Provides Telegram commands:
- /sys (or /health) — System health & host resource snapshot
- /logs [n]         — View last n lines of bot logs (default 25, capped at 100)
- /logs errors      — View recent warning & error logs (default 20)
"""
import logging
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
from apscheduler.triggers.interval import IntervalTrigger

from .formatters import format_system_health_card
from .probes import get_full_system_snapshot
from .logs import (
    DEFAULT_LOG_LINES,
    DEFAULT_ERROR_LINES,
    MAX_LOG_LINES,
    ensure_file_logging,
    read_last_n_lines,
    read_recent_errors,
    format_logs_for_telegram,
)
from .jobs import check_system_health_job

logger = logging.getLogger(__name__)

NAME = "health"
DESCRIPTION = "🏥 Health — host resource & system diagnostics"


async def sys_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /sys or /health — returns resource snapshot."""
    msg = await update.message.reply_text("🔍 Gathering system metrics…", parse_mode="Markdown")
    try:
        snapshot = get_full_system_snapshot()
        card = format_system_health_card(snapshot)
        await msg.edit_text(card, parse_mode="Markdown")
    except Exception as e:
        logger.error("Error generating system health snapshot: %s", e, exc_info=True)
        await msg.edit_text(f"❌ Failed to query system metrics: `{e}`", parse_mode="Markdown")


async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /logs [n] or /logs errors."""
    args = context.args or []
    sub = args[0].lower() if args else ""

    if sub in ("errors", "error", "warn", "warnings"):
        limit = DEFAULT_ERROR_LINES
        if len(args) > 1 and args[1].isdigit():
            limit = int(args[1])
        error_lines = read_recent_errors(limit=limit)
        text = format_logs_for_telegram(error_lines, title=f"Recent Errors & Warnings (last {len(error_lines)})")
        await update.message.reply_text(text, parse_mode="Markdown")
        return

    n = DEFAULT_LOG_LINES
    if sub and sub.isdigit():
        n = int(sub)

    lines = read_last_n_lines(n=n)
    text = format_logs_for_telegram(lines, title=f"Bot Logs (last {len(lines)} lines)")
    await update.message.reply_text(text, parse_mode="Markdown")


def register(bot_app, scheduler, authorized) -> dict:
    """Register ops/health commands and scheduled jobs."""
    # Ensure log file output is active
    ensure_file_logging()

    # Telegram command handlers
    bot_app.add_handler(CommandHandler(["sys", "health"], sys_command, filters=authorized))
    bot_app.add_handler(CommandHandler("logs", logs_command, filters=authorized))

    # APScheduler watchdog every 15 minutes
    scheduler.add_job(
        check_system_health_job,
        IntervalTrigger(minutes=15),
        id="ops_health_check",
        replace_existing=True,
    )

    return {
        "name": NAME,
        "description": DESCRIPTION,
        "commands": ["sys", "health", "logs"],
        "help": [
            "`/sys` (or `/health`) — resource & system health snapshot",
            "`/logs [n]` — view last n lines of bot logs (capped at 100)",
            "`/logs errors` — view recent warning and error log entries",
        ],
    }
