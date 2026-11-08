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
    """Escape Markdown special characters in exception messages.

    Escapes characters that Telegram Markdown V1 may misparse in inline text:
    * _ — italic marker
    * * — bold marker
    * ` — code span marker
    * [ ] — link syntax anchor characters
    """
    for ch in r"_*`[]":
        text = text.replace(ch, f"\\{ch}")
    return text


# ── /scan ─────────────────────────────────────────────────────────────────────

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/scan [ticker] — Force an immediate trade scan.

    With no argument: scans the full watchlist (same as the scheduled job).
    With a ticker:    scans only that single ticker regardless of market hours.
    """
    args = context.args or []
    single_ticker = args[0].upper().strip() if args else None

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
            stats = await scan_all_tickers()
            if stats and stats.get("scanned", 0) == 0:
                closed = ", ".join(sorted(stats.get("closed_categories", []))) or "All monitored markets"
                await msg.edit_text(
                    f"⚠️ No financial markets currently open for watchlist tickers ({closed} closed).\n"
                    "Use `/scan <ticker>` to scan a specific symbol off-hours.",
                    parse_mode="Markdown",
                )
            elif stats:
                scanned = stats.get("scanned", 0)
                setups_count = stats.get("new_setups", 0)
                setup_msg = f"{setups_count} new setup(s) sent as alerts." if setups_count > 0 else "No new setups found."
                await msg.edit_text(
                    f"✅ Full watchlist scan complete.\n"
                    f"Scanned `{scanned}` active ticker(s). {setup_msg}",
                    parse_mode="Markdown",
                )
            else:
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
    from datetime import datetime, timezone
    from sqlalchemy import select
    from core.db import get_db
    from modules.finance.watchlist.models import WatchlistEntry
    from .schemas import scan_mode_for, timeframe_triad_for
    from .setups import check_all
    from .data import get_provider
    from .bias import compute_htf_bias
    from .regimes import classify_bar_regime, classify_macro_regime
    from .jobs import _update_open_trades_for_ticker
    from .models import TradeSignal
    from .chart import generate_chart
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from modules.finance._shared.notify import notify_photo, notify

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
    triad = timeframe_triad_for(mode)

    # Fetch 3-Tier Multi-Timeframe Triad OHLCV via configured DataProvider for this category
    provider = get_provider(entry.category)
    loop = asyncio.get_event_loop()

    df_htf, df_mtf, df_trigger = await asyncio.gather(
        loop.run_in_executor(None, provider.fetch_ohlcv, ticker, triad.htf),
        loop.run_in_executor(None, provider.fetch_ohlcv, ticker, triad.mtf),
        loop.run_in_executor(None, provider.fetch_ohlcv, ticker, triad.trigger),
    )

    if df_htf is None or df_trigger is None or df_htf.empty or df_trigger.empty:
        return f"❌ Could not fetch OHLCV data for `{ticker}`\\. Try again in a moment\\."

    # Update live outcome of previously fired setups and open OMS positions
    with get_db() as db:
        _update_open_trades_for_ticker(ticker, df_trigger, db)
        try:
            from .oms import update_open_positions_for_ticker
            update_open_positions_for_ticker(ticker, df_trigger, db)
        except Exception as e:
            logger.warning("[trader] OMS position update failed for %s: %s", ticker, e)
        db.commit()

    # Precompute Macro Bias and Institutional Market Regime
    bias = compute_htf_bias(df_htf, mode)
    regime_df = df_mtf if (df_mtf is not None and len(df_mtf) >= 20) else df_htf
    regime_info = classify_bar_regime(regime_df)
    macro_regime = classify_macro_regime(bias.bias, regime_info.adx, regime_info.name)

    # Evaluate 3-tier triad setups
    setups = check_all(
        ticker,
        entry.category,
        df_htf,
        df_trigger,
        df_mtf=df_mtf,
        df_trigger=df_trigger,
        mode=mode,
    )

    regime_emoji = {
        "Orderly Trend Expansion": "🌊",
        "Horizontal Range Chop": "🦀",
        "High-Volatility Stop Raid": "⚡",
        "Parabolic Blow-Off / Cascade": "🚀",
        "Volatility Squeeze (Coil)": "🗜️",
        "Broadening Megaphone": "📣",
        "Creeping Drift / Melt-Up": "📈",
        "Macro News Shock / Vacuum": "💥",
    }.get(regime_info.name, "🏛️")

    if not setups:
        lines = [
            f"📭 *{ticker}* — No Trade setups found right now\\.\n",
            f"🧭 *Macro Bias:* `{bias.bias.replace('_', ' ').upper()}` \\({macro_regime}\\)",
            f"{regime_emoji} *Market Regime:* `{regime_info.name}` \\(ADX: `{regime_info.adx:.1f}`\\)",
        ]
        if bias.bias_notes:
            lines.append(f"_{_safe_md(bias.bias_notes)}_")
        if regime_info.details:
            lines.append(f"_{_safe_md(regime_info.details)}_")
        return "\n".join(lines)

    # Persist detected setups to DB so they can be approved/dismissed via OMS
    now = datetime.now(timezone.utc)
    signal_entries = []
    with get_db() as db:
        for s in setups:
            row = TradeSignal(
                ticker=s.ticker,
                category=s.category,
                mode=s.mode,
                setup_name=s.setup_name,
                direction=s.direction,
                setup_type=s.setup_type,
                bias=s.bias.bias,
                conf_score=s.confluence.score,
                conf_label=s.confluence.label,
                entry_mid=s.entry_mid,
                stop_loss=s.stop_loss,
                tp1=s.tp1,
                rr_ratio=s.rr_ratio,
                setup_json=s.to_db_json(),
                fired_at=now,
                notified=True,
            )
            db.add(row)
            db.flush()
            signal_entries.append((s, row.id))
        db.commit()

    # Dispatch interactive chart alerts with Approve/Dismiss buttons
    for s, sig_id in sorted(signal_entries, key=lambda x: x[0].confluence.score, reverse=True):
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Approve",
                    callback_data=f"trade:approve:{sig_id}",
                ),
                InlineKeyboardButton(
                    "❌ Dismiss",
                    callback_data=f"trade:dismiss:{sig_id}",
                ),
            ]
        ])
        try:
            png_path = await loop.run_in_executor(None, generate_chart, s, df_trigger)
            if png_path:
                caption = s.to_telegram_text()
                if len(caption) > 1024:
                    caption = caption[:1020] + "…"
                await notify_photo(
                    png_path,
                    caption=caption,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
            else:
                await notify(
                    s.to_telegram_text(),
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
        except Exception as e:
            logger.warning("[trader] Failed to send setup alert for %s: %s", ticker, e)
            try:
                await notify(s.to_telegram_text(), parse_mode="Markdown", reply_markup=keyboard)
            except Exception:
                pass

    lines = [
        f"🎯 *{ticker}* — {len(setups)} setup(s) found:\n",
        f"🏛️ Regime: `{regime_info.name}` | Bias: `{bias.bias.replace('_', ' ').upper()}`\n",
    ]
    for s in sorted(setups, key=lambda x: x.confluence.score, reverse=True):
        dir_e = "📈" if s.direction == "long" else "📉"
        lines.append(
            f"{dir_e} *{s.setup_name}* | Score: `{s.confluence.score:.1f}` | R:R: `{s.rr_ratio:.1f}×`"
        )
        lines.append(f"  Entry: `{s.entry_low:,.4f}` – `{s.entry_high:,.4f}` | SL: `{s.stop_loss:,.4f}` | TP1: `{s.tp1:,.4f}`")

    lines.append("\n_Full interactive alerts with chart & approval buttons sent above ↑_")
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
        from .schemas import scan_mode_for, timeframe_triad_for
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
        triad  = timeframe_triad_for(mode)
        htf_tf = triad.htf

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

    # Update the database & OMS execution
    pos = None
    pos_err = None
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

        if approved:
            from .oms import open_position_from_signal
            pos, pos_err = open_position_from_signal(signal_id)

    except Exception as e:
        logger.error("[trader] approve_dismiss DB/OMS update failed: %s", e, exc_info=True)
        pos_err = str(e)

    # Edit the original Telegram message to remove the buttons and confirm
    try:
        if approved:
            if pos:
                status_line = (
                    f"\n\n✅ *Trade Approved & Position Opened \\[{pos.broker.upper()}\\]*\n"
                    f"Position `#{pos.id}`: *{pos.direction.upper()}* `{pos.qty:g}` *{pos.ticker}* @ `{pos.entry_price:,.4f}`\n"
                    f"SL: `{pos.stop_loss:,.4f}` | TP1: `{pos.take_profit:,.4f}`\n"
                    f"_Live position tracking is active\\._"
                )
            else:
                status_line = (
                    f"\n\n⚠️ *Trade Approved — Execution Skipped*\n"
                    f"Reason: _{_safe_md(pos_err or 'Risk limit exceeded')}_\n"
                    f"_Signal recorded as approved, but no position opened\\._"
                )
        else:
            status_line = (
                "\n\n❌ *Trade Dismissed by Human*\n"
                "_You dismissed this setup\\. No position was opened\\._"
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


# ── /positions ────────────────────────────────────────────────────────────────

async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/positions — List all currently open trading positions with live P&L."""
    from core.db import get_db
    from .oms import get_open_positions, get_broker

    with get_db() as db:
        open_positions = get_open_positions(db)

    if not open_positions:
        await update.message.reply_text(
            "📭 No open positions currently\\.\n"
            "_Approve trade signals from scanner alerts to open positions\\._",
            parse_mode="Markdown",
        )
        return

    broker = get_broker()
    lines = [f"📊 *Open Trading Positions* \\({len(open_positions)}\\):\n"]
    lines.append("```text")
    lines.append(f"{'ID':<4} {'Ticker':<9} {'Side':<5} {'Qty':<7} {'Entry':<9} {'Curr':<9} {'PnL ($)':<9} {'R:R':<5}")
    lines.append("-" * 62)

    total_unrealised_pnl = 0.0
    for p in open_positions:
        curr_price = broker.get_current_price(p.ticker)
        mult = 1.0 if p.direction == "long" else -1.0
        qty = p.qty or 1.0
        pnl = (curr_price - p.entry_price) * qty * mult if curr_price > 0 else 0.0
        total_unrealised_pnl += pnl

        unit_risk = abs(p.entry_price - p.stop_loss)
        unrealised_rr = (
            ((curr_price - p.entry_price) * mult / unit_risk)
            if unit_risk > 0 and curr_price > 0
            else 0.0
        )

        side_str = "BUY" if p.direction == "long" else "SELL"
        lines.append(
            f"#{p.id:<3} {p.ticker:<9} {side_str:<5} {qty:<7.2f} "
            f"{p.entry_price:<9.2f} {curr_price:<9.2f} {pnl:+<9.2f} {unrealised_rr:+<4.1f}R"
        )

    lines.append("-" * 62)
    lines.append(f"Total Unrealised PnL: ${total_unrealised_pnl:+,.2f}")
    lines.append("```")
    lines.append("\n_Use `/close <id>` to exit a position manually at market price._")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /close ────────────────────────────────────────────────────────────────────

