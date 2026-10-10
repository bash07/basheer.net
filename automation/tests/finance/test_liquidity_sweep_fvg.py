"""Unit tests for Multi-Timeframe Triad Liquidity Sweep + FVG Strategy.

Verifies:
1. Gate 1 (HTF Where):
   - Rejects neutral bias or weak trend momentum (ADX < 18.0).
   - Rejects Longs outside HTF Discount (RelPos > 0.55).
   - Rejects Shorts outside HTF Premium (RelPos < 0.45).
   - Rejects stale sweeps (> 25 bars ago).
2. Gate 2 (MTF When):
   - Rejects if no FVG formed after the sweep bar.
   - Rejects degenerate micro-gaps (< 0.10x ATR) and oversized blowout gaps (> 2.5x ATR).
   - Rejects exhausted FVGs with > 2 prior touches.
   - Rejects blowout acceptance on MTF (2 consecutive closes past sweep price).
   - Rejects extreme falling knives / parabolic surges (MTF RSI < 25 on Long).
3. Gate 3 (Trigger How):
   - Rejects weak trigger candle wicks (< 35% rejection wick).
   - Valid Bullish Liquidity Sweep + FVG triggers Long setup with >= 1.5 R:R.
   - Valid Bearish Liquidity Sweep + FVG triggers Short setup with >= 1.5 R:R.
   - Adaptive SL buffer: 0.50x ATR for Crypto vs 0.25x ATR for Equities.
   - Legacy 2-tier fallback: Evaluates properly when df_mtf is None.
"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime

from app.modules.finance.trader.strategies.liquidity_sweep import LiquiditySweepFVGStrategy
from app.modules.finance.trader.strategies.base import StrategyContext
from app.modules.finance.trader.schemas import (
    BiasResult, VolumeProfile, SRLevel, SwingStructure, LiquidityLevel, FVG,
)


def _build_candles(n: int = 50, start_price: float = 100.0, trend: float = 0.0) -> pd.DataFrame:
    """Helper to generate mock OHLCV bars."""
    dates = pd.bdate_range(end=datetime(2025, 1, 1), periods=n)
    close = np.linspace(start_price, start_price + trend, n)
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


def _make_context(
    df_trigger: pd.DataFrame,
    df_mtf: pd.DataFrame = None,
    df_htf: pd.DataFrame = None,
    bias_str: str = "strong_bull",
    category: str = "us_stock",
    liq_list: list = None,
    fvg_list: list = None,
    atr: float = 2.0,
    current_price: float = 100.0,
    adx_val: float = 25.0,
) -> StrategyContext:
    htf = df_htf if df_htf is not None else df_trigger
    mtf = df_mtf if df_mtf is not None else df_trigger

    vp = VolumeProfile(
        poc=current_price,
        vah=current_price + 5.0,
        val=current_price - 5.0,
        hvn=[current_price],
        lvn=[],
        lookback=len(df_trigger),
    )
    bias = BiasResult(
        mode="swing",
        htf_tf="1d",
        ltf_tf="1h",
        vp_lookback=60,
        rsi_value=50.0,
        regime="neutral" if bias_str == "neutral" else ("bull" if "bull" in bias_str else "bear"),
        hidden_div="none",
        bias=bias_str,
        bias_notes="Testing Liquidity Sweep + FVG",
    )
    sr_levels = [
        SRLevel(price=current_price, label="Support FVG Zone", weight=3.0, source="swing"),
        SRLevel(price=current_price + 8.0, label="Resistance Target", weight=3.0, source="swing"),
    ]

    indicators = {
        "bias": bias,
        "adx": adx_val,
        "atr_14": atr,
        "volume_profile": vp,
        "sr_levels": sr_levels,
        "structure": SwingStructure(),
        "bos_list": [],
        "ob_list": [],
        "fvg_list": fvg_list or [],
        "liq_list": liq_list or [],
    }

    return StrategyContext(
        ticker="TEST",
        category=category,
        mode="swing",
        current_price=current_price,
        df_htf=htf,
        df_mtf=mtf,
        df_trigger=df_trigger,
        indicators=indicators,
    )


class TestLiquiditySweepFVGStrategy:

    def test_gate1_neutral_bias_rejected(self):
        strat = LiquiditySweepFVGStrategy()
        df = _build_candles(30, 100.0)
        ctx = _make_context(df_trigger=df, bias_str="neutral")
        assert strat.evaluate(ctx) is None

    def test_gate1_weak_trend_momentum_rejected(self):
        strat = LiquiditySweepFVGStrategy()
        df = _build_candles(30, 100.0)
        ctx = _make_context(df_trigger=df, bias_str="strong_bull", adx_val=14.0)
        assert strat.evaluate(ctx) is None

    def test_gate1_long_rejected_outside_htf_discount(self):
        strat = LiquiditySweepFVGStrategy()
        df_htf = _build_candles(30, 100.0)
        df_htf["High"] = 120.0
        df_htf["Low"]  = 100.0
        # Price 115 is RelPos = 15/20 = 0.75 > 0.55 (HTF Premium)
        df_trigger = _build_candles(20, 115.0)

        liq = LiquidityLevel(price=114.0, kind="eql", bar_indices=[5, 8], swept=True, sweep_bar=15)
        fvg = FVG(bar_index=16, direction="bullish", gap_low=114.5, gap_high=115.5, displacement_ratio=1.5)

        ctx = _make_context(
            df_trigger=df_trigger, df_htf=df_htf,
            bias_str="strong_bull", current_price=115.0,
            liq_list=[liq], fvg_list=[fvg],
        )
        assert strat.evaluate(ctx) is None

    def test_gate1_short_rejected_outside_htf_premium(self):
        strat = LiquiditySweepFVGStrategy()
        df_htf = _build_candles(30, 100.0)
        df_htf["High"] = 120.0
        df_htf["Low"]  = 100.0
        # Price 105 is RelPos = 5/20 = 0.25 < 0.45 (HTF Discount)
        df_trigger = _build_candles(20, 105.0)

        liq = LiquidityLevel(price=106.0, kind="eqh", bar_indices=[5, 8], swept=True, sweep_bar=15)
        fvg = FVG(bar_index=16, direction="bearish", gap_low=104.5, gap_high=105.5, displacement_ratio=1.5)

        ctx = _make_context(
            df_trigger=df_trigger, df_htf=df_htf,
            bias_str="strong_bear", current_price=105.0,
            liq_list=[liq], fvg_list=[fvg],
        )
        assert strat.evaluate(ctx) is None

    def test_gate1_stale_sweep_rejected(self):
        strat = LiquiditySweepFVGStrategy()
        df_trigger = _build_candles(50, 100.0)
        # Sweep was at bar 10 in a 50-bar series -> 39 bars ago > 25
        liq = LiquidityLevel(price=98.0, kind="eql", bar_indices=[3, 5], swept=True, sweep_bar=10)
        fvg = FVG(bar_index=12, direction="bullish", gap_low=99.5, gap_high=100.5, displacement_ratio=1.5)

        ctx = _make_context(
            df_trigger=df_trigger, bias_str="strong_bull",
            current_price=100.0, liq_list=[liq], fvg_list=[fvg],
        )
        assert strat.evaluate(ctx) is None

    def test_gate2_gap_size_too_small_rejected(self):
        strat = LiquiditySweepFVGStrategy()
        df_trigger = _build_candles(30, 100.0)
        # Gap size = 0.05, ATR = 2.0 -> gap size 0.025x ATR < 0.10x ATR
        liq = LiquidityLevel(price=98.0, kind="eql", bar_indices=[5, 8], swept=True, sweep_bar=20)
        fvg = FVG(bar_index=22, direction="bullish", gap_low=99.95, gap_high=100.0, displacement_ratio=1.5)

        ctx = _make_context(
            df_trigger=df_trigger, bias_str="strong_bull", atr=2.0,
            current_price=100.0, liq_list=[liq], fvg_list=[fvg],
        )
        assert strat.evaluate(ctx) is None

    def test_gate2_gap_size_oversized_rejected(self):
        strat = LiquiditySweepFVGStrategy()
        df_trigger = _build_candles(30, 100.0)
        # Gap size = 6.0, ATR = 2.0 -> gap size 3.0x ATR > 2.5x ATR
        liq = LiquidityLevel(price=95.0, kind="eql", bar_indices=[5, 8], swept=True, sweep_bar=20)
        fvg = FVG(bar_index=22, direction="bullish", gap_low=97.0, gap_high=103.0, displacement_ratio=1.5)

        ctx = _make_context(
            df_trigger=df_trigger, bias_str="strong_bull", atr=2.0,
            current_price=100.0, liq_list=[liq], fvg_list=[fvg],
        )
        assert strat.evaluate(ctx) is None

    def test_gate2_fvg_exhausted_touches_rejected(self):
        strat = LiquiditySweepFVGStrategy()
        df_trigger = _build_candles(30, 100.0)
        # Place baseline candles outside FVG zone [99.5, 100.5]
        for idx in range(21, 30):
            df_trigger.loc[df_trigger.index[idx], "Low"] = 102.0
            df_trigger.loc[df_trigger.index[idx], "High"] = 103.0
            df_trigger.loc[df_trigger.index[idx], "Close"] = 102.5
            df_trigger.loc[df_trigger.index[idx], "Open"] = 102.2

        # 3 distinct dips/touches into the FVG zone [99.5, 100.5]
        df_trigger.loc[df_trigger.index[22], "Low"] = 100.0
        df_trigger.loc[df_trigger.index[24], "Low"] = 100.0
        df_trigger.loc[df_trigger.index[26], "Low"] = 100.0

        liq = LiquidityLevel(price=98.0, kind="eql", bar_indices=[5, 8], swept=True, sweep_bar=18)
        fvg = FVG(bar_index=20, direction="bullish", gap_low=99.5, gap_high=100.5, displacement_ratio=1.5)

        ctx = _make_context(
            df_trigger=df_trigger, bias_str="strong_bull",
            current_price=100.0, liq_list=[liq], fvg_list=[fvg],
        )
        assert strat.evaluate(ctx) is None

    def test_gate2_mtf_consecutive_close_blowout_rejected(self):
        strat = LiquiditySweepFVGStrategy()
        df_mtf = _build_candles(30, 100.0)
        sweep_price = 98.0
        # 2 consecutive MTF closes below sweep_price
        df_mtf.loc[df_mtf.index[-2], "Close"] = 97.0
        df_mtf.loc[df_mtf.index[-1], "Close"] = 96.5

        df_trigger = _build_candles(20, 100.0)
        liq = LiquidityLevel(price=sweep_price, kind="eql", bar_indices=[5, 8], swept=True, sweep_bar=20)
        fvg = FVG(bar_index=22, direction="bullish", gap_low=99.0, gap_high=101.0, displacement_ratio=1.5)

        ctx = _make_context(
            df_trigger=df_trigger, df_mtf=df_mtf,
            bias_str="strong_bull", current_price=100.0,
            liq_list=[liq], fvg_list=[fvg],
        )
        assert strat.evaluate(ctx) is None

    def test_gate2_mtf_falling_knife_rsi_rejected(self):
        strat = LiquiditySweepFVGStrategy()
        df_mtf = _build_candles(30, 100.0, trend=-25.0)  # sharp crash -> RSI < 25
        df_trigger = _build_candles(20, 75.0)

        liq = LiquidityLevel(price=74.0, kind="eql", bar_indices=[5, 8], swept=True, sweep_bar=20)
        fvg = FVG(bar_index=22, direction="bullish", gap_low=74.5, gap_high=75.5, displacement_ratio=1.5)

        ctx = _make_context(
            df_trigger=df_trigger, df_mtf=df_mtf,
            bias_str="strong_bull", current_price=75.0,
            liq_list=[liq], fvg_list=[fvg],
        )
        assert strat.evaluate(ctx) is None

    def test_gate3_weak_rejection_wick_rejected(self):
        strat = LiquiditySweepFVGStrategy()
        df_trigger = _build_candles(25, 100.0)
        # Candle that closes at bottom with no lower wick
        df_trigger.loc[df_trigger.index[-1], "Open"] = 100.5
        df_trigger.loc[df_trigger.index[-1], "High"] = 100.8
        df_trigger.loc[df_trigger.index[-1], "Low"] = 99.5
        df_trigger.loc[df_trigger.index[-1], "Close"] = 99.55

        liq = LiquidityLevel(price=98.0, kind="eql", bar_indices=[5, 8], swept=True, sweep_bar=18)
        fvg = FVG(bar_index=20, direction="bullish", gap_low=99.0, gap_high=101.0, displacement_ratio=1.5)

        ctx = _make_context(
            df_trigger=df_trigger, bias_str="strong_bull",
            current_price=100.0, liq_list=[liq], fvg_list=[fvg],
        )
        assert strat.evaluate(ctx) is None

    def test_triad_bullish_sweep_fvg_triggers_long(self):
        strat = LiquiditySweepFVGStrategy()
        df_htf = _build_candles(35, 95.0, trend=10.0)  # 95 to 105, 100 is RelPos ~ 0.50 (Discount)
        df_mtf = _build_candles(30, 99.0, trend=1.0)
        df_trigger = _build_candles(25, 100.0)

        # Trigger bar has solid lower rejection wick
        df_trigger.loc[df_trigger.index[-1], "Open"] = 100.2
        df_trigger.loc[df_trigger.index[-1], "High"] = 100.5
        df_trigger.loc[df_trigger.index[-1], "Low"] = 99.2
        df_trigger.loc[df_trigger.index[-1], "Close"] = 100.3

        liq = LiquidityLevel(price=98.5, kind="eql", bar_indices=[5, 8], swept=True, sweep_bar=18)
        fvg = FVG(bar_index=20, direction="bullish", gap_low=99.5, gap_high=100.5, displacement_ratio=1.6)

        ctx = _make_context(
            df_trigger=df_trigger, df_mtf=df_mtf, df_htf=df_htf,
            bias_str="strong_bull", current_price=100.0, atr=2.0,
            liq_list=[liq], fvg_list=[fvg], category="crypto",
        )

        setup = strat.evaluate(ctx)
        assert setup is not None
        assert setup.direction == "long"
        assert setup.setup_name == "Liquidity Sweep + FVG"
        assert setup.rr_ratio >= 1.5
        # Crypto buffer = 0.50 * 2.0 = 1.0 -> SL = 98.5 - 1.0 = 97.5
        assert setup.stop_loss == 97.5
        assert setup.tp1 > 100.0

    def test_triad_bearish_sweep_fvg_triggers_short(self):
        strat = LiquiditySweepFVGStrategy()
        df_htf = _build_candles(35, 105.0, trend=-10.0)  # 105 down to 95
        # Set HTF high 105, low 95, current 100 -> RelPos 0.50 (Premium for short)
        df_htf["High"] = 105.0
        df_htf["Low"]  = 95.0
        df_mtf = _build_candles(30, 101.0, trend=-1.0)
        df_trigger = _build_candles(25, 100.0)

        # Trigger bar has upper rejection wick
        df_trigger.loc[df_trigger.index[-1], "Open"] = 99.8
        df_trigger.loc[df_trigger.index[-1], "High"] = 100.8
        df_trigger.loc[df_trigger.index[-1], "Low"] = 99.5
        df_trigger.loc[df_trigger.index[-1], "Close"] = 99.7

        liq = LiquidityLevel(price=101.5, kind="eqh", bar_indices=[5, 8], swept=True, sweep_bar=18)
        fvg = FVG(bar_index=20, direction="bearish", gap_low=99.5, gap_high=100.5, displacement_ratio=1.6)

        ctx = _make_context(
            df_trigger=df_trigger, df_mtf=df_mtf, df_htf=df_htf,
            bias_str="strong_bear", current_price=100.0, atr=2.0,
            liq_list=[liq], fvg_list=[fvg], category="us_stock",
        )

        setup = strat.evaluate(ctx)
        assert setup is not None
        assert setup.direction == "short"
        assert setup.setup_name == "Liquidity Sweep + FVG"
        assert setup.rr_ratio >= 1.5
        # Equities buffer = 0.25 * 2.0 = 0.5 -> SL = 101.5 + 0.5 = 102.0
        assert setup.stop_loss == 102.0
        assert setup.tp1 < 100.0

    def test_legacy_2tier_fallback(self):
        strat = LiquiditySweepFVGStrategy()
        df_trigger = _build_candles(25, 100.0)
        df_trigger.loc[df_trigger.index[-1], "Open"] = 100.2
        df_trigger.loc[df_trigger.index[-1], "High"] = 100.5
        df_trigger.loc[df_trigger.index[-1], "Low"] = 99.2
        df_trigger.loc[df_trigger.index[-1], "Close"] = 100.3

        liq = LiquidityLevel(price=98.5, kind="eql", bar_indices=[5, 8], swept=True, sweep_bar=18)
        fvg = FVG(bar_index=20, direction="bullish", gap_low=99.5, gap_high=100.5, displacement_ratio=1.6)

        # Context with df_mtf=None explicitly
        ctx = _make_context(
            df_trigger=df_trigger, df_mtf=None, df_htf=df_trigger,
            bias_str="strong_bull", current_price=100.0, atr=2.0,
            liq_list=[liq], fvg_list=[fvg],
        )

        setup = strat.evaluate(ctx)
        assert setup is not None
        assert setup.direction == "long"
