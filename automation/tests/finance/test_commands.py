"""Tests for Telegram command handlers in the finance domain.

Key patterns demonstrated here:
  1. No-args guard   — command called without arguments → usage message
  2. External mock   — yfinance / chart renderer patched so tests are fast + offline
  3. DB + command    — clean_db fixture wires the DB; commands read/write normally
  4. Reply assertion — check what the bot actually sent (text content, photo sent)
  5. Two-step reply  — reply_text → msg.edit_text / msg.delete pattern

All handlers are async; pytest-asyncio (asyncio_mode=auto in pytest.ini) handles this.
"""
import pytest
from unittest.mock import patch, AsyncMock


# ═══════════════════════════════════════════════════════════════════════════════
# Pattern 1 — No-args guard
# ═══════════════════════════════════════════════════════════════════════════════

class TestWatchCommandNoArgs:
    """When called with no arguments, every command must reply with a usage string."""

    async def test_watch_no_args(self, make_update):
        update, context = make_update(args=[])
        from modules.finance.watchlist.commands import watch_command
        await watch_command(update, context)

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "Usage" in text

    async def test_unwatch_no_args(self, make_update):
        update, context = make_update(args=[])
        from modules.finance.watchlist.commands import unwatch_command
        await unwatch_command(update, context)

        update.message.reply_text.assert_called_once()
        assert "Usage" in update.message.reply_text.call_args[0][0]

    async def test_alert_no_args(self, make_update):
        update, context = make_update(args=[])
        from modules.finance.watchlist.commands import alert_command
        await alert_command(update, context)

        update.message.reply_text.assert_called_once()
        assert "Usage" in update.message.reply_text.call_args[0][0]

    async def test_delalert_no_args(self, make_update):
        update, context = make_update(args=[])
        from modules.finance.watchlist.commands import delalert_command
        await delalert_command(update, context)

        update.message.reply_text.assert_called_once()
        assert "Usage" in update.message.reply_text.call_args[0][0]

    async def test_ta_no_args(self, make_update):
        update, context = make_update(args=[])
        from modules.finance.technical.commands import ta_command
        await ta_command(update, context)

        update.message.reply_text.assert_called_once()
        assert "Usage" in update.message.reply_text.call_args[0][0]

    async def test_rsi_no_args(self, make_update):
        update, context = make_update(args=[])
        from modules.finance.technical.commands import rsi_command
        await rsi_command(update, context)

        update.message.reply_text.assert_called_once()
        assert "Usage" in update.message.reply_text.call_args[0][0]


# ═══════════════════════════════════════════════════════════════════════════════
# Pattern 2 — External dependency mocking (yfinance)
# ═══════════════════════════════════════════════════════════════════════════════

class TestWatchCommandWithMockedYFinance:
    """
    /watch calls _fetch_ticker_info() inside run_in_executor.
    We patch the function at the module level — the executor receives the mock
    and returns its return_value synchronously in the test event loop.
    """

    async def test_watch_valid_ticker_adds_to_db(self, make_update, clean_db):
        update, context = make_update(args=["AAPL"])

        with patch(
            "modules.finance.watchlist.commands._fetch_ticker_info",
            return_value={"display_name": "Apple Inc.", "price": 185.50},
        ):
            from modules.finance.watchlist.commands import watch_command
            await watch_command(update, context)

        # The "Validating…" placeholder message should have been edited to success
        sent_msg = update.message.reply_text.return_value
        sent_msg.edit_text.assert_called_once()
        final_text = sent_msg.edit_text.call_args[0][0]
        assert "✅" in final_text
        assert "AAPL" in final_text

    async def test_watch_invalid_ticker_shows_error(self, make_update, clean_db):
        update, context = make_update(args=["FAKEXYZ"])

        with patch(
            "modules.finance.watchlist.commands._fetch_ticker_info",
            side_effect=ValueError("'FAKEXYZ' not found or has no price data."),
        ):
            from modules.finance.watchlist.commands import watch_command
            await watch_command(update, context)

        sent_msg = update.message.reply_text.return_value
        sent_msg.edit_text.assert_called_once()
        error_text = sent_msg.edit_text.call_args[0][0]
        assert "❌" in error_text

    async def test_watch_duplicate_ticker_warns(self, make_update, clean_db):
        """Second /watch for the same ticker should warn, not crash or duplicate."""
        from modules.finance.watchlist.models import WatchlistEntry
        clean_db.add(WatchlistEntry(symbol="AAPL", display_name="Apple Inc.", category="us_stock"))
        clean_db.commit()

        update, context = make_update(args=["AAPL"])
        with patch(
            "modules.finance.watchlist.commands._fetch_ticker_info",
            return_value={"display_name": "Apple Inc.", "price": 185.0},
        ):
            from modules.finance.watchlist.commands import watch_command
            await watch_command(update, context)

        sent_msg = update.message.reply_text.return_value
        text = sent_msg.edit_text.call_args[0][0]
        assert "already" in text.lower()

    async def test_watch_invalid_category_override(self, make_update):
        update, context = make_update(args=["AAPL", "--cat", "invalid_cat"])
        from modules.finance.watchlist.commands import watch_command
        await watch_command(update, context)

        update.message.reply_text.assert_called_once()
        assert "Unknown category" in update.message.reply_text.call_args[0][0]


