"""Data Provider abstraction for the finance/trader module.

All market data fetching goes through get_provider(category), which returns
the appropriate DataProvider (e.g. YFinanceProvider for stocks/forex,
BinanceProvider for crypto).

Architecture:
- DataProvider (ABC): Common interface for OHLCV & current price.
- YFinanceProvider: Multi-asset provider using yfinance with shared caching.
- BinanceProvider: Fast, native crypto provider using public Binance REST API.
  Provides true 4H bars without resampling delays.
- Provider Registry: Supports global default + per-category routing
  (e.g., category 'crypto' -> binance, 'us_stock' -> yfinance).

Usage:
    from modules.finance.trader.data import get_provider

    # Provider for a category
    crypto_prov = get_provider("crypto")
    df = crypto_prov.fetch_ohlcv("BTC-USD", "4h")

    # Global default provider
    default_prov = get_provider()
    df = default_prov.fetch_ohlcv("AAPL", "1d")

Custom provider:
    from modules.finance.trader.data import DataProvider, register_provider, route_category

    class AlpacaProvider(DataProvider):
        ...

    register_provider("alpaca", AlpacaProvider())
    route_category("us_stock", "alpaca")
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Default lookback period strings when neither period nor start/end is passed
_DEFAULT_PERIODS = {
    "1wk": "2y",
    "1w":  "2y",
    "1d":  "1y",
    "4h":  "60d",
    "1h":  "30d",
    "15m": "7d",
    "5m":  "5d",
    "1m":  "1d",
}


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
        """Fetch OHLCV bars for a single symbol.

        Args:
            symbol:      Ticker symbol (e.g. "AAPL", "BTC-USD")
            interval:    Bar interval ("1h", "4h", "1d", "1wk", etc.)
            period:      Lookback period string ("30d", "1y") — mutually exclusive with start/end.
            start:       Start datetime (UTC).
            end:         End datetime (UTC).
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
    """DataProvider backed by yfinance with shared caching & auto-resampling."""

    _CACHE_PREFIX = "ohlcv:yf:"

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

        if period is None and start is None:
            period = _DEFAULT_PERIODS.get(interval, "30d")

        # yfinance lacks native 4H support: fetch 1H and auto-resample
        fetch_interval = "1h" if interval == "4h" else ("1wk" if interval == "1w" else interval)
        target_resample = "4h" if interval == "4h" else resample_to

        cache_key = f"{self._CACHE_PREFIX}{symbol}:{interval}:{period or ''}:{start}:{end}"
        alt_key = f"ohlcv:{symbol}:{period or ''}:{interval}"
        if ticker_cache:
            cached = ticker_cache.get(cache_key) or ticker_cache.get(alt_key)
            if cached is not None:
                return cached

        df = self._download_one(symbol, fetch_interval, period, start, end)
        if target_resample and df is not None and not df.empty:
            df = self._resample(df, target_resample)

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

        if period is None and start is None:
            period = _DEFAULT_PERIODS.get(interval, "30d")

        fetch_interval = "1h" if interval == "4h" else ("1wk" if interval == "1w" else interval)
        target_resample = "4h" if interval == "4h" else resample_to

        result: dict[str, object] = {}
        uncached: list[str] = []

        # Check cache first
        for sym in symbols:
            cache_key = f"{self._CACHE_PREFIX}{sym}:{interval}:{period or ''}:{start}:{end}"
            if ticker_cache:
                cached = ticker_cache.get(cache_key)
                if cached is not None:
                    result[sym] = cached
                    continue
            uncached.append(sym)

        if not uncached:
            return result

        # Batch download uncached
        try:
            import yfinance as yf
            kwargs: dict = {"interval": fetch_interval, "auto_adjust": True, "progress": False}
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
            logger.error("[trader.data] YFinance batch download failed (%s %s): %s",
                         fetch_interval, period or "", e)
            return result

        for sym in uncached:
            try:
                df = raw[sym] if len(uncached) > 1 else raw
                df = self._normalise(df)
                if df is None or df.empty:
                    continue
                if target_resample:
                    df = self._resample(df, target_resample)
                cache_key = f"{self._CACHE_PREFIX}{sym}:{interval}:{period or ''}:{start}:{end}"
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
            logger.warning("[trader.data] YFinance get_current_price failed for %s: %s", symbol, e)
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
            logger.error("[trader.data] YFinance download failed for %s (%s): %s", symbol, interval, e)
            return None

    @staticmethod
    def _normalise(df) -> object:
        """Normalise a raw yfinance DataFrame to Open/High/Low/Close/Volume."""
        if df is None:
            return None
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


# ── Binance Implementation ───────────────────────────────────────────────────

