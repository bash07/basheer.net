"""Tests for finance/_shared/market_hours.py."""
import pytest
from unittest.mock import patch
from datetime import datetime
import zoneinfo

from modules.finance._shared.market_hours import is_market_open, open_categories

IST = zoneinfo.ZoneInfo("Asia/Kolkata")


def _at(weekday: int, hour: int, minute: int = 0):
    """Build a datetime for a specific IST weekday/time. weekday: 0=Mon, 5=Sat, 6=Sun."""
    # Find the next occurrence of the target weekday from a known Monday
    base = datetime(2025, 1, 6, hour, minute, tzinfo=IST)  # 2025-01-06 is a Monday
    return base.replace(day=6 + weekday)


class TestCryptoAlwaysOpen:
    def test_crypto_open_weekday(self):
        with patch("modules.finance._shared.market_hours.datetime") as m:
            m.now.return_value = _at(1, 3, 0)  # Tuesday 3 AM
            assert is_market_open("crypto") is True

    def test_crypto_open_weekend(self):
        with patch("modules.finance._shared.market_hours.datetime") as m:
            m.now.return_value = _at(5, 14, 0)  # Saturday 2 PM
            assert is_market_open("crypto") is True


class TestIndianMarket:
    def test_open_during_session(self):
        with patch("modules.finance._shared.market_hours.datetime") as m:
            m.now.return_value = _at(0, 11, 0)  # Monday 11 AM
            assert is_market_open("indian_stock") is True

    def test_closed_before_open(self):
        with patch("modules.finance._shared.market_hours.datetime") as m:
            m.now.return_value = _at(0, 8, 0)  # Monday 8 AM (before 9:15)
            assert is_market_open("indian_stock") is False

    def test_closed_after_close(self):
        with patch("modules.finance._shared.market_hours.datetime") as m:
            m.now.return_value = _at(0, 16, 0)  # Monday 4 PM (after 3:30)
            assert is_market_open("indian_stock") is False

    def test_closed_on_weekend(self):
        with patch("modules.finance._shared.market_hours.datetime") as m:
            m.now.return_value = _at(5, 11, 0)  # Saturday 11 AM
            assert is_market_open("indian_stock") is False

    def test_indian_index_same_hours(self):
        with patch("modules.finance._shared.market_hours.datetime") as m:
            m.now.return_value = _at(2, 12, 0)  # Wednesday noon
            assert is_market_open("indian_index") is True


class TestUSMarket:
    def test_open_during_evening_session(self):
        with patch("modules.finance._shared.market_hours.datetime") as m:
            m.now.return_value = _at(0, 21, 0)  # Monday 9 PM IST (market open)
            assert is_market_open("us_stock") is True

    def test_open_after_midnight_before_close(self):
        with patch("modules.finance._shared.market_hours.datetime") as m:
            m.now.return_value = _at(1, 1, 0)  # Tuesday 1 AM IST (overnight session)
            assert is_market_open("us_stock") is True

    def test_closed_midday(self):
        with patch("modules.finance._shared.market_hours.datetime") as m:
            m.now.return_value = _at(0, 13, 0)  # Monday 1 PM IST (US market closed)
            assert is_market_open("us_stock") is False

    def test_closed_on_weekend(self):
        with patch("modules.finance._shared.market_hours.datetime") as m:
            m.now.return_value = _at(6, 20, 0)  # Sunday 8 PM IST
            assert is_market_open("us_stock") is False


class TestUnknownCategory:
    def test_unknown_returns_true(self):
        """Unknown categories fail-open to avoid silently skipping tickers."""
        assert is_market_open("unknown_market") is True


class TestOpenCategories:
    def test_returns_list(self):
        result = open_categories()
        assert isinstance(result, list)
        # Crypto is always in the list
        assert "crypto" in result
