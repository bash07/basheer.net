"""Watchlist submodule — Telegram command handlers.

Commands:
  /watch <ticker> [--cat category]   Add a ticker to the watchlist
  /unwatch <ticker>                  Remove a ticker + its alerts
  /watchlist [category]              List all tracked tickers
  /alert <ticker> > <price>          Set a price alert
  /alerts                            List all active alerts
  /delalert <id>                     Delete a price alert by ID
"""
import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select, delete

from core.db import get_db
from .models import WatchlistEntry, PriceAlert
from .prices import fetch_price_detail, fetch_prices_batch, format_price_card, format_price_row

logger = logging.getLogger(__name__)

# ── Category helpers ──────────────────────────────────────────────────────────

_KNOWN_US_INDICES  = {"^GSPC", "^IXIC", "^DJI", "^RUT", "^NDX", "^VIX", "^TNX"}
_KNOWN_IN_INDICES  = {"^NSEI", "^BSESN", "^CNXIT", "^NSEBANK", "^NIFMDCP100"}

_CATEGORY_LABELS = {
    "us_index":     "🇺🇸 US Indices",
    "us_stock":     "🇺🇸 US Stocks",
    "indian_index": "🇮🇳 Indian Indices",
    "indian_stock": "🇮🇳 Indian Stocks",
    "crypto":       "₿ Crypto",
    "forex":        "💱 Forex",
    "commodity":    "🪙 Commodities",
}

_VALID_CATEGORIES = set(_CATEGORY_LABELS.keys())

# Lookup table: strip-underscore form → canonical key
# Lets users type 'indianindex' or 'indian_index' interchangeably.
_CATEGORY_ALIAS: dict[str, str] = {k.replace("_", ""): k for k in _VALID_CATEGORIES}

# Pre-built display string (used in multiple error messages)
_VALID_CATS_MD = ", ".join(f"`{c}`" for c in sorted(_VALID_CATEGORIES))


def _resolve_category(raw: str) -> str | None:
    """Return the canonical category key or None if not recognised.

    Accepts both 'indian_index' and 'indianindex' (Telegram eats underscores
    in Markdown messages so users often type the no-underscore form).
    """
    cat = raw.lower().strip()
    if cat in _VALID_CATEGORIES:
        return cat
    return _CATEGORY_ALIAS.get(cat)


def _detect_category(symbol: str) -> str:
    """Infer ticker category from symbol format conventions."""
    if symbol in _KNOWN_US_INDICES:
        return "us_index"
    if symbol in _KNOWN_IN_INDICES:
        return "indian_index"
    if symbol.startswith("^"):
        return "us_index"         # default for unknown ^ symbols
    if symbol.endswith(".NS") or symbol.endswith(".BO"):
        return "indian_stock"
    if symbol.endswith("-USD") or symbol.endswith("-USDT"):
        return "crypto"
    if symbol.endswith("=X"):
        return "forex"
    if symbol.endswith("=F"):
        return "commodity"
    return "us_stock"


def _fetch_ticker_info(symbol: str) -> dict:
    """Validate and fetch ticker display name via yfinance (blocking)."""
    from modules.finance._shared.cache import ticker_cache

    cache_key = f"info:{symbol}"
    cached = ticker_cache.get(cache_key)
    if cached:
        return cached

    import yfinance as yf  # lazy import — heavy package
    t = yf.Ticker(symbol)

    try:
        price = t.fast_info.last_price
    except Exception:
        price = None

    if price is None:
        raise ValueError(f"Ticker '{symbol}' not found or has no price data.")

    display_name = symbol
    try:
        info = t.info
        display_name = info.get("longName") or info.get("shortName") or symbol
    except Exception:
        pass  # display_name stays as symbol

    result = {"display_name": display_name, "price": price}
    ticker_cache.set(cache_key, result)
    return result


# ── Command handlers ──────────────────────────────────────────────────────────

