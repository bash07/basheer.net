"""Setup 3 — Displacement + FVG Re-entry strategy."""
from __future__ import annotations

import logging
from typing import Optional

from .base import BaseStrategy, StrategyContext

logger = logging.getLogger(__name__)

_MIN_RR = 1.5


class DisplacementFVGStrategy(BaseStrategy):
    """Large displacement candle + FVG + price retracing to FVG 50%.

    Gates:
      - bias strong OR moderate (widest gate)
      - FVG present with displacement_ratio >= 1.2
      - FVG direction aligns with bias
      - Price within ±20% of FVG midpoint
      - Confluence >= MIN_SCORE, R:R >= 1.5
    """

    name = "Displacement + FVG"
    description = "Strong displacement candle followed by FVG fill"
    setup_type = "continuation"
    priority = 30

    def evaluate(self, ctx: StrategyContext) -> Optional[object]:
        from ..confluence import score_zone
        from ..schemas import TradeSetup, ConfluenceReport
        from ..setups import _MIN_SCORE, _calc_rr, _get_atr, _next_swing_high, _next_swing_low

        bias = ctx.bias
        if bias.bias == "neutral":
            return None

        direction = "long" if "bull" in bias.bias else "short"
        fvg_dir   = "bullish" if direction == "long" else "bearish"

        aligned_fvgs = [f for f in ctx.fvg_list if f.direction == fvg_dir]
        if not aligned_fvgs:
            return None

        fvg      = aligned_fvgs[0]
        fvg_mid  = fvg.midpoint
        half_gap = (fvg.gap_high - fvg.gap_low) / 2 * 1.2

        if not (fvg_mid - half_gap <= ctx.current_price <= fvg_mid + half_gap):
            return None

        report = score_zone(fvg.gap_low, fvg.gap_high, ctx.sr_levels, ctx.volume_profile)
        if report.score < _MIN_SCORE:
            return None

        entry_low  = fvg.gap_low
        entry_high = fvg.gap_high
        entry_mid  = fvg.midpoint
        atr = _get_atr(ctx.df_ltf)

        if direction == "long":
            stop_loss   = fvg.gap_low - (atr * 0.5)
            tp1         = _next_swing_high(ctx.structure, fvg.bar_index, ctx.df_ltf) or entry_mid * 1.04
            tp2         = None
            invalidation = f"Candle closes below FVG low {fvg.gap_low:,.4f}"
        else:
            stop_loss   = fvg.gap_high + (atr * 0.5)
            tp1         = _next_swing_low(ctx.structure, fvg.bar_index, ctx.df_ltf) or entry_mid * 0.96
            tp2         = None
            invalidation = f"Candle closes above FVG high {fvg.gap_high:,.4f}"

        rr = _calc_rr(entry_mid, stop_loss, tp1)
        if rr < _MIN_RR:
            return None

        # Cap confidence at medium for moderate bias
        if "moderate" in bias.bias and report.label == "high":
            report = ConfluenceReport(
                score=report.score, sr_hits=report.sr_hits, vp_hits=report.vp_hits,
                poc=report.poc, vah=report.vah, val=report.val,
                label="medium",
            )

        return TradeSetup(
            ticker=ctx.ticker, category=ctx.category, mode=ctx.mode,
            setup_name=self.name, setup_type=self.setup_type,
            direction=direction, bias=bias, confluence=report,
            entry_low=entry_low, entry_high=entry_high,
            stop_loss=stop_loss, tp1=tp1, tp2=tp2,
            rr_ratio=round(rr, 2),
            invalidation_note=invalidation,
            ltf_tf=bias.ltf_tf, htf_tf=bias.htf_tf,
        )
