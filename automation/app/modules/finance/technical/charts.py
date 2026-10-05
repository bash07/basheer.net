"""Data fetching + chart rendering coordinator for finance/technical.

Usage:
    from modules.finance.technical.charts import fetch_and_render, fetch_ta_only

    png_path, result = await fetch_and_render("AAPL", timeframe="1d")
    result           = await fetch_ta_only("AAPL")
"""
import asyncio
import logging

from modules.finance._shared.chart_renderer import render_chart
from .indicators import compute_ta, TAResult

logger = logging.getLogger(__name__)

# yfinance period/interval for each user-facing timeframe
_TF_MAP: dict[str, tuple[str, str]] = {
    "1d":  ("6mo",  "1d"),   # 6 months of daily candles
    "1wk": ("2y",   "1wk"),  # 2 years of weekly candles
    "1mo": ("5y",   "1mo"),  # 5 years of monthly candles
}
DEFAULT_TF = "1d"


# ── Data layer ────────────────────────────────────────────────────────────────

def _fetch_ohlcv_sync(symbol: str, period: str, interval: str):
    """Blocking yfinance download with caching. Run inside an executor."""
    import yfinance as yf    # lazy
    from modules.finance._shared.cache import ticker_cache

    cache_key = f"ohlcv:{symbol}:{period}:{interval}"
    cached = ticker_cache.get(cache_key)
    if cached is not None:
        return cached

    raw = yf.download(
        symbol, period=period, interval=interval,
        progress=False, auto_adjust=True,
    )
    if raw.empty:
        raise ValueError(f"No data returned for '{symbol}'. Check the symbol.")

    # yfinance sometimes returns MultiIndex columns — flatten
    if hasattr(raw.columns, "levels"):
        raw.columns = raw.columns.get_level_values(0)
    raw.columns = [c.title() for c in raw.columns]
    df = raw[["Open", "High", "Low", "Close", "Volume"]].dropna()

    ticker_cache.set(cache_key, df)
    return df


async def _fetch(symbol: str, timeframe: str):
    tf = timeframe if timeframe in _TF_MAP else DEFAULT_TF
    period, interval = _TF_MAP[tf]
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_ohlcv_sync, symbol, period, interval), tf


# ── Public interface ──────────────────────────────────────────────────────────

async def fetch_and_render(symbol: str, timeframe: str = DEFAULT_TF) -> tuple[str, TAResult]:
    """
    Fetch OHLCV, compute TA, render a chart PNG, return (png_path, TAResult).
    Caller must delete the PNG after sending (or use _shared/notify.notify_photo).
    """
    df, tf = await _fetch(symbol, timeframe)
    result = compute_ta(df, symbol=symbol, timeframe=tf)

    png_path = await render_chart({
        "ohlcv_json":       result.ohlcv_json,
        "emas":             result.ema_series,
        "rsi":              result.rsi_series,
        "volume_profile":   result.volume_profile,
        "title":            f"{symbol} — {tf.upper()}",
    })
    return png_path, result


async def fetch_ta_only(symbol: str, timeframe: str = DEFAULT_TF) -> TAResult:
    """Fetch OHLCV and compute TA without rendering a chart (used by /rsi)."""
    df, tf = await _fetch(symbol, timeframe)
    return compute_ta(df, symbol=symbol, timeframe=tf)
