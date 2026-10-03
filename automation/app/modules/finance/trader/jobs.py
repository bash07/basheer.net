"""APScheduler jobs for the finance/trader module.

Jobs:
    scan_all_tickers — every 30 minutes (market-hours aware per category).
        1. Load all watchlist tickers grouped into swing / intraday modes.
        2. Fetch HTF + LTF OHLCV for each ticker (via shared cache).
        3. Run check_all() to evaluate all v1 Trade setups.
        4. Deduplicate against trade_signals table (24-hour window).
        5. Persist new signals and send a Telegram digest.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_DEDUP_HOURS = 24    # suppress same ticker+setup+direction within this window
_BATCH_PAUSE = 0.4   # seconds between Telegram messages (flood control)

# yfinance period/interval for each timeframe
_OHLCV_PARAMS: dict[str, tuple[str, str]] = {
    "1wk": ("2y",   "1wk"),   # HTF swing  — 2 years of weekly bars
    "1d":  ("1y",   "1d"),    # HTF intraday — 1 year of daily bars
    "4h":  ("60d",  "1h"),    # LTF swing — 60 days of 1h (yfinance max for 4h workaround)
    "1h":  ("30d",  "1h"),    # LTF intraday — 30 days of 1h
}
# Note: yfinance doesn't support 4h interval natively; we use 1h and resample to 4h.
_RESAMPLE_4H = True


async def scan_all_tickers() -> None:
    """Proactive Trade setup scanner — called every 30 minutes by APScheduler."""
    from sqlalchemy import select
    from core.db import get_db
    from modules.finance.watchlist.models import WatchlistEntry
    from modules.finance._shared.market_hours import is_market_open
    from modules.finance._shared.notify import notify
    from .models import TradeSignal
    from .setups import check_all
    from .schemas import scan_mode_for

    # 1. Load watchlist and filter by open markets
    with get_db() as db:
        all_entries = db.execute(select(WatchlistEntry)).scalars().all()

    active = [e for e in all_entries if is_market_open(e.category)]
    if not active:
        logger.debug("[trader] scan_all_tickers: no open markets right now")
        return

    logger.info("[trader] scan_all_tickers: checking %d tickers", len(active))

    # 2. Load recent signals for deduplication
    cutoff = datetime.now(timezone.utc) - timedelta(hours=_DEDUP_HOURS)
    with get_db() as db:
        recent_rows = db.execute(
            select(TradeSignal).where(TradeSignal.fired_at >= cutoff)
        ).scalars().all()

    dedup_keys: set[str] = {
        f"{r.ticker}:{r.setup_name}:{r.direction}" for r in recent_rows
    }

    # 3. Group tickers by scan mode for batch OHLCV fetching
    swing_tickers    = [e for e in active if scan_mode_for(e.category) == "swing"]
    intraday_tickers = [e for e in active if scan_mode_for(e.category) == "intraday"]

    loop = asyncio.get_event_loop()

    # Fetch all OHLCV data in background threads (non-blocking)
    swing_ohlcv, intraday_ohlcv = await asyncio.gather(
        loop.run_in_executor(None, _fetch_all_ohlcv, swing_tickers,    "swing"),
        loop.run_in_executor(None, _fetch_all_ohlcv, intraday_tickers, "intraday"),
    )

    # 4. Evaluate setups for each ticker
    new_setups   = []   # list of (TradeSetup, ltf_df)

    with get_db() as db:
        for entry in swing_tickers + intraday_tickers:
            ohlcv_map = swing_ohlcv if scan_mode_for(entry.category) == "swing" else intraday_ohlcv
            htf_df = ohlcv_map.get(f"{entry.symbol}:htf")
            ltf_df = ohlcv_map.get(f"{entry.symbol}:ltf")

            if htf_df is None or ltf_df is None or htf_df.empty or ltf_df.empty:
                logger.debug("[trader] %s: missing OHLCV data, skipping", entry.symbol)
                continue

            # Update live outcome of previously fired setups
            _update_open_trades_for_ticker(entry.symbol, ltf_df, db)

            try:
                setups = check_all(entry.symbol, entry.category, htf_df, ltf_df)
            except Exception as e:
                logger.warning("[trader] check_all failed for %s: %s", entry.symbol, e)
                continue

            for setup in setups:
                key = setup.to_dedup_key()
                if key in dedup_keys:
                    logger.debug("[trader] %s: dedup hit for %s — skipping", entry.symbol, setup.setup_name)
                    continue
                new_setups.append((setup, ltf_df))   # keep ltf_df for chart generation
                dedup_keys.add(key)  # prevent intra-scan duplicates
        
        db.commit() # commit outcome updates

    if not new_setups:
        logger.debug("[trader] scan_all_tickers: no new setups this cycle")
        return

    logger.info("[trader] Firing %d new setup alert(s)", len(new_setups))

    # 5. Persist new signals and collect DB ids for button callbacks
    now = datetime.now(timezone.utc)
    signal_rows   = []   # list of (TradeSignal db row, ltf_df)

    with get_db() as db:
        for setup, ltf_df in new_setups:
            row = TradeSignal(
                ticker=setup.ticker,
                category=setup.category,
                mode=setup.mode,
                setup_name=setup.setup_name,
                direction=setup.direction,
                setup_type=setup.setup_type,
                bias=setup.bias.bias,
                conf_score=setup.confluence.score,
                conf_label=setup.confluence.label,
                entry_mid=setup.entry_mid,
                stop_loss=setup.stop_loss,
                tp1=setup.tp1,
                rr_ratio=setup.rr_ratio,
                setup_json=setup.to_db_json(),
                fired_at=now,
                notified=True,
            )
            db.add(row)
            db.flush()           # assigns row.id without committing
            signal_rows.append((setup, row.id, ltf_df))
        db.commit()

    # 6. Send Telegram alerts as charts with Approve / Dismiss buttons
    # Sort by confidence score descending so best setups arrive first
    signal_rows.sort(key=lambda t: t[0].confluence.score, reverse=True)

    for setup, signal_id, ltf_df in signal_rows:
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            from modules.finance._shared.notify import notify_photo, notify
            from .chart import generate_chart

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ Approve",
                        callback_data=f"trade:approve:{signal_id}",
                    ),
                    InlineKeyboardButton(
                        "❌ Dismiss",
                        callback_data=f"trade:dismiss:{signal_id}",
                    ),
                ]
            ])

            # Generate annotated chart PNG
            png_path = await loop.run_in_executor(
                None, generate_chart, setup, ltf_df
            )

            if png_path:
                # Send chart image + buttons
                caption = setup.to_telegram_text()
                # Telegram captions max 1024 chars — truncate gracefully
                if len(caption) > 1024:
                    caption = caption[:1020] + "…"
                await notify_photo(
                    png_path,
                    caption=caption,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
            else:
                # Chart failed — fall back to plain text with buttons
                await notify(
                    setup.to_telegram_text(),
                    parse_mode="Markdown",
                )

            await asyncio.sleep(_BATCH_PAUSE)
        except Exception as e:
            logger.error("[trader] Failed to notify for %s: %s", setup.ticker, e)


# ── OHLCV fetching ────────────────────────────────────────────────────────────

def _fetch_all_ohlcv(entries: list, mode: str) -> dict:
    """Fetch HTF + LTF OHLCV for all entries using the configured DataProvider.

    Returns:
        Dict with keys "{symbol}:htf" and "{symbol}:ltf" → DataFrame
    """
    from .data import get_provider
    from .schemas import timeframes_for

    if not entries:
        return {}

    htf_tf, ltf_tf, _ = timeframes_for(mode)
    provider = get_provider()
    symbols = [e.symbol for e in entries]

    # yfinance periods for each TF
    _PERIODS = {
        "1wk": "2y",
        "1d":  "1y",
        "4h":  "60d",
        "1h":  "30d",
    }

    htf_period = _PERIODS.get(htf_tf, "1y")
    ltf_period = _PERIODS.get(ltf_tf, "30d")
    # yfinance doesn't support 4h; fetch 1h and resample
    ltf_fetch_interval = "1h" if ltf_tf == "4h" else ltf_tf
    ltf_resample = "4h" if ltf_tf == "4h" else None

    result = {}
    htf_map = provider.fetch_ohlcv_batch(symbols, htf_tf, period=htf_period)
    ltf_map = provider.fetch_ohlcv_batch(symbols, ltf_fetch_interval, period=ltf_period,
                                         resample_to=ltf_resample)

    for sym in symbols:
        if sym in htf_map:
            result[f"{sym}:htf"] = htf_map[sym]
        if sym in ltf_map:
            result[f"{sym}:ltf"] = ltf_map[sym]

    return result


# NOTE: _fetch_tf_batch is kept below for reference; all active fetching now
# goes through DataProvider.fetch_ohlcv_batch() via _fetch_all_ohlcv above.
def _fetch_tf_batch(symbols: list, period: str, interval: str, tf_label: str,
                    result: dict, cache_key_suffix: str, resample_to: str | None = None) -> None:
    """Deprecated: use get_provider().fetch_ohlcv_batch() instead."""
    import warnings
    warnings.warn(
        "_fetch_tf_batch is deprecated; use get_provider().fetch_ohlcv_batch()",
        DeprecationWarning,
        stacklevel=2,
    )


def _update_open_trades_for_ticker(ticker: str, ltf_df, db) -> None:
    """Evaluate OPEN trades for a ticker against new price bars and update outcome."""
    from sqlalchemy import select
    from .models import TradeSignal
    import pandas as pd

    open_trades = db.execute(
        select(TradeSignal).where(
            TradeSignal.ticker == ticker,
            TradeSignal.outcome == "OPEN"
        )
    ).scalars().all()

    if not open_trades:
        return

    # Ensure index is timezone-aware if fired_at is, or naive if not
    for trade in open_trades:
        try:
            fired_at = trade.fired_at.replace(tzinfo=ltf_df.index.tz) if ltf_df.index.tz is not None else trade.fired_at.replace(tzinfo=None)
            future_bars = ltf_df[ltf_df.index > fired_at]
            
            for ts, row in future_bars.iterrows():
                hi = float(row["High"])
                lo = float(row["Low"])
                
                tp_hit = False
                sl_hit = False
                
                if trade.direction == "long":
                    if hi >= trade.tp1: tp_hit = True
                    if lo <= trade.stop_loss: sl_hit = True
                else:
                    if lo <= trade.tp1: tp_hit = True
                    if hi >= trade.stop_loss: sl_hit = True
                    
                if tp_hit and sl_hit:
                    # Ambiguous candle: assume whichever is closer gets hit first
                    tp_hit = abs(trade.entry_mid - trade.tp1) < abs(trade.entry_mid - trade.stop_loss)
                    sl_hit = not tp_hit

                if tp_hit or sl_hit:
                    trade.outcome = "WIN" if tp_hit else "LOSS"
                    # Safe conversion to naive python datetime for DB
                    trade.outcome_date = ts.to_pydatetime().replace(tzinfo=timezone.utc) if hasattr(ts, "to_pydatetime") else datetime.now(timezone.utc)
                    trade.realised_rr = trade.rr_ratio if tp_hit else -1.0
                    break
        except Exception as e:
            logger.warning("[trader] Failed to update outcome for trade %d: %s", trade.id, e)
