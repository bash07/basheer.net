"""Setup 1 — OB Retracement strategy.

Upgraded to the 3-Tier Institutional Multi-Timeframe Triad:
  - Gate 1: The HTF Setup ("Where" — Macro Trend, POI Alignment, & Discount/Premium Dealing Range)
  - Gate 2: The MTF Confirmation ("When" — Structure Defense & Momentum Exhaustion)
  - Gate 3: The Entry Trigger ("How" — Micro Retracement, Absorption Rejection Wick, Adaptive SL)
"""
from __future__ import annotations

import logging
from typing import Optional

from .base import BaseStrategy, StrategyContext

logger = logging.getLogger(__name__)

_MIN_RR = 1.5
_MIN_WICK_RATIO = 0.35      # Micro rejection wick >= 35% of bar range
_MAX_OB_SIZE_ATR = 2.5     # Reject oversized OBs wider than 2.5x ATR
_MAX_SL_DIST_ATR = 2.5     # Reject setups where stop loss distance exceeds 2.5x ATR


def _compute_rsi(close_series, period: int = 14):
    """Wilder's EWM RSI-14."""
    import numpy as np
    if len(close_series) < period + 1:
        return None
    delta = close_series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    ag = gain.ewm(com=period - 1, min_periods=period).mean()
    al = loss.ewm(com=period - 1, min_periods=period).mean()

    # When al > 0: standard formula
    # When al == 0 and ag > 0: 100.0
    # When al == 0 and ag == 0: 50.0
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = ag / al
        rsi = 100.0 - (100.0 / (1.0 + rs))

    rsi = rsi.where(al > 0, np.where(ag > 0, 100.0, 50.0))
    return rsi


def _count_ob_touches(df, ob_bar_index: int, zone_low: float, zone_high: float, direction: str) -> int:
    """Count distinct multi-bar visits/touches into the OB zone after its formation."""
    if df is None or len(df) <= ob_bar_index + 1:
        return 1
    touches = 0
    in_zone = False
    highs = df["High"].values
    lows  = df["Low"].values
    for i in range(ob_bar_index + 1, len(df)):
        if direction == "long":
            is_touching = (lows[i] <= zone_high) and (highs[i] >= zone_low)
        else:
            is_touching = (highs[i] >= zone_low) and (lows[i] <= zone_high)

        if is_touching and not in_zone:
            touches += 1
            in_zone = True
        elif not is_touching:
            in_zone = False
    return max(1, touches)


