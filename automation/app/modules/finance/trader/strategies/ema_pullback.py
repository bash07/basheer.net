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
    supported_styles = ["swing", "intraday", "scalping"]
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

        # ═════════════════════════════════════════════════════════════════
        # Gate 1: The HTF Setup ("Where" — Trend & Key Zones)
        # ═════════════════════════════════════════════════════════════════
        bias = ctx.bias
        if bias.bias == "neutral":
            return None

        direction = "long" if "bull" in bias.bias else "short"

        # Check HTF key zone / swing equilibrium (Where)
        if ctx.df_htf is not None and len(ctx.df_htf) >= 20:
            htf_high = float(ctx.df_htf["High"].tail(40).max())
            htf_low  = float(ctx.df_htf["Low"].tail(40).min())
            htf_rng  = htf_high - htf_low
            if htf_rng > 0:
                rel_pos = (ctx.current_price - htf_low) / htf_rng
                # In long, price should not be buying at the extreme ceiling (>0.88 of HTF range)
                # In short, price should not be shorting at the extreme floor (<0.12 of HTF range)
                if direction == "long" and rel_pos > 0.88:
                    logger.debug("[trader.strategy] %s: Long rejected — price at extreme HTF premium (%.1f%%)", ctx.ticker, rel_pos * 100)
                    return None
                if direction == "short" and rel_pos < 0.12:
                    logger.debug("[trader.strategy] %s: Short rejected — price at extreme HTF discount (%.1f%%)", ctx.ticker, rel_pos * 100)
                    return None

        # ═════════════════════════════════════════════════════════════════
        # Gate 2: The MTF Confirmation ("When" — Structure & Divergence)
        # ═════════════════════════════════════════════════════════════════
        # Check MTF Hidden Divergence on df_mtf if available, or fallback to HTF bias
        mtf_hidden_div = bias.hidden_div
        if ctx.df_mtf is not None and len(ctx.df_mtf) >= 20:
            from ..bias import _hidden_divergence
            mtf_close = ctx.df_mtf["Close"]
            delta = mtf_close.diff()
            gain = delta.where(delta > 0, 0.0)
            loss = (-delta).where(delta < 0, 0.0)
            ag = gain.ewm(com=13, min_periods=14).mean()
            al = loss.ewm(com=13, min_periods=14).mean()
            rs = ag / al.replace(0, float("inf"))
            mtf_rsi = 100 - (100 / (1 + rs))
            div_res = _hidden_divergence(mtf_close, mtf_rsi)
            if div_res != "none":
                mtf_hidden_div = div_res

        # Enforce Hidden Divergence continuation or strong trend confirmation
        if direction == "long" and mtf_hidden_div != "bullish" and bias.bias != "strong_bull":
            logger.debug("[trader.strategy] %s: Long rejected — HTF/MTF bullish hidden divergence not confirmed", ctx.ticker)
            return None
        if direction == "short" and mtf_hidden_div != "bearish" and bias.bias != "strong_bear":
            logger.debug("[trader.strategy] %s: Short rejected — HTF/MTF bearish hidden divergence not confirmed", ctx.ticker)
            return None

        # Filter out choppy / sideways regimes via ADX
        adx_val = ctx.adx
        if adx_val is not None and adx_val < 20.0:
            logger.debug("[trader.strategy] %s: Rejected — ADX %.1f < 20.0 indicates choppy / ranging regime", ctx.ticker, adx_val)
            return None

        # EMA Trend alignment and slope (21 > 50 fan for long, 21 < 50 for short)
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

        # ═════════════════════════════════════════════════════════════════
        # Gate 3: The Entry Trigger ("How" — Rejection Wick & Tight SL)
        # ═════════════════════════════════════════════════════════════════
        exec_df = ctx.df_trigger if ctx.df_trigger is not None else ctx.df_ltf
        if len(exec_df) < 30:
            return None

        ema21_val = float(ctx.ema_21.iloc[-1])
        atr       = ctx.atr_14 or 1.0

        # Overextension / Parabolic blowout filter (> 2.5 ATR from EMA21)
        if abs(ctx.current_price - ema21_val) > atr * 2.5:
            logger.debug("[trader.strategy] %s: Rejected — price overextended from EMA 21 (> 2.5 ATR)", ctx.ticker)
            return None

        # Deep piercing & wick quality rejection trigger
        if not _ema_pullback_triggered(
            exec_df, direction,
            ema8=ctx.ema_8, ema21=ctx.ema_21, ema50=ctx.ema_50, atr=atr,
        ):
            return None

        entry_low  = min(ctx.current_price, ema21_val)
        entry_high = max(ctx.current_price, ema21_val)
        entry_mid  = (entry_low + entry_high) / 2

        min_sl_dist = ctx.get_min_sl_distance(entry_price=entry_mid, atr=atr)

        if direction == "long":
            sl_anchor = _prev_swing_low_price(ctx.structure, ctx.current_price)
            if sl_anchor is None:
                sl_anchor = entry_mid - (atr * 1.5)
            raw_sl = sl_anchor - (atr * 0.25)
            sl_dist = entry_mid - raw_sl
            stop_loss = entry_mid - max(sl_dist, min_sl_dist)
            invalidation = f"Close below prev swing low {sl_anchor:,.4f}"
        else:
            sl_anchor = _prev_swing_high_price(ctx.structure, ctx.current_price)
            if sl_anchor is None:
                sl_anchor = entry_mid + (atr * 1.5)
            raw_sl = sl_anchor + (atr * 0.25)
            sl_dist = raw_sl - entry_mid
            stop_loss = entry_mid + max(sl_dist, min_sl_dist)
            invalidation = f"Close above prev swing high {sl_anchor:,.4f}"

        sl_distance = abs(entry_mid - stop_loss)
        if sl_distance > atr * 3.5:
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

        # Pattern-specific confluence additions:
        # 1. Base EMA 21 dynamic support/resistance
        report.score = min(report.score + 1.5, 10.0)
        report.sr_hits.append(f"EMA 21 Dynamic {'Support' if direction == 'long' else 'Resistance'}")

        # 2. Hidden Divergence continuation signal
        if mtf_hidden_div in ("bullish", "bearish") or bias.hidden_div in ("bullish", "bearish"):
            report.score = min(report.score + 1.5, 10.0)
            report.sr_hits.append(f"Hidden Divergence Continuation ({direction.upper()})")

        # 3. EMA 21/50 trend fan alignment
        if ema21 is not None and ema50 is not None:
            report.score = min(report.score + 1.0, 10.0)
            report.sr_hits.append(f"EMA 21/50 Trend Fan ({direction.upper()})")

        report.label = (
            "high"   if report.score >= 7.0 else
            "medium" if report.score >= 5.0 else
            "low"
        )

        if report.score < max(0, _MIN_SCORE - 2.0):
            return None

        if "moderate" in bias.bias and report.label == "high":
            report = ConfluenceReport(
                score=report.score, sr_hits=report.sr_hits, vp_hits=report.vp_hits,
                poc=report.poc, vah=report.vah, val=report.val,
                label="medium",
            )

        triad = None
        try:
            from ..schemas import timeframe_triad_for
            triad = timeframe_triad_for(ctx.mode)
        except Exception:
            pass

        return TradeSetup(
            ticker=ctx.ticker, category=ctx.category, mode=ctx.mode,
            setup_name=self.name, setup_type=self.setup_type,
            direction=direction, bias=bias, confluence=report,
            entry_low=entry_low, entry_high=entry_high,
            stop_loss=stop_loss, tp1=tp1, tp2=tp2,
            rr_ratio=round(rr, 2),
            invalidation_note=invalidation,
            ltf_tf=triad.trigger if triad else bias.ltf_tf,
            htf_tf=triad.htf if triad else bias.htf_tf,
            mtf_tf=triad.mtf if triad else "",
            trigger_tf=triad.trigger if triad else bias.ltf_tf,
        )
