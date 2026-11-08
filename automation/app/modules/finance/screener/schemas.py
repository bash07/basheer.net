"""Schemas for the finance/screener module."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class ScreenedCandidate:
    """A single security discovered and qualified by the screener."""
    symbol: str
    display_name: str
    category: str
    current_price: float
    score: float
    concept_tags: list[str] = field(default_factory=list)      # e.g. ["concept_a", "concept_b"]
    strategy_types: list[str] = field(default_factory=list)    # e.g. ["OBRetracement", "DisplacementFVG"]
    cadence: str = "weekly"                                     # "weekly" | "monthly"
    sector: Optional[str] = None
    metrics: dict[str, Any] = field(default_factory=dict)       # Raw calculated metrics for auditing/display


@dataclass
class ScreenReport:
    """Aggregated output of a full universe screening execution."""
    universe: str
    cadence: str                                                # "weekly" | "monthly"
    screened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    total_universe_size: int = 0
    qualified_count: int = 0
    candidates: list[ScreenedCandidate] = field(default_factory=list)
    sector_rankings: list[dict[str, Any]] = field(default_factory=list)
