"""Tests for Multi-Timeframe Range Liquidity Sweep strategy.

Verifies:
1. Gate 1 (HTF Where):
   - Rejects strong runaway trend expansion (ADX >= 25 with strong_bull/strong_bear).
   - Rejects compressed ranges (< 1.5x ATR).
   - Rejects mid-range trades via Location Filter (rel_pos).
2. Gate 2 (MTF When):
   - Rejects breakouts accepted on MTF (consecutive closes outside boundary).
   - Requires MTF momentum exhaustion (RSI or divergence).
3. Gate 3 (Trigger How):
   - Rejects penetration blowouts (> 1.0x ATR past boundary).
   - Rejects non-sweep candles (poor wick ratio < 40%).
   - Triggers valid Bearish Sweep (Short) with asymmetric R:R.
   - Triggers valid Bullish Sweep (Long) with asymmetric R:R.
4. Backward compatibility: Gracefully evaluates legacy 2-tier context (df_htf, df_ltf).
"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime

from app.modules.finance.trader.strategies.range_liquidity_sweep import RangeLiquiditySweepStrategy
from app.modules.finance.trader.strategies.base import StrategyContext
from app.modules.finance.trader.schemas import BiasResult, VolumeProfile, SRLevel, SwingStructure


def _build_range_df(n: int = 50, center: float = 100.0, swing_amp: float = 2.0) -> pd.DataFrame:
    """Helper to build an oscillating/ranging OHLCV DataFrame."""
    dates = pd.bdate_range(end=datetime(2025, 1, 1), periods=n)
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
    df_trigger: pd.DataFrame,
    df_mtf: pd.DataFrame = None,
    df_htf: pd.DataFrame = None,
    adx: float = 18.0,
    bias_str: str = "neutral",
    vah: float = 103.0,
    val: float = 97.0,
    poc: float = 100.0,
    rsi_val: float = 50.0,
    atr: float = 1.0,
    liq_list: list = None,
) -> StrategyContext:
    htf = df_htf if df_htf is not None else df_trigger
    mtf = df_mtf if df_mtf is not None else df_trigger

    vp = VolumeProfile(
        poc=poc,
        vah=vah,
        val=val,
        hvn=[poc],
        lvn=[],
        lookback=len(df_trigger),
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
        "rsi_14": pd.Series([rsi_val] * len(df_trigger), index=df_trigger.index),
        "volume_profile": vp,
        "sr_levels": sr_levels,
        "liq_list": liq_list or [],
        "structure": SwingStructure(),
    }

    return StrategyContext(
        ticker="TEST",
        category="crypto",
        mode="intraday",
        current_price=float(df_trigger["Close"].iloc[-1]),
        df_htf=htf,
        df_ltf=df_trigger,
        df_mtf=mtf,
        df_trigger=df_trigger,
        indicators=indicators,
    )


class TestRangeLiquiditySweepMTF:
    """Unit tests for the 3-Tier Multi-Timeframe Range Liquidity Sweep Strategy."""

    def setup_method(self):
        self.strategy = RangeLiquiditySweepStrategy()

    def test_gate1_strong_trend_rejected(self):
        """Gate 1: ADX >= 25 with strong directional bias must be skipped."""
        df = _build_range_df(50)
        ctx = _make_context(df, adx=30.0, bias_str="strong_bull")
        setup = self.strategy.evaluate(ctx)
        assert setup is None

    def test_gate1_range_too_tight_rejected(self):
        """Gate 1: Range height < 1.5x ATR must be skipped."""
        df = _build_range_df(50)
        ctx = _make_context(df, adx=15.0, vah=100.5, val=99.8, atr=1.0)
        setup = self.strategy.evaluate(ctx)
        assert setup is None

    def test_gate1_mid_range_location_rejected(self):
        """Gate 1: RelPos filter rejects when sweep extreme does not reach range extreme."""
        df = _build_range_df(50, center=100.0)
        vah = 103.0
        val = 97.0
        # High is 101.0 -> rel_pos = (101 - 97) / 6 = 0.67 < 0.80 -> rejected for short
        df.iloc[-2, df.columns.get_loc("Open")] = 100.2
        df.iloc[-2, df.columns.get_loc("High")] = 101.0
        df.iloc[-2, df.columns.get_loc("Low")] = 99.8
        df.iloc[-2, df.columns.get_loc("Close")] = 100.3

        ctx = _make_context(df, vah=vah, val=val)
        setup = self.strategy.evaluate(ctx)
        assert setup is None

    def test_gate2_mtf_consecutive_close_breakout_rejected(self):
        """Gate 2: Rejects when MTF has 2 consecutive closes outside boundary."""
        df_trig = _build_range_df(50, center=100.0)
        df_mtf = _build_range_df(30, center=100.0)
        vah = 103.0
        val = 97.0

        # SFP candle on trigger
        df_trig.iloc[-2, df_trig.columns.get_loc("Open")] = 102.0
        df_trig.iloc[-2, df_trig.columns.get_loc("High")] = 103.5
        df_trig.iloc[-2, df_trig.columns.get_loc("Low")] = 101.8
        df_trig.iloc[-2, df_trig.columns.get_loc("Close")] = 102.5

        # But MTF had 2 closes ABOVE VAH (103.2 and 103.4) -> expansion breakout!
        df_mtf.iloc[-2, df_mtf.columns.get_loc("Close")] = 103.2
        df_mtf.iloc[-1, df_mtf.columns.get_loc("Close")] = 103.4

        ctx = _make_context(df_trig, df_mtf=df_mtf, vah=vah, val=val)
        setup = self.strategy.evaluate(ctx)
        assert setup is None

    def test_gate3_blowout_penetration_rejected(self):
        """Gate 3: Rejects when sweep wick pierces > 1.0x ATR past boundary."""
        df = _build_range_df(50, center=100.0)
        vah = 103.0
        val = 97.0
        atr = 1.0

        # Pierces by 1.6 ATR (High 104.6 vs VAH 103.0) -> Blowout!
        df.iloc[-2, df.columns.get_loc("Open")] = 102.0
        df.iloc[-2, df.columns.get_loc("High")] = 104.6
        df.iloc[-2, df.columns.get_loc("Low")] = 101.8
        df.iloc[-2, df.columns.get_loc("Close")] = 102.4

        ctx = _make_context(df, vah=vah, val=val, atr=atr)
        setup = self.strategy.evaluate(ctx)
        assert setup is None

    def test_gate3_poor_wick_breakout_rejected(self):
        """Gate 3: Full-bodied candle with small wick (< 40%) is rejected."""
        df = _build_range_df(50, center=100.0)
        vah = 103.0
        val = 97.0

        # High = 103.8, Close = 103.6 -> upper wick is only 0.2 of 2.0 range (10%)
        df.iloc[-2, df.columns.get_loc("Open")] = 102.0
        df.iloc[-2, df.columns.get_loc("High")] = 103.8
        df.iloc[-2, df.columns.get_loc("Low")] = 101.8
        df.iloc[-2, df.columns.get_loc("Close")] = 103.6

        ctx = _make_context(df, vah=vah, val=val)
        setup = self.strategy.evaluate(ctx)
        assert setup is None

    def test_triad_bearish_sweep_triggers_short(self):
        """Full 3-tier triad: HTF ceiling + MTF overbought + Micro SFP triggers SHORT."""
        df_trig = _build_range_df(50, center=100.0)
        df_mtf = _build_range_df(30, center=102.0)
        vah = 103.0
        val = 97.0
        atr = 1.0

        # Trigger SFP: High 103.8 (sweep), Close 102.5, Open 102.0 -> 65% wick
        df_trig.iloc[-2, df_trig.columns.get_loc("Open")] = 102.0
        df_trig.iloc[-2, df_trig.columns.get_loc("High")] = 103.8
        df_trig.iloc[-2, df_trig.columns.get_loc("Low")] = 101.8
        df_trig.iloc[-2, df_trig.columns.get_loc("Close")] = 102.5
        df_trig.iloc[-1, df_trig.columns.get_loc("Close")] = 102.4

        ctx = _make_context(
            df_trig, df_mtf=df_mtf, adx=18.0, bias_str="neutral",
            vah=vah, val=val, poc=100.0, rsi_val=70.0, atr=atr,
        )
        setup = self.strategy.evaluate(ctx)

        assert setup is not None
        assert setup.direction == "short"
        assert setup.setup_name == "Range Liquidity Sweep"
        assert setup.setup_type == "range"
        assert setup.stop_loss > 103.8
        assert setup.tp1 == 100.0
        assert setup.tp2 < 100.0
        assert setup.rr_ratio >= 1.2

    def test_triad_bullish_sweep_triggers_long(self):
        """Full 3-tier triad: HTF floor + MTF oversold + Micro SFP triggers LONG."""
        df_trig = _build_range_df(50, center=100.0)
        df_mtf = _build_range_df(30, center=98.0)
        vah = 103.0
        val = 97.0
        atr = 1.0

        # Trigger SFP: Low 96.3 (sweep), Close 97.5, Open 98.0 -> 60% lower wick
        df_trig.iloc[-2, df_trig.columns.get_loc("Open")] = 98.0
        df_trig.iloc[-2, df_trig.columns.get_loc("High")] = 98.2
        df_trig.iloc[-2, df_trig.columns.get_loc("Low")] = 96.3
        df_trig.iloc[-2, df_trig.columns.get_loc("Close")] = 97.5
        df_trig.iloc[-1, df_trig.columns.get_loc("Close")] = 97.6

        ctx = _make_context(
            df_trig, df_mtf=df_mtf, adx=18.0, bias_str="neutral",
            vah=vah, val=val, poc=100.0, rsi_val=30.0, atr=atr,
        )
        setup = self.strategy.evaluate(ctx)

        assert setup is not None
        assert setup.direction == "long"
        assert setup.setup_name == "Range Liquidity Sweep"
        assert setup.setup_type == "range"
        assert setup.stop_loss < 96.3
        assert setup.tp1 == 100.0
        assert setup.tp2 > 100.0
        assert setup.rr_ratio >= 1.2

    def test_legacy_2tier_fallback(self):
        """Context initialized with legacy 2-tier context (df_htf, df_ltf) evaluates cleanly."""
        df = _build_range_df(50, center=100.0)
        vah = 103.0
        val = 97.0
        atr = 1.0

        # Craft SFP
        df.iloc[-2, df.columns.get_loc("Open")] = 102.0
        df.iloc[-2, df.columns.get_loc("High")] = 103.8
        df.iloc[-2, df.columns.get_loc("Low")] = 101.8
        df.iloc[-2, df.columns.get_loc("Close")] = 102.5
        df.iloc[-1, df.columns.get_loc("Close")] = 102.4

        vp = VolumeProfile(poc=100.0, vah=vah, val=val, hvn=[100.0], lvn=[], lookback=50)
        bias = BiasResult(
            mode="intraday", htf_tf="1d", ltf_tf="1h", vp_lookback=60,
            rsi_value=70.0, regime="neutral", hidden_div="none",
            bias="neutral", bias_notes="",
        )
        indicators = {
            "bias": bias, "adx": 18.0, "atr_14": atr,
            "rsi_14": pd.Series([70.0] * 50, index=df.index),
            "volume_profile": vp,
            "sr_levels": [
                SRLevel(price=vah, label="VAH", weight=1.5, source="vp"),
                SRLevel(price=val, label="VAL", weight=1.5, source="vp"),
            ],
            "liq_list": [], "structure": SwingStructure(),
        }

        # Legacy context: only df_htf and df_ltf
        ctx = StrategyContext(
            ticker="TEST", category="crypto", mode="intraday",
            current_price=102.4, df_htf=df, df_ltf=df,
            indicators=indicators,
        )

        setup = self.strategy.evaluate(ctx)
        assert setup is not None
        assert setup.direction == "short"

    def test_gate1_pro_trend_drift_filter(self):
        """Pro-trend drift filter rejects counter-trend sweeps but allows pro-trend sweeps."""
        df_trig = _build_range_df(50, center=100.0)
        vah, val, atr = 103.0, 97.0, 1.0

        # Craft bearish sweep at resistance (ceiling)
        df_trig.iloc[-2, df_trig.columns.get_loc("Open")] = 102.0
        df_trig.iloc[-2, df_trig.columns.get_loc("High")] = 103.8
        df_trig.iloc[-2, df_trig.columns.get_loc("Low")] = 101.8
        df_trig.iloc[-2, df_trig.columns.get_loc("Close")] = 102.5
        df_trig.iloc[-1, df_trig.columns.get_loc("Close")] = 102.4

        # With moderate_bull bias, shorting the ceiling is REJECTED
        ctx_bull = _make_context(df_trig, bias_str="moderate_bull", vah=vah, val=val, atr=atr)
        setup_rejected = self.strategy.evaluate(ctx_bull)
        assert setup_rejected is None

        # With moderate_bear bias, shorting the ceiling is ALLOWED (pro-trend drift)
        ctx_bear = _make_context(df_trig, bias_str="moderate_bear", vah=vah, val=val, atr=atr)
        setup_allowed = self.strategy.evaluate(ctx_bear)
        assert setup_allowed is not None
        assert setup_allowed.direction == "short"

    def test_gate3_crypto_adaptive_buffer(self):
        """Crypto pairs get 0.50x ATR buffer, while equities get 0.30x ATR buffer."""
        df_trig = _build_range_df(50, center=100.0)
        vah, val, atr = 103.0, 97.0, 1.0

        df_trig.iloc[-2, df_trig.columns.get_loc("Open")] = 102.0
        df_trig.iloc[-2, df_trig.columns.get_loc("High")] = 103.8
        df_trig.iloc[-2, df_trig.columns.get_loc("Low")] = 101.8
        df_trig.iloc[-2, df_trig.columns.get_loc("Close")] = 102.5
        df_trig.iloc[-1, df_trig.columns.get_loc("Close")] = 102.4

        ctx_crypto = _make_context(df_trig, bias_str="neutral", vah=vah, val=val, rsi_val=70.0, atr=atr)
        ctx_crypto.category = "crypto"
        setup_crypto = self.strategy.evaluate(ctx_crypto)
        assert setup_crypto is not None
        # SL = 103.8 + 0.50 * 1.0 = 104.30
        assert round(setup_crypto.stop_loss, 2) == 104.30

        ctx_equity = _make_context(df_trig, bias_str="neutral", vah=vah, val=val, rsi_val=70.0, atr=atr)
        ctx_equity.category = "us_stock"
        setup_equity = self.strategy.evaluate(ctx_equity)
        assert setup_equity is not None
        # SL = 103.8 + 0.30 * 1.0 = 104.10
        assert round(setup_equity.stop_loss, 2) == 104.10
