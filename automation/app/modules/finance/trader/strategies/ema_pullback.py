"""Setup 4 — EMA Pullback + Hidden Divergence strategy."""
from __future__ import annotations

import logging
from typing import Optional

from .base import BaseStrategy, StrategyContext

logger = logging.getLogger(__name__)

_MIN_RR = 1.5


class EMAPullbackStrategy(BaseStrategy):
    """HTF Hidden Divergence bias + LTF EMA 8/21 pullback/cross entry.

    Entry:
      LONG:  Last bar LOW <= EMA21 AND close > EMA21 (rejection wick),
             OR EMA8 crossed above EMA21 in last 5 bars.
      SHORT: Last bar HIGH >= EMA21 AND close < EMA21,
             OR EMA8 crossed below EMA21 in last 5 bars.

    SL: Previous structural swing (+ 0.25 ATR buffer). Rejected if > 3×ATR.
    TP: Next two structural swings.
    """

    name = "EMA Pullback + Hidden Div"
    description = "EMA 8/21 pullback with hidden divergence HTF bias"
    setup_type = "continuation"
    priority = 40
    required_indicators = [
        "bias", "structure", "ema_8", "ema_21", "ema_50", "adx",
        "atr_14", "volume_profile", "sr_levels",
    ]

    def evaluate(self, ctx: StrategyContext) -> Optional[object]:
        from ..confluence import score_zone
        from ..schemas import TradeSetup, ConfluenceReport
        from ..setups import (
            _MIN_SCORE, _calc_rr,
            _ema_pullback_triggered, _prev_swing_low_price, _prev_swing_high_price,
            _next_two_swing_highs, _next_two_swing_lows,
        )

        bias = ctx.bias
        if bias.bias == "neutral":
            return None

        if len(ctx.df_ltf) < 30:
            return None

        direction = "long" if "bull" in bias.bias else "short"

        # ── Edge Case 3: Enforce HTF Hidden Divergence continuation signal ──
        if direction == "long" and bias.hidden_div != "bullish" and bias.bias != "strong_bull":
            logger.debug("[trader.strategy] %s: Long rejected — HTF bullish hidden divergence not confirmed", ctx.ticker)
            return None
        if direction == "short" and bias.hidden_div != "bearish" and bias.bias != "strong_bear":
            logger.debug("[trader.strategy] %s: Short rejected — HTF bearish hidden divergence not confirmed", ctx.ticker)
            return None

        # ── Edge Case 1: Choppy / Flat Market Filters (ADX & EMA Slope / Fan) ──
        adx_val = ctx.adx
        if adx_val is not None and adx_val < 20.0:
            logger.debug("[trader.strategy] %s: Rejected — ADX %.1f < 20.0 indicates choppy / ranging regime", ctx.ticker, adx_val)
            return None

        ema21 = ctx.ema_21
        ema50 = ctx.ema_50
        if ema21 is not None and ema50 is not None and len(ema21) >= 3 and len(ema50) >= 3:
            e21_last = float(ema21.iloc[-1])
            e21_prev = float(ema21.iloc[-3])
            e50_last = float(ema50.iloc[-1])
            if direction == "long":
                if e21_last < e50_last or e21_last < e21_prev:
                    logger.debug("[trader.strategy] %s: Long rejected — EMA 21 < EMA 50 or EMA 21 not sloping upwards", ctx.ticker)
                    return None
            else:
                if e21_last > e50_last or e21_last > e21_prev:
                    logger.debug("[trader.strategy] %s: Short rejected — EMA 21 > EMA 50 or EMA 21 not sloping downwards", ctx.ticker)
                    return None

        ema21_val = float(ctx.ema_21.iloc[-1])
        atr       = ctx.atr_14 or 1.0

        # ── Edge Case 5: Overextension / Parabolic Blowout Filter ───────────
        if abs(ctx.current_price - ema21_val) > atr * 2.5:
            logger.debug("[trader.strategy] %s: Rejected — price overextended from EMA 21 (> 2.5 ATR)", ctx.ticker)
            return None

        # ── Edge Case 2: Deep Piercing & Wick Quality in Rejection Trigger ─
        if not _ema_pullback_triggered(
            ctx.df_ltf, direction,
            ema8=ctx.ema_8, ema21=ctx.ema_21, ema50=ctx.ema_50, atr=atr,
        ):
            return None

        entry_low  = min(ctx.current_price, ema21_val)
        entry_high = max(ctx.current_price, ema21_val)
        entry_mid  = (entry_low + entry_high) / 2

        if direction == "long":
            sl_anchor   = _prev_swing_low_price(ctx.structure, ctx.current_price)
            if sl_anchor is None:
                sl_anchor = entry_mid - (atr * 1.5)
            stop_loss   = sl_anchor - (atr * 0.25)
            invalidation = f"Close below prev swing low {sl_anchor:,.4f}"
        else:
            sl_anchor   = _prev_swing_high_price(ctx.structure, ctx.current_price)
            if sl_anchor is None:
                sl_anchor = entry_mid + (atr * 1.5)
            stop_loss   = sl_anchor + (atr * 0.25)
            invalidation = f"Close above prev swing high {sl_anchor:,.4f}"

        sl_distance = abs(entry_mid - stop_loss)
        if sl_distance > atr * 3:
            return None

        if direction == "long":
            tp1, tp2 = _next_two_swing_highs(ctx.structure, ctx.current_price)
            if tp1 is None:
                tp1 = entry_mid * 1.03
        else:
            tp1, tp2 = _next_two_swing_lows(ctx.structure, ctx.current_price)
            if tp1 is None:
                tp1 = entry_mid * 0.97

        rr = _calc_rr(entry_mid, stop_loss, tp1)
        if rr < _MIN_RR:
            return None

        report = score_zone(entry_low, entry_high, ctx.sr_levels, ctx.volume_profile)
        if report.score < max(0, _MIN_SCORE - 2.0):
            return None

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
