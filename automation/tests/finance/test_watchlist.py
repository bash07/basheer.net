"""Tests for finance/watchlist — category detection, alias resolution, and DB operations."""
import pytest
from datetime import datetime

from modules.finance.watchlist.commands import _detect_category, _resolve_category


class TestResolveCategory:
    """_resolve_category must accept canonical AND no-underscore alias forms."""

    def test_canonical_indian_index(self):
        assert _resolve_category("indian_index") == "indian_index"

    def test_alias_indianindex(self):
        # This is the bug the user hit: Telegram strips _ from Markdown display
        # so users type 'indianindex' — it must still resolve.
        assert _resolve_category("indianindex") == "indian_index"

    def test_canonical_us_stock(self):
        assert _resolve_category("us_stock") == "us_stock"

    def test_alias_usstock(self):
        assert _resolve_category("usstock") == "us_stock"

    def test_alias_usindex(self):
        assert _resolve_category("usindex") == "us_index"

    def test_alias_indianstock(self):
        assert _resolve_category("indianstock") == "indian_stock"

    def test_no_change_crypto(self):
        assert _resolve_category("crypto") == "crypto"

    def test_invalid_returns_none(self):
        assert _resolve_category("nonsense") is None

    def test_case_insensitive(self):
        assert _resolve_category("CRYPTO") == "crypto"
        assert _resolve_category("IndianIndex") == "indian_index"


class TestDetectCategory:
    """Pure function — no DB or network needed."""

    def test_sp500(self):        assert _detect_category("^GSPC") == "us_index"
    def test_nasdaq(self):       assert _detect_category("^IXIC") == "us_index"
    def test_nifty50(self):      assert _detect_category("^NSEI") == "indian_index"
    def test_sensex(self):       assert _detect_category("^BSESN") == "indian_index"
    def test_unknown_caret(self):assert _detect_category("^UNKNOWN") == "us_index"  # default ^ → us_index
    def test_ns_suffix(self):    assert _detect_category("RELIANCE.NS") == "indian_stock"
    def test_bo_suffix(self):    assert _detect_category("INFY.BO") == "indian_stock"
    def test_btc_usd(self):      assert _detect_category("BTC-USD") == "crypto"
    def test_eth_usd(self):      assert _detect_category("ETH-USD") == "crypto"
    def test_usd_inr(self):      assert _detect_category("INR=X") == "forex"
    def test_eurusd(self):       assert _detect_category("EURUSD=X") == "forex"
    def test_gold(self):         assert _detect_category("GC=F") == "commodity"
    def test_crude(self):        assert _detect_category("CL=F") == "commodity"
    def test_us_stock_default(self): assert _detect_category("AAPL") == "us_stock"
    def test_case_preserved(self):   assert _detect_category("RELIANCE.NS") == "indian_stock"


class TestWatchlistDB:
    """Integration tests — use the clean_db fixture from conftest.py."""

    def test_add_entry(self, clean_db):
        from modules.finance.watchlist.models import WatchlistEntry
        entry = WatchlistEntry(symbol="AAPL", display_name="Apple Inc.", category="us_stock")
        clean_db.add(entry)
        clean_db.commit()

        from sqlalchemy import select
        result = clean_db.execute(
            select(WatchlistEntry).where(WatchlistEntry.symbol == "AAPL")
        ).scalar_one_or_none()
        assert result is not None
        assert result.display_name == "Apple Inc."
        assert result.category == "us_stock"

    def test_symbol_is_unique(self, clean_db):
        from modules.finance.watchlist.models import WatchlistEntry
        from sqlalchemy.exc import IntegrityError

        clean_db.add(WatchlistEntry(symbol="AAPL", display_name="Apple", category="us_stock"))
        clean_db.commit()
        clean_db.add(WatchlistEntry(symbol="AAPL", display_name="Duplicate", category="us_stock"))
        with pytest.raises(IntegrityError):
            clean_db.commit()
        clean_db.rollback()

    def test_delete_entry(self, clean_db):
        from modules.finance.watchlist.models import WatchlistEntry
        from sqlalchemy import select

        entry = WatchlistEntry(symbol="TSLA", display_name="Tesla", category="us_stock")
        clean_db.add(entry)
        clean_db.commit()

        clean_db.delete(entry)
        clean_db.commit()

        result = clean_db.execute(
            select(WatchlistEntry).where(WatchlistEntry.symbol == "TSLA")
        ).scalar_one_or_none()
        assert result is None

    def test_added_at_auto_set(self, clean_db):
        from modules.finance.watchlist.models import WatchlistEntry
        entry = WatchlistEntry(symbol="BTC-USD", display_name="Bitcoin", category="crypto")
        clean_db.add(entry)
        clean_db.commit()
        clean_db.refresh(entry)
        assert entry.added_at is not None
        assert isinstance(entry.added_at, datetime)


class TestPriceAlertDB:
    def test_add_alert(self, clean_db):
        from modules.finance.watchlist.models import WatchlistEntry, PriceAlert
        from sqlalchemy import select

        # Watchlist entry first
        clean_db.add(WatchlistEntry(symbol="ETH-USD", display_name="Ethereum", category="crypto"))
        clean_db.commit()

        alert = PriceAlert(symbol="ETH-USD", condition=">", threshold=5000.0)
        clean_db.add(alert)
        clean_db.commit()

        result = clean_db.execute(
            select(PriceAlert).where(PriceAlert.symbol == "ETH-USD")
        ).scalar_one_or_none()
        assert result is not None
        assert result.condition == ">"
        assert result.threshold == 5000.0
        assert result.triggered is False

    def test_mark_triggered(self, clean_db):
        from modules.finance.watchlist.models import PriceAlert
        from sqlalchemy import select

        alert = PriceAlert(symbol="BTC-USD", condition="<", threshold=30000.0)
        clean_db.add(alert)
        clean_db.commit()

        alert.triggered = True
        clean_db.commit()

        result = clean_db.execute(
            select(PriceAlert).where(PriceAlert.symbol == "BTC-USD")
        ).scalar_one()
        assert result.triggered is True
