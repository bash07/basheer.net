"""Telegram command handlers for the finance/trader module.

Commands:
  /scan [ticker]         Force ICT scan on full watchlist or a single ticker
  /setups                List active trade signals from the last 48 hours
  /traderbias <ticker>   Show HTF RSI bias breakdown for a single ticker
  /confluence <ticker>   Show Volume Profile + S/R levels for a single ticker
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


def _safe_md(text: str) -> str:
    """Escape Markdown special characters in exception messages."""
    for ch in r"_*`[":
        text = text.replace(ch, f"\\{ch}")
    return text


# ── /scan ─────────────────────────────────────────────────────────────────────

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/scan [ticker] — Force an immediate ICT scan.

    With no argument: scans the full watchlist (same as the scheduled job).
    With a ticker:    scans only that single ticker regardless of market hours.
    """
    args = context.args or []
    single_ticker = args[0].upper() if args else None

    if single_ticker:
        msg = await update.message.reply_text(
            f"🔍 Scanning `{single_ticker}` for ICT setups…", parse_mode="Markdown"
        )
        try:
            result_text = await _scan_single(single_ticker)
            await msg.edit_text(result_text, parse_mode="Markdown")
        except Exception as e:
            logger.error("[trader] /scan %s: %s", single_ticker, e, exc_info=True)
            try:
                await msg.edit_text(f"❌ Scan failed for `{single_ticker}`: {_safe_md(str(e))}", parse_mode="Markdown")
            except Exception:
                await msg.edit_text(f"❌ Scan failed for {single_ticker}: {e}")
    else:
        msg = await update.message.reply_text("🔍 Scanning full watchlist for ICT setups…")
        try:
            from .jobs import scan_all_tickers
            await scan_all_tickers()
            await msg.edit_text("✅ Full watchlist scan complete. Any setups have been sent as alerts.")
        except Exception as e:
            logger.error("[trader] /scan (full): %s", e, exc_info=True)
            try:
                await msg.edit_text(f"❌ Full scan failed: {_safe_md(str(e))}", parse_mode="Markdown")
            except Exception:
                await msg.edit_text(f"❌ Full scan failed: {e}")


async def _scan_single(ticker: str) -> str:
    """Run the ICT scanner for a single ticker and return a formatted result."""
    import asyncio
    from sqlalchemy import select
    from core.db import get_db
    from modules.finance.watchlist.models import WatchlistEntry
    from .schemas import scan_mode_for, timeframes_for
    from .setups import check_all
    from .jobs import _fetch_tf_batch

    # Look up category from watchlist
    with get_db() as db:
        entry = db.execute(
            select(WatchlistEntry).where(WatchlistEntry.symbol == ticker)
        ).scalar_one_or_none()

    if entry is None:
        return (
            f"⚠️ *{ticker}* is not in your watchlist\\.\n"
            f"Add it first with `/watch {ticker}`\\."
        )

    mode = scan_mode_for(entry.category)
    htf_tf, ltf_tf, _ = timeframes_for(mode)

    # Fetch OHLCV
    ohlcv: dict = {}
    loop = asyncio.get_event_loop()
    from .jobs import _OHLCV_PARAMS
    htf_period, htf_interval = _OHLCV_PARAMS[htf_tf]
    ltf_period, ltf_interval = _OHLCV_PARAMS[ltf_tf]

    await loop.run_in_executor(
        None, _fetch_tf_batch, [ticker], htf_period, htf_interval, htf_tf, ohlcv, "htf", None
    )
    await loop.run_in_executor(
        None, _fetch_tf_batch, [ticker], ltf_period, ltf_interval, ltf_tf, ohlcv, "ltf",
        "4h" if mode == "swing" else None
    )

    htf_df = ohlcv.get(f"{ticker}:htf")
    ltf_df = ohlcv.get(f"{ticker}:ltf")

    if htf_df is None or ltf_df is None or htf_df.empty or ltf_df.empty:
        return f"❌ Could not fetch OHLCV data for `{ticker}`\\. Try again in a moment\\."

    setups = check_all(ticker, entry.category, htf_df, ltf_df)

    if not setups:
        from .bias import compute_htf_bias
        bias = compute_htf_bias(htf_df, mode)
        return (
            f"📭 *{ticker}* — No ICT setups found right now\\.\n\n"
            f"🧭 HTF Bias: *{bias.bias.replace('_', ' ').title()}*\n"
            f"_{bias.bias_notes}_"
        )

    lines = [f"🎯 *{ticker}* — {len(setups)} setup(s) found:\n"]
    for s in sorted(setups, key=lambda x: x.confluence.score, reverse=True):
        dir_e = "📈" if s.direction == "long" else "📉"
        lines.append(
            f"{dir_e} *{s.setup_name}* | Score: `{s.confluence.score:.1f}` | R:R: `{s.rr_ratio:.1f}×`"
        )
        lines.append(f"  Entry: `{s.entry_low:,.4f}` – `{s.entry_high:,.4f}` | SL: `{s.stop_loss:,.4f}` | TP1: `{s.tp1:,.4f}`")

    lines.append("\n_Full alerts sent above ↑_")

    # Also send full alerts
    from modules.finance._shared.notify import notify
    for s in sorted(setups, key=lambda x: x.confluence.score, reverse=True):
        try:
            await notify(s.to_telegram_text(), parse_mode="Markdown")
        except Exception as e:
            logger.warning("[trader] Failed to send setup alert: %s", e)

    return "\n".join(lines)


