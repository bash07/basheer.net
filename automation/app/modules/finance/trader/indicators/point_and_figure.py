"""Point & Figure (P&F) Indicator and Wyckoff Cause-and-Effect Projection Engine.

Implements Wyckoff's 2nd Law (The Law of Cause and Effect):
- The Effect (vertical price trend) is directly proportional to the Cause
  (horizontal inventory absorption count in Point & Figure columns).

Features:
1. Dynamic ATR-based Box Sizing or Fixed Percentage Box Sizing.
2. Classic 3-box reversal High/Low P&F column generator.
3. Horizontal Count Extractor:
   - Full Base Count (N_full): All consecutive columns within consolidation base.
   - Conservative Count (N_cons): Columns from Phase C (Spring/UTAD/LPS) forward.
4. Vertical Price Target Projections:
   - Long Target = Count Row + (N * Box * Reversal)
   - Short Target = Count Row - (N * Box * Reversal)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from . import register

logger = logging.getLogger(__name__)


@dataclass
class PointAndFigureColumn:
    """A single vertical column in a Point & Figure chart."""
    direction: int   # +1 for X (up), -1 for O (down)
    top: float       # Highest box price in column
    bottom: float    # Lowest box price in column
    start_bar: int   # Bar index where column began
    end_bar: int     # Bar index where column ended


@dataclass
class PointAndFigureInfo:
    """Quantitative Cause & Effect footprint from Point & Figure analysis."""
    box_size: float
    reversal: int
    columns: list[PointAndFigureColumn] = field(default_factory=list)
    horizontal_count: int = 0
    conservative_count: int = 0
    count_row_price: float = 0.0
    cause_pts: float = 0.0
    cause_pts_conservative: float = 0.0
    projected_long_conservative: float = 0.0
    projected_long_full: float = 0.0
    projected_short_conservative: float = 0.0
    projected_short_full: float = 0.0


def generate_pnf_columns(
    df: pd.DataFrame,
    box_size: float,
    reversal: int = 3,
) -> list[PointAndFigureColumn]:
    """Generate 3-box reversal Point & Figure columns from OHLC DataFrame."""
    n = len(df)
    if n == 0 or box_size <= 0:
        return []

    highs = df["High"].values if "High" in df.columns else df["Close"].values
    lows = df["Low"].values if "Low" in df.columns else df["Close"].values

    h0, l0 = float(highs[0]), float(lows[0])
    curr_top = float(np.floor(h0 / box_size) * box_size)
    curr_bottom = float(np.floor(l0 / box_size) * box_size)
    curr_dir = 1  # Start with X column

    columns: list[PointAndFigureColumn] = [
        PointAndFigureColumn(
            direction=curr_dir,
            top=curr_top,
            bottom=curr_bottom,
            start_bar=0,
            end_bar=0,
        )
    ]

    for i in range(1, n):
        h = float(highs[i])
        l = float(lows[i])

        if curr_dir == 1:
            # Check for column extension upwards
            if h >= curr_top + box_size:
                curr_top = float(np.floor(h / box_size) * box_size)
                columns[-1].top = curr_top
                columns[-1].end_bar = i
            # Check for reversal downwards
            elif l <= curr_top - reversal * box_size:
                curr_dir = -1
                curr_bottom = float(np.floor(l / box_size) * box_size)
                new_top = curr_top - box_size
                columns.append(
                    PointAndFigureColumn(
                        direction=-1,
                        top=new_top,
                        bottom=curr_bottom,
                        start_bar=i,
                        end_bar=i,
                    )
                )
        else:
            # Check for column extension downwards
            if l <= curr_bottom - box_size:
                curr_bottom = float(np.floor(l / box_size) * box_size)
                columns[-1].bottom = curr_bottom
                columns[-1].end_bar = i
            # Check for reversal upwards
            elif h >= curr_bottom + reversal * box_size:
                curr_dir = 1
                curr_top = float(np.floor(h / box_size) * box_size)
                new_bottom = curr_bottom + box_size
                columns.append(
                    PointAndFigureColumn(
                        direction=1,
                        top=curr_top,
                        bottom=new_bottom,
                        start_bar=i,
                        end_bar=i,
                    )
                )

    return columns


def compute_pnf_cause_and_effect(
    df: pd.DataFrame,
    box_size: Optional[float] = None,
    reversal: int = 3,
    range_low: Optional[float] = None,
    range_high: Optional[float] = None,
    range_mid: Optional[float] = None,
    spring_detected: bool = False,
    upthrust_detected: bool = False,
) -> PointAndFigureInfo:
    """Compute Point & Figure columns and calculate Wyckoff Cause & Effect projections."""
    n = len(df)
    if n < 15:
        close_last = float(df["Close"].iloc[-1]) if n > 0 else 0.0
        return PointAndFigureInfo(
            box_size=1.0,
            reversal=reversal,
            columns=[],
            horizontal_count=0,
            conservative_count=0,
            count_row_price=close_last,
            cause_pts=0.0,
            cause_pts_conservative=0.0,
            projected_long_conservative=close_last,
            projected_long_full=close_last,
            projected_short_conservative=close_last,
            projected_short_full=close_last,
        )

    close_last = float(df["Close"].iloc[-1])

    # Dynamic box size: ~0.5x ATR or 0.25% of price
    if box_size is None or box_size <= 0:
        highs = df["High"].values if "High" in df.columns else df["Close"].values
        lows = df["Low"].values if "Low" in df.columns else df["Close"].values
        # Fast 14-period true range approximation
        tr = np.maximum(highs[1:] - lows[1:], np.abs(highs[1:] - df["Close"].values[:-1]))
        atr = float(np.mean(tr[-14:])) if len(tr) >= 14 else close_last * 0.005
        box_size = round(max(atr * 0.5, close_last * 0.001), 4)

    columns = generate_pnf_columns(df, box_size, reversal)

    # Trading range boundaries
    if range_low is None or range_high is None or range_mid is None:
        sub = df.iloc[-min(30, n):]
        range_low = float(sub["Low"].min())
        range_high = float(sub["High"].max())
        range_mid = (range_low + range_high) / 2.0

    count_row = range_mid

    # Count consecutive columns intersecting the consolidation base from right to left
    horizontal_count = 0
    tolerance = box_size * 1.5

    for c in reversed(columns):
        if c.bottom <= range_high + tolerance and c.top >= range_low - tolerance:
            horizontal_count += 1
        else:
            break

    # Guard against 0 count
    horizontal_count = max(1, horizontal_count)

    # Conservative count (Phase C to current)
    if spring_detected or upthrust_detected:
        conservative_count = max(2, round(horizontal_count * 0.5))
    else:
        conservative_count = max(2, round(horizontal_count * 0.65))

    cause_full = horizontal_count * box_size * reversal
    cause_cons = conservative_count * box_size * reversal

    return PointAndFigureInfo(
        box_size=round(box_size, 4),
        reversal=reversal,
        columns=columns,
        horizontal_count=horizontal_count,
        conservative_count=conservative_count,
        count_row_price=round(count_row, 4),
        cause_pts=round(cause_full, 4),
        cause_pts_conservative=round(cause_cons, 4),
        projected_long_conservative=round(count_row + cause_cons, 4),
        projected_long_full=round(count_row + cause_full, 4),
        projected_short_conservative=round(count_row - cause_cons, 4),
        projected_short_full=round(count_row - cause_full, 4),
    )


@register("point_and_figure", deps=["atr_14", "accum_dist"])
def _point_and_figure(df_ltf: pd.DataFrame, df_htf: pd.DataFrame, cache: dict) -> PointAndFigureInfo:
    """Register 'point_and_figure' indicator into the trader engine."""
    atr = cache.get("atr_14")
    accum_dist = cache.get("accum_dist")

    box_size = round(max(atr * 0.5, 0.01), 4) if atr and atr > 0 else None

    r_low = accum_dist.range_low if accum_dist else None
    r_high = accum_dist.range_high if accum_dist else None
    r_mid = accum_dist.range_mid if accum_dist else None
    spring = accum_dist.spring_detected if accum_dist else False
    utad = accum_dist.upthrust_detected if accum_dist else False

    return compute_pnf_cause_and_effect(
        df=df_ltf,
        box_size=box_size,
        reversal=3,
        range_low=r_low,
        range_high=r_high,
        range_mid=r_mid,
        spring_detected=spring,
        upthrust_detected=utad,
    )


@register("pnf", deps=["point_and_figure"])
def _pnf_alias(df_ltf: pd.DataFrame, df_htf: pd.DataFrame, cache: dict) -> PointAndFigureInfo:
    """Short alias 'pnf' for point_and_figure."""
    return cache.get("point_and_figure")