class BinanceProvider(DataProvider):
    """DataProvider backed by Binance public REST API.

    Features:
    - Native 4H bars (no resampling required).
    - True crypto exchange order execution prices & volumes.
    - Zero external dependency (standard library urllib).
    - Automatic symbol conversion (e.g. BTC-USD -> BTCUSDT).
    """

    _BASE_URLS = [
        "https://api.binance.com",
        "https://data-api.binance.vision",
    ]
    _CACHE_PREFIX = "ohlcv:binance:"

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        """Map generic watchlist symbols to Binance USDT pairs."""
        s = symbol.upper().replace("/", "").strip()
        if s.endswith("-USD"):
            return s[:-4] + "USDT"
        if not any(s.endswith(quote) for quote in ("USDT", "BUSD", "FDUSD", "USDC", "BTC")):
            return s + "USDT"
        return s

    @staticmethod
    def normalize_interval(interval: str) -> str:
        """Map standard interval strings to Binance interval codes."""
        i = interval.lower()
        if i in ("1wk", "1week"):
            return "1w"
        if i in ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"):
            return i
        return "1h"

    def fetch_ohlcv(
        self,
        symbol: str,
        interval: str,
        period: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        resample_to: Optional[str] = None,
    ) -> object:
        """Fetch OHLCV bars from Binance."""
        try:
            from modules.finance._shared.cache import ticker_cache
        except Exception:
            ticker_cache = None

        if period is None and start is None:
            period = _DEFAULT_PERIODS.get(interval, "30d")

        binance_sym = self.normalize_symbol(symbol)
        binance_int = self.normalize_interval(interval)

        cache_key = f"{self._CACHE_PREFIX}{binance_sym}:{binance_int}:{period or ''}:{start}:{end}"
        if ticker_cache:
            cached = ticker_cache.get(cache_key)
            if cached is not None:
                return cached

        df = self._fetch_klines(binance_sym, binance_int, period=period, start=start, end=end)
        if resample_to and df is not None and not df.empty and resample_to != interval:
            df = YFinanceProvider._resample(df, resample_to)

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
        """Fetch OHLCV for multiple symbols concurrently using ThreadPoolExecutor."""
        if not symbols:
            return {}

        results: dict[str, object] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(symbols), 8)) as executor:
            future_to_sym = {
                executor.submit(
                    self.fetch_ohlcv, sym, interval, period, start, end, resample_to
                ): sym
                for sym in symbols
            }
            for future in concurrent.futures.as_completed(future_to_sym):
                sym = future_to_sym[future]
                try:
                    df = future.result()
                    if df is not None and not df.empty:
                        results[sym] = df
                except Exception as e:
                    logger.warning("[trader.data] Binance fetch failed for %s: %s", sym, e)

        return results

    def get_current_price(self, symbol: str) -> float:
        """Get latest price from Binance /api/v3/ticker/price."""
        binance_sym = self.normalize_symbol(symbol)
        for base_url in self._BASE_URLS:
            try:
                url = f"{base_url}/api/v3/ticker/price?symbol={urllib.parse.quote(binance_sym)}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode())
                    return float(data["price"])
            except Exception as e:
                logger.debug("[trader.data] Binance price check failed on %s: %s", base_url, e)
                continue
        logger.warning("[trader.data] Binance price unavailable for %s", symbol)
        return 0.0

    def _fetch_klines(
        self,
        symbol: str,
        interval: str,
        period: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ):
        """Fetch and parse klines from Binance."""
        import pandas as pd

        # Calculate start timestamp in milliseconds
        start_ms = None
        end_ms = None

        now = datetime.now(timezone.utc)
        if start:
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            start_ms = int(start.timestamp() * 1000)
        elif period:
            delta = self._parse_period(period)
            start_ms = int((now - delta).timestamp() * 1000)

        if end:
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            end_ms = int(end.timestamp() * 1000)

        all_klines = []
        limit = 1000

        curr_start = start_ms
        for _ in range(5):  # Max 5 chunks (up to 5,000 bars)
            params = {
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
            }
            if curr_start:
                params["startTime"] = curr_start
            if end_ms:
                params["endTime"] = end_ms

            chunk = self._request_klines(params)
            if not chunk:
                break
            all_klines.extend(chunk)
            if len(chunk) < limit:
                break
            # Next chunk starts 1ms after the last candle close time
            curr_start = int(chunk[-1][6]) + 1
            if end_ms and curr_start >= end_ms:
                break

        if not all_klines:
            return None

        # Build DataFrame
        try:
            df = pd.DataFrame(all_klines, columns=[
                "open_time", "Open", "High", "Low", "Close", "Volume",
                "close_time", "quote_volume", "count", "taker_buy_vol", "taker_buy_quote_vol", "ignore"
            ])
            df["Open"] = df["Open"].astype(float)
            df["High"] = df["High"].astype(float)
            df["Low"] = df["Low"].astype(float)
            df["Close"] = df["Close"].astype(float)
            df["Volume"] = df["Volume"].astype(float)
            df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            df = df[["Open", "High", "Low", "Close", "Volume"]]
            # Drop duplicates if chunks overlapped
            df = df[~df.index.duplicated(keep="first")].sort_index()
            return df if not df.empty else None
        except Exception as e:
            logger.error("[trader.data] Error parsing Binance klines for %s: %s", symbol, e)
            return None

    def _request_klines(self, params: dict) -> list:
        query = urllib.parse.urlencode(params)
        for base_url in self._BASE_URLS:
            try:
                url = f"{base_url}/api/v3/klines?{query}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return json.loads(resp.read().decode())
            except Exception as e:
                logger.debug("[trader.data] Binance klines failed on %s: %s", base_url, e)
                continue
        return []

    @staticmethod
    def _parse_period(period_str: str) -> timedelta:
        """Parse period string like '30d', '1y', '60d' into a timedelta."""
        p = period_str.strip().lower()
        if p.endswith("d"):
            return timedelta(days=int(p[:-1]))
        if p.endswith("mo") or p.endswith("m"):
            val = p[:-2] if p.endswith("mo") else p[:-1]
            return timedelta(days=int(val) * 30)
        if p.endswith("y"):
            return timedelta(days=int(p[:-1]) * 365)
        if p.endswith("h"):
            return timedelta(hours=int(p[:-1]))
        return timedelta(days=30)