# ── /setups ───────────────────────────────────────────────────────────────────

async def setups_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setups — List active trade signals from the last 48 hours."""
    from sqlalchemy import select, desc
    from datetime import timedelta
    from core.db import get_db
    from .models import TradeSignal

    cutoff = __import__("datetime").datetime.now(__import__("datetime").timezone.utc) - timedelta(hours=48)

    with get_db() as db:
        rows = db.execute(
            select(TradeSignal)
            .where(TradeSignal.fired_at >= cutoff)
            .order_by(desc(TradeSignal.conf_score), desc(TradeSignal.fired_at))
            .limit(15)
        ).scalars().all()

    if not rows:
        await update.message.reply_text(
            "📭 No ICT setups in the last 48 hours\\. The scanner runs every 30 minutes\\.",
            parse_mode="Markdown",
        )
        return

    lines = ["🎯 *Active ICT Setups* \\(last 48h\\):\n"]
    for row in rows:
        ts      = row.fired_at.strftime("%d %b %H:%M")
        dir_e   = "📈" if row.direction == "long" else "📉"
        conf_e  = {"high": "🟢", "medium": "🟡", "low": "🟠"}.get(row.conf_label, "⚪")
        mode_tag = "\\[S\\]" if row.mode == "swing" else "\\[I\\]"
        lines.append(
            f"`{ts}` {mode_tag} {dir_e} *{row.ticker}* — {row.setup_name}\n"
            f"  {conf_e} Score: `{row.conf_score:.1f}` | R:R: `{row.rr_ratio:.1f}×` | "
            f"Entry: `{row.entry_mid:,.4f}` | SL: `{row.stop_loss:,.4f}`"
        )

    lines.append("\n_\\[S\\] = Swing  \\[I\\] = Intraday_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /traderbias ───────────────────────────────────────────────────────────────

async def traderbias_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/traderbias <ticker> — Show HTF RSI bias breakdown."""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: `/traderbias <ticker>`\nExample: `/traderbias AAPL`",
            parse_mode="Markdown",
        )
        return

    ticker = args[0].upper()
    msg    = await update.message.reply_text(
        f"🧭 Computing HTF bias for `{ticker}`…", parse_mode="Markdown"
    )

    try:
        from sqlalchemy import select
        from core.db import get_db
        from modules.finance.watchlist.models import WatchlistEntry
        from .schemas import scan_mode_for, timeframes_for
        from .bias import compute_htf_bias
        from .jobs import _fetch_tf_batch, _OHLCV_PARAMS
        import asyncio

        with get_db() as db:
            entry = db.execute(
                select(WatchlistEntry).where(WatchlistEntry.symbol == ticker)
            ).scalar_one_or_none()

        if entry is None:
            await msg.edit_text(
                f"⚠️ `{ticker}` is not in your watchlist\\. Add it first with `/watch {ticker}`\\.",
                parse_mode="Markdown",
            )
            return

        mode   = scan_mode_for(entry.category)
        htf_tf, _, _ = timeframes_for(mode)
        htf_period, htf_interval = _OHLCV_PARAMS[htf_tf]

        ohlcv: dict = {}
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, _fetch_tf_batch, [ticker], htf_period, htf_interval, htf_tf, ohlcv, "htf", None
        )

        htf_df = ohlcv.get(f"{ticker}:htf")
        if htf_df is None or htf_df.empty:
            await msg.edit_text(f"❌ Could not fetch HTF data for `{ticker}`\\.", parse_mode="Markdown")
            return

        bias = compute_htf_bias(htf_df, mode)

        bias_emoji = {
            "strong_bull":   "🟢🟢",
            "strong_bear":   "🔴🔴",
            "moderate_bull": "🟡📈",
            "moderate_bear": "🟡📉",
            "neutral":       "⚪",
        }.get(bias.bias, "⚪")

        hd_emoji = {"bullish": "✅", "bearish": "✅", "none": "—"}.get(bias.hidden_div, "—")
        regime_emoji = {"bull": "🟢", "bear": "🔴", "neutral": "⚪"}.get(bias.regime, "⚪")

        text = "\n".join([
            f"🧭 *HTF Bias — {ticker}* \\({mode.upper()} mode\\)",
            "━━━━━━━━━━━━━━━━━━━━",
            f"Timeframe:    `{htf_tf.upper()}`",
            f"RSI\\(14\\):     `{bias.rsi_value:.1f}`",
            f"Regime:       {regime_emoji} `{bias.regime.title()}`",
            f"Hidden Div:   {hd_emoji} `{bias.hidden_div.title()}`",
            f"",
            f"{bias_emoji} *Overall Bias: {bias.bias.replace('_', ' ').upper()}*",
            f"",
            f"_{bias.bias_notes}_",
            "━━━━━━━━━━━━━━━━━━━━",
        ])
        await msg.edit_text(text, parse_mode="Markdown")

    except Exception as e:
        logger.error("[trader] /traderbias %s: %s", ticker, e, exc_info=True)
        try:
            await msg.edit_text(
                f"❌ Bias computation failed for `{ticker}`: {_safe_md(str(e))}",
                parse_mode="Markdown",
            )
        except Exception:
            await msg.edit_text(f"❌ Bias computation failed for {ticker}: {e}")


