"""Tests for EMA Pullback + Hidden Divergence strategy and its edge-case handling.

Covers:
1. Edge Case 1: Choppy / Sideways markets (ADX < 20 & EMA slope/alignment filters).
2. Edge Case 2: Deep EMA Piercing & Candle Rejection quality.
3. Edge Case 3: Higher-Timeframe alignment & Hidden Divergence verification.
4. Edge Case 4: Swing anchoring lookback, distance, and freshness bounds.
5. Edge Case 5: Overextension / Parabolic blowout filter (> 2.5 ATR).
"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from modules.finance.trader.strategies.ema_pullback import EMAPullbackStrategy
from modules.finance.trader.strategies.base import StrategyContext
from modules.finance.trader.schemas import BiasResult
from modules.finance.trader.setups import _ema_pullback_triggered
from modules.finance.trader.bias import _hidden_divergence


def _build_test_df(n: int = 60, base_price: float = 100.0, trend: float = 0.5) -> pd.DataFrame:
    """Helper to build an OHLCV DataFrame with a specified trend slope."""
    dates = pd.bdate_range(end=datetime(2025, 1, 1), periods=n)
    close = base_price + np.arange(n) * trend
    high = close + 1.0
    low = close - 1.0
    open_p = close - 0.2
    volume = np.full(n, 100000.0)

    df = pd.DataFrame({
        "Open": open_p,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": volume,
    }, index=dates)
    df.index.name = "Date"
    return df


class TestTriggerEdgeCases:
    """Test _ema_pullback_triggered guards against deep pierces and bad candle shapes."""

    def test_bullish_clean_rejection_passes(self):
        df = _build_test_df(n=50, base_price=100.0, trend=0.2)
        close = df["Close"]
        ema21 = close.ewm(span=21, adjust=False).mean()
        e21_last = float(ema21.iloc[-1])

        # Last bar: low pierces EMA21, but close is above EMA21, closing in upper 50%
        df.iloc[-1, df.columns.get_loc("Low")] = e21_last - 0.3
        df.iloc[-1, df.columns.get_loc("Close")] = e21_last + 0.5
        df.iloc[-1, df.columns.get_loc("High")] = e21_last + 0.6

        triggered = _ema_pullback_triggered(df, "long", atr=1.0)
        assert triggered is True

    def test_bullish_deep_pierce_rejected(self):
        """Edge Case 2: Falling knife plunging > 1.5 ATR below EMA21 should be rejected."""
        df = _build_test_df(n=50, base_price=100.0, trend=0.2)
        close = df["Close"]
        ema21 = close.ewm(span=21, adjust=False).mean()
        e21_last = float(ema21.iloc[-1])

        # Low pierces 2.5 ATR below EMA21
        atr = 1.0
        df.iloc[-1, df.columns.get_loc("Low")] = e21_last - 2.5
        df.iloc[-1, df.columns.get_loc("Close")] = e21_last + 0.2
        df.iloc[-1, df.columns.get_loc("High")] = e21_last + 0.5

        triggered = _ema_pullback_triggered(df, "long", atr=atr)
        assert triggered is False

    def test_bullish_ema50_floor_break_rejected(self):
        """Edge Case 2: Candle close below EMA50 should be rejected."""
        df = _build_test_df(n=50, base_price=100.0, trend=0.2)
        close = df["Close"]
        ema21 = close.ewm(span=21, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        e21_last = float(ema21.iloc[-1])
        e50_last = float(ema50.iloc[-1])

        # Candle closes below EMA50
        df.iloc[-1, df.columns.get_loc("Low")] = e50_last - 0.5
        df.iloc[-1, df.columns.get_loc("Close")] = e50_last - 0.1
        df.iloc[-1, df.columns.get_loc("High")] = e21_last + 0.5

        triggered = _ema_pullback_triggered(df, "long", ema21=ema21, ema50=ema50, atr=1.0)
        assert triggered is False

    def test_bullish_poor_wick_rejected(self):
        """Edge Case 2: Close dumping into lower part of candle shows no rejection."""
        df = _build_test_df(n=50, base_price=100.0, trend=0.2)
        close = df["Close"]
        ema21 = close.ewm(span=21, adjust=False).mean()
        e21_last = float(ema21.iloc[-1])

        # High = 105, Low = 99 (EMA21 is 100), Close = 100.2 (closing at bottom of wide range)
        df.iloc[-1, df.columns.get_loc("High")] = e21_last + 5.0
        df.iloc[-1, df.columns.get_loc("Low")] = e21_last - 0.5
        df.iloc[-1, df.columns.get_loc("Close")] = e21_last + 0.1  # bottom ~10% of range

        triggered = _ema_pullback_triggered(df, "long", atr=2.0)
        assert triggered is False

    def test_bearish_clean_rejection_passes(self):
        df = _build_test_df(n=50, base_price=100.0, trend=-0.2)
        close = df["Close"]
        ema21 = close.ewm(span=21, adjust=False).mean()
        e21_last = float(ema21.iloc[-1])

        # Last bar: high touches EMA21, close rejects down
        df.iloc[-1, df.columns.get_loc("High")] = e21_last + 0.3
        df.iloc[-1, df.columns.get_loc("Close")] = e21_last - 0.5
        df.iloc[-1, df.columns.get_loc("Low")] = e21_last - 0.8

        triggered = _ema_pullback_triggered(df, "short", atr=1.0)
        assert triggered is True

    def test_bearish_deep_pierce_rejected(self):
        """Edge Case 2: Short setup where high explodes > 1.5 ATR above EMA21."""
        df = _build_test_df(n=50, base_price=100.0, trend=-0.2)
        close = df["Close"]
        ema21 = close.ewm(span=21, adjust=False).mean()
        e21_last = float(ema21.iloc[-1])

        atr = 1.0
        df.iloc[-1, df.columns.get_loc("High")] = e21_last + 2.5
        df.iloc[-1, df.columns.get_loc("Close")] = e21_last - 0.2
        df.iloc[-1, df.columns.get_loc("Low")] = e21_last - 0.5

        triggered = _ema_pullback_triggered(df, "short", atr=atr)
        assert triggered is False


class TestHiddenDivergenceAnchoring:
    """Test swing anchoring lookback, distance, and freshness (Edge Case 4)."""

    def test_swings_too_close_rejected(self):
        """Pivots separated by < 5 bars should not register as divergence."""
        n = 60
        close = pd.Series(np.linspace(100, 110, n))
        rsi = pd.Series(np.linspace(50, 60, n))

        # Artificial micro oscillations 2 bars apart
        res = _hidden_divergence(close, rsi)
        assert res == "none"

    def test_valid_fresh_bullish_hidden_div(self):
        """Trough 1 at bar -22, Trough 2 at bar -8 (HL price, LL RSI, dist=14, age=7)."""
        n = 60
        close = np.full(n, 100.0)
        rsi = np.full(n, 50.0)

        # Build baseline upward price
        for i in range(n):
            close[i] = 100.0 + (i * 0.1)
            rsi[i] = 50.0

        # Trough 1 at idx 35 (25 bars ago): price 101, rsi 38
        close[35] = 95.0
        rsi[35]   = 45.0
        for offset in [-2, -1, 1, 2]:
            close[35 + offset] = 98.0
            rsi[35 + offset]   = 48.0

        # Trough 2 at idx 50 (9 bars ago): price 97.0 (HL > 95), rsi 35.0 (LL < 45)
        close[50] = 97.0
        rsi[50]   = 35.0
        for offset in [-2, -1, 1, 2]:
            close[50 + offset] = 100.0
            rsi[50 + offset]   = 42.0

        res = _hidden_divergence(pd.Series(close), pd.Series(rsi))
        assert res == "bullish"


class TestEMAPullbackStrategyEdgeCases:
    """Test end-to-end strategy evaluation with all edge case filters."""

    def _setup_context(self, adx_val: float = 28.0, hidden_div: str = "bullish", bias_str: str = "strong_bull"):
        df_ltf = _build_test_df(n=50, base_price=100.0, trend=0.3)
        df_htf = _build_test_df(n=80, base_price=90.0, trend=0.3)

        close = df_ltf["Close"]
        ema8  = close.ewm(span=8, adjust=False).mean()
        ema21 = close.ewm(span=21, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()

        e21_last = float(ema21.iloc[-1])
        # Rejection wick on last bar
        df_ltf.iloc[-1, df_ltf.columns.get_loc("Low")] = e21_last - 0.2
        df_ltf.iloc[-1, df_ltf.columns.get_loc("Close")] = e21_last + 0.4
        df_ltf.iloc[-1, df_ltf.columns.get_loc("High")] = e21_last + 0.5
        current_price = float(df_ltf["Close"].iloc[-1])

        bias = BiasResult(
            mode="intraday", htf_tf="1d", ltf_tf="1h", vp_lookback=60,
            rsi_value=55.0, regime="bull", hidden_div=hidden_div,
            failure_swing="none", bias=bias_str, bias_notes="test",
        )

        indicators = {
            "bias": bias,
            "ema_8": ema8,
            "ema_21": ema21,
            "ema_50": ema50,
            "adx": adx_val,
            "atr_14": 1.0,
            "volume_profile": None,
            "sr_levels": [],
            "structure": None,
        }

        ctx = StrategyContext(
            ticker="TEST",
            category="crypto",
            mode="intraday",
            current_price=current_price,
            df_htf=df_htf,
            df_ltf=df_ltf,
            indicators=indicators,
        )
        return ctx

    def test_chop_filter_rejects_low_adx(self):
        """Edge Case 1: ADX < 20 must be rejected."""
        ctx = self._setup_context(adx_val=15.0)  # low ADX
        strat = EMAPullbackStrategy()
        setup = strat.evaluate(ctx)
        assert setup is None

    def test_ema_trend_alignment_rejects_inverted_emas(self):
        """Edge Case 1: Inverted EMAs (EMA21 < EMA50 in long) must be rejected."""
        ctx = self._setup_context(adx_val=30.0)
        # Force inverted EMA50 > EMA21
        ctx.indicators["ema_50"] = ctx.indicators["ema_21"] + 5.0
        strat = EMAPullbackStrategy()
        setup = strat.evaluate(ctx)
        assert setup is None

    def test_htf_missing_hidden_divergence_rejected(self):
        """Edge Case 3: When hidden_div is 'none' or conflicting, reject setup."""
        ctx = self._setup_context(adx_val=28.0, hidden_div="none", bias_str="moderate_bull")
        strat = EMAPullbackStrategy()
        setup = strat.evaluate(ctx)
        assert setup is None

    def test_overextended_price_rejected(self):
        """Edge Case 5: When price is > 2.5 ATR away from EMA21, reject setup."""
        ctx = self._setup_context(adx_val=28.0)
        ema21_val = float(ctx.ema_21.iloc[-1])
        ctx.current_price = ema21_val + 3.5  # 3.5 ATR away (ATR=1.0)
        strat = EMAPullbackStrategy()
        setup = strat.evaluate(ctx)
        assert setup is None

    def test_gate1_htf_extreme_premium_rejected(self):
        """Gate 1: Buying at extreme HTF premium (> 0.88 of range) is rejected."""
        ctx = self._setup_context(adx_val=28.0)
        # Set HTF range low=90, high=101, so current_price=100.5 is > 0.88
        ctx.df_htf["High"] = 101.0
        ctx.df_htf["Low"]  = 90.0
        ctx.current_price = 100.8  # (100.8 - 90) / 11 = 0.98 (> 0.88)
        strat = EMAPullbackStrategy()
        setup = strat.evaluate(ctx)
        assert setup is None

    def test_setup_triad_metadata_populated(self):
        """Gate 3: Successfully evaluated setup populates mtf_tf and trigger_tf."""
        from modules.finance.trader.confluence import SRLevel
        ctx = self._setup_context(adx_val=28.0)
        e21_val = float(ctx.ema_21.iloc[-1])
        ctx.indicators["sr_levels"] = [
            SRLevel(price=e21_val, label="Daily S/R", source="pivot", weight=4.0)
        ]
        strat = EMAPullbackStrategy()
        setup = strat.evaluate(ctx)
        assert setup is not None
        assert setup.mtf_tf == "15m"
        assert setup.trigger_tf == "5m"
        assert setup.htf_tf == "4h"
        assert setup.ltf_tf == "5m"


class TestTimeframeTriadAndStyleSelection:
    """Test TimeframeTriad, 3-tier StrategyContext, and style selection filtering."""

    def test_triad_resolution_for_all_styles(self):
        from modules.finance.trader.schemas import timeframe_triad_for

        swing = timeframe_triad_for("swing")
        assert swing.htf == "1wk"
        assert swing.mtf == "4h"
        assert swing.trigger == "1h"
        assert swing.ltf == "1h"

        intraday = timeframe_triad_for("intraday")
        assert intraday.htf == "4h"
        assert intraday.mtf == "15m"
        assert intraday.trigger == "5m"

        scalping = timeframe_triad_for("scalping")
        assert scalping.htf == "1h"
        assert scalping.mtf == "5m"
        assert scalping.trigger == "1m"

    def test_strategy_context_3_tier_attributes(self):
        df_htf = _build_test_df(n=50)
        df_mtf = _build_test_df(n=30)
        df_trigger = _build_test_df(n=20)

        ctx = StrategyContext(
            ticker="TEST",
            category="us_stock",
            mode="intraday",
            current_price=100.0,
            df_htf=df_htf,
            df_mtf=df_mtf,
            df_trigger=df_trigger,
        )

        assert ctx.df_htf is df_htf
        assert ctx.df_mtf is df_mtf
        assert ctx.df_trigger is df_trigger
        # Backward compatibility alias
        assert ctx.df_ltf is df_trigger

    def test_strategy_context_legacy_fallback(self):
        df_htf = _build_test_df(n=50)
        df_ltf = _build_test_df(n=20)

        ctx = StrategyContext(
            ticker="TEST",
            category="us_stock",
            mode="swing",
            current_price=100.0,
            df_htf=df_htf,
            df_ltf=df_ltf,
        )

        assert ctx.df_htf is df_htf
        assert ctx.df_ltf is df_ltf
        assert ctx.df_trigger is df_ltf
        assert ctx.df_mtf is df_ltf


class TestBacktestTriadReplay:
    """Test Phase 3 Backtest Engine Triad replay, lookahead masking, and lookback clamping."""

    def test_interval_to_timedelta(self):
        from modules.finance.trader.backtest import _interval_to_timedelta

        assert _interval_to_timedelta("1m") == timedelta(minutes=1)
        assert _interval_to_timedelta("5m") == timedelta(minutes=5)
        assert _interval_to_timedelta("15m") == timedelta(minutes=15)
        assert _interval_to_timedelta("1h") == timedelta(hours=1)
        assert _interval_to_timedelta("4h") == timedelta(hours=4)
        assert _interval_to_timedelta("1d") == timedelta(days=1)
        assert _interval_to_timedelta("1wk") == timedelta(weeks=1)

    def test_max_and_warmup_days_for_interval(self):
        from modules.finance.trader.backtest import (
            _max_days_for_interval,
            _warmup_days_for_interval,
            _YF_1M_MAX_DAYS,
            _YF_5M_MAX_DAYS,
            _YF_1H_MAX_DAYS,
        )

        assert _max_days_for_interval("1m") == _YF_1M_MAX_DAYS
        assert _max_days_for_interval("5m") == _YF_5M_MAX_DAYS
        assert _max_days_for_interval("15m") == _YF_5M_MAX_DAYS
        assert _max_days_for_interval("1h") == _YF_1H_MAX_DAYS
        assert _max_days_for_interval("4h") == _YF_1H_MAX_DAYS

        assert _warmup_days_for_interval("1m") == 2
        assert _warmup_days_for_interval("5m") == 5
        assert _warmup_days_for_interval("15m") == 5
        assert _warmup_days_for_interval("1h") == 60

    def test_zero_lookahead_candle_closure_masking(self):
        """Verify that higher timeframe candles are only included once fully closed."""
        from modules.finance.trader.backtest import _interval_to_timedelta

        # MTF candles (15m intervals): 10:00, 10:15, 10:30
        mtf_idx = pd.date_range("2026-03-02 10:00", periods=3, freq="15min")
        df_mtf = pd.DataFrame({"Close": [100.0, 101.0, 102.0]}, index=mtf_idx)

        # Trigger candles (5m intervals): 10:00, 10:05, 10:10, 10:15
        trig_idx = pd.date_range("2026-03-02 10:00", periods=4, freq="5min")
        df_trig = pd.DataFrame({"Close": [100.0, 100.2, 100.4, 101.0]}, index=trig_idx)

        trigger_delta = _interval_to_timedelta("5m")
        mtf_delta = _interval_to_timedelta("15m")

        # Case 1: At 10:05 bar (closes at 10:10)
        # 10:00 MTF bar closes at 10:15 > 10:10 -> MTF bar should NOT be closed yet!
        slice_1005 = df_trig.iloc[:2]  # bars 10:00 and 10:05
        close_ts_1005 = slice_1005.index[-1] + trigger_delta  # 10:05 + 5m = 10:10
        mtf_closed_1005 = df_mtf[df_mtf.index + mtf_delta <= close_ts_1005]
        assert len(mtf_closed_1005) == 0

        # Case 2: At 10:10 bar (closes at 10:15)
        # 10:00 MTF bar closes at 10:15 <= 10:15 -> MTF bar IS now closed and available!
        slice_1010 = df_trig.iloc[:3]  # bars 10:00, 10:05, 10:10
        close_ts_1010 = slice_1010.index[-1] + trigger_delta  # 10:10 + 5m = 10:15
        mtf_closed_1010 = df_mtf[df_mtf.index + mtf_delta <= close_ts_1010]
        assert len(mtf_closed_1010) == 1
        assert mtf_closed_1010.index[0] == pd.Timestamp("2026-03-02 10:00")

    def test_run_backtest_with_mocked_triad(self, monkeypatch):
        """Verify that run_backtest executes end-to-end with style selection and triad data."""
        from modules.finance.trader.backtest import run_backtest
        from datetime import date

        # Build synthetic 3-tier data
        dates_trig = pd.date_range("2026-03-01 00:00", periods=120, freq="5min")
        df_trigger = pd.DataFrame({
            "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5, "Volume": 1000
        }, index=dates_trig)

        dates_mtf = pd.date_range("2026-03-01 00:00", periods=60, freq="15min")
        df_mtf = pd.DataFrame({
            "Open": 100.0, "High": 101.5, "Low": 98.5, "Close": 100.5, "Volume": 3000
        }, index=dates_mtf)

        dates_htf = pd.date_range("2026-02-20 00:00", periods=40, freq="4h")
        df_htf = pd.DataFrame({
            "Open": 95.0, "High": 102.0, "Low": 94.0, "Close": 100.0, "Volume": 12000
        }, index=dates_htf)

        monkeypatch.setattr(
            "modules.finance.trader.backtest._download_full_triad_history",
            lambda ticker, triad, date_from, date_to, category=None: (df_trigger, df_mtf, df_htf)
        )

        report = run_backtest(
            ticker="BTC-USD",
            category="crypto",
            date_from=date(2026, 3, 1),
            date_to=date(2026, 3, 1),
            style="intraday",
            step=5,
        )

        assert report.ticker == "BTC-USD"
        assert report.mode == "intraday"
        assert report.total_bars > 0