async def watch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/watch <ticker...> [--cat category]"""
    args = list(context.args or [])
    if not args:
        await update.message.reply_text(
            "Usage: `/watch <ticker...> [--cat category]`\n"
            f"Categories: {_VALID_CATS_MD}",
            parse_mode="Markdown",
        )
        return

    # Parse optional --cat override
    category_override = None
    if "--cat" in args:
        idx = args.index("--cat")
        if idx + 1 < len(args):
            cat = _resolve_category(args[idx + 1])
            if cat is None:
                await update.message.reply_text(
                    f"❌ Unknown category `{args[idx + 1].lower()}`.\nValid: {_VALID_CATS_MD}",
                    parse_mode="Markdown",
                )
                return
            category_override = cat
            args = args[:idx] + args[idx + 2:]
        else:
            args = args[:idx]

    # Extract all candidate symbols (support comma and space separation)
    symbols: list[str] = []
    for a in args:
        for s in a.replace(",", " ").split():
            s = s.strip().upper()
            if s and s not in symbols:
                symbols.append(s)

    if not symbols:
        await update.message.reply_text("Usage: `/watch <ticker...> [--cat category]`", parse_mode="Markdown")
        return

    # Single-ticker fast path (maintains exact existing UX)
    if len(symbols) == 1:
        symbol = symbols[0]
        msg = await update.message.reply_text(f"🔍 Validating `{symbol}`…", parse_mode="Markdown")

        loop = asyncio.get_event_loop()
        try:
            info = await loop.run_in_executor(None, _fetch_ticker_info, symbol)
        except ValueError as e:
            await msg.edit_text(f"❌ {e}")
            return
        except Exception as e:
            logger.error("[watchlist] watch validation error: %s", e, exc_info=True)
            await msg.edit_text(f"❌ Could not validate `{symbol}`: {e}", parse_mode="Markdown")
            return

        display_name = info["display_name"]
        category = category_override or _detect_category(symbol)

        with get_db() as db:
            existing = db.execute(
                select(WatchlistEntry).where(WatchlistEntry.symbol == symbol)
            ).scalar_one_or_none()

            if existing:
                await msg.edit_text(
                    f"⚠️ `{symbol}` is already in your watchlist "
                    f"(category: `{existing.category}`).",
                    parse_mode="Markdown",
                )
                return

            db.add(WatchlistEntry(
                symbol=symbol,
                display_name=display_name,
                category=category,
            ))

        cat_label = _CATEGORY_LABELS.get(category, category)
        price = info["price"]
        await msg.edit_text(
            f"✅ Added *{symbol}* to watchlist\n"
            f"📛 {display_name}\n"
            f"🗂 {cat_label}\n"
            f"💵 Current price: {price:,.2f}",
            parse_mode="Markdown",
        )
        return

    # Multi-ticker batch path
    msg = await update.message.reply_text(f"🔍 Validating {len(symbols)} tickers…", parse_mode="Markdown")
    loop = asyncio.get_event_loop()

    added: list[dict] = []
    already_tracked: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []

    with get_db() as db:
        for sym in symbols:
            existing = db.execute(
                select(WatchlistEntry).where(WatchlistEntry.symbol == sym)
            ).scalar_one_or_none()
            if existing:
                already_tracked.append((sym, existing.category))
                continue

            try:
                info = await loop.run_in_executor(None, _fetch_ticker_info, sym)
                cat = category_override or _detect_category(sym)
                db.add(WatchlistEntry(
                    symbol=sym,
                    display_name=info["display_name"],
                    category=cat,
                ))
                added.append({
                    "symbol": sym,
                    "display_name": info["display_name"],
                    "category": cat,
                    "price": info["price"],
                })
            except Exception as e:
                failed.append((sym, str(e)))

    # Format multi-ticker summary
    out = [f"📋 *Watchlist Batch Onboarding* ({len(symbols)} submitted)\n"]
    if added:
        out.append(f"✅ *Added ({len(added)}):*")
        for item in added:
            cat_label = _CATEGORY_LABELS.get(item["category"], item["category"])
            out.append(f"  • *{item['symbol']}* ({item['display_name']}) — `{item['price']:,.2f}` [{cat_label}]")
        out.append("")
    if already_tracked:
        out.append(f"⚠️ *Already in Watchlist ({len(already_tracked)}):*")
        for sym, cat in already_tracked:
            cat_label = _CATEGORY_LABELS.get(cat, cat)
            out.append(f"  • *{sym}* [{cat_label}]")
        out.append("")
    if failed:
        out.append(f"❌ *Failed ({len(failed)}):*")
        for sym, err in failed:
            out.append(f"  • *{sym}*: {err}")

    await msg.edit_text("\n".join(out).strip(), parse_mode="Markdown")


async def unwatch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unwatch <ticker...>"""
    args = list(context.args or [])
    if not args:
        await update.message.reply_text("Usage: `/unwatch <ticker...>`", parse_mode="Markdown")
        return

    symbols: list[str] = []
    for a in args:
        for s in a.replace(",", " ").split():
            s = s.strip().upper()
            if s and s not in symbols:
                symbols.append(s)

    if not symbols:
        await update.message.reply_text("Usage: `/unwatch <ticker...>`", parse_mode="Markdown")
        return

    removed: list[tuple[str, int]] = []
    not_found: list[str] = []

    with get_db() as db:
        for sym in symbols:
            entry = db.execute(
                select(WatchlistEntry).where(WatchlistEntry.symbol == sym)
            ).scalar_one_or_none()
            if not entry:
                not_found.append(sym)
                continue

            alert_count = db.execute(
                delete(PriceAlert).where(PriceAlert.symbol == sym)
            ).rowcount
            db.delete(entry)
            removed.append((sym, alert_count))

    if len(symbols) == 1:
        if not_found:
            await update.message.reply_text(f"⚠️ `{symbols[0]}` is not in your watchlist.", parse_mode="Markdown")
        else:
            sym, ac = removed[0]
            suffix = f" (and {ac} alert(s))" if ac else ""
            await update.message.reply_text(f"🗑 Removed `{sym}` from watchlist{suffix}.", parse_mode="Markdown")
        return

    # Multi-ticker summary
    out = [f"🗑 *Watchlist Removal Summary*\n"]
    if removed:
        out.append(f"✅ *Removed ({len(removed)}):*")
        for sym, ac in removed:
            suffix = f" (and {ac} alert(s))" if ac else ""
            out.append(f"  • `{sym}`{suffix}")
        out.append("")
    if not_found:
        out.append(f"⚠️ *Not in Watchlist ({len(not_found)}):*")
        for sym in not_found:
            out.append(f"  • `{sym}`")

    await update.message.reply_text("\n".join(out).strip(), parse_mode="Markdown")


