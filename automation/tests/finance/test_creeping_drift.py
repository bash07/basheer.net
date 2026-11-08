"""Unit tests for CreepingDriftStrategy."""
from datetime import datetime
import pandas as pd
import numpy as np
import pytest

from modules.finance.trader.strategies.creeping_drift import CreepingDriftStrategy
from modules.finance.trader.strategies.base import StrategyContext
from modules.finance.trader.schemas import (
    BiasResult, VolumeProfile, SRLevel, SwingStructure, SwingPoint
)


def _build_grinding_uptrend_df(n: int = 40, base_price: float = 100.0) -> pd.DataFrame:
    dates = pd.date_range(end=datetime(2026, 11, 1), periods=n)
    # Slow steady grinding up-movement (overlapping candles)
    trend = np.linspace(0, 10, n)
    close = base_price + trend
    high = close + 0.6
    low = close - 0.5
    open_p = close - 0.2
    vol = np.full(n, 50000.0)

    df = pd.DataFrame({
        "Open": open_p,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": vol,
    }, index=dates)
    df.index.name = "Date"
    return df


class TestCreepingDrift:
    def test_bullish_creeping_drift_success(self):
        strat = CreepingDriftStrategy()
        assert strat.name == "Creeping Drift"
        assert strat.setup_type == "trend"

        df = _build_grinding_uptrend_df(n=35, base_price=100.0)
        curr_price = float(df["Close"].iloc[-1])

        # Ensure last candle has a lower rejection wick touching EMA 21
        df.iloc[-1, df.columns.get_loc("Open")] = curr_price - 0.1
        df.iloc[-1, df.columns.get_loc("Low")] = curr_price - 0.7  # test dip
        df.iloc[-1, df.columns.get_loc("Close")] = curr_price

        bias = BiasResult(
            mode="swing", htf_tf="1wk", ltf_tf="1h", vp_lookback=100,
            rsi_value=58.0, regime="bull", hidden_div="none", bias="moderate_bull"
        )
        vp = VolumeProfile(poc=curr_price - 0.5, vah=curr_price + 2.0, val=curr_price - 2.0)
        sr = [
            SRLevel(price=curr_price, label="EMA Ribbon Support", weight=2.0, source="ema"),
            SRLevel(price=curr_price - 0.5, label="Pivot Support", weight=1.5, source="pivot"),
        ]
        structure = SwingStructure(points=[
            SwingPoint(index=20, price=curr_price - 3.0, kind="low", label="HL"),
            SwingPoint(index=30, price=curr_price + 3.0, kind="high", label="HH"),
        ], trend="bullish")

        indicators = {
            "bias": bias,
            "structure": structure,
            "sr_levels": sr,
            "volume_profile": vp,
            "atr_14": 1.0,
            "adx": 22.0,
        }

        ctx = StrategyContext(
            ticker="^NSEI",
            category="indian_index",
            mode="swing",
            current_price=curr_price,
            df_htf=df,
            df_ltf=df,
            df_trigger=df,
            indicators=indicators,
        )
        ctx.bind_strategy(strat)

        setup = strat.evaluate(ctx)
        assert setup is not None
        assert setup.direction == "long"
        assert setup.stop_loss < setup.entry_mid
        assert setup.tp1 > setup.entry_mid
        assert setup.rr_ratio >= 1.5

    def test_rejects_when_adx_is_choppy_flat(self):
        strat = CreepingDriftStrategy()
        df = _build_grinding_uptrend_df(n=35, base_price=100.0)
        indicators = {
            "bias": BiasResult(
                mode="swing", htf_tf="1wk", ltf_tf="1h", vp_lookback=100,
                rsi_value=58.0, regime="bull", hidden_div="none", bias="moderate_bull"
            ),
            "structure": SwingStructure(trend="bullish"),
            "sr_levels": [],
            "volume_profile": VolumeProfile(poc=100.0, vah=102.0, val=98.0),
            "atr_14": 1.0,
            "adx": 10.0, # Flat chop < 14
        }
        ctx = StrategyContext(
            ticker="^NSEI", category="indian_index", mode="swing", current_price=105.0,
            df_htf=df, df_ltf=df, df_trigger=df, indicators=indicators,
        )
        ctx.bind_strategy(strat)
        setup = strat.evaluate(ctx)
        assert setup is None
