"""Finance/Trader module — auto-discovery entry point.

Registers with the platform registry via register().
Commands:  /scan, /setups, /traderbias, /confluence
Scheduled: scan_all_tickers every 30 minutes (market-hours aware)
"""
from __future__ import annotations

NAME        = "trader"
DESCRIPTION = "🎯 ICT Trader — Signal scanner with RSI hidden divergence bias + confluence scoring"


def register(bot_app, scheduler, authorized) -> dict:
    """Register the trader module with the automation platform.

    Called once at startup by the module registry. Adds Telegram command
    handlers and an APScheduler job for the 30-minute scan cycle.
    """
    from telegram.ext import CommandHandler
    from apscheduler.triggers.interval import IntervalTrigger

    # Import models so SQLAlchemy creates the table on startup
    from . import models  # noqa: F401 — side-effect import triggers Base metadata

    from .commands import (
        scan_command,
        setups_command,
        traderbias_command,
        confluence_command,
    )
    from .jobs import scan_all_tickers

    # ── Telegram Commands ────────────────────────────────────────────────────
    bot_app.add_handler(CommandHandler("scan",        scan_command,       filters=authorized))
    bot_app.add_handler(CommandHandler("setups",      setups_command,     filters=authorized))
    bot_app.add_handler(CommandHandler("traderbias",  traderbias_command, filters=authorized))
    bot_app.add_handler(CommandHandler("confluence",  confluence_command, filters=authorized))

    # ── Scheduled Job — every 30 minutes ────────────────────────────────────
    scheduler.add_job(
        scan_all_tickers,
        IntervalTrigger(minutes=30),
        id="trader_scan_job",
        replace_existing=True,
    )

    return {
        "name":        NAME,
        "description": DESCRIPTION,
        "commands":    ["scan", "setups", "traderbias", "confluence"],
        "help": [
            "`/scan [ticker]`       — Force ICT scan on full watchlist or one ticker",
            "`/setups`              — List active setups from the last 48 hours",
            "`/traderbias <ticker>` — HTF RSI hidden divergence bias breakdown",
            "`/confluence <ticker>` — Volume profile (POC/VAH/VAL) + S/R levels",
        ],
    }
