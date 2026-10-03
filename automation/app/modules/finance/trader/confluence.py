"""Confluence scoring engine for the finance/trader module.

Two layers of confluence are computed for each trading zone (OB midpoint or FVG zone):

  Layer A — Classical S/R:
    1. Swing pivot highs/lows (LTF)
    2. Prior Day High/Low/Close and Prior Week High/Low (HTF)
    3. Round / psychological numbers (dynamic unit by price magnitude)
    4. EMA 21/50/200 dynamic S/R (computed here from LTF close)

  Layer B — Volume Profile (FRVP from OHLCV):
    - POC (Point of Control)   — max-volume price level
    - VAH / VAL                — Value Area boundaries (70% of volume)
    - HVN / LVN                — High/Low Volume Nodes

All heavy imports (numpy, scipy) are LAZY.

Usage:
    vp = compute_volume_profile(df_ltf, lookback=100)
    sr = get_sr_levels(df_ltf, df_htf, current_price)
    report = score_zone(zone_low, zone_high, sr, vp)
"""
from __future__ import annotations

import logging

from .schemas import ConfluenceReport, SRLevel, VolumeProfile

logger = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────────────

_VP_BINS           = 50     # price buckets in the volume profile
_VP_VALUE_AREA_PCT = 0.70   # 70% of total volume = Value Area
_VP_HVN_MULT       = 1.5   # bins > mean × this → HVN
_VP_LVN_MULT       = 0.5   # bins < mean × this → LVN

_SR_PIVOT_ORDER    = 4     # argrelextrema half-width for swing pivots
_SR_PIVOT_BARS     = 40   # scan last N bars for swing pivots
_SR_TOLERANCE_PCT  = 0.003 # ±0.3% zone match tolerance for S/R labels

# S/R level weights (contribution to confluence score)
_W_PDH_PDL  = 1.00   # Prior Day High/Low
_W_PWH_PWL  = 1.00   # Prior Week High/Low
_W_SWING    = 0.75   # Swing pivot
_W_ROUND    = 0.50   # Round/psychological number
_W_EMA      = 0.50   # EMA level

# VP score contributions
_W_POC      = 2.00
_W_VAH_VAL  = 1.50
_W_HVN      = 0.75
_W_LVN      = 0.50   # LVN inside zone = fast-move boost

_SCORE_HIGH    = 7.0
_SCORE_MEDIUM  = 5.0


# ── Volume Profile ────────────────────────────────────────────────────────────

def compute_volume_profile(df_ltf, lookback: int) -> VolumeProfile:
    """Compute a Fixed-Range Volume Profile from OHLCV candles.

    Volume is distributed uniformly across each candle's High→Low range.
    lookback is the number of bars from the end of df_ltf to include.

    Args:
        df_ltf:   LTF OHLCV DataFrame
        lookback: 100 (swing) or 60 (intraday) — passed by caller

    Returns:
        VolumeProfile with poc, vah, val, hvn, lvn.
    """
    try:
        import numpy as np
    except ImportError:
        logger.warning("[trader.confluence] numpy not available — VP skipped")
        # Return neutral profile so scoring degrades gracefully
        mid = float(df_ltf["Close"].iloc[-1])
        return VolumeProfile(poc=mid, vah=mid * 1.01, val=mid * 0.99, bins=_VP_BINS, lookback=lookback)

    window     = df_ltf.tail(lookback)
    price_min  = float(window["Low"].min())
    price_max  = float(window["High"].max())

    if price_max <= price_min:
        mid = (price_min + price_max) / 2
        return VolumeProfile(poc=mid, vah=mid, val=mid, bins=_VP_BINS, lookback=lookback)

    bins        = _VP_BINS
    edges       = np.linspace(price_min, price_max, bins + 1)
    centers     = (edges[:-1] + edges[1:]) / 2
    vol_profile = np.zeros(bins, dtype=float)

    for _, row in window.iterrows():
        lo, hi, vol = float(row["Low"]), float(row["High"]), float(row["Volume"])
        if hi <= lo or vol <= 0:
            continue
        # Which bins does this candle's range overlap?
        mask = (edges[1:] >= lo) & (edges[:-1] <= hi)
        n_bins = int(mask.sum())
        if n_bins > 0:
            vol_profile[mask] += vol / n_bins

    poc_idx = int(np.argmax(vol_profile))
    poc     = float(centers[poc_idx])

    # Value Area — expand outward from POC until 70% of volume is captured
    total_vol   = vol_profile.sum()
    target      = total_vol * _VP_VALUE_AREA_PCT
    lo_idx      = poc_idx
    hi_idx      = poc_idx
    accumulated = float(vol_profile[poc_idx])

    while accumulated < target and (lo_idx > 0 or hi_idx < bins - 1):
        lo_vol = vol_profile[lo_idx - 1] if lo_idx > 0 else 0.0
        hi_vol = vol_profile[hi_idx + 1] if hi_idx < bins - 1 else 0.0
        if lo_vol >= hi_vol and lo_idx > 0:
            lo_idx -= 1
            accumulated += vol_profile[lo_idx]
        elif hi_idx < bins - 1:
            hi_idx += 1
            accumulated += vol_profile[hi_idx]
        else:
            break

    val = float(centers[lo_idx])
    vah = float(centers[hi_idx])

    # HVN / LVN
    mean_vol = vol_profile.mean()
    hvn = [float(centers[i]) for i in range(bins) if vol_profile[i] > mean_vol * _VP_HVN_MULT]
    lvn = [float(centers[i]) for i in range(bins) if vol_profile[i] < mean_vol * _VP_LVN_MULT]

    return VolumeProfile(poc=poc, vah=vah, val=val, hvn=hvn, lvn=lvn, bins=bins, lookback=lookback)


