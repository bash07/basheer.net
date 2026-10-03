"""Stochastic Oscillator indicators (%K, %D)."""
from __future__ import annotations

import pandas as pd
from . import register


@register("stoch_k")
def _stoch_k(df_ltf, df_htf, cache: dict, period: int = 14) -> pd.Series:
    """Fast Stochastic %K (14 period) on LTF chart."""
    if "stoch_k" in df_ltf.columns:
        return df_ltf["stoch_k"]
    h = df_ltf["High"].rolling(period).max()
    l = df_ltf["Low"].rolling(period).min()
    c = df_ltf["Close"]
    denom = (h - l).replace(0, float("nan"))
    return (100 * (c - l) / denom).fillna(50.0)


@register("stoch_d", deps=["stoch_k"])
def _stoch_d(df_ltf, df_htf, cache: dict) -> pd.Series:
    """Slow Stochastic %D (3-period SMA of %K)."""
    if "stoch_d" in df_ltf.columns:
        return df_ltf["stoch_d"]
    return cache["stoch_k"].rolling(3).mean().fillna(50.0)
