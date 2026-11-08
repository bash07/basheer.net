"""Unit tests for WyckoffDistributionStrategy."""
from datetime import datetime
import numpy as np
import pandas as pd
import pytest

from modules.finance.trader.indicators.accumulation_distribution import (
    AccumulationDistributionInfo, _accum_dist,
)
from modules.finance.trader.strategies.wyckoff_distribution import WyckoffDistributionStrategy
from modules.finance.trader.strategies.base import StrategyContext
from modules.finance.trader.schemas import BiasResult, VolumeProfile, SRLevel


def _build_distribution_df(n: int = 30, base_price: float = 100.0) -> pd.DataFrame:
    """Build a tight range consolidation DataFrame at highs."""
    dates = pd.date_range(end=datetime(2026, 11, 1), periods=n, freq="1h")
    close = base_price + np.sin(np.linspace(0, 3 * np.pi, n)) * 0.8
    high = close + 0.3
    low = close - 0.3
    open_p = close + 0.1
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


class TestWyckoffDistribution:
    def test_wyckoff_distribution_strategy_utad_trigger(self):
        strat = WyckoffDistributionStrategy()
        assert strat.name == "Wyckoff Distribution"
        assert strat.strategy_type == "distribution"
        assert strat.requires_directional_bias is False

        df = _build_distribution_df(n=25, base_price=100.0)
        range_low = 98.5
        range_high = 101.5

        # Trigger bar: UTAD Upthrust (pierces 102.0, closes at 101.2 with upper wick)
        df.iloc[-1, df.columns.get_loc("Open")] = 100.5
        df.iloc[-1, df.columns.get_loc("Low")] = 100.2
        df.iloc[-1, df.columns.get_loc("High")] = 102.0
        df.iloc[-1, df.columns.get_loc("Close")] = 101.2

        dist_info = AccumulationDistributionInfo(
            state="distribution",
            score=-45.0,
            is_squeeze=True,
            absorption_divergence=False,
            exhaustion_divergence=True,
            range_high=range_high,
            range_low=range_low,
            range_mid=100.0,
            spring_detected=False,
            upthrust_detected=True,
            obv_slope=-0.25,
            ibs_mean=0.42,
            wick_ratio=0.65,
        )

        bias = BiasResult(
            mode="swing", htf_tf="1wk", ltf_tf="1h", vp_lookback=100,
            rsi_value=55.0, regime="neutral", hidden_div="none", bias="neutral"
        )
        vp = VolumeProfile(poc=100.0, vah=101.5, val=98.5)
        sr = [SRLevel(price=101.5, label="Distribution Resistance Ceiling", weight=1.0, source="pivot")]

        indicators = {
            "bias": bias,
            "accum_dist": dist_info,
            "sr_levels": sr,
            "volume_profile": vp,
            "atr_14": 1.0,
            "rsi_14": pd.Series(np.full(len(df), 55.0), index=df.index),
        }

        ctx = StrategyContext(
            ticker="^GSPC",
            category="us_index",
            mode="swing",
            current_price=101.2,
            df_htf=df,
            df_ltf=df,
            indicators=indicators,
            df_mtf=df,
            df_trigger=df,
        )
        ctx.bind_strategy(strat)

        setup = strat.evaluate(ctx)
        assert setup is not None
        assert setup.direction == "short"
        assert setup.setup_name == "Wyckoff Distribution"
        assert setup.stop_loss > 101.5
        assert setup.tp1 < 101.2
        assert setup.rr_ratio >= 1.5