# ── Classical S/R Levels ──────────────────────────────────────────────────────

def get_sr_levels(df_ltf, df_htf, current_price: float) -> list[SRLevel]:
    """Collect all classical S/R levels relevant to the current price.

    Args:
        df_ltf:        LTF OHLCV DataFrame (4H / 1H)
        df_htf:        HTF OHLCV DataFrame (Weekly / Daily)
        current_price: Latest close price (for round-number calculation)

    Returns:
        List of SRLevel objects, each with .price, .label, .weight, .source
    """
    levels: list[SRLevel] = []

    levels.extend(_swing_pivot_levels(df_ltf))
    levels.extend(_prior_period_levels(df_htf))
    levels.extend(_round_number_levels(current_price))
    levels.extend(_ema_levels(df_ltf))

    return levels


# ── Confluence Scoring ────────────────────────────────────────────────────────

def score_zone(
    zone_low: float,
    zone_high: float,
    sr_levels: list[SRLevel],
    vp: VolumeProfile,
) -> ConfluenceReport:
    """Score an trading zone against S/R levels and the volume profile.

    Args:
        zone_low:  Lower bound of the OB or FVG zone
        zone_high: Upper bound of the OB or FVG zone
        sr_levels: Output of get_sr_levels()
        vp:        Output of compute_volume_profile()

    Returns:
        ConfluenceReport with .score, .sr_hits, .vp_hits, .label
    """
    score      = 0.0
    sr_hits:  list[str] = []
    vp_hits:  list[str] = []

    tol = max(
        (zone_high - zone_low) * 0.5,          # half-zone
        zone_low * _SR_TOLERANCE_PCT,           # or 0.3% of price
    )
    zone_mid = (zone_low + zone_high) / 2

    # ── Layer A: S/R Levels ───────────────────────────────────────────────────
    seen_labels: set[str] = set()
    for lvl in sr_levels:
        if zone_low - tol <= lvl.price <= zone_high + tol:
            label_key = f"{lvl.source}:{round(lvl.price, 2)}"
            if label_key not in seen_labels:
                score += lvl.weight
                sr_hits.append(f"{lvl.label} @ {_fmt(lvl.price, zone_mid)}")
                seen_labels.add(label_key)

    # ── Layer B: Volume Profile ───────────────────────────────────────────────
    vp_tol = tol * 1.5  # slightly wider tolerance for VP levels

    if abs(vp.poc - zone_mid) <= vp_tol:
        score += _W_POC
        vp_hits.append(f"POC @ {_fmt(vp.poc, zone_mid)} (peak volume)")
    elif abs(vp.vah - zone_mid) <= vp_tol:
        score += _W_VAH_VAL
        vp_hits.append(f"VAH @ {_fmt(vp.vah, zone_mid)} (value area top)")
    elif abs(vp.val - zone_mid) <= vp_tol:
        score += _W_VAH_VAL
        vp_hits.append(f"VAL @ {_fmt(vp.val, zone_mid)} (value area bottom)")

    hvn_nearby = [h for h in vp.hvn if zone_low - vp_tol * 2 < h < zone_high + vp_tol * 2]
    if hvn_nearby:
        score += _W_HVN
        vp_hits.append(f"HVN cluster near {_fmt(hvn_nearby[0], zone_mid)}")

    lvn_inside = [l for l in vp.lvn if zone_low * 0.998 < l < zone_high * 1.002]
    if lvn_inside:
        score += _W_LVN
        vp_hits.append("LVN within zone (fast price move expected)")

    score = min(score, 10.0)   # cap at 10
    label = (
        "high"   if score >= _SCORE_HIGH   else
        "medium" if score >= _SCORE_MEDIUM  else
        "low"
    )

    return ConfluenceReport(
        score=round(score, 2),
        sr_hits=sr_hits,
        vp_hits=vp_hits,
        poc=vp.poc,
        vah=vp.vah,
        val=vp.val,
        label=label,
    )


# ── Private helpers ───────────────────────────────────────────────────────────

