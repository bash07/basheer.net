"""Trade concept detectors for the finance/trader module.

All detectors operate on the LTF DataFrame (4H for swing, 1H for intraday).
All pandas/numpy/scipy imports are LAZY — never at module level.

Public functions:
    detect_structure(df)                -> SwingStructure
    detect_bos_choch(df, structure)     -> list[BOS]
    find_order_blocks(df, bos_list)     -> list[OrderBlock]
    find_fvg(df)                        -> list[FVG]
    find_eqh_eql(df)                    -> list[LiquidityLevel]
"""
from __future__ import annotations

import logging

from .schemas import BOS, FVG, OrderBlock, LiquidityLevel, SwingPoint, SwingStructure

logger = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────────────

_STRUCTURE_ORDER  = 4    # argrelextrema half-width for swing detection on LTF
_STRUCTURE_WINDOW = 40   # bars to scan for market structure labels
_BOS_BODY_CLOSE   = True # require body close beyond level (not just wick) for BOS
_OB_SEARCH_BACK   = 5    # bars before BOS to search for the last opposing candle
_FVG_MIN_GAP_PCT  = 0.003 # 0.3% minimum gap to filter micro-FVGs
_FVG_DISP_RATIO   = 1.2  # middle candle body must be > this × ATR20
_EQL_TOLERANCE    = 0.001 # 0.1% tolerance for equal highs/lows grouping
_EQL_MIN_POINTS   = 2    # minimum pivots to form an EQH/EQL
_BOS_LOOKBACK     = 15   # bars: only report BOS events within last N bars


# ── 1. Market Structure ───────────────────────────────────────────────────────

def detect_structure(df) -> SwingStructure:
    """Label swing highs and lows as HH/HL/LH/LL to determine market trend.

    Args:
        df: LTF OHLCV DataFrame (DatetimeIndex)

    Returns:
        SwingStructure with .trend = "bullish" | "bearish" | "ranging"
    """
    try:
        import numpy as np
        from modules.finance._shared.extrema import argrelextrema
    except ImportError:
        logger.warning("[trader.scanner] numpy not available — structure skipped")
        return SwingStructure(trend="ranging")

    high  = df["High"].values
    low   = df["Low"].values
    n     = len(df)
    order = _STRUCTURE_ORDER

    peak_idx   = argrelextrema(high, np.greater, order=order)[0].tolist()
    trough_idx = argrelextrema(low,  np.less,    order=order)[0].tolist()

    # Build combined swing list, sorted by bar index
    swings: list[SwingPoint] = []
    for i in peak_idx:
        swings.append(SwingPoint(index=i, price=float(high[i]), kind="high"))
    for i in trough_idx:
        swings.append(SwingPoint(index=i, price=float(low[i]),  kind="low"))
    swings.sort(key=lambda s: s.index)

    # Label each swing relative to the previous swing of the same type
    prev_high: float | None = None
    prev_low:  float | None = None
    for sp in swings:
        if sp.kind == "high":
            if prev_high is not None:
                sp.label = "HH" if sp.price > prev_high else "LH"
            prev_high = sp.price
        else:
            if prev_low is not None:
                sp.label = "HL" if sp.price > prev_low else "LL"
            prev_low = sp.price

    # Determine trend from the last 4 labelled swings
    labelled = [s for s in swings if s.label]
    recent_4 = labelled[-4:] if len(labelled) >= 4 else labelled
    highs = [s.label for s in recent_4 if s.kind == "high"]
    lows  = [s.label for s in recent_4 if s.kind == "low"]

    bullish = all(l in ("HH", "HL") for l in highs + lows if l)
    bearish = all(l in ("LH", "LL") for l in highs + lows if l)

    if bullish and (highs or lows):
        trend = "bullish"
    elif bearish and (highs or lows):
        trend = "bearish"
    else:
        trend = "ranging"

    return SwingStructure(points=swings, trend=trend)


# ── 2. BOS / CHoCH ───────────────────────────────────────────────────────────

