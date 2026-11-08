"""Named Trade setup evaluators for the finance/trader module.

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
_MIN_RR_CONTINUATION = 1.5
_MIN_RR_SWEEP        = 2.0

# Set of setup names to skip. Modify at runtime or override in backtest.
# Example: DISABLED_SETUPS.add("Liquidity Sweep + FVG")
DISABLED_SETUPS: set[str] = set()


# ── Scanner result container ──────────────────────────────────────────────────

@dataclass
class ScannerResult:
    """Aggregated output of all trading scanner primitives for one ticker."""
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
    df_mtf=None,
    df_trigger=None,
    mode: Optional[str] = None,
    allowed_setups: Optional[list[str] | set[str]] = None,
) -> list:
    """Run all enabled strategies for a ticker and return valid TradeSetup objects.

    Supports both legacy 2-tier (df_htf, df_ltf) and 3-tier (df_htf, df_mtf, df_trigger)
    multi-timeframe matrix models. Optionally filters strategies by allowed_setups.
    """
    from .schemas import scan_mode_for
    from .indicators import compute_indicators
    from .strategies import get_all_strategies
    from .strategies.base import StrategyContext

    mode = mode or scan_mode_for(category)

    # Filter by allowed_setups if provided
    norm_allowed: Optional[set[str]] = None
    if allowed_setups is not None:
        norm_allowed = {
            "".join(c for c in str(name).lower() if c.isalnum())
            for name in allowed_setups
            if name
        }

    # Identify enabled strategies (respecting disabled list and supported styles)
    def _is_allowed(s) -> bool:
        if norm_allowed is None:
            return True
        s_variants = {
            "".join(c for c in s.name.lower() if c.isalnum()),
            "".join(c for c in s.__class__.__name__.lower() if c.isalnum()),
        }
        cls_name = s.__class__.__name__
        if cls_name.endswith("Strategy"):
            s_variants.add("".join(c for c in cls_name[:-8].lower() if c.isalnum()))
        return bool(s_variants.intersection(norm_allowed))

    enabled = [
        s for s in get_all_strategies()
        if s.name not in DISABLED_SETUPS
        and (not getattr(s, "supported_styles", None) or mode in s.supported_styles)
        and _is_allowed(s)
    ]
    if not enabled:
        logger.debug("[trader.setups] %s: all strategies disabled, filtered or unsuited for style %s — skipping", ticker, mode)
        return []

    # Collect the union of all required indicators
    needed: set[str] = set()
    for s in enabled:
        needed.update(s.required_indicators)
        # If strategy uses a non-default bias provider (e.g. macd, ema, composite)
        bp = getattr(s, "bias_provider", "rsi")
        if bp != "rsi":
            needed.add(f"bias_{bp}")

    # Include default bias if needed by any strategy
    if any("bias" in s.required_indicators for s in enabled):
        needed.add("bias")

    # Compute only what is needed, with automatic dependency resolution
    initial_cache = {"_mode": mode, "_category": category}
    try:
        indicators = compute_indicators(list(needed), df_ltf, df_htf, initial_cache)
    except Exception as e:
        logger.warning("[trader.setups] Indicator computation failed for %s: %s", ticker, e)
        return []

    # Helper to check if a specific strategy has neutral / missing directional bias
    def _strategy_bias_is_neutral(strategy) -> bool:
        if not getattr(strategy, "requires_directional_bias", True):
            return False  # Non-directional / range strategies don't require trend bias
        bp = getattr(strategy, "bias_provider", "rsi")
        b = indicators.get(f"bias_{bp}") if bp != "rsi" else indicators.get("bias")
        return b is None or b.bias == "neutral"

    # Early exit only if EVERY enabled strategy has a neutral / invalid bias
    if all(_strategy_bias_is_neutral(s) for s in enabled):
        logger.debug("[trader.setups] %s: all strategy biases are neutral — skipping", ticker)
        return []

    # Build shared context (supporting 3-tier triad)
    exec_df = df_trigger if df_trigger is not None else df_ltf
    ctx = StrategyContext(
        ticker=ticker,
        category=category,
        mode=mode,
        current_price=float(exec_df["Close"].iloc[-1]),
        df_htf=df_htf,
        df_ltf=df_ltf,
        df_mtf=df_mtf,
        df_trigger=df_trigger,
        indicators=indicators,
    )

    # Evaluate each enabled strategy
    results = []
    for strategy in enabled:
        if _strategy_bias_is_neutral(strategy):
            logger.debug("[trader.setups] %s: '%s' bias is neutral — skipping",
                         ticker, strategy.name)
            continue

        ctx.bind_strategy(strategy)
        try:
            setup = strategy.evaluate(ctx)
            if setup is not None:
                results.append(setup)
        except Exception as e:
            logger.warning("[trader.setups] %s failed for %s: %s",
                           strategy.name, ticker, e)

    return results


# Registry: (display_name, function) — order determines evaluation priority.
# Add new setups here; use the same name as setup.setup_name.
# DEPRECATED: Use strategies/ sub-package instead.
_SETUP_REGISTRY: list[tuple[str, object]] = []   # populated at bottom of module


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
    atr = _get_atr(df_ltf)

    if direction == "long":
        stop_loss = ob.zone_low - (atr * 0.5)   # 0.5 ATR buffer below OB
        # TP1: next swing high above entry
        next_sh = _next_swing_high(scanner.structure, ob.bar_index, df_ltf)
        tp1 = next_sh if next_sh else entry_mid * 1.04
        tp2 = _next_eqh(scanner.liq_list, entry_mid)
        invalidation = f"Candle closes below {ob.zone_low:,.4f} (OB low)"
    else:
        stop_loss = ob.zone_high + (atr * 0.5)  # 0.5 ATR buffer above OB
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
    atr = _get_atr(df_ltf)

    if direction == "long":
        stop_loss = recent_sweep.price - (atr * 0.5)  # 0.5 ATR buffer below sweep
        tp1 = _next_swing_high(scanner.structure, fvg.bar_index, df_ltf) or entry_mid * 1.05
        tp2 = _next_eqh(scanner.liq_list, entry_mid)
        invalidation = f"Candle closes below sweep low {recent_sweep.price:,.4f}"
    else:
        stop_loss = recent_sweep.price + (atr * 0.5)  # 0.5 ATR buffer above sweep
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
    atr = _get_atr(df_ltf)

    if direction == "long":
        stop_loss = fvg.gap_low - (atr * 0.5)  # 0.5 ATR buffer below FVG
        tp1 = _next_swing_high(scanner.structure, fvg.bar_index, df_ltf) or entry_mid * 1.04
        tp2 = None
        invalidation = f"Candle closes below FVG low {fvg.gap_low:,.4f}"
    else:
        stop_loss = fvg.gap_high + (atr * 0.5)  # 0.5 ATR buffer above FVG
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


# ── Setup 4 — EMA Pullback + Hidden Divergence ───────────────────────────────────

def _check_ema_pullback(
    ticker, category, mode, bias, scanner: ScannerResult,
    vp, sr_lvls, current_price: float, df_ltf,
) -> Optional[object]:
    """Setup 4: HTF Hidden Divergence bias + LTF 21 EMA pullback/rejection entry.

    Entry Logic:
      LONG:  Last bar's LOW touched or pierced the 21 EMA AND close is back
             above the 21 EMA (bullish rejection wick), OR the 8 EMA has
             crossed above the 21 EMA within the last 5 bars.
      SHORT: Last bar's HIGH touched or pierced the 21 EMA AND close is back
             below the 21 EMA (bearish rejection wick), OR the 8 EMA has
             crossed below the 21 EMA within the last 5 bars.

    Stop Loss:
      Previous confirmed structural swing low (LONG) or swing high (SHORT).
      A 0.25 ATR buffer is added beyond the swing level.
      Rejected if SL distance > 3 × ATR14 (stop is too wide).

    Take Profit:
      TP1: Next confirmed swing high / low beyond current price.
      TP2: Second swing high / low beyond TP1 (partial runner).

    Gates:
      - Any non-neutral bias (strong or moderate)
      - At least 30 LTF bars for EMA stabilisation
      - R:R ≥ 1.5
      - Confluence score ≥ (_MIN_SCORE − 2.0)  [lower bar than Trade setups]
    """
    from .confluence import score_zone
    from .schemas import TradeSetup, ConfluenceReport

    # Accept non-neutral bias with matching HTF Hidden Divergence (Edge Case 3)
    if bias.bias == "neutral":
        return None

    direction = "long" if "bull" in bias.bias else "short"
    if direction == "long" and bias.hidden_div != "bullish" and bias.bias != "strong_bull":
        return None
    if direction == "short" and bias.hidden_div != "bearish" and bias.bias != "strong_bear":
        return None

    if len(df_ltf) < 30:
        return None

    close      = df_ltf["Close"]
    ema21_val  = float(close.ewm(span=21, adjust=False).mean().iloc[-1])
    atr        = _get_atr(df_ltf)

    # Overextension check (Edge Case 5)
    if abs(current_price - ema21_val) > atr * 2.5:
        logger.debug("[trader.setups] %s EMA Pullback: price overextended from EMA21", ticker)
        return None

    # ── EMA pullback / cross trigger with depth & wick guards (Edge Case 2) ──
    if not _ema_pullback_triggered(df_ltf, direction, atr=atr):
        logger.debug("[trader.setups] %s EMA Pullback: no trigger on last bar", ticker)
        return None

    # Entry zone: between current close and the 21 EMA touch level
    entry_low  = min(current_price, ema21_val)
    entry_high = max(current_price, ema21_val)
    entry_mid  = (entry_low + entry_high) / 2


    # ── Stop Loss: previous structural swing ─────────────────────────────────
    if direction == "long":
        sl_anchor = _prev_swing_low_price(scanner.structure, current_price)
        if sl_anchor is None:
            sl_anchor = entry_mid - (atr * 1.5)   # fallback when no swing low found
        stop_loss = sl_anchor - (atr * 0.25)      # 0.25 ATR buffer below swing
        invalidation = f"Close below prev swing low {sl_anchor:,.4f}"
    else:
        sl_anchor = _prev_swing_high_price(scanner.structure, current_price)
        if sl_anchor is None:
            sl_anchor = entry_mid + (atr * 1.5)
        stop_loss = sl_anchor + (atr * 0.25)      # 0.25 ATR buffer above swing
        invalidation = f"Close above prev swing high {sl_anchor:,.4f}"

    # Gate: SL must not be wider than 3 × ATR (avoids terrible R:R)
    sl_distance = abs(entry_mid - stop_loss)
    if sl_distance > atr * 3:
        logger.debug(
            "[trader.setups] %s EMA Pullback: SL %.4f too wide (%.2f ATR > 3 ATR)",
            ticker, sl_distance, sl_distance / atr,
        )
        return None

    # ── Take Profit: next two structural swings ─────────────────────────────
    if direction == "long":
        tp1, tp2 = _next_two_swing_highs(scanner.structure, current_price)
        if tp1 is None:
            tp1 = entry_mid * 1.03   # fallback: 3% above entry
    else:
        tp1, tp2 = _next_two_swing_lows(scanner.structure, current_price)
        if tp1 is None:
            tp1 = entry_mid * 0.97   # fallback: 3% below entry

    # R:R gate
    rr = _calc_rr(entry_mid, stop_loss, tp1)
    if rr < 1.5:
        logger.debug(
            "[trader.setups] %s EMA Pullback: R:R %.2f < 1.5 — skipping", ticker, rr
        )
        return None

    # ── Confluence ──────────────────────────────────────────────────────────
    # EMA setups aren't price-level zone setups, so lower the confluence gate by 2
    report = score_zone(entry_low, entry_high, sr_lvls, vp)
    if report.score < max(0, _MIN_SCORE - 2.0):
        logger.debug(
            "[trader.setups] %s EMA Pullback: score %.1f too low", ticker, report.score
        )
        return None

    # Cap confidence at "medium" for moderate-bias signals
    if "moderate" in bias.bias and report.label == "high":
        report = ConfluenceReport(
            score=report.score,
            sr_hits=report.sr_hits,
            vp_hits=report.vp_hits,
            poc=report.poc, vah=report.vah, val=report.val,
            label="medium",
        )

    return TradeSetup(
        ticker=ticker, category=category, mode=mode,
        setup_name="EMA Pullback + Hidden Div",
        setup_type="continuation",
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


def _get_atr(df, period: int = 14) -> float:
    """Calculate the Average True Range (ATR) over the last `period` bars."""
    import pandas as pd
    if len(df) < period:
        return float((df["High"].iloc[-1] - df["Low"].iloc[-1]) * 0.5)
    tr1 = df["High"] - df["Low"]
    tr2 = (df["High"] - df["Close"].shift(1)).abs()
    tr3 = (df["Low"] - df["Close"].shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


# ── Setup 4 helper functions ───────────────────────────────────────────────

def _ema_pullback_triggered(
    df_ltf,
    direction: str,
    ema8=None,
    ema21=None,
    ema50=None,
    atr: float | None = None,
) -> bool:
    """Return True if the last bar shows an EMA pullback entry signal with edge-case guards.

    LONG triggers:
      1. Rejection wick:
         - Bar LOW <= 21 EMA AND bar CLOSE > 21 EMA.
         - Depth guard: bar LOW must not breach EMA21 by > 1.5 * ATR (prevents falling knives).
         - Structural floor: bar CLOSE must remain >= EMA50.
         - Wick quality: CLOSE finishes in the upper 40%+ of the bar range.
      2. Bullish cross:
         - 8 EMA crossed above 21 EMA in the last 5 bars AND CLOSE > 21 EMA.

    SHORT triggers:
      1. Rejection wick:
         - Bar HIGH >= 21 EMA AND bar CLOSE < 21 EMA.
         - Depth guard: bar HIGH must not exceed EMA21 by > 1.5 * ATR.
         - Structural ceiling: bar CLOSE must remain <= EMA50.
         - Wick quality: CLOSE finishes in the lower 40%+ of the bar range.
      2. Bearish cross:
         - 8 EMA crossed below 21 EMA in the last 5 bars AND CLOSE < 21 EMA.
    """
    close = df_ltf["Close"]
    high  = df_ltf["High"]
    low   = df_ltf["Low"]
    # Use pre-computed series if provided, otherwise compute fresh
    ema8  = ema8  if ema8  is not None else close.ewm(span=8,  adjust=False).mean()
    ema21 = ema21 if ema21 is not None else close.ewm(span=21, adjust=False).mean()
    if ema50 is None and len(df_ltf) >= 50:
        ema50 = close.ewm(span=50, adjust=False).mean()

    e21        = float(ema21.iloc[-1])
    last_close = float(close.iloc[-1])
    last_high  = float(high.iloc[-1])
    last_low   = float(low.iloc[-1])
    bar_range  = max(last_high - last_low, 1e-8)
    e50        = float(ema50.iloc[-1]) if ema50 is not None and len(ema50) else None

    # Scan last 5 bars for EMA cross (index: -5 to -1)
    n = min(5, len(df_ltf) - 1)

    if direction == "long":
        # Guard: Deep pierce check (Edge Case 2)
        if atr is not None and (e21 - last_low) > (atr * 1.5):
            return False
        # Guard: EMA50 structural floor
        if e50 is not None and last_close < e50:
            return False

        # Wick quality: close in upper 40% of bar range
        wick_rejection = (last_close - last_low) >= (bar_range * 0.4)
        rejection = (last_low <= e21) and (last_close > e21) and wick_rejection

        cross = any(
            float(ema8.iloc[-i]) > float(ema21.iloc[-i])
            and float(ema8.iloc[-i - 1]) <= float(ema21.iloc[-i - 1])
            for i in range(1, n + 1)
        ) and (last_close > e21)

        return rejection or cross
    else:
        # Guard: Deep pierce check (Edge Case 2)
        if atr is not None and (last_high - e21) > (atr * 1.5):
            return False
        # Guard: EMA50 structural ceiling
        if e50 is not None and last_close > e50:
            return False

        # Wick quality: close in lower 40% of bar range
        wick_rejection = (last_high - last_close) >= (bar_range * 0.4)
        rejection = (last_high >= e21) and (last_close < e21) and wick_rejection

        cross = any(
            float(ema8.iloc[-i]) < float(ema21.iloc[-i])
            and float(ema8.iloc[-i - 1]) >= float(ema21.iloc[-i - 1])
            for i in range(1, n + 1)
        ) and (last_close < e21)

        return rejection or cross



def _prev_swing_low_price(structure, current_price: float) -> Optional[float]:
    """Most recent confirmed swing low strictly below current price (nearest = highest)."""
    if structure is None or not hasattr(structure, "points"):
        return None
    lows = [sp.price for sp in structure.points
            if sp.kind == "low" and sp.price < current_price]
    return max(lows) if lows else None


def _prev_swing_high_price(structure, current_price: float) -> Optional[float]:
    """Most recent confirmed swing high strictly above current price (nearest = lowest)."""
    if structure is None or not hasattr(structure, "points"):
        return None
    highs = [sp.price for sp in structure.points
             if sp.kind == "high" and sp.price > current_price]
    return min(highs) if highs else None


def _next_two_swing_highs(
    structure, current_price: float
) -> tuple[Optional[float], Optional[float]]:
    """Return the two nearest swing highs above current price (TP1, TP2)."""
    if structure is None or not hasattr(structure, "points"):
        return None, None
    highs = sorted(
        sp.price for sp in structure.points
        if sp.kind == "high" and sp.price > current_price
    )
    tp1 = highs[0] if highs else None
    tp2 = highs[1] if len(highs) > 1 else None
    return tp1, tp2


def _next_two_swing_lows(
    structure, current_price: float
) -> tuple[Optional[float], Optional[float]]:
    """Return the two nearest swing lows below current price (TP1, TP2)."""
    if structure is None or not hasattr(structure, "points"):
        return None, None
    lows = sorted(
        (sp.price for sp in structure.points
         if sp.kind == "low" and sp.price < current_price),
        reverse=True,
    )
    tp1 = lows[0] if lows else None
    tp2 = lows[1] if len(lows) > 1 else None
    return tp1, tp2


# ── Registry population (runs once at import time) ────────────────────────────
# Order here controls evaluation priority. Add or remove entries to enable /
# disable setups permanently; use DISABLED_SETUPS for runtime toggling.
_SETUP_REGISTRY.extend([
    ("OB Retracement",          _check_ob_retracement),
    ("Liquidity Sweep + FVG",   _check_sweep_fvg),
    ("Displacement + FVG",      _check_displacement_fvg),
    ("EMA Pullback + Hidden Div", _check_ema_pullback),
])

def _get_all_setup_names() -> list[str]:
    """Return all strategy names from auto-discovered strategies."""
    try:
        from .strategies import get_all_strategies
        return [s.name for s in get_all_strategies()]
    except Exception:
        # Fallback for edge cases (e.g. during import of strategies/)
        return [name for name, _ in _SETUP_REGISTRY]

ALL_SETUP_NAMES: list[str] = _get_all_setup_names()


# ── Auto-load disabled setups from config (runs once at import time) ──────────
# This means ALL callers (jobs.py, commands.py, backtest.py) automatically
# respect the saved config without any extra wiring.
def _load_config_into_disabled() -> None:
    try:
        from .manage import get_disabled_setups
        saved = get_disabled_setups()
        DISABLED_SETUPS.update(saved)
        if saved:
            logger.debug(
                "[trader.setups] Loaded %d disabled setup(s) from config: %s",
                len(saved), ", ".join(sorted(saved)),
            )
    except Exception:
        pass   # config missing or corrupt — start with all enabled


_load_config_into_disabled()