async def watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/watchlist [category]"""
    args = context.args or []
    category_filter = args[0].lower() if args else None

    # Build grouped structure INSIDE the session so attributes are accessible
    grouped: dict[str, list[dict]] = {}
    total = 0
    with get_db() as db:
        q = select(WatchlistEntry).order_by(WatchlistEntry.category, WatchlistEntry.symbol)
        if category_filter:
            q = q.where(WatchlistEntry.category == category_filter)
        for e in db.execute(q).scalars().all():
            grouped.setdefault(e.category, []).append(
                {"symbol": e.symbol, "display_name": e.display_name}
            )
            total += 1

    if not grouped:
        msg = "Your watchlist is empty. Use `/watch <ticker>` to add tickers."
        if category_filter:
            msg = f"No tickers in category `{category_filter}`."
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    lines = ["📋 *Watchlist*\n"]
    for cat, items in grouped.items():
        label = _CATEGORY_LABELS.get(cat, cat)
        lines.append(f"{label} ({len(items)})")
        for item in items:
            lines.append(f"  • `{item['symbol']}` — {item['display_name']}")
        lines.append("")

    await update.message.reply_text("\n".join(lines).strip(), parse_mode="Markdown")



async def alert_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/alert <ticker> > <price>  or  /alert <ticker> < <price>  or  /alert <ticker> trail <-5%|+10%>"""
    args = context.args or []
    if len(args) < 3:
        await update.message.reply_text(
            "Usage:\n"
            "• Static: `/alert <ticker> > <price>` (e.g. `/alert AAPL > 200`)\n"
            "• Trailing: `/alert <ticker> trail -5%` (e.g. alert on 5% pullback from high)",
            parse_mode="Markdown",
        )
        return

    symbol    = args[0].upper()
    condition = args[1].lower()

    # Trailing alert mode
    if condition in ("trail", "trailing"):
        val_str = args[2].replace("%", "").strip()
        try:
            trail_pct = float(val_str)
        except ValueError:
            await update.message.reply_text("❌ Trailing percentage must be a number (e.g. `-5%` or `10%`).", parse_mode="Markdown")
            return

        with get_db() as db:
            entry = db.execute(
                select(WatchlistEntry).where(WatchlistEntry.symbol == symbol)
            ).scalar_one_or_none()

            if not entry:
                await update.message.reply_text(
                    f"⚠️ `{symbol}` is not in your watchlist.\n"
                    "Add it first with `/watch <ticker>`.",
                    parse_mode="Markdown",
                )
                return

            # Fetch current market price immediately for initial reference anchor
            current_price = None
            try:
                loop = asyncio.get_event_loop()
                info = await loop.run_in_executor(None, _fetch_ticker_info, symbol)
                current_price = float(info["price"])
            except Exception as e:
                logger.debug("[watchlist] Could not fetch instant price for trailing alert %s: %s", symbol, e)

            db.add(PriceAlert(
                symbol=symbol,
                condition="<" if trail_pct < 0 else ">",
                threshold=0.0,
                alert_type="trailing_pct",
                trail_pct=trail_pct,
                trail_reference_price=current_price,
            ))

        dir_desc = f"pulls back {abs(trail_pct):.1f}% from its peak" if trail_pct < 0 else f"rallies +{trail_pct:.1f}% from its trough"
        ref_desc = f" (base price: `{current_price:,.2f}`)" if current_price is not None else ""
        await update.message.reply_text(
            f"🔔 Trailing alert set: notify when *{symbol}* {dir_desc}{ref_desc}.",
            parse_mode="Markdown",
        )
        return

    # Static alert mode
    try:
        threshold = float(args[2].replace(",", ""))
    except ValueError:
        await update.message.reply_text("❌ Price must be a number.", parse_mode="Markdown")
        return

    if condition not in (">", "<"):
        await update.message.reply_text(
            "❌ Condition must be `>` or `<`, or `trail`.", parse_mode="Markdown"
        )
        return

    # Ticker must be in the watchlist
    with get_db() as db:
        entry = db.execute(
            select(WatchlistEntry).where(WatchlistEntry.symbol == symbol)
        ).scalar_one_or_none()

        if not entry:
            await update.message.reply_text(
                f"⚠️ `{symbol}` is not in your watchlist.\n"
                "Add it first with `/watch <ticker>`.",
                parse_mode="Markdown",
            )
            return

        db.add(PriceAlert(symbol=symbol, condition=condition, threshold=threshold, alert_type="static"))

    direction = "rises above" if condition == ">" else "drops below"
    await update.message.reply_text(
        f"🔔 Alert set: notify when *{symbol}* {direction} `{threshold:,.2f}`",
        parse_mode="Markdown",
    )


