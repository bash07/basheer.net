"""Fundamental analysis scheduled jobs.

Jobs:
  weekly_screen_job   — Sunday 8:00 AM IST. Screens all watchlisted stocks,
                        sends ranked results to Telegram.
  earnings_alert_job  — Daily 8:00 AM IST. Checks earnings dates; alerts
                        when any tracked stock reports within 3 days.
"""
import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_EARNINGS_ALERT_DAYS = 3   # alert N days before earnings


async def weekly_screen_job() -> None:
    """Sunday 8 AM IST — Graham screen on all watchlisted stocks."""
    from sqlalchemy import select
    from core.db import get_db
    from modules.finance.watchlist.models import WatchlistEntry
    from modules.finance._shared.notify import notify
    from .fetcher import fetch_fundamentals, FUNDAMENTAL_CATEGORIES
    from .strategies import STRATEGIES, DEFAULT_METHOD
    from .formatter import format_screen_report
    from .commands import _safe_fetch

    symbols: list[str] = []
    with get_db() as db:
        for e in db.execute(
            select(WatchlistEntry).where(
                WatchlistEntry.category.in_(FUNDAMENTAL_CATEGORIES)
            ).order_by(WatchlistEntry.symbol)
        ).scalars().all():
            symbols.append(e.symbol)

    if not symbols:
        logger.info("[fundamental] weekly_screen: watchlist has no stocks")
        return

    logger.info("[fundamental] weekly_screen: fetching %d tickers", len(symbols))
    loop = asyncio.get_event_loop()
    tasks = [loop.run_in_executor(None, _safe_fetch, sym) for sym in symbols]
    fetched = dict(zip(symbols, await asyncio.gather(*tasks)))

    strategy_cls = STRATEGIES[DEFAULT_METHOD]
    rows = []
    for sym in symbols:
        data = fetched[sym]
        rows.append((sym, None if isinstance(data, Exception) else strategy_cls().evaluate(data)))

    rows.sort(key=lambda x: x[1].score if x[1] else -1, reverse=True)

    today = datetime.now(timezone.utc).date()
    text = format_screen_report(rows, DEFAULT_METHOD, today)
    await notify(f"📊 *Weekly Fundamental Screen*\n\n{text}", parse_mode="Markdown")
    logger.info("[fundamental] weekly_screen: sent %d results", len(rows))


async def earnings_alert_job() -> None:
    """Daily 8 AM IST — alert when any tracked stock reports earnings within 3 days."""
    from sqlalchemy import select
    from core.db import get_db
    from modules.finance.watchlist.models import WatchlistEntry
    from modules.finance._shared.notify import notify
    from .fetcher import FUNDAMENTAL_CATEGORIES
    from .formatter import format_earnings_list
    from .commands import _safe_fetch

    symbols: list[str] = []
    with get_db() as db:
        for e in db.execute(
            select(WatchlistEntry).where(
                WatchlistEntry.category.in_(FUNDAMENTAL_CATEGORIES)
            )
        ).scalars().all():
            symbols.append(e.symbol)

    if not symbols:
        return

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
        if 0 <= diff <= _EARNINGS_ALERT_DAYS:
            upcoming.append((sym, data.display_name, data.next_earnings_date, diff))

    if not upcoming:
        return

    upcoming.sort(key=lambda x: x[2])
    text = format_earnings_list(upcoming, _EARNINGS_ALERT_DAYS)
    await notify(f"⚠️ *Earnings Alert*\n\n{text}", parse_mode="Markdown")
    logger.info("[fundamental] earnings_alert: %d upcoming", len(upcoming))