async def close_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/close <position_id> — Manually close an open position at market price."""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: `/close <position_id>`\nExample: `/close 1`",
            parse_mode="Markdown",
        )
        return

    try:
        position_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Position ID must be an integer.")
        return

    from .oms import close_position_manual
    trade, msg = close_position_manual(position_id=position_id)

    if not trade:
        await update.message.reply_text(
            f"❌ Failed to close position: {_safe_md(msg)}",
            parse_mode="Markdown",
        )
        return

    icon = "🎉" if trade.outcome == "WIN" else ("🛑" if trade.outcome == "LOSS" else "⚪")
    text = "\n".join([
        f"✅ *Position #{trade.position_id} Closed [Manual]*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"Symbol:       `{trade.ticker}` \\({trade.direction.upper()}\\)",
        f"Entry Price:  `{trade.entry_price:,.4f}`",
        f"Exit Price:   `{trade.exit_price:,.4f}`",
        f"Quantity:     `{trade.qty:g}`",
        f"Outcome:      {icon} *{trade.outcome}*",
        f"Realised RR:  `{trade.realised_rr:+.2f}R`",
        f"Realised PnL: `${trade.realised_pnl:+,.2f}`",
        "━━━━━━━━━━━━━━━━━━━━",
    ])
    await update.message.reply_text(text, parse_mode="Markdown")


# ── /account ──────────────────────────────────────────────────────────────────

async def account_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/account — Show paper trading account balance, equity, and performance."""
    from core.db import get_db
    from .oms import get_account_summary

    with get_db() as db:
        summary = get_account_summary(db)

    pnl_icon = "🟢" if summary["unrealised_pnl"] >= 0 else "🔴"
    realised_icon = "🟢" if summary["total_realised_pnl"] >= 0 else "🔴"

    text = "\n".join([
        f"💼 *Trading Account Summary* \\[{summary['broker'].title()}\\]",
        "━━━━━━━━━━━━━━━━━━━━",
        f"💵 Cash Balance:     `${summary['balance']:,.2f} {summary['currency']}`",
        f"📈 Account Equity:    `${summary['equity']:,.2f} {summary['currency']}`",
        f"{pnl_icon} Unrealised P&L:   `${summary['unrealised_pnl']:+,.2f}`",
        f"🎯 Open Positions:   `{summary['open_positions_count']} / {summary['max_open_positions']}`",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🏆 Closed Trades:    `{summary['total_trades']}` \\({summary['wins']}W / {summary['losses']}L\\)",
        f"🎯 Win Rate:         `{summary['win_rate']:.1f}%`",
        f"📐 Average RR:       `{summary['avg_rr']:.2f}×`",
        f"{realised_icon} Realised P&L:    `${summary['total_realised_pnl']:+,.2f}`",
        f"⚙️ Risk Per Trade:   `{summary['risk_per_trade_pct']*100:.1f}%`",
        "━━━━━━━━━━━━━━━━━━━━",
    ])
    await update.message.reply_text(text, parse_mode="Markdown")


