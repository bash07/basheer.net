"""Shared in-memory TTL cache for the finance domain.

Prevents redundant HTTP calls to yfinance and external APIs when multiple
commands or scheduled jobs request the same data within a short window.

Usage:
    from modules.finance._shared.cache import ticker_cache, coingecko_cache

    data = ticker_cache.get("AAPL")
    if data is None:
        data = fetch_from_yfinance("AAPL")
        ticker_cache.set("AAPL", data)
"""
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


class TTLCache:
    """Thread-safe in-memory cache with per-key time-to-live expiry."""

    def __init__(self, ttl_seconds: int = 300, name: str = "cache") -> None:
        self._store: dict[str, tuple[Any, float]] = {}  # key → (value, expires_at)
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._name = name

    def get(self, key: str) -> Any | None:
        """Return the cached value for key, or None if missing/expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                logger.debug("[%s] cache EXPIRED: %s", self._name, key)
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        """Store a value with the configured TTL."""
        with self._lock:
            self._store[key] = (value, time.monotonic() + self._ttl)
            logger.debug("[%s] cache SET: %s (ttl=%ds)", self._name, key, self._ttl)

    def delete(self, key: str) -> None:
        """Explicitly evict a key (e.g. after a watchlist change)."""
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        """Flush the entire cache."""
        with self._lock:
            self._store.clear()
            logger.debug("[%s] cache CLEARED", self._name)

    def size(self) -> int:
        """Return the number of live (non-expired) entries."""
        with self._lock:
            now = time.monotonic()
            return sum(1 for _, exp in self._store.values() if exp > now)


# ── Module-level singletons — import and share these, don't create your own ──

# 5-minute cache for yfinance ticker OHLCV + info responses
ticker_cache = TTLCache(ttl_seconds=300, name="ticker")

# 10-minute cache for CoinGecko API responses
coingecko_cache = TTLCache(ttl_seconds=600, name="coingecko")

# 15-minute cache for alternative.me Fear & Greed Index
feargreed_cache = TTLCache(ttl_seconds=900, name="feargreed")

# 24-hour cache for yfinance fundamentals (Ticker.info is slow — ~1-2 s/ticker)
fundamentals_cache = TTLCache(ttl_seconds=86400, name="fundamentals")
