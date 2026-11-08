"""Setup — Creeping Drift / Momentum Trail (Regime 7).

Captures persistent, low-volatility grinding trends (e.g. Indian market melt-ups like NIFTY 50)
where deep 50% pullbacks never fill and traditional reversal setups fail.

Institutional 3-Tier Multi-Timeframe Architecture:
  - Gate 1: Regime 7 Identification (Persistent EMA 20/50 slope, ADX 15-32, orderly drift).
  - Gate 2: Ribbon Defense (Price respecting EMA 8/21 moving average ribbon without breakdown).
  - Gate 3: Trigger Execution (Shallow EMA 21 re-test rejection or consolidation breakout).
"""
from __future__ import annotations

import logging
from typing import Optional

from .base import BaseStrategy, StrategyContext

logger = logging.getLogger(__name__)

_MIN_RR = 1.5
_MIN_WICK_RATIO = 0.30
_MIN_ADX = 14.0
_MAX_ADX = 35.0  # Above 35 is parabolic blow-off / climax, not creeping drift


class CreepingDriftStrategy(BaseStrategy):
    """Trend-following strategy for persistent creeping drift / melt-up regimes."""

    name = "Creeping Drift"
    description = "Captures persistent low-volatility trends riding the EMA 8/21 ribbon"
    setup_type = "trend"
    priority = 45
    requires_directional_bias = True
    supported_styles = ["swing", "intraday"]
    required_indicators = [
        "bias",
        "structure",
        "ema_8",
        "ema_21",
        "ema_50",
        "adx",
        "atr_14",
        "rsi_14",
        "sr_levels",
        "volume_profile",
    ]

    def evaluate(self, ctx: StrategyContext) -> Optional[object]:
        from ..confluence import score_zone
        from ..schemas import TradeSetup, ConfluenceReport
        from ..setups import _calc_rr, _next_swing_high, _next_swing_low

        df_trigger = ctx.df_trigger if ctx.df_trigger is not None else ctx.df_ltf
        if df_trigger is None or len(df_trigger) < 15:
            return None

        atr = ctx.atr_14
        if atr <= 0:
            return None

        adx_val = ctx.adx if ctx.adx is not None else 20.0
        # Gate 1: Trend momentum must be in steady drift zone (not flat chop < 14, not parabolic climax > 35)
        if not (_MIN_ADX <= adx_val <= _MAX_ADX):
            return None

        bias = ctx.bias
        if bias is None or bias.bias == "neutral":
            return None

        direction = "long" if "bull" in bias.bias else "short"

        # Check EMA Ribbon on trigger/LTF
        close = df_trigger["Close"]
        if len(close) < 25:
            return None

        e8 = close.ewm(span=8, adjust=False).mean()
        e21 = close.ewm(span=21, adjust=False).mean()
        e50 = close.ewm(span=50, adjust=False).mean() if len(close) >= 50 else e21

        e8_now = float(e8.iloc[-1])
        e21_now = float(e21.iloc[-1])
        e50_now = float(e50.iloc[-1])
        e21_prev = float(e21.iloc[-5]) if len(e21) >= 6 else e21_now

        current_price = ctx.current_price

        # ── Gate 1 & 2: Ribbon Alignment & Slope ──────────────────────────────
        if direction == "long":
            # Bullish creeping drift: Price > EMA 21 >= EMA 50, EMA 21 sloping upward
            if not (e8_now >= e21_now >= e50_now and e21_now >= e21_prev):
                return None
            # Price must be holding above or near EMA 21 (not deep breakdown)
            if current_price < (e21_now - 0.75 * atr):
                return None
            # Reject if too detached from EMA 21 (> 3.5 ATR blow-off)
            if current_price > (e21_now + 3.5 * atr):
                return None
        else:
            # Bearish creeping drift: Price < EMA 21 <= EMA 50, EMA 21 sloping downward
            if not (e8_now <= e21_now <= e50_now and e21_now <= e21_prev):
                return None
            if current_price > (e21_now + 0.75 * atr):
                return None
            if current_price < (e21_now - 3.5 * atr):
                return None

        # ── Gate 3: Trigger Execution (Shallow Ribbon Touch or Consolidation Break) ─
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

            if direction == "long":
                # Rejection wick off EMA ribbon (EMA 8/21), or solid close near highs
                wick = min(bar_o, bar_c) - bar_l
                touched_ribbon = bar_l <= (e8_now + 0.5 * atr)
                if touched_ribbon and (wick / rng >= _MIN_WICK_RATIO or bar_c > e8_now):
                    trigger_bar = bar
                    break
            else:
                wick = bar_h - max(bar_o, bar_c)
                touched_ribbon = bar_h >= (e8_now - 0.5 * atr)
                if touched_ribbon and (wick / rng >= _MIN_WICK_RATIO or bar_c < e8_now):
                    trigger_bar = bar
                    break

        if trigger_bar is None:
            return None

        # Confluence Check
        zone_low = min(current_price, e21_now)
        zone_high = max(current_price, e21_now)
        report = score_zone(zone_low, zone_high, ctx.sr_levels, ctx.volume_profile)

        # Confluence Check (minimum score 3.0)
        if report.score < 3.0:
            return None

        if direction == "long":
            # SL anchored below EMA 21 / trigger bar low with ATR buffer
            stop_loss = min(float(trigger_bar["Low"]), e21_now) - (0.4 * atr)
            sl_dist = abs(current_price - stop_loss)
            if sl_dist < (0.3 * atr) or sl_dist > (3.5 * atr):
                return None
            tp1 = _next_swing_high(ctx.structure, len(df_trigger) - 1, df_trigger) or (current_price + 1.8 * sl_dist)
            tp2 = current_price + 3.0 * sl_dist
            invalidation = f"Candle closes below EMA 21 ribbon ({e21_now:,.4f})"
        else:
            stop_loss = max(float(trigger_bar["High"]), e21_now) + (0.4 * atr)
            sl_dist = abs(current_price - stop_loss)
            if sl_dist < (0.3 * atr) or sl_dist > (3.5 * atr):
                return None
            tp1 = _next_swing_low(ctx.structure, len(df_trigger) - 1, df_trigger) or (current_price - 1.8 * sl_dist)
            tp2 = current_price - 3.0 * sl_dist
            invalidation = f"Candle closes above EMA 21 ribbon ({e21_now:,.4f})"

        rr = _calc_rr(current_price, stop_loss, tp1)
        if rr < _MIN_RR:
            return None

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
            entry_low=round(zone_low, 4),
            entry_high=round(zone_high, 4),
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
