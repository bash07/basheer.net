"""Technical Analysis & Wyckoff Interactive Engine UI Router."""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import select
from starlette.background import BackgroundTask

from core.db import get_db
from core.security import require_auth
from core.templates import templates
from modules.finance._shared.indicators import calc_ema
from modules.finance.technical.charts import DEFAULT_TF, fetch_and_render, fetch_ohlcv_and_ta
from modules.finance.technical.indicators import TAResult
from modules.finance.trader.indicators.accumulation_distribution import _accum_dist
from modules.finance.trader.indicators.point_and_figure import _point_and_figure
from modules.finance.watchlist.models import WatchlistEntry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Technical Analysis & Wyckoff"])


def _compute_wyckoff_structure(df: pd.DataFrame, current_price: float, atr: float) -> dict[str, Any]:
    """Calculate institutional Wyckoff order-flow, phase progression, and P&F Cause & Effect targets."""
    cache = {"atr_14": atr}
    ad = _accum_dist(df, df, cache)
    pnf = _point_and_figure(df, df, cache)

    state = ad.state  # "accumulation" | "distribution" | "neutral"
    score = ad.score
    range_high = ad.range_high
    range_low = ad.range_low
    range_span = range_high - range_low
    range_atr = (range_span / max(atr, 1e-4)) if atr > 0 else 0.0

    # Determine Phase Progression (Phases A through E)
    if state == "accumulation":
        schematic_name = "Accumulation Schematic #1 (Spring Variant)"
        if current_price > range_high:
            active_phase = "D" if (current_price - range_high) < (1.5 * atr) else "E"
        elif ad.spring_detected or ad.absorption_divergence:
            active_phase = "C"
        elif ad.is_squeeze:
            active_phase = "B"
        else:
            active_phase = "A"
    elif state == "distribution":
        schematic_name = "Distribution Schematic #2 (UTAD Variant)"
        if current_price < range_low:
            active_phase = "D" if (range_low - current_price) < (1.5 * atr) else "E"
        elif ad.upthrust_detected or ad.exhaustion_divergence:
            active_phase = "C"
        elif ad.is_squeeze:
            active_phase = "B"
        else:
            active_phase = "A"
    else:
        schematic_name = "Pre-Accumulation / Consolidation Base"
        active_phase = "B" if ad.is_squeeze else "A"

    phase_prog_map = {"A": 20, "B": 40, "C": 60, "D": 80, "E": 100}
    progress_pct = phase_prog_map.get(active_phase, 40)

    # Descriptions
    obv_desc = (
        "Institutional Inflow" if ad.obv_slope > 0.15
        else "Distribution Outflow" if ad.obv_slope < -0.15
        else "Neutral Volume"
    )
    ibs_desc = (
        "Dip Buying Absorption" if ad.ibs_mean > 0.53
        else "Rally Selling Rejection" if ad.ibs_mean < 0.47
        else "Balanced Bar Closings"
    )
    wick_desc = (
        "Lower Wick Demand" if ad.wick_ratio > 1.20
        else "Upper Wick Supply" if ad.wick_ratio < 0.80
        else "Symmetric Wicks"
    )

    trigger_label = "—"
    if ad.spring_detected:
        trigger_label = "⚡ Spring Shakeout Confirmed"
    elif ad.upthrust_detected:
        trigger_label = "⚡ UTAD Upthrust Rejection"
    elif ad.absorption_divergence:
        trigger_label = "✅ Bullish Supply Depletion"
    elif ad.exhaustion_divergence:
        trigger_label = "⚠️ Bearish Demand Exhaustion"

    # P&F Cause & Effect vertical targets (Wyckoff 2nd Law)
    is_dist = (state == "distribution")
    t_cons = pnf.projected_short_conservative if is_dist else pnf.projected_long_conservative
    t_full = pnf.projected_short_full if is_dist else pnf.projected_long_full

    if t_cons <= 0:
        t_cons = current_price - (2.0 * atr) if is_dist else current_price + (2.0 * atr)
    if t_full <= 0:
        t_full = current_price - (3.5 * atr) if is_dist else current_price + (3.5 * atr)

    cons_pct = ((t_cons - current_price) / max(current_price, 1e-4)) * 100
    full_pct = ((t_full - current_price) / max(current_price, 1e-4)) * 100

    # 5-Phase Sequence Generator
    if is_dist:
        phases = [
            {
                "key": "A",
                "title": "PHASE A",
                "sub": "Preliminary Supply (PSY)",
                "desc": "Buying Climax (BC) & Automatic Reaction (AR) setting upper boundary.",
                "footer": "Base Established",
                "status": "VERIFIED" if active_phase in ["B", "C", "D", "E"] else "ACTIVE",
                "is_active": (active_phase == "A"),
            },
            {
                "key": "B",
                "title": "PHASE B",
                "sub": "Building Downward Cause",
                "desc": "Testing upper resistance & institutional inventory absorption coils.",
                "footer": f"P&F: {pnf.horizontal_count} Cols Count",
                "status": "VERIFIED" if active_phase in ["C", "D", "E"] else ("ACTIVE" if active_phase == "B" else "PENDING"),
                "is_active": (active_phase == "B"),
            },
            {
                "key": "C",
                "title": "PHASE C",
                "sub": "The Upthrust (UTAD)",
                "desc": "Bull trap high pierced resistance with volume drain, closed back below range.",
                "footer": "UTAD Rejection Confirmed" if ad.upthrust_detected else "Testing Boundary",
                "status": "ACTIVE" if active_phase == "C" else ("VERIFIED" if active_phase in ["D", "E"] else "PENDING"),
                "is_active": (active_phase == "C"),
            },
            {
                "key": "D",
                "title": "PHASE D",
                "sub": "Sign of Weakness (SOW)",
                "desc": f"Breakdown below {range_low:.2f} ice support followed by LPSY pullback.",
                "footer": f"Trigger: Close < {range_low:.2f}",
                "status": "ACTIVE" if active_phase == "D" else ("VERIFIED" if active_phase == "E" else "PENDING"),
                "is_active": (active_phase == "D"),
            },
            {
                "key": "E",
                "title": "PHASE E",
                "sub": "Markdown Phase",
                "desc": "Uninhibited downward trend progression towards P&F vertical Cause targets.",
                "footer": f"Targets: {t_cons:.2f} / {t_full:.2f}",
                "status": "ACTIVE" if active_phase == "E" else "PROJECTED",
                "is_active": (active_phase == "E"),
            },
        ]
    else:
        phases = [
            {
                "key": "A",
                "title": "PHASE A",
                "sub": "Stopping Action",
                "desc": "Preliminary Support (PS) & Selling Climax (SC) with Automatic Rally (AR).",
                "footer": "Base Established",
                "status": "VERIFIED" if active_phase in ["B", "C", "D", "E"] else "ACTIVE",
                "is_active": (active_phase == "A"),
            },
            {
                "key": "B",
                "title": "PHASE B",
                "sub": "Building the Cause",
                "desc": "Secondary Tests (ST) & supply absorption coil within established range.",
                "footer": f"P&F: {pnf.horizontal_count} Cols Count",
                "status": "VERIFIED" if active_phase in ["C", "D", "E"] else ("ACTIVE" if active_phase == "B" else "PENDING"),
                "is_active": (active_phase == "B"),
            },
            {
                "key": "C",
                "title": "PHASE C",
                "sub": "The Shakeout (Spring)",
                "desc": "Bear trap low pierced support with volume surge, reclaimed inside range.",
                "footer": "Spring Reclaim Confirmed" if ad.spring_detected else "Testing Range Floor",
                "status": "ACTIVE" if active_phase == "C" else ("VERIFIED" if active_phase in ["D", "E"] else "PENDING"),
                "is_active": (active_phase == "C"),
            },
            {
                "key": "D",
                "title": "PHASE D",
                "sub": "Sign of Strength (SOS)",
                "desc": f"Breakout above {range_high:.2f} (SOS) followed by Last Point of Support (LPS).",
                "footer": f"Trigger: Close > {range_high:.2f}",
                "status": "ACTIVE" if active_phase == "D" else ("VERIFIED" if active_phase == "E" else "PENDING"),
                "is_active": (active_phase == "D"),
            },
            {
                "key": "E",
                "title": "PHASE E",
                "sub": "Markup Expansion",
                "desc": "Uninhibited upward trend progression towards P&F vertical Cause targets.",
                "footer": f"Targets: {t_cons:.2f} / {t_full:.2f}",
                "status": "ACTIVE" if active_phase == "E" else "PROJECTED",
                "is_active": (active_phase == "E"),
            },
        ]

    # Three Wyckoff Laws Validation Checklist
    law1_pass = (ad.wick_ratio >= 1.1 or ad.obv_slope > 0.05) if not is_dist else (ad.wick_ratio <= 0.9 or ad.obv_slope < -0.05)
    law2_pass = (pnf.horizontal_count >= 8)
    law3_pass = (ad.spring_detected or ad.absorption_divergence or ad.upthrust_detected or ad.exhaustion_divergence or ad.is_squeeze)

    laws = [
        {
            "name": "1. Law of Supply & Demand",
            "status": "PASS ✓" if law1_pass else "WATCH ⏳",
            "desc": (
                f"Wick ratio ({ad.wick_ratio:.2f}×) and OBV slope ({ad.obv_slope:+.2f}) confirm institutional absorption."
                if law1_pass
                else "Order flow balanced; waiting for pronounced wick or OBV absorption delta."
            ),
        },
        {
            "name": "2. Law of Cause & Effect",
            "status": "PASS ✓" if law2_pass else "FORMING ⏳",
            "desc": (
                f"P&F base width of {pnf.horizontal_count} columns stores {pnf.cause_pts:,.2f} pts of potential Cause energy."
                if law2_pass
                else f"Base has {pnf.horizontal_count} columns; minimum 8 columns recommended for full cycle projection."
            ),
        },
        {
            "name": "3. Law of Effort vs. Result",
            "status": "PASS ✓" if law3_pass else "INCONCLUSIVE",
            "desc": (
                "Volume spikes on extreme tests produced zero continuation, confirming trap absorption."
                if law3_pass
                else "Price and volume moving symmetrically with no divergence trap observed."
            ),
        },
    ]

    return {
        "valid": True,
        "schematic_name": schematic_name,
        "state": state,
        "score": score,
        "progress_pct": progress_pct,
        "active_phase": active_phase,
        "range_high": range_high,
        "range_low": range_low,
        "range_mid": ad.range_mid,
        "range_atr": range_atr,
        "is_squeeze": ad.is_squeeze,
        "obv_slope": ad.obv_slope,
        "obv_desc": obv_desc,
        "ibs_mean": ad.ibs_mean,
        "ibs_desc": ibs_desc,
        "wick_ratio": ad.wick_ratio,
        "wick_desc": wick_desc,
        "spring_detected": ad.spring_detected,
        "upthrust_detected": ad.upthrust_detected,
        "trigger_label": trigger_label,
        "phases": phases,
        "laws": laws,
        "pnf": {
            "horizontal_count": pnf.horizontal_count,
            "box_size": pnf.box_size,
            "cause_pts": pnf.cause_pts,
            "t_cons": t_cons,
            "t_cons_pct": cons_pct,
            "t_full": t_full,
            "t_full_pct": full_pct,
        },
    }