async def alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/alerts — list all active (untriggered) price alerts with one-tap delete buttons"""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    with get_db() as db:
        rows = db.execute(
            select(PriceAlert)
            .where(PriceAlert.triggered == False)  # noqa: E712
            .order_by(PriceAlert.symbol, PriceAlert.created_at)
        ).scalars().all()

    if not rows:
        await update.message.reply_text(
            "No active price alerts. Set one with `/alert <ticker> > <price>`.",
            parse_mode="Markdown",
        )
        return

    lines = ["🔔 *Active Price Alerts*\n"]
    buttons: list[list[InlineKeyboardButton]] = []
    for a in rows:
        if getattr(a, "alert_type", "static") == "trailing_pct" and a.trail_pct is not None:
            pct_desc = f"{a.trail_pct:+.1f}% trail"
            ref_str = f" (ref: `{a.trail_reference_price:,.2f}`)" if a.trail_reference_price else ""
            lines.append(f"`#{a.id}` *{a.symbol}* {pct_desc}{ref_str}")
        else:
            direction = "↑ above" if a.condition == ">" else "↓ below"
            lines.append(f"`#{a.id}` *{a.symbol}* {direction} `{a.threshold:,.2f}`")
        buttons.append([InlineKeyboardButton(f"🗑 Delete #{a.id} ({a.symbol})", callback_data=f"alert:del:{a.id}")])

    lines.append("\nTap a button below to delete, or use `/delalert <id>`")
    reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=reply_markup)


async def delalert_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/delalert <id> — delete a price alert by ID"""
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text(
            "Usage: `/delalert <id>`  (get IDs from `/alerts`)", parse_mode="Markdown"
        )
        return

    alert_id = int(args[0])
    with get_db() as db:
        alert = db.get(PriceAlert, alert_id)
        if not alert:
            await update.message.reply_text(
                f"⚠️ Alert `#{alert_id}` not found.", parse_mode="Markdown"
            )
            return
        symbol = alert.symbol
        db.delete(alert)

    await update.message.reply_text(
        f"🗑 Deleted alert `#{alert_id}` for `{symbol}`.", parse_mode="Markdown"
    )


