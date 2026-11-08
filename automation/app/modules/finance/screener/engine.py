"""Core screening engine orchestrating universe scans across Concepts A-E."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from .schemas import ScreenedCandidate, ScreenReport
from .universes.nifty100 import (
    NIFTY_100_CONSTITUENTS,
    get_nifty100_symbols,
    get_stock_info_map,
)
from .fetcher import fetch_batch_ohlcv
from .filters.technical import (
    compute_concept_a_relative_strength,
    compute_concept_b_volatility_squeeze,
    compute_concept_c_volume_flow,
)
from .filters.sector import (
    compute_sector_momentum,
    evaluate_concept_e_sector_leaders,
)
from .filters.fundamental import (
    evaluate_concept_d_fundamental_quality,
)
from .populator import populate_screened_candidates, purge_expired_entries

logger = logging.getLogger(__name__)


def run_weekly_ta_screen(
    top_n: int = 15,
    auto_populate: bool = True,
) -> ScreenReport:
    """Execute Weekly TA Screening pass across Indian Nifty 100.

    Evaluates:
      - Concept A: Relative Strength (Minervini Stage 2 vs Nifty 50)
      - Concept B: Volatility Squeeze (BBW / NR7)
      - Concept C: Institutional Volume Surge (RVOL / Flow)
      - Concept E: Sector Momentum Leaders
    """
    logger.info("[screener.engine] Starting Weekly TA Screen for Indian Equities...")
    symbols = get_nifty100_symbols()
    info_map = get_stock_info_map()

    # Fetch Nifty 50 benchmark (^NSEI) and all universe stocks
    all_symbols_to_fetch = ["^NSEI"] + symbols
    ohlcv_map = fetch_batch_ohlcv(all_symbols_to_fetch, period="1y", interval="1d")
    df_benchmark = ohlcv_map.get("^NSEI")

    # 1. Sector momentum ranking (Concept E pre-calculation)
    sector_rankings = compute_sector_momentum(ohlcv_map, lookback_bars=20)
    leading_sectors = {s["sector"] for s in sector_rankings[:2]} if sector_rankings else set()

    candidates: list[ScreenedCandidate] = []

    for sym in symbols:
        df = ohlcv_map.get(sym)
        if df is None or len(df) < 30:
            continue

        stock_info = info_map.get(sym)
        display_name = stock_info.display_name if stock_info else sym
        sector = stock_info.sector if stock_info else "Unclassified"
        current_price = float(df["Close"].iloc[-1])

        concept_tags: list[str] = []
        strategy_types_set: set[str] = set()
        total_score = 0.0
        combined_metrics: dict = {}

        # Concept A: Relative Strength
        res_a = compute_concept_a_relative_strength(df, df_benchmark=df_benchmark)
        if res_a:
            concept_tags.append(res_a["concept"])
            strategy_types_set.update(res_a["target_strategies"])
            total_score += res_a["score"]
            combined_metrics["concept_a"] = res_a["metrics"]

        # Concept B: Volatility Squeeze
        res_b = compute_concept_b_volatility_squeeze(df)
        if res_b:
            concept_tags.append(res_b["concept"])
            strategy_types_set.update(res_b["target_strategies"])
            total_score += res_b["score"]
            combined_metrics["concept_b"] = res_b["metrics"]

        # Concept C: Volume Flow
        res_c = compute_concept_c_volume_flow(df)
        if res_c:
            concept_tags.append(res_c["concept"])
            strategy_types_set.update(res_c["target_strategies"])
            total_score += res_c["score"]
            combined_metrics["concept_c"] = res_c["metrics"]

        # Concept E: Sector Leader
        if stock_info:
            res_e = evaluate_concept_e_sector_leaders(df, stock_info, leading_sectors)
            if res_e:
                concept_tags.append(res_e["concept"])
                strategy_types_set.update(res_e["target_strategies"])
                total_score += res_e["score"]
                combined_metrics["concept_e"] = res_e["metrics"]

        # If any TA concept triggered
        if concept_tags and total_score > 0:
            candidate = ScreenedCandidate(
                symbol=sym,
                display_name=display_name,
                category="indian_stock",
                current_price=current_price,
                score=round(total_score, 2),
                concept_tags=concept_tags,
                strategy_types=sorted(strategy_types_set),
                cadence="weekly",
                sector=sector,
                metrics=combined_metrics,
            )
            candidates.append(candidate)

    # Sort candidates descending by aggregated score
    candidates.sort(key=lambda c: c.score, reverse=True)
    top_candidates = candidates[:top_n]

    # Purge any expired items and optionally auto-populate
    if auto_populate and top_candidates:
        populate_screened_candidates(top_candidates, cadence="weekly", expires_in_days=7)

    return ScreenReport(
        universe="Nifty 100",
        cadence="weekly",
        screened_at=datetime.now(timezone.utc),
        total_universe_size=len(symbols),
        qualified_count=len(candidates),
        candidates=top_candidates,
        sector_rankings=sector_rankings,
    )


def run_monthly_fa_screen(
    top_n: int = 10,
    auto_populate: bool = True,
) -> ScreenReport:
    """Execute Monthly FA Screening pass across Indian Nifty 100.

    Evaluates:
      - Concept D: Fundamental Quality + Margin Integrity + SMA200 Gate
    """
    logger.info("[screener.engine] Starting Monthly FA Screen for Indian Equities...")
    symbols = get_nifty100_symbols()
    info_map = get_stock_info_map()

    # Pre-fetch 1y daily OHLCV for technical filter (SMA200)
    ohlcv_map = fetch_batch_ohlcv(symbols, period="1y", interval="1d")

    candidates: list[ScreenedCandidate] = []

    for sym in symbols:
        df = ohlcv_map.get(sym)
        stock_info = info_map.get(sym)
        display_name = stock_info.display_name if stock_info else sym
        sector = stock_info.sector if stock_info else "Unclassified"
        current_price = float(df["Close"].iloc[-1]) if df is not None and not df.empty else 0.0

        res_d = evaluate_concept_d_fundamental_quality(sym, df_daily=df)
        if res_d:
            candidate = ScreenedCandidate(
                symbol=sym,
                display_name=display_name,
                category="indian_stock",
                current_price=current_price,
                score=res_d["score"],
                concept_tags=[res_d["concept"]],
                strategy_types=res_d["target_strategies"],
                cadence="monthly",
                sector=sector,
                metrics={"concept_d": res_d["metrics"]},
            )
            candidates.append(candidate)

    candidates.sort(key=lambda c: c.score, reverse=True)
    top_candidates = candidates[:top_n]

    if auto_populate and top_candidates:
        populate_screened_candidates(top_candidates, cadence="monthly", expires_in_days=30)

    return ScreenReport(
        universe="Nifty 100",
        cadence="monthly",
        screened_at=datetime.now(timezone.utc),
        total_universe_size=len(symbols),
        qualified_count=len(candidates),
        candidates=top_candidates,
        sector_rankings=[],
    )
