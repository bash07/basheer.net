"""Paper trading execution broker for the Order Management System (OMS).

Simulates order execution, tracks cash balance, unrealised and realised P&L,
and persists account state to paper_account.json in the data directory.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from .broker import AccountInfo, Broker, BrokerOrder

logger = logging.getLogger(__name__)

_DEFAULT_STARTING_BALANCE = 10_000.0


def _get_account_file_path() -> str:
    """Return persistent path for paper_account.json."""
    try:
        from config import config
        if config.data_dir and os.path.isdir(config.data_dir):
            return os.path.join(config.data_dir, "paper_account.json")
    except Exception:
        pass
    return os.path.join(os.path.dirname(__file__), ".paper_account.json")


class PaperBroker(Broker):
    """Simulated Paper Broker with persistent account balance."""

    name: str = "paper"

    def __init__(self, initial_balance: float = _DEFAULT_STARTING_BALANCE) -> None:
        self._initial_balance = initial_balance
        self._state_file = _get_account_file_path()
        self._ensure_state()

    # ── State Persistence (DB-backed with JSON fallback) ─────────────────────

    def _ensure_state(self, db=None) -> dict:
        """Ensure account record exists in DB (or state file), returning dict."""
        from ..models import AccountBalance
        from sqlalchemy import select

        if db is not None:
            bal_row = db.execute(
                select(AccountBalance).where(AccountBalance.broker == self.name)
            ).scalar_one_or_none()
            if bal_row is None:
                bal_row = AccountBalance(
                    broker=self.name,
                    currency="USD",
                    initial_balance=self._initial_balance,
                    cash=self._initial_balance,
                    total_realised_pnl=0.0,
                    updated_at=datetime.now(timezone.utc),
                )
                db.add(bal_row)
                db.flush()
            return {
                "initial_balance": bal_row.initial_balance,
                "cash": bal_row.cash,
                "total_realised_pnl": bal_row.total_realised_pnl,
                "currency": bal_row.currency,
                "updated_at": bal_row.updated_at.isoformat() if bal_row.updated_at else datetime.now(timezone.utc).isoformat(),
            }

        try:
            from core.db import get_db
            with get_db() as local_db:
                bal_row = local_db.execute(
                    select(AccountBalance).where(AccountBalance.broker == self.name)
                ).scalar_one_or_none()
                if bal_row is None:
                    bal_row = AccountBalance(
                        broker=self.name,
                        currency="USD",
                        initial_balance=self._initial_balance,
                        cash=self._initial_balance,
                        total_realised_pnl=0.0,
                        updated_at=datetime.now(timezone.utc),
                    )
                    local_db.add(bal_row)
                    local_db.commit()
                return {
                    "initial_balance": bal_row.initial_balance,
                    "cash": bal_row.cash,
                    "total_realised_pnl": bal_row.total_realised_pnl,
                    "currency": bal_row.currency,
                    "updated_at": bal_row.updated_at.isoformat() if bal_row.updated_at else datetime.now(timezone.utc).isoformat(),
                }
        except Exception as e:
            logger.debug("[oms.paper] Falling back to file state: %s", e)
            if not os.path.exists(self._state_file):
                state = {
                    "initial_balance": self._initial_balance,
                    "cash": self._initial_balance,
                    "total_realised_pnl": 0.0,
                    "currency": "USD",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                self._save_state(state)
                return state
            return self._load_file_state()

    def _load_file_state(self) -> dict:
        """Read account state from JSON file."""
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("[oms.paper] Failed to read state file: %s", e)
            return {
                "initial_balance": self._initial_balance,
                "cash": self._initial_balance,
                "total_realised_pnl": 0.0,
                "currency": "USD",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

    def _load_state(self, db=None) -> dict:
        """Load account state from DB (or file fallback)."""
        return self._ensure_state(db=db)

    def _save_state(self, state: dict) -> None:
        """Save account state to disk fallback."""
        try:
            os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error("[oms.paper] Failed to save account state: %s", e)

    def record_trade_pnl(self, pnl: float, db=None) -> None:
        """Update cash and realised P&L when a trade is closed."""
        from ..models import AccountBalance
        from sqlalchemy import select

        if db is not None:
            bal_row = db.execute(
                select(AccountBalance).where(AccountBalance.broker == self.name)
            ).scalar_one_or_none()
            if bal_row:
                bal_row.cash = round(bal_row.cash + pnl, 4)
                bal_row.total_realised_pnl = round(bal_row.total_realised_pnl + pnl, 4)
                bal_row.updated_at = datetime.now(timezone.utc)
                logger.info(
                    "[oms.paper] Trade PnL recorded in DB: %+.2f USD. New cash: %.2f USD",
                    pnl, bal_row.cash,
                )
                return

        try:
            from core.db import get_db
            with get_db() as local_db:
                bal_row = local_db.execute(
                    select(AccountBalance).where(AccountBalance.broker == self.name)
                ).scalar_one_or_none()
                if bal_row:
                    bal_row.cash = round(bal_row.cash + pnl, 4)
                    bal_row.total_realised_pnl = round(bal_row.total_realised_pnl + pnl, 4)
                    bal_row.updated_at = datetime.now(timezone.utc)
                    local_db.commit()
                    logger.info(
                        "[oms.paper] Trade PnL recorded in DB: %+.2f USD. New cash: %.2f USD",
                        pnl, bal_row.cash,
                    )
                    return
        except Exception as e:
            logger.debug("[oms.paper] DB record_trade_pnl failed, falling back to file: %s", e)

        state = self._load_state()
        state["cash"] = round(float(state.get("cash", self._initial_balance)) + pnl, 4)
        state["total_realised_pnl"] = round(
            float(state.get("total_realised_pnl", 0.0)) + pnl, 4
        )
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save_state(state)
        logger.info(
            "[oms.paper] Trade PnL recorded: %+.2f USD. New cash: %.2f USD",
            pnl, state["cash"],
        )

    def reset_account(self, starting_balance: Optional[float] = None, db=None) -> None:
        """Reset paper account balance back to clean starting state."""
        bal = starting_balance or self._initial_balance
        from ..models import AccountBalance
        from sqlalchemy import select

        if db is not None:
            bal_row = db.execute(
                select(AccountBalance).where(AccountBalance.broker == self.name)
            ).scalar_one_or_none()
            if bal_row:
                bal_row.initial_balance = bal
                bal_row.cash = bal
                bal_row.total_realised_pnl = 0.0
                bal_row.updated_at = datetime.now(timezone.utc)
                return

        try:
            from core.db import get_db
            with get_db() as local_db:
                bal_row = local_db.execute(
                    select(AccountBalance).where(AccountBalance.broker == self.name)
                ).scalar_one_or_none()
                if bal_row:
                    bal_row.initial_balance = bal
                    bal_row.cash = bal
                    bal_row.total_realised_pnl = 0.0
                    bal_row.updated_at = datetime.now(timezone.utc)
                    local_db.commit()
                    return
        except Exception as e:
            logger.debug("[oms.paper] DB reset_account failed, falling back to file: %s", e)

        state = {
            "initial_balance": bal,
            "cash": bal,
            "total_realised_pnl": 0.0,
            "currency": "USD",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save_state(state)
        logger.info("[oms.paper] Account reset with %.2f USD", bal)

    # ── Broker API Implementation ─────────────────────────────────────────────

    def submit_order(
        self,
        ticker: str,
        side: str,
        order_type: str,
        qty: float,
        price: Optional[float] = None,
    ) -> BrokerOrder:
        """Simulate immediate market fill for paper trading."""
        fill_price = price
        if fill_price is None or fill_price <= 0:
            fill_price = self.get_current_price(ticker)
            if fill_price <= 0:
                logger.error("[oms.paper] Unable to get price for %s, order rejected", ticker)
                return BrokerOrder(
                    id=f"paper_rej_{uuid.uuid4().hex[:8]}",
                    ticker=ticker,
                    side=side,
                    order_type=order_type,
                    qty=qty,
                    status="REJECTED",
                    broker=self.name,
                    notes="Current market price unavailable",
                )

        order_id = f"paper_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)

        return BrokerOrder(
            id=order_id,
            ticker=ticker,
            side=side,
            order_type=order_type,
            qty=qty,
            price=fill_price,
            status="FILLED",
            filled_price=fill_price,
            filled_at=now,
            broker=self.name,
            notes="Paper market execution simulated",
        )

    def close_position(
        self,
        ticker: str,
        side: str,
        qty: float,
        exit_price: Optional[float] = None,
    ) -> BrokerOrder:
        """Simulate closing a position at market/limit price."""
        fill_price = exit_price
        if fill_price is None or fill_price <= 0:
            fill_price = self.get_current_price(ticker)
            if fill_price <= 0:
                logger.error("[oms.paper] Unable to get exit price for %s", ticker)
                fill_price = 0.0

        order_id = f"paper_close_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)

        return BrokerOrder(
            id=order_id,
            ticker=ticker,
            side=side,
            order_type="market",
            qty=qty,
            price=fill_price,
            status="FILLED" if fill_price > 0 else "REJECTED",
            filled_price=fill_price,
            filled_at=now,
            broker=self.name,
            notes="Paper position close executed",
        )

    def get_account_info(
        self,
        open_positions: Optional[list] = None,
        db=None,
    ) -> AccountInfo:
        """Return current account balance, equity, and unrealised P&L."""
        state = self._load_state(db=db)
        cash = float(state.get("cash", self._initial_balance))

        unrealised_pnl = 0.0
        if open_positions:
            for pos in open_positions:
                try:
                    curr_px = self.get_current_price(pos.ticker)
                    if curr_px > 0:
                        direction = getattr(pos, "direction", "long")
                        entry_px = float(getattr(pos, "entry_price", 0.0))
                        qty = float(getattr(pos, "qty", 0.0) or 0.0)
                        if direction == "long":
                            unrealised_pnl += (curr_px - entry_px) * qty
                        else:
                            unrealised_pnl += (entry_px - curr_px) * qty
                except Exception as e:
                    logger.warning("[oms.paper] Error calc unrealised PnL for %s: %s", getattr(pos, "ticker", ""), e)

        equity = cash + unrealised_pnl
        return AccountInfo(
            balance=round(cash, 2),
            equity=round(equity, 2),
            unrealised_pnl=round(unrealised_pnl, 2),
            currency=state.get("currency", "USD"),
            broker_name=self.name,
        )

    def get_current_price(self, ticker: str, category: str = "default") -> float:
        """Get latest price for ticker via the configured DataProvider."""
        try:
            from ..data import get_provider
            provider = get_provider(category)
            price = provider.get_current_price(ticker)
            if price > 0:
                return price

            # Fallback: fetch 1h OHLCV and take last close
            df = provider.fetch_ohlcv(ticker, "1h", period="5d")
            if df is not None and not df.empty:
                return float(df["Close"].iloc[-1])
        except Exception as e:
            logger.warning("[oms.paper] Failed to get price for %s: %s", ticker, e)
        return 0.0
