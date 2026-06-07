"""Technical analysis submodule registration."""
from telegram.ext import CommandHandler
from apscheduler.triggers.interval import IntervalTrigger

NAME        = "technical"
DESCRIPTION = "📊 Technical Analysis — charts, RSI, EMAs & signal alerts"


def register(bot_app, scheduler, authorized) -> dict:
    from .commands import ta_command, rsi_command, chart_command, signals_command
    from .jobs import scan_signals_job

    # ── Telegram commands ─────────────────────────────────────────────────────
    bot_app.add_handler(CommandHandler("ta",      ta_command,      filters=authorized))
    bot_app.add_handler(CommandHandler("rsi",     rsi_command,     filters=authorized))
    bot_app.add_handler(CommandHandler("chart",   chart_command,   filters=authorized))
    bot_app.add_handler(CommandHandler("signals", signals_command, filters=authorized))

    # ── Scheduled jobs ────────────────────────────────────────────────────────
    scheduler.add_job(
        scan_signals_job,
        IntervalTrigger(minutes=30),
        id="finance_ta_signal_scan",
        replace_existing=True,
    )

    return {
        "name": NAME,
        "description": DESCRIPTION,
        "commands": ["ta", "rsi", "chart", "signals"],
        "help": [
            "`/ta <ticker> [--tf 1d|1wk|1mo]` — full TA chart + RSI/EMA/volume summary",
            "`/rsi <ticker>` — quick RSI(14) check (text only)",
            "`/chart <ticker> [--tf 1d|1wk|1mo]` — chart image only",
            "`/signals` — show recent TA signals detected by the scanner",
        ],
    }
