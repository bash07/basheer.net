"""Technical Analysis — Telegram command handlers.

Commands:
  /ta <ticker> [--tf 1d|1wk|1mo]   Full TA: chart image + text summary
  /rsi <ticker>                     Quick RSI text-only response
  /chart <ticker> [--tf 1d|1wk|1mo] Chart image only (no summary text)
  /signals                          List all unread signals from SignalLog
"""
import logging
import os

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)
_VALID_TF = ("1d", "1wk", "1mo")


def _parse_args(args: list[str]) -> tuple[str | None, str]:
    """Extract (symbol, timeframe) from command args list."""
    if not args:
        return None, "1d"
    symbol    = args[0].upper()
    timeframe = "1d"
    if "--tf" in args:
        idx = args.index("--tf")
        if idx + 1 < len(args) and args[idx + 1].lower() in _VALID_TF:
            timeframe = args[idx + 1].lower()
    return symbol, timeframe


def _safe_err(e: Exception) -> str:
    """Escape special Markdown characters in exception messages to prevent
    Telegram from rejecting edit_text calls with parse_mode='Markdown'."""
    return (str(e)
            .replace("_", r"\_")
            .replace("*", r"\*")
            .replace("`", r"\`")
            .replace("[", r"\["))



async def ta_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ta <ticker> [--tf 1d|1wk|1mo] — full TA chart + summary"""
    symbol, tf = _parse_args(context.args or [])
    if not symbol:
        await update.message.reply_text(
            "Usage: `/ta <ticker> [--tf 1d|1wk|1mo]`\nExample: `/ta AAPL --tf 1wk`",
            parse_mode="Markdown",
        )
        return

    msg = await update.message.reply_text(
        f"📊 Computing TA for `{symbol}` ({tf.upper()})…", parse_mode="Markdown"
    )
    try:
        from .charts import fetch_and_render
        from .indicators import format_ta_summary

        png_path, result = await fetch_and_render(symbol, tf)
        caption = format_ta_summary(result)

        with open(png_path, "rb") as f:
            await update.message.reply_photo(photo=f, caption=caption, parse_mode="Markdown")
        os.unlink(png_path)
        await msg.delete()

    except ValueError as e:
        try:
            await msg.edit_text(f"❌ {_safe_err(e)}")
        except Exception:
            pass
    except Exception as e:
        logger.error("[technical] /ta %s: %s", symbol, e, exc_info=True)
        try:
            await msg.edit_text(
                f"❌ TA failed for `{symbol}`: {_safe_err(e)}",
                parse_mode="Markdown",
            )
        except Exception:
            # Last-resort: plain text, no markdown
            try:
                await msg.edit_text(f"❌ TA failed for {symbol}: {e}")
            except Exception:
                pass


async def rsi_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/rsi <ticker> — quick RSI check, text only"""
    symbol, _ = _parse_args(context.args or [])
    if not symbol:
        await update.message.reply_text("Usage: `/rsi <ticker>`", parse_mode="Markdown")
        return

    msg = await update.message.reply_text(f"🔍 Fetching RSI for `{symbol}`…", parse_mode="Markdown")
    try:
        from .charts import fetch_ta_only

        r      = await fetch_ta_only(symbol)
        rsi_s  = f"{r.rsi:.1f}" if r.rsi is not None else "N/A"
        sig    = r.rsi_signal.upper()
        emoji  = {"OVERBOUGHT": "🔴", "OVERSOLD": "🟢", "NEUTRAL": "⚪"}.get(sig, "⚪")

        await msg.edit_text(
            f"{emoji} *{symbol}* RSI(14): `{rsi_s}` — *{sig}*\n"
            f"💵 Price: `{r.current_price:,.2f}`  |  Trend: *{r.trend.title()}*",
            parse_mode="Markdown",
        )
    except ValueError as e:
        try:
            await msg.edit_text(f"❌ {_safe_err(e)}")
        except Exception:
            pass
    except Exception as e:
        logger.error("[technical] /rsi %s: %s", symbol, e, exc_info=True)
        try:
            await msg.edit_text(
                f"❌ RSI failed for `{symbol}`: {_safe_err(e)}",
                parse_mode="Markdown",
            )
        except Exception:
            try:
                await msg.edit_text(f"❌ RSI failed for {symbol}: {e}")
            except Exception:
                pass


async def chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/chart <ticker> [--tf 1d|1wk|1mo] — chart image only"""
    symbol, tf = _parse_args(context.args or [])
    if not symbol:
        await update.message.reply_text(
            "Usage: `/chart <ticker> [--tf 1d|1wk|1mo]`", parse_mode="Markdown"
        )
        return

    msg = await update.message.reply_text(
        f"📈 Rendering chart for `{symbol}` ({tf.upper()})…", parse_mode="Markdown"
    )
    try:
        from .charts import fetch_and_render
        import os

        png_path, result = await fetch_and_render(symbol, tf)
        with open(png_path, "rb") as f:
            await update.message.reply_photo(
                photo=f,
                caption=f"📊 *{symbol}* — {tf.upper()}  |  💵 `{result.current_price:,.2f}`",
                parse_mode="Markdown",
            )
        os.unlink(png_path)
        await msg.delete()

    except ValueError as e:
        try:
            await msg.edit_text(f"❌ {_safe_err(e)}")
        except Exception:
            pass
    except Exception as e:
        logger.error("[technical] /chart %s: %s", symbol, e, exc_info=True)
        try:
            await msg.edit_text(
                f"❌ Chart failed for `{symbol}`: {_safe_err(e)}",
                parse_mode="Markdown",
            )
        except Exception:
            try:
                await msg.edit_text(f"❌ Chart failed for {symbol}: {e}")
            except Exception:
                pass


async def signals_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/signals — list all unread TA signals from the SignalLog"""
    from sqlalchemy import select, desc
    from core.db import get_db
    from .models import SignalLog

    with get_db() as db:
        rows = db.execute(
            select(SignalLog)
            .where(SignalLog.notified == True)   # noqa: E712
            .order_by(desc(SignalLog.detected_at))
            .limit(20)
        ).scalars().all()

    if not rows:
        await update.message.reply_text(
            "No signals detected yet. The scanner runs every 30 minutes.",
            parse_mode="Markdown",
        )
        return

    lines = ["⚡ *Recent TA Signals*\n"]
    for row in rows:
        ts = row.detected_at.strftime("%d %b %H:%M")
        lines.append(f"`{ts}` *{row.symbol}* — {row.description}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
