"""Unified core technical indicators for the finance domain.

Contains vectorized pure NumPy / pandas implementations of standard indicators:
- EMA (Exponential Moving Average)
- SMA (Simple Moving Average)
- RSI (Relative Strength Index with Wilder smoothing)
- ATR (Average True Range)
- Bollinger Bands
- MACD
- Stochastic Oscillator
- ADX / DMI
- Volume Profile (Fixed Range)

Shared across technical, trader/oms, and watchlist submodules to eliminate
redundant computations and duplicate logic.
"""
from __future__ import annotations
import math
from typing import NamedTuple, Optional
import numpy as np
import pandas as pd


# ── Moving Averages ───────────────────────────────────────────────────────────

def calc_ema(series: pd.Series, span: int) -> pd.Series:
    """Calculate Exponential Moving Average (EMA)."""
    return series.ewm(span=span, adjust=False).mean()


def calc_sma(series: pd.Series, period: int) -> pd.Series:
    """Calculate Simple Moving Average (SMA)."""
    return series.rolling(period).mean()


# ── RSI ───────────────────────────────────────────────────────────────────────

def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Relative Strength Index (RSI) using Wilder EWM smoothing."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("inf"))
    return 100 - (100 / (1 + rs))


# ── ATR ───────────────────────────────────────────────────────────────────────

def calc_atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average True Range (ATR) series."""
    h = df["High"]
    l = df["Low"]
    c = df["Close"]

    tr = pd.concat(
        [h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()],
        axis=1,
    ).max(axis=1)

    return tr.rolling(period).mean()


def calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Calculate latest scalar ATR value."""
    h = df["High"]
    l = df["Low"]
    if len(df) < period:
        return float((h.iloc[-1] - l.iloc[-1]) * 0.5)

    atr_s = calc_atr_series(df, period)
    val = atr_s.iloc[-1]
    return float(val) if not math.isnan(val) else float((h.iloc[-1] - l.iloc[-1]) * 0.5)


# ── Bollinger Bands ───────────────────────────────────────────────────────────

class BollingerBands(NamedTuple):
    upper: pd.Series
    mid: pd.Series
    lower: pd.Series
    width: pd.Series
    pct_b: pd.Series


def calc_bollinger(
    close: pd.Series,
    period: int = 20,
    num_std: float = 2.0,
) -> BollingerBands:
    """Calculate Bollinger Bands."""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = (upper - lower) / mid
    denom = (upper - lower).replace(0, np.nan)
    pct_b = (close - lower) / denom
    return BollingerBands(upper=upper, mid=mid, lower=lower, width=width, pct_b=pct_b)


# ── MACD ──────────────────────────────────────────────────────────────────────

class MACDResult(NamedTuple):
    macd: pd.Series
    signal: pd.Series
    hist: pd.Series


def calc_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> MACDResult:
    """Calculate MACD line, Signal line, and Histogram."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
    hist = macd_line - signal_line
    return MACDResult(macd=macd_line, signal=signal_line, hist=hist)


# ── Stochastic Oscillator ─────────────────────────────────────────────────────

class StochResult(NamedTuple):
    k: pd.Series
    d: pd.Series


def calc_stochastic(
    df: pd.DataFrame,
    k_period: int = 14,
    d_period: int = 3,
) -> StochResult:
    """Calculate Fast Stochastic (%K, %D)."""
    low_min = df["Low"].rolling(k_period).min()
    high_max = df["High"].rolling(k_period).max()
    denom = (high_max - low_min).replace(0, np.nan)
    fast_k = 100 * ((df["Close"] - low_min) / denom)
    fast_d = fast_k.rolling(d_period).mean()
    return StochResult(k=fast_k, d=fast_d)


# ── ADX / DMI ─────────────────────────────────────────────────────────────────

class ADXResult(NamedTuple):
    adx: pd.Series
    di_plus: pd.Series
    di_minus: pd.Series


def calc_adx(df: pd.DataFrame, period: int = 14) -> ADXResult:
    """Calculate Average Directional Index (ADX), +DI, and -DI."""
    h = df["High"]
    l = df["Low"]
    c = df["Close"]

    up_move = h - h.shift(1)
    down_move = l.shift(1) - l

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = pd.concat(
        [h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()],
        axis=1,
    ).max(axis=1)

    tr_smooth = pd.Series(tr).ewm(com=period - 1, min_periods=period).mean()
    plus_di = 100 * (pd.Series(plus_dm, index=df.index).ewm(com=period - 1, min_periods=period).mean() / tr_smooth.replace(0, np.nan))
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).ewm(com=period - 1, min_periods=period).mean() / tr_smooth.replace(0, np.nan))

    dx_denom = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * ((plus_di - minus_di).abs() / dx_denom)
    adx = dx.ewm(com=period - 1, min_periods=period).mean()

    return ADXResult(adx=adx, di_plus=plus_di, di_minus=minus_di)


# ── Volume Profile (Fixed Range) ──────────────────────────────────────────────

class VolumeProfileCore(NamedTuple):
    poc: float
    vah: float
    val: float
    bins: int
    lookback: int


def calc_volume_profile(
    df: pd.DataFrame,
    lookback: int = 100,
    num_bins: int = 50,
    value_area_pct: float = 0.70,
) -> VolumeProfileCore:
    """Compute Fixed-Range Volume Profile (POC, VAH, VAL)."""
    window = df.tail(lookback)
    price_min = float(window["Low"].min())
    price_max = float(window["High"].max())

    if price_max <= price_min:
        mid = (price_min + price_max) / 2
        return VolumeProfileCore(poc=mid, vah=mid, val=mid, bins=num_bins, lookback=lookback)

    edges = np.linspace(price_min, price_max, num_bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2
    vol_profile = np.zeros(num_bins, dtype=float)

    for _, row in window.iterrows():
        lo, hi, vol = float(row["Low"]), float(row["High"]), float(row["Volume"])
        if hi <= lo or vol <= 0:
            continue
        mask = (edges[1:] >= lo) & (edges[:-1] <= hi)
        n_bins = int(mask.sum())
        if n_bins > 0:
            vol_profile[mask] += vol / n_bins

    poc_idx = int(np.argmax(vol_profile))
    poc = float(centers[poc_idx])

    total_vol = vol_profile.sum()
    target = total_vol * value_area_pct
    lo_idx = poc_idx
    hi_idx = poc_idx
    accumulated = float(vol_profile[poc_idx])

    while accumulated < target and (lo_idx > 0 or hi_idx < num_bins - 1):
        lo_vol = vol_profile[lo_idx - 1] if lo_idx > 0 else 0.0
        hi_vol = vol_profile[hi_idx + 1] if hi_idx < num_bins - 1 else 0.0
        if lo_vol >= hi_vol and lo_idx > 0:
            lo_idx -= 1
            accumulated += vol_profile[lo_idx]
        elif hi_idx < num_bins - 1:
            hi_idx += 1
            accumulated += vol_profile[hi_idx]
        else:
            break

    val = float(centers[lo_idx])
    vah = float(centers[hi_idx])

    return VolumeProfileCore(poc=poc, vah=vah, val=val, bins=num_bins, lookback=lookback)
