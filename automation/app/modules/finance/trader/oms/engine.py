"""Order Management System (OMS) Execution Engine.

Orchestrates:
  - Human gate approval → position creation.
  - Sizing calculation via RiskManager.
  - Order execution via active Broker (PaperBroker by default).
  - Open position lifecycle tracking (TP1 / SL triggers).
  - Manual market close.
  - Recording of immutable Trade records upon position exit.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from ..models import Order, Position, Trade, TradeSignal
from .broker import Broker
from .paper_broker import PaperBroker
from .risk import RiskManager
from .trailing import TrailingStopManager

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """Core Execution & Lifecycle Engine for trading."""

    def __init__(
        self,
        broker: Optional[Broker] = None,
        risk_manager: Optional[RiskManager] = None,
        trailing_stop_manager: Optional[TrailingStopManager] = None,
    ) -> None:
        self.broker = broker or PaperBroker()
        self.risk_manager = risk_manager or RiskManager()
        self.trailing_stop_manager = trailing_stop_manager or TrailingStopManager()

    # ── Signal Approval & Position Opening ────────────────────────────────────

    def open_position_from_signal(
        self,
        signal_id: int,
        db=None,
    ) -> tuple[Optional[Position], str]:
        """Execute a human-approved TradeSignal into a live or paper Position.

        Args:
            signal_id: Primary key of TradeSignal in the database.
            db: Optional active SQLAlchemy session.

        Returns:
            (Position, status_message) or (None, failure_reason)
        """
        from core.db import get_db

        if db is not None:
            return self._open_position_impl(signal_id, db)

        with get_db() as local_db:
            res, msg = self._open_position_impl(signal_id, local_db)
            local_db.commit()
            return res, msg

    def _open_position_impl(
        self, signal_id: int, db
    ) -> tuple[Optional[Position], str]:
        # 1. Fetch signal
        signal = db.execute(
            select(TradeSignal).where(TradeSignal.id == signal_id)
        ).scalar_one_or_none()

        if signal is None:
            return None, f"TradeSignal #{signal_id} not found."

        # Mark human approved
        signal.human_approved = True

        # 2. Check if a position already exists for this signal
        existing = db.execute(
            select(Position).where(
                Position.signal_id == signal_id,
                Position.status == "OPEN",
            )
        ).scalar_one_or_none()
        if existing:
            return existing, f"Position #{existing.id} already open for signal #{signal_id}."

        # 3. Get account equity for sizing
        open_positions = db.execute(
            select(Position).where(Position.status == "OPEN")
        ).scalars().all()
        try:
            account_info = self.broker.get_account_info(open_positions, db=db)
        except TypeError:
            account_info = self.broker.get_account_info(open_positions)
        equity = account_info.equity

        if equity <= 0:
            return None, f"Insufficient account equity (${equity:.2f})."

        # 4. Calculate position sizing
        category = getattr(signal, "category", "default")
        qty, risk_dollars, size_note = self.risk_manager.calculate_position_size(
            entry_price=signal.entry_mid,
            stop_loss=signal.stop_loss,
            equity=equity,
            ticker=signal.ticker,
            category=category,
        )

        if qty <= 0:
            return None, f"Calculated quantity is 0 ({size_note})."

        # 5. Risk policy validation
        valid, reason = self.risk_manager.validate_new_position(
            db=db,
            ticker=signal.ticker,
            new_risk_dollars=risk_dollars,
            equity=equity,
        )
        if not valid:
            return None, f"Risk check rejected: {reason}"

        # 6. Submit entry order to broker
        side = "buy" if signal.direction == "long" else "sell"
        broker_order = self.broker.submit_order(
            ticker=signal.ticker,
            side=side,
            order_type="market",
            qty=qty,
            price=signal.entry_mid,
        )

        if broker_order.status != "FILLED" or broker_order.filled_price is None:
            return None, f"Broker order execution failed: {broker_order.notes or 'Rejected'}"

        now = datetime.now(timezone.utc)

        # 7. Persist Order record
        db_order = Order(
            signal_id=signal.id,
            ticker=signal.ticker,
            side=broker_order.side,
            order_type=broker_order.order_type,
            qty=broker_order.qty,
            price=broker_order.filled_price,
            status=broker_order.status,
            broker=self.broker.name,
            broker_order_id=broker_order.id,
            created_at=now,
            filled_at=broker_order.filled_at or now,
            filled_price=broker_order.filled_price,
        )
        db.add(db_order)
        db.flush()

        # 8. Persist Position record
        # Derive TP2 (1.5x of TP1 distance or from setup if present)
        tp1_dist = abs(signal.tp1 - broker_order.filled_price)
        tp2_val = round(signal.tp1 + (tp1_dist * 0.5 if signal.direction == "long" else -tp1_dist * 0.5), 4)

        position = Position(
            signal_id=signal.id,
            ticker=signal.ticker,
            direction=signal.direction,
            entry_price=broker_order.filled_price,
            stop_loss=signal.stop_loss,
            initial_stop_loss=signal.stop_loss,
            take_profit=signal.tp1,
            tp2=tp2_val,
            tp1_hit=False,
            trailing_stop_active=False,
            peak_price=broker_order.filled_price,
            qty=qty,
            status="OPEN",
            broker=self.broker.name,
            broker_order_id=broker_order.id,
            opened_at=now,
            notes=f"Strategy: {signal.setup_name} | {size_note}",
        )
        db.add(position)
        db.flush()

        # Link order to position
        db_order.position_id = position.id

        logger.info(
            "[oms.engine] Position #%d opened: %s %s %s @ %.4f (Risk: $%.2f)",
            position.id, position.direction.upper(), qty, position.ticker,
            position.entry_price, risk_dollars,
        )

        msg = (
            f"Position #{position.id} opened: {position.direction.upper()} "
            f"{qty:g} {position.ticker} @ {position.entry_price:,.4f}"
        )
        return position, msg

    # ── Position Closing (Manual or Automated) ────────────────────────────────

    def close_position(
        self,
        position_id: int,
        reason: str = "MANUAL",
        exit_price: Optional[float] = None,
        db=None,
    ) -> tuple[Optional[Trade], str]:
        """Close an open position, record exit order, and create immutable Trade record.

        Args:
            position_id: Primary key of Position.
            reason: "MANUAL", "TP_HIT", "SL_HIT", "TIMEOUT", etc.
            exit_price: Optional explicit exit price. If None, queries market price.
            db: Optional active SQLAlchemy session.

        Returns:
            (Trade, status_message) or (None, failure_reason)
        """
        from core.db import get_db

        if db is not None:
            return self._close_position_impl(position_id, reason, exit_price, db)

        with get_db() as local_db:
            res, msg = self._close_position_impl(position_id, reason, exit_price, local_db)
            local_db.commit()
            return res, msg

    def _close_position_impl(
        self,
        position_id: int,
        reason: str,
        exit_price: Optional[float],
        db,
    ) -> tuple[Optional[Trade], str]:
        position = db.execute(
            select(Position).where(Position.id == position_id)
        ).scalar_one_or_none()

        if position is None:
            return None, f"Position #{position_id} not found."
        if position.status != "OPEN":
            return None, f"Position #{position_id} is already {position.status}."

        # Fetch exit price if not provided
        if exit_price is None or exit_price <= 0:
            exit_price = self.broker.get_current_price(position.ticker)
            if exit_price <= 0:
                return None, f"Unable to determine current price for {position.ticker}."

        # Submit close order to broker
        close_side = "sell" if position.direction == "long" else "buy"
        broker_order = self.broker.close_position(
            ticker=position.ticker,
            side=close_side,
            qty=position.qty or 1.0,
            exit_price=exit_price,
        )

        now = datetime.now(timezone.utc)

        # Record exit order in DB
        db_order = Order(
            position_id=position.id,
            signal_id=position.signal_id,
            ticker=position.ticker,
            side=close_side,
            order_type="market",
            qty=position.qty or 1.0,
            price=exit_price,
            status="FILLED",
            broker=self.broker.name,
            broker_order_id=broker_order.id,
            created_at=now,
            filled_at=now,
            filled_price=exit_price,
        )
        db.add(db_order)

        # Calculate PnL and realised R:R
        qty = position.qty or 1.0
        multiplier = 1.0 if position.direction == "long" else -1.0
        pnl = (exit_price - position.entry_price) * qty * multiplier

        unit_risk = abs(position.entry_price - position.stop_loss)
        if unit_risk > 1e-9:
            realised_rr = ((exit_price - position.entry_price) * multiplier) / unit_risk
        else:
            realised_rr = 0.0

        if pnl > 0.01:
            outcome = "WIN"
        elif pnl < -0.01:
            outcome = "LOSS"
        else:
            outcome = "BREAKEVEN"

        # Update paper balance if broker supports record_trade_pnl
        if hasattr(self.broker, "record_trade_pnl"):
            try:
                self.broker.record_trade_pnl(pnl, db=db)
            except TypeError:
                self.broker.record_trade_pnl(pnl)

        # Update position
        position.status = "CLOSED"
        position.closed_at = now
        position.close_price = exit_price
        position.realised_pnl = round(pnl, 2)
        position.realised_rr = round(realised_rr, 2)
        position.outcome = outcome
        position.notes = f"{position.notes or ''} | Closed ({reason}) @ {exit_price:,.4f}"

        # Setup name lookup
        setup_name = "Manual"
        if position.signal_id:
            sig = db.execute(
                select(TradeSignal).where(TradeSignal.id == position.signal_id)
            ).scalar_one_or_none()
            if sig:
                setup_name = sig.setup_name
                sig.outcome = outcome
                sig.outcome_date = now
                sig.realised_rr = round(realised_rr, 2)

        # Insert Trade record (immutable ledger)
        trade = Trade(
            position_id=position.id,
            signal_id=position.signal_id,
            ticker=position.ticker,
            setup_name=setup_name,
            direction=position.direction,
            entry_price=position.entry_price,
            exit_price=exit_price,
            qty=position.qty,
            realised_rr=round(realised_rr, 2),
            realised_pnl=round(pnl, 2),
            outcome=outcome,
            opened_at=position.opened_at,
            closed_at=now,
            broker=position.broker,
            notes=f"Reason: {reason}",
        )
        db.add(trade)
        db.flush()

        logger.info(
            "[oms.engine] Position #%d closed: %s (%+.2fR, %+.2f USD)",
            position.id, outcome, realised_rr, pnl,
        )

        msg = (
            f"Position #{position.id} closed @ {exit_price:,.4f} "
            f"({outcome}: {realised_rr:+.2f}R, ${pnl:+,.2f})"
        )
        return trade, msg

    # ── Live Monitoring (Trigger Evaluation) ──────────────────────────────────

    def update_open_positions_for_ticker(
        self,
        ticker: str,
        ltf_df,
        db,
    ) -> list[Trade]:
        """Evaluate open positions for a ticker against incoming LTF bars.

        Closes position if TP1 or SL price level is triggered.
        Returns list of newly closed Trade objects.
        """
        open_positions = db.execute(
            select(Position).where(
                Position.ticker == ticker,
                Position.status == "OPEN",
            )
        ).scalars().all()

        if not open_positions or ltf_df is None or ltf_df.empty:
            return []

        closed_trades: list[Trade] = []

        for pos in open_positions:
            try:
                # Timezone alignment
                pos_time = pos.opened_at
                if ltf_df.index.tz is not None:
                    pos_time = pos_time.replace(tzinfo=ltf_df.index.tz)
                else:
                    pos_time = pos_time.replace(tzinfo=None)

                future_bars = ltf_df[ltf_df.index >= pos_time]
                if future_bars.empty:
                    # Evaluate on latest bar if no bars strictly after open
                    future_bars = ltf_df.tail(1)

                for _ts, row in future_bars.iterrows():
                    # Dynamic Trailing Stop Loss (+1.5R Chandelier / EMA21 ratchet)
                    if getattr(self, "trailing_stop_manager", None) and self.trailing_stop_manager.enabled:
                        _new_sl, ratcheted, _msg = self.trailing_stop_manager.evaluate_bar(
                            pos=pos,
                            bar=row,
                            df=ltf_df,
                        )
                        if ratcheted:
                            db.flush()

                    hi = float(row["High"])
                    lo = float(row["Low"])

                    tp_hit = False
                    sl_hit = False

                    if pos.direction == "long":
                        if hi >= pos.take_profit:
                            tp_hit = True
                        if lo <= pos.stop_loss:
                            sl_hit = True
                    else:
                        if lo <= pos.take_profit:
                            tp_hit = True
                        if hi >= pos.stop_loss:
                            sl_hit = True

                    if tp_hit and sl_hit:
                        # Ambiguous candle: both TP and SL touched on the same bar.
                        # Conservative (capital-protection) default: assume SL was hit first.
                        # This avoids overstating the win-rate in live tracking.
                        tp_hit = False
                        sl_hit = True

                    if tp_hit:
                        # Check if this is TP1 hit (scale out 50% & move SL to breakeven)
                        if not pos.tp1_hit and pos.tp2:
                            # 1. Partial close 50%
                            close_qty = round((pos.qty or 1.0) * 0.5, 4)
                            rem_qty = round((pos.qty or 1.0) - close_qty, 4)
                            pnl_partial = (pos.take_profit - pos.entry_price) * close_qty * (1.0 if pos.direction == "long" else -1.0)
                            close_side = "sell" if pos.direction == "long" else "buy"
                            if self.broker.name != "paper":
                                try:
                                    self.broker.close_position(
                                        ticker=pos.ticker,
                                        side=close_side,
                                        qty=close_qty,
                                        exit_price=pos.take_profit,
                                    )
                                except Exception as exc:
                                    logger.error("[oms.engine] Failed live partial close for %s: %s", pos.ticker, exc)

                            if hasattr(self.broker, "record_trade_pnl"):
                                try:
                                    self.broker.record_trade_pnl(pnl_partial, db=db)
                                except TypeError:
                                    self.broker.record_trade_pnl(pnl_partial)

                            pos.tp1_hit = True
                            pos.qty = rem_qty
                            # Move SL to entry_price (Risk-Free Breakeven)
                            old_sl = pos.stop_loss
                            pos.stop_loss = pos.entry_price
                            # Next target is TP2
                            pos.take_profit = pos.tp2
                            pos.notes = f"{pos.notes or ''} | TP1 hit @ {pos.entry_price:,.4f}: scaled 50%, SL moved from {old_sl:,.4f} to breakeven {pos.entry_price:,.4f}"
                            logger.info(
                                "[oms.engine] Position #%d TP1 hit! Scaled out 50%%, SL moved to breakeven (%.4f)",
                                pos.id, pos.entry_price,
                            )
                            db.flush()
                            # Continue monitoring remainder of position
                            continue

                        # Full close on final TP (or TP2)
                        trade, _ = self.close_position(
                            position_id=pos.id,
                            reason="TP_HIT",
                            exit_price=pos.take_profit,
                            db=db,
                        )
                        if trade:
                            closed_trades.append(trade)
                        break

                    if sl_hit:
                        close_reason = "TRAILING_SL_HIT" if getattr(pos, "trailing_stop_active", False) else "SL_HIT"
                        trade, _ = self.close_position(
                            position_id=pos.id,
                            reason=close_reason,
                            exit_price=pos.stop_loss,
                            db=db,
                        )
                        if trade:
                            closed_trades.append(trade)
                        break

            except Exception as e:
                logger.warning(
                    "[oms.engine] Failed to evaluate open position #%d for %s: %s",
                    pos.id, ticker, e,
                )

        return closed_trades

    # ── Summary & Queries ─────────────────────────────────────────────────────

    def get_open_positions(self, db) -> list[Position]:
        """Fetch all currently open positions."""
        return db.execute(
            select(Position).where(Position.status == "OPEN").order_by(Position.opened_at.desc())
        ).scalars().all()

    def get_account_summary(self, db) -> dict:
        """Calculate complete account summary metrics."""
        open_pos = self.get_open_positions(db)
        try:
            account_info = self.broker.get_account_info(open_pos, db=db)
        except TypeError:
            account_info = self.broker.get_account_info(open_pos)

        trades = db.execute(select(Trade)).scalars().all()
        wins = sum(1 for t in trades if t.outcome == "WIN")
        losses = sum(1 for t in trades if t.outcome == "LOSS")
        total_closed = wins + losses
        win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0

        rr_sum = sum((t.realised_rr or 0.0) for t in trades if t.outcome == "WIN")
        avg_rr = (rr_sum / wins) if wins > 0 else 0.0
        total_pnl = sum((t.realised_pnl or 0.0) for t in trades)

        return {
            "broker": self.broker.name,
            "balance": account_info.balance,
            "equity": account_info.equity,
            "unrealised_pnl": account_info.unrealised_pnl,
            "currency": account_info.currency,
            "open_positions_count": len(open_pos),
            "max_open_positions": self.risk_manager.max_open_positions,
            "risk_per_trade_pct": self.risk_manager.risk_per_trade_pct,
            "total_trades": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "avg_rr": avg_rr,
            "total_realised_pnl": total_pnl,
        }
