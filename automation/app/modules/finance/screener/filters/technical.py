"""Technical analysis filter calculators for Concepts A, B, and C."""
from __future__ import annotations

import logging
from typing import Optional
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_concept_a_relative_strength(
    df: pd.DataFrame,
    df_benchmark: Optional[pd.DataFrame] = None,
) -> Optional[dict]:
    """Concept A: Relative Strength & Trend Leaders (Minervini / Stage 2).

    Rules:
      - Price > EMA50 > EMA200
      - Within 15% of 52-week High
      - Positive 3-Month / 6-Month return outperforming benchmark (Nifty 50)
    """
    if len(df) < 120:
        return None

    close = df["Close"].values
    high = df["High"].values
    current_price = float(close[-1])

    # Moving averages
    ema50 = pd.Series(close).ewm(span=50, adjust=False).mean().values[-1]
    ema200 = pd.Series(close).ewm(span=200, adjust=False).mean().values[-1] if len(close) >= 200 else pd.Series(close).ewm(span=len(close), adjust=False).mean().values[-1]

    # Trend structure check
    in_stage2 = (current_price > ema50) and (ema50 > ema200)

    # 52-week high check (approx 250 trading bars)
    lookback_52w = min(len(high), 250)
    high_52w = float(np.max(high[-lookback_52w:]))
    dist_from_high_pct = (high_52w - current_price) / high_52w if high_52w > 0 else 1.0

    # 3-Month (60 bars) return
    bars_3m = min(len(close) - 1, 60)
    ret_3m = (current_price - close[-bars_3m]) / close[-bars_3m]

    # Benchmark outperformance
    bench_ret_3m = 0.0
    if df_benchmark is not None and len(df_benchmark) > bars_3m:
        b_close = df_benchmark["Close"].values
        bench_ret_3m = (b_close[-1] - b_close[-bars_3m]) / b_close[-bars_3m]
    rel_strength_3m = ret_3m - bench_ret_3m

    # Score calculation (0.0 to 10.0)
    # Passed if Stage 2 + near high + positive relative strength
    score = 0.0
    if in_stage2:
        score += 4.0
    if dist_from_high_pct <= 0.15:
        score += 3.0 * (1.0 - (dist_from_high_pct / 0.15))
    if rel_strength_3m > 0:
        score += min(3.0, rel_strength_3m * 10.0)

    passed = (in_stage2 or current_price > ema50) and (dist_from_high_pct <= 0.18) and (rel_strength_3m > -0.05)
    if not passed or score < 4.5:
        return None

    return {
        "concept": "concept_a",
        "name": "Relative Strength Leader",
        "score": round(score, 2),
        "target_strategies": ["OBRetracement", "DisplacementFVG", "EMAPullback", "CreepingDrift"],
        "metrics": {
            "current_price": current_price,
            "ema50": round(ema50, 2),
            "ema200": round(ema200, 2),
            "dist_from_52w_high_pct": round(dist_from_high_pct * 100, 2),
            "ret_3m_pct": round(ret_3m * 100, 2),
            "rs_vs_bench_3m_pct": round(rel_strength_3m * 100, 2),
        },
    }