# ═══════════════════════════════════════════════════════════════════════════════
# Pattern 3 — DB + command integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestUnwatchCommand:
    """
    /unwatch reads the DB. We pre-populate the DB via clean_db and verify
    that the handler deletes the entry and replies correctly.
    """

    async def test_unwatch_existing_ticker(self, make_update, clean_db):
        from modules.finance.watchlist.models import WatchlistEntry
        clean_db.add(WatchlistEntry(symbol="BTC-USD", display_name="Bitcoin", category="crypto"))
        clean_db.commit()

        update, context = make_update(args=["BTC-USD"])
        from modules.finance.watchlist.commands import unwatch_command
        await unwatch_command(update, context)

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "BTC-USD" in text
        assert "🗑" in text

    async def test_unwatch_nonexistent_ticker(self, make_update, clean_db):
        update, context = make_update(args=["NOTHERE"])
        from modules.finance.watchlist.commands import unwatch_command
        await unwatch_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "not in your watchlist" in text

    async def test_unwatch_also_removes_alerts(self, make_update, clean_db):
        """Unwatching should cascade-delete associated price alerts."""
        from sqlalchemy import select
        from modules.finance.watchlist.models import WatchlistEntry, PriceAlert

        clean_db.add(WatchlistEntry(symbol="ETH-USD", display_name="Ethereum", category="crypto"))
        clean_db.commit()
        clean_db.add(PriceAlert(symbol="ETH-USD", condition=">", threshold=5000.0))
        clean_db.add(PriceAlert(symbol="ETH-USD", condition="<", threshold=2000.0))
        clean_db.commit()

        update, context = make_update(args=["ETH-USD"])
        from modules.finance.watchlist.commands import unwatch_command
        await unwatch_command(update, context)

        # Alerts should be gone
        remaining = clean_db.execute(
            select(PriceAlert).where(PriceAlert.symbol == "ETH-USD")
        ).scalars().all()
        assert len(remaining) == 0

        # Reply should mention the alert count
        text = update.message.reply_text.call_args[0][0]
        assert "2 alert" in text


class TestWatchlistCommand:
    async def test_empty_watchlist(self, make_update, clean_db):
        update, context = make_update(args=[])
        from modules.finance.watchlist.commands import watchlist_command
        await watchlist_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "empty" in text.lower()

    async def test_shows_all_tickers(self, make_update, clean_db):
        from modules.finance.watchlist.models import WatchlistEntry
        clean_db.add_all([
            WatchlistEntry(symbol="AAPL",    display_name="Apple",   category="us_stock"),
            WatchlistEntry(symbol="BTC-USD", display_name="Bitcoin", category="crypto"),
        ])
        clean_db.commit()

        update, context = make_update(args=[])
        from modules.finance.watchlist.commands import watchlist_command
        await watchlist_command(update, context)

        text = update.message.reply_text.call_args[0][0]
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
        from modules.finance.watchlist.commands import watchlist_command
        await watchlist_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "BTC-USD" in text
        assert "AAPL" not in text


