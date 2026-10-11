"""Unit tests for Dynamic Trailing Stop Loss Manager (+1.5R Trigger & Chandelier Exit)."""
from __future__ import annotations

import pandas as pd
import pytest

from modules.finance.trader.models import Position, TradeSignal
from modules.finance.trader.oms.engine import ExecutionEngine
from modules.finance.trader.oms.paper_broker import PaperBroker
from modules.finance.trader.oms.trailing import TrailingStopManager


class DummyPosition:
    """Mock position object for unit testing trailing stop logic."""

    def __init__(
        self,
        direction: str = "long",
        entry_price: float = 100.0,
        stop_loss: float = 90.0,
        initial_stop_loss: float = 90.0,
        take_profit: float = 130.0,
        trailing_stop_active: bool = False,
        peak_price: float = None,
    ):
        self.id = 1
        self.ticker = "TEST_TRAIL"
        self.direction = direction
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.initial_stop_loss = initial_stop_loss
        self.take_profit = take_profit
        self.trailing_stop_active = trailing_stop_active
        self.peak_price = peak_price or entry_price
        self.qty = 1.0


class TestTrailingStopActivation:
    """Test +1.5R threshold activation rules for Long and Short."""

    def test_long_activation_at_1_5r(self):
        # Entry=100, Initial SL=90 => R=10. +1.5R target = 115.0
        pos = DummyPosition(direction="long", entry_price=100.0, stop_loss=90.0)
        mgr = TrailingStopManager(activation_r=1.5, atr_multiplier=2.0)

        # Bar 1: High=112.0 (below 115.0) => Not active
        sl, ratcheted, reason = mgr.evaluate_bar(pos, {"High": 112.0, "Low": 98.0, "Close": 110.0})
        assert pos.trailing_stop_active is False
        assert ratcheted is False
        assert sl == 90.0

        # Bar 2: High=116.0 (crosses 115.0) => Unlocked!
        sl, ratcheted, reason = mgr.evaluate_bar(pos, {"High": 116.0, "Low": 105.0, "Close": 115.0})
        assert pos.trailing_stop_active is True
        assert pos.peak_price == 116.0
        # Stop loss is ratcheted up (guaranteed >= entry 100.0)
        assert ratcheted is True
        assert sl >= 100.0

    def test_short_activation_at_1_5r(self):
        # Entry=100, Initial SL=110 => R=10. +1.5R target = 85.0
        pos = DummyPosition(direction="short", entry_price=100.0, stop_loss=110.0)
        mgr = TrailingStopManager(activation_r=1.5, atr_multiplier=2.0)

        # Bar 1: Low=88.0 => Not active
        sl, ratcheted, reason = mgr.evaluate_bar(pos, {"High": 102.0, "Low": 88.0, "Close": 90.0})
        assert pos.trailing_stop_active is False
        assert sl == 110.0

        # Bar 2: Low=84.0 (crosses 85.0) => Unlocked!
        sl, ratcheted, reason = mgr.evaluate_bar(pos, {"High": 92.0, "Low": 84.0, "Close": 85.0})
        assert pos.trailing_stop_active is True
        assert pos.peak_price == 84.0
        assert ratcheted is True
        assert sl <= 100.0


