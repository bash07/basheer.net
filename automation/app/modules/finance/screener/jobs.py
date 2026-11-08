"""APScheduler jobs for the finance/screener module."""
from __future__ import annotations

import asyncio
import logging

from modules.finance._shared.notify import notify
from .engine import run_weekly_ta_screen, run_monthly_fa_screen
from .formatter import format_screener_report_markdown
from .populator import purge_expired_entries
from core.db import get_db

logger = logging.getLogger(__name__)


async def weekly_ta_screen_job() -> None:
    """Sunday 07:00 AM IST — Run Weekly TA Screen across Indian Nifty 100."""
    logger.info("[screener.jobs] Executing scheduled weekly_ta_screen_job...")
    loop = asyncio.get_event_loop()
    try:
        report = await loop.run_in_executor(None, run_weekly_ta_screen, 15, True)
        text = format_screener_report_markdown(report)
        await notify(text, parse_mode="Markdown")
        logger.info("[screener.jobs] weekly_ta_screen_job finished, populated %d items", len(report.candidates))
    except Exception as e:
        logger.error("[screener.jobs] weekly_ta_screen_job failed: %s", e, exc_info=True)


async def monthly_fa_screen_job() -> None:
    """1st of month 07:30 AM IST — Run Monthly FA Screen across Indian Nifty 100."""
    logger.info("[screener.jobs] Executing scheduled monthly_fa_screen_job...")
    loop = asyncio.get_event_loop()
    try:
        report = await loop.run_in_executor(None, run_monthly_fa_screen, 10, True)
        text = format_screener_report_markdown(report)
        await notify(text, parse_mode="Markdown")
        logger.info("[screener.jobs] monthly_fa_screen_job finished, populated %d items", len(report.candidates))
    except Exception as e:
        logger.error("[screener.jobs] monthly_fa_screen_job failed: %s", e, exc_info=True)


async def daily_purge_job() -> None:
    """Daily midnight IST — Clean up any expired dynamic watchlist entries."""
    with get_db() as db:
        purged = purge_expired_entries(db)
        if purged > 0:
            logger.info("[screener.jobs] Daily purge: removed %d expired entries", purged)
