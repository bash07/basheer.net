"""Tests for finance/technical/indicators.py.

Uses the synthetic OHLCV fixtures from conftest.py — no network calls.
All assertions validate the pure computation logic only.
"""
import pytest
from modules.finance.technical.indicators import (
    compute_ta, format_ta_summary, TAResult, EMASet,
    _rsi_class, _trend, _vol_class, _crossovers, _rsi_divergence,
    _potential_divergence, _ema_convergences, _rsi_cross_signals,
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

    def test_with_signals(self, sample_ohlcv):
        r = compute_ta(sample_ohlcv, symbol="AAPL", timeframe="1d")
        r.signals = ["🟢 Bullish crossover: EMA21 above EMA50", "Volume spike (2.5× avg)"]
        r.rsi_divergence = "bullish"
        text = format_ta_summary(r)
        assert "Bullish crossover" in text
        assert "Bullish Divergence" in text


class TestCrossovers:
    def test_bullish_crossover(self):
        import pandas as pd
        # EMA21 was below EMA50, now above
        e21 = pd.Series([10.0, 10.0, 10.0, 12.0])
        e50 = pd.Series([11.0, 11.0, 11.0, 11.0])
        e200 = pd.Series([15.0, 15.0, 15.0, 15.0])
        res = _crossovers(e21, e50, e200)
        assert len(res) == 1
        assert "Bullish crossover: EMA21 above EMA50" in res[0]

    def test_bearish_crossover(self):
        import pandas as pd
        # EMA50 was above EMA200, now below
        e21 = pd.Series([15.0, 15.0, 15.0, 15.0])
        e50 = pd.Series([20.0, 20.0, 20.0, 18.0])
        e200 = pd.Series([19.0, 19.0, 19.0, 19.0])
        res = _crossovers(e21, e50, e200)
        assert any("Bearish crossover: EMA50 below EMA200" in s for s in res)

    def test_no_crossover(self):
        import pandas as pd
        e21 = pd.Series([10.0, 10.0, 10.0, 10.0])
        e50 = pd.Series([12.0, 12.0, 12.0, 12.0])
        e200 = pd.Series([15.0, 15.0, 15.0, 15.0])
        res = _crossovers(e21, e50, e200)
        assert len(res) == 0


class TestRSIDivergence:
    def test_bullish_divergence(self):
        import pandas as pd
        import numpy as np
        # Price: lower low at trough 2; RSI: higher low at trough 2
        price = np.array([10.0] * 30, dtype=float)
        price[10] = 5.0
        price[7:10] = [8, 7, 6]
        price[11:14] = [6, 7, 8]
        price[20] = 3.0  # lower low
        price[17:20] = [8, 7, 5]
        price[21:24] = [5, 7, 8]

        rsi = np.array([50.0] * 30, dtype=float)
        rsi[10] = 20.0
        rsi[7:10] = [35, 30, 25]
        rsi[11:14] = [25, 30, 35]
        rsi[20] = 28.0  # higher low
        rsi[17:20] = [35, 30, 29]
        rsi[21:24] = [29, 30, 35]

        res = _rsi_divergence(pd.Series(price), pd.Series(rsi))
        assert res == "bullish"

    def test_bearish_divergence(self):
        import pandas as pd
        import numpy as np
        # Price: higher high at peak 2; RSI: lower high at peak 2
        price = np.array([10.0] * 30, dtype=float)
        price[10] = 20.0
        price[7:10] = [12, 15, 18]
        price[11:14] = [18, 15, 12]
        price[20] = 25.0  # higher high
        price[17:20] = [12, 15, 22]
        price[21:24] = [22, 15, 12]

        rsi = np.array([50.0] * 30, dtype=float)
        rsi[10] = 80.0
        rsi[7:10] = [55, 65, 75]
        rsi[11:14] = [75, 65, 55]
        rsi[20] = 72.0  # lower high
        rsi[17:20] = [55, 65, 70]
        rsi[21:24] = [70, 65, 55]

        res = _rsi_divergence(pd.Series(price), pd.Series(rsi))
        assert res == "bearish"

    def test_no_divergence_when_short_series(self):
        import pandas as pd
        price = pd.Series([10.0, 11.0, 12.0])
        rsi = pd.Series([50.0, 52.0, 55.0])
        assert _rsi_divergence(price, rsi) == "none"


class TestPotentialSetups:
    def test_potential_bull_divergence_curling_up(self):
        import pandas as pd
        import numpy as np

        # Trough at index 10: price = 100.0, RSI = 25.0
        price = np.array([120.0] * 30, dtype=float)
        price[10] = 100.0
        price[7:10] = [110, 105, 102]
        price[11:14] = [102, 105, 110]
        # Current price testing low at 101.0 (within 2.5% of prior trough 100.0)
        price[-1] = 101.0
        price[-2] = 102.0

        # RSI: prior trough 25.0; current RSI curling up from 28.0 to 32.0 (> 25.0)
        rsi = np.array([50.0] * 30, dtype=float)
        rsi[10] = 25.0
        rsi[7:10] = [40, 35, 30]
        rsi[11:14] = [30, 35, 40]
        rsi[-2] = 28.0
        rsi[-1] = 32.0  # curling up

        res = _potential_divergence(pd.Series(price), pd.Series(rsi), current_price=101.0)
        assert res is not None
        assert "Developing Bull Divergence" in res
        assert "curling up" in res

    def test_potential_bear_divergence_curling_down(self):
        import pandas as pd
        import numpy as np

        # Peak at index 10: price = 150.0, RSI = 75.0
        price = np.array([120.0] * 30, dtype=float)
        price[10] = 150.0
        price[7:10] = [130, 140, 145]
        price[11:14] = [145, 140, 130]
        # Current price testing peak at 149.0 (within 2.5% of prior peak 150.0)
        price[-1] = 149.0
        price[-2] = 148.0

        # RSI: prior peak 75.0; current RSI curling down from 68.0 to 64.0 (< 75.0)
        rsi = np.array([50.0] * 30, dtype=float)
        rsi[10] = 75.0
        rsi[7:10] = [60, 65, 70]
        rsi[11:14] = [70, 65, 60]
        rsi[-2] = 68.0
        rsi[-1] = 64.0  # curling down

        res = _potential_divergence(pd.Series(price), pd.Series(rsi), current_price=149.0)
        assert res is not None
        assert "Developing Bear Divergence" in res
        assert "curling down" in res

    def test_ema_convergence_bullish_watch(self):
        import pandas as pd
        # Current price 100. EMA21 at 99.5, EMA50 at 100.0 (spread 0.5%, contracting)
        price = 100.0
        ema21 = pd.Series([98.0, 98.8, 99.5])
        ema50 = pd.Series([100.0, 100.0, 100.0])
        ema200 = pd.Series([110.0, 110.0, 110.0])

        res = _ema_convergences(price, ema21, ema50, ema200)
        assert len(res) >= 1
        assert any("Bullish Cross watch" in s for s in res)
        assert any("Converging: EMA21" in s for s in res)

    def test_ema_convergence_bearish_watch(self):
        import pandas as pd
        # Current price 100. EMA21 at 100.5, EMA50 at 100.0 (spread 0.5%, contracting)
        price = 100.0
        ema21 = pd.Series([102.0, 101.2, 100.5])
        ema50 = pd.Series([100.0, 100.0, 100.0])
        ema200 = pd.Series([90.0, 90.0, 90.0])

        res = _ema_convergences(price, ema21, ema50, ema200)
        assert len(res) >= 1
        assert any("Bearish Cross watch" in s for s in res)

    def test_format_ta_summary_with_potential_setups(self, sample_ohlcv):
        r = compute_ta(sample_ohlcv, symbol="AAPL", timeframe="1d")
        r.potential_signals = [
            "⚠️ Developing Bull Divergence: Price testing 180.00 with RSI curling up",
            "⏳ Converging: EMA21 is 0.4% below EMA50 (21/50 Bullish Cross watch)",
        ]
        text = format_ta_summary(r)
        assert "🔮 *Potential Setups:*" in text
        assert "Developing Bull Divergence" in text
        assert "Converging: EMA21" in text


class TestRSICrossSignals:
    """Solution A: Only emit alerts on boundary crossings, not on static lingering."""

    def test_cross_into_oversold_emits_signal(self):
        import pandas as pd
        s = pd.Series([35.0, 28.5])
        sigs = _rsi_cross_signals(s)
        assert len(sigs) == 1
        assert "RSI Entered OVERSOLD" in sigs[0]

    def test_lingering_in_oversold_emits_no_signal(self):
        import pandas as pd
        # Already oversold yesterday at 28.0, today at 25.0 -> NO new signal
        s = pd.Series([28.0, 25.0])
        sigs = _rsi_cross_signals(s)
        assert len(sigs) == 0

    def test_deep_oversold_milestone_below_20(self):
        import pandas as pd
        s = pd.Series([22.0, 18.5])
        sigs = _rsi_cross_signals(s)
        assert len(sigs) == 1
        assert "Deep OVERSOLD Below 20" in sigs[0]

    def test_recovery_from_oversold_emits_signal(self):
        import pandas as pd
        s = pd.Series([27.0, 31.5])
        sigs = _rsi_cross_signals(s)
        assert len(sigs) == 1
        assert "Recovered Above 30" in sigs[0]

    def test_cross_into_overbought_emits_signal(self):
        import pandas as pd
        s = pd.Series([67.0, 72.5])
        sigs = _rsi_cross_signals(s)
        assert len(sigs) == 1
        assert "RSI Entered OVERBOUGHT" in sigs[0]

    def test_lingering_in_overbought_emits_no_signal(self):
        import pandas as pd
        s = pd.Series([72.0, 75.0])
        sigs = _rsi_cross_signals(s)
        assert len(sigs) == 0

    def test_pullback_from_overbought_emits_signal(self):
        import pandas as pd
        s = pd.Series([74.0, 68.0])
        sigs = _rsi_cross_signals(s)
        assert len(sigs) == 1
        assert "Pulled Back Below 70" in sigs[0]