# ── Provider Registry & Category Routing ──────────────────────────────────────

_PROVIDERS: dict[str, DataProvider] = {
    "yfinance": YFinanceProvider(),
    "binance":  BinanceProvider(),
}

# Category to provider mapping: e.g. "crypto" -> "binance"
_CATEGORY_ROUTES: dict[str, str] = {
    "crypto": "binance",
}

_DEFAULT_PROVIDER: str = "yfinance"


def register_provider(name: str, provider: DataProvider) -> None:
    """Register a custom DataProvider under a unique name."""
    _PROVIDERS[name.lower()] = provider
    logger.info("[trader.data] Registered provider '%s' (%s)", name, type(provider).__name__)


def route_category(category: str, provider_name: str) -> None:
    """Route a market category (e.g. 'crypto', 'us_stock') to a named provider."""
    provider_name = provider_name.lower()
    if provider_name not in _PROVIDERS:
        raise ValueError(f"Unknown provider '{provider_name}'. Available: {list(_PROVIDERS.keys())}")
    _CATEGORY_ROUTES[category.lower()] = provider_name
    logger.info("[trader.data] Routed category '%s' -> provider '%s'", category, provider_name)


def get_provider(category: Optional[str] = None) -> DataProvider:
    """Return the active DataProvider for a given category (or the global default).

    Args:
        category: Watchlist category ('crypto', 'us_stock', 'forex', etc.)
    """
    if category:
        cat_key = category.lower()
        provider_name = _CATEGORY_ROUTES.get(cat_key)
        if provider_name and provider_name in _PROVIDERS:
            return _PROVIDERS[provider_name]

    return _PROVIDERS.get(_DEFAULT_PROVIDER, _PROVIDERS["yfinance"])


def set_provider(
    provider: DataProvider,
    category: Optional[str] = None,
    name: Optional[str] = None,
) -> None:
    """Set or replace an active data provider.

    Args:
        provider: An instance of DataProvider.
        category: Optional category to route to this provider. If None, sets global default.
        name:     Optional registration name. Defaults to class name.
    """
    prov_name = (name or type(provider).__name__.lower().replace("provider", "")).lower()
    register_provider(prov_name, provider)

    if category:
        route_category(category, prov_name)
    else:
        global _DEFAULT_PROVIDER
        _DEFAULT_PROVIDER = prov_name
        logger.info("[trader.data] Global default provider switched to %s", prov_name)


def get_active_routes() -> dict[str, str]:
    """Return a dictionary of {category: provider_name} along with 'default'."""
    routes = dict(_CATEGORY_ROUTES)
    routes["default"] = _DEFAULT_PROVIDER
    return routes


def list_providers() -> list[str]:
    """Return list of all registered provider names."""
    return sorted(_PROVIDERS.keys())


# ── Auto-load provider configuration from env & config ────────────────────────

def _load_config_routes() -> None:
    # 1. Environment variable overrides
    env_default = os.environ.get("TRADER_DEFAULT_PROVIDER")
    if env_default and env_default.lower() in _PROVIDERS:
        global _DEFAULT_PROVIDER
        _DEFAULT_PROVIDER = env_default.lower()

    env_crypto = os.environ.get("TRADER_CRYPTO_PROVIDER")
    if env_crypto and env_crypto.lower() in _PROVIDERS:
        _CATEGORY_ROUTES["crypto"] = env_crypto.lower()

    # 2. Config file routes (e.g. from .trader_config.json)
    try:
        from .manage import load_config
        cfg = load_config()
        providers_cfg = cfg.get("providers", {})
        for cat, prov_name in providers_cfg.items():
            if prov_name.lower() in _PROVIDERS:
                if cat == "default":
                    _DEFAULT_PROVIDER = prov_name.lower()
                else:
                    _CATEGORY_ROUTES[cat.lower()] = prov_name.lower()
    except Exception:
        pass


_load_config_routes()
