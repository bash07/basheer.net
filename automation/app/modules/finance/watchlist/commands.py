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
    """/watch <ticker> [--cat category]"""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: `/watch <ticker> [--cat category]`\n"
            f"Categories: {_VALID_CATS_MD}",
            parse_mode="Markdown",
        )
        return

    symbol = args[0].upper()

    # Parse optional --cat override
    category_override = None
    if "--cat" in args:
        idx = args.index("--cat")
        if idx + 1 < len(args):
            cat = _resolve_category(args[idx + 1])
            if cat is None:
                await update.message.reply_text(
                    f"❌ Unknown category `{args[idx + 1].lower()}`."
                    f"\nValid: {_VALID_CATS_MD}",
                    parse_mode="Markdown",
                )
                return
            category_override = cat

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


async def unwatch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unwatch <ticker>"""
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: `/unwatch <ticker>`", parse_mode="Markdown")
        return

    symbol = args[0].upper()

    with get_db() as db:
        entry = db.execute(
            select(WatchlistEntry).where(WatchlistEntry.symbol == symbol)
        ).scalar_one_or_none()

        if not entry:
            await update.message.reply_text(
                f"⚠️ `{symbol}` is not in your watchlist.", parse_mode="Markdown"
            )
            return

        # Cascade-delete associated price alerts
        alert_count = db.execute(
            delete(PriceAlert).where(PriceAlert.symbol == symbol)
        ).rowcount
        db.delete(entry)

    suffix = f" (and {alert_count} alert(s))" if alert_count else ""
    await update.message.reply_text(
        f"🗑 Removed `{symbol}` from watchlist{suffix}.", parse_mode="Markdown"
    )


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
    """/alert <ticker> > <price>  or  /alert <ticker> < <price>"""
    args = context.args or []
    if len(args) < 3:
        await update.message.reply_text(
            "Usage: `/alert <ticker> > <price>`\n"
            "Example: `/alert AAPL > 200` or `/alert BTC-USD < 50000`",
            parse_mode="Markdown",
        )
        return

    symbol    = args[0].upper()
    condition = args[1]
    try:
        threshold = float(args[2].replace(",", ""))
    except ValueError:
        await update.message.reply_text("❌ Price must be a number.", parse_mode="Markdown")
        return

    if condition not in (">", "<"):
        await update.message.reply_text(
            "❌ Condition must be `>` or `<`.", parse_mode="Markdown"
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

        db.add(PriceAlert(symbol=symbol, condition=condition, threshold=threshold))

    direction = "rises above" if condition == ">" else "drops below"
    await update.message.reply_text(
        f"🔔 Alert set: notify when *{symbol}* {direction} `{threshold:,.2f}`",
        parse_mode="Markdown",
    )


async def alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/alerts — list all active (untriggered) price alerts"""
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
    for a in rows:
        direction = "↑ above" if a.condition == ">" else "↓ below"
        lines.append(f"`#{a.id}` *{a.symbol}* {direction} `{a.threshold:,.2f}`")

    lines.append("\nDelete with `/delalert <id>`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


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