def compute_concept_b_volatility_squeeze(df: pd.DataFrame) -> Optional[dict]:
    """Concept B: Volatility Squeeze / Coiling (BB Squeeze, NR7, ATR drop).

    Rules:
      - Bollinger Band Width (BBW) at or near multi-month lows (coiling)
      - NR7 (Narrowest Range in 7 sessions) on daily
      - ATR(14) compressing relative to ATR(50)
    """
    if len(df) < 50:
        return None

    close = df["Close"].values
    high = df["High"].values
    low = df["Low"].values

    # Bollinger Band Width
    sma20 = pd.Series(close).rolling(window=20).mean()
    std20 = pd.Series(close).rolling(window=20).std()
    bbw = (2 * 2 * std20) / sma20  # 4 * std / sma
    bbw_clean = bbw.dropna().values
    if len(bbw_clean) < 30:
        return None

    current_bbw = float(bbw_clean[-1])
    min_bbw_60 = float(np.min(bbw_clean[-min(len(bbw_clean), 60):]))
    is_bb_squeeze = current_bbw <= (min_bbw_60 * 1.35)

    # NR7 check (last bar range vs previous 6 bars)
    ranges = high - low
    last_range = ranges[-1]
    is_nr7 = False
    if len(ranges) >= 7:
        is_nr7 = last_range <= np.min(ranges[-7:])

    # ATR(14) vs ATR(50)
    tr = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    atr14 = float(pd.Series(tr).ewm(span=14).mean().iloc[-1])
    atr50 = float(pd.Series(tr).ewm(span=min(len(tr), 50)).mean().iloc[-1])
    atr_ratio = atr14 / atr50 if atr50 > 0 else 1.0

    score = 0.0
    if is_bb_squeeze:
        score += 4.5
    if is_nr7:
        score += 3.0
    if atr_ratio < 0.85:
        score += 2.5

    passed = (is_bb_squeeze or is_nr7) and (atr_ratio < 1.05)
    if not passed or score < 4.5:
        return None

    return {
        "concept": "concept_b",
        "name": "Volatility Squeeze",
        "score": round(score, 2),
        "target_strategies": ["WyckoffAccumulation", "WyckoffDistribution", "BreakdownRetest", "DisplacementFVG"],
        "metrics": {
            "current_bbw": round(current_bbw, 4),
            "min_bbw_60": round(min_bbw_60, 4),
            "is_nr7": is_nr7,
            "atr14_to_atr50_ratio": round(atr_ratio, 2),
        },
    }


def compute_concept_c_volume_flow(df: pd.DataFrame) -> Optional[dict]:
    """Concept C: Institutional Volume & Liquidity Surges (RVOL & Flow).

    Rules:
      - RVOL >= 1.5x of 20-day average volume
      - Volume Up/Down accumulation ratio > 1.3 over 20 sessions
      - Price closing strong in the upper 40% of the bar
    """
    if len(df) < 25:
        return None

    close = df["Close"].values
    high = df["High"].values
    low = df["Low"].values
    volume = df["Volume"].values

    curr_vol = float(volume[-1])
    avg_vol20 = float(np.mean(volume[-21:-1])) if len(volume) >= 21 else float(np.mean(volume[:-1]))
    if avg_vol20 <= 0:
        return None

    rvol = curr_vol / avg_vol20

    # Up-day vs down-day volume accumulation over 20 sessions
    deltas = np.diff(close[-21:])
    vols = volume[-20:]
    up_vol = float(np.sum(vols[deltas > 0])) if np.any(deltas > 0) else 1.0
    down_vol = float(np.sum(vols[deltas < 0])) if np.any(deltas < 0) else 1.0
    up_down_ratio = up_vol / down_vol if down_vol > 0 else 2.0

    # Bar closing strength
    bar_range = high[-1] - low[-1]
    closing_loc = (close[-1] - low[-1]) / bar_range if bar_range > 0 else 0.5

    score = 0.0
    if rvol >= 1.5:
        score += min(5.0, rvol * 2.0)
    if up_down_ratio >= 1.3:
        score += min(3.0, (up_down_ratio - 1.0) * 2.5)
    if closing_loc >= 0.60:
        score += 2.0

    passed = (rvol >= 1.4 or up_down_ratio >= 1.4) and (score >= 4.5)
    if not passed:
        return None

    return {
        "concept": "concept_c",
        "name": "Institutional Volume Surge",
        "score": round(score, 2),
        "target_strategies": ["LiquiditySweepFVG", "RangeLiquiditySweep", "OBRetracement"],
        "metrics": {
            "rvol": round(rvol, 2),
            "up_down_vol_ratio": round(up_down_ratio, 2),
            "closing_location_pct": round(closing_loc * 100, 1),
        },
    }