def _compute_extended_ta(df: pd.DataFrame, ta: TAResult) -> dict[str, Any]:
    """Compute detailed Moving Average spectrum, Bollinger Band metrics, and Oscillators."""
    close = df["Close"].astype(float)
    cp = ta.current_price

    # Moving Average Spectrum
    ma_specs = [
        ("21", "EMA", "#00bcd4", calc_ema(close, 21)),
        ("50", "EMA", "#ff9800", calc_ema(close, 50)),
        ("100", "EMA", "#9c27b0", calc_ema(close, 100)),
        ("200", "EMA", "#e91e63", calc_ema(close, 200)),
        ("20", "SMA", "#a1a1aa", close.rolling(20).mean()),
        ("200", "SMA", "#71717a", close.rolling(200).mean() if len(close) >= 200 else close.rolling(50).mean()),
    ]

    mas_table = []
    for period, ma_type, color, series in ma_specs:
        val = float(series.iloc[-1]) if series is not None and len(series) > 0 and not pd.isna(series.iloc[-1]) else cp
        dist_pct = ((cp - val) / val) * 100 if val > 0 else 0.0
        mas_table.append({
            "period": period,
            "type": ma_type,
            "color": color,
            "value": val,
            "dist_pct": dist_pct,
        })

    # MACD (12, 26, 9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    macd_signal = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - macd_signal

    # Bollinger Bands (20, 2)
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    bb_upper = sma20 + (2.0 * std20)
    bb_lower = sma20 - (2.0 * std20)
    bb_w = ((bb_upper - bb_lower) / sma20) * 100
    bb_pct_b = (close - bb_lower) / (bb_upper - bb_lower).replace(0, 1e-6)

    # ATR (14)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr14 = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else float(tr.mean())
    atr_pct = (atr14 / cp) * 100 if cp > 0 else 0.0

    return {
        "mas_table": mas_table,
        "macd": {
            "val": float(macd_line.iloc[-1]),
            "signal": float(macd_signal.iloc[-1]),
            "hist": float(macd_hist.iloc[-1]),
            "bullish": float(macd_hist.iloc[-1]) >= 0,
        },
        "bb": {
            "upper": float(bb_upper.iloc[-1]),
            "mid": float(sma20.iloc[-1]),
            "lower": float(bb_lower.iloc[-1]),
            "bandwidth": float(bb_w.iloc[-1]),
            "pct_b": float(bb_pct_b.iloc[-1]),
        },
        "atr": atr14,
        "atr_pct": atr_pct,
    }


