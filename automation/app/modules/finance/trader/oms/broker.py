"""Broker interface abstraction for the Order Management System (OMS).

Defines the Broker ABC and common dataclasses for orders, positions, and accounts.
Allows plugging in PaperBroker (default), BinanceBroker, AlpacaBroker, IBKRBroker, etc.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class BrokerOrder:
    """Standard representation of an order placed with a broker."""

    id: str
    ticker: str
    side: str            # "buy" | "sell"
    order_type: str      # "market" | "limit" | "stop"
    qty: float
    price: Optional[float] = None
    status: str = "PENDING"  # "PENDING" | "FILLED" | "CANCELLED" | "REJECTED"
    filled_price: Optional[float] = None
    filled_at: Optional[datetime] = None
    broker: str = "paper"
    notes: Optional[str] = None


@dataclass
class BrokerPosition:
    """Standard representation of an open broker position."""

    ticker: str
    direction: str       # "long" | "short"
    qty: float
    entry_price: float
    current_price: float
    unrealised_pnl: float
    opened_at: Optional[datetime] = None


@dataclass
class AccountInfo:
    """Account balance and equity summary."""

    balance: float         # Settled cash balance
    equity: float          # Cash + Unrealised P&L
    unrealised_pnl: float  # Current open positions P&L
    currency: str = "USD"
    broker_name: str = "paper"


class Broker(ABC):
    """Abstract base class for trading brokers."""

    name: str = "base"

    @abstractmethod
    def submit_order(
        self,
        ticker: str,
        side: str,
        order_type: str,
        qty: float,
        price: Optional[float] = None,
    ) -> BrokerOrder:
        """Submit an order to the broker.

        Args:
            ticker: Symbol (e.g. "AAPL", "BTC-USD")
            side: "buy" | "sell"
            order_type: "market" | "limit" | "stop"
            qty: Quantity (units / shares / contracts)
            price: Limit or stop price if applicable

        Returns:
            BrokerOrder with status and fill details.
        """
        ...

    @abstractmethod
    def close_position(
        self,
        ticker: str,
        side: str,
        qty: float,
        exit_price: Optional[float] = None,
    ) -> BrokerOrder:
        """Close an existing position.

        Args:
            ticker: Symbol
            side: "sell" (to close long) | "buy" (to close short)
            qty: Quantity to close
            exit_price: Optional market or limit exit price

        Returns:
            BrokerOrder with exit execution details.
        """
        ...

    @abstractmethod
    def get_account_info(
        self,
        open_positions: Optional[list] = None,
        db=None,
    ) -> AccountInfo:
        """Return current account balance and equity."""
        ...

    @abstractmethod
    def get_current_price(self, ticker: str, category: str = "default") -> float:
        """Fetch the current market price for a symbol."""
        ...