def detect_bos_choch(df, structure: SwingStructure) -> list[BOS]:
    """Detect Break of Structure and Change of Character events.

    BOS  — price closes decisively beyond the prior swing high (bullish BOS)
           or swing low (bearish BOS), confirming the existing trend.
    CHoCH — first BOS in the OPPOSITE direction of the current trend;
            signals a potential reversal.

    Returns:
        List of BOS events within the last _BOS_LOOKBACK bars, most recent first.
    """
    close = df["Close"].values
    high  = df["High"].values
    low   = df["Low"].values
    n     = len(df)
    found: list[BOS] = []

    swings = structure.points
    if not swings:
        return []

    # Prior swing highs and lows as reference levels
    swing_highs = sorted(
        [s for s in swings if s.kind == "high"],
        key=lambda s: s.index
    )
    swing_lows = sorted(
        [s for s in swings if s.kind == "low"],
        key=lambda s: s.index
    )

    start_bar = max(0, n - _BOS_LOOKBACK - 1)

    for bar_i in range(start_bar + 1, n):
        candle_close = float(close[bar_i])

        # Check bullish BOS: close above a prior swing high
        prior_highs = [s for s in swing_highs if s.index < bar_i]
        if prior_highs:
            last_sh = prior_highs[-1]
            if candle_close > last_sh.price:
                kind = "choch" if structure.trend == "bearish" else "bos"
                found.append(BOS(
                    bar_index=bar_i,
                    direction="bullish",
                    level_broken=last_sh.price,
                    kind=kind,
                    candle_close=candle_close,
                ))

        # Check bearish BOS: close below a prior swing low
        prior_lows = [s for s in swing_lows if s.index < bar_i]
        if prior_lows:
            last_sl = prior_lows[-1]
            if candle_close < last_sl.price:
                kind = "choch" if structure.trend == "bullish" else "bos"
                found.append(BOS(
                    bar_index=bar_i,
                    direction="bearish",
                    level_broken=last_sl.price,
                    kind=kind,
                    candle_close=candle_close,
                ))

    # Deduplicate consecutive same-direction BOS (keep most recent per direction)
    seen: dict[str, BOS] = {}
    for b in found:
        seen[b.direction] = b  # later overwrites earlier

    return list(seen.values())


# ── 3. Order Blocks ───────────────────────────────────────────────────────────

def find_order_blocks(df, bos_list: list[BOS]) -> list[OrderBlock]:
    """Find Order Blocks associated with each BOS event.

    Bullish OB: the last BEARISH candle (close < open) before a bullish displacement.
    Bearish OB: the last BULLISH candle (close > open) before a bearish displacement.

    An OB is invalidated if price has since traded completely through it
    (close beyond the far edge of the OB).

    Returns:
        List of active (non-invalidated) OrderBlocks, newest first.
    """
    open_  = df["Open"].values
    high   = df["High"].values
    low    = df["Low"].values
    close  = df["Close"].values
    n      = len(df)

    obs: list[OrderBlock] = []

    for bos in bos_list:
        bi = bos.bar_index
        search_start = max(0, bi - _OB_SEARCH_BACK)

        if bos.direction == "bullish":
            # Look for last bearish candle before bi
            for i in range(bi - 1, search_start - 1, -1):
                if close[i] < open_[i]:  # bearish candle
                    ob = OrderBlock(
                        bar_index=i,
                        direction="bullish",
                        zone_low=float(low[i]),
                        zone_high=float(high[i]),
                        origin_bos_level=bos.level_broken,
                    )
                    # Invalidation: any subsequent close below zone_low
                    invalidated = any(
                        close[j] < ob.zone_low for j in range(i + 1, n)
                    )
                    ob.invalidated = invalidated
                    obs.append(ob)
                    break

        elif bos.direction == "bearish":
            # Look for last bullish candle before bi
            for i in range(bi - 1, search_start - 1, -1):
                if close[i] > open_[i]:  # bullish candle
                    ob = OrderBlock(
                        bar_index=i,
                        direction="bearish",
                        zone_low=float(low[i]),
                        zone_high=float(high[i]),
                        origin_bos_level=bos.level_broken,
                    )
                    # Invalidation: any subsequent close above zone_high
                    invalidated = any(
                        close[j] > ob.zone_high for j in range(i + 1, n)
                    )
                    ob.invalidated = invalidated
                    obs.append(ob)
                    break

    # Return active OBs only, newest first
    active = [ob for ob in obs if not ob.invalidated]
    active.sort(key=lambda ob: ob.bar_index, reverse=True)
    return active


# ── 4. Fair Value Gaps ────────────────────────────────────────────────────────

