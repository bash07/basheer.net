"""Unit tests for Accumulation/Distribution indicator and WyckoffAccumulationStrategy."""
from datetime import datetime
import numpy as np
import pandas as pd
import pytest

from modules.finance.trader.indicators.accumulation_distribution import (
    AccumulationDistributionInfo, _accum_dist,
)
from modules.finance.trader.strategies.wyckoff_accumulation import WyckoffAccumulationStrategy
from modules.finance.trader.strategies.base import StrategyContext
from modules.finance.trader.schemas import BiasResult, VolumeProfile, SRLevel


def _build_squeeze_df(n: int = 30, base_price: float = 100.0) -> pd.DataFrame:
    """Build a tight range consolidation DataFrame."""
    dates = pd.date_range(end=datetime(2026, 11, 1), periods=n, freq="1h")
    # Oscillate tightly between 99.0 and 101.0
    close = base_price + np.sin(np.linspace(0, 3 * np.pi, n)) * 0.8
    high = close + 0.3
    low = close - 0.3
    open_p = close - 0.1
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


class TestWyckoffAccumulation:
    def test_accum_dist_indicator_spring_detection(self):
        df = _build_squeeze_df(n=25, base_price=100.0)
        # Create a Spring on the last bar: pierce below 98.5 and close back at 99.8 with lower wick
        range_low = float(df["Low"].iloc[:-1].min())
        df.iloc[-1, df.columns.get_loc("Open")] = 99.5
        df.iloc[-1, df.columns.get_loc("Low")] = range_low - 0.6  # Pierces below
        df.iloc[-1, df.columns.get_loc("High")] = 100.2
        df.iloc[-1, df.columns.get_loc("Close")] = range_low + 0.4 # Reclaims range low

        cache = {
            "atr_14": 0.8,
            "rsi_14": pd.Series(np.linspace(55, 42, len(df)), index=df.index),
            "bb_upper": pd.Series(np.full(len(df), 101.5), index=df.index),
            "bb_lower": pd.Series(np.full(len(df), 98.5), index=df.index),
            "bb_mid": pd.Series(np.full(len(df), 100.0), index=df.index),
        }

        info = _accum_dist(df, df, cache)
        assert isinstance(info, AccumulationDistributionInfo)
        assert info.is_squeeze is True
        assert info.spring_detected is True
        assert info.score > 0

    def test_wyckoff_accumulation_strategy_trigger(self):
        strat = WyckoffAccumulationStrategy()
        assert strat.name == "Wyckoff Accumulation"
        assert strat.strategy_type == "accumulation"
        assert strat.requires_directional_bias is False

        df = _build_squeeze_df(n=25, base_price=100.0)
        range_low = 98.5
        range_high = 101.5

        # Trigger bar: Spring
        df.iloc[-1, df.columns.get_loc("Open")] = 99.5
        df.iloc[-1, df.columns.get_loc("Low")] = 98.0
        df.iloc[-1, df.columns.get_loc("High")] = 100.2
        df.iloc[-1, df.columns.get_loc("Close")] = 99.8

        accum_info = AccumulationDistributionInfo(
            state="accumulation",
            score=45.0,
            is_squeeze=True,
            absorption_divergence=True,
            exhaustion_divergence=False,
            range_high=range_high,
            range_low=range_low,
            range_mid=100.0,
            spring_detected=True,
            upthrust_detected=False,
            obv_slope=0.25,
            ibs_mean=0.58,
            wick_ratio=1.45,
        )

        bias = BiasResult(
            mode="swing", htf_tf="1wk", ltf_tf="1h", vp_lookback=100,
            rsi_value=48.0, regime="neutral", hidden_div="none", bias="neutral"
        )
        vp = VolumeProfile(poc=100.0, vah=101.5, val=98.5)
        sr = [SRLevel(price=98.5, label="Accumulation Support Floor", weight=1.0, source="pivot")]

        indicators = {
            "bias": bias,
            "accum_dist": accum_info,
            "sr_levels": sr,
            "volume_profile": vp,
            "atr_14": 1.0,
            "rsi_14": pd.Series(np.full(len(df), 48.0), index=df.index),
        }

        ctx = StrategyContext(
            ticker="^NSEI",
            category="indian_stock",
            mode="swing",
            current_price=99.8,
            df_htf=df,
            df_ltf=df,
            indicators=indicators,
            df_mtf=df,
            df_trigger=df,
        )
        ctx.bind_strategy(strat)

        setup = strat.evaluate(ctx)
        assert setup is not None
        assert setup.direction == "long"
        assert setup.setup_name == "Wyckoff Accumulation"
        assert setup.stop_loss < 98.5
        assert setup.tp1 > 99.8
        assert setup.rr_ratio >= 1.5
