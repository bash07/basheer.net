"""RSI indicators — registered into the global indicator registry."""
from __future__ import annotations

from modules.finance._shared.indicators import calc_rsi
from . import register


@register("rsi_14")
def _rsi_14(df_ltf, df_htf, cache: dict):
    """RSI-14 series using Wilder EWM on LTF close prices.
    Short-circuits to pre-computed column if present (backtest optimisation).
    """
    if "rsi_14" in df_ltf.columns:
        return df_ltf["rsi_14"]
    return calc_rsi(df_ltf["Close"], 14)