# ── /confluence ───────────────────────────────────────────────────────────────

async def confluence_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/confluence <ticker> — Show Volume Profile + S/R levels."""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: `/confluence <ticker>`\nExample: `/confluence BTC-USD`",
            parse_mode="Markdown",
        )
        return

    ticker = args[0].upper()
    msg    = await update.message.reply_text(
        f"📊 Computing volume profile for `{ticker}`…", parse_mode="Markdown"
    )

    try:
        from sqlalchemy import select
        from core.db import get_db
        from modules.finance.watchlist.models import WatchlistEntry
        from .schemas import scan_mode_for, timeframes_for
        from .confluence import compute_volume_profile, get_sr_levels, format_vp_report
        from .jobs import _fetch_tf_batch, _OHLCV_PARAMS
        import asyncio

        with get_db() as db:
            entry = db.execute(
                select(WatchlistEntry).where(WatchlistEntry.symbol == ticker)
            ).scalar_one_or_none()

        if entry is None:
            await msg.edit_text(
                f"⚠️ `{ticker}` not in watchlist\\. Add it with `/watch {ticker}`\\.",
                parse_mode="Markdown",
            )
            return

        mode          = scan_mode_for(entry.category)
        htf_tf, ltf_tf, vp_lookback = timeframes_for(mode)
        htf_period, htf_interval = _OHLCV_PARAMS[htf_tf]
        ltf_period, ltf_interval = _OHLCV_PARAMS[ltf_tf]

        ohlcv: dict = {}
        loop = asyncio.get_event_loop()
        await asyncio.gather(
            loop.run_in_executor(None, _fetch_tf_batch, [ticker], htf_period, htf_interval, htf_tf, ohlcv, "htf", None),
            loop.run_in_executor(None, _fetch_tf_batch, [ticker], ltf_period, ltf_interval, ltf_tf, ohlcv, "ltf",
                                 "4h" if mode == "swing" else None),
        )

        htf_df = ohlcv.get(f"{ticker}:htf")
        ltf_df = ohlcv.get(f"{ticker}:ltf")

        if ltf_df is None or ltf_df.empty:
            await msg.edit_text(f"❌ Could not fetch LTF data for `{ticker}`\\.", parse_mode="Markdown")
            return

        current_price = float(ltf_df["Close"].iloc[-1])
        vp      = compute_volume_profile(ltf_df, vp_lookback)
        sr_lvls = get_sr_levels(ltf_df, htf_df if htf_df is not None else ltf_df, current_price)
        text    = format_vp_report(vp, sr_lvls, ticker)

        await msg.edit_text(text, parse_mode="Markdown")

    except Exception as e:
        logger.error("[trader] /confluence %s: %s", ticker, e, exc_info=True)
        try:
            await msg.edit_text(
                f"❌ Confluence report failed for `{ticker}`: {_safe_md(str(e))}",
                parse_mode="Markdown",
            )
        except Exception:
            await msg.edit_text(f"❌ Confluence report failed for {ticker}: {e}")
