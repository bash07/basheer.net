"""ATR indicator — registered into the global indicator registry."""
from __future__ import annotations

import pandas as pd
from . import register


def calc_atr(df_ltf, period: int = 14) -> float:
    """Calculate ATR scalar (latest bar) for any period."""
    h = df_ltf["High"]
    l = df_ltf["Low"]
    c = df_ltf["Close"]

    if len(df_ltf) < period:
        return float((h.iloc[-1] - l.iloc[-1]) * 0.5)

    tr = pd.concat(
        [h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()],
        axis=1,
    ).max(axis=1)

    result = tr.rolling(period).mean().iloc[-1]
    return float(result) if result == result else float((h.iloc[-1] - l.iloc[-1]) * 0.5)


@register("atr_14")
def _atr_14(df_ltf, df_htf, cache: dict) -> float:
    """ATR-14 as a scalar float (last-bar value).

    Short-circuits to the pre-computed column when available (e.g., backtest.py
    injects ``atr_14`` into the DataFrame before the replay loop to avoid
    O(N²) recomputation per bar).
    """
    # ── Short-circuit: use pre-attached column (backtest optimisation) ────────
    if "atr_14" in df_ltf.columns:
        val = df_ltf["atr_14"].iloc[-1]
        try:
            import math
            if not math.isnan(float(val)):
                return float(val)
        except (TypeError, ValueError):
            pass  # fall through to full computation

    return calc_atr(df_ltf, 14)
