"""Technical analysis scheduled jobs.

Jobs:
  scan_signals — every 30 minutes (market hours per category).
    1. Loads all watchlist tickers.
    2. Filters by is_market_open(category).
    3. Batch-fetches OHLCV via yfinance.
    4. Computes TA for each ticker.
    5. Compares new signals vs SignalLog within a dedup window.
    6. Inserts new signals and sends Telegram alerts.
"""
import asyncio
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_DEDUP_HOURS   = 6    # suppress same signal within this window
_BATCH_PAUSE   = 0.5  # seconds between Telegram notifications (flood control)


async def scan_signals_job() -> None:
    """Proactive TA signal scanner — called every 30 minutes by APScheduler."""
    from sqlalchemy import select
    from core.db import get_db
    from modules.finance.watchlist.models import WatchlistEntry
    from modules.finance._shared.market_hours import is_market_open
    from modules.finance._shared.notify import notify
    from .models import SignalLog
    from .indicators import compute_ta

    # 1. Load watchlist and filter by open markets
    with get_db() as db:
        all_entries = db.execute(select(WatchlistEntry)).scalars().all()

    active = [e for e in all_entries if is_market_open(e.category)]
    if not active:
        logger.debug("[technical] scan_signals: no open markets right now")
        return

    logger.info("[technical] scan_signals: checking %d tickers", len(active))

    # 2. Batch-fetch OHLCV (run in executor to avoid blocking event loop)
    loop = asyncio.get_event_loop()
    symbols = [e.symbol for e in active]
    ohlcv_map = await loop.run_in_executor(None, _batch_fetch, symbols)

    # 3. Dedup window: load signals logged in the last _DEDUP_HOURS hours
    cutoff = datetime.utcnow() - timedelta(hours=_DEDUP_HOURS)
    with get_db() as db:
        recent = db.execute(
            select(SignalLog).where(SignalLog.detected_at >= cutoff)
        ).scalars().all()

    recent_keys: set[str] = {f"{r.symbol}:{r.signal_type}:{r.description[:60]}" for r in recent}

    # 4. Compute TA and collect new signals
    new_signals: list[dict] = []
    for entry in active:
        df = ohlcv_map.get(entry.symbol)
        if df is None or df.empty:
            continue
        try:
            result = compute_ta(df, symbol=entry.symbol, timeframe="1d")
        except Exception as e:
            logger.warning("[technical] compute_ta failed for %s: %s", entry.symbol, e)
            continue

        for sig_text in result.signals:
            sig_type = _classify_signal_type(sig_text)
            key = f"{entry.symbol}:{sig_type}:{sig_text[:60]}"
            if key not in recent_keys:
                new_signals.append({
                    "symbol":      entry.symbol,
                    "signal_type": sig_type,
                    "description": sig_text,
                })
                recent_keys.add(key)  # prevent intra-scan duplicates

    if not new_signals:
        logger.debug("[technical] scan_signals: no new signals this cycle")
        return

    # 5. Persist new signals
    with get_db() as db:
        for s in new_signals:
            db.add(SignalLog(
                symbol=s["symbol"],
                signal_type=s["signal_type"],
                description=s["description"],
                notified=True,
            ))

    # 6. Send Telegram alerts
    logger.info("[technical] Firing %d new signal alert(s)", len(new_signals))
    for s in new_signals:
        await notify(
            f"⚡ *Signal: {s['symbol']}*\n{s['description']}",
            parse_mode="Markdown",
        )
        await asyncio.sleep(_BATCH_PAUSE)


def _classify_signal_type(description: str) -> str:
    desc = description.lower()
    if "crossover" in desc: return "ema_crossover"
    if "rsi"       in desc: return "rsi_extreme"
    if "volume"    in desc: return "volume_spike"
    return "other"


def _batch_fetch(symbols: list[str]) -> dict:
    """Synchronous batch OHLCV download — runs inside an executor thread."""
    import yfinance as yf    # lazy
    import pandas as pd      # lazy
    from modules.finance._shared.cache import ticker_cache

    result: dict = {}
    uncached = []
    for sym in symbols:
        cached = ticker_cache.get(f"ohlcv:{sym}:6mo:1d")
        if cached is not None:
            result[sym] = cached
        else:
            uncached.append(sym)

    if not uncached:
        return result

    try:
        raw = yf.download(
            tickers=uncached,
            period="6mo",
            interval="1d",
            progress=False,
            auto_adjust=True,
            group_by="ticker" if len(uncached) > 1 else "column",
        )
        for sym in uncached:
            try:
                df = raw[sym] if len(uncached) > 1 else raw
                if hasattr(df.columns, "levels"):
                    df.columns = df.columns.get_level_values(0)
                df.columns = [c.title() for c in df.columns]
                df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
                if not df.empty:
                    ticker_cache.set(f"ohlcv:{sym}:6mo:1d", df)
                    result[sym] = df
            except Exception as e:
                logger.warning("[technical] Failed to parse %s from batch: %s", sym, e)
    except Exception as e:
        logger.error("[technical] Batch download failed: %s", e, exc_info=True)

    return result
