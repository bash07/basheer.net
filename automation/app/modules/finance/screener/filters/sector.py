"""Sector momentum and rotation analysis for Concept E."""
from __future__ import annotations

import logging
from typing import Optional
import numpy as np
import pandas as pd

from ..universes.nifty100 import get_sector_map, StockInfo

logger = logging.getLogger(__name__)


def compute_sector_momentum(
    ohlcv_map: dict[str, pd.DataFrame],
    lookback_bars: int = 20,
) -> list[dict]:
    """Calculate the average momentum (Rate of Change) for each Nifty sector.

    Returns ranked list of sectors sorted by trailing performance.
    """
    sector_map = get_sector_map()
    sector_scores: list[dict] = []

    for sector_name, stocks in sector_map.items():
        returns: list[float] = []
        for stock in stocks:
            df = ohlcv_map.get(stock.symbol)
            if df is not None and len(df) >= lookback_bars + 1:
                c = df["Close"].values
                ret = (c[-1] - c[-lookback_bars]) / c[-lookback_bars]
                returns.append(ret)

        if returns:
            avg_ret = float(np.mean(returns))
            median_ret = float(np.median(returns))
            win_rate = float(np.mean([1.0 if r > 0 else 0.0 for r in returns]))
            sector_scores.append({
                "sector": sector_name,
                "avg_return_pct": round(avg_ret * 100, 2),
                "median_return_pct": round(median_ret * 100, 2),
                "breadth_pct": round(win_rate * 100, 1),
                "stock_count": len(returns),
            })

    # Sort descending by average return
    sector_scores.sort(key=lambda s: s["avg_return_pct"], reverse=True)
    return sector_scores


def evaluate_concept_e_sector_leaders(
    df: pd.DataFrame,
    stock_info: StockInfo,
    leading_sectors: set[str],
) -> Optional[dict]:
    """Concept E (Weekly TA): Identify leading stocks in outperforming sectors.

    Target Strategies: DisplacementFVG, OBRetracement, EMAPullback
    """
    if stock_info.sector not in leading_sectors:
        return None

    if len(df) < 30:
        return None

    close = df["Close"].values
    ret_20d = (close[-1] - close[-20]) / close[-20] if len(close) >= 20 else 0.0

    # Only include stocks that are positively advancing with the sector
    if ret_20d <= 0.02:  # at least +2% in 20 days
        return None

    score = 5.0 + min(5.0, ret_20d * 20.0)

    return {
        "concept": "concept_e",
        "name": f"Sector Leader ({stock_info.sector})",
        "score": round(score, 2),
        "target_strategies": ["DisplacementFVG", "OBRetracement", "EMAPullback"],
        "metrics": {
            "sector": stock_info.sector,
            "ret_20d_pct": round(ret_20d * 100, 2),
        },
    }
