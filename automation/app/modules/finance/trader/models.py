"""SQLAlchemy models for the finance/trader module.

Tables:
    trade_signals — persisted Trade setups fired by the scanner.
    positions     — open trades (OMS: signal → executed position).
    trades        — closed trade history (position → final outcome).
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


class TradeSignal(Base):
    """One fired Trade setup alert.

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
    fired_at:       Mapped[datetime]      = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    notified:       Mapped[bool]          = mapped_column(Boolean, default=False)
    human_approved: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True, default=None)
    # None = awaiting human decision | True = approved | False = dismissed

    # Live Outcome Tracking
    outcome:        Mapped[str]           = mapped_column(String(10), default="OPEN") # "OPEN", "WIN", "LOSS"
    outcome_date:   Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, default=None)
    realised_rr:    Mapped[Optional[float]]    = mapped_column(Float, nullable=True, default=None)

    def __repr__(self) -> str:
        return (
            f"<TradeSignal id={self.id} {self.ticker} {self.setup_name} "
            f"{self.direction} score={self.conf_score:.1f}>"
        )


# ── OMS: Position ─────────────────────────────────────────────────────────────

class Position(Base):
    """An open trade position linked to a TradeSignal.

    Created when a human-approved signal is executed (paper or live).
    Represents a live, in-progress trade with tracked P&L.

    Lifecycle: OPEN → CLOSED (when SL or TP is hit or manually closed).
    """

    __tablename__ = "positions"

    id:          Mapped[int]   = mapped_column(Integer, primary_key=True)
    signal_id:   Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("trade_signals.id"), nullable=True, index=True
    )

    ticker:      Mapped[str]   = mapped_column(String(30), index=True)
    direction:   Mapped[str]   = mapped_column(String(10))   # "long" | "short"
    entry_price: Mapped[float] = mapped_column(Float)
    stop_loss:   Mapped[float] = mapped_column(Float)
    take_profit: Mapped[float] = mapped_column(Float)        # TP1
    tp2:         Mapped[Optional[float]] = mapped_column(Float, nullable=True) # TP2
    tp1_hit:     Mapped[bool]  = mapped_column(Boolean, default=False)
    qty:         Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # units / contracts

    # "OPEN" | "CLOSED" | "CANCELLED"
    status:      Mapped[str]   = mapped_column(String(15), default="OPEN")

    # Source of execution: "paper" | "binance" | "ibkr" | "alpaca" | ...
    broker:      Mapped[str]   = mapped_column(String(30), default="paper")
    # Broker-assigned order ID for reconciliation
    broker_order_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    opened_at:   Mapped[datetime]          = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    closed_at:   Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    close_price: Mapped[Optional[float]]   = mapped_column(Float, nullable=True)
    realised_pnl: Mapped[Optional[float]]  = mapped_column(Float, nullable=True)
    realised_rr:  Mapped[Optional[float]]  = mapped_column(Float, nullable=True)

    # "WIN" | "LOSS" | "BREAKEVEN" | None (while open)
    outcome:     Mapped[Optional[str]]     = mapped_column(String(15), nullable=True)

    notes:       Mapped[Optional[str]]     = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<Position id={self.id} {self.ticker} {self.direction} "
            f"@ {self.entry_price} [{self.status}]>"
        )


# ── OMS: Trade (closed history) ───────────────────────────────────────────────

class Trade(Base):
    """Immutable record of a completed trade (position that has been closed).

    Created when a Position transitions to CLOSED.
    Forms the historical ledger for performance analytics.
    """

    __tablename__ = "trades"

    id:           Mapped[int]   = mapped_column(Integer, primary_key=True)
    position_id:  Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("positions.id"), nullable=True, index=True
    )
    signal_id:    Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("trade_signals.id"), nullable=True
    )

    ticker:       Mapped[str]   = mapped_column(String(30), index=True)
    setup_name:   Mapped[str]   = mapped_column(String(60))
    direction:    Mapped[str]   = mapped_column(String(10))

    entry_price:  Mapped[float] = mapped_column(Float)
    exit_price:   Mapped[float] = mapped_column(Float)
    qty:          Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    realised_rr:  Mapped[float] = mapped_column(Float)    # negative = loss
    realised_pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # "WIN" | "LOSS" | "BREAKEVEN"
    outcome:      Mapped[str]   = mapped_column(String(15))

    opened_at:    Mapped[datetime]  = mapped_column(DateTime)
    closed_at:    Mapped[datetime]  = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    broker:       Mapped[str]   = mapped_column(String(30), default="paper")
    notes:        Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<Trade id={self.id} {self.ticker} {self.outcome} "
            f"RR={self.realised_rr:.2f}>"
        )


# ── OMS: Order (order lifecycle) ──────────────────────────────────────────────

class Order(Base):
    """An execution order submitted to a broker (entry, SL, TP, or market exit).

    Lifecycle: PENDING → FILLED | CANCELLED | REJECTED.
    """

    __tablename__ = "orders"

    id:              Mapped[int]           = mapped_column(Integer, primary_key=True)
    position_id:     Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("positions.id"), nullable=True, index=True
    )
    signal_id:       Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("trade_signals.id"), nullable=True, index=True
    )

    ticker:          Mapped[str]           = mapped_column(String(30), index=True)
    side:            Mapped[str]           = mapped_column(String(10))          # "buy" | "sell"
    order_type:      Mapped[str]           = mapped_column(String(20))          # "market" | "limit" | "stop"
    qty:             Mapped[float]         = mapped_column(Float)
    price:           Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # "PENDING" | "FILLED" | "CANCELLED" | "REJECTED"
    status:          Mapped[str]           = mapped_column(String(20), default="PENDING")

    broker:          Mapped[str]           = mapped_column(String(30), default="paper")
    broker_order_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    created_at:      Mapped[datetime]      = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    filled_at:       Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    filled_price:    Mapped[Optional[float]]    = mapped_column(Float, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<Order id={self.id} {self.ticker} {self.side} {self.qty} "
            f"[{self.status}] @ {self.filled_price or self.price}>"
        )


class AccountBalance(Base):
    """Persistent account balance ledger for brokers (e.g. PaperBroker).
    
    Replaces paper_account.json to guarantee ACID transactional consistency
    with orders and positions within SQLite.
    """

    __tablename__ = "account_balances"

    id:                 Mapped[int]      = mapped_column(Integer, primary_key=True)
    broker:             Mapped[str]      = mapped_column(String(30), unique=True, index=True)
    currency:           Mapped[str]      = mapped_column(String(10), default="USD")
    initial_balance:    Mapped[float]    = mapped_column(Float, default=10_000.0)
    cash:               Mapped[float]    = mapped_column(Float, default=10_000.0)
    total_realised_pnl: Mapped[float]    = mapped_column(Float, default=0.0)
    updated_at:         Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return (
            f"<AccountBalance broker={self.broker} cash={self.cash} "
            f"realised_pnl={self.total_realised_pnl} currency={self.currency}>"
        )


