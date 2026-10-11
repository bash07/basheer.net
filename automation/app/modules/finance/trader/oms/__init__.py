"""Order Management System (OMS) package for finance/trader.

Provides:
  - Broker abstraction (PaperBroker by default, extensible to Binance/IBKR/Alpaca)
  - Risk Manager for dynamic account sizing and exposure caps
  - Execution Engine for position lifecycle and trade tracking
"""
from __future__ import annotations

import logging
from typing import Optional

import os
from .binance_broker import BinanceBroker
from .broker import AccountInfo, Broker, BrokerOrder, BrokerPosition
from .engine import ExecutionEngine
from .paper_broker import PaperBroker
from .risk import RiskManager
from .trailing import TrailingStopManager

logger = logging.getLogger(__name__)

_engine: Optional[ExecutionEngine] = None


def create_broker(broker_name: Optional[str] = None, **kwargs) -> Broker:
    """Instantiate and configure a Broker based on name or configuration."""
    if broker_name is None:
        try:
            from ..manage import load_config
            cfg = load_config().get("oms", {})
            broker_name = cfg.get("active_broker") or os.environ.get("TRADER_ACTIVE_BROKER", "paper")
        except Exception:
            broker_name = os.environ.get("TRADER_ACTIVE_BROKER", "paper")

    name = str(broker_name).lower().strip()

    if name in ("binance_testnet", "binance_futures_testnet", "binance_spot_testnet"):
        market_type = "futures" if "futures" in name else kwargs.pop("market_type", None)
        if market_type is None:
            try:
                from ..manage import load_config
                market_type = load_config().get("oms", {}).get("market_type", "spot")
            except Exception:
                market_type = "spot"
        try:
            from ..manage import load_config
            oms_cfg = load_config().get("oms", {})
            api_key = kwargs.pop("api_key", oms_cfg.get("binance_api_key"))
            api_secret = kwargs.pop("api_secret", oms_cfg.get("binance_secret"))
        except Exception:
            api_key = kwargs.pop("api_key", None)
            api_secret = kwargs.pop("api_secret", None)

        return BinanceBroker(
            api_key=api_key,
            api_secret=api_secret,
            testnet=True,
            market_type=market_type,
            **kwargs,
        )

    if name in ("binance", "binance_live", "binance_spot", "binance_futures"):
        market_type = "futures" if "futures" in name else kwargs.pop("market_type", None)
        if market_type is None:
            try:
                from ..manage import load_config
                market_type = load_config().get("oms", {}).get("market_type", "spot")
            except Exception:
                market_type = "spot"
        try:
            from ..manage import load_config
            oms_cfg = load_config().get("oms", {})
            api_key = kwargs.pop("api_key", oms_cfg.get("binance_api_key"))
            api_secret = kwargs.pop("api_secret", oms_cfg.get("binance_secret"))
        except Exception:
            api_key = kwargs.pop("api_key", None)
            api_secret = kwargs.pop("api_secret", None)

        return BinanceBroker(
            api_key=api_key,
            api_secret=api_secret,
            testnet=False,
            market_type=market_type,
            **kwargs,
        )

    return PaperBroker(**kwargs)


def get_engine() -> ExecutionEngine:
    """Return the global ExecutionEngine singleton."""
    global _engine
    if _engine is None:
        broker = create_broker()
        risk_manager = RiskManager()
        _engine = ExecutionEngine(broker=broker, risk_manager=risk_manager)
    return _engine


def get_broker() -> Broker:
    """Return the currently configured active broker."""
    return get_engine().broker


def set_broker(broker: Broker) -> None:
    """Replace the active broker instance in the execution engine."""
    engine = get_engine()
    engine.broker = broker
    logger.info("[oms] Active broker set to %s", broker.name)


def open_position_from_signal(signal_id: int, db=None):
    """Execute a human-approved signal into an open position."""
    return get_engine().open_position_from_signal(signal_id, db=db)


def close_position_manual(position_id: int, exit_price: Optional[float] = None, db=None):
    """Manually close an open position at market price."""
    return get_engine().close_position(
        position_id=position_id,
        reason="MANUAL",
        exit_price=exit_price,
        db=db,
    )


def update_open_positions_for_ticker(ticker: str, ltf_df, db):
    """Evaluate open positions for a ticker against new bars (TP/SL triggers)."""
    return get_engine().update_open_positions_for_ticker(ticker, ltf_df, db)


def get_open_positions(db):
    """Return all open positions."""
    return get_engine().get_open_positions(db)


def get_account_summary(db):
    """Return complete account summary metrics."""
    return get_engine().get_account_summary(db)


def resume_trading():
    """Reset the circuit breaker halt state."""
    get_engine().risk_manager.resume_trading()


def check_circuit_breaker(db, equity: float):
    """Check circuit breaker state."""
    return get_engine().risk_manager.check_circuit_breaker(db, equity)


__all__ = [
    "AccountInfo",
    "BinanceBroker",
    "Broker",
    "BrokerOrder",
    "BrokerPosition",
    "ExecutionEngine",
    "PaperBroker",
    "RiskManager",
    "TrailingStopManager",
    "check_circuit_breaker",
    "close_position_manual",
    "create_broker",
    "get_account_summary",
    "get_broker",
    "get_engine",
    "get_open_positions",
    "open_position_from_signal",
    "resume_trading",
    "set_broker",
    "update_open_positions_for_ticker",
]
