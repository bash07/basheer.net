"""Data Provider abstraction for the finance/trader module.

All OHLCV fetching goes through get_provider(), which returns the currently
configured DataProvider. To swap data sources (e.g. Binance, IBKR, Alpaca),
call set_provider() at startup with a custom implementation.

Usage:
    from modules.finance.trader.data import get_provider
    df = get_provider().fetch_ohlcv("BTC-USD", "1h", period="30d")

Custom provider:
    from modules.finance.trader.data import DataProvider, set_provider

    class MyBrokerProvider(DataProvider):
        def fetch_ohlcv(self, symbol, interval, period=None, start=None, end=None):
            ...
        def get_current_price(self, symbol):
            ...

    set_provider(MyBrokerProvider())
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# ── Abstract Interface ────────────────────────────────────────────────────────

class DataProvider(ABC):
    """Abstract base class for all market data providers."""

    @abstractmethod
    def fetch_ohlcv(
        self,
        symbol: str,
        interval: str,
        period: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        resample_to: Optional[str] = None,
    ) -> object:  # pd.DataFrame
        """Fetch OHLCV bars for a symbol.

        Args:
            symbol:      Ticker symbol (e.g. "AAPL", "BTC-USD")
            interval:    Bar interval ("1h", "1d", "1wk", etc.)
            period:      Lookback period string ("30d", "1y") — mutually exclusive with start/end.
            start:       Start datetime (UTC) — mutually exclusive with period.
            end:         End datetime (UTC) — defaults to now if start is given.
            resample_to: Optional target interval to resample into (e.g. "4h" from "1h").

        Returns:
            pd.DataFrame with columns Open/High/Low/Close/Volume and DatetimeIndex.
        """
        ...

    @abstractmethod
    def fetch_ohlcv_batch(
        self,
        symbols: list[str],
        interval: str,
        period: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        resample_to: Optional[str] = None,
    ) -> dict[str, object]:  # dict[str, pd.DataFrame]
        """Batch fetch OHLCV for multiple symbols. Returns {symbol: DataFrame}."""
        ...

    @abstractmethod
    def get_current_price(self, symbol: str) -> float:
        """Return the latest price for a symbol."""
        ...


# ── YFinance Implementation ───────────────────────────────────────────────────

class YFinanceProvider(DataProvider):
    """DataProvider backed by yfinance (default)."""

    # Cache TTL is handled by the shared ticker_cache from _shared/cache.py
    _CACHE_PREFIX = "ohlcv:"

    def fetch_ohlcv(
        self,
        symbol: str,
        interval: str,
        period: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        resample_to: Optional[str] = None,
    ) -> object:
        """Fetch a single symbol's OHLCV from yfinance with shared cache."""
        try:
            from modules.finance._shared.cache import ticker_cache
        except Exception:
            ticker_cache = None

        cache_key = f"{self._CACHE_PREFIX}{symbol}:{interval}:{period or ''}"
        if ticker_cache:
            cached = ticker_cache.get(cache_key)
            if cached is not None:
                return cached

        df = self._download_one(symbol, interval, period, start, end)
        if resample_to and df is not None and not df.empty:
            df = self._resample(df, resample_to)
        if df is not None and not df.empty and ticker_cache:
            ticker_cache.set(cache_key, df)
        return df

    def fetch_ohlcv_batch(
        self,
        symbols: list[str],
        interval: str,
        period: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        resample_to: Optional[str] = None,
    ) -> dict[str, object]:
        """Batch download for multiple symbols using yfinance group_by='ticker'."""
        if not symbols:
            return {}

        try:
            from modules.finance._shared.cache import ticker_cache
        except Exception:
            ticker_cache = None

        result: dict[str, object] = {}
        uncached: list[str] = []

        # Check cache first
        for sym in symbols:
            cache_key = f"{self._CACHE_PREFIX}{sym}:{interval}:{period or ''}"
            if ticker_cache:
                cached = ticker_cache.get(cache_key)
                if cached is not None:
                    result[sym] = cached
                    continue
            uncached.append(sym)

        if not uncached:
            return result

        # Batch download
        try:
            import yfinance as yf
            kwargs: dict = {"interval": interval, "auto_adjust": True, "progress": False}
            if period:
                kwargs["period"] = period
            if start:
                kwargs["start"] = start
            if end:
                kwargs["end"] = end

            raw = yf.download(
                tickers=uncached,
                group_by="ticker" if len(uncached) > 1 else "column",
                **kwargs,
            )
        except Exception as e:
            logger.error("[trader.data] Batch download failed (%s %s): %s",
                         interval, period or "", e)
            return result

        for sym in uncached:
            try:
                df = raw[sym] if len(uncached) > 1 else raw
                df = self._normalise(df)
                if df is None or df.empty:
                    continue
                if resample_to:
                    df = self._resample(df, resample_to)
                cache_key = f"{self._CACHE_PREFIX}{sym}:{interval}:{period or ''}"
                if ticker_cache:
                    ticker_cache.set(cache_key, df)
                result[sym] = df
            except Exception as e:
                logger.warning("[trader.data] Failed to parse %s: %s", sym, e)

        return result

    def get_current_price(self, symbol: str) -> float:
        try:
            import yfinance as yf
            return float(yf.Ticker(symbol).fast_info["lastPrice"])
        except Exception as e:
            logger.warning("[trader.data] get_current_price failed for %s: %s", symbol, e)
            return 0.0

    # ── Private helpers ───────────────────────────────────────────────────────

    def _download_one(
        self, symbol: str, interval: str,
        period: Optional[str], start: Optional[datetime], end: Optional[datetime],
    ) -> object:
        try:
            import yfinance as yf
            kwargs: dict = {"interval": interval, "auto_adjust": True, "progress": False}
            if period:
                kwargs["period"] = period
            if start:
                kwargs["start"] = start
            if end:
                kwargs["end"] = end
            df = yf.download(symbol, **kwargs)
            return self._normalise(df)
        except Exception as e:
            logger.error("[trader.data] Download failed for %s (%s): %s", symbol, interval, e)
            return None

    @staticmethod
    def _normalise(df) -> object:
        """Normalise a raw yfinance DataFrame to Open/High/Low/Close/Volume."""
        if df is None:
            return None
        # Flatten MultiIndex columns if present
        if hasattr(df.columns, "levels"):
            df.columns = df.columns.get_level_values(0)
        df.columns = [str(c).title() for c in df.columns]
        cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        df = df[cols].dropna()
        return df if not df.empty else None

    @staticmethod
    def _resample(df, target: str) -> object:
        """Resample OHLCV DataFrame to a larger interval."""
        try:
            return df.resample(target).agg({
                "Open":   "first",
                "High":   "max",
                "Low":    "min",
                "Close":  "last",
                "Volume": "sum",
            }).dropna()
        except Exception as e:
            logger.warning("[trader.data] Resample to %s failed: %s", target, e)
            return df


# ── Module-level provider registry ───────────────────────────────────────────

_provider: DataProvider = YFinanceProvider()


def get_provider() -> DataProvider:
    """Return the currently active data provider."""
    return _provider


def set_provider(provider: DataProvider) -> None:
    """Replace the active data provider. Call at startup before any fetching."""
    global _provider
    _provider = provider
    logger.info("[trader.data] Provider switched to %s", type(provider).__name__)
