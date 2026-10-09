"""Tests for Range Liquidity Sweep (Mean Reversion) strategy.

Verifies:
1. Regime gate: Allows neutral/low-ADX chop, rejects strong directional trends (ADX >= 25).
2. Boundary gate: Rejects compressed ranges (< 1.5x ATR).
3. Bearish sweep trigger: Fades false breakout above resistance (VAH) with upper wick rejection.
4. Bullish sweep trigger: Fades false breakout below support (VAL) with lower wick rejection.
5. Wick quality: Rejects non-sweep candles (small wick / full body).
6. Targets & R:R: Verifies TP1 (POC/midpoint) and TP2 (opposite boundary).
"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime

from app.modules.finance.trader.strategies.range_liquidity_sweep import RangeLiquiditySweepStrategy
from app.modules.finance.trader.strategies.base import StrategyContext
from app.modules.finance.trader.schemas import BiasResult, VolumeProfile, SRLevel, LiquidityLevel, SwingStructure


def _build_range_df(n: int = 50, center: float = 100.0, swing_amp: float = 2.0) -> pd.DataFrame:
    """Helper to build an oscillating/ranging OHLCV DataFrame."""
    dates = pd.bdate_range(end=datetime(2025, 1, 1), periods=n)
    # Oscillate around center
    t = np.linspace(0, 4 * np.pi, n)
    close = center + swing_amp * np.sin(t)
    high = close + 0.5
    low = close - 0.5
    open_p = close - 0.1
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


def _make_context(
    df_ltf: pd.DataFrame,
    adx: float = 18.0,
    bias_str: str = "neutral",
    vah: float = 103.0,
    val: float = 97.0,
    poc: float = 100.0,
    rsi_val: float = 50.0,
    atr: float = 1.0,
    liq_list: list = None,
) -> StrategyContext:
    vp = VolumeProfile(
        poc=poc,
        vah=vah,
        val=val,
        hvn=[poc],
        lvn=[],
        lookback=len(df_ltf),
    )
    bias = BiasResult(
        mode="intraday",
        htf_tf="1d",
        ltf_tf="1h",
        vp_lookback=60,
        rsi_value=rsi_val,
        regime="neutral" if bias_str == "neutral" else ("bull" if "bull" in bias_str else "bear"),
        hidden_div="none",
        bias=bias_str,
        bias_notes="Testing range strategy",
    )
    sr_levels = [
        SRLevel(price=vah, label="Resistance VAH", weight=1.5, source="vp"),
        SRLevel(price=val, label="Support VAL", weight=1.5, source="vp"),
        SRLevel(price=poc, label="POC Equilibrium", weight=2.0, source="vp"),
    ]

    indicators = {
        "bias": bias,
        "adx": adx,
        "atr_14": atr,
        "rsi_14": pd.Series([rsi_val] * len(df_ltf), index=df_ltf.index),
        "volume_profile": vp,
        "sr_levels": sr_levels,
        "liq_list": liq_list or [],
        "structure": SwingStructure(),
    }

    return StrategyContext(
        ticker="TEST",
        category="crypto",
        mode="intraday",
        current_price=float(df_ltf["Close"].iloc[-1]),
        df_htf=df_ltf,
        df_ltf=df_ltf,
        indicators=indicators,
    )


class TestRangeLiquiditySweepGates:
    """Unit tests for RangeLiquiditySweepStrategy."""

    def setup_method(self):
        self.strategy = RangeLiquiditySweepStrategy()

    def test_strong_trend_rejected(self):
        """ADX >= 25 with strong directional bias must be skipped."""
        df = _build_range_df(50)
        ctx = _make_context(df, adx=30.0, bias_str="strong_bull")
        setup = self.strategy.evaluate(ctx)
        assert setup is None

    def test_range_too_tight_rejected(self):
        """Range height < 1.5x ATR must be skipped."""
        df = _build_range_df(50)
        # VAH = 100.5, VAL = 99.8 -> height = 0.7 < 1.5 * ATR (1.0)
        ctx = _make_context(df, adx=15.0, vah=100.5, val=99.8, atr=1.0)
        setup = self.strategy.evaluate(ctx)
        assert setup is None

    def test_bearish_sweep_triggers_short(self):
        """Candle piercing VAH with >= 40% upper wick and closing inside triggers short."""
        df = _build_range_df(50, center=100.0)
        vah = 103.0
        val = 97.0
        atr = 1.0

        # Craft the last completed bar as a clean SFP at resistance (High 103.8, Close 102.5, Open 102.0)
        # Bar range = 103.8 - 101.8 = 2.0. Upper wick = 103.8 - 102.5 = 1.3 -> 65% wick
        df.iloc[-2, df.columns.get_loc("Open")] = 102.0
        df.iloc[-2, df.columns.get_loc("High")] = 103.8
        df.iloc[-2, df.columns.get_loc("Low")] = 101.8
        df.iloc[-2, df.columns.get_loc("Close")] = 102.5

        # Current price remains near resistance
        df.iloc[-1, df.columns.get_loc("Close")] = 102.4

        ctx = _make_context(df, adx=18.0, bias_str="neutral", vah=vah, val=val, poc=100.0, rsi_val=70.0, atr=atr)
        setup = self.strategy.evaluate(ctx)

        assert setup is not None
        assert setup.direction == "short"
        assert setup.setup_name == "Range Liquidity Sweep"
        assert setup.setup_type == "range"
        assert setup.stop_loss > 103.8  # Above sweep high
        assert setup.tp1 == 100.0       # At POC midpoint
        assert setup.tp2 < 100.0        # At Support VAL
        assert setup.rr_ratio >= 1.5

    def test_bullish_sweep_triggers_long(self):
        """Candle piercing VAL with >= 40% lower wick and closing inside triggers long."""
        df = _build_range_df(50, center=100.0)
        vah = 103.0
        val = 97.0
        atr = 1.0

        # Craft the last completed bar as an SFP at support (Low 96.2, Close 97.5, Open 98.0)
        # Bar range = 98.2 - 96.2 = 2.0. Lower wick = 97.5 - 96.2 = 1.3 -> 65% wick
        df.iloc[-2, df.columns.get_loc("Open")] = 98.0
        df.iloc[-2, df.columns.get_loc("High")] = 98.2
        df.iloc[-2, df.columns.get_loc("Low")] = 96.2
        df.iloc[-2, df.columns.get_loc("Close")] = 97.5

        # Current price remains near support
        df.iloc[-1, df.columns.get_loc("Close")] = 97.6

        ctx = _make_context(df, adx=18.0, bias_str="neutral", vah=vah, val=val, poc=100.0, rsi_val=30.0, atr=atr)
        setup = self.strategy.evaluate(ctx)

        assert setup is not None
        assert setup.direction == "long"
        assert setup.setup_name == "Range Liquidity Sweep"
        assert setup.setup_type == "range"
        assert setup.stop_loss < 96.2  # Below sweep low
        assert setup.tp1 == 100.0      # At POC midpoint
        assert setup.tp2 > 100.0       # At Resistance VAH
        assert setup.rr_ratio >= 1.5

    def test_poor_wick_breakout_rejected(self):
        """Full-bodied candle closing outside range has small wick and is rejected."""
        df = _build_range_df(50, center=100.0)
        vah = 103.0
        val = 97.0

        # Piercing bar with small wick (strong momentum breakout attempt)
        # High = 104.5, Close = 104.2 (closed above VAH)
        df.iloc[-2, df.columns.get_loc("Open")] = 102.0
        df.iloc[-2, df.columns.get_loc("High")] = 104.5
        df.iloc[-2, df.columns.get_loc("Low")] = 101.8
        df.iloc[-2, df.columns.get_loc("Close")] = 104.2

        ctx = _make_context(df, adx=18.0, bias_str="neutral", vah=vah, val=val)
        setup = self.strategy.evaluate(ctx)

        assert setup is None
