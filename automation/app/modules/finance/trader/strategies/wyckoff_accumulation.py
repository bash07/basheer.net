"""Wyckoff Accumulation & Supply Absorption Strategy (Regime 5 Squeeze).

Captures institutional absorption coils, false momentum divergences, Wyckoff
Springs (shakeouts), and Breakouts from tight compression ranges.

Tri-Tier Architecture Mapping:
- Tier 1 (Where): HTF & MTF range boundaries, POC/Value Area, and Volatility Squeeze.
- Tier 2 (When): Volume absorption divergence (OBV / IBS / Wick Ratio) proving supply
  depletion despite apparent "bearish divergence" on RSI.
- Tier 3 (How): Trigger on either:
  1. Wyckoff Spring / UTAD wick rejection reclaiming range boundaries.
  2. Sign of Strength (SOS) / Sign of Weakness (SOW) breakout closing outside range.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..confluence import score_zone
from ..schemas import TradeSetup
from .base import BaseStrategy, StrategyContext

logger = logging.getLogger(__name__)

_MIN_RR = 1.5
_MIN_WICK_RATIO = 0.30


class WyckoffAccumulationStrategy(BaseStrategy):
    """Institutional Wyckoff Accumulation and Distribution Strategy."""

    name = "Wyckoff Accumulation"
    strategy_type = "accumulation"
    priority = 35
    requires_directional_bias = False  # Resolves directional bias internally from order-flow absorption

    required_indicators = [
        "accum_dist",
        "atr_14",
        "rsi_14",
        "sr_levels",
        "volume_profile",
    ]

    def evaluate(self, ctx: StrategyContext) -> Optional[TradeSetup]:
        """Evaluate market data for Wyckoff accumulation springs or breakouts."""
        df_trigger = ctx.df_trigger
        if df_trigger is None or len(df_trigger) < 15:
            return None

        accum_dist = ctx.indicators.get("accum_dist")
        if accum_dist is None or not accum_dist.is_squeeze:
            return None

        # State must be clearly accumulation
        if accum_dist.state != "accumulation":
            return None

        direction = "long"
        current_price = ctx.current_price
        atr = ctx.atr_14 or 1e-4

        range_high = accum_dist.range_high
        range_low  = accum_dist.range_low
        range_span = range_high - range_low
        min_sl = ctx.get_min_sl_distance(current_price, atr)

        # ── Execution Trigger Identification ─────────────────────────────────
        trigger_bar = None
        trigger_type = ""

        last_bar = df_trigger.iloc[-1]
        last_c   = float(last_bar["Close"])
        last_h   = float(last_bar["High"])
        last_l   = float(last_bar["Low"])
        last_o   = float(last_bar["Open"])
        last_rng = max(last_h - last_l, 1e-6)

        # Trigger A: Wyckoff Spring (false breakdown reclaiming range low with rejection wick)
        if accum_dist.spring_detected:
            trigger_type = "spring"
            trigger_bar = last_bar
        # Trigger B: Absorption Breakout (Sign of Strength)
        # Price closes above range_high with absorption divergence confirmation
        elif last_c > range_high and (accum_dist.absorption_divergence or accum_dist.score >= 35.0):
            trigger_type = "breakout"
            trigger_bar = last_bar

        if trigger_bar is None:
            return None

        # ── Entry, Stop Loss, and Take Profit Computation ────────────────────
        trig_l = float(trigger_bar["Low"])

        entry_low  = current_price - (0.2 * atr)
        entry_high = current_price
        entry_mid  = (entry_low + entry_high) / 2.0

        # SL placed below the spring low or below range_low
        base_sl = min(trig_l, range_low) - (0.25 * atr)
        stop_loss = min(base_sl, entry_mid - min_sl)
        risk = entry_mid - stop_loss

        if risk <= 0:
            return None

        # TP1: Range high or 1.8R
        tp1 = max(entry_mid + (1.8 * risk), range_high)
        # TP2: 2.8R expansion markup target
        tp2 = entry_mid + (2.8 * risk)

        rr_ratio = abs(tp1 - entry_mid) / risk
        if rr_ratio < _MIN_RR:
            return None

        # ── Confluence Scoring ────────────────────────────────────────────────
        zone_l = min(current_price, range_low)
        zone_h = max(current_price, range_high)
        report = score_zone(zone_l, zone_h, ctx.sr_levels, ctx.volume_profile)

        # Baseline institutional setup viability requires S/R or volume support
        if report.score < 2.5:
            return None

        invalidation_note = (
            f"Wyckoff {trigger_type.upper()} ({accum_dist.state}). "
            f"Invalid if price closes beyond SL at {stop_loss:.2f}"
        )

        return TradeSetup(
            ticker=ctx.ticker,
            category=ctx.category,
            mode=ctx.mode,
            setup_name=self.name,
            setup_type=self.strategy_type,
            direction=direction,
            bias=ctx.bias,
            confluence=report,
            entry_low=round(entry_low, 4),
            entry_high=round(entry_high, 4),
            stop_loss=round(stop_loss, 4),
            tp1=round(tp1, 4),
            tp2=round(tp2, 4) if tp2 is not None else None,
            rr_ratio=round(rr_ratio, 2),
            invalidation_note=invalidation_note,
        )
