"""Unit tests for BreakdownRetestStrategy."""
from datetime import datetime
import pandas as pd
import numpy as np
import pytest

from modules.finance.trader.strategies.breakdown_retest import BreakdownRetestStrategy
from modules.finance.trader.strategies.base import StrategyContext
from modules.finance.trader.schemas import (
    BiasResult, VolumeProfile, SRLevel, SwingStructure, BOS, SwingPoint
)


def _build_mock_df(n: int = 50, base_price: float = 100.0) -> pd.DataFrame:
    dates = pd.date_range(end=datetime(2026, 11, 1), periods=n)
    close = base_price - np.arange(n) * 0.2
    high = close + 0.8
    low = close - 0.8
    open_p = close + 0.1
    vol = np.full(n, 100000.0)

    df = pd.DataFrame({
        "Open": open_p,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": vol,
    }, index=dates)
    df.index.name = "Date"
    return df


class TestBreakdownRetest:
    def test_bearish_breakdown_retest_success(self):
        strat = BreakdownRetestStrategy()
        assert strat.name == "Breakdown Retest"
        assert strat.setup_type == "breakdown"

        df = _build_mock_df(n=30, base_price=100.0)
        # Create a trigger bar that retests broken level 95.0 with upper rejection wick
        df.iloc[-1, df.columns.get_loc("Open")] = 94.8
        df.iloc[-1, df.columns.get_loc("High")] = 95.3  # pierces level
        df.iloc[-1, df.columns.get_loc("Low")] = 94.5
        df.iloc[-1, df.columns.get_loc("Close")] = 94.7 # closes below level with upper wick

        bos = BOS(
            bar_index=20,
            direction="bearish",
            level_broken=95.0,
            candle_close=94.2,
        )

        bias = BiasResult(
            mode="swing", htf_tf="1wk", ltf_tf="1h", vp_lookback=100,
            rsi_value=45.0, regime="bear", hidden_div="none", bias="moderate_bear"
        )
        vp = VolumeProfile(poc=95.0, vah=97.0, val=93.0)
        sr = [SRLevel(price=95.0, label="Support Turned Resistance", weight=1.0, source="pivot")]
        structure = SwingStructure(points=[
            SwingPoint(index=15, price=95.0, kind="low"),
            SwingPoint(index=25, price=92.0, kind="low", label="LL"),
        ], trend="bearish")

        indicators = {
            "bias": bias,
            "structure": structure,
            "bos_list": [bos],
            "sr_levels": sr,
            "volume_profile": vp,
            "atr_14": 1.0,
            "adx": 22.0,
        }

        ctx = StrategyContext(
            ticker="^GSPC",
            category="us_index",
            mode="swing",
            current_price=94.7,
            df_htf=df,
            df_ltf=df,
            df_trigger=df,
            indicators=indicators,
        )
        ctx.bind_strategy(strat)

        setup = strat.evaluate(ctx)
        assert setup is not None
        assert setup.direction == "short"
        assert setup.stop_loss > setup.entry_mid
        assert setup.tp1 < setup.entry_mid
        assert setup.rr_ratio >= 1.5

    def test_rejects_when_htf_is_strong_bull(self):
        strat = BreakdownRetestStrategy()
        df = _build_mock_df(n=30, base_price=100.0)
        bos = BOS(bar_index=20, direction="bearish", level_broken=95.0, candle_close=94.2)
        bias = BiasResult(
            mode="swing", htf_tf="1wk", ltf_tf="1h", vp_lookback=100,
            rsi_value=70.0, regime="bull", hidden_div="none", bias="strong_bull"
        )
        indicators = {
            "bias": bias,
            "structure": SwingStructure(trend="bullish"),
            "bos_list": [bos],
            "sr_levels": [],
            "volume_profile": VolumeProfile(poc=95.0, vah=97.0, val=93.0),
            "atr_14": 1.0,
            "adx": 25.0,
        }
        ctx = StrategyContext(
            ticker="^GSPC", category="us_index", mode="swing", current_price=94.8,
            df_htf=df, df_ltf=df, df_trigger=df, indicators=indicators,
        )
        ctx.bind_strategy(strat)
        setup = strat.evaluate(ctx)
        assert setup is None