class TestChandelierExitRatcheting:
    """Test Chandelier Exit calculation and monotonic ratchet guarantee."""

    def test_long_chandelier_monotonic_ratchet(self):
        pos = DummyPosition(
            direction="long",
            entry_price=100.0,
            stop_loss=100.0,
            initial_stop_loss=90.0,
            trailing_stop_active=True,
            peak_price=115.0,
        )
        # R=10. Fixed fallback ATR will be 5.0. Multiplier=2.0 => distance = 10.0
        mgr = TrailingStopManager(style="chandelier", atr_multiplier=2.0)

        # Bar 1: Peak expands to 125.0 => Candidate = 125 - 10 = 115.0
        sl, ratcheted, msg = mgr.evaluate_bar(pos, {"High": 125.0, "Low": 118.0, "Close": 122.0})
        assert ratcheted is True
        assert sl == 115.0
        assert pos.stop_loss == 115.0
        assert pos.peak_price == 125.0

        # Bar 2: Price pulls back (High=121, Low=116) => Stop loss MUST NOT move down
        sl, ratcheted, msg = mgr.evaluate_bar(pos, {"High": 121.0, "Low": 116.0, "Close": 117.0})
        assert ratcheted is False
        assert sl == 115.0
        assert pos.stop_loss == 115.0
        assert pos.peak_price == 125.0  # Peak stays at highest recorded

        # Bar 3: Peak expands to 135.0 => Candidate = 135 - 10 = 125.0
        sl, ratcheted, msg = mgr.evaluate_bar(pos, {"High": 135.0, "Low": 124.0, "Close": 130.0})
        assert ratcheted is True
        assert sl == 125.0
        assert pos.stop_loss == 125.0

    def test_short_chandelier_monotonic_ratchet(self):
        pos = DummyPosition(
            direction="short",
            entry_price=100.0,
            stop_loss=100.0,
            initial_stop_loss=110.0,
            trailing_stop_active=True,
            peak_price=85.0,
        )
        mgr = TrailingStopManager(style="chandelier", atr_multiplier=2.0)

        # Bar 1: Peak expands down to 75.0 => Candidate = 75 + 10 = 85.0
        sl, ratcheted, msg = mgr.evaluate_bar(pos, {"High": 82.0, "Low": 75.0, "Close": 77.0})
        assert ratcheted is True
        assert sl == 85.0
        assert pos.stop_loss == 85.0

        # Bar 2: Bounce (High=83, Low=78) => Stop loss MUST NOT loosen (move up)
        sl, ratcheted, msg = mgr.evaluate_bar(pos, {"High": 83.0, "Low": 78.0, "Close": 80.0})
        assert ratcheted is False
        assert sl == 85.0


class TestBreakevenFloorCeilingGuarantee:
    """Ensure stop loss is always at least at breakeven once +1.5R is unlocked."""

    def test_wide_atr_does_not_drop_below_entry(self):
        # Even if atr_multiplier * atr would place candidate below entry price,
        # the floor must hold candidate >= entry_price.
        pos = DummyPosition(
            direction="long",
            entry_price=100.0,
            stop_loss=90.0,
            initial_stop_loss=90.0,
        )
        # atr_multiplier=5.0 with atr=5 => distance=25
        mgr = TrailingStopManager(activation_r=1.5, atr_multiplier=5.0)

        # Peak reaches 116.0 (+1.6R) => 116 - 25 = 91 (below entry 100)
        # Must clamp to at least breakeven (100.0)
        sl, ratcheted, msg = mgr.evaluate_bar(pos, {"High": 116.0, "Low": 105.0, "Close": 110.0})
        assert pos.trailing_stop_active is True
        assert sl >= 100.0
        assert pos.stop_loss >= 100.0


class TestExecutionEngineTrailingExitIntegration:
    """Test full integration with ExecutionEngine.update_open_positions_for_ticker."""

    def test_engine_trailing_stop_ratchet_and_exit(self, clean_db):
        engine = ExecutionEngine(broker=PaperBroker())

        # Create signal & position
        sig = TradeSignal(
            ticker="TRAIL_SYM",
            category="crypto",
            mode="swing",
            setup_name="Test Setup",
            direction="long",
            setup_type="continuation",
            bias="strong_bull",
            conf_score=8.5,
            conf_label="high",
            entry_mid=100.0,
            stop_loss=90.0,
            tp1=120.0,
            rr_ratio=2.0,
            setup_json="{}",
        )
        clean_db.add(sig)
        clean_db.commit()

        pos, _ = engine.open_position_from_signal(sig.id, db=clean_db)
        assert pos is not None
        assert pos.initial_stop_loss == 90.0
        assert pos.trailing_stop_active is False

        # Build simulated candles:
        # Candle 1: Crosses +1.5R (High=118.0) -> ratchets SL above 100.0
        # Candle 2: Drops sharply (Low=95.0, High=105.0) -> hits ratcheted SL
        dates = pd.date_range("2026-03-01 10:00", periods=2, freq="1h", tz="UTC")
        df_candles = pd.DataFrame(
            [
                {"Open": 100.0, "High": 118.0, "Low": 99.0, "Close": 115.0, "Volume": 1000},
                {"Open": 115.0, "High": 116.0, "Low": 95.0, "Close": 98.0, "Volume": 2000},
            ],
            index=dates,
        )

        closed_trades = engine.update_open_positions_for_ticker("TRAIL_SYM", df_candles, db=clean_db)

        assert len(closed_trades) == 1
        trade = closed_trades[0]
        assert pos.status == "CLOSED"
        assert pos.trailing_stop_active is True
        assert trade.outcome == "WIN"
        assert trade.realised_pnl > 0
        assert "TRAILING_SL_HIT" in (pos.notes or "")
