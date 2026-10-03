"""Setup 2 — Liquidity Sweep + FVG strategy."""
from __future__ import annotations

import logging
from typing import Optional

from .base import BaseStrategy, StrategyContext

logger = logging.getLogger(__name__)

_MIN_RR = 2.0


class LiquiditySweepFVGStrategy(BaseStrategy):
    """EQH/EQL swept + FVG created within 3 bars + price at FVG.

    Gates:
      - bias strong_bull | strong_bear
      - A swept EQL (for longs) or EQH (for shorts) within last 10 bars
      - At least one FVG present after the sweep bar
      - Current price is at or entering the FVG zone
      - Confluence >= MIN_SCORE, R:R >= 2.0
    """

    name = "Liquidity Sweep + FVG"
    description = "Liquidity sweep followed by FVG re-entry"
    setup_type = "continuation"
    priority = 20
    required_indicators = [
        "bias", "fvg_list", "liq_list",
        "volume_profile", "sr_levels", "atr_14",
    ]

    def evaluate(self, ctx: StrategyContext) -> Optional[object]:
        from ..confluence import score_zone
        from ..schemas import TradeSetup
        from ..setups import _MIN_SCORE, _calc_rr, _next_swing_high, _next_swing_low, _next_eqh, _next_eql

        bias = ctx.bias
        if bias.bias not in ("strong_bull", "strong_bear"):
            return None

        direction = "long" if "bull" in bias.bias else "short"
        liq_kind  = "eql" if direction == "long" else "eqh"

        swept = [l for l in ctx.liq_list if l.swept and l.kind == liq_kind]
        if not swept:
            return None

        swept.sort(key=lambda l: l.sweep_bar, reverse=True)
        recent_sweep = swept[0]

        fvg_dir = "bullish" if direction == "long" else "bearish"
        post_sweep_fvgs = [
            f for f in ctx.fvg_list
            if f.bar_index > recent_sweep.sweep_bar and f.direction == fvg_dir
        ]
        if not post_sweep_fvgs:
            return None

        fvg      = post_sweep_fvgs[0]
        half_gap = (fvg.gap_high - fvg.gap_low) / 2
        in_fvg   = (fvg.gap_low - half_gap) <= ctx.current_price <= (fvg.gap_high + half_gap)
        if not in_fvg:
            return None

        report = score_zone(fvg.gap_low, fvg.gap_high, ctx.sr_levels, ctx.volume_profile)
        if report.score < _MIN_SCORE:
            return None

        entry_low  = fvg.gap_low
        entry_high = fvg.gap_high
        entry_mid  = fvg.midpoint
        atr = ctx.atr_14

        if direction == "long":
            stop_loss   = recent_sweep.price - (atr * 0.5)
            tp1         = _next_swing_high(ctx.structure, fvg.bar_index, ctx.df_ltf) or entry_mid * 1.05
            tp2         = _next_eqh(ctx.liq_list, entry_mid)
            invalidation = f"Candle closes below sweep low {recent_sweep.price:,.4f}"
        else:
            stop_loss   = recent_sweep.price + (atr * 0.5)
            tp1         = _next_swing_low(ctx.structure, fvg.bar_index, ctx.df_ltf) or entry_mid * 0.95
            tp2         = _next_eql(ctx.liq_list, entry_mid)
            invalidation = f"Candle closes above sweep high {recent_sweep.price:,.4f}"

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
