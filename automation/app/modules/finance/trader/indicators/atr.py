"""ATR indicator — registered into the global indicator registry."""
from __future__ import annotations

from modules.finance._shared.indicators import calc_atr
from . import register


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
