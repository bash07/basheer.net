"""Volume Profile and S/R level indicators.

Both indicators use ``_mode`` from the cache (seeded by the caller) to
determine the correct VP lookback window per scan mode.
"""
from __future__ import annotations

from . import register


@register("volume_profile")
def _volume_profile(df_ltf, df_htf, cache: dict):
    """Fixed-Range Volume Profile (POC, VAH, VAL, HVN, LVN).

    The lookback window is determined by ``_mode`` in the cache
    (set by compute_indicators' initial_cache parameter).
    """
    from ..confluence import compute_volume_profile
    from ..schemas import timeframes_for
    mode = cache.get("_mode", "swing")
    _, _, vp_lookback = timeframes_for(mode)
    return compute_volume_profile(df_ltf, lookback=vp_lookback)


@register("sr_levels")
def _sr_levels(df_ltf, df_htf, cache: dict):
    """Classical S/R levels: swing pivots, PDH/PDL, EMAs, round numbers."""
    from ..confluence import get_sr_levels
    current_price = float(df_ltf["Close"].iloc[-1])
    return get_sr_levels(df_ltf, df_htf, current_price)