class TestAlertCommand:
    async def test_alert_condition_parsing(self, make_update, clean_db):
        from modules.finance.watchlist.models import WatchlistEntry
        clean_db.add(WatchlistEntry(symbol="BTC-USD", display_name="Bitcoin", category="crypto"))
        clean_db.commit()

        update, context = make_update(args=["BTC-USD", ">", "70000"])
        from modules.finance.watchlist.commands import alert_command
        await alert_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "🔔" in text
        assert "70,000" in text

    async def test_alert_requires_watchlist_entry(self, make_update, clean_db):
        update, context = make_update(args=["AAPL", ">", "200"])
        from modules.finance.watchlist.commands import alert_command
        await alert_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "not in your watchlist" in text

    async def test_alert_invalid_condition(self, make_update, clean_db):
        from modules.finance.watchlist.models import WatchlistEntry
        clean_db.add(WatchlistEntry(symbol="AAPL", display_name="Apple", category="us_stock"))
        clean_db.commit()

        update, context = make_update(args=["AAPL", "==", "200"])
        from modules.finance.watchlist.commands import alert_command
        await alert_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "❌" in text


# ═══════════════════════════════════════════════════════════════════════════════
# Pattern 4 — Mocking the chart + TA pipeline (/ta, /rsi, /chart)
# ═══════════════════════════════════════════════════════════════════════════════

class TestTACommands:
    """
    /ta, /rsi, /chart all call into charts.fetch_and_render or fetch_ta_only.
    We patch those at the module boundary so no yfinance or subprocess is needed.
    """

    def _make_ta_result(self, sample_ohlcv):
        """Build a real TAResult from the synthetic fixture (no network)."""
        from modules.finance.technical.indicators import compute_ta
        return compute_ta(sample_ohlcv, symbol="TEST", timeframe="1d")

    async def test_rsi_command_replies_with_rsi_value(self, make_update, sample_ohlcv):
        result = self._make_ta_result(sample_ohlcv)
        update, context = make_update(args=["TEST"])

        with patch(
            "modules.finance.technical.charts.fetch_ta_only",
            new=AsyncMock(return_value=result),
        ):
            from modules.finance.technical.commands import rsi_command
            await rsi_command(update, context)

        sent_msg = update.message.reply_text.return_value
        sent_msg.edit_text.assert_called_once()
        text = sent_msg.edit_text.call_args[0][0]
        assert "TEST" in text
        assert "RSI" in text

    async def test_ta_command_sends_photo(self, make_update, sample_ohlcv, tmp_path):
        result = self._make_ta_result(sample_ohlcv)
        # Create a real (tiny) PNG so open() succeeds
        fake_png = str(tmp_path / "chart.png")
        with open(fake_png, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)  # minimal PNG header

        update, context = make_update(args=["TEST"])

        with patch(
            "modules.finance.technical.charts.fetch_and_render",
            new=AsyncMock(return_value=(fake_png, result)),
        ):
            from modules.finance.technical.commands import ta_command
            await ta_command(update, context)

        update.message.reply_photo.assert_called_once()
        caption = update.message.reply_photo.call_args[1]["caption"]
        assert "TEST" in caption

    async def test_ta_command_handles_bad_ticker(self, make_update):
        update, context = make_update(args=["BADTICKER"])

        with patch(
            "modules.finance.technical.charts.fetch_and_render",
            new=AsyncMock(side_effect=ValueError("No data returned for 'BADTICKER'.")),
        ):
            from modules.finance.technical.commands import ta_command
            await ta_command(update, context)

        sent_msg = update.message.reply_text.return_value
        sent_msg.edit_text.assert_called_once()
        assert "❌" in sent_msg.edit_text.call_args[0][0]
