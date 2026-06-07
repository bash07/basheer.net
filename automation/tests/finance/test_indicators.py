"""Tests for finance/technical/indicators.py.

Uses the synthetic OHLCV fixtures from conftest.py — no network calls.
All assertions validate the pure computation logic only.
"""
import pytest
from modules.finance.technical.indicators import (
    compute_ta, format_ta_summary, TAResult, EMASet,
    _rsi_class, _trend, _vol_class,
)


class TestComputeTA:
    def test_returns_ta_result(self, sample_ohlcv):
        result = compute_ta(sample_ohlcv, symbol="TEST", timeframe="1d")
        assert isinstance(result, TAResult)

    def test_symbol_and_timeframe_preserved(self, sample_ohlcv):
        r = compute_ta(sample_ohlcv, symbol="AAPL", timeframe="1wk")
        assert r.symbol == "AAPL"
        assert r.timeframe == "1wk"

    def test_current_price_is_last_close(self, sample_ohlcv):
        r = compute_ta(sample_ohlcv, symbol="X", timeframe="1d")
        assert r.current_price == pytest.approx(float(sample_ohlcv["Close"].iloc[-1]))

    def test_rsi_in_valid_range(self, sample_ohlcv):
        r = compute_ta(sample_ohlcv, symbol="X", timeframe="1d")
        assert r.rsi is not None
        assert 0.0 <= r.rsi <= 100.0

    def test_all_emas_populated(self, sample_ohlcv):
        r = compute_ta(sample_ohlcv, symbol="X", timeframe="1d")
        assert r.emas.ema21  is not None
        assert r.emas.ema50  is not None
        assert r.emas.ema100 is not None
        assert r.emas.ema200 is not None

    def test_ema_ordering_in_trend(self, sample_ohlcv):
        """EMA200 should be smoother (less extreme) than EMA21 on a trending series."""
        r = compute_ta(sample_ohlcv, symbol="X", timeframe="1d")
        # EMA21 is more reactive — in any trend it should differ from EMA200
        assert r.emas.ema21 != r.emas.ema200

    def test_volume_ratio_positive(self, sample_ohlcv):
        r = compute_ta(sample_ohlcv, symbol="X", timeframe="1d")
        assert r.volume_ratio is not None
        assert r.volume_ratio > 0

    def test_ohlcv_json_serialised(self, sample_ohlcv):
        r = compute_ta(sample_ohlcv, symbol="X", timeframe="1d")
        import json
        data = json.loads(r.ohlcv_json)
        assert "columns" in data
        assert "Close" in data["columns"]

    def test_ema_series_all_present(self, sample_ohlcv):
        r = compute_ta(sample_ohlcv, symbol="X", timeframe="1d")
        assert set(r.ema_series.keys()) == {"ema21", "ema50", "ema100", "ema200"}
        assert len(r.ema_series["ema200"]) == len(sample_ohlcv)

    def test_rsi_series_length_matches(self, sample_ohlcv):
        r = compute_ta(sample_ohlcv, symbol="X", timeframe="1d")
        assert len(r.rsi_series) == len(sample_ohlcv)

    def test_signals_is_list(self, sample_ohlcv):
        r = compute_ta(sample_ohlcv, symbol="X", timeframe="1d")
        assert isinstance(r.signals, list)

    def test_crossovers_is_list(self, sample_ohlcv):
        r = compute_ta(sample_ohlcv, symbol="X", timeframe="1d")
        assert isinstance(r.crossovers, list)


class TestRSIClassification:
    def test_overbought(self):
        assert _rsi_class(75.0) == "overbought"

    def test_oversold(self):
        assert _rsi_class(25.0) == "oversold"

    def test_neutral(self):
        assert _rsi_class(50.0) == "neutral"

    def test_boundary_70(self):
        assert _rsi_class(70.1) == "overbought"
        assert _rsi_class(69.9) == "neutral"

    def test_boundary_30(self):
        assert _rsi_class(29.9) == "oversold"
        assert _rsi_class(30.1) == "neutral"

    def test_none(self):
        assert _rsi_class(None) == "neutral"


class TestTrendClassification:
    def _emas(self, e21, e50, e100, e200):
        return EMASet(ema21=e21, ema50=e50, ema100=e100, ema200=e200)

    def test_bullish(self):
        # price > ema21 > ema50 > ema200
        assert _trend(110, self._emas(105, 100, 95, 90)) == "bullish"

    def test_bearish(self):
        # price < ema21 < ema50 < ema200
        assert _trend(80, self._emas(85, 90, 95, 100)) == "bearish"

    def test_mixed(self):
        assert _trend(100, self._emas(98, 102, 95, 90)) == "mixed"


class TestVolumeClassification:
    def test_spike(self):
        assert _vol_class(2.5) == "spike"

    def test_elevated(self):
        assert _vol_class(1.6) == "elevated"

    def test_normal(self):
        assert _vol_class(1.0) == "normal"

    def test_low(self):
        assert _vol_class(0.3) == "low"

    def test_none(self):
        assert _vol_class(None) == "normal"


class TestFormatTASummary:
    def test_contains_symbol(self, sample_ohlcv):
        r = compute_ta(sample_ohlcv, symbol="GOLD", timeframe="1d")
        text = format_ta_summary(r)
        assert "GOLD" in text

    def test_contains_rsi(self, sample_ohlcv):
        r = compute_ta(sample_ohlcv, symbol="X", timeframe="1d")
        text = format_ta_summary(r)
        assert "RSI" in text

    def test_contains_trend(self, sample_ohlcv):
        r = compute_ta(sample_ohlcv, symbol="X", timeframe="1d")
        text = format_ta_summary(r)
        assert "Trend" in text

    def test_no_signals_shows_none(self, sample_ohlcv):
        r = compute_ta(sample_ohlcv, symbol="X", timeframe="1d")
        r.signals = []  # force empty
        text = format_ta_summary(r)
        assert "None detected" in text
