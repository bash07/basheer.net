"""Tests for Point & Figure (P&F) Indicator and Wyckoff Cause & Effect Engine."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from modules.finance.trader.indicators.point_and_figure import (
    generate_pnf_columns,
    compute_pnf_cause_and_effect,
    PointAndFigureInfo,
)
from modules.finance.trader.indicators import compute_indicators
from modules.finance.trader.strategies.base import StrategyContext
from modules.finance.trader.strategies.wyckoff_accumulation import WyckoffAccumulationStrategy
from modules.finance.trader.strategies.wyckoff_distribution import WyckoffDistributionStrategy
from modules.finance.trader.schemas import BiasResult


def _make_oscillating_df(waves: int = 4, low: float = 100.0, high: float = 105.0) -> pd.DataFrame:
    """Create a synthetic oscillating price DataFrame."""
    prices = []
    for _ in range(waves):
        prices.extend(np.linspace(low, high, 10))
        prices.extend(np.linspace(high, low, 10))

    arr = np.array(prices)
    dates = pd.date_range("2026-01-01", periods=len(arr), freq="1h")
    return pd.DataFrame({
        "Open": arr,
        "High": arr + 0.2,
        "Low": arr - 0.2,
        "Close": arr,
        "Volume": np.full(len(arr), 1000.0),
    }, index=dates)


class TestPointAndFigure:
    """Test suite for P&F generation and Cause & Effect calculations."""

    def test_generate_pnf_columns_alternating(self):
        df = _make_oscillating_df(waves=3, low=100.0, high=106.0)
        box_size = 1.0
        reversal = 3

        cols = generate_pnf_columns(df, box_size=box_size, reversal=reversal)
        assert len(cols) >= 4

        # Columns should strictly alternate between X (+1) and O (-1)
        for i in range(1, len(cols)):
            assert cols[i].direction == -cols[i - 1].direction

    def test_compute_pnf_cause_and_effect_accumulation(self):
        df = _make_oscillating_df(waves=4, low=100.0, high=105.0)
        box_size = 0.5
        reversal = 3

        pnf = compute_pnf_cause_and_effect(
            df=df,
            box_size=box_size,
            reversal=reversal,
            range_low=99.5,
            range_high=105.5,
            range_mid=102.5,
            spring_detected=True,
        )

        assert isinstance(pnf, PointAndFigureInfo)
        assert pnf.horizontal_count >= 4
        assert pnf.conservative_count >= 2
        assert pnf.cause_pts > 0
        assert pnf.projected_long_full > pnf.count_row_price
        assert pnf.projected_long_conservative > pnf.count_row_price
        assert pnf.projected_long_full >= pnf.projected_long_conservative

    def test_compute_pnf_cause_and_effect_distribution(self):
        df = _make_oscillating_df(waves=4, low=200.0, high=210.0)
        box_size = 1.0
        reversal = 3

        pnf = compute_pnf_cause_and_effect(
            df=df,
            box_size=box_size,
            reversal=reversal,
            range_low=199.0,
            range_high=211.0,
            range_mid=205.0,
            upthrust_detected=True,
        )

        assert pnf.horizontal_count >= 4
        assert pnf.projected_short_full < pnf.count_row_price
        assert pnf.projected_short_conservative < pnf.count_row_price
        assert pnf.projected_short_full <= pnf.projected_short_conservative

    def test_indicator_registry_resolution(self):
        df = _make_oscillating_df(waves=2)
        inds = compute_indicators(["point_and_figure", "pnf"], df, df)

        assert "point_and_figure" in inds
        assert "pnf" in inds
        assert inds["point_and_figure"].horizontal_count >= 1

    def test_wyckoff_accumulation_uses_pnf_targets(self):
        df = _make_oscillating_df(waves=3, low=100.0, high=105.0)
        inds = compute_indicators(
            ["accum_dist", "point_and_figure", "atr_14", "rsi_14", "sr_levels", "volume_profile"],
            df,
            df,
        )

        strat = WyckoffAccumulationStrategy()
        # Mock accum_dist to trigger spring
        ad = inds["accum_dist"]
        ad.state = "accumulation"
        ad.is_squeeze = True
        ad.spring_detected = True

        inds["bias"] = BiasResult(
            mode="intraday",
            htf_tf="4h",
            ltf_tf="1h",
            vp_lookback=60,
            rsi_value=45.0,
            regime="neutral",
            hidden_div="none",
            bias="neutral",
        )

        ctx = StrategyContext(
            ticker="TEST",
            category="crypto",
            mode="intraday",
            current_price=101.0,
            df_htf=df,
            df_ltf=df,
            df_mtf=df,
            df_trigger=df,
            indicators=inds,
        )

        setup = strat.evaluate(ctx)
        if setup:
            pnf = inds["point_and_figure"]
            assert setup.tp1 >= pnf.projected_long_conservative or setup.tp1 >= ad.range_high
            assert setup.tp2 >= setup.tp1