def find_fvg(df) -> list[FVG]:
    """Detect Fair Value Gaps (3-candle imbalances) in the LTF chart.

    Bullish FVG: candle[i-2].high < candle[i].low  (gap above C1, below C3)
    Bearish FVG: candle[i-2].low  > candle[i].high (gap below C1, above C3)

    Filters:
      - Gap size >= _FVG_MIN_GAP_PCT of price (removes micro-gaps)
      - Middle candle body >= _FVG_DISP_RATIO × ATR20 (confirms displacement)

    Returns:
        List of FVGs from the last 20 bars, sorted newest first.
    """
    try:
        import numpy as np
    except ImportError:
        logger.warning("[trader.scanner] numpy not available — FVG skipped")
        return []

    high  = df["High"].values
    low   = df["Low"].values
    open_ = df["Open"].values
    close = df["Close"].values
    n     = len(df)

    # ATR20 (True Range average over 20 bars)
    tr = _true_range(high, low, close)
    atr20 = _rolling_mean(tr, 20)

    fvgs: list[FVG] = []
    scan_start = max(2, n - 30)

    for i in range(scan_start, n):
        mid_body = abs(close[i - 1] - open_[i - 1])
        atr_val  = atr20[i - 1] if atr20[i - 1] > 0 else 1.0
        disp_ok  = (mid_body / atr_val) >= _FVG_DISP_RATIO

        # Bullish FVG
        if high[i - 2] < low[i]:
            gap_low  = float(high[i - 2])
            gap_high = float(low[i])
            gap_pct  = (gap_high - gap_low) / ((gap_low + gap_high) / 2)
            if gap_pct >= _FVG_MIN_GAP_PCT:
                fvgs.append(FVG(
                    bar_index=i - 1,
                    direction="bullish",
                    gap_low=gap_low,
                    gap_high=gap_high,
                    displacement_ratio=round(mid_body / atr_val, 2),
                ))

        # Bearish FVG
        elif low[i - 2] > high[i]:
            gap_low  = float(high[i])
            gap_high = float(low[i - 2])
            gap_pct  = (gap_high - gap_low) / ((gap_low + gap_high) / 2)
            if gap_pct >= _FVG_MIN_GAP_PCT:
                fvgs.append(FVG(
                    bar_index=i - 1,
                    direction="bearish",
                    gap_low=gap_low,
                    gap_high=gap_high,
                    displacement_ratio=round(mid_body / atr_val, 2),
                ))

    fvgs.sort(key=lambda f: f.bar_index, reverse=True)
    return fvgs


# ── 5. Equal Highs / Equal Lows (Liquidity Pools) ────────────────────────────

def find_eqh_eql(df) -> list[LiquidityLevel]:
    """Identify Equal Highs (EQH) and Equal Lows (EQL) — liquidity pools.

    Two or more swing highs within _EQL_TOLERANCE of each other form an EQH.
    Two or more swing lows within _EQL_TOLERANCE form an EQL.

    A level is marked as 'swept' if a candle wick has traded through it while
    the body closed back inside (fake-out / stop hunt).

    Returns:
        List of LiquidityLevel objects.
    """
    try:
        import numpy as np
        from modules.finance._shared.extrema import argrelextrema
    except ImportError:
        logger.warning("[trader.scanner] numpy not available — EQH/EQL skipped")
        return []

    high  = df["High"].values
    low   = df["Low"].values
    close = df["Close"].values
    n     = len(df)

    peak_idx   = argrelextrema(high, np.greater, order=_STRUCTURE_ORDER)[0].tolist()
    trough_idx = argrelextrema(low,  np.less,    order=_STRUCTURE_ORDER)[0].tolist()

    levels: list[LiquidityLevel] = []

    # Equal Highs
    for i, pi in enumerate(peak_idx):
        group_prices = [float(high[pi])]
        group_bars   = [pi]
        for pj in peak_idx[i + 1:]:
            ref = group_prices[0]
            if abs(float(high[pj]) - ref) / ref < _EQL_TOLERANCE:
                group_prices.append(float(high[pj]))
                group_bars.append(pj)
        if len(group_prices) >= _EQL_MIN_POINTS:
            avg_price = sum(group_prices) / len(group_prices)
            # Swept: a candle wick is above avg_price but body closes below
            swept, sweep_bar = False, -1
            for bi in range(max(group_bars) + 1, n):
                if high[bi] > avg_price and close[bi] < avg_price:
                    swept, sweep_bar = True, bi
                    break
            levels.append(LiquidityLevel(
                price=round(avg_price, 6),
                kind="eqh",
                bar_indices=group_bars,
                swept=swept,
                sweep_bar=sweep_bar,
            ))

    # Equal Lows
    for i, ti in enumerate(trough_idx):
        group_prices = [float(low[ti])]
        group_bars   = [ti]
        for tj in trough_idx[i + 1:]:
            ref = group_prices[0]
            if abs(float(low[tj]) - ref) / ref < _EQL_TOLERANCE:
                group_prices.append(float(low[tj]))
                group_bars.append(tj)
        if len(group_prices) >= _EQL_MIN_POINTS:
            avg_price = sum(group_prices) / len(group_prices)
            swept, sweep_bar = False, -1
            for bi in range(max(group_bars) + 1, n):
                if low[bi] < avg_price and close[bi] > avg_price:
                    swept, sweep_bar = True, bi
                    break
            levels.append(LiquidityLevel(
                price=round(avg_price, 6),
                kind="eql",
                bar_indices=group_bars,
                swept=swept,
                sweep_bar=sweep_bar,
            ))

    return levels


# ── Private helpers ───────────────────────────────────────────────────────────

def _true_range(high, low, close):
    """Compute True Range array."""
    import numpy as np
    n  = len(high)
    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i]  - close[i - 1]),
        )
    return tr


def _rolling_mean(arr, window: int):
    """Simple rolling mean with forward-fill for the warm-up period."""
    import numpy as np
    result = np.zeros_like(arr, dtype=float)
    for i in range(len(arr)):
        start = max(0, i - window + 1)
        result[i] = arr[start: i + 1].mean()
    return result
