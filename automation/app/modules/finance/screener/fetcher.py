"""Batch OHLCV and fundamental data fetcher for screening."""
from __future__ import annotations

import logging
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)


def fetch_batch_ohlcv(
    symbols: list[str],
    period: str = "1y",
    interval: str = "1d",
) -> dict[str, pd.DataFrame]:
    """Download daily/weekly OHLCV data for multiple symbols in parallel.

    Returns dict mapping canonical symbol string to individual clean OHLCV DataFrame.
    """
    if not symbols:
        return {}

    try:
        import yfinance as yf
    except ImportError:
        logger.error("[screener.fetcher] yfinance not installed")
        return {}

    logger.info("[screener.fetcher] Batch downloading %d symbols (%s, %s)...", len(symbols), period, interval)
    try:
        raw_df = yf.download(
            tickers=" ".join(symbols),
            period=period,
            interval=interval,
            group_by="ticker",
            threads=True,
            progress=False,
            auto_adjust=False,
        )
    except Exception as e:
        logger.error("[screener.fetcher] Batch download failed: %s", e)
        return {}

    result: dict[str, pd.DataFrame] = {}
    if raw_df is None or raw_df.empty:
        return result

    # When 1 symbol is fetched, columns are 1-level (Open, High, Low, Close, Volume)
    if len(symbols) == 1:
        sym = symbols[0]
        cleaned = raw_df.dropna(how="all").copy()
        if not cleaned.empty:
            result[sym] = cleaned
        return result

    # When multiple symbols are fetched, columns are MultiIndex: (Ticker, Field) or (Field, Ticker)
    for sym in symbols:
        try:
            if isinstance(raw_df.columns, pd.MultiIndex):
                # Check level 0
                if sym in raw_df.columns.levels[0]:
                    df_sym = raw_df[sym].dropna(how="all").copy()
                elif sym in raw_df.columns.levels[1]:
                    df_sym = raw_df.xs(sym, level=1, axis=1).dropna(how="all").copy()
                else:
                    continue
            else:
                df_sym = raw_df.dropna(how="all").copy()

            if not df_sym.empty and "Close" in df_sym.columns:
                result[sym] = df_sym
        except Exception as e:
            logger.debug("[screener.fetcher] Error slicing df for %s: %s", sym, e)

    logger.info("[screener.fetcher] Successfully parsed %d / %d DataFrames", len(result), len(symbols))
    return result
