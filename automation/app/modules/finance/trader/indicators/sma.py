"""SMA (Simple Moving Average) indicators."""
from __future__ import annotations

import pandas as pd
from . import register


@register("sma_20")
def _sma_20(df_ltf, df_htf, cache: dict) -> pd.Series:
    """SMA-20 on LTF close."""
    if "sma_20" in df_ltf.columns:
        return df_ltf["sma_20"]
    return df_ltf["Close"].rolling(20).mean()


@register("sma_50")
def _sma_50(df_ltf, df_htf, cache: dict) -> pd.Series:
    """SMA-50 on LTF close."""
    if "sma_50" in df_ltf.columns:
        return df_ltf["sma_50"]
    return df_ltf["Close"].rolling(50).mean()


@register("sma_200")
def _sma_200(df_ltf, df_htf, cache: dict) -> pd.Series:
    """SMA-200 on LTF close."""
    if "sma_200" in df_ltf.columns:
        return df_ltf["sma_200"]
    return df_ltf["Close"].rolling(200).mean()
