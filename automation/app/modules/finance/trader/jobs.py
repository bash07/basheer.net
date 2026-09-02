"""APScheduler jobs for the finance/trader module.

Jobs:
    scan_all_tickers — every 30 minutes (market-hours aware per category).
        1. Load all watchlist tickers grouped into swing / intraday modes.
        2. Fetch HTF + LTF OHLCV for each ticker (via shared cache).
        3. Run check_all() to evaluate all v1 ICT setups.
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
    """Proactive ICT setup scanner — called every 30 minutes by APScheduler."""
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
    new_setups = []

    for entry in swing_tickers + intraday_tickers:
        ohlcv_map = swing_ohlcv if scan_mode_for(entry.category) == "swing" else intraday_ohlcv
        htf_df = ohlcv_map.get(f"{entry.symbol}:htf")
        ltf_df = ohlcv_map.get(f"{entry.symbol}:ltf")

        if htf_df is None or ltf_df is None or htf_df.empty or ltf_df.empty:
            logger.debug("[trader] %s: missing OHLCV data, skipping", entry.symbol)
            continue

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
            new_setups.append(setup)
            dedup_keys.add(key)  # prevent intra-scan duplicates

    if not new_setups:
        logger.debug("[trader] scan_all_tickers: no new setups this cycle")
        return

    # 5. Persist new signals
    now = datetime.now(timezone.utc)
    with get_db() as db:
        for setup in new_setups:
            db.add(TradeSignal(
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
            ))

    logger.info("[trader] Firing %d new setup alert(s)", len(new_setups))

    # 6. Send Telegram alerts
    # Sort by confidence score descending so best setups arrive first
    new_setups.sort(key=lambda s: s.confluence.score, reverse=True)

    for setup in new_setups:
        try:
            await notify(setup.to_telegram_text(), parse_mode="Markdown")
            await asyncio.sleep(_BATCH_PAUSE)
        except Exception as e:
            logger.error("[trader] Failed to notify for %s: %s", setup.ticker, e)


# ── OHLCV fetching ────────────────────────────────────────────────────────────

def _fetch_all_ohlcv(entries: list, mode: str) -> dict:
    """Synchronous batch OHLCV fetch — runs inside an executor thread.

    Returns:
        Dict with keys "{symbol}:htf" and "{symbol}:ltf" → DataFrame
    """
    import yfinance as yf
    from modules.finance._shared.cache import ticker_cache

    if not entries:
        return {}

    htf_tf, ltf_tf = ("1wk", "4h") if mode == "swing" else ("1d", "1h")
    htf_period, htf_interval = _OHLCV_PARAMS[htf_tf]
    ltf_period, ltf_interval = _OHLCV_PARAMS[ltf_tf]

    symbols = [e.symbol for e in entries]
    result  = {}

    # --- HTF ---
    _fetch_tf_batch(symbols, htf_period, htf_interval, htf_tf, result, cache_key_suffix="htf")

    # --- LTF ---
    # yfinance doesn't support 4h natively; we fetch 1h and resample
    _fetch_tf_batch(symbols, ltf_period, ltf_interval, ltf_tf, result,
                    cache_key_suffix="ltf", resample_to="4h" if mode == "swing" else None)

    return result


def _fetch_tf_batch(symbols: list, period: str, interval: str, tf_label: str,
                    result: dict, cache_key_suffix: str, resample_to: str | None = None) -> None:
    """Fetch OHLCV for a list of symbols at one timeframe and populate result dict."""
    import yfinance as yf
    from modules.finance._shared.cache import ticker_cache

    uncached = []
    for sym in symbols:
        key = f"trader_ohlcv:{sym}:{tf_label}"
        cached = ticker_cache.get(key)
        if cached is not None:
            result[f"{sym}:{cache_key_suffix}"] = cached
        else:
            uncached.append(sym)

    if not uncached:
        return

    try:
        raw = yf.download(
            tickers=uncached,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=True,
            group_by="ticker" if len(uncached) > 1 else "column",
        )
    except Exception as e:
        logger.error("[trader] yfinance batch download failed (%s %s): %s", period, interval, e)
        return

    for sym in uncached:
        try:
            df = raw[sym] if len(uncached) > 1 else raw
            if hasattr(df.columns, "levels"):
                df.columns = df.columns.get_level_values(0)
            df.columns = [c.title() for c in df.columns]
            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
            if df.empty:
                continue

            # Resample 1H → 4H for swing LTF
            if resample_to == "4h":
                df = df.resample("4h").agg({
                    "Open":   "first",
                    "High":   "max",
                    "Low":    "min",
                    "Close":  "last",
                    "Volume": "sum",
                }).dropna()

            cache_key = f"trader_ohlcv:{sym}:{tf_label}"
            ticker_cache.set(cache_key, df)
            result[f"{sym}:{cache_key_suffix}"] = df

        except Exception as e:
            logger.warning("[trader] Failed to parse %s from batch (%s): %s", sym, tf_label, e)