class OBRetracementStrategy(BaseStrategy):
    """BOS confirmed + active Order Block + price retracing into OB zone.

    3-Tier Institutional Multi-Timeframe Architecture:
      - Gate 1: HTF Setup (Where):
          * Directional Macro Bias alignment (strong/moderate bull/bear).
          * Discount / Premium Dealing Range Filter: Longs <= 0.55 RelPos, Shorts >= 0.45 RelPos.
          * Institutional POI Discovery: Resolves Order Blocks on MTF when available,
            falling back to LTF in legacy mode.
          * Order Block Dimension & Sanity Guards (non-zero width, <= 2.5x ATR).
      - Gate 2: MTF Confirmation (When):
          * Blowout Acceptance Filter: Rejects if 2 consecutive MTF candles closed through distal edge.
          * Momentum Deceleration: Rejects extreme knife crashes (Long MTF RSI < 25, Short MTF RSI > 75).
      - Gate 3: Trigger Execution (How):
          * Micro Retracement into OB zone (+-10% tolerance).
          * Micro Absorption Rejection Wick (>= 35% wick or close in favorable half).
          * Adaptive Stop Loss: Distal edge + 0.50x ATR (crypto) or 0.25x ATR (equities/forex).
          * Multi-tier TP targets with R:R >= 1.5.
    """

    name = "OB Retracement"
    description = "BOS + Order Block retracement entry with MTF confirmation"
    setup_type = "continuation"
    priority = 10
    bias_provider = "ema"
    supported_styles = ["swing", "intraday", "scalping"]
    required_indicators = [
        "bias", "structure", "bos_list", "ob_list", "liq_list", "fvg_list",
        "volume_profile", "sr_levels", "atr_14", "rsi_14", "adx",
    ]

    def evaluate(self, ctx: StrategyContext) -> Optional[object]:
        from ..confluence import score_zone
        from ..schemas import TradeSetup, ConfluenceReport
        from ..setups import (
            _MIN_SCORE, _calc_rr,
            _next_swing_high, _next_swing_low, _next_eqh, _next_eql,
        )

        # ── Timeframe Cascading Fallback (Backward Compatibility) ────────────
        df_htf     = ctx.df_htf
        df_mtf     = ctx.df_mtf if ctx.df_mtf is not None else (ctx.df_ltf if ctx.df_ltf is not None else df_htf)
        df_trigger = ctx.df_trigger if ctx.df_trigger is not None else ctx.df_ltf

        if df_trigger is None or len(df_trigger) < 5:
            return None

        # ═════════════════════════════════════════════════════════════════════
        # Gate 1: The HTF Setup ("Where" — Trend & Dealing Range POI)
        # ═════════════════════════════════════════════════════════════════════
        bias = ctx.bias
        if bias is None or bias.bias == "neutral":
            return None

        # Trend Momentum Filter (continuation setups require active trend momentum)
        adx_val = ctx.adx if ctx.adx is not None else 25.0
        if adx_val < 18.0:
            logger.debug(
                "[%s] OB Retracement: Skipped - trend momentum too weak (ADX=%.1f < 18.0)",
                ctx.ticker, adx_val,
            )
            return None

        direction = "long" if "bull" in bias.bias else "short"
        bos_dir   = "bullish" if direction == "long" else "bearish"

        # 1. Discount / Premium Dealing Range Filter (Gate 1 Where)
        if df_htf is not None and len(df_htf) >= 10:
            lookback = min(30, len(df_htf))
            htf_high = float(df_htf["High"].tail(lookback).max())
            htf_low  = float(df_htf["Low"].tail(lookback).min())
            htf_rng  = htf_high - htf_low
            if htf_rng > 0:
                rel_pos = (ctx.current_price - htf_low) / htf_rng
                if direction == "long" and rel_pos > 0.55:
                    logger.debug(
                        "[%s] OB Retracement: Long rejected - not in HTF Discount (RelPos=%.2f > 0.55)",
                        ctx.ticker, rel_pos,
                    )
                    return None
                if direction == "short" and rel_pos < 0.45:
                    logger.debug(
                        "[%s] OB Retracement: Short rejected - not in HTF Premium (RelPos=%.2f < 0.45)",
                        ctx.ticker, rel_pos,
                    )
                    return None

        # 2. Institutional Order Block Resolution (MTF priority, LTF fallback)
        aligned_obs = []
        if df_mtf is not None and len(df_mtf) >= 15:
            try:
                from ..scanner import detect_structure, detect_bos_choch, find_order_blocks
                mtf_struct = detect_structure(df_mtf)
                mtf_bos    = detect_bos_choch(df_mtf, mtf_struct)
                mtf_obs    = find_order_blocks(df_mtf, mtf_bos)
                aligned_obs = [ob for ob in mtf_obs if ob.direction == bos_dir]
            except Exception as e:
                logger.debug("[%s] OB Retracement: MTF OB detection failed: %s", ctx.ticker, e)

        # Fallback to context ob_list if MTF has no OBs or is unavailable
        if not aligned_obs:
            aligned_obs = [ob for ob in ctx.ob_list if ob.direction == bos_dir]

        if not aligned_obs:
            return None

        ob = aligned_obs[0]
        zone_size = ob.zone_high - ob.zone_low
        if zone_size <= 0:
            return None

        # Freshness Check: Max 2 visits into zone (prevent trading depleted / fully mitigated blocks)
        touches = _count_ob_touches(df_mtf, ob.bar_index, ob.zone_low, ob.zone_high, direction)
        if touches > 2:
            logger.debug(
                "[%s] OB Retracement: OB rejected - already mitigated (%d prior visits)",
                ctx.ticker, touches,
            )
            return None

        atr = ctx.atr_14 if ctx.atr_14 and ctx.atr_14 > 0 else 1.0
        if zone_size > (_MAX_OB_SIZE_ATR * atr):
            logger.debug(
                "[%s] OB Retracement: OB zone size (%.4f) exceeds %.1fx ATR (%.4f)",
                ctx.ticker, zone_size, _MAX_OB_SIZE_ATR, atr,
            )
            return None

        # ═════════════════════════════════════════════════════════════════════
        # Gate 2: The MTF Confirmation ("When" — Structure Defense & Momentum)
        # ═════════════════════════════════════════════════════════════════════
        if df_mtf is not None and len(df_mtf) >= 2:
            # Invalidation / Blowout Guard (no consecutive closes slicing through distal edge)
            c_last = float(df_mtf["Close"].iloc[-1])
            c_prev = float(df_mtf["Close"].iloc[-2])

            if direction == "long" and c_last < ob.zone_low and c_prev < ob.zone_low:
                logger.debug(
                    "[%s] OB Retracement: Long rejected - 2 consecutive MTF closes below distal edge %.4f",
                    ctx.ticker, ob.zone_low,
                )
                return None
            if direction == "short" and c_last > ob.zone_high and c_prev > ob.zone_high:
                logger.debug(
                    "[%s] OB Retracement: Short rejected - 2 consecutive MTF closes above distal edge %.4f",
                    ctx.ticker, ob.zone_high,
                )
                return None

            # Momentum Deceleration / Knife-Catching Guard
            if len(df_mtf) >= 15:
                mtf_rsi = _compute_rsi(df_mtf["Close"], period=14)
                if mtf_rsi is not None and not mtf_rsi.empty:
                    last_rsi = float(mtf_rsi.iloc[-1])
                    if direction == "long" and last_rsi < 25.0:
                        logger.debug(
                            "[%s] OB Retracement: Long rejected - extreme falling knife (MTF RSI=%.1f < 25)",
                            ctx.ticker, last_rsi,
                        )
                        return None
                    if direction == "short" and last_rsi > 75.0:
                        logger.debug(
                            "[%s] OB Retracement: Short rejected - extreme parabolic surge (MTF RSI=%.1f > 75)",
                            ctx.ticker, last_rsi,
                        )
                        return None

        # ═════════════════════════════════════════════════════════════════════
        # Gate 3: The Entry Trigger ("How" — Micro Rejection, Adaptive SL, R:R)
        # ═════════════════════════════════════════════════════════════════════
        in_zone_low  = ob.zone_low  - zone_size * 0.10
        in_zone_high = ob.zone_high + zone_size * 0.10

        if not (in_zone_low <= ctx.current_price <= in_zone_high):
            logger.debug(
                "[%s] OB Retracement: price %.4f not in OB zone [%.4f, %.4f]",
                ctx.ticker, ctx.current_price, in_zone_low, in_zone_high,
            )
            return None

        # Micro Absorption Rejection Wick Confirmation on trigger bar
        last_o = float(df_trigger["Open"].iloc[-1])
        last_h = float(df_trigger["High"].iloc[-1])
        last_l = float(df_trigger["Low"].iloc[-1])
        last_c = float(df_trigger["Close"].iloc[-1])
        candle_range = max(last_h - last_l, 1e-6)

        if direction == "long":
            lower_wick = min(last_o, last_c) - last_l
            wick_ratio = lower_wick / candle_range
            close_pos  = (last_c - last_l) / candle_range
            if wick_ratio < _MIN_WICK_RATIO and close_pos < 0.45:
                logger.debug(
                    "[%s] OB Retracement: Long rejected - weak rejection wick (wick=%.2f, close_pos=%.2f)",
                    ctx.ticker, wick_ratio, close_pos,
                )
                return None
        else:
            upper_wick = last_h - max(last_o, last_c)
            wick_ratio = upper_wick / candle_range
            close_pos  = (last_h - last_c) / candle_range
            if wick_ratio < _MIN_WICK_RATIO and close_pos < 0.45:
                logger.debug(
                    "[%s] OB Retracement: Short rejected - weak rejection wick (wick=%.2f, close_pos=%.2f)",
                    ctx.ticker, wick_ratio, close_pos,
                )
                return None

        # Confluence Scoring & FVG Imbalance bonus
        report = score_zone(ob.zone_low, ob.zone_high, ctx.sr_levels, ctx.volume_profile)
        aligned_fvgs = [f for f in ctx.fvg_list if f.direction == ("bullish" if direction == "long" else "bearish")]
        if aligned_fvgs:
            report = ConfluenceReport(
                score=min(10.0, report.score + 1.0),
                sr_hits=report.sr_hits,
                vp_hits=report.vp_hits + ["Aligned Institutional FVG"],
                poc=report.poc, vah=report.vah, val=report.val,
                label=report.label,
            )
        if report.score < _MIN_SCORE:
            return None

        entry_low  = ob.zone_low
        entry_high = ob.zone_high
        entry_mid  = ob.midpoint

        # Adaptive Stop Loss Buffer (Crypto absorbs volatility; Equities/Forex tight)
        is_crypto = (ctx.category == "crypto")
        buffer_mult = 0.50 if is_crypto else 0.25
        buffer = atr * buffer_mult

        # Resolve target structure: MTF swing if available, else trigger swing
        target_df = df_mtf if df_mtf is not None else df_trigger
        target_struct = None
        if df_mtf is not None:
            try:
                from ..scanner import detect_structure
                target_struct = detect_structure(df_mtf)
            except Exception:
                target_struct = ctx.structure
        else:
            target_struct = ctx.structure

        min_sl_dist = ctx.get_min_sl_distance(entry_price=entry_mid, atr=atr)

        if direction == "long":
            raw_sl    = ob.zone_low - buffer
            sl_dist   = ctx.current_price - raw_sl
            if sl_dist < min_sl_dist:
                stop_loss = ctx.current_price - min_sl_dist
                sl_dist   = min_sl_dist
            else:
                stop_loss = raw_sl

            if sl_dist > (_MAX_SL_DIST_ATR * atr):
                return None

            # Intermediate swing target for TP1 (scale-out + breakeven shift)
            candidate_tp1 = _next_swing_high(ctx.structure, 0, df_trigger)
            target_1 = entry_mid + (sl_dist * 2.0)
            if candidate_tp1 and (candidate_tp1 - entry_mid) >= (sl_dist * _MIN_RR):
                tp1 = min(candidate_tp1, entry_mid + sl_dist * 3.0)
            else:
                tp1 = target_1

            # Major HTF target for TP2 runner
            candidate_tp2 = None
            if df_mtf is not None and target_struct is not None:
                candidate_tp2 = _next_swing_high(target_struct, ob.bar_index, df_mtf)
            tp2 = candidate_tp2 or _next_eqh(ctx.liq_list, entry_mid)
            invalidation = f"Candle closes below {ob.zone_low:,.4f} (OB distal edge)"
            entry_price = min(ctx.current_price, ob.midpoint + zone_size * 0.25)
        else:
            raw_sl    = ob.zone_high + buffer
            sl_dist   = raw_sl - ctx.current_price
            if sl_dist < min_sl_dist:
                stop_loss = ctx.current_price + min_sl_dist
                sl_dist   = min_sl_dist
            else:
                stop_loss = raw_sl

            if sl_dist > (_MAX_SL_DIST_ATR * atr):
                return None

            candidate_tp1 = _next_swing_low(ctx.structure, 0, df_trigger)
            target_1 = entry_mid - (sl_dist * 2.0)
            if candidate_tp1 and (entry_mid - candidate_tp1) >= (sl_dist * _MIN_RR):
                tp1 = max(candidate_tp1, entry_mid - sl_dist * 3.0)
            else:
                tp1 = target_1

            candidate_tp2 = None
            if df_mtf is not None and target_struct is not None:
                candidate_tp2 = _next_swing_low(target_struct, ob.bar_index, df_mtf)
            tp2 = candidate_tp2 or _next_eql(ctx.liq_list, entry_mid)
            invalidation = f"Candle closes above {ob.zone_high:,.4f} (OB distal edge)"
            entry_price = max(ctx.current_price, ob.midpoint - zone_size * 0.25)

        rr = _calc_rr(entry_price, stop_loss, tp1)
        if rr < _MIN_RR:
            return None

        # Moderate bias confidence cap
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