# ── /performance ──────────────────────────────────────────────────────────────

async def performance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/performance — Show executed trades analytics and signal scanner stats."""
    from sqlalchemy import select
    from core.db import get_db
    from .models import Trade, TradeSignal

    try:
        with get_db() as db:
            trades = db.execute(select(Trade)).scalars().all()
            signals = db.execute(select(TradeSignal)).scalars().all()

        if not trades and not signals:
            await update.message.reply_text("📭 No trading records found yet.", parse_mode="Markdown")
            return

        lines = ["📊 *Trading Performance Analytics*\n"]

        # 1. Executed Trades (OMS Ledger)
        if trades:
            wins = sum(1 for t in trades if t.outcome == "WIN")
            losses = sum(1 for t in trades if t.outcome == "LOSS")
            be = sum(1 for t in trades if t.outcome == "BREAKEVEN")
            total = wins + losses
            wr = (wins / total * 100) if total > 0 else 0.0
            pnl = sum((t.realised_pnl or 0.0) for t in trades)
            rr_sum = sum((t.realised_rr or 0.0) for t in trades if t.outcome == "WIN")
            avg_rr = (rr_sum / wins) if wins > 0 else 0.0

            be_str = f" / {be}BE" if be > 0 else ""
            lines.append("*Executed Trades (OMS):*")
            lines.append(
                f"  • Total Closed: `{len(trades)}` ({wins}W / {losses}L{be_str})\n"
                f"  • Win Rate:     `{wr:.1f}%`\n"
                f"  • Avg Win RR:   `{avg_rr:.2f}×`\n"
                f"  • Realised P&L: `${pnl:+,.2f}`\n"
            )
        else:
            lines.append("*Executed Trades (OMS):* _None closed yet_\n")

        # 2. Algorithmic Signals Breakdown
        if signals:
            stats: dict[str, dict] = {}
            for r in signals:
                sn = r.setup_name or "Unknown"
                if sn not in stats:
                    stats[sn] = {"W": 0, "L": 0, "OPEN": 0, "BE": 0, "RR_sum": 0.0}

                outcome = (r.outcome or "OPEN").upper()
                if outcome in ("WIN", "W"):
                    stats[sn]["W"] += 1
                    if r.realised_rr is not None:
                        stats[sn]["RR_sum"] += r.realised_rr
                elif outcome in ("LOSS", "L"):
                    stats[sn]["L"] += 1
                elif outcome in ("BREAKEVEN", "BE"):
                    stats[sn]["BE"] += 1
                else:
                    stats[sn]["OPEN"] += 1

            lines.append("*Signal Scanner Stats (Raw Signals):*")
            lines.append("```text")
            lines.append(f"{'Setup Name':<25} {'W':<3} {'L':<3} {'OPN':<4} {'WR%':<5} {'AvgRR':<5}")
            lines.append("-" * 52)

            for sn, s in stats.items():
                w, l, o = s["W"], s["L"], s["OPEN"]
                total_closed = w + l
                swr = (w / total_closed * 100) if total_closed > 0 else 0.0
                s_avg_rr = (s["RR_sum"] / w) if w > 0 else 0.0
                name_short = sn[:24]
                lines.append(
                    f"{name_short:<25} {w:<3} {l:<3} {o:<4} {swr:>4.0f}% {s_avg_rr:>4.1f}x"
                )
            lines.append("```")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error("[trader] /performance error: %s", e, exc_info=True)
        await update.message.reply_text(
            f"❌ Failed to fetch performance stats: {_safe_md(str(e))}",
            parse_mode="Markdown",
        )


# ── /resume ───────────────────────────────────────────────────────────────────

async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/resume — Reset the OMS Circuit Breaker kill switch."""
    from .oms import resume_trading
    resume_trading()
    await update.message.reply_text(
        "🟢 *OMS Circuit Breaker Reset*\n"
        "Automated trade approvals and OMS orders have been resumed.",
        parse_mode="Markdown",
    )