async def alert_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle interactive callbacks for price alerts: del, dismiss, chart, ta."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""
    parts = data.split(":")
    if len(parts) < 3 or parts[0] != "alert":
        return

    action, target = parts[1], parts[2]

    if action == "del":
        try:
            aid = int(target)
            with get_db() as db:
                alert = db.get(PriceAlert, aid)
                if alert:
                    sym = alert.symbol
                    db.delete(alert)
                    db.commit()
                    await query.answer(f"Deleted alert #{aid} for {sym}")
                else:
                    await query.answer("Alert already deleted", show_alert=True)

                # Query remaining active alerts
                remaining_rows = db.execute(
                    select(PriceAlert)
                    .where(PriceAlert.triggered == False)  # noqa: E712
                    .order_by(PriceAlert.symbol, PriceAlert.created_at)
                ).scalars().all()

            if not remaining_rows:
                await query.edit_message_text(f"🗑 Deleted alert `#{aid}`.\n\nNo active price alerts remaining.", parse_mode="Markdown")
            else:
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                lines = [f"🗑 Deleted alert `#{aid}`.\n", "🔔 *Active Price Alerts*\n"]
                buttons = []
                for a in remaining_rows:
                    if getattr(a, "alert_type", "static") == "trailing_pct" and a.trail_pct is not None:
                        pct_desc = f"{a.trail_pct:+.1f}% trail"
                        ref_str = f" (ref: `{a.trail_reference_price:,.2f}`)" if a.trail_reference_price else ""
                        lines.append(f"`#{a.id}` *{a.symbol}* {pct_desc}{ref_str}")
                    else:
                        direction = "↑ above" if a.condition == ">" else "↓ below"
                        lines.append(f"`#{a.id}` *{a.symbol}* {direction} `{a.threshold:,.2f}`")
                    buttons.append([InlineKeyboardButton(f"🗑 Delete #{a.id} ({a.symbol})", callback_data=f"alert:del:{a.id}")])

                lines.append("\nTap a button below to delete, or use `/delalert <id>`")
                reply_markup = InlineKeyboardMarkup(buttons)
                await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=reply_markup)
        except Exception as e:
            logger.error("[watchlist] Callback alert:del failed: %s", e)

    elif action == "dismiss":
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

    elif action == "chart":
        symbol = target.upper()
        try:
            from modules.finance.technical.charts import fetch_and_render
            import os
            png_path, result = await fetch_and_render(symbol, "1d")
            with open(png_path, "rb") as f:
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=f,
                    caption=f"📊 *{symbol}* — 1D  |  💵 `{result.current_price:,.2f}`",
                    parse_mode="Markdown",
                )
            try:
                os.unlink(png_path)
            except OSError:
                pass
        except Exception as e:
            logger.error("[watchlist] Callback alert:chart failed for %s: %s", symbol, e)
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"❌ Chart generation failed for `{symbol}`: {e}",
                parse_mode="Markdown",
            )

    elif action == "ta":
        symbol = target.upper()
        try:
            from modules.finance.technical.charts import fetch_ta_only
            from modules.finance.technical.indicators import format_ta_summary
            result = await fetch_ta_only(symbol)
            summary = format_ta_summary(result)
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=summary,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error("[watchlist] Callback alert:ta failed for %s: %s", symbol, e)
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"❌ TA computation failed for `{symbol}`: {e}",
                parse_mode="Markdown",
            )

