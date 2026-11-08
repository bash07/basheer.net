"""Indian Stock Screener submodule registration."""
from telegram.ext import CommandHandler
from apscheduler.triggers.cron import CronTrigger

NAME        = "screener"
DESCRIPTION = "🔍 Market Screener — Weekly TA & Monthly FA dynamic candidate discovery"


def register(bot_app, scheduler, authorized) -> dict:
    from .commands import screen_run_command, screen_status_command, screen_purge_command
    from .jobs import weekly_ta_screen_job, monthly_fa_screen_job, daily_purge_job

    # ── Telegram commands ─────────────────────────────────────────────────────
    bot_app.add_handler(CommandHandler("screen_run",    screen_run_command,    filters=authorized))
    bot_app.add_handler(CommandHandler("screen_status", screen_status_command, filters=authorized))
    bot_app.add_handler(CommandHandler("screen_purge",  screen_purge_command,  filters=authorized))

    # ── Scheduled jobs ────────────────────────────────────────────────────────
    # Weekly TA screen — Sunday 07:00 AM IST
    scheduler.add_job(
        weekly_ta_screen_job,
        CronTrigger(day_of_week="sun", hour=7, minute=0, timezone="Asia/Kolkata"),
        id="finance_screener_weekly_ta",
        replace_existing=True,
    )
    # Monthly FA screen — 1st of month 07:30 AM IST
    scheduler.add_job(
        monthly_fa_screen_job,
        CronTrigger(day=1, hour=7, minute=30, timezone="Asia/Kolkata"),
        id="finance_screener_monthly_fa",
        replace_existing=True,
    )
    # Daily dynamic entry cleanup — 00:05 AM IST
    scheduler.add_job(
        daily_purge_job,
        CronTrigger(hour=0, minute=5, timezone="Asia/Kolkata"),
        id="finance_screener_daily_purge",
        replace_existing=True,
    )

    return {
        "name": NAME,
        "description": DESCRIPTION,
        "commands": ["screen_run", "screen_status", "screen_purge"],
        "help": [
            "`/screen_run [weekly|monthly] [--dry-run]` — discover & populate Indian market setups",
            "`/screen_status` — view active dynamic watchlist candidates and TTL",
            "`/screen_purge [weekly|monthly|all]` — clear dynamic entries preserving manual watchlist",
        ],
    }
