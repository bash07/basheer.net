"""Tests for /price and /prices commands and price helper utilities."""
import pytest
from unittest.mock import patch


# ── prices.py unit tests ──────────────────────────────────────────────────────

class TestFormatPriceCard:
    def _info(self, pct=1.5, **kw):
        base = {
            "symbol": "AAPL", "price": 185.0,
            "change": 1.5, "change_pct": pct,
            "day_high": 186.0, "day_low": 183.0,
            "week52_high": 200.0, "week52_low": 160.0,
        }
        base.update(kw)
        return base

    def test_positive_change_shows_up_arrow(self):
        from modules.finance.watchlist.prices import format_price_card
        text = format_price_card(self._info(pct=1.5))
        assert "📈" in text
        assert "+1.50%" in text

    def test_negative_change_shows_down_arrow(self):
        from modules.finance.watchlist.prices import format_price_card
        text = format_price_card(self._info(pct=-2.3))
        assert "📉" in text
        assert "-2.30%" in text

    def test_includes_day_range(self):
        from modules.finance.watchlist.prices import format_price_card
        text = format_price_card(self._info())
        assert "Day" in text

    def test_includes_52w_range(self):
        from modules.finance.watchlist.prices import format_price_card
        text = format_price_card(self._info())
        assert "52W" in text

    def test_omits_ranges_when_none(self):
        from modules.finance.watchlist.prices import format_price_card
        info = self._info()
        info["day_high"] = None
        info["week52_high"] = None
        text = format_price_card(info)
        assert "Day" not in text
        assert "52W" not in text


class TestFormatPriceRow:
    def test_with_data(self):
        from modules.finance.watchlist.prices import format_price_row
        info = {"symbol": "BTC-USD", "price": 67000.0, "change_pct": 2.1,
                "change": 1000.0, "day_high": None, "day_low": None,
                "week52_high": None, "week52_low": None}
        row = format_price_row("BTC-USD", info)
        assert "BTC-USD" in row
        assert "67,000" in row

    def test_none_shows_na(self):
        from modules.finance.watchlist.prices import format_price_row
        assert "N/A" in format_price_row("FAIL", None)


# ── /price command ────────────────────────────────────────────────────────────

class TestPriceCommand:
    def _sample_info(self, symbol="AAPL"):
        return {
            "symbol": symbol, "price": 185.0,
            "change": 2.0, "change_pct": 1.09,
            "day_high": 186.5, "day_low": 183.0,
            "week52_high": 200.0, "week52_low": 155.0,
        }

    async def test_no_args_returns_usage(self, make_update):
        update, context = make_update(args=[])
        from modules.finance.watchlist.commands import price_command
        await price_command(update, context)
        assert "Usage" in update.message.reply_text.call_args[0][0]

    async def test_valid_ticker_shows_price(self, make_update):
        update, context = make_update(args=["AAPL"])
        with patch(
            "modules.finance.watchlist.commands.fetch_price_detail",
            return_value=self._sample_info(),
        ):
            from modules.finance.watchlist.commands import price_command
            await price_command(update, context)

        msg = update.message.reply_text.return_value
        msg.edit_text.assert_called_once()
        text = msg.edit_text.call_args[0][0]
        assert "AAPL" in text
        assert "185" in text

    async def test_invalid_ticker_shows_error(self, make_update):
        update, context = make_update(args=["BADXYZ"])
        with patch(
            "modules.finance.watchlist.prices.fetch_price_detail",
            side_effect=ValueError("No price data for 'BADXYZ'."),
        ):
            # fetch_price_detail is imported into commands at module level,
            # so patch the source
            with patch(
                "modules.finance.watchlist.commands.fetch_price_detail",
                side_effect=ValueError("No price data for 'BADXYZ'."),
            ):
                from modules.finance.watchlist.commands import price_command
                await price_command(update, context)

        text = update.message.reply_text.return_value.edit_text.call_args[0][0]
        assert "❌" in text

    async def test_symbol_uppercased(self, make_update):
        update, context = make_update(args=["aapl"])
        captured = {}

        def _fake(sym):
            captured["sym"] = sym
            return self._sample_info(sym)

        with patch("modules.finance.watchlist.commands.fetch_price_detail", side_effect=_fake):
            from modules.finance.watchlist.commands import price_command
            await price_command(update, context)

        assert captured["sym"] == "AAPL"


# ── /prices command ───────────────────────────────────────────────────────────

class TestPricesCommand:
    def _fake_prices(self, symbols):
        return {
            sym: {
                "symbol": sym, "price": 100.0 + i,
                "change": 1.0, "change_pct": 1.0,
                "day_high": None, "day_low": None,
                "week52_high": None, "week52_low": None,
            }
            for i, sym in enumerate(symbols)
        }

    async def test_empty_watchlist_shows_message(self, make_update, clean_db):
        update, context = make_update(args=[])
        from modules.finance.watchlist.commands import prices_command
        await prices_command(update, context)
        assert "empty" in update.message.reply_text.call_args[0][0].lower()

    async def test_shows_all_tickers(self, make_update, clean_db):
        from modules.finance.watchlist.models import WatchlistEntry
        clean_db.add_all([
            WatchlistEntry(symbol="AAPL",    display_name="Apple",   category="us_stock"),
            WatchlistEntry(symbol="BTC-USD", display_name="Bitcoin", category="crypto"),
        ])
        clean_db.commit()

        update, context = make_update(args=[])
        with patch(
            "modules.finance.watchlist.commands.fetch_prices_batch",
            side_effect=self._fake_prices,
        ):
            from modules.finance.watchlist.commands import prices_command
            await prices_command(update, context)

        msg = update.message.reply_text.return_value
        text = msg.edit_text.call_args[0][0]
        assert "AAPL" in text
        assert "BTC-USD" in text

    async def test_category_filter(self, make_update, clean_db):
        from modules.finance.watchlist.models import WatchlistEntry
        clean_db.add_all([
            WatchlistEntry(symbol="AAPL",    display_name="Apple",   category="us_stock"),
            WatchlistEntry(symbol="BTC-USD", display_name="Bitcoin", category="crypto"),
        ])
        clean_db.commit()

        update, context = make_update(args=["crypto"])
        with patch(
            "modules.finance.watchlist.commands.fetch_prices_batch",
            side_effect=self._fake_prices,
        ):
            from modules.finance.watchlist.commands import prices_command
            await prices_command(update, context)

        text = update.message.reply_text.return_value.edit_text.call_args[0][0]
        assert "BTC-USD" in text
        assert "AAPL" not in text

    async def test_failed_fetch_shows_na(self, make_update, clean_db):
        from modules.finance.watchlist.models import WatchlistEntry
        clean_db.add(WatchlistEntry(symbol="FAIL", display_name="Fail", category="us_stock"))
        clean_db.commit()

        update, context = make_update(args=[])
        with patch(
            "modules.finance.watchlist.commands.fetch_prices_batch",
            return_value={"FAIL": None},
        ):
            from modules.finance.watchlist.commands import prices_command
            await prices_command(update, context)

        text = update.message.reply_text.return_value.edit_text.call_args[0][0]
        assert "N/A" in text