async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/price <ticker> — quick price card with 24h change, day/52W ranges"""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: `/price <ticker>`\nExample: `/price BTC-USD` or `/price ^NSEI`",
            parse_mode="Markdown",
        )
        return

    symbol = args[0].upper()
    msg = await update.message.reply_text(f"💵 Fetching `{symbol}`…", parse_mode="Markdown")

    loop = asyncio.get_event_loop()
    try:
        info = await loop.run_in_executor(None, fetch_price_detail, symbol)
    except ValueError as e:
        await msg.edit_text(f"❌ {e}")
        return
    except Exception as e:
        logger.error("[watchlist] /price %s: %s", symbol, e, exc_info=True)
        await msg.edit_text(f"❌ Could not fetch `{symbol}`: {e}", parse_mode="Markdown")
        return

    await msg.edit_text(format_price_card(info), parse_mode="Markdown")


async def prices_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/prices [category] — batch price card for all watchlisted tickers"""
    args = context.args or []
    category_filter = args[0].lower() if args else None

    rows: list[dict] = []
    with get_db() as db:
        q = select(WatchlistEntry).order_by(WatchlistEntry.category, WatchlistEntry.symbol)
        if category_filter:
            q = q.where(WatchlistEntry.category == category_filter)
        for e in db.execute(q).scalars().all():
            rows.append({"symbol": e.symbol, "category": e.category})

    if not rows:
        empty = (
            f"No tickers in category `{category_filter}`." if category_filter
            else "Your watchlist is empty. Use `/watch <ticker>` to add tickers."
        )
        await update.message.reply_text(empty, parse_mode="Markdown")
        return

    msg = await update.message.reply_text("⏳ Fetching prices…")
    loop = asyncio.get_event_loop()
    prices = await loop.run_in_executor(
        None, fetch_prices_batch, [r["symbol"] for r in rows]
    )

    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r["category"], []).append(r)

    lines = ["💹 *Watchlist Prices*\n"]
    for cat, items in grouped.items():
        lines.append(f"*{_CATEGORY_LABELS.get(cat, cat)}*")
        for item in items:
            lines.append(format_price_row(item["symbol"], prices.get(item["symbol"])))
        lines.append("")

    await msg.edit_text("\n".join(lines).strip(), parse_mode="Markdown")
