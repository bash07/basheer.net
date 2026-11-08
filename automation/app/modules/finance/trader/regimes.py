"""Institutional Market Regime Classification & Dynamic Routing Engine.

Implements the 8-regime taxonomy and dynamic routing matrix defined in
STRATEGY_PLAYBOOK.md (Section 3: Institutional Strategy Playbook & Market Regime Selection Matrix).

The 8 Market Regimes:
    1. Orderly Trend Expansion     (ADX >= 22, aligned EMA 20/50/200 slope, HH/HL)
    2. Horizontal Range Chop       (ADX < 20, flat EMAs, price oscillating between VAH/VAL)
    3. High-Volatility Stop Raid   (Sharp wick sweep >= 40% of EQL/EQH, volume spike)
    4. Parabolic Blow-Off / Cascade (ADX > 45, extreme RSI > 80/< 20, detached from EMA 20)
    5. Volatility Squeeze (Coil)   (BB inside Keltner Channels, multi-bar low ATR)
    6. Broadening Megaphone        (Expanding swings: HH and LL, high ATR, erratic bias)
    7. Creeping Drift / Melt-Up    (Low-moderate ADX 15-20, steady EMA slope, overlapping bars)
    8. Macro News Shock / Vacuum   (Extreme single-bar range > 4x ATR, volume shock)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Set

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Canonical Regime Names ────────────────────────────────────────────────────
REGIME_TREND_EXPANSION   = "Orderly Trend Expansion"
REGIME_RANGE_CHOP        = "Horizontal Range Chop"
REGIME_STOP_RAID         = "High-Volatility Stop Raid"
REGIME_PARABOLIC         = "Parabolic Blow-Off / Cascade"
REGIME_SQUEEZE           = "Volatility Squeeze (Coil)"
REGIME_BROADENING        = "Broadening Megaphone"
REGIME_CREEPING_DRIFT    = "Creeping Drift / Melt-Up"
REGIME_MACRO_NEWS        = "Macro News Shock / Vacuum"

ALL_REGIME_NAMES = [
    REGIME_TREND_EXPANSION,
    REGIME_RANGE_CHOP,
    REGIME_STOP_RAID,
    REGIME_PARABOLIC,
    REGIME_SQUEEZE,
    REGIME_BROADENING,
    REGIME_CREEPING_DRIFT,
    REGIME_MACRO_NEWS,
]

# ── Macro Trend Environment (Tri-State Regime) ────────────────────────────────
MACRO_BULL    = "Bull"
MACRO_BEAR    = "Bear"
MACRO_RANGING = "Ranging"

ALL_MACRO_REGIMES = [MACRO_BULL, MACRO_BEAR, MACRO_RANGING]


def classify_macro_regime(bias_label: str, adx: float = 20.0, micro_regime: str = "") -> str:
    """Classify the macro market environment into Bull, Bear, or Ranging.

    Parameters:
      - bias_label: Directional bias string (e.g. 'strong_bull', 'moderate_bear', 'neutral')
      - adx: ADX trend strength indicator (adx < 18 indicates low trend momentum)
      - micro_regime: The 8-regime microstructure state (e.g. 'Horizontal Range Chop')

    Returns:
      - 'Bull' if bullish directional bias with trending momentum
      - 'Bear' if bearish directional bias with trending momentum
      - 'Ranging' if neutral bias, flat momentum, chop, or volatility squeeze
    """
    bias_str = (bias_label or "").lower()

    # Explicit consolidation chop or squeeze states default to Ranging
    if micro_regime in (REGIME_RANGE_CHOP, REGIME_SQUEEZE) or bias_str == "neutral":
        return MACRO_RANGING

    if "bull" in bias_str:
        return MACRO_BULL if (adx >= 18.0 or micro_regime in (REGIME_TREND_EXPANSION, REGIME_CREEPING_DRIFT)) else MACRO_RANGING
    if "bear" in bias_str:
        return MACRO_BEAR if (adx >= 18.0 or micro_regime in (REGIME_TREND_EXPANSION, REGIME_CREEPING_DRIFT)) else MACRO_RANGING

    return MACRO_RANGING

# ── Dynamic Regime Router Rules (Playbook Section 3.1) ─────────────────────────
# Initial theoretical reference from STRATEGY_PLAYBOOK.md.
# NOTE: During backtesting, all strategies are tested across ALL regimes WITHOUT
# prohibition, allowing empirical data collection to calibrate router rules later.
PROHIBITED_SETUPS_BY_REGIME: dict[str, Set[str]] = {
    REGIME_TREND_EXPANSION: {
        "Range Liquidity Sweep",
    },
    REGIME_RANGE_CHOP: {
        "OB Retracement",
        "EMA Pullback + Hidden Div",
        "Displacement + FVG",
    },
    REGIME_STOP_RAID: {
        "EMA Pullback + Hidden Div",
    },
    REGIME_PARABOLIC: {
        "Range Liquidity Sweep",
        "OB Retracement",
        "EMA Pullback + Hidden Div",
        "Displacement + FVG",
    },
    REGIME_SQUEEZE: {
        "Range Liquidity Sweep",
        "OB Retracement",
        "Displacement + FVG",
        "EMA Pullback + Hidden Div",
    },
    REGIME_BROADENING: {
        "OB Retracement",
        "EMA Pullback + Hidden Div",
        "Displacement + FVG",
    },
    REGIME_CREEPING_DRIFT: {
        "OB Retracement",
        "Liquidity Sweep + FVG",
    },
    REGIME_MACRO_NEWS: {
        "OB Retracement",
        "Liquidity Sweep + FVG",
        "Displacement + FVG",
        "Range Liquidity Sweep",
        "EMA Pullback + Hidden Div",
    },
}

RECOMMENDED_SETUPS_BY_REGIME: dict[str, Set[str]] = {
    REGIME_TREND_EXPANSION: {
        "OB Retracement",
        "Liquidity Sweep + FVG",
        "Displacement + FVG",
    },
    REGIME_RANGE_CHOP: {
        "Range Liquidity Sweep",
    },
    REGIME_STOP_RAID: {
        "Liquidity Sweep + FVG",
        "Range Liquidity Sweep",
    },
    REGIME_PARABOLIC: set(),
    REGIME_SQUEEZE: set(),
    REGIME_BROADENING: set(),
    REGIME_CREEPING_DRIFT: {
        "EMA Pullback + Hidden Div",
    },
    REGIME_MACRO_NEWS: set(),
}


@dataclass
class MarketRegimeInfo:
    """Classified market regime characteristics at a specific bar."""
    name: str
    code: str
    adx: float
    atr: float
    details: str = ""
    prohibited_setups: Set[str] = field(default_factory=set)
    recommended_setups: Set[str] = field(default_factory=set)


def is_setup_permitted(regime_name: str, setup_name: str) -> bool:
    """Return True if the dynamic router permits setup_name under regime_name."""
    prohibited = PROHIBITED_SETUPS_BY_REGIME.get(regime_name, set())
    return setup_name not in prohibited


# ── High-Performance Precomputation for Replay Backtesting ────────────────────

def precompute_regime_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Precompute all indicators required for O(1) regime classification per bar.

    Attaches the following columns to df if not already present:
      - atr_14: Wilder / SMA ATR-14
      - adx_14: ADX-14 trend strength
      - rsi_14: RSI-14
      - ema_20, ema_50, ema_200: Moving averages
      - bb_upper, bb_lower, bb_mid, bb_width: Bollinger Bands (20, 2.0 std)
      - kc_upper, kc_lower: Keltner Channels (EMA 20 +- 1.5 * ATR)
      - bar_range: High - Low
    """
    df = df.copy()
    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]

    # Bar range
    if "bar_range" not in df.columns:
        df["bar_range"] = high - low

    # ATR-14
    if "atr_14" not in df.columns:
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low  - close.shift(1)).abs()
        tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df["atr_14"] = tr.rolling(14).mean().bfill()

    # RSI-14
    if "rsi_14" not in df.columns:
        delta = close.diff()
        gain  = delta.where(delta > 0, 0.0)
        loss  = (-delta).where(delta < 0, 0.0)
        ag    = gain.ewm(com=13, min_periods=14).mean()
        al    = loss.ewm(com=13, min_periods=14).mean()
        rs    = ag / al.replace(0, float("inf"))
        df["rsi_14"] = (100 - 100 / (1 + rs)).bfill()

    # EMAs
    if "ema_20" not in df.columns:
        df["ema_20"] = close.ewm(span=20, adjust=False).mean()
    if "ema_50" not in df.columns:
        df["ema_50"] = close.ewm(span=50, adjust=False).mean()
    if "ema_200" not in df.columns:
        df["ema_200"] = close.ewm(span=200, adjust=False).mean()

    # ADX-14
    if "adx_14" not in df.columns:
        up_move   = high.diff()
        down_move = -low.diff()
        plus_dm   = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm  = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low  - close.shift(1)).abs()
        tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        tr_smooth       = pd.Series(tr, index=df.index).ewm(com=13, min_periods=14).mean()
        plus_dm_smooth  = pd.Series(plus_dm, index=df.index).ewm(com=13, min_periods=14).mean()
        minus_dm_smooth = pd.Series(minus_dm, index=df.index).ewm(com=13, min_periods=14).mean()

        plus_di  = 100 * (plus_dm_smooth / tr_smooth.replace(0, np.nan))
        minus_di = 100 * (minus_dm_smooth / tr_smooth.replace(0, np.nan))
        dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        df["adx_14"] = dx.ewm(com=13, min_periods=14).mean().fillna(20.0)

    # Bollinger Bands (20, 2.0 std)
    if "bb_mid" not in df.columns:
        std20 = close.rolling(20).std()
        df["bb_mid"]   = close.rolling(20).mean().bfill()
        df["bb_upper"] = df["bb_mid"] + (2.0 * std20)
        df["bb_lower"] = df["bb_mid"] - (2.0 * std20)
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"].replace(0, np.nan)

    # Keltner Channels (EMA 20 +- 1.5 * ATR 14)
    if "kc_upper" not in df.columns:
        df["kc_upper"] = df["ema_20"] + (1.5 * df["atr_14"])
        df["kc_lower"] = df["ema_20"] - (1.5 * df["atr_14"])

    return df