async def _get_full_technical_data(symbol: str, tf: str = DEFAULT_TF) -> dict[str, Any]:
    """Coordinate OHLCV fetch, TA calculations, and Wyckoff diagnostic engine."""
    sym = symbol.strip().upper()
    df, resolved_tf, ta_res = await fetch_ohlcv_and_ta(sym, tf)

    ext = _compute_extended_ta(df, ta_res)
    wyckoff = _compute_wyckoff_structure(df, ta_res.current_price, ext["atr"])

    # 24h Price Change & Volume
    cp = ta_res.current_price
    prior_c = float(df["Close"].iloc[-2]) if len(df) >= 2 else cp
    chg_val = cp - prior_c
    chg_pct = (chg_val / prior_c) * 100 if prior_c > 0 else 0.0

    vol_last = float(df["Volume"].iloc[-1]) if "Volume" in df.columns else 0.0
    vol_mean = float(df["Volume"].rolling(20).mean().iloc[-1]) if "Volume" in df.columns and len(df) >= 20 else max(vol_last, 1.0)
    vol_ratio = (vol_last / vol_mean) if vol_mean > 0 else 1.0

    # Category determination
    category = "us_stock"
    if sym.endswith(".NS"):
        category = "indian_stock"
    elif "-USD" in sym or sym.endswith("USD"):
        category = "crypto"
    elif "=X" in sym:
        category = "forex"

    return {
        "symbol": sym,
        "timeframe": resolved_tf,
        "category": category,
        "current_price": cp,
        "change_val": chg_val,
        "change_pct": chg_pct,
        "volume": vol_last,
        "volume_ratio": vol_ratio,
        "ta": ta_res,
        "ext": ext,
        "wyckoff": wyckoff,
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/technical", response_class=HTMLResponse)
async def technical_page(
    request: Request,
    symbol: Optional[str] = None,
    tf: Optional[str] = "1d",
    user: str = Depends(require_auth),
):
    """Full Technical Analysis & Wyckoff Schematic Dashboard View."""
    target_symbol = symbol.strip().upper() if symbol else "NVDA"
    timeframe = tf if tf in ["1d", "1wk", "1mo"] else "1d"

    # Watchlist tickers for quick pills
    with get_db() as db:
        entries = db.execute(select(WatchlistEntry.symbol).order_by(WatchlistEntry.symbol)).scalars().all()
    quick_symbols = list(entries[:8]) if entries else ["NVDA", "BTC-USD", "RELIANCE.NS", "AAPL", "TATASTEEL.NS"]
    if target_symbol not in quick_symbols:
        quick_symbols.insert(0, target_symbol)

    try:
        data = await _get_full_technical_data(target_symbol, timeframe)
        error_msg = None
    except Exception as e:
        logger.error("[technical_ui] Failed to compute TA for %s: %s", target_symbol, e, exc_info=True)
        data = None
        error_msg = str(e)

    return templates.TemplateResponse(
        request=request,
        name="technical/index.html",
        context={
            "title": f"ai.basheer.net — Technical & Wyckoff ({target_symbol})",
            "active_page": "technical",
            "csrf_token": request.state.csrf_token,
            "target_symbol": target_symbol,
            "current_tf": timeframe,
            "quick_symbols": quick_symbols,
            "data": data,
            "error_msg": error_msg,
        },
    )


@router.get("/api/technical/analyze", response_class=HTMLResponse)
async def api_technical_analyze(
    request: Request,
    symbol: str = Query("NVDA"),
    tf: str = Query("1d"),
    user: str = Depends(require_auth),
):
    """HTMX partial returning the complete analysis block, chart, stepper, and 4 cards."""
    target_symbol = symbol.strip().upper()
    timeframe = tf if tf in ["1d", "1wk", "1mo"] else "1d"

    try:
        data = await _get_full_technical_data(target_symbol, timeframe)
        error_msg = None
    except Exception as e:
        logger.error("[technical_ui] Partial analyze failed for %s: %s", target_symbol, e, exc_info=True)
        data = None
        error_msg = str(e)

    return templates.TemplateResponse(
        request=request,
        name="technical/partials/analysis.html",
        context={
            "csrf_token": request.state.csrf_token,
            "target_symbol": target_symbol,
            "current_tf": timeframe,
            "data": data,
            "error_msg": error_msg,
        },
    )


@router.get("/api/technical/chart-image")
async def api_technical_chart_image(
    symbol: str = Query("NVDA"),
    tf: str = Query("1d"),
    wyckoff: bool = Query(True),
    user: str = Depends(require_auth),
):
    """Generate high-fidelity candlestick PNG chart via isolated subprocess and stream to client."""
    target_symbol = symbol.strip().upper()
    timeframe = tf if tf in ["1d", "1wk", "1mo"] else "1d"

    wyckoff_data = None
    if wyckoff:
        try:
            df, _, ta_res = await fetch_ohlcv_and_ta(target_symbol, timeframe)
            ext = _compute_extended_ta(df, ta_res)
            w_res = _compute_wyckoff_structure(df, ta_res.current_price, ext["atr"])
            wyckoff_data = {
                "range_high": w_res["range_high"],
                "range_low": w_res["range_low"],
                "t_cons": w_res["pnf"]["t_cons"],
                "t_full": w_res["pnf"]["t_full"],
            }
        except Exception as e:
            logger.warning("[technical_ui] Could not precompute Wyckoff chart overlay: %s", e)

    png_path, _ = await fetch_and_render(target_symbol, timeframe, wyckoff_data=wyckoff_data)

    return FileResponse(
        path=png_path,
        media_type="image/png",
        filename=f"{target_symbol}_{timeframe}.png",
        background=BackgroundTask(os.unlink, png_path),
    )


@router.get("/api/technical/wyckoff")
async def api_technical_wyckoff_json(
    symbol: str = Query("NVDA"),
    tf: str = Query("1d"),
    user: str = Depends(require_auth),
):
    """Return raw Wyckoff order flow and Point & Figure footprint as JSON."""
    target_symbol = symbol.strip().upper()
    data = await _get_full_technical_data(target_symbol, tf)
    return {
        "symbol": target_symbol,
        "price": data["current_price"],
        "wyckoff": data["wyckoff"],
    }
