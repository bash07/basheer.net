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
import os
import pickle
import sqlite3
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


def _get_cache_db_path() -> str:
    """Return persistent SQLite database path for disk cache."""
    try:
        from config import config
        if config.data_dir and os.path.isdir(config.data_dir):
            return os.path.join(config.data_dir, "cache.db")
    except Exception:
        pass
    fallback_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
    os.makedirs(fallback_dir, exist_ok=True)
    return os.path.join(fallback_dir, "cache.db")


class TTLCache:
    """Thread-safe cache with per-key time-to-live expiry and SQLite disk persistence.
    
    Provides an L1 in-memory dict for high-frequency sub-millisecond access and
    an L2 SQLite disk backing so that long-lived items (fundamentals, OHLCV)
    survive application and container restarts.
    """

    def __init__(
        self,
        ttl_seconds: int = 300,
        name: str = "cache",
        db_path: str | None = None,
        persist: bool = True,
    ) -> None:
        self._store: dict[str, tuple[Any, float]] = {}  # key → (value, expires_at_wallclock)
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._name = name
        self._persist = persist
        self._db_path = db_path or _get_cache_db_path()
        if self._persist:
            self._init_disk()

    def _init_disk(self) -> None:
        """Initialize SQLite disk cache table if persisting."""
        try:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            with sqlite3.connect(self._db_path, timeout=5.0) as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cache_entries (
                        namespace TEXT NOT NULL,
                        key TEXT NOT NULL,
                        value BLOB NOT NULL,
                        expires_at REAL NOT NULL,
                        PRIMARY KEY (namespace, key)
                    );
                    """
                )
        except Exception as e:
            logger.warning("[%s] Failed to initialize SQLite cache table: %s", self._name, e)

    def get(self, key: str) -> Any | None:
        """Return the cached value for key, or None if missing/expired."""
        now = time.time()
        with self._lock:
            # 1. Check L1 Memory Cache
            entry = self._store.get(key)
            if entry is not None:
                value, expires_at = entry
                if now > expires_at:
                    del self._store[key]
                    logger.debug("[%s] L1 cache EXPIRED: %s", self._name, key)
                else:
                    return value

            # 2. Check L2 Disk Cache
            if not self._persist:
                return None

            try:
                with sqlite3.connect(self._db_path, timeout=5.0) as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT value, expires_at FROM cache_entries WHERE namespace = ? AND key = ?",
                        (self._name, key),
                    )
                    row = cur.fetchone()
                    if row is not None:
                        val_blob, expires_at = row
                        if now > expires_at:
                            cur.execute(
                                "DELETE FROM cache_entries WHERE namespace = ? AND key = ?",
                                (self._name, key),
                            )
                            conn.commit()
                            logger.debug("[%s] L2 disk cache EXPIRED: %s", self._name, key)
                            return None
                        value = pickle.loads(val_blob)
                        # Promote to L1
                        self._store[key] = (value, expires_at)
                        return value
            except Exception as e:
                logger.debug("[%s] L2 disk cache get error: %s", self._name, e)
            return None

    def set(self, key: str, value: Any) -> None:
        """Store a value with the configured TTL."""
        now = time.time()
        expires_at = now + self._ttl
        with self._lock:
            self._store[key] = (value, expires_at)
            logger.debug("[%s] cache SET: %s (ttl=%ds)", self._name, key, self._ttl)

            if self._persist:
                try:
                    val_blob = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
                    with sqlite3.connect(self._db_path, timeout=5.0) as conn:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO cache_entries (namespace, key, value, expires_at)
                            VALUES (?, ?, ?, ?)
                            """,
                            (self._name, key, val_blob, expires_at),
                        )
                except Exception as e:
                    logger.debug("[%s] L2 disk cache set error: %s", self._name, e)

    def delete(self, key: str) -> None:
        """Explicitly evict a key (e.g. after a watchlist change)."""
        with self._lock:
            self._store.pop(key, None)
            if self._persist:
                try:
                    with sqlite3.connect(self._db_path, timeout=5.0) as conn:
                        conn.execute(
                            "DELETE FROM cache_entries WHERE namespace = ? AND key = ?",
                            (self._name, key),
                        )
                except Exception as e:
                    logger.debug("[%s] L2 disk cache delete error: %s", self._name, e)

    def clear(self) -> None:
        """Flush the entire cache namespace."""
        with self._lock:
            self._store.clear()
            if self._persist:
                try:
                    with sqlite3.connect(self._db_path, timeout=5.0) as conn:
                        conn.execute(
                            "DELETE FROM cache_entries WHERE namespace = ?",
                            (self._name,),
                        )
                except Exception as e:
                    logger.debug("[%s] L2 disk cache clear error: %s", self._name, e)
            logger.debug("[%s] cache CLEARED", self._name)

    def size(self) -> int:
        """Return the number of live (non-expired) entries."""
        now = time.time()
        with self._lock:
            if not self._persist:
                return sum(1 for _, exp in self._store.values() if exp > now)

            try:
                with sqlite3.connect(self._db_path, timeout=5.0) as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT COUNT(*) FROM cache_entries WHERE namespace = ? AND expires_at > ?",
                        (self._name, now),
                    )
                    return cur.fetchone()[0]
            except Exception as e:
                logger.debug("[%s] L2 disk cache size error: %s", self._name, e)
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
