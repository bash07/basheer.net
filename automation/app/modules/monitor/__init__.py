"""
Monitor module registration.
"""
from telegram.ext import CommandHandler
from apscheduler.triggers.interval import IntervalTrigger

NAME = "monitor"
DESCRIPTION = "📡 Monitor — uptime monitoring with Telegram alerts"


def register(bot_app, scheduler, authorized) -> dict:
    from .commands import addurl_command, rmurl_command, urls_command, check_command, uptime_command
    from .jobs import check_all_job

    bot_app.add_handler(CommandHandler("addurl", addurl_command, filters=authorized))
    bot_app.add_handler(CommandHandler("rmurl",  rmurl_command,  filters=authorized))
    bot_app.add_handler(CommandHandler("urls",   urls_command,   filters=authorized))
    bot_app.add_handler(CommandHandler("check",  check_command,  filters=authorized))
    bot_app.add_handler(CommandHandler("uptime", uptime_command, filters=authorized))

    scheduler.add_job(
        check_all_job,
        IntervalTrigger(minutes=5),
        id="monitor_check_all",
        replace_existing=True,
    )

    return {
        "name": NAME,
        "description": DESCRIPTION,
        "commands": ["addurl", "rmurl", "urls", "check", "uptime"],
        "help": [
            "`/addurl <url> [name]` — add endpoint to monitor",
            "`/rmurl <name>` — remove endpoint",
            "`/urls` — list all endpoints with status",
            "`/check` — manual check now",
            "`/uptime [name]` — 24h uptime stats",
        ],
    }
