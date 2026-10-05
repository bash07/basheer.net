"""Risk Management and Position Sizing engine for the Order Management System (OMS).

Implements:
  - Account equity-based fractional risk sizing (e.g., 1% risk per trade).
  - Stop loss distance to quantity conversion.
  - Exposure caps (max positions, max aggregate risk, max position size).
  - Symbol-specific rounding (crypto vs equities).
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class RiskManager:
    """Manages trading risk, exposure limits, and position sizing."""

    def __init__(
        self,
        risk_per_trade_pct: float = 0.01,    # 1.0% equity risk per trade
        max_open_positions: int = 5,          # Max concurrent open positions
        max_account_risk_pct: float = 0.05,  # Max 5% aggregate open portfolio risk
        max_position_size_pct: float = 0.25, # Max 25% account equity in a single position
        max_daily_drawdown_pct: float = 0.03, # Max 3% realized loss in trailing 24h
        max_consecutive_losses: int = 3,      # Max 3 consecutive stop outs
    ) -> None:
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_open_positions = max_open_positions
        self.max_account_risk_pct = max_account_risk_pct
        self.max_position_size_pct = max_position_size_pct
        self.max_daily_drawdown_pct = max_daily_drawdown_pct
        self.max_consecutive_losses = max_consecutive_losses
        self.circuit_breaker_halted: bool = False
        self.halt_reason: str = ""
        self._load_config_overrides()

    def _load_config_overrides(self) -> None:
        """Load optional risk overrides from trader_config.json."""
        try:
            from ..manage import load_config
            cfg = load_config().get("oms", {})
            if "risk_per_trade_pct" in cfg:
                self.risk_per_trade_pct = float(cfg["risk_per_trade_pct"])
            if "max_open_positions" in cfg:
                self.max_open_positions = int(cfg["max_open_positions"])
            if "max_account_risk_pct" in cfg:
                self.max_account_risk_pct = float(cfg["max_account_risk_pct"])
            if "max_position_size_pct" in cfg:
                self.max_position_size_pct = float(cfg["max_position_size_pct"])
            if "max_daily_drawdown_pct" in cfg:
                self.max_daily_drawdown_pct = float(cfg["max_daily_drawdown_pct"])
            if "max_consecutive_losses" in cfg:
                self.max_consecutive_losses = int(cfg["max_consecutive_losses"])
        except Exception as e:
            logger.debug("[oms.risk] Could not load config overrides: %s", e)

    def calculate_position_size(
        self,
        entry_price: float,
        stop_loss: float,
        equity: float,
        ticker: str,
        category: str = "default",
    ) -> tuple[float, float, str]:
        """Calculate the trade quantity and dollar risk.

        Args:
            entry_price: Planned entry price
            stop_loss: Planned stop loss price
            equity: Current account equity
            ticker: Symbol
            category: Watchlist category (e.g. "crypto", "us_stock")

        Returns:
            (quantity, dollar_risk, note)
        """
        if equity <= 0:
            return 0.0, 0.0, "Account equity is zero or negative"

        per_unit_risk = abs(entry_price - stop_loss)
        if per_unit_risk <= 1e-9:
            return 0.0, 0.0, "Invalid stop loss: distance to entry is zero"

        target_risk_dollars = equity * self.risk_per_trade_pct
        raw_qty = target_risk_dollars / per_unit_risk

        # Guard against exceeding single position capital cap
        max_capital = equity * self.max_position_size_pct
        position_value = raw_qty * entry_price
        capped = False

        if position_value > max_capital and entry_price > 0:
            raw_qty = max_capital / entry_price
            capped = True

        # Rounding according to asset class
        is_crypto = (category == "crypto") or ("-" in ticker) or ("USDT" in ticker)
        if is_crypto:
            qty = round(raw_qty, 4 if entry_price > 10 else 2)
        else:
            # Equities / indices — round to 2 decimal places (allows fractional shares)
            qty = round(raw_qty, 2)
            # NOTE: Never force a minimum of 1 share.  If the risk model says we can
            # only afford 0.2 shares, trading 1 share would multiply the actual dollar
            # risk by 5×, blowing through the risk_per_trade_pct cap.  Reject instead.

        if qty <= 0:
            return 0.0, 0.0, (
                f"Calculated qty is zero (risk=${target_risk_dollars:.2f}, "
                f"unit_risk={per_unit_risk:.4f}). Trade rejected to preserve risk limits."
            )

        actual_risk = round(qty * per_unit_risk, 2)
        note = (
            f"Capped at {self.max_position_size_pct * 100:.0f}% equity"
            if capped
            else f"Sized to {self.risk_per_trade_pct * 100:.1f}% risk (${target_risk_dollars:.2f})"
        )

        return qty, actual_risk, note

    def check_circuit_breaker(self, db, equity: float) -> tuple[bool, str]:
        """Evaluate circuit breaker rules against recent trade ledger.
        
        Rules:
          1. Explicit manual or automated halt.
          2. Trailing 24h realized drawdown exceeds max_daily_drawdown_pct.
          3. Consecutive losing trades exceed max_consecutive_losses.
        """
        if self.circuit_breaker_halted:
            return True, f"Trading halted by Circuit Breaker: {self.halt_reason}"

        if db is None:
            return False, ""

        from datetime import datetime, timezone, timedelta
        from sqlalchemy import select
        from ..models import Trade

        trades = db.execute(
            select(Trade).order_by(Trade.closed_at.desc())
        ).scalars().all()

        if not trades:
            return False, ""

        # Check consecutive losses
        consecutive_losses = 0
        for t in trades:
            if t.outcome == "LOSS":
                consecutive_losses += 1
            elif t.outcome == "WIN":
                break

        if consecutive_losses >= self.max_consecutive_losses:
            self.circuit_breaker_halted = True
            self.halt_reason = f"{consecutive_losses} consecutive stop-outs (limit {self.max_consecutive_losses})"
            logger.warning("[oms.risk] CIRCUIT BREAKER TRIGGERED: %s", self.halt_reason)
            return True, self.halt_reason

        # Check 24h drawdown
        cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        recent_trades = [
            t for t in trades
            if t.closed_at and (
                t.closed_at.replace(tzinfo=timezone.utc) if t.closed_at.tzinfo is None else t.closed_at
            ) >= cutoff_24h
        ]
        realised_24h = sum((t.realised_pnl or 0.0) for t in recent_trades)
        if equity > 0 and realised_24h < 0:
            loss_pct = abs(realised_24h) / equity
            if loss_pct >= self.max_daily_drawdown_pct:
                self.circuit_breaker_halted = True
                self.halt_reason = (
                    f"24h drawdown -{loss_pct*100:.1f}% exceeds limit "
                    f"(-{self.max_daily_drawdown_pct*100:.1f}%, ${abs(realised_24h):,.2f})"
                )
                logger.warning("[oms.risk] CIRCUIT BREAKER TRIGGERED: %s", self.halt_reason)
                return True, self.halt_reason

        return False, ""

    def resume_trading(self) -> None:
        """Reset the circuit breaker halt state."""
        self.circuit_breaker_halted = False
        self.halt_reason = ""
        logger.info("[oms.risk] Circuit breaker reset: trading resumed.")

    def validate_new_position(
        self,
        db,
        ticker: str,
        new_risk_dollars: float,
        equity: float,
    ) -> tuple[bool, str]:
        """Check if opening a new position violates risk policies.

        Returns:
            (is_valid, reason)
        """
        # 0. Check circuit breaker / kill switch
        halted, halt_msg = self.check_circuit_breaker(db, equity)
        if halted:
            return False, f"CIRCUIT BREAKER: {halt_msg}"

        from sqlalchemy import select
        from ..models import Position

        open_positions = db.execute(
            select(Position).where(Position.status == "OPEN")
        ).scalars().all()

        # 1. Check max open positions
        if len(open_positions) >= self.max_open_positions:
            return False, f"Maximum open positions limit ({self.max_open_positions}) reached"

        # 2. Check duplicate position for ticker
        for pos in open_positions:
            if pos.ticker.upper() == ticker.upper():
                return False, f"A position is already open for {ticker} (Position #{pos.id})"

        # 3. Check aggregate account risk
        if equity > 0:
            current_risk = 0.0
            for pos in open_positions:
                unit_risk = abs(pos.entry_price - pos.stop_loss)
                current_risk += unit_risk * (pos.qty or 0.0)

            total_risk = current_risk + new_risk_dollars
            max_allowed = equity * self.max_account_risk_pct
            if total_risk > max_allowed:
                return (
                    False,
                    f"Total portfolio risk (${total_risk:.2f} / {(total_risk/equity)*100:.1f}%) "
                    f"exceeds limit ({self.max_account_risk_pct*100:.1f}%)",
                )

        return True, "OK"