# ── Bar Classifier ────────────────────────────────────────────────────────────

def classify_bar_regime(
    df: pd.DataFrame,
    bar_i: int = -1,
    lookback: int = 25,
) -> MarketRegimeInfo:
    """Classify the prevailing market regime at `bar_i` of `df`.

    Evaluates the hierarchical flowchart from STRATEGY_PLAYBOOK.md:
      1. Volatility Anomalies:
         - Macro News / Vacuum Shock (single bar range > 4.0x ATR)
         - Parabolic Blow-Off (ADX > 45, extreme RSI, detached from EMA)
         - Volatility Squeeze (BB inside KC, or compressed BB width)
         - Broadening Megaphone (expanding HH & LL pivots with high dispersion)
         - High-Volatility Stop Raid (rejection wick >= 40% sweeping key level)
      2. Trending vs Ranging:
         - Creeping Drift (ADX 15-22, persistent slope, tiny overlapping bars)
         - Orderly Trend Expansion (ADX >= 22, stacked EMAs)
         - Horizontal Range Chop (ADX < 20, flat EMAs, bounded oscillation)
    """
    n = len(df)
    if n == 0:
        return MarketRegimeInfo(
            name=REGIME_RANGE_CHOP,
            code="R2_CHOP",
            adx=15.0,
            atr=0.0,
            details="Empty data fallback",
            prohibited_setups=PROHIBITED_SETUPS_BY_REGIME[REGIME_RANGE_CHOP],
            recommended_setups=RECOMMENDED_SETUPS_BY_REGIME[REGIME_RANGE_CHOP],
        )

    # Ensure valid bar_i
    if bar_i < 0:
        bar_i = n + bar_i
    bar_i = min(max(0, bar_i), n - 1)

    # Current bar metrics
    close   = float(df["Close"].iloc[bar_i])
    high    = float(df["High"].iloc[bar_i])
    low     = float(df["Low"].iloc[bar_i])
    open_   = float(df["Open"].iloc[bar_i])
    atr     = float(df["atr_14"].iloc[bar_i]) if "atr_14" in df.columns else (high - low)
    adx     = float(df["adx_14"].iloc[bar_i]) if "adx_14" in df.columns else 20.0
    rsi     = float(df["rsi_14"].iloc[bar_i]) if "rsi_14" in df.columns else 50.0
    ema20   = float(df["ema_20"].iloc[bar_i]) if "ema_20" in df.columns else close
    ema50   = float(df["ema_50"].iloc[bar_i]) if "ema_50" in df.columns else close
    bar_rng = high - low

    safe_atr = max(atr, 1e-6)

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Macro News Shock / Liquidity Vacuum (Regime 8)
    # Giant single-bar range > 4.0x ATR or giant wick > 3.0x ATR
    # ─────────────────────────────────────────────────────────────────────────
    if bar_rng >= 4.0 * safe_atr:
        return MarketRegimeInfo(
            name=REGIME_MACRO_NEWS,
            code="R8_NEWS",
            adx=adx,
            atr=atr,
            details=f"Bar range {bar_rng:.2f} >= 4.0x ATR ({safe_atr:.2f})",
            prohibited_setups=PROHIBITED_SETUPS_BY_REGIME[REGIME_MACRO_NEWS],
            recommended_setups=RECOMMENDED_SETUPS_BY_REGIME[REGIME_MACRO_NEWS],
        )

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Parabolic Blow-Off / Cascade (Regime 4)
    # Extreme ADX > 45, RSI pinned > 80 or < 20, detached from EMA 20 by > 2.5x ATR
    # ─────────────────────────────────────────────────────────────────────────
    dist_ema20 = abs(close - ema20)
    if adx > 45.0 and (rsi > 80.0 or rsi < 20.0) and dist_ema20 > 2.5 * safe_atr:
        return MarketRegimeInfo(
            name=REGIME_PARABOLIC,
            code="R4_PARABOLIC",
            adx=adx,
            atr=atr,
            details=f"ADX={adx:.1f} > 45, RSI={rsi:.1f}, dist to EMA20={dist_ema20/safe_atr:.1f}x ATR",
            prohibited_setups=PROHIBITED_SETUPS_BY_REGIME[REGIME_PARABOLIC],
            recommended_setups=RECOMMENDED_SETUPS_BY_REGIME[REGIME_PARABOLIC],
        )

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Volatility Squeeze / Coil (Regime 5)
    # Bollinger Bands inside Keltner Channels OR BB width at multi-bar lows
    # ─────────────────────────────────────────────────────────────────────────
    has_bb_kc = "bb_upper" in df.columns and "kc_upper" in df.columns
    is_bb_kc_squeeze = False
    if has_bb_kc:
        bb_u = float(df["bb_upper"].iloc[bar_i])
        bb_l = float(df["bb_lower"].iloc[bar_i])
        kc_u = float(df["kc_upper"].iloc[bar_i])
        kc_l = float(df["kc_lower"].iloc[bar_i])
        if bb_u < kc_u and bb_l > kc_l:
            is_bb_kc_squeeze = True

    # Check 20-bar range compression (< 1.2x ATR total movement across 5 bars)
    sub_slice = df.iloc[max(0, bar_i - 5): bar_i + 1]
    is_range_squeeze = False
    if len(sub_slice) >= 5:
        total_5bar_span = float(sub_slice["High"].max() - sub_slice["Low"].min())
        if total_5bar_span < 1.2 * safe_atr:
            is_range_squeeze = True

    if is_bb_kc_squeeze or (is_range_squeeze and adx < 18.0):
        return MarketRegimeInfo(
            name=REGIME_SQUEEZE,
            code="R5_SQUEEZE",
            adx=adx,
            atr=atr,
            details="Bollinger Bands inside Keltner Channels / multi-bar compression",
            prohibited_setups=PROHIBITED_SETUPS_BY_REGIME[REGIME_SQUEEZE],
            recommended_setups=RECOMMENDED_SETUPS_BY_REGIME[REGIME_SQUEEZE],
        )

    # ─────────────────────────────────────────────────────────────────────────
    # 4. High-Volatility Stop Raid (Regime 3)
    # Sharp wick sweep (>= 40% wick) piercing recent 20-bar high/low
    # ─────────────────────────────────────────────────────────────────────────
    upper_wick = high - max(open_, close)
    lower_wick = min(open_, close) - low
    max_wick = max(upper_wick, lower_wick)
    wick_ratio = max_wick / bar_rng if bar_rng > 0 else 0.0

    if wick_ratio >= 0.40 and bar_i >= 20:
        prior_20 = df.iloc[bar_i - 20: bar_i]
        prior_hi = float(prior_20["High"].max())
        prior_lo = float(prior_20["Low"].min())
        # Swept above prior high and rejected, or swept below prior low and rejected
        is_sweep = (high > prior_hi and close < prior_hi) or (low < prior_lo and close > prior_lo)
        if is_sweep and bar_rng >= 1.0 * safe_atr:
            return MarketRegimeInfo(
                name=REGIME_STOP_RAID,
                code="R3_STOP_RAID",
                adx=adx,
                atr=atr,
                details=f"Sweep wick {wick_ratio*100:.0f}% piercing 20-bar extreme",
                prohibited_setups=PROHIBITED_SETUPS_BY_REGIME[REGIME_STOP_RAID],
                recommended_setups=RECOMMENDED_SETUPS_BY_REGIME[REGIME_STOP_RAID],
            )

    # ─────────────────────────────────────────────────────────────────────────
    # 5. Broadening Megaphone (Regime 6)
    # Expanding swings: recent 15-bar high > prior 15-bar high AND
    # recent 15-bar low < prior 15-bar low with high ATR & non-aligned EMAs
    # ─────────────────────────────────────────────────────────────────────────
    if bar_i >= 30:
        recent_half = df.iloc[bar_i - 15: bar_i + 1]
        prior_half  = df.iloc[bar_i - 30: bar_i - 15]
        rec_hi = float(recent_half["High"].max())
        rec_lo = float(recent_half["Low"].min())
        pri_hi = float(prior_half["High"].max())
        pri_lo = float(prior_half["Low"].min())
        if rec_hi > pri_hi and rec_lo < pri_lo and adx < 25.0:
            return MarketRegimeInfo(
                name=REGIME_BROADENING,
                code="R6_BROADENING",
                adx=adx,
                atr=atr,
                details="Expanding high and low swings with neutral/whipsaw trend",
                prohibited_setups=PROHIBITED_SETUPS_BY_REGIME[REGIME_BROADENING],
                recommended_setups=RECOMMENDED_SETUPS_BY_REGIME[REGIME_BROADENING],
            )

    # ─────────────────────────────────────────────────────────────────────────
    # 6. Creeping Drift / Melt-Up (Regime 7)
    # Low-moderate ADX (15-22), persistent EMA slope, tiny overlapping bars
    # ─────────────────────────────────────────────────────────────────────────
    ema_slope_bull = ema20 > ema50
    ema_slope_bear = ema20 < ema50
    if 15.0 <= adx < 22.0 and (ema_slope_bull or ema_slope_bear):
        # Check if recent 5 bars have small ranges and modest RSI
        rec_5 = df.iloc[max(0, bar_i - 5): bar_i + 1]
        avg_rec_rng = float(rec_5["bar_range"].mean()) if "bar_range" in rec_5.columns else safe_atr
        if avg_rec_rng <= 1.0 * safe_atr and (35.0 <= rsi <= 65.0):
            return MarketRegimeInfo(
                name=REGIME_CREEPING_DRIFT,
                code="R7_DRIFT",
                adx=adx,
                atr=atr,
                details=f"ADX={adx:.1f}, small overlapping bars ({avg_rec_rng/safe_atr:.2f}x ATR), RSI={rsi:.1f}",
                prohibited_setups=PROHIBITED_SETUPS_BY_REGIME[REGIME_CREEPING_DRIFT],
                recommended_setups=RECOMMENDED_SETUPS_BY_REGIME[REGIME_CREEPING_DRIFT],
            )

    # ─────────────────────────────────────────────────────────────────────────
    # 7. Orderly Trend Expansion (Regime 1)
    # ADX >= 22.0 and directional EMA alignment
    # ─────────────────────────────────────────────────────────────────────────
    is_trend_aligned = (close > ema20 > ema50) or (close < ema20 < ema50)
    if adx >= 22.0 or (adx >= 20.0 and is_trend_aligned):
        return MarketRegimeInfo(
            name=REGIME_TREND_EXPANSION,
            code="R1_TREND",
            adx=adx,
            atr=atr,
            details=f"ADX={adx:.1f} >= 22, aligned EMA trend structure",
            prohibited_setups=PROHIBITED_SETUPS_BY_REGIME[REGIME_TREND_EXPANSION],
            recommended_setups=RECOMMENDED_SETUPS_BY_REGIME[REGIME_TREND_EXPANSION],
        )

    # ─────────────────────────────────────────────────────────────────────────
    # 8. Horizontal Range Chop (Regime 2)
    # ADX < 20.0, non-trending, bounded oscillation
    # ─────────────────────────────────────────────────────────────────────────
    return MarketRegimeInfo(
        name=REGIME_RANGE_CHOP,
        code="R2_CHOP",
        adx=adx,
        atr=atr,
        details=f"ADX={adx:.1f} < 20, non-trending consolidation chop",
        prohibited_setups=PROHIBITED_SETUPS_BY_REGIME[REGIME_RANGE_CHOP],
        recommended_setups=RECOMMENDED_SETUPS_BY_REGIME[REGIME_RANGE_CHOP],
    )


# Backward-compatible alias
classify_market_regime = classify_bar_regime
