"""MACD (Moving Average Convergence Divergence) indicators."""
from __future__ import annotations

import pandas as pd
from . import register


@register("macd")
def _macd(df_ltf, df_htf, cache: dict) -> pd.Series:
    """MACD Line (12 EMA - 26 EMA) on LTF close."""
    if "macd" in df_ltf.columns:
        return df_ltf["macd"]
    close = df_ltf["Close"]
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    return ema12 - ema26


@register("macd_signal", deps=["macd"])
def _macd_signal(df_ltf, df_htf, cache: dict) -> pd.Series:
    """MACD Signal Line (9 EMA of MACD line) on LTF close."""
    if "macd_signal" in df_ltf.columns:
        return df_ltf["macd_signal"]
    macd_line = cache["macd"]
    return macd_line.ewm(span=9, adjust=False).mean()


@register("macd_hist", deps=["macd", "macd_signal"])
def _macd_hist(df_ltf, df_htf, cache: dict) -> pd.Series:
    """MACD Histogram (MACD line - Signal line) on LTF close."""
    if "macd_hist" in df_ltf.columns:
        return df_ltf["macd_hist"]
    return cache["macd"] - cache["macd_signal"]
