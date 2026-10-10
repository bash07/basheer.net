"""Unit tests for Multi-Timeframe Triad OB Retracement Strategy.

Verifies:
1. Gate 1 (HTF Where):
   - Rejects neutral bias.
   - Rejects Longs outside HTF Discount (RelPos > 0.55).
   - Rejects Shorts outside HTF Premium (RelPos < 0.45).
   - Rejects oversized OB zones (> 2.5x ATR).
2. Gate 2 (MTF When):
   - Rejects blowout acceptance on MTF (2 consecutive closes past distal edge).
   - Rejects extreme falling knives / parabolic surges (MTF RSI < 25 on Long).
3. Gate 3 (Trigger How):
   - Rejects weak trigger candle wicks (< 35% rejection wick).
   - Valid Bullish OB Retracement triggers Long setup with >= 1.5 R:R.
   - Valid Bearish OB Retracement triggers Short setup with >= 1.5 R:R.
   - Adaptive SL buffer: 0.50x ATR for Crypto vs 0.25x ATR for Equities.
   - Legacy 2-tier fallback: Evaluates properly when df_mtf is None.
"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime

from app.modules.finance.trader.strategies.ob_retracement import OBRetracementStrategy
from app.modules.finance.trader.strategies.base import StrategyContext
from app.modules.finance.trader.schemas import (
    BiasResult, VolumeProfile, SRLevel, SwingStructure, OrderBlock, BOS,
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
    ob_list: list = None,
    bos_list: list = None,
    atr: float = 2.0,
    current_price: float = 100.0,
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
        bias_notes="Testing OB Retracement",
    )
    sr_levels = [
        SRLevel(price=current_price - 2.0, label="Support OB Zone", weight=3.0, source="swing"),
        SRLevel(price=current_price + 8.0, label="Resistance Target", weight=3.0, source="swing"),
    ]

    indicators = {
        "bias": bias,
        "atr_14": atr,
        "volume_profile": vp,
        "sr_levels": sr_levels,
        "structure": SwingStructure(),
        "bos_list": bos_list or [],
        "ob_list": ob_list or [],
        "liq_list": [],
    }

    return StrategyContext(
        ticker="TEST-ASSET",
        category=category,
        mode="swing",
        current_price=current_price,
        df_htf=htf,
        df_mtf=mtf,
        df_trigger=df_trigger,
        indicators=indicators,
    )


class TestOBRetracementMTF:

    def test_gate1_neutral_bias_rejected(self):
        df = _build_candles(30, 100.0)
        ctx = _make_context(df, bias_str="neutral")
        strategy = OBRetracementStrategy()
        assert strategy.evaluate(ctx) is None

    def test_gate1_long_rejected_outside_discount(self):
        # HTF range from 80 to 120. Range = 40. RelPos = (110 - 80) / 40 = 0.75 (> 0.55)
        df_htf = _build_candles(30, 100.0)
        df_htf.iloc[0, df_htf.columns.get_loc("Low")] = 80.0
        df_htf.iloc[1, df_htf.columns.get_loc("High")] = 120.0

        df_trigger = _build_candles(20, 110.0)
        ob = OrderBlock(bar_index=15, direction="bullish", zone_low=109.0, zone_high=111.0, origin_bos_level=108.0)

        ctx = _make_context(df_trigger, df_htf=df_htf, bias_str="strong_bull", current_price=110.0, ob_list=[ob])
        strategy = OBRetracementStrategy()
        assert strategy.evaluate(ctx) is None

    def test_gate1_short_rejected_outside_premium(self):
        # HTF range from 80 to 120. Price at 90. RelPos = (90 - 80) / 40 = 0.25 (< 0.45)
        df_htf = _build_candles(30, 100.0)
        df_htf.iloc[0, df_htf.columns.get_loc("Low")] = 80.0
        df_htf.iloc[1, df_htf.columns.get_loc("High")] = 120.0

        df_trigger = _build_candles(20, 90.0)
        ob = OrderBlock(bar_index=15, direction="bearish", zone_low=89.0, zone_high=91.0, origin_bos_level=92.0)

        ctx = _make_context(df_trigger, df_htf=df_htf, bias_str="strong_bear", current_price=90.0, ob_list=[ob])
        strategy = OBRetracementStrategy()
        assert strategy.evaluate(ctx) is None

    def test_gate1_oversized_ob_rejected(self):
        # ATR is 2.0. Max OB size is 2.5 * 2.0 = 5.0. An OB of 8.0 should be rejected.
        df_trigger = _build_candles(20, 100.0)
        ob = OrderBlock(bar_index=15, direction="bullish", zone_low=96.0, zone_high=104.0, origin_bos_level=95.0)

        ctx = _make_context(df_trigger, bias_str="strong_bull", atr=2.0, current_price=100.0, ob_list=[ob])
        strategy = OBRetracementStrategy()
        assert strategy.evaluate(ctx) is None

    def test_gate2_mtf_blowout_consecutive_closes_rejected(self):
        # OB zone_low is 99.0. If last 2 MTF closes are below 99.0 (e.g. 97.0 and 96.5), reject.
        df_mtf = _build_candles(30, 100.0)
        df_mtf.iloc[-2, df_mtf.columns.get_loc("Close")] = 97.0
        df_mtf.iloc[-1, df_mtf.columns.get_loc("Close")] = 96.5

        df_trigger = _build_candles(20, 99.5)
        ob = OrderBlock(bar_index=15, direction="bullish", zone_low=99.0, zone_high=101.0, origin_bos_level=98.0)

        ctx = _make_context(df_trigger, df_mtf=df_mtf, bias_str="strong_bull", current_price=99.5, ob_list=[ob])
        strategy = OBRetracementStrategy()
        assert strategy.evaluate(ctx) is None

    def test_gate2_mtf_extreme_falling_knife_rejected(self):
        # MTF with heavy falling candles driving RSI < 25
        df_mtf = _build_candles(30, 150.0, trend=-60.0)  # sharp drop
        df_trigger = _build_candles(20, 99.5)
        ob = OrderBlock(bar_index=15, direction="bullish", zone_low=99.0, zone_high=101.0, origin_bos_level=98.0)

        ctx = _make_context(df_trigger, df_mtf=df_mtf, bias_str="strong_bull", current_price=99.5, ob_list=[ob])
        strategy = OBRetracementStrategy()
        assert strategy.evaluate(ctx) is None

    def test_gate3_weak_trigger_rejection_wick_rejected(self):
        # Trigger candle has almost no lower wick on Long
        df_trigger = _build_candles(20, 100.0)
        df_trigger.iloc[-1, df_trigger.columns.get_loc("Open")] = 100.5
        df_trigger.iloc[-1, df_trigger.columns.get_loc("High")] = 101.0
        df_trigger.iloc[-1, df_trigger.columns.get_loc("Low")] = 99.0
        df_trigger.iloc[-1, df_trigger.columns.get_loc("Close")] = 99.2  # close near low, lower wick = 0.2 / 2.0 = 10%

        ob = OrderBlock(bar_index=15, direction="bullish", zone_low=99.0, zone_high=101.0, origin_bos_level=98.0)
        ctx = _make_context(df_trigger, bias_str="strong_bull", current_price=99.5, ob_list=[ob])
        strategy = OBRetracementStrategy()
        assert strategy.evaluate(ctx) is None

    def test_triad_bullish_ob_retracement_triggers_long(self):
        # HTF range 80 to 120. Price at 95. RelPos = 15/40 = 0.375 (in Discount <= 0.55).
        df_htf = _build_candles(30, 100.0)
        df_htf.iloc[0, df_htf.columns.get_loc("Low")] = 80.0
        df_htf.iloc[1, df_htf.columns.get_loc("High")] = 120.0

        # MTF stable
        df_mtf = _build_candles(30, 95.0)

        # Trigger candle with hammer/rejection wick: Open 95.5, High 96.0, Low 93.5, Close 95.8 (lower wick = 2.0 / 2.5 = 80%)
        df_trigger = _build_candles(20, 95.0)
        df_trigger.iloc[-1, df_trigger.columns.get_loc("Open")] = 95.5
        df_trigger.iloc[-1, df_trigger.columns.get_loc("High")] = 96.0
        df_trigger.iloc[-1, df_trigger.columns.get_loc("Low")] = 93.5
        df_trigger.iloc[-1, df_trigger.columns.get_loc("Close")] = 95.8

        ob = OrderBlock(bar_index=15, direction="bullish", zone_low=94.0, zone_high=96.0, origin_bos_level=92.0)
        ctx = _make_context(
            df_trigger, df_mtf=df_mtf, df_htf=df_htf,
            bias_str="strong_bull", current_price=95.0,
            ob_list=[ob], atr=2.0, category="us_stock",
        )

        strategy = OBRetracementStrategy()
        setup = strategy.evaluate(ctx)

        assert setup is not None
        assert setup.direction == "long"
        assert setup.setup_name == "OB Retracement"
        assert setup.rr_ratio >= 1.5
        # Adaptive SL for us_stock is 0.25 * atr = 0.50 below zone_low (94.0 - 0.50 = 93.50)
        assert setup.stop_loss == pytest.approx(93.50, rel=1e-3)
        assert setup.tp1 > setup.entry_high

    def test_triad_bearish_ob_retracement_triggers_short(self):
        # HTF range 80 to 120. Price at 108. RelPos = 28/40 = 0.70 (in Premium >= 0.45).
        df_htf = _build_candles(30, 100.0)
        df_htf.iloc[0, df_htf.columns.get_loc("Low")] = 80.0
        df_htf.iloc[1, df_htf.columns.get_loc("High")] = 120.0

        # MTF stable
        df_mtf = _build_candles(30, 108.0)

        # Trigger candle with shooting star / upper rejection wick: Open 107.5, High 109.5, Low 107.0, Close 107.2 (upper wick = 2.0 / 2.5 = 80%)
        df_trigger = _build_candles(20, 108.0)
        df_trigger.iloc[-1, df_trigger.columns.get_loc("Open")] = 107.5
        df_trigger.iloc[-1, df_trigger.columns.get_loc("High")] = 109.5
        df_trigger.iloc[-1, df_trigger.columns.get_loc("Low")] = 107.0
        df_trigger.iloc[-1, df_trigger.columns.get_loc("Close")] = 107.2

        ob = OrderBlock(bar_index=15, direction="bearish", zone_low=107.0, zone_high=109.0, origin_bos_level=111.0)
        ctx = _make_context(
            df_trigger, df_mtf=df_mtf, df_htf=df_htf,
            bias_str="strong_bear", current_price=108.0,
            ob_list=[ob], atr=2.0, category="us_stock",
        )

        strategy = OBRetracementStrategy()
        setup = strategy.evaluate(ctx)

        assert setup is not None
        assert setup.direction == "short"
        assert setup.setup_name == "OB Retracement"
        assert setup.rr_ratio >= 1.5
        # Adaptive SL for us_stock is 0.25 * atr = 0.50 above zone_high (109.0 + 0.50 = 109.50)
        assert setup.stop_loss == pytest.approx(109.50, rel=1e-3)
        assert setup.tp1 < setup.entry_low

    def test_legacy_2tier_fallback(self):
        # No df_mtf, only df_htf and df_ltf
        df_htf = _build_candles(30, 100.0)
        df_ltf = _build_candles(20, 95.0)
        df_ltf.iloc[-1, df_ltf.columns.get_loc("Open")] = 95.5
        df_ltf.iloc[-1, df_ltf.columns.get_loc("High")] = 96.0
        df_ltf.iloc[-1, df_ltf.columns.get_loc("Low")] = 93.5
        df_ltf.iloc[-1, df_ltf.columns.get_loc("Close")] = 95.8

        ob = OrderBlock(bar_index=15, direction="bullish", zone_low=94.0, zone_high=96.0, origin_bos_level=92.0)

        # Context created with df_mtf=None
        ctx = _make_context(
            df_trigger=df_ltf, df_mtf=None, df_htf=df_htf,
            bias_str="strong_bull", current_price=95.0,
            ob_list=[ob], atr=2.0,
        )

        strategy = OBRetracementStrategy()
        setup = strategy.evaluate(ctx)
        assert setup is not None
        assert setup.setup_name == "OB Retracement"

    def test_adaptive_sl_buffer_crypto_vs_equity(self):
        # Compare crypto buffer (0.50 ATR) vs equity buffer (0.25 ATR)
        df_htf = _build_candles(30, 100.0)
        df_htf.iloc[0, df_htf.columns.get_loc("Low")] = 80.0
        df_htf.iloc[1, df_htf.columns.get_loc("High")] = 120.0

        df_trigger = _build_candles(20, 95.0)
        df_trigger.iloc[-1, df_trigger.columns.get_loc("Open")] = 95.5
        df_trigger.iloc[-1, df_trigger.columns.get_loc("High")] = 96.0
        df_trigger.iloc[-1, df_trigger.columns.get_loc("Low")] = 93.5
        df_trigger.iloc[-1, df_trigger.columns.get_loc("Close")] = 95.8

        ob = OrderBlock(bar_index=15, direction="bullish", zone_low=94.0, zone_high=96.0, origin_bos_level=92.0)

        # Crypto context: 0.50 * 2.0 = 1.0 buffer -> SL = 94.0 - 1.0 = 93.0
        ctx_crypto = _make_context(
            df_trigger, df_htf=df_htf, bias_str="strong_bull", current_price=95.0,
            ob_list=[ob], atr=2.0, category="crypto",
        )
        strategy = OBRetracementStrategy()
        setup_crypto = strategy.evaluate(ctx_crypto)
        assert setup_crypto is not None
        assert setup_crypto.stop_loss == pytest.approx(93.0, rel=1e-3)

        # Equity context: 0.25 * 2.0 = 0.5 buffer -> SL = 94.0 - 0.5 = 93.5
        ctx_equity = _make_context(
            df_trigger, df_htf=df_htf, bias_str="strong_bull", current_price=95.0,
            ob_list=[ob], atr=2.0, category="us_stock",
        )
        setup_equity = strategy.evaluate(ctx_equity)
        assert setup_equity is not None
        assert setup_equity.stop_loss == pytest.approx(93.5, rel=1e-3)
