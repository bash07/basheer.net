"""EMA indicators — registered into the global indicator registry."""
from __future__ import annotations

from modules.finance._shared.indicators import calc_ema
from . import register


@register("ema_8")
def _ema_8(df_ltf, df_htf, cache: dict):
    """EMA-8 series on LTF close. Short-circuits to pre-computed column if present."""
    if "ema_8" in df_ltf.columns:
        return df_ltf["ema_8"]
    return calc_ema(df_ltf["Close"], span=8)


@register("ema_21")
def _ema_21(df_ltf, df_htf, cache: dict):
    """EMA-21 series on LTF close. Short-circuits to pre-computed column if present."""
    if "ema_21" in df_ltf.columns:
        return df_ltf["ema_21"]
    return calc_ema(df_ltf["Close"], span=21)


@register("ema_50")
def _ema_50(df_ltf, df_htf, cache: dict):
    """EMA-50 series on LTF close. Short-circuits to pre-computed column if present."""
    if "ema_50" in df_ltf.columns:
        return df_ltf["ema_50"]
    return calc_ema(df_ltf["Close"], span=50)


@register("ema_200")
def _ema_200(df_ltf, df_htf, cache: dict):
    """EMA-200 series on LTF close. Short-circuits to pre-computed column if present."""
    if "ema_200" in df_ltf.columns:
        return df_ltf["ema_200"]
    return calc_ema(df_ltf["Close"], span=200)

