"""Fundamental analysis filter calculator for Concept D (Monthly FA)."""
from __future__ import annotations

import logging
from typing import Optional
import pandas as pd

from modules.finance.fundamental.fetcher import fetch_fundamentals, FundamentalsData
from modules.finance.fundamental.strategies.buffett import BuffettStrategy
from modules.finance.fundamental.strategies.lynch import LynchStrategy

logger = logging.getLogger(__name__)


def evaluate_concept_d_fundamental_quality(
    symbol: str,
    df_daily: Optional[pd.DataFrame] = None,
    fundamentals_data: Optional[FundamentalsData] = None,
) -> Optional[dict]:
    """Concept D: Fundamental Quality + Technical Momentum (Monthly FA).

    Rules:
      - Buffett Quality or Lynch GARP score >= 4 / 6
      - No 3-quarter operating margin compression
      - Technical filter: Price > SMA200 (avoids falling knives / value traps)
    """
    try:
        data = fundamentals_data or fetch_fundamentals(symbol)
    except Exception as e:
        logger.debug("[screener.fundamental] Failed to fetch fundamentals for %s: %s", symbol, e)
        return None

    if not data:
        return None

    # Evaluate Buffett Quality
    buffett_strat = BuffettStrategy()
    buffett_res = buffett_strat.evaluate(data)

    # Evaluate Lynch GARP
    lynch_strat = LynchStrategy()
    lynch_res = lynch_strat.evaluate(data)

    best_score = max(buffett_res.score, lynch_res.score)
    chosen_method = "buffett" if buffett_res.score >= lynch_res.score else "lynch"

    # Require minimum score of 4
    if best_score < 4:
        return None

    # Reject if 3-quarter margin compression warning is active
    if data.margin_trend_warning:
        return None

    # Technical trend gate: Price > SMA200
    if df_daily is not None and len(df_daily) >= 150:
        c = df_daily["Close"].values
        sma200 = float(pd.Series(c).rolling(min(len(c), 200)).mean().iloc[-1])
        if c[-1] < sma200 * 0.95:  # more than 5% below 200 SMA -> skip
            return None

    score = float(best_score) * 1.5  # maps 4-6 to 6.0-9.0

    return {
        "concept": "concept_d",
        "name": f"Fundamental Quality ({chosen_method.title()})",
        "score": round(score, 2),
        "target_strategies": ["EMAPullback", "CreepingDrift"],
        "metrics": {
            "method": chosen_method,
            "fundamental_score": f"{best_score}/6",
            "pe_ratio": data.pe_ratio,
            "roe": data.roe,
            "quarterly_margins": getattr(data, "quarterly_margins", None),
        },
    }
