"""Telegram command handlers for the finance/trader module.

Commands:
  /scan [ticker]         Force trade scan on full watchlist or a single ticker
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
    """/scan [ticker] — Force an immediate trade scan.

    With no argument: scans the full watchlist (same as the scheduled job).
    With a ticker:    scans only that single ticker regardless of market hours.
    """
    args = context.args or []
    single_ticker = args[0].upper() if args else None

    if single_ticker:
        msg = await update.message.reply_text(
            f"🔍 Scanning `{single_ticker}` for Trade setups…", parse_mode="Markdown"
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
        msg = await update.message.reply_text("🔍 Scanning full watchlist for Trade setups…")
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
    """Run the trading scanner for a single ticker and return a formatted result."""
    import asyncio
    from sqlalchemy import select
    from core.db import get_db
    from modules.finance.watchlist.models import WatchlistEntry
    from .schemas import scan_mode_for, timeframes_for
    from .setups import check_all
    from .data import get_provider

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

    # Fetch OHLCV via configured DataProvider for this category
    provider = get_provider(entry.category)
    loop = asyncio.get_event_loop()

    htf_df, ltf_df = await asyncio.gather(
        loop.run_in_executor(None, provider.fetch_ohlcv, ticker, htf_tf),
        loop.run_in_executor(None, provider.fetch_ohlcv, ticker, ltf_tf),
    )

    if htf_df is None or ltf_df is None or htf_df.empty or ltf_df.empty:
        return f"❌ Could not fetch OHLCV data for `{ticker}`\\. Try again in a moment\\."

    setups = check_all(ticker, entry.category, htf_df, ltf_df)

    if not setups:
        from .bias import compute_htf_bias
        bias = compute_htf_bias(htf_df, mode)
        return (
            f"📭 *{ticker}* — No Trade setups found right now\\.\n\n"
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
            "📭 No Trade setups in the last 48 hours\\. The scanner runs every 30 minutes\\.",
            parse_mode="Markdown",
        )
        return

    lines = ["🎯 *Active Trade Setups* \\(last 48h\\):\n"]
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

        from .data import get_provider
        provider = get_provider(entry.category)
        loop = asyncio.get_event_loop()
        htf_df = await loop.run_in_executor(None, provider.fetch_ohlcv, ticker, htf_tf)

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

        from .data import get_provider
        provider = get_provider(entry.category)
        loop = asyncio.get_event_loop()

        htf_df, ltf_df = await asyncio.gather(
            loop.run_in_executor(None, provider.fetch_ohlcv, ticker, htf_tf),
            loop.run_in_executor(None, provider.fetch_ohlcv, ticker, ltf_tf),
        )

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


# ── Approve / Dismiss callback ────────────────────────────────────────────────

async def approve_dismiss_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle inline keyboard button presses for trade signal approval.

    Callback data format: "trade:approve:<signal_id>" | "trade:dismiss:<signal_id>"

    On Approve: marks the signal as human_approved=True in the DB,
                edits the Telegram message to show a confirmation.
    On Dismiss: marks the signal as human_approved=False in the DB,
                edits the Telegram message to show a dismissal notice.
    """
    query = update.callback_query
    await query.answer()   # acknowledge the button tap immediately (removes loading spinner)

    data = query.data or ""
    # Expected: "trade:approve:42" or "trade:dismiss:42"
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "trade":
        logger.warning("[trader] Unexpected callback data: %s", data)
        return

    action    = parts[1]          # "approve" | "dismiss"
    signal_id = int(parts[2])

    approved  = (action == "approve")

    # Update the database
    try:
        from sqlalchemy import select
        from core.db import get_db
        from .models import TradeSignal

        with get_db() as db:
            row = db.execute(
                select(TradeSignal).where(TradeSignal.id == signal_id)
            ).scalar_one_or_none()

            if row:
                row.human_approved = approved
                db.commit()
            else:
                logger.warning("[trader] approve_dismiss: signal %d not found in DB", signal_id)

    except Exception as e:
        logger.error("[trader] approve_dismiss DB update failed: %s", e, exc_info=True)

    # Edit the original Telegram message to remove the buttons and confirm
    try:
        if approved:
            status_line = (
                "\n\n✅ *Trade Approved by Human*\n"
                "_You have approved this setup\\. The signal has been recorded\\._"
            )
        else:
            status_line = (
                "\n\n❌ *Trade Dismissed by Human*\n"
                "_You dismissed this setup\\. No action will be taken\\._"
            )

        original_caption = query.message.caption or ""
        new_caption = original_caption + status_line

        # Telegram captions max 1024 chars
        if len(new_caption) > 1024:
            new_caption = new_caption[:1020] + "…"

        await query.edit_message_caption(
            caption=new_caption,
            parse_mode="Markdown",
            reply_markup=None,    # removes the buttons
        )
    except Exception as e:
        logger.error("[trader] approve_dismiss: failed to edit message: %s", e)


# ── /performance ──────────────────────────────────────────────────────────────

async def performance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/performance — Show live performance statistics per strategy."""
    from sqlalchemy import select
    from core.db import get_db
    from .models import TradeSignal

    with get_db() as db:
        rows = db.execute(select(TradeSignal)).scalars().all()

    if not rows:
        await update.message.reply_text("📭 No trade signals recorded yet.", parse_mode="Markdown")
        return

    # Aggregate stats per setup_name
    stats = {}
    for r in rows:
        sn = r.setup_name
        if sn not in stats:
            stats[sn] = {"W": 0, "L": 0, "OPEN": 0, "RR_sum": 0.0}
        
        # Outcome can be "WIN", "LOSS", "OPEN"
        stats[sn][r.outcome] += 1
        if r.outcome == "WIN" and r.realised_rr is not None:
            stats[sn]["RR_sum"] += r.realised_rr

    lines = ["📊 *Live Performance Stats*\n"]
    lines.append("```text")
    lines.append(f"{'Setup Name':<26} {'W':<4} {'L':<4} {'OPEN':<5} {'WR%':<6} {'Avg RR':<6}")
    lines.append("-" * 55)

    for sn, s in stats.items():
        w, l, o = s["W"], s["L"], s["OPEN"]
        total_closed = w + l
        wr = (w / total_closed * 100) if total_closed > 0 else 0.0
        avg_rr = (s["RR_sum"] / w) if w > 0 else 0.0
        
        name_short = sn[:25]
        lines.append(
            f"{name_short:<26} {w:<4} {l:<4} {o:<5} {wr:>4.1f}%  {avg_rr:>4.1f}x"
        )
        
    lines.append("```")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