def _swing_pivot_levels(df_ltf) -> list[SRLevel]:
    """Swing highs/lows from the last _SR_PIVOT_BARS on the LTF chart."""
    try:
        import numpy as np
        from scipy.signal import argrelextrema
    except ImportError:
        return []

    window = df_ltf.tail(_SR_PIVOT_BARS)
    high   = window["High"].values
    low    = window["Low"].values

    peak_idx   = argrelextrema(high, np.greater, order=_SR_PIVOT_ORDER)[0]
    trough_idx = argrelextrema(low,  np.less,    order=_SR_PIVOT_ORDER)[0]

    levels = []
    for i in peak_idx:
        levels.append(SRLevel(
            price=float(high[i]),
            label=f"Swing High @ {high[i]:,.4f}",
            weight=_W_SWING,
            source="pivot",
        ))
    for i in trough_idx:
        levels.append(SRLevel(
            price=float(low[i]),
            label=f"Swing Low @ {low[i]:,.4f}",
            weight=_W_SWING,
            source="pivot",
        ))
    return levels


def _prior_period_levels(df_htf) -> list[SRLevel]:
    """Prior Daily/Weekly H/L/C as institutional reference levels.

    These are the single most important external S/R reference levels
    in trading methodology — banks and algos are programmed to these levels.
    """
    if len(df_htf) < 2:
        return []

    prev = df_htf.iloc[-2]  # last COMPLETED candle
    curr = df_htf.iloc[-1]

    is_weekly = True  # caller passes correct HTF df; we label generically
    pfx = "PW" if is_weekly else "PD"

    levels = [
        SRLevel(price=float(prev["High"]),  label=f"{pfx}H @ {prev['High']:,.4f}",  weight=_W_PWH_PWL, source="prior_week"),
        SRLevel(price=float(prev["Low"]),   label=f"{pfx}L @ {prev['Low']:,.4f}",   weight=_W_PWH_PWL, source="prior_week"),
        SRLevel(price=float(prev["Close"]), label=f"{pfx}C @ {prev['Close']:,.4f}", weight=_W_PDH_PDL * 0.75, source="prior_week"),
    ]
    return levels


def _round_number_levels(price: float) -> list[SRLevel]:
    """Psychological / round number S/R levels near the current price.

    Dynamic unit based on price magnitude:
      < 10      → 0.5
      10–100    → 5
      100–1000  → 25
      1000–10000 → 100
      > 10000   → 500
    """
    if   price < 10:      unit = 0.5
    elif price < 100:     unit = 5.0
    elif price < 1_000:   unit = 25.0
    elif price < 10_000:  unit = 100.0
    else:                 unit = 500.0

    levels = []
    # Check the nearest 3 round levels above and below current price
    base = round(price / unit) * unit
    for mult in range(-3, 4):
        lvl_price = base + mult * unit
        if lvl_price > 0:
            levels.append(SRLevel(
                price=round(lvl_price, 4),
                label=f"Round {lvl_price:g}",
                weight=_W_ROUND,
                source="round",
            ))
    return levels


def _ema_levels(df_ltf) -> list[SRLevel]:
    """EMA 21/50/200 as dynamic support/resistance on the LTF chart."""
    close = df_ltf["Close"]
    levels = []
    for span, label in [(21, "EMA21"), (50, "EMA50"), (200, "EMA200")]:
        if len(close) >= span:
            ema_val = float(close.ewm(span=span, adjust=False).mean().iloc[-1])
            levels.append(SRLevel(
                price=round(ema_val, 6),
                label=f"{label} @ {ema_val:,.4f}",
                weight=_W_EMA,
                source="ema",
            ))
    return levels


def _fmt(price: float, reference: float) -> str:
    """Format a price with appropriate decimal places based on magnitude."""
    if reference >= 1000:
        return f"{price:,.2f}"
    elif reference >= 1:
        return f"{price:,.4f}"
    else:
        return f"{price:,.6f}"


# ── Public convenience: format a VP report as Telegram text ──────────────────

def format_vp_report(vp: VolumeProfile, sr_levels: list[SRLevel], ticker: str) -> str:
    """Format the volume profile and S/R levels as a Telegram Markdown message.

    Used by /confluence <ticker> command.
    """
    lines = [
        f"📊 *Confluence Report — {ticker}* ({vp.lookback}-bar VP)",
        "━━━━━━━━━━━━━━━━━━━━",
        "*Volume Profile:*",
        f"  🔴 POC: `{vp.poc:,.4f}` (most traded)",
        f"  🟡 VAH: `{vp.vah:,.4f}` (value area top)",
        f"  🟡 VAL: `{vp.val:,.4f}` (value area bottom)",
    ]
    if vp.hvn:
        top3 = vp.hvn[:3]
        lines.append(f"  🟢 HVN: " + " | ".join(f"`{h:,.4f}`" for h in top3))
    if vp.lvn:
        top3 = vp.lvn[:3]
        lines.append(f"  ⚪ LVN: " + " | ".join(f"`{l:,.4f}`" for l in top3))
    lines.append("")
    lines.append("*Classical S/R Levels:*")
    for lvl in sorted(sr_levels, key=lambda x: x.price, reverse=True)[:10]:
        lines.append(f"  • {lvl.label}")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)