# ── /wyckoff ──────────────────────────────────────────────────────────────────

async def wyckoff_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/wyckoff [ticker] — On-demand Wyckoff accumulation/distribution diagnostics.

    With a ticker: detailed breakdown of squeeze, volume flow, IBS, wick ratio, divergences, and Phase C/D triggers.
    With no argument: scan active watchlist and show an overview summary table.
    """
    args = context.args or []
    ticker = args[0].upper().strip() if args else None

    if ticker:
        msg = await update.message.reply_text(
            f"🏛️ Evaluating Wyckoff order flow for `{ticker}`…",
            parse_mode="Markdown",
        )
        try:
            result_text = await _wyckoff_single(ticker)
            await msg.edit_text(result_text, parse_mode="Markdown")
        except Exception as e:
            logger.error("[trader] /wyckoff %s: %s", ticker, e, exc_info=True)
            try:
                await msg.edit_text(
                    f"❌ Wyckoff evaluation failed for `{ticker}`: {_safe_md(str(e))}",
                    parse_mode="Markdown",
                )
            except Exception:
                await msg.edit_text(f"❌ Wyckoff evaluation failed for {ticker}: {e}")
    else:
        msg = await update.message.reply_text(
            "🏛️ Scanning watchlist for Wyckoff accumulation / distribution…",
            parse_mode="Markdown",
        )
        try:
            result_text = await _wyckoff_overview()
            await msg.edit_text(result_text, parse_mode="Markdown")
        except Exception as e:
            logger.error("[trader] /wyckoff overview: %s", e, exc_info=True)
            try:
                await msg.edit_text(
                    f"❌ Wyckoff scan failed: {_safe_md(str(e))}",
                    parse_mode="Markdown",
                )
            except Exception:
                await msg.edit_text(f"❌ Wyckoff scan failed: {e}")


async def _wyckoff_single(ticker: str) -> str:
    """Run Wyckoff order-flow diagnostics on a single ticker."""
    import asyncio
    from sqlalchemy import select
    from core.db import get_db
    from modules.finance.watchlist.models import WatchlistEntry
    from .schemas import scan_mode_for, timeframe_triad_for
    from .data import get_provider
    from .indicators import compute_indicators

    with get_db() as db:
        entry = db.execute(
            select(WatchlistEntry).where(WatchlistEntry.symbol == ticker)
        ).scalar_one_or_none()

    category = entry.category if entry else "us_stock"
    mode = scan_mode_for(category)
    triad = timeframe_triad_for(mode)

    provider = get_provider(category)
    loop = asyncio.get_event_loop()

    df_htf, df_trigger = await asyncio.gather(
        loop.run_in_executor(None, provider.fetch_ohlcv, ticker, triad.htf),
        loop.run_in_executor(None, provider.fetch_ohlcv, ticker, triad.trigger),
    )

    if df_trigger is None or df_trigger.empty:
        if entry is None:
            return (
                f"⚠️ `{ticker}` is not in your watchlist and no market data could be fetched\\.\n"
                f"Add it first with `/watch {ticker}`\\."
            )
        return f"❌ Could not fetch data for `{ticker}`\\. Try again in a moment\\."

    if df_htf is None or df_htf.empty:
        df_htf = df_trigger

    initial_cache = {"_mode": mode, "_category": category}
    indicators = compute_indicators(["accum_dist", "rsi_14", "atr_14", "point_and_figure"], df_trigger, df_htf, initial_cache)

    ad = indicators.get("accum_dist")
    if ad is None:
        return f"❌ Failed to calculate Wyckoff metrics for `{ticker}`\\."

    pnf = indicators.get("point_and_figure")

    current_price = float(df_trigger["Close"].iloc[-1])
    atr_val = indicators.get("atr_14") or 1e-4
    range_span = ad.range_high - ad.range_low
    range_atr = (range_span / atr_val) if atr_val > 0 else 0.0

    rsi_series = indicators.get("rsi_14")
    rsi_val = float(rsi_series.iloc[-1]) if rsi_series is not None and len(rsi_series) > 0 else 50.0

    # Interpretations
    state_label = {
        "accumulation": "🟢 ACCUMULATION",
        "distribution": "🔴 DISTRIBUTION",
        "neutral":      "⚪ NEUTRAL / BALANCED",
    }.get(ad.state, ad.state.upper())

    squeeze_status = "🔒 SQUEEZE ACTIVE" if ad.is_squeeze else "🔓 NORMAL EXPANSION"

    obv_desc = (
        "Institutional Inflow" if ad.obv_slope > 0.15
        else "Distribution Outflow" if ad.obv_slope < -0.15
        else "Neutral Volume"
    )
    ibs_desc = (
        "Dip Buying Absorption" if ad.ibs_mean > 0.53
        else "Rally Selling Rejection" if ad.ibs_mean < 0.47
        else "Balanced Bar Closings"
    )
    wick_desc = (
        "Heavy Lower Wick Demand" if ad.wick_ratio > 1.25
        else "Heavy Upper Wick Supply" if ad.wick_ratio < 0.75
        else "Symmetric Wicks"
    )

    spring_status = "⚡ *CONFIRMED (Phase C Shakeout)*" if ad.spring_detected else "—"
    utad_status = "⚡ *CONFIRMED (Phase C Upthrust)*" if ad.upthrust_detected else "—"
    abs_div_status = "✅ *Bullish Supply Depletion*" if ad.absorption_divergence else "—"
    exh_div_status = "⚠️ *Bearish Demand Exhaustion*" if ad.exhaustion_divergence else "—"

    lines = [
        f"🏛️ *Wyckoff Diagnostics — `{ticker}`*",
        f"Timeframe: `{triad.trigger.upper()}` | Mode: `{mode.upper()}`",
        "━━━━━━━━━━━━━━━━━━━━",
        f"State:        *{state_label}*",
        f"Score:        `{ad.score:+.1f}` / 100",
        f"Price:        `{current_price:,.4f}`",
        f"Range Box:    `{ad.range_low:,.4f}` – `{ad.range_high:,.4f}`",
        f"Squeeze:      *{squeeze_status}* (`{range_atr:.1f}× ATR`)",
        "",
        "📊 *Order Flow & Structural Metrics:*",
        f"  • OBV Slope:       `{ad.obv_slope:+.2f}` ({obv_desc})",
        f"  • IBS Mean:        `{ad.ibs_mean * 100:.1f}%` ({ibs_desc})",
        f"  • Wick Ratio:      `{ad.wick_ratio:.2f}×` ({wick_desc})",
        f"  • RSI(14):         `{rsi_val:.1f}`",
        "",
        "⚡ *Wyckoff Phase C / D Signals:*",
        f"  • Spring Reclaim:   {spring_status}",
        f"  • UTAD Upthrust:    {utad_status}",
        f"  • Absorption Div:   {abs_div_status}",
        f"  • Exhaustion Div:   {exh_div_status}",
    ]

    if pnf and pnf.horizontal_count > 0:
        lines.append("")
        lines.append("📐 *Cause & Effect (P&F 3-Box Projections):*")
        lines.append(f"  • Base Width:       `{pnf.horizontal_count}` columns (Box: `{pnf.box_size:,.2f}`)")
        if ad.state == "distribution":
            t_cons = pnf.projected_short_conservative
            t_full = pnf.projected_short_full
            cons_pct = (t_cons - current_price) / current_price * 100
            full_pct = (t_full - current_price) / current_price * 100
            lines.append(f"  • Cause Value:      `-{pnf.cause_pts:,.2f}` pts markdown")
            lines.append(f"  • Target 1 (Cons):  `{t_cons:,.4f}` ({cons_pct:+.2f}%)")
            lines.append(f"  • Target 2 (Full):  `{t_full:,.4f}` ({full_pct:+.2f}%)")
        else:
            t_cons = pnf.projected_long_conservative
            t_full = pnf.projected_long_full
            cons_pct = (t_cons - current_price) / current_price * 100
            full_pct = (t_full - current_price) / current_price * 100
            lines.append(f"  • Cause Value:      `+{pnf.cause_pts:,.2f}` pts markup")
            lines.append(f"  • Target 1 (Cons):  `{t_cons:,.4f}` ({cons_pct:+.2f}%)")
            lines.append(f"  • Target 2 (Full):  `{t_full:,.4f}` ({full_pct:+.2f}%)")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


async def _wyckoff_overview() -> str:
    """Scan active watchlist tickers and format an overview table."""
    import asyncio
    from sqlalchemy import select
    from core.db import get_db
    from modules.finance.watchlist.models import WatchlistEntry
    from .schemas import scan_mode_for, timeframe_triad_for
    from .data import get_provider
    from .indicators import compute_indicators

    with get_db() as db:
        entries = db.execute(select(WatchlistEntry)).scalars().all()

    if not entries:
        return "📭 No active tickers in your watchlist\\. Add tickers with `/watch <ticker>`\\."

    loop = asyncio.get_event_loop()

    async def _eval_entry(entry):
        try:
            mode = scan_mode_for(entry.category)
            triad = timeframe_triad_for(mode)
            provider = get_provider(entry.category)
            df_trigger = await loop.run_in_executor(None, provider.fetch_ohlcv, entry.symbol, triad.trigger)
            if df_trigger is None or df_trigger.empty or len(df_trigger) < 15:
                return entry.symbol, None
            initial_cache = {"_mode": mode, "_category": entry.category}
            inds = compute_indicators(["accum_dist", "point_and_figure"], df_trigger, df_trigger, initial_cache)
            return entry.symbol, inds.get("accum_dist"), inds.get("point_and_figure")
        except Exception as e:
            logger.warning("[trader] Wyckoff overview failed for %s: %s", entry.symbol, e)
            return entry.symbol, None, None

    results = await asyncio.gather(*[_eval_entry(e) for e in entries], return_exceptions=True)

    lines = [
        "🏛️ *Wyckoff Market Diagnostics Overview*\n",
        "```text",
        f"{'Ticker':<10} {'State':<7} {'Score':>5} {'Sq':<3} {'Cause Pts':>10} {'Phase C/D':<9}",
        "-" * 50,
    ]

    has_data = False
    for res in results:
        if isinstance(res, Exception) or not isinstance(res, tuple):
            continue
        ticker, ad, pnf = res
        if ad is None:
            continue
        has_data = True
        ticker_disp = ticker[:9]
        state_disp = "ACCUM" if ad.state == "accumulation" else "DIST" if ad.state == "distribution" else "NEUT"
        score_disp = f"{ad.score:+.0f}"
        squeeze_disp = "Y" if ad.is_squeeze else "N"
        cause_disp = f"{pnf.cause_pts:+.1f}" if (pnf and pnf.cause_pts > 0) else "—"
        if ad.spring_detected:
            phase_disp = "Spring"
        elif ad.upthrust_detected:
            phase_disp = "UTAD"
        elif ad.absorption_divergence:
            phase_disp = "AbsDiv"
        elif ad.exhaustion_divergence:
            phase_disp = "ExhDiv"
        else:
            phase_disp = "None"
        lines.append(f"{ticker_disp:<10} {state_disp:<7} {score_disp:>5} {squeeze_disp:<3} {cause_disp:>10} {phase_disp:<9}")

    if not has_data:
        return "⚠️ Could not retrieve sufficient market data for watchlist tickers\\."

    lines.append("```")
    lines.append("\n_Use `/wyckoff <ticker>` for deep order-flow diagnostics._")
    return "\n".join(lines)


