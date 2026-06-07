"""Fundamental Analysis — Telegram command handlers.

Commands:
  /fundamental <ticker> [--method graham]
      Full fundamental screen + LLM verdict for one ticker.

  /screen [--method graham]
      Screen all stock tickers in the watchlist, rank by score.

  /earnings [days]
      Show upcoming earnings dates within N days (default: 7).
      Only stock categories are checked.
"""
import asyncio
import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select

from core.db import get_db
from modules.finance.watchlist.models import WatchlistEntry
from .fetcher import fetch_fundamentals, FUNDAMENTAL_CATEGORIES
from .strategies import STRATEGIES, DEFAULT_METHOD
from .analyst import generate_verdict
from .formatter import format_full_report, format_screen_report, format_earnings_list

logger = logging.getLogger(__name__)

_VALID_METHODS_MD = ", ".join(f"`{m}`" for m in sorted(STRATEGIES))


def _parse_method(args: list[str]) -> str | None:
    """Extract --method <name> from args list, return canonical name or None."""
    if "--method" in args:
        idx = args.index("--method")
        if idx + 1 < len(args):
            return args[idx + 1].lower()
    return None


# ── /fundamental ──────────────────────────────────────────────────────────────

async def fundamental_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/fundamental <ticker> [--method graham]"""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: `/fundamental <ticker> [--method graham]`\n"
            f"Methods: {_VALID_METHODS_MD}\n"
            "Example: `/fundamental AAPL --method graham`",
            parse_mode="Markdown",
        )
        return

    symbol = args[0].upper()
    method = _parse_method(args) or DEFAULT_METHOD
    if method not in STRATEGIES:
        await update.message.reply_text(
            f"❌ Unknown method `{method}`. Valid: {_VALID_METHODS_MD}",
            parse_mode="Markdown",
        )
        return

    msg = await update.message.reply_text(
        f"🔍 Fetching fundamentals for `{symbol}` ({method})…",
        parse_mode="Markdown",
    )

    loop = asyncio.get_event_loop()
    try:
        data = await loop.run_in_executor(None, fetch_fundamentals, symbol)
    except ValueError as e:
        await msg.edit_text(f"❌ {e}")
        return
    except Exception as e:
        logger.error("[fundamental] fetch failed for %s: %s", symbol, e, exc_info=True)
        await msg.edit_text(f"❌ Failed to fetch data for `{symbol}`: {e}", parse_mode="Markdown")
        return

    await msg.edit_text(f"🧮 Scoring against {method.title()} criteria…")

    strategy = STRATEGIES[method]()
    result = strategy.evaluate(data)

    await msg.edit_text("✍️ Generating verdict…")
    result.verdict = await generate_verdict(data, result)

    await msg.edit_text(format_full_report(data, result), parse_mode="Markdown")


# ── /screen ───────────────────────────────────────────────────────────────────

async def screen_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/screen [--method graham] — rank all watchlisted stocks by fundamental score."""
    args = context.args or []
    method = _parse_method(args) or DEFAULT_METHOD
    if method not in STRATEGIES:
        await update.message.reply_text(
            f"❌ Unknown method `{method}`. Valid: {_VALID_METHODS_MD}",
            parse_mode="Markdown",
        )
        return

    # Load stock-only watchlist entries
    symbols: list[str] = []
    with get_db() as db:
        for e in db.execute(
            select(WatchlistEntry).where(
                WatchlistEntry.category.in_(FUNDAMENTAL_CATEGORIES)
            ).order_by(WatchlistEntry.symbol)
        ).scalars().all():
            symbols.append(e.symbol)

    if not symbols:
        await update.message.reply_text(
            "No stocks in your watchlist. Add some with `/watch <ticker>`.",
            parse_mode="Markdown",
        )
        return

    msg = await update.message.reply_text(
        f"⏳ Fetching fundamentals for {len(symbols)} ticker(s)…"
    )

    # Concurrent fetch via individual executor tasks
    loop = asyncio.get_event_loop()
    tasks = [loop.run_in_executor(None, _safe_fetch, sym) for sym in symbols]
    fetched = dict(zip(symbols, await asyncio.gather(*tasks)))

    strategy_cls = STRATEGIES[method]
    rows: list[tuple[str, object]] = []
    for sym in symbols:
        data = fetched[sym]
        if isinstance(data, Exception):
            rows.append((sym, None))
        else:
            rows.append((sym, strategy_cls().evaluate(data)))

    # Sort descending by score (None last)
    rows.sort(key=lambda x: x[1].score if x[1] else -1, reverse=True)

    today = datetime.now(timezone.utc).date()
    await msg.edit_text(format_screen_report(rows, method, today), parse_mode="Markdown")


# ── /earnings ─────────────────────────────────────────────────────────────────

async def earnings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/earnings [days] — upcoming earnings for watchlisted stocks (default: 7 days)."""
    args = context.args or []
    try:
        days = int(args[0]) if args else 7
        if days < 1 or days > 90:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "Usage: `/earnings [days]`  (1–90, default: 7)\nExample: `/earnings 14`",
            parse_mode="Markdown",
        )
        return

    symbols: list[str] = []
    with get_db() as db:
        for e in db.execute(
            select(WatchlistEntry).where(
                WatchlistEntry.category.in_(FUNDAMENTAL_CATEGORIES)
            )
        ).scalars().all():
            symbols.append(e.symbol)

    if not symbols:
        await update.message.reply_text(
            "No stocks in your watchlist.", parse_mode="Markdown"
        )
        return

    msg = await update.message.reply_text(f"⏳ Checking earnings for {len(symbols)} ticker(s)…")

    loop = asyncio.get_event_loop()
    tasks = [loop.run_in_executor(None, _safe_fetch, sym) for sym in symbols]
    fetched = dict(zip(symbols, await asyncio.gather(*tasks)))

    today = datetime.now(timezone.utc).date()
    upcoming = []
    for sym, data in fetched.items():
        if isinstance(data, Exception) or data is None:
            continue
        if data.next_earnings_date is None:
            continue
        diff = (data.next_earnings_date - today).days
        if 0 <= diff <= days:
            upcoming.append((sym, data.display_name, data.next_earnings_date, diff))

    upcoming.sort(key=lambda x: x[2])
    await msg.edit_text(format_earnings_list(upcoming, days), parse_mode="Markdown")


# ── Shared helpers ────────────────────────────────────────────────────────────

def _safe_fetch(symbol: str):
    """Run fetch_fundamentals and return Exception on failure instead of raising."""
    try:
        return fetch_fundamentals(symbol)
    except Exception as e:
        logger.warning("[fundamental] %s: %s", symbol, e)
        return e
