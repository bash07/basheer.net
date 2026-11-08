"""Setup — Breakdown & Retest strategy.

Captures market structure transitions and trend continuation by trading the retest of
broken structural levels (support turned resistance for breakdowns, resistance turned
support for breakouts). Specifically engineered to capture bearish rollovers and distributions.

Institutional 3-Tier Multi-Timeframe Architecture:
  - Gate 1: Macro Structure Break (Bearish BOS/CHoCH breaking prior swing low).
  - Gate 2: Retest Zone Verification (Price pulling back to broken level +- ATR tolerance).
  - Gate 3: Trigger Execution (Rejection wick showing absorption/failure to reclaim level).
"""
from __future__ import annotations

import logging
from typing import Optional

from .base import BaseStrategy, StrategyContext

logger = logging.getLogger(__name__)

_MIN_RR = 1.5
_MIN_WICK_RATIO = 0.30       # Rejection wick >= 30% of candle range
_MAX_RETEST_ATR = 1.0       # Retest proximity within 1.0x ATR of broken level
_MAX_SL_DIST_ATR = 2.8      # Reject wide stops


class BreakdownRetestStrategy(BaseStrategy):
    """Breakdown / Breakout & Retest of structural swing levels."""

    name = "Breakdown Retest"
    description = "Trades the retest of broken structural levels following BOS/CHoCH"
    setup_type = "breakdown"
    priority = 25
    requires_directional_bias = False
    supported_styles = ["swing", "intraday", "scalping"]
    required_indicators = [
        "bias",
        "structure",
        "bos_list",
        "sr_levels",
        "volume_profile",
        "atr_14",
        "adx",
    ]

    def evaluate(self, ctx: StrategyContext) -> Optional[object]:
        from ..confluence import score_zone
        from ..schemas import TradeSetup, ConfluenceReport
        from ..setups import _calc_rr, _next_swing_low, _next_swing_high

        df_trigger = ctx.df_trigger if ctx.df_trigger is not None else ctx.df_ltf
        if df_trigger is None or len(df_trigger) < 5:
            return None

        atr = ctx.atr_14
        if atr <= 0:
            return None

        bias = ctx.bias
        current_price = ctx.current_price
        bos_list = ctx.bos_list or []

        if not bos_list:
            return None

        # Sort recent BOS events by bar index (most recent first)
        sorted_bos = sorted(bos_list, key=lambda b: getattr(b, "bar_index", 0), reverse=True)

        for bos in sorted_bos[:4]:
            direction = "short" if bos.direction == "bearish" else "long"

            # Filter against strong contradictory macro trend
            if bias is not None:
                if direction == "short" and bias.bias == "strong_bull":
                    continue
                if direction == "long" and bias.bias == "strong_bear":
                    continue

            level = getattr(bos, "level_broken", getattr(bos, "level", 0.0))
            if level <= 0:
                continue
            # ── Gate 2: Retest Proximity ──────────────────────────────────────────
            # Price must be retesting the broken level
            dist_to_level = abs(current_price - level)
            if dist_to_level > (_MAX_RETEST_ATR * atr):
                continue

            # For short: price broke below level, now pulling up to test level from below
            # For long: price broke above level, now pulling down to test level from above
            if direction == "short":
                # Price should not have deeply reclaimed above the broken level (> 0.75 ATR)
                if current_price > (level + 0.75 * atr):
                    continue
            else:
                if current_price < (level - 0.75 * atr):
                    continue

            # ── Gate 3: Trigger Execution (Rejection Wick / Failure to Reclaim) ────
            trigger_bar = None
            lookback = min(3, len(df_trigger))
            for i in range(1, lookback + 1):
                bar = df_trigger.iloc[-i]
                bar_h = float(bar["High"])
                bar_l = float(bar["Low"])
                bar_o = float(bar["Open"])
                bar_c = float(bar["Close"])
                rng = bar_h - bar_l
                if rng <= 0:
                    continue

                if direction == "short":
                    # Upper wick showing rejection at or near level
                    upper_wick = bar_h - max(bar_o, bar_c)
                    if (upper_wick / rng) >= _MIN_WICK_RATIO and bar_c <= (level + 0.3 * atr):
                        trigger_bar = bar
                        break
                else:
                    # Lower wick showing rejection at or near level
                    lower_wick = min(bar_o, bar_c) - bar_l
                    if (lower_wick / rng) >= _MIN_WICK_RATIO and bar_c >= (level - 0.3 * atr):
                        trigger_bar = bar
                        break

            if trigger_bar is None:
                continue

            # ── Confluence & Risk/Reward ───────────────────────────────────────────
            zone_low = min(level, current_price)
            zone_high = max(level, current_price)
            report = score_zone(zone_low, zone_high, ctx.sr_levels, ctx.volume_profile)

            # Confluence check (S/R and Volume Profile)
            if report.score < 3.0:
                continue

            trig_high = float(trigger_bar["High"])
            trig_low = float(trigger_bar["Low"])

            if direction == "short":
                stop_loss = max(trig_high, level) + (0.35 * atr)
                sl_dist = abs(current_price - stop_loss)
                if sl_dist > (_MAX_SL_DIST_ATR * atr) or sl_dist < (0.2 * atr):
                    continue
                # Next structural swing low or 2R default target
                tp1 = _next_swing_low(ctx.structure, bos.bar_index, df_trigger) or (current_price - 2.0 * sl_dist)
                tp2 = current_price - 3.5 * sl_dist if tp1 > (current_price - 2.0 * sl_dist) else None
                invalidation = f"Candle closes above retest resistance {level:,.4f}"
            else:
                stop_loss = min(trig_low, level) - (0.35 * atr)
                sl_dist = abs(current_price - stop_loss)
                if sl_dist > (_MAX_SL_DIST_ATR * atr) or sl_dist < (0.2 * atr):
                    continue
                tp1 = _next_swing_high(ctx.structure, bos.bar_index, df_trigger) or (current_price + 2.0 * sl_dist)
                tp2 = current_price + 3.5 * sl_dist if tp1 < (current_price + 2.0 * sl_dist) else None
                invalidation = f"Candle closes below retest support {level:,.4f}"

            rr = _calc_rr(current_price, stop_loss, tp1)
            if rr < _MIN_RR:
                continue

            triad = getattr(ctx, "triad", None)
            return TradeSetup(
                ticker=ctx.ticker,
                category=ctx.category,
                mode=ctx.mode,
                setup_name=self.name,
                direction=direction,
                setup_type=self.setup_type,
                bias=bias,
                confluence=report,
                entry_low=min(current_price, level),
                entry_high=max(current_price, level),
                stop_loss=round(stop_loss, 4),
                tp1=round(tp1, 4),
                tp2=round(tp2, 4) if tp2 else None,
                rr_ratio=round(rr, 2),
                invalidation_note=invalidation,
                ltf_tf=getattr(triad, "ltf", "1h") if triad else "1h",
                htf_tf=getattr(triad, "htf", "1wk") if triad else "1wk",
                mtf_tf=getattr(triad, "mtf", "4h") if triad else "4h",
                trigger_tf=getattr(triad, "trigger", "1h") if triad else "1h",
            )

        return None
