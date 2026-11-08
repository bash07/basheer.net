"""Telegram command handlers for the finance/screener module.

Commands:
  /screen_run [weekly|monthly] [--dry-run]
      Run a screening cycle on Indian equities (default: weekly).

  /screen_status
      Display currently active screened candidates, tags, and TTL.

  /screen_purge [weekly|monthly|all]
      Purge auto-screened candidates without touching permanent manual entries.
"""
from __future__ import annotations

import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select, delete

from core.db import get_db
from modules.finance.watchlist.models import WatchlistEntry
from .engine import run_weekly_ta_screen, run_monthly_fa_screen
from .formatter import format_screener_report_markdown, format_screener_status_markdown
from .populator import purge_expired_entries

logger = logging.getLogger(__name__)


async def screen_run_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/screen_run [weekly|monthly] [--dry-run]"""
    args = context.args or []
    cadence = "weekly"
    dry_run = False

    for arg in args:
        lower = arg.lower()
        if lower in ("weekly", "monthly"):
            cadence = lower
        elif lower == "--dry-run":
            dry_run = True

    msg = await update.message.reply_text(
        f"🔍 Initiating Indian Equities {cadence.title()} Screener"
        f"{' (DRY RUN - no watchlist changes)' if dry_run else ''}…",
        parse_mode="Markdown",
    )

    loop = asyncio.get_event_loop()
    try:
        if cadence == "weekly":
            report = await loop.run_in_executor(None, run_weekly_ta_screen, 15, not dry_run)
        else:
            report = await loop.run_in_executor(None, run_monthly_fa_screen, 10, not dry_run)
    except Exception as e:
        logger.error("[screener.commands] screen_run failed: %s", e, exc_info=True)
        await msg.edit_text(f"❌ Screener failed: `{e}`", parse_mode="Markdown")
        return

    text = format_screener_report_markdown(report)
    # Telegram message length safe chunking
    if len(text) > 4000:
        chunks = [text[i:i + 3900] for i in range(0, len(text), 3900)]
        await msg.edit_text(chunks[0], parse_mode="Markdown")
        for chunk in chunks[1:]:
            await update.message.reply_text(chunk, parse_mode="Markdown")
    else:
        await msg.edit_text(text, parse_mode="Markdown")


async def screen_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/screen_status — show currently active dynamic entries."""
    with get_db() as db:
        purge_expired_entries(db)
        entries = db.execute(select(WatchlistEntry)).scalars().all()

    weekly = [e for e in entries if e.source == "screen_weekly_ta" and e.cadence == "weekly"]
    monthly = [e for e in entries if e.source == "screen_monthly_fa" and e.cadence == "monthly"]
    permanent = [e for e in entries if e.source == "manual" or e.cadence == "permanent"]

    text = format_screener_status_markdown(weekly, monthly, len(permanent))
    await update.message.reply_text(text, parse_mode="Markdown")


async def screen_purge_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/screen_purge [weekly|monthly|all] — clear dynamic entries."""
    args = context.args or []
    target = args[0].lower() if args else "all"

    with get_db() as db:
        if target == "weekly":
            stmt = delete(WatchlistEntry).where(WatchlistEntry.source == "screen_weekly_ta")
        elif target == "monthly":
            stmt = delete(WatchlistEntry).where(WatchlistEntry.source == "screen_monthly_fa")
        else:
            stmt = delete(WatchlistEntry).where(
                WatchlistEntry.source.in_(["screen_weekly_ta", "screen_monthly_fa"])
            )

        res = db.execute(stmt)
        deleted = res.rowcount
        db.commit()

    await update.message.reply_text(
        f"🗑️ Purged *{deleted}* dynamic {target} entries from Watchlist. Permanent entries untouched.",
        parse_mode="Markdown",
    )
