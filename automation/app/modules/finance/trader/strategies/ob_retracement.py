"""Setup 1 — OB Retracement strategy."""
from __future__ import annotations

import logging
from typing import Optional

from .base import BaseStrategy, StrategyContext

logger = logging.getLogger(__name__)

_MIN_RR = 1.5


class OBRetracementStrategy(BaseStrategy):
    """BOS confirmed + active Order Block + price retracing into OB zone.

    Gates:
      - bias must be strong_bull | strong_bear
      - A BOS must exist in the direction of bias
      - An active (non-invalidated) OB must exist aligned with bias
      - Current price must be within the OB zone (±10% of zone size)
      - Confluence score >= MIN_SCORE
      - R:R >= 1.5
    """

    name = "OB Retracement"
    description = "BOS + Order Block retracement entry"
    setup_type = "continuation"
    priority = 10

    def evaluate(self, ctx: StrategyContext) -> Optional[object]:
        from ..confluence import score_zone
        from ..schemas import TradeSetup
        from ..setups import _MIN_SCORE, _calc_rr, _get_atr, _next_swing_high, _next_swing_low, _next_eqh, _next_eql

        bias = ctx.bias
        if bias.bias not in ("strong_bull", "strong_bear"):
            return None

        direction = "long" if "bull" in bias.bias else "short"
        bos_dir   = "bullish" if direction == "long" else "bearish"

        aligned_bos = [b for b in ctx.bos_list if b.direction == bos_dir]
        if not aligned_bos:
            return None

        aligned_obs = [ob for ob in ctx.ob_list if ob.direction == bos_dir]
        if not aligned_obs:
            return None

        ob = aligned_obs[0]
        zone_size    = ob.zone_high - ob.zone_low
        in_zone_low  = ob.zone_low  - zone_size * 0.10
        in_zone_high = ob.zone_high + zone_size * 0.10

        if not (in_zone_low <= ctx.current_price <= in_zone_high):
            logger.debug("[%s] OB Retracement: price %.4f not in OB zone",
                         ctx.ticker, ctx.current_price)
            return None

        report = score_zone(ob.zone_low, ob.zone_high, ctx.sr_levels, ctx.volume_profile)
        if report.score < _MIN_SCORE:
            return None

        entry_low  = ob.zone_low
        entry_high = ob.zone_high
        entry_mid  = ob.midpoint
        atr = _get_atr(ctx.df_ltf)

        if direction == "long":
            stop_loss   = ob.zone_low - (atr * 0.5)
            tp1         = _next_swing_high(ctx.structure, ob.bar_index, ctx.df_ltf) or entry_mid * 1.04
            tp2         = _next_eqh(ctx.liq_list, entry_mid)
            invalidation = f"Candle closes below {ob.zone_low:,.4f} (OB low)"
        else:
            stop_loss   = ob.zone_high + (atr * 0.5)
            tp1         = _next_swing_low(ctx.structure, ob.bar_index, ctx.df_ltf) or entry_mid * 0.96
            tp2         = _next_eql(ctx.liq_list, entry_mid)
            invalidation = f"Candle closes above {ob.zone_high:,.4f} (OB high)"

        rr = _calc_rr(entry_mid, stop_loss, tp1)
        if rr < _MIN_RR:
            return None

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
