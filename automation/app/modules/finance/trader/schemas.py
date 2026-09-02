"""Pydantic / dataclass schemas for the finance/trader module.

Internal ICT primitives (OrderBlock, FVG, etc.) use plain dataclasses for speed.
The top-level TradeSetup uses Pydantic for JSON serialisation into SQLite.

Import pattern:
    from modules.finance.trader.schemas import (
        BiasResult, TradeSetup, OrderBlock, FVG, LiquidityLevel,
        VolumeProfile, ConfluenceReport, SwingStructure, BOS,
    )
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


# ── Scan mode ─────────────────────────────────────────────────────────────────

# Category sets that determine which scan mode applies
SWING_CATEGORIES: frozenset[str] = frozenset(
    {"us_index", "us_stock", "indian_index", "indian_stock", "forex", "commodity"}
)
INTRADAY_CATEGORIES: frozenset[str] = frozenset({"crypto"})


def scan_mode_for(category: str) -> str:
    """Return 'swing' or 'intraday' based on the watchlist category."""
    if category in INTRADAY_CATEGORIES:
        return "intraday"
    return "swing"


def timeframes_for(mode: str) -> tuple[str, str, int]:
    """Return (htf, ltf, vp_lookback) for a given scan mode."""
    if mode == "intraday":
        return "1d", "1h", 60
    return "1wk", "4h", 100


# ── HTF Bias ──────────────────────────────────────────────────────────────────

class BiasResult(BaseModel):
    """Output of the HTF bias engine."""

    mode: str                        # "swing" | "intraday"
    htf_tf: str                      # "1wk" | "1d"
    ltf_tf: str                      # "4h"  | "1h"
    vp_lookback: int                 # 100   | 60
    rsi_value: float
    regime: str                      # "bull" | "bear" | "neutral"
    hidden_div: str                  # "bullish" | "bearish" | "none"
    failure_swing: str = "none"      # "bullish" | "bearish" | "none"  (v1.1)
    bias: str = "neutral"            # "strong_bull" | "strong_bear" |
                                     # "moderate_bull" | "moderate_bear" | "neutral"
    bias_notes: str = ""


# ── ICT Primitives (dataclasses for speed) ───────────────────────────────────

@dataclass
class SwingPoint:
    """A single swing high or swing low."""
    index: int          # bar index in the DataFrame
    price: float
    kind: str           # "high" | "low"
    label: str = ""     # "HH" | "HL" | "LH" | "LL"


@dataclass
class SwingStructure:
    """Series of labelled swing points describing market structure."""
    points: list[SwingPoint] = field(default_factory=list)
    trend: str = "ranging"   # "bullish" | "bearish" | "ranging"


@dataclass
class BOS:
    """Break of Structure or Change of Character event."""
    bar_index: int
    direction: str          # "bullish" | "bearish"
    level_broken: float     # the swing high/low that was exceeded
    kind: str = "bos"       # "bos" | "choch"
    candle_close: float = 0.0


@dataclass
class OrderBlock:
    """ICT Order Block — the last opposing candle before a displacement move."""
    bar_index: int
    direction: str              # "bullish" (OB is bearish candle) | "bearish"
    zone_low: float
    zone_high: float
    origin_bos_level: float     # the BOS level this OB is associated with
    invalidated: bool = False   # True if price closed through the OB

    @property
    def midpoint(self) -> float:
        return (self.zone_low + self.zone_high) / 2

    @property
    def zone_size_pct(self) -> float:
        return (self.zone_high - self.zone_low) / self.midpoint * 100


@dataclass
class FVG:
    """Fair Value Gap — 3-candle imbalance."""
    bar_index: int              # index of the middle (displacement) candle
    direction: str              # "bullish" | "bearish"
    gap_low: float              # lower boundary of the gap
    gap_high: float             # upper boundary of the gap
    displacement_ratio: float   # middle candle body / ATR20

    @property
    def midpoint(self) -> float:
        return (self.gap_low + self.gap_high) / 2

    @property
    def gap_pct(self) -> float:
        return (self.gap_high - self.gap_low) / self.midpoint * 100


@dataclass
class LiquidityLevel:
    """Equal Highs (EQH) or Equal Lows (EQL) — buy/sell-side liquidity pool."""
    price: float
    kind: str               # "eqh" (buy-side) | "eql" (sell-side)
    bar_indices: list[int]  # bars forming the equal level
    swept: bool = False     # True if a wick has traded through it
    sweep_bar: int = -1     # bar index of the sweep candle (if swept)


# ── Confluence ────────────────────────────────────────────────────────────────

@dataclass
class SRLevel:
    """A single classical support/resistance level."""
    price: float
    label: str      # e.g. "PDH", "Swing High", "EMA200", "Round $150"
    weight: float   # contribution to confluence score (PDH/PDL=1.0, Swing=0.75, …)
    source: str     # "pivot" | "prior_day" | "prior_week" | "round" | "ema"


@dataclass
class VolumeProfile:
    """Fixed-Range Volume Profile computed from OHLCV candles."""
    poc: float                          # Point of Control
    vah: float                          # Value Area High
    val: float                          # Value Area Low
    hvn: list[float] = field(default_factory=list)   # High Volume Nodes
    lvn: list[float] = field(default_factory=list)   # Low Volume Nodes
    bins: int = 50
    lookback: int = 60


class ConfluenceReport(BaseModel):
    """Confluence scoring result for a single ICT zone."""
    score: float = 0.0              # 0–10 total
    sr_hits: list[str] = Field(default_factory=list)
    vp_hits: list[str] = Field(default_factory=list)
    poc: Optional[float] = None
    vah: Optional[float] = None
    val: Optional[float] = None
    label: str = "low"              # "high" | "medium" | "low"

    def to_summary_lines(self) -> list[str]:
        lines = []
        lines.extend(self.sr_hits)
        lines.extend(self.vp_hits)
        return lines


# ── Final TradeSetup ──────────────────────────────────────────────────────────

class TradeSetup(BaseModel):
    """A fully-formed ICT trade setup with bias + confluence scoring."""

    # Identification
    ticker: str
    category: str                   # watchlist category
    mode: str                       # "swing" | "intraday"
    setup_name: str                 # "OB Retracement" | "Liquidity Sweep + FVG" | ...
    setup_type: str                 # "continuation" | "reversal" | "aggressive"
    direction: str                  # "long" | "short"

    # Bias context
    bias: BiasResult

    # Confluence
    confluence: ConfluenceReport

    # Trade levels
    entry_low: float                # entry zone low
    entry_high: float               # entry zone high
    stop_loss: float
    tp1: float
    tp2: Optional[float] = None
    rr_ratio: float                 # (tp1 - entry_mid) / abs(entry_mid - stop_loss)
    invalidation_note: str = ""     # "Invalid if price closes below X"

    # Metadata
    fired_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ltf_tf: str = ""
    htf_tf: str = ""

    @property
    def entry_mid(self) -> float:
        return (self.entry_low + self.entry_high) / 2

    def to_dedup_key(self) -> str:
        """Unique key for 24-hour deduplication."""
        return f"{self.ticker}:{self.setup_name}:{self.direction}"

    def to_telegram_text(self) -> str:
        """Format as a Telegram Markdown alert message."""
        dir_emoji = "📈" if self.direction == "long" else "📉"
        dir_label = self.direction.upper()
        conf_emoji = {"high": "🟢", "medium": "🟡", "low": "🟠"}.get(self.confluence.label, "⚪")
        type_tag = {
            "continuation": "",
            "reversal":     " \\[REVERSAL\\]",
            "aggressive":   " \\[AGGRESSIVE\\]",
        }.get(self.setup_type, "")

        entry_str = f"`{self.entry_low:,.4f}` – `{self.entry_high:,.4f}`"
        sl_pct = abs(self.entry_mid - self.stop_loss) / self.entry_mid * 100
        tp1_pct = abs(self.tp1 - self.entry_mid) / self.entry_mid * 100

        lines = [
            f"🎯 *ICT SETUP DETECTED{type_tag}*",
            "━━━━━━━━━━━━━━━━━━━━",
            f"{dir_emoji} *{self.ticker}* — {self.setup_name} \\[{self.mode.upper()}\\]",
            f"Direction: *{dir_label}*  |  {conf_emoji} Confidence: *{self.confluence.label.upper()}* "
            f"({self.confluence.score:.1f}/10)",
            "",
            f"🧭 *Bias* ({self.bias.htf_tf.upper()} RSI: `{self.bias.rsi_value:.1f}`):",
            f"  Regime: {self.bias.regime.title()}  |  Hidden Div: {self.bias.hidden_div.title()}",
            f"  _{self.bias.bias_notes}_",
            "",
            f"📊 *Entry Zone:*  {entry_str}",
            f"🛑 *Stop Loss:*   `{self.stop_loss:,.4f}`  (-{sl_pct:.2f}%)",
            f"🎯 *TP1:*         `{self.tp1:,.4f}`  (+{tp1_pct:.2f}%)  R:R = `{self.rr_ratio:.1f}×`",
        ]
        if self.tp2:
            tp2_pct = abs(self.tp2 - self.entry_mid) / self.entry_mid * 100
            lines.append(f"🎯 *TP2:*         `{self.tp2:,.4f}`  (+{tp2_pct:.2f}%)")
        if self.invalidation_note:
            lines.append(f"⚠️  _Invalid if: {self.invalidation_note}_")

        conf_lines = self.confluence.to_summary_lines()
        if conf_lines:
            lines += ["", f"🔗 *Confluence ({self.confluence.score:.1f}/10):*"]
            lines += [f"  • {c}" for c in conf_lines]

        lines.append("━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    def to_db_json(self) -> str:
        """Serialise to JSON string for storage in SQLite TEXT column."""
        return self.model_dump_json()

    @classmethod
    def from_db_json(cls, raw: str) -> "TradeSetup":
        return cls.model_validate(json.loads(raw))
