"""Named ICT setup evaluators for the finance/trader module.

Each setup function receives the pre-computed bias, scanner result, and
confluence data for a ticker and returns a TradeSetup if all gates pass,
or None if the setup is not valid at this time.

v1 setups implemented here:
    1. OB Retracement     — BOS + active OB + price retrace into OB
    2. Liquidity Sweep + FVG — EQH/EQL swept + FVG created + price at FVG
    3. Displacement + FVG Re-entry — strong impulse + FVG + price at midpoint

All heavy imports are lazy. No pandas/numpy at module level.

Usage:
    from modules.finance.trader.setups import check_all
    signals = check_all(ticker, category, df_htf, df_ltf)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Minimum confluence score to fire an alert
_MIN_SCORE = 5.0
# Minimum risk:reward ratio
_MIN_RR_CONTINUATION = 2.0
_MIN_RR_SWEEP        = 2.5


# ── Scanner result container ──────────────────────────────────────────────────

@dataclass
class ScannerResult:
    """Aggregated output of all ICT scanner primitives for one ticker."""
    structure:  object                # SwingStructure
    bos_list:   list = field(default_factory=list)  # list[BOS]
    ob_list:    list = field(default_factory=list)  # list[OrderBlock]
    fvg_list:   list = field(default_factory=list)  # list[FVG]
    liq_list:   list = field(default_factory=list)  # list[LiquidityLevel]


# ── Main entry point ──────────────────────────────────────────────────────────

def check_all(
    ticker:   str,
    category: str,
    df_htf,
    df_ltf,
) -> list:
    """Run all v1 setups for a ticker and return valid TradeSetup objects.

    Args:
        ticker:   Ticker symbol (e.g. "AAPL", "BTC-USD")
        category: Watchlist category (determines scan mode)
        df_htf:   HTF OHLCV DataFrame (Weekly / Daily)
        df_ltf:   LTF OHLCV DataFrame (4H / 1H)

    Returns:
        List of TradeSetup objects that pass all gates. May be empty.
    """
    from .schemas import scan_mode_for
    from .bias import compute_htf_bias
    from .scanner import (
        detect_structure, detect_bos_choch, find_order_blocks,
        find_fvg, find_eqh_eql,
    )
    from .confluence import compute_volume_profile, get_sr_levels

    mode = scan_mode_for(category)

    # ── Bias ──────────────────────────────────────────────────────────────────
    try:
        bias = compute_htf_bias(df_htf, mode)
    except Exception as e:
        logger.warning("[trader.setups] bias computation failed for %s: %s", ticker, e)
        return []

    if bias.bias == "neutral":
        logger.debug("[trader.setups] %s: neutral bias — skipping all setups", ticker)
        return []

    # ── ICT Scanner ───────────────────────────────────────────────────────────
    try:
        structure = detect_structure(df_ltf)
        bos_list  = detect_bos_choch(df_ltf, structure)
        ob_list   = find_order_blocks(df_ltf, bos_list)
        fvg_list  = find_fvg(df_ltf)
        liq_list  = find_eqh_eql(df_ltf)
    except Exception as e:
        logger.warning("[trader.setups] scanner failed for %s: %s", ticker, e)
        return []

    scanner = ScannerResult(
        structure=structure,
        bos_list=bos_list,
        ob_list=ob_list,
        fvg_list=fvg_list,
        liq_list=liq_list,
    )

    # ── Volume Profile + S/R ──────────────────────────────────────────────────
    try:
        vp       = compute_volume_profile(df_ltf, bias.vp_lookback)
        sr_lvls  = get_sr_levels(df_ltf, df_htf, float(df_ltf["Close"].iloc[-1]))
    except Exception as e:
        logger.warning("[trader.setups] confluence prep failed for %s: %s", ticker, e)
        return []

    # ── Evaluate each setup ───────────────────────────────────────────────────
    results = []
    current_price = float(df_ltf["Close"].iloc[-1])

    for fn in (_check_ob_retracement, _check_sweep_fvg, _check_displacement_fvg):
        try:
            setup = fn(ticker, category, mode, bias, scanner, vp, sr_lvls, current_price, df_ltf)
            if setup is not None:
                results.append(setup)
        except Exception as e:
            logger.warning("[trader.setups] %s failed for %s: %s", fn.__name__, ticker, e)

    return results


# ── Setup 1 — OB Retracement ─────────────────────────────────────────────────

def _check_ob_retracement(
    ticker, category, mode, bias, scanner: ScannerResult,
    vp, sr_lvls, current_price: float, df_ltf,
) -> Optional[object]:
    """Setup 1: BOS confirmed + active OB + price retracing into OB zone.

    Gates:
      - bias must be strong_bull | strong_bear (or moderate for Setup 3 only)
      - A BOS must exist in the direction of bias
      - An active (non-invalidated) OB must exist aligned with bias
      - Current price must be within the OB zone (±10% of zone size)
      - Confluence score >= _MIN_SCORE
      - R:R >= _MIN_RR_CONTINUATION
    """
    from .confluence import score_zone
    from .schemas import TradeSetup

    if bias.bias not in ("strong_bull", "strong_bear"):
        return None

    direction = "long" if "bull" in bias.bias else "short"

    # Find a BOS in the bias direction
    aligned_bos = [b for b in scanner.bos_list if b.direction == ("bullish" if direction == "long" else "bearish")]
    if not aligned_bos:
        return None

    # Find active OBs aligned with bias direction
    aligned_obs = [ob for ob in scanner.ob_list if ob.direction == ("bullish" if direction == "long" else "bearish")]
    if not aligned_obs:
        return None

    # Pick the most recent OB
    ob = aligned_obs[0]

    # Check price is retracing INTO the OB (±10% of zone size tolerance)
    zone_size   = ob.zone_high - ob.zone_low
    in_zone_low  = ob.zone_low  - zone_size * 0.10
    in_zone_high = ob.zone_high + zone_size * 0.10

    if not (in_zone_low <= current_price <= in_zone_high):
        logger.debug("[trader.setups] %s OB Retracement: price %.4f not in OB zone [%.4f, %.4f]",
                     ticker, current_price, in_zone_low, in_zone_high)
        return None

    # Confluence
    report = score_zone(ob.zone_low, ob.zone_high, sr_lvls, vp)
    if report.score < _MIN_SCORE:
        logger.debug("[trader.setups] %s OB Retracement: score %.1f < min %.1f",
                     ticker, report.score, _MIN_SCORE)
        return None

    # Trade levels
    entry_low  = ob.zone_low
    entry_high = ob.zone_high
    entry_mid  = ob.midpoint

    if direction == "long":
        stop_loss = ob.zone_low * 0.998   # 0.2% below OB low
        # TP1: next swing high above entry
        next_sh = _next_swing_high(scanner.structure, ob.bar_index, df_ltf)
        tp1 = next_sh if next_sh else entry_mid * 1.04
        tp2 = _next_eqh(scanner.liq_list, entry_mid)
        invalidation = f"Candle closes below {ob.zone_low:,.4f} (OB low)"
    else:
        stop_loss = ob.zone_high * 1.002
        next_sl   = _next_swing_low(scanner.structure, ob.bar_index, df_ltf)
        tp1 = next_sl if next_sl else entry_mid * 0.96
        tp2 = _next_eql(scanner.liq_list, entry_mid)
        invalidation = f"Candle closes above {ob.zone_high:,.4f} (OB high)"

    rr = _calc_rr(entry_mid, stop_loss, tp1)
    if rr < _MIN_RR_CONTINUATION:
        logger.debug("[trader.setups] %s OB Retracement: R:R %.1f < min %.1f",
                     ticker, rr, _MIN_RR_CONTINUATION)
        return None

    return TradeSetup(
        ticker=ticker, category=category, mode=mode,
        setup_name="OB Retracement", setup_type="continuation",
        direction=direction, bias=bias, confluence=report,
        entry_low=entry_low, entry_high=entry_high,
        stop_loss=stop_loss, tp1=tp1, tp2=tp2,
        rr_ratio=round(rr, 2),
        invalidation_note=invalidation,
        ltf_tf=bias.ltf_tf, htf_tf=bias.htf_tf,
    )


# ── Setup 2 — Liquidity Sweep + FVG ──────────────────────────────────────────

def _check_sweep_fvg(
    ticker, category, mode, bias, scanner: ScannerResult,
    vp, sr_lvls, current_price: float, df_ltf,
) -> Optional[object]:
    """Setup 2: EQH/EQL swept + FVG created within 3 bars + price at FVG.

    Gates:
      - bias strong_bull | strong_bear
      - A swept EQL (for longs) or EQH (for shorts) within last 10 bars
      - At least one FVG present after the sweep bar
      - Current price is at or entering the FVG zone (±half-gap tolerance)
      - Confluence score >= _MIN_SCORE, R:R >= _MIN_RR_SWEEP
    """
    from .confluence import score_zone
    from .schemas import TradeSetup

    if bias.bias not in ("strong_bull", "strong_bear"):
        return None

    direction = "long" if "bull" in bias.bias else "short"
    liq_kind  = "eql" if direction == "long" else "eqh"  # sweeping lows = long

    # Find recently swept liquidity levels
    swept = [l for l in scanner.liq_list if l.swept and l.kind == liq_kind]
    if not swept:
        return None

    # Sort by sweep bar, most recent first
    swept.sort(key=lambda l: l.sweep_bar, reverse=True)
    recent_sweep = swept[0]

    # Find FVGs that formed AFTER the sweep bar
    post_sweep_fvgs = [
        f for f in scanner.fvg_list
        if f.bar_index > recent_sweep.sweep_bar
        and f.direction == ("bullish" if direction == "long" else "bearish")
    ]
    if not post_sweep_fvgs:
        return None

    fvg = post_sweep_fvgs[0]  # most recent

    # Check price is at or entering the FVG (within half-gap tolerance)
    half_gap = (fvg.gap_high - fvg.gap_low) / 2
    in_fvg = (fvg.gap_low - half_gap) <= current_price <= (fvg.gap_high + half_gap)
    if not in_fvg:
        logger.debug("[trader.setups] %s Sweep+FVG: price %.4f not in FVG [%.4f, %.4f]",
                     ticker, current_price, fvg.gap_low, fvg.gap_high)
        return None

    report = score_zone(fvg.gap_low, fvg.gap_high, sr_lvls, vp)
    if report.score < _MIN_SCORE:
        return None

    entry_low  = fvg.gap_low
    entry_high = fvg.gap_high
    entry_mid  = fvg.midpoint

    if direction == "long":
        stop_loss = recent_sweep.price * 0.997  # below the sweep extreme
        tp1 = _next_swing_high(scanner.structure, fvg.bar_index, df_ltf) or entry_mid * 1.05
        tp2 = _next_eqh(scanner.liq_list, entry_mid)
        invalidation = f"Candle closes below sweep low {recent_sweep.price:,.4f}"
    else:
        stop_loss = recent_sweep.price * 1.003
        tp1 = _next_swing_low(scanner.structure, fvg.bar_index, df_ltf) or entry_mid * 0.95
        tp2 = _next_eql(scanner.liq_list, entry_mid)
        invalidation = f"Candle closes above sweep high {recent_sweep.price:,.4f}"

    rr = _calc_rr(entry_mid, stop_loss, tp1)
    if rr < _MIN_RR_SWEEP:
        return None

    return TradeSetup(
        ticker=ticker, category=category, mode=mode,
        setup_name="Liquidity Sweep + FVG", setup_type="continuation",
        direction=direction, bias=bias, confluence=report,
        entry_low=entry_low, entry_high=entry_high,
        stop_loss=stop_loss, tp1=tp1, tp2=tp2,
        rr_ratio=round(rr, 2),
        invalidation_note=invalidation,
        ltf_tf=bias.ltf_tf, htf_tf=bias.htf_tf,
    )


# ── Setup 3 — Displacement + FVG Re-entry ────────────────────────────────────

def _check_displacement_fvg(
    ticker, category, mode, bias, scanner: ScannerResult,
    vp, sr_lvls, current_price: float, df_ltf,
) -> Optional[object]:
    """Setup 3: Large displacement candle + FVG + price retracing to FVG 50%.

    Gates:
      - bias strong OR moderate (widest gate of the three setups)
      - FVG present with displacement_ratio >= 1.2 (already filtered by scanner)
      - FVG direction aligns with bias direction
      - Price is currently within ±20% of FVG midpoint (approaching or inside)
      - Confluence score >= _MIN_SCORE, R:R >= _MIN_RR_CONTINUATION
    """
    from .confluence import score_zone
    from .schemas import TradeSetup

    # Moderate bias allowed for this setup
    if bias.bias == "neutral":
        return None

    direction = "long" if "bull" in bias.bias else "short"
    fvg_dir   = "bullish" if direction == "long" else "bearish"

    aligned_fvgs = [f for f in scanner.fvg_list if f.direction == fvg_dir]
    if not aligned_fvgs:
        return None

    fvg = aligned_fvgs[0]  # most recent
    fvg_mid = fvg.midpoint
    half_gap = (fvg.gap_high - fvg.gap_low) / 2 * 1.2  # 20% extra tolerance

    if not (fvg_mid - half_gap <= current_price <= fvg_mid + half_gap):
        logger.debug("[trader.setups] %s Displacement FVG: price %.4f not near FVG mid %.4f",
                     ticker, current_price, fvg_mid)
        return None

    report = score_zone(fvg.gap_low, fvg.gap_high, sr_lvls, vp)
    if report.score < _MIN_SCORE:
        return None

    entry_low  = fvg.gap_low
    entry_high = fvg.gap_high
    entry_mid  = fvg.midpoint

    if direction == "long":
        stop_loss = fvg.gap_low * 0.997   # beyond full FVG
        tp1 = _next_swing_high(scanner.structure, fvg.bar_index, df_ltf) or entry_mid * 1.04
        tp2 = None
        invalidation = f"Candle closes below FVG low {fvg.gap_low:,.4f}"
    else:
        stop_loss = fvg.gap_high * 1.003
        tp1 = _next_swing_low(scanner.structure, fvg.bar_index, df_ltf) or entry_mid * 0.96
        tp2 = None
        invalidation = f"Candle closes above FVG high {fvg.gap_high:,.4f}"

    rr = _calc_rr(entry_mid, stop_loss, tp1)
    if rr < _MIN_RR_CONTINUATION:
        return None

    # Downgrade confidence label if bias is only moderate
    if "moderate" in bias.bias and report.label == "high":
        from .schemas import ConfluenceReport
        report = ConfluenceReport(
            score=report.score,
            sr_hits=report.sr_hits,
            vp_hits=report.vp_hits,
            poc=report.poc, vah=report.vah, val=report.val,
            label="medium",   # cap at medium for moderate bias
        )

    return TradeSetup(
        ticker=ticker, category=category, mode=mode,
        setup_name="Displacement + FVG", setup_type="continuation",
        direction=direction, bias=bias, confluence=report,
        entry_low=entry_low, entry_high=entry_high,
        stop_loss=stop_loss, tp1=tp1, tp2=tp2,
        rr_ratio=round(rr, 2),
        invalidation_note=invalidation,
        ltf_tf=bias.ltf_tf, htf_tf=bias.htf_tf,
    )


# ── Private helpers ───────────────────────────────────────────────────────────

def _calc_rr(entry_mid: float, stop_loss: float, tp1: float) -> float:
    """Risk:Reward = |TP1 - entry| / |entry - SL|. Returns 0 on division-by-zero."""
    risk   = abs(entry_mid - stop_loss)
    reward = abs(tp1 - entry_mid)
    if risk == 0:
        return 0.0
    return reward / risk


def _next_swing_high(structure, after_bar: int, df_ltf) -> Optional[float]:
    """Find the next swing high above current price after a given bar index."""
    current_price = float(df_ltf["Close"].iloc[-1])
    highs = [
        sp for sp in structure.points
        if sp.kind == "high" and sp.index > after_bar and sp.price > current_price
    ]
    if highs:
        return min(highs, key=lambda sp: sp.price).price  # nearest swing high above
    return None


def _next_swing_low(structure, after_bar: int, df_ltf) -> Optional[float]:
    """Find the next swing low below current price after a given bar index."""
    current_price = float(df_ltf["Close"].iloc[-1])
    lows = [
        sp for sp in structure.points
        if sp.kind == "low" and sp.index > after_bar and sp.price < current_price
    ]
    if lows:
        return max(lows, key=lambda sp: sp.price).price  # nearest swing low below
    return None


def _next_eqh(liq_list, entry_mid: float) -> Optional[float]:
    """Find the nearest EQH (buy-side liquidity) above current entry."""
    eqhs = [l.price for l in liq_list if l.kind == "eqh" and l.price > entry_mid and not l.swept]
    return min(eqhs) if eqhs else None


def _next_eql(liq_list, entry_mid: float) -> Optional[float]:
    """Find the nearest EQL (sell-side liquidity) below current entry."""
    eqls = [l.price for l in liq_list if l.kind == "eql" and l.price < entry_mid and not l.swept]
    return max(eqls) if eqls else None
