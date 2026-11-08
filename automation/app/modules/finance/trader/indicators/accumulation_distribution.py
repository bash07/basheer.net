"""Institutional Accumulation / Distribution & Supply Absorption Detector.

Detects Wyckoff accumulation and distribution regimes, volume-price absorption,
volatility squeeze coiling, and resolves false momentum divergences.

Core Principles:
1. Range Compression (Regime 5 Squeeze):
   - Bollinger Bands coiling inside Keltner Channels.
   - Multi-bar tight range (< 2.5x ATR).
2. Volume-Flow Absorption (when volume is available):
   - On-Balance Volume (OBV) trend vs. price trend divergence.
   - Rising OBV during flat/drooping price consolidation = Institutional Accumulation.
   - Falling OBV during flat/churning price at resistance = Institutional Distribution.
3. Synthetic Money Flow & Wick Absorption (fallback when volume is 0 or synthetic):
   - Internal Bar Strength (IBS): (Close - Low) / (High - Low). Mean > 0.52 indicates dip buying.
   - Wick Absorption Ratio: Sum(Lower Wicks) / Sum(Upper Wicks). Ratio > 1.2 indicates strong bids.
4. Momentum Divergence Resolution:
   - Identifies when a declining RSI slope during a tight range is actually
     Supply Depletion (Absorption Coil) rather than true distribution.
5. Wyckoff Phase C Triggers:
   - Spring / Shakeout: False breakdown below range low that closes back inside.
   - Upthrust (UTAD): False breakout above range high that fails back inside.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from . import register

logger = logging.getLogger(__name__)


@dataclass
class AccumulationDistributionInfo:
    """Quantitative footprint of Accumulation vs. Distribution."""
    state: str                 # "accumulation" | "distribution" | "neutral"
    score: float               # -100.0 to +100.0 (positive = accumulation, negative = distribution)
    is_squeeze: bool           # BB inside KC or tight range
    absorption_divergence: bool # True if price holds support while RSI/momentum has lower highs
    exhaustion_divergence: bool # True if price stalls at resistance while RSI has higher highs / volume drains
    range_high: float
    range_low: float
    range_mid: float
    spring_detected: bool      # Low of recent bar swept below range_low and reclaimed
    upthrust_detected: bool    # High of recent bar swept above range_high and rejected
    obv_slope: float           # Normalized OBV slope
    ibs_mean: float            # Internal Bar Strength mean (0.0 to 1.0)
    wick_ratio: float          # Lower wick sum / Upper wick sum


@register("accum_dist", deps=["atr_14", "rsi_14", "bb_upper", "bb_lower", "bb_mid"])
def _accum_dist(df_ltf: pd.DataFrame, df_htf: pd.DataFrame, cache: dict) -> AccumulationDistributionInfo:
    """Compute institutional accumulation / distribution metrics on LTF."""
    n = len(df_ltf)
    if n < 15:
        close_last = float(df_ltf["Close"].iloc[-1]) if n > 0 else 0.0
        return AccumulationDistributionInfo(
            state="neutral",
            score=0.0,
            is_squeeze=False,
            absorption_divergence=False,
            exhaustion_divergence=False,
            range_high=close_last,
            range_low=close_last,
            range_mid=close_last,
            spring_detected=False,
            upthrust_detected=False,
            obv_slope=0.0,
            ibs_mean=0.5,
            wick_ratio=1.0,
        )

    # Effective lookback window for the consolidation box
    window = min(25, n)
    sub_df = df_ltf.iloc[-window:]

    # Established consolidation range from prior bars (excluding current testing bar)
    prior_df = sub_df.iloc[:-1] if len(sub_df) > 5 else sub_df
    range_high = float(prior_df["High"].max())
    range_low  = float(prior_df["Low"].min())
    range_span = range_high - range_low
    range_mid  = (range_high + range_low) / 2.0

    close_series = sub_df["Close"].astype(float)
    high_series  = sub_df["High"].astype(float)
    low_series   = sub_df["Low"].astype(float)
    open_series  = sub_df["Open"].astype(float)

    current_price = float(close_series.iloc[-1])

    # ATR scalar
    atr = float(cache.get("atr_14", 0.0))
    if atr <= 0:
        atr = max(float((high_series - low_series).mean()), 1e-4)

    # ── 1. Volatility Squeeze & Compression ─────────────────────────────────
    is_bb_kc_squeeze = False
    if "bb_upper" in cache and "bb_lower" in cache:
        bb_u = float(cache["bb_upper"].iloc[-1])
        bb_l = float(cache["bb_lower"].iloc[-1])
        ema20 = float(df_ltf["Close"].ewm(span=20, adjust=False).mean().iloc[-1])
        kc_u  = ema20 + (1.5 * atr)
        kc_l  = ema20 - (1.5 * atr)
        if bb_u <= (kc_u + 0.1 * atr) and bb_l >= (kc_l - 0.1 * atr):
            is_bb_kc_squeeze = True

    # Multi-bar compression check: range span relative to ATR (typically <= 4.0x ATR for 20 bars)
    is_tight_span = (range_span <= 4.0 * atr)
    is_squeeze    = is_bb_kc_squeeze or is_tight_span

    # ── 2. Volume Flow Analysis (OBV & CMF) ──────────────────────────────────
    has_volume = "Volume" in sub_df.columns and float(sub_df["Volume"].sum()) > 0
    obv_slope = 0.0

    if has_volume:
        vol = sub_df["Volume"].astype(float)
        price_change = close_series.diff().fillna(0.0)
        direction_sign = np.sign(price_change)
        obv = (direction_sign * vol).cumsum()
        mean_vol = max(float(vol.mean()), 1.0)
        x = np.arange(window)
        # Normalized OBV slope per bar
        obv_slope = float(np.polyfit(x, obv.values, 1)[0]) / mean_vol

    # ── 3. Internal Bar Strength & Wick Absorption Ratio ──────────────────────
    bar_rng = (high_series - low_series).replace(0, 1e-6)
    ibs = (close_series - low_series) / bar_rng
    ibs_mean = float(ibs.mean())

    lower_wicks = (np.minimum(open_series, close_series) - low_series).clip(lower=0.0)
    upper_wicks = (high_series - np.maximum(open_series, close_series)).clip(lower=0.0)

    sum_lw = float(lower_wicks.sum())
    sum_uw = float(upper_wicks.sum())
    wick_ratio = sum_lw / max(sum_uw, 1e-6)

    # ── 4. Price Slope vs Momentum Slope (Divergence Resolution) ─────────────
    x_axis = np.arange(window)
    price_slope = float(np.polyfit(x_axis, close_series.values, 1)[0]) / atr

    # RSI slope over window
    rsi_series = cache.get("rsi_14")
    if rsi_series is None or len(rsi_series) < window:
        from ..indicators.rsi import calc_rsi
        rsi_series = calc_rsi(df_ltf["Close"], 14)
    sub_rsi = rsi_series.iloc[-window:].fillna(50.0).astype(float)
    rsi_slope = float(np.polyfit(x_axis, sub_rsi.values, 1)[0])

    # Absorption Divergence: RSI has negative slope (bearish divergence look)
    # BUT price is holding range support in a squeeze AND buyers are absorbing
    absorption_divergence = False
    if rsi_slope < -0.15 and is_squeeze:
        # Check buyer absorption via OBV, IBS or Wick ratio
        if (has_volume and obv_slope > 0.05) or (ibs_mean >= 0.51) or (wick_ratio >= 1.15):
            absorption_divergence = True

    # Exhaustion Divergence: RSI has positive slope BUT price stalls at range ceiling
    # AND sellers are leaking volume
    exhaustion_divergence = False
    if rsi_slope > 0.15 and is_squeeze:
        if (has_volume and obv_slope < -0.05) or (ibs_mean <= 0.49) or (wick_ratio <= 0.85):
            exhaustion_divergence = True

    # ── 5. Wyckoff Phase C Triggers (Spring & Upthrust) ───────────────────────
    # Look back over the last 3 closed bars
    check_bars = min(3, len(df_ltf))
    spring_detected = False
    upthrust_detected = False

    for i in range(1, check_bars + 1):
        bar = df_ltf.iloc[-i]
        b_h = float(bar["High"])
        b_l = float(bar["Low"])
        b_o = float(bar["Open"])
        b_c = float(bar["Close"])
        b_rng = max(b_h - b_l, 1e-6)

        # Spring: pierced below range_low and closed back above range_low
        if b_l < range_low and b_c >= range_low:
            lw = min(b_o, b_c) - b_l
            if (lw / b_rng) >= 0.30:
                spring_detected = True

        # Upthrust: pierced above range_high and closed back below range_high
        if b_h > range_high and b_c <= range_high:
            uw = b_h - max(b_o, b_c)
            if (uw / b_rng) >= 0.30:
                upthrust_detected = True

    # ── 6. Composite Score Calculation (-100 to +100) ─────────────────────────
    score = 0.0

    # Volume / IBS contribution (-30 to +30)
    if has_volume:
        if obv_slope > 0.2:
            score += 30.0
        elif obv_slope > 0.05:
            score += 15.0
        elif obv_slope < -0.2:
            score -= 30.0
        elif obv_slope < -0.05:
            score -= 15.0
    else:
        if ibs_mean >= 0.55:
            score += 25.0
        elif ibs_mean <= 0.45:
            score -= 25.0

    # Wick absorption contribution (-25 to +25)
    if wick_ratio >= 1.35:
        score += 25.0
    elif wick_ratio >= 1.15:
        score += 12.0
    elif wick_ratio <= 0.70:
        score -= 25.0
    elif wick_ratio <= 0.85:
        score -= 12.0

    # Divergence resolution contribution (-25 to +25)
    if absorption_divergence:
        score += 25.0
    elif exhaustion_divergence:
        score -= 25.0

    # Spring / Upthrust contribution (-20 to +20)
    if spring_detected:
        score += 20.0
    if upthrust_detected:
        score -= 20.0

    score = float(np.clip(score, -100.0, 100.0))

    if score >= 25.0:
        state = "accumulation"
    elif score <= -25.0:
        state = "distribution"
    else:
        state = "neutral"

    return AccumulationDistributionInfo(
        state=state,
        score=score,
        is_squeeze=is_squeeze,
        absorption_divergence=absorption_divergence,
        exhaustion_divergence=exhaustion_divergence,
        range_high=range_high,
        range_low=range_low,
        range_mid=range_mid,
        spring_detected=spring_detected,
        upthrust_detected=upthrust_detected,
        obv_slope=obv_slope,
        ibs_mean=ibs_mean,
        wick_ratio=wick_ratio,
    )
