"""RSI indicators — registered into the global indicator registry."""
from __future__ import annotations

from . import register


def calc_rsi(close, period: int = 14):
    """Calculate Wilder RSI series for any period."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("inf"))
    return 100 - (100 / (1 + rs))


@register("rsi_14")
def _rsi_14(df_ltf, df_htf, cache: dict):
    """RSI-14 series using Wilder EWM on LTF close prices.
    Short-circuits to pre-computed column if present (backtest optimisation).
    """
    if "rsi_14" in df_ltf.columns:
        return df_ltf["rsi_14"]
    return calc_rsi(df_ltf["Close"], 14)
