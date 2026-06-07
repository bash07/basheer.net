"""Tests for finance/_shared/cache.py — TTLCache."""
import time
import pytest
from modules.finance._shared.cache import TTLCache, ticker_cache, coingecko_cache, feargreed_cache


class TestTTLCache:
    def test_set_and_get(self):
        c = TTLCache(ttl_seconds=60)
        c.set("key", "value")
        assert c.get("key") == "value"

    def test_get_missing_returns_none(self):
        c = TTLCache(ttl_seconds=60)
        assert c.get("nonexistent") is None

    def test_expiry(self):
        c = TTLCache(ttl_seconds=1)
        c.set("key", "value")
        assert c.get("key") == "value"
        time.sleep(1.05)
        assert c.get("key") is None  # expired

    def test_delete(self):
        c = TTLCache(ttl_seconds=60)
        c.set("key", "value")
        c.delete("key")
        assert c.get("key") is None

    def test_delete_nonexistent_is_safe(self):
        c = TTLCache(ttl_seconds=60)
        c.delete("never_existed")  # should not raise

    def test_clear(self):
        c = TTLCache(ttl_seconds=60)
        c.set("a", 1)
        c.set("b", 2)
        c.clear()
        assert c.size() == 0

    def test_size_counts_live_entries(self):
        c = TTLCache(ttl_seconds=60)
        assert c.size() == 0
        c.set("x", 1)
        c.set("y", 2)
        assert c.size() == 2

    def test_overwrite(self):
        c = TTLCache(ttl_seconds=60)
        c.set("key", "original")
        c.set("key", "updated")
        assert c.get("key") == "updated"

    def test_stores_various_types(self):
        c = TTLCache(ttl_seconds=60)
        c.set("list",  [1, 2, 3])
        c.set("dict",  {"a": 1})
        c.set("none",  None)
        # None value stored but get() returns None — distinguish via .get() missing check
        assert c.get("list") == [1, 2, 3]
        assert c.get("dict") == {"a": 1}

    def test_singletons_exist(self):
        """Module-level singleton instances must be available."""
        assert isinstance(ticker_cache,    TTLCache)
        assert isinstance(coingecko_cache, TTLCache)
        assert isinstance(feargreed_cache, TTLCache)
