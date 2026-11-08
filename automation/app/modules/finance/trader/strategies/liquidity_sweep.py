"""Setup 2 — Liquidity Sweep + FVG strategy.

Upgraded to the 3-Tier Institutional Multi-Timeframe Triad:
  - Gate 1: The HTF Setup ("Where" — Macro Trend, Dealing Range, & Liquidity Target)
  - Gate 2: The MTF Confirmation ("When" — Displacement & Imbalance Quality)
  - Gate 3: The Entry Trigger ("How" — Imbalance Retracement, Absorption Wick, Adaptive SL)
"""
from __future__ import annotations

import logging
from typing import Optional

from .base import BaseStrategy, StrategyContext

logger = logging.getLogger(__name__)

_MIN_RR = 1.5               # Minimum Risk-to-Reward to TP1
_MIN_WICK_RATIO = 0.35       # Micro rejection wick >= 35% of bar range
_MIN_FVG_SIZE_ATR = 0.10     # Reject negligible micro-gaps (< 0.10x ATR)
_MAX_FVG_SIZE_ATR = 2.5      # Reject oversized blowout gaps (> 2.5x ATR)
_MAX_SL_DIST_ATR = 2.5       # Reject setups where stop loss distance exceeds 2.5x ATR
_MIN_SL_DIST_ATR = 0.50      # Enforce minimum stop loss buffer of 0.50x ATR
_MAX_SWEEP_LOOKBACK = 25     # Maximum bars elapsed since the sweep occurred


def _compute_rsi(close_series, period: int = 14):
    """Wilder's EWM RSI-14 with safe handling for zero loss or flat series."""
    import numpy as np
    if len(close_series) < period + 1:
        return None
    delta = close_series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    ag = gain.ewm(com=period - 1, min_periods=period).mean()
    al = loss.ewm(com=period - 1, min_periods=period).mean()

    with np.errstate(divide="ignore", invalid="ignore"):
        rs = ag / al
        rsi = 100.0 - (100.0 / (1.0 + rs))

    rsi = rsi.where(al > 0, np.where(ag > 0, 100.0, 50.0))
    return rsi


def _count_fvg_touches(df, fvg_bar_index: int, gap_low: float, gap_high: float, direction: str) -> int:
    """Count distinct multi-bar visits/touches into the FVG zone after its formation."""
    if df is None or len(df) <= fvg_bar_index + 1:
        return 1
    touches = 0
    in_zone = False
    highs = df["High"].values
    lows  = df["Low"].values
    for i in range(fvg_bar_index + 1, len(df)):
        if direction == "long":
            is_touching = (lows[i] <= gap_high) and (highs[i] >= gap_low)
        else:
            is_touching = (highs[i] >= gap_low) and (lows[i] <= gap_high)

        if is_touching and not in_zone:
            touches += 1
            in_zone = True
        elif not is_touching:
            in_zone = False
    return max(1, touches)


