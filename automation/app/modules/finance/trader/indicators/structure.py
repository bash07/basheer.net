"""SMC / ICT market structure indicators.

Wraps scanner.py functions and registers them in the indicator registry with
their proper dependency chain:

    structure → bos_list → ob_list
    fvg_list  (independent)
    liq_list  (independent)
"""
from __future__ import annotations

from . import register


@register("structure")
def _structure(df_ltf, df_htf, cache: dict):
    """LTF swing structure: HH/HL/LH/LL labels and trend direction."""
    from ..scanner import detect_structure
    return detect_structure(df_ltf)


@register("bos_list", deps=["structure"])
def _bos_list(df_ltf, df_htf, cache: dict):
    """Break of Structure / Change of Character events on the LTF chart."""
    from ..scanner import detect_bos_choch
    return detect_bos_choch(df_ltf, cache["structure"])


@register("ob_list", deps=["bos_list"])
def _ob_list(df_ltf, df_htf, cache: dict):
    """Active (non-invalidated) Order Blocks associated with each BOS."""
    from ..scanner import find_order_blocks
    return find_order_blocks(df_ltf, cache["bos_list"])


@register("fvg_list")
def _fvg_list(df_ltf, df_htf, cache: dict):
    """Fair Value Gaps (3-candle imbalances) on the LTF chart."""
    from ..scanner import find_fvg
    return find_fvg(df_ltf)


@register("liq_list")
def _liq_list(df_ltf, df_htf, cache: dict):
    """Equal Highs / Equal Lows liquidity levels on the LTF chart."""
    from ..scanner import find_eqh_eql
    return find_eqh_eql(df_ltf)
