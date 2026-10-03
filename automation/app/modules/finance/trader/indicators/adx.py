"""ADX (Average Directional Index) trend strength indicators."""
from __future__ import annotations

import numpy as np
import pandas as pd
from . import register


def _calc_dmi(df_ltf, period: int = 14):
    """Compute +DI, -DI, and DX series."""
    h = df_ltf["High"]
    l = df_ltf["Low"]
    c = df_ltf["Close"]

    up_move = h.diff()
    down_move = -l.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)

    # Wilder smoothing via ewm(com=period-1)
    tr_smooth = pd.Series(tr, index=df_ltf.index).ewm(com=period - 1, min_periods=period).mean()
    plus_dm_smooth = pd.Series(plus_dm, index=df_ltf.index).ewm(com=period - 1, min_periods=period).mean()
    minus_dm_smooth = pd.Series(minus_dm, index=df_ltf.index).ewm(com=period - 1, min_periods=period).mean()

    plus_di = 100 * (plus_dm_smooth / tr_smooth.replace(0, np.nan))
    minus_di = 100 * (minus_dm_smooth / tr_smooth.replace(0, np.nan))

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return plus_di.fillna(0), minus_di.fillna(0), dx.fillna(0)


@register("adx_series")
def _adx_series(df_ltf, df_htf, cache: dict) -> pd.Series:
    """ADX-14 series on LTF chart."""
    if "adx_series" in df_ltf.columns:
        return df_ltf["adx_series"]
    _, _, dx = _calc_dmi(df_ltf, 14)
    return dx.ewm(com=13, min_periods=14).mean()


@register("adx", deps=["adx_series"])
def _adx(df_ltf, df_htf, cache: dict) -> float:
    """ADX-14 scalar (latest bar). >25 indicates a strong trend."""
    series = cache["adx_series"]
    return float(series.iloc[-1]) if len(series) and not pd.isna(series.iloc[-1]) else 0.0


@register("di_plus")
def _di_plus(df_ltf, df_htf, cache: dict) -> pd.Series:
    """+DI (Plus Directional Indicator) series."""
    p_di, _, _ = _calc_dmi(df_ltf, 14)
    return p_di


@register("di_minus")
def _di_minus(df_ltf, df_htf, cache: dict) -> pd.Series:
    """-DI (Minus Directional Indicator) series."""
    _, m_di, _ = _calc_dmi(df_ltf, 14)
    return m_di
