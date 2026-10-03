"""Watchlist scheduled jobs.

Jobs:
  check_price_alerts — every 5 minutes.
  daily_summary      — 9:00 AM IST, weekdays only.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


async def daily_summary_job() -> None:
    """Morning briefing: prices for all watchlisted tickers sent at 9 AM IST."""
    from sqlalchemy import select
    from core.db import get_db
    from .models import WatchlistEntry
    from .prices import fetch_prices_batch, format_price_row, _CATEGORY_LABELS_LOCAL
    from modules.finance._shared.notify import notify

    rows: list[dict] = []
    with get_db() as db:
        for e in db.execute(
            select(WatchlistEntry).order_by(WatchlistEntry.category, WatchlistEntry.symbol)
        ).scalars().all():
            rows.append({"symbol": e.symbol, "category": e.category})

    if not rows:
        return

    loop = asyncio.get_event_loop()
    prices = await loop.run_in_executor(None, fetch_prices_batch, [r["symbol"] for r in rows])

    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["category"], []).append(r["symbol"])

    today = datetime.now(timezone.utc).strftime("%A, %d %b %Y")
    lines = [f"☀️ *Morning Briefing* — {today}\n"]

    for cat, symbols in grouped.items():
        lines.append(f"*{_CATEGORY_LABELS_LOCAL.get(cat, cat)}*")
        for sym in symbols:
            lines.append(format_price_row(sym, prices.get(sym)))
        lines.append("")

    # Append recent technical signals from the last 24 hours
    try:
        from modules.finance.technical.models import SignalLog
        cutoff_ta = datetime.now(timezone.utc) - timedelta(hours=24)
        with get_db() as db:
            recent_ta = db.execute(
                select(SignalLog)
                .where(SignalLog.detected_at >= cutoff_ta)
                .order_by(SignalLog.detected_at.desc())
            ).scalars().all()

        if recent_ta:
            lines.append("⚡ *Technical Signals (Last 24h)*")
            seen_ta = set()
            for s in recent_ta:
                key = f"{s.symbol}:{s.description[:40]}"
                if key not in seen_ta:
                    seen_ta.add(key)
                    lines.append(f"• *{s.symbol}* — {s.description}")
            lines.append("")
        else:
            lines.append("⚡ *Technical Signals:* No active alerts in the last 24h\n")
    except Exception as e:
        logger.warning("[watchlist] daily_summary: failed to query TA signals: %s", e)

    await notify("\n".join(lines).strip(), parse_mode="Markdown")
    logger.info("[watchlist] Daily summary sent (%d tickers)", len(rows))


async def check_price_alerts_job() -> None:
    """Evaluate all active price alerts and notify on trigger."""
    from sqlalchemy import select
    from core.db import get_db
    from .models import PriceAlert, WatchlistEntry
    from modules.finance._shared.notify import notify

    with get_db() as db:
        alerts = db.execute(
            select(PriceAlert).where(PriceAlert.triggered == False)  # noqa: E712
        ).scalars().all()

    if not alerts:
        return

    # Deduplicate symbols and fetch prices in one yfinance batch call
    symbols = list({a.symbol for a in alerts})
    prices = await _fetch_prices_batch(symbols)

    triggered_ids: list[int] = []
    messages: list[str] = []

    for alert in alerts:
        price = prices.get(alert.symbol)
        if price is None:
            logger.warning("[watchlist] No price for %s — skipping alert #%d", alert.symbol, alert.id)
            continue

        hit = (alert.condition == ">" and price > alert.threshold) or \
              (alert.condition == "<" and price < alert.threshold)

        if hit:
            direction = "📈 rose above" if alert.condition == ">" else "📉 dropped below"
            messages.append(
                f"🔔 *Price Alert Triggered!*\n"
                f"`{alert.symbol}` {direction} `{alert.threshold:,.2f}`\n"
                f"Current price: `{price:,.2f}`"
            )
            triggered_ids.append(alert.id)

    # Mark triggered alerts in DB
    if triggered_ids:
        with get_db() as db:
            for aid in triggered_ids:
                alert_row = db.get(PriceAlert, aid)
                if alert_row:
                    alert_row.triggered = True

        # Send notifications (one message per triggered alert)
        for msg in messages:
            await notify(msg)
            await asyncio.sleep(0.3)  # small gap between messages

    if triggered_ids:
        logger.info("[watchlist] Triggered %d price alert(s): %s", len(triggered_ids), triggered_ids)


async def _fetch_prices_batch(symbols: list[str]) -> dict[str, float | None]:
    """Batch-fetch latest prices for a list of symbols via yfinance (in executor)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_prices_sync, symbols)


def _fetch_prices_sync(symbols: list[str]) -> dict[str, float | None]:
    """Synchronous batch price fetch — runs inside an executor thread."""
    import yfinance as yf  # lazy import
    from modules.finance._shared.cache import ticker_cache

    prices: dict[str, float | None] = {}

    # Use yf.download for batch efficiency
    try:
        import pandas as pd  # lazy import
        raw = yf.download(
            tickers=symbols,
            period="2d",
            interval="1d",
            progress=False,
            auto_adjust=True,
            group_by="ticker" if len(symbols) > 1 else "column",
        )

        if len(symbols) == 1:
            sym = symbols[0]
            try:
                prices[sym] = float(raw["Close"].dropna().iloc[-1])
            except Exception:
                prices[sym] = None
        else:
            for sym in symbols:
                try:
                    prices[sym] = float(raw["Close"][sym].dropna().iloc[-1])
                except Exception:
                    prices[sym] = None
    except Exception as e:
        logger.error("[watchlist] Batch price fetch failed: %s — falling back to per-ticker", e)
        # Fallback: per-ticker fast_info
        for sym in symbols:
            try:
                prices[sym] = yf.Ticker(sym).fast_info.last_price
            except Exception:
                prices[sym] = None

    return prices
