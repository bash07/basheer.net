"""Bollinger Bands volatility indicators (20 period, 2.0 std)."""
from __future__ import annotations

import pandas as pd
from . import register


@register("bb_mid")
def _bb_mid(df_ltf, df_htf, cache: dict) -> pd.Series:
    """Bollinger Bands Middle Band (20 SMA) on LTF close."""
    if "bb_mid" in df_ltf.columns:
        return df_ltf["bb_mid"]
    return df_ltf["Close"].rolling(20).mean()


@register("bb_upper", deps=["bb_mid"])
def _bb_upper(df_ltf, df_htf, cache: dict) -> pd.Series:
    """Bollinger Bands Upper Band (20 SMA + 2 * std)."""
    if "bb_upper" in df_ltf.columns:
        return df_ltf["bb_upper"]
    std20 = df_ltf["Close"].rolling(20).std()
    return cache["bb_mid"] + (2.0 * std20)


@register("bb_lower", deps=["bb_mid"])
def _bb_lower(df_ltf, df_htf, cache: dict) -> pd.Series:
    """Bollinger Bands Lower Band (20 SMA - 2 * std)."""
    if "bb_lower" in df_ltf.columns:
        return df_ltf["bb_lower"]
    std20 = df_ltf["Close"].rolling(20).std()
    return cache["bb_mid"] - (2.0 * std20)


@register("bb_width", deps=["bb_upper", "bb_lower", "bb_mid"])
def _bb_width(df_ltf, df_htf, cache: dict) -> pd.Series:
    """Bollinger Bandwidth: (Upper - Lower) / Middle."""
    mid = cache["bb_mid"].replace(0, float("nan"))
    return (cache["bb_upper"] - cache["bb_lower"]) / mid


@register("bb_pct_b", deps=["bb_upper", "bb_lower"])
def _bb_pct_b(df_ltf, df_htf, cache: dict) -> pd.Series:
    """Bollinger %B: (Close - Lower) / (Upper - Lower)."""
    denom = (cache["bb_upper"] - cache["bb_lower"]).replace(0, float("nan"))
    return (df_ltf["Close"] - cache["bb_lower"]) / denom
