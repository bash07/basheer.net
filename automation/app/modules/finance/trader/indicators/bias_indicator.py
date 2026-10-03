"""HTF Bias indicators.

Wraps bias.py's compute_htf_bias() and registers specific bias indicators:
- ``bias``: Flexible bias, reads ``_bias_provider`` from cache (default "rsi").
- ``bias_rsi``: Specifically evaluates Cardwell RSI regime + hidden divergence.
- ``bias_macd``: Evaluates HTF MACD trend & momentum.
- ``bias_ema``: Evaluates HTF EMA 20/50/200 trend alignment.
- ``bias_composite``: Evaluates multi-indicator consensus.
"""
from __future__ import annotations

from . import register


@register("bias")
def _bias(df_ltf, df_htf, cache: dict):
    """HTF directional bias (BiasResult)."""
    from ..bias import compute_htf_bias
    mode     = cache.get("_mode", "swing")
    provider = cache.get("_bias_provider", "rsi")
    return compute_htf_bias(df_htf, mode, provider=provider)


@register("bias_rsi")
def _bias_rsi(df_ltf, df_htf, cache: dict):
    """RSI regime + hidden divergence HTF bias."""
    from ..bias import compute_htf_bias
    mode = cache.get("_mode", "swing")
    return compute_htf_bias(df_htf, mode, provider="rsi")


@register("bias_macd")
def _bias_macd(df_ltf, df_htf, cache: dict):
    """MACD trend & momentum HTF bias."""
    from ..bias import compute_htf_bias
    mode = cache.get("_mode", "swing")
    return compute_htf_bias(df_htf, mode, provider="macd")


@register("bias_ema")
def _bias_ema(df_ltf, df_htf, cache: dict):
    """EMA 20/50/200 stack alignment HTF bias."""
    from ..bias import compute_htf_bias
    mode = cache.get("_mode", "swing")
    return compute_htf_bias(df_htf, mode, provider="ema")


@register("bias_composite")
def _bias_composite(df_ltf, df_htf, cache: dict):
    """Composite consensus HTF bias."""
    from ..bias import compute_htf_bias
    mode = cache.get("_mode", "swing")
    return compute_htf_bias(df_htf, mode, provider="composite")
