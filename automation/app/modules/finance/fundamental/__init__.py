"""Fundamental analysis submodule registration."""
from telegram.ext import CommandHandler
from apscheduler.triggers.cron import CronTrigger

NAME        = "fundamental"
DESCRIPTION = "📈 Fundamental Analysis — Graham screen, earnings alerts"


def register(bot_app, scheduler, authorized) -> dict:
    from .commands import fundamental_command, screen_command, earnings_command
    from .jobs import weekly_screen_job, earnings_alert_job

    # ── Telegram commands ─────────────────────────────────────────────────────
    bot_app.add_handler(CommandHandler("fundamental", fundamental_command, filters=authorized))
    bot_app.add_handler(CommandHandler("screen",      screen_command,      filters=authorized))
    bot_app.add_handler(CommandHandler("earnings",    earnings_command,    filters=authorized))

    # ── Scheduled jobs ────────────────────────────────────────────────────────
    # Weekly Graham screen — Sunday 8:00 AM IST
    scheduler.add_job(
        weekly_screen_job,
        CronTrigger(day_of_week="sun", hour=8, minute=0, timezone="Asia/Kolkata"),
        id="finance_fundamental_weekly_screen",
        replace_existing=True,
    )
    # Daily earnings alert — 8:00 AM IST every day
    scheduler.add_job(
        earnings_alert_job,
        CronTrigger(hour=8, minute=0, timezone="Asia/Kolkata"),
        id="finance_fundamental_earnings_alert",
        replace_existing=True,
    )

    return {
        "name": NAME,
        "description": DESCRIPTION,
        "commands": ["fundamental", "screen", "earnings"],
        "help": [
            "`/fundamental <ticker> [--method graham]` — full fundamental screen + LLM verdict",
            "`/screen [--method graham]` — rank all watchlisted stocks by score",
            "`/earnings [days]` — upcoming earnings dates (default: 7 days)",
        ],
    }
