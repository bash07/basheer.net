"""SQLAlchemy models for the finance/trader module.

Tables:
    trade_signals — persisted ICT setups fired by the scanner.
                    Each row stores the full TradeSetup as JSON plus
                    denormalised key fields for fast DB queries.
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


class TradeSignal(Base):
    """One fired ICT setup alert.

    The full TradeSetup Pydantic model is stored as JSON in ``setup_json``
    for completeness. Denormalised columns allow efficient queries for
    deduplication, listing, and filtering without JSON parsing.
    """

    __tablename__ = "trade_signals"

    id:           Mapped[int]      = mapped_column(Integer, primary_key=True)

    # Denormalised fields for fast queries
    ticker:       Mapped[str]      = mapped_column(String(30), index=True)
    category:     Mapped[str]      = mapped_column(String(30))          # watchlist category
    mode:         Mapped[str]      = mapped_column(String(20))          # "swing" | "intraday"
    setup_name:   Mapped[str]      = mapped_column(String(60))
    direction:    Mapped[str]      = mapped_column(String(10))          # "long" | "short"
    setup_type:   Mapped[str]      = mapped_column(String(20))          # "continuation" | ...
    bias:         Mapped[str]      = mapped_column(String(20))          # "strong_bull" | ...
    conf_score:   Mapped[float]    = mapped_column(Float)               # 0–10
    conf_label:   Mapped[str]      = mapped_column(String(10))          # "high" | "medium" | "low"
    entry_mid:    Mapped[float]    = mapped_column(Float)
    stop_loss:    Mapped[float]    = mapped_column(Float)
    tp1:          Mapped[float]    = mapped_column(Float)
    rr_ratio:     Mapped[float]    = mapped_column(Float)

    # Full setup as JSON for complete reconstruction
    setup_json:   Mapped[str]      = mapped_column(Text)

    # Lifecycle
    fired_at:     Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    notified:     Mapped[bool]     = mapped_column(Boolean, default=False)

    def __repr__(self) -> str:
        return (
            f"<TradeSignal id={self.id} {self.ticker} {self.setup_name} "
            f"{self.direction} score={self.conf_score:.1f}>"
        )
