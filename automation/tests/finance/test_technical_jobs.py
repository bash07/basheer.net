"""Tests for finance/technical/jobs.py and technical submodule registration."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from modules.finance.technical.jobs import (
    _classify_signal_type,
    _stable_description,
    scan_signals_job,
)
from modules.finance.technical.models import SignalLog
from modules.finance.watchlist.models import WatchlistEntry


class TestSignalClassificationAndDedup:
    def test_classify_crossover(self):
        assert _classify_signal_type("🟢 Bullish crossover: EMA21 above EMA50 (21/50)") == "ema_crossover"
        assert _classify_signal_type("🔴 Bearish crossover: EMA50 below EMA200 (50/200)") == "ema_crossover"

    def test_classify_divergence(self):
        assert _classify_signal_type("🟢 RSI Bullish Divergence (price lower low, RSI higher low)") == "rsi_divergence"
        assert _classify_signal_type("🔴 RSI Bearish Divergence (price higher high, RSI lower high)") == "rsi_divergence"

    def test_classify_rsi_extreme(self):
        assert _classify_signal_type("RSI OVERBOUGHT (74.3)") == "rsi_extreme"
        assert _classify_signal_type("RSI OVERSOLD (25.1)") == "rsi_extreme"

    def test_classify_volume_spike(self):
        assert _classify_signal_type("Volume spike (2.3× avg)") == "volume_spike"

    def test_classify_other(self):
        assert _classify_signal_type("Unknown technical setup") == "other"

    def test_stable_description_rsi(self):
        assert _stable_description("RSI OVERSOLD (24.3)", "rsi_extreme") == "RSI OVERSOLD"
        assert _stable_description("RSI OVERBOUGHT (78.9)", "rsi_extreme") == "RSI OVERBOUGHT"

    def test_stable_description_volume(self):
        assert _stable_description("Volume spike (3.2× avg)", "volume_spike") == "Volume spike"

    def test_stable_description_passthrough(self):
        desc = "🟢 Bullish crossover: EMA21 above EMA50 (21/50)"
        assert _stable_description(desc, "ema_crossover") == desc[:60]


class TestScanSignalsJob:
    async def test_skips_when_no_open_markets(self, clean_db):
        clean_db.add(WatchlistEntry(symbol="AAPL", display_name="Apple", category="us_stock"))
        clean_db.commit()

        with patch("modules.finance._shared.market_hours.is_market_open", return_value=False):
            with patch("modules.finance.technical.jobs._batch_fetch") as mock_fetch:
                await scan_signals_job()
                mock_fetch.assert_not_called()

    async def test_processes_open_markets_and_deduplicates(self, clean_db, sample_ohlcv):
        clean_db.add(WatchlistEntry(symbol="BTC-USD", display_name="Bitcoin", category="crypto"))
        clean_db.commit()

        from modules.finance.technical.indicators import compute_ta
        ta_res = compute_ta(sample_ohlcv, symbol="BTC-USD", timeframe="1d")
        ta_res.signals = [
            "🟢 Bullish crossover: EMA21 above EMA50 (21/50)",
            "RSI OVERBOUGHT (75.0)",
        ]

        with patch("modules.finance._shared.market_hours.is_market_open", return_value=True):
            with patch("modules.finance.technical.jobs._batch_fetch", return_value={"BTC-USD": sample_ohlcv}):
                with patch("modules.finance.technical.indicators.compute_ta", return_value=ta_res):
                    with patch("modules.finance._shared.notify.notify", new=AsyncMock()) as mock_notify:
                        await scan_signals_job()

                        assert mock_notify.call_count == 2
                        rows = clean_db.query(SignalLog).all()
                        assert len(rows) == 2
                        symbols = [r.symbol for r in rows]
                        assert symbols == ["BTC-USD", "BTC-USD"]

        # Run a second time within dedup window with fluctuating numbers:
        # e.g. RSI is now 77.8 instead of 75.0, Volume spike is now 3.4x instead of 2.5x
        ta_res_fluctuating = compute_ta(sample_ohlcv, symbol="BTC-USD", timeframe="1d")
        ta_res_fluctuating.signals = [
            "🟢 Bullish crossover: EMA21 above EMA50 (21/50)",
            "RSI OVERBOUGHT (77.8)",
        ]

        with patch("modules.finance._shared.market_hours.is_market_open", return_value=True):
            with patch("modules.finance.technical.jobs._batch_fetch", return_value={"BTC-USD": sample_ohlcv}):
                with patch("modules.finance.technical.indicators.compute_ta", return_value=ta_res_fluctuating):
                    with patch("modules.finance._shared.notify.notify", new=AsyncMock()) as mock_notify:
                        await scan_signals_job()
                        mock_notify.assert_not_called()
                        rows = clean_db.query(SignalLog).all()
                        assert len(rows) == 2



class TestSubmoduleRegistration:
    def test_register_technical_submodule(self):
        from modules.finance.technical import register

        bot_app = MagicMock()
        scheduler = MagicMock()
        authorized = MagicMock()

        meta = register(bot_app, scheduler, authorized)

        assert meta["name"] == "technical"
        assert "ta" in meta["commands"]
        assert "rsi" in meta["commands"]
        assert "chart" in meta["commands"]
        assert "signals" in meta["commands"]

        # 4 commands registered to bot_app
        assert bot_app.add_handler.call_count == 4
        # 1 job registered to scheduler
        assert scheduler.add_job.call_count == 1