class LiquiditySweepFVGStrategy(BaseStrategy):
    """EQH/EQL swept + FVG created within displacement impulse + price retraces into FVG.

    3-Tier Institutional Multi-Timeframe Architecture:
      - Gate 1: HTF Setup (Where):
          * Macro Trend alignment (EMA stack bias strong/moderate bull/bear).
          * Discount / Premium Dealing Range: Longs <= 0.55 RelPos, Shorts >= 0.45 RelPos.
          * HTF ADX trend strength filter (>= 18.0) to filter dead consolidation.
          * Liquidity Pool Resolution: Detects swept EQL/EQH on MTF when available,
            falling back to LTF context.
      - Gate 2: MTF Confirmation (When):
          * Sweep Recency: Swept within the last 25 bars.
          * Post-Sweep Displacement: Bullish/Bearish FVG formed on or after sweep bar.
          * FVG Dimension & Sanity Guards (0.10x ATR <= gap <= 2.5x ATR).
          * Imbalance Freshness Guard: Max 2 visits into FVG zone (no exhausted gaps).
          * Blowout Acceptance Guard: Rejects if 2 consecutive MTF closes breach sweep price.
          * Momentum Deceleration: Rejects falling knives (Long MTF RSI < 25, Short MTF RSI > 75).
      - Gate 3: Trigger Execution (How):
          * Imbalance Retracement into FVG (+-10% buffer).
          * Micro Absorption Rejection Wick (>= 35% wick or favorable close).
          * Adaptive Stop Loss: Sweep price +- 0.50x ATR (crypto) or 0.25x ATR (equities/forex).
          * Multi-tier TP targets with R:R >= 1.5.
    """

    name = "Liquidity Sweep + FVG"
    description = "Liquidity sweep followed by FVG re-entry with MTF confirmation"
    setup_type = "continuation"
    priority = 20
    bias_provider = "ema"
    supported_styles = ["swing", "intraday", "scalping"]
    required_indicators = [
        "bias", "adx", "atr_14", "rsi_14", "structure", "bos_list", "ob_list",
        "fvg_list", "liq_list", "volume_profile", "sr_levels",
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
        # Gate 1: The HTF Setup ("Where" — Trend & Dealing Range Liquidity)
        # ═════════════════════════════════════════════════════════════════════
        bias = ctx.bias
        if bias is None or bias.bias == "neutral":
            return None

        # Trend Momentum Filter (continuation setups require active trend momentum)
        adx_val = ctx.adx if ctx.adx is not None else 25.0
        if adx_val < 18.0:
            logger.debug(
                "[%s] Liquidity Sweep + FVG: Skipped - trend momentum too weak (ADX=%.1f < 18.0)",
                ctx.ticker, adx_val,
            )
            return None

        direction = "long" if "bull" in bias.bias else "short"
        liq_kind  = "eql" if direction == "long" else "eqh"
        fvg_dir   = "bullish" if direction == "long" else "bearish"

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
                        "[%s] Liquidity Sweep + FVG: Long rejected - not in HTF Discount (RelPos=%.2f > 0.55)",
                        ctx.ticker, rel_pos,
                    )
                    return None
                if direction == "short" and rel_pos < 0.45:
                    logger.debug(
                        "[%s] Liquidity Sweep + FVG: Short rejected - not in HTF Premium (RelPos=%.2f < 0.45)",
                        ctx.ticker, rel_pos,
                    )
                    return None

        # 2. Liquidity Pool Discovery (MTF priority, LTF fallback)
        swept_levels = []
        scan_liq_df = df_mtf if df_mtf is not None and len(df_mtf) >= 15 else df_trigger

        if df_mtf is not None and len(df_mtf) >= 15:
            try:
                from ..scanner import find_eqh_eql
                mtf_liq = find_eqh_eql(df_mtf)
                swept_levels = [l for l in mtf_liq if l.swept and l.kind == liq_kind]
            except Exception as e:
                logger.debug("[%s] Liquidity Sweep + FVG: MTF liq detection failed: %s", ctx.ticker, e)

        if not swept_levels:
            swept_levels = [l for l in ctx.liq_list if l.swept and l.kind == liq_kind]
            scan_liq_df = df_trigger

        if not swept_levels:
            return None

        swept_levels.sort(key=lambda l: l.sweep_bar, reverse=True)
        recent_sweep = swept_levels[0]

        # Sweep Recency Filter: Must have been swept within last _MAX_SWEEP_LOOKBACK bars
        bars_since_sweep = len(scan_liq_df) - 1 - recent_sweep.sweep_bar
        if bars_since_sweep > _MAX_SWEEP_LOOKBACK:
            logger.debug(
                "[%s] Liquidity Sweep + FVG: Sweep rejected - too stale (%d bars ago > %d)",
                ctx.ticker, bars_since_sweep, _MAX_SWEEP_LOOKBACK,
            )
            return None

        # ═════════════════════════════════════════════════════════════════════
        # Gate 2: The MTF Confirmation ("When" — Displacement & Imbalance)
        # ═════════════════════════════════════════════════════════════════════
        aligned_fvgs = []
        if df_mtf is not None and len(df_mtf) >= 15 and scan_liq_df is df_mtf:
            try:
                from ..scanner import find_fvg
                mtf_fvgs = find_fvg(df_mtf)
                aligned_fvgs = [
                    f for f in mtf_fvgs
                    if f.bar_index >= recent_sweep.sweep_bar and f.direction == fvg_dir
                ]
            except Exception as e:
                logger.debug("[%s] Liquidity Sweep + FVG: MTF FVG detection failed: %s", ctx.ticker, e)

        if not aligned_fvgs:
            aligned_fvgs = [
                f for f in ctx.fvg_list
                if f.bar_index >= recent_sweep.sweep_bar and f.direction == fvg_dir
            ]

        if not aligned_fvgs:
            return None

        fvg = aligned_fvgs[0]
        gap_size = fvg.gap_high - fvg.gap_low
        if gap_size <= 0:
            return None

        atr = ctx.atr_14 if ctx.atr_14 and ctx.atr_14 > 0 else 1.0

        # FVG Dimension & Sanity Guards
        if gap_size < (_MIN_FVG_SIZE_ATR * atr):
            logger.debug(
                "[%s] Liquidity Sweep + FVG: FVG gap (%.4f) too small (< %.2fx ATR)",
                ctx.ticker, gap_size, _MIN_FVG_SIZE_ATR,
            )
            return None
        if gap_size > (_MAX_FVG_SIZE_ATR * atr):
            logger.debug(
                "[%s] Liquidity Sweep + FVG: FVG gap (%.4f) exceeds %.1fx ATR (%.4f)",
                ctx.ticker, gap_size, _MAX_FVG_SIZE_ATR, atr,
            )
            return None

        # Imbalance Freshness Guard: Max 2 visits into FVG zone
        touches = _count_fvg_touches(scan_liq_df, fvg.bar_index, fvg.gap_low, fvg.gap_high, direction)
        if touches > 2:
            logger.debug(
                "[%s] Liquidity Sweep + FVG: FVG rejected - exhausted (%d touches > 2)",
                ctx.ticker, touches,
            )
            return None

        # Blowout Acceptance Guard & Momentum Guard on MTF
        if df_mtf is not None and len(df_mtf) >= 2:
            c_last = float(df_mtf["Close"].iloc[-1])
            c_prev = float(df_mtf["Close"].iloc[-2])

            if direction == "long" and c_last < recent_sweep.price and c_prev < recent_sweep.price:
                logger.debug(
                    "[%s] Liquidity Sweep + FVG: Long rejected - 2 consecutive MTF closes below sweep price %.4f",
                    ctx.ticker, recent_sweep.price,
                )
                return None
            if direction == "short" and c_last > recent_sweep.price and c_prev > recent_sweep.price:
                logger.debug(
                    "[%s] Liquidity Sweep + FVG: Short rejected - 2 consecutive MTF closes above sweep price %.4f",
                    ctx.ticker, recent_sweep.price,
                )
                return None

            if len(df_mtf) >= 15:
                mtf_rsi = _compute_rsi(df_mtf["Close"], period=14)
                if mtf_rsi is not None and not mtf_rsi.empty:
                    last_rsi = float(mtf_rsi.iloc[-1])
                    if direction == "long" and last_rsi < 25.0:
                        logger.debug(
                            "[%s] Liquidity Sweep + FVG: Long rejected - extreme falling knife (MTF RSI=%.1f < 25)",
                            ctx.ticker, last_rsi,
                        )
                        return None
                    if direction == "short" and last_rsi > 75.0:
                        logger.debug(
                            "[%s] Liquidity Sweep + FVG: Short rejected - extreme parabolic surge (MTF RSI=%.1f > 75)",
                            ctx.ticker, last_rsi,
                        )
                        return None

        # ═════════════════════════════════════════════════════════════════════
        # Gate 3: The Entry Trigger ("How" — Imbalance Retracement, SL, R:R)
        # ═════════════════════════════════════════════════════════════════════
        in_zone_low  = fvg.gap_low  - gap_size * 0.10
        in_zone_high = fvg.gap_high + gap_size * 0.10

        if not (in_zone_low <= ctx.current_price <= in_zone_high):
            logger.debug(
                "[%s] Liquidity Sweep + FVG: price %.4f not in FVG zone [%.4f, %.4f]",
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
                    "[%s] Liquidity Sweep + FVG: Long rejected - weak rejection wick (wick=%.2f, close_pos=%.2f)",
                    ctx.ticker, wick_ratio, close_pos,
                )
                return None
        else:
            upper_wick = last_h - max(last_o, last_c)
            wick_ratio = upper_wick / candle_range
            close_pos  = (last_h - last_c) / candle_range
            if wick_ratio < _MIN_WICK_RATIO and close_pos < 0.45:
                logger.debug(
                    "[%s] Liquidity Sweep + FVG: Short rejected - weak rejection wick (wick=%.2f, close_pos=%.2f)",
                    ctx.ticker, wick_ratio, close_pos,
                )
                return None

        # Confluence Scoring & Displacement Bonus
        report = score_zone(fvg.gap_low, fvg.gap_high, ctx.sr_levels, ctx.volume_profile)
        if fvg.displacement_ratio >= 1.2:
            report = ConfluenceReport(
                score=min(10.0, report.score + 1.0),
                sr_hits=report.sr_hits,
                vp_hits=report.vp_hits + ["Strong Institutional Displacement"],
                poc=report.poc, vah=report.vah, val=report.val,
                label=report.label,
            )
        if report.score < _MIN_SCORE:
            return None

        entry_low  = fvg.gap_low
        entry_high = fvg.gap_high
        entry_mid  = fvg.midpoint

        # Resolve sweep wick extreme from the scanning dataframe
        sweep_bar = recent_sweep.sweep_bar
        if 0 <= sweep_bar < len(scan_liq_df):
            sweep_low  = float(scan_liq_df["Low"].iloc[sweep_bar])
            sweep_high = float(scan_liq_df["High"].iloc[sweep_bar])
        else:
            sweep_low  = recent_sweep.price
            sweep_high = recent_sweep.price

        # Adaptive Stop Loss Buffer (Crypto absorbs volatility; Equities/Forex tight)
        is_crypto = (ctx.category == "crypto")
        buffer_mult = 0.50 if is_crypto else 0.25
        buffer = atr * buffer_mult
        min_sl_dist = max(_MIN_SL_DIST_ATR * atr, ctx.get_min_sl_distance(entry_price=entry_mid, atr=atr))

        if direction == "long":
            anchor_low = min(recent_sweep.price, sweep_low)
            raw_sl     = anchor_low - buffer
            sl_dist    = ctx.current_price - raw_sl
            if sl_dist < min_sl_dist:
                stop_loss = ctx.current_price - min_sl_dist
                sl_dist   = min_sl_dist
            else:
                stop_loss = raw_sl

            if sl_dist > (_MAX_SL_DIST_ATR * atr):
                return None

            candidate_tp1 = _next_swing_high(ctx.structure, fvg.bar_index, df_trigger)
            target_1 = entry_mid + (sl_dist * 2.0)
            if candidate_tp1 and (candidate_tp1 - entry_mid) >= (sl_dist * _MIN_RR):
                tp1 = min(candidate_tp1, entry_mid + sl_dist * 3.0)
            else:
                tp1 = target_1

            tp2 = _next_eqh(ctx.liq_list, entry_mid) or (entry_mid + sl_dist * 3.5)
            invalidation = f"Candle closes below sweep low {anchor_low:,.4f}"
        else:
            anchor_high = max(recent_sweep.price, sweep_high)
            raw_sl      = anchor_high + buffer
            sl_dist     = raw_sl - ctx.current_price
            if sl_dist < min_sl_dist:
                stop_loss = ctx.current_price + min_sl_dist
                sl_dist   = min_sl_dist
            else:
                stop_loss = raw_sl

            if sl_dist > (_MAX_SL_DIST_ATR * atr):
                return None

            candidate_tp1 = _next_swing_low(ctx.structure, fvg.bar_index, df_trigger)
            target_1 = entry_mid - (sl_dist * 2.0)
            if candidate_tp1 and (entry_mid - candidate_tp1) >= (sl_dist * _MIN_RR):
                tp1 = max(candidate_tp1, entry_mid - sl_dist * 3.0)
            else:
                tp1 = target_1

            tp2 = _next_eql(ctx.liq_list, entry_mid) or (entry_mid - sl_dist * 3.5)
            invalidation = f"Candle closes above sweep high {anchor_high:,.4f}"

        rr = _calc_rr(entry_mid, stop_loss, tp1)
        if rr < _MIN_RR:
            return None

        return TradeSetup(
            ticker=ctx.ticker,
            category=ctx.category,
            mode=ctx.mode,
            setup_name=self.name,
            setup_type=self.setup_type,
            direction=direction,
            bias=bias,
            confluence=report,
            entry_low=entry_low,
            entry_high=entry_high,
            stop_loss=round(stop_loss, 4),
            tp1=round(tp1, 4),
            tp2=round(tp2, 4) if tp2 else None,
            rr_ratio=round(rr, 2),
            invalidation_note=invalidation,
            ltf_tf=bias.ltf_tf,
            htf_tf=bias.htf_tf,
        )
