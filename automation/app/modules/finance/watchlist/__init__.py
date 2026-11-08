"""Watchlist submodule registration."""
from telegram.ext import CommandHandler, CallbackQueryHandler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

NAME        = "watchlist"
DESCRIPTION = "📋 Watchlist — Track tickers, prices & alerts"


def register(bot_app, scheduler, authorized) -> dict:
    from .commands import (
        watch_command, unwatch_command, watchlist_command,
        alert_command, alerts_command, delalert_command,
        price_command, prices_command, alert_callback,
    )
    from .jobs import check_price_alerts_job, daily_summary_job

    # ── Telegram commands ─────────────────────────────────────────────────────
    bot_app.add_handler(CommandHandler("watch",     watch_command,     filters=authorized))
    bot_app.add_handler(CommandHandler("unwatch",   unwatch_command,   filters=authorized))
    bot_app.add_handler(CommandHandler("watchlist", watchlist_command, filters=authorized))
    bot_app.add_handler(CommandHandler("alert",     alert_command,     filters=authorized))
    bot_app.add_handler(CommandHandler("alerts",    alerts_command,    filters=authorized))
    bot_app.add_handler(CommandHandler("delalert",  delalert_command,  filters=authorized))
    bot_app.add_handler(CommandHandler("price",     price_command,     filters=authorized))
    bot_app.add_handler(CommandHandler("prices",    prices_command,    filters=authorized))

    # ── Inline button callbacks ───────────────────────────────────────────────
    bot_app.add_handler(
        CallbackQueryHandler(
            alert_callback,
            pattern=r"^alert:(del|chart|ta|dismiss):.*$",
        )
    )

    # ── Scheduled jobs ────────────────────────────────────────────────────────
    scheduler.add_job(
        check_price_alerts_job,
        IntervalTrigger(minutes=5),
        id="finance_price_alert_check",
        replace_existing=True,
    )
    # Morning briefing — 9:00 AM IST, Mon–Fri
    scheduler.add_job(
        daily_summary_job,
        CronTrigger(hour=9, minute=0, day_of_week="mon-fri", timezone="Asia/Kolkata"),
        id="finance_daily_summary",
        replace_existing=True,
    )

    return {
        "name": NAME,
        "description": DESCRIPTION,
        "commands": [
            "watch", "unwatch", "watchlist",
            "alert", "alerts", "delalert",
            "price", "prices",
        ],
        "help": [
            "`/watch <ticker...> [--cat category]` — add one or more tickers to the watchlist",
            "`/unwatch <ticker...>` — remove one or more tickers and their alerts",
            "`/watchlist [category]` — list tracked tickers",
            "`/price <ticker>` — quick price with 24h change, ranges & 52W gauge",
            "`/prices [category]` — batch prices for all watchlisted tickers",
            "`/alert <ticker> > <price>` — set static price alert (or `trail -5%` / `trail +10%`)",
            "`/alerts` — list all active price alerts with one-tap delete",
            "`/delalert <id>` — delete a price alert by ID",
        ],
    }
