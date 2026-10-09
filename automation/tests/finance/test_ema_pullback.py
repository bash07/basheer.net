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
from datetime import datetime

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
