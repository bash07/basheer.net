"""Tests for Phase 3 enhancements:
1. OMS Circuit Breaker (Kill Switch) triggered on consecutive stop-outs and 24h drawdown.
2. Resume command / function resetting circuit breaker halt.
3. Automated Partial Take-Profit (TP1 scale-out 50% & breakeven stop loss adjustment).
4. Watchlist Trailing Price Alerts (drawdown from peak & rally from trough).
"""
import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import select

from modules.finance.trader.models import Position, Trade, TradeSignal
from modules.finance.trader.oms.risk import RiskManager
from modules.finance.trader.oms.engine import ExecutionEngine
from modules.finance.trader.oms.paper_broker import PaperBroker
from modules.finance.watchlist.models import PriceAlert, WatchlistEntry
from modules.finance.watchlist.jobs import check_price_alerts_job


def test_circuit_breaker_consecutive_losses(clean_db):
    """Verify circuit breaker trips after 3 consecutive stop-outs."""
    risk = RiskManager(max_consecutive_losses=3)

    # Insert 3 losing trades
    now = datetime.now(timezone.utc)
    for i in range(3):
        t = Trade(
            ticker=f"TICK{i}",
            setup_name="TestSetup",
            direction="long",
            entry_price=100.0,
            exit_price=90.0,
            qty=1.0,
            realised_pnl=-10.0,
            realised_rr=-1.0,
            outcome="LOSS",
            opened_at=now - timedelta(hours=1),
            closed_at=now - timedelta(minutes=i*10),
            notes="SL hit",
        )
        clean_db.add(t)
    clean_db.commit()

    valid, reason = risk.validate_new_position(
        db=clean_db,
        ticker="AAPL",
        new_risk_dollars=50.0,
        equity=10_000.0,
    )
    assert not valid
    assert "CIRCUIT BREAKER" in reason
    assert risk.circuit_breaker_halted

    # Resume trading resets it
    risk.resume_trading()
    assert not risk.circuit_breaker_halted


def test_circuit_breaker_24h_drawdown(clean_db):
    """Verify circuit breaker trips if trailing 24h loss exceeds 3% equity."""
    risk = RiskManager(max_daily_drawdown_pct=0.03, max_consecutive_losses=10)

    # Insert a single large loss > 3% of 10,000 ($300)
    now = datetime.now(timezone.utc)
    t = Trade(
        ticker="BTC-USD",
        setup_name="TestSetup",
        direction="long",
        entry_price=60000.0,
        exit_price=56000.0,
        qty=0.1,
        realised_pnl=-400.0,  # 4% loss on $10k
        realised_rr=-1.5,
        outcome="LOSS",
        opened_at=now - timedelta(hours=5),
        closed_at=now - timedelta(hours=2),
        notes="SL hit",
    )
    clean_db.add(t)
    clean_db.commit()

    valid, reason = risk.validate_new_position(
        db=clean_db,
        ticker="AAPL",
        new_risk_dollars=50.0,
        equity=10_000.0,
    )
    assert not valid
    assert "24h drawdown" in reason


def test_partial_tp1_and_breakeven_sl(clean_db, sample_ohlcv):
    """Verify touching TP1 scales out 50% and moves SL to entry price."""
    broker = PaperBroker()
    engine = ExecutionEngine(broker=broker)

    # Create an open position
    pos = Position(
        ticker="TEST_PARTIAL",
        direction="long",
        entry_price=100.0,
        stop_loss=90.0,
        take_profit=110.0,
        tp2=120.0,
        tp1_hit=False,
        qty=10.0,
        status="OPEN",
        opened_at=datetime.now(timezone.utc) - timedelta(hours=5),
    )
    clean_db.add(pos)
    clean_db.commit()

    # Create a bar that reaches 112 (exceeding TP1=110)
    import pandas as pd
    bar_df = pd.DataFrame([{
        "Open": 105.0,
        "High": 112.0,
        "Low": 104.0,
        "Close": 111.0,
        "Volume": 1000.0,
    }], index=[datetime.now(timezone.utc)])

    engine.update_open_positions_for_ticker("TEST_PARTIAL", bar_df, clean_db)
    clean_db.refresh(pos)

    # Position should still be OPEN, but scaled out 50% and SL at 100.0
    assert pos.status == "OPEN"
    assert pos.tp1_hit is True
    assert pos.qty == 5.0
    assert pos.stop_loss == 100.0  # moved to breakeven entry price
    assert pos.take_profit == 120.0  # target updated to TP2


@pytest.mark.asyncio
async def test_watchlist_trailing_alerts(clean_db, monkeypatch):
    """Verify trailing price alert triggers on downside pullback from peak."""
    # Add watchlist entry
    entry = WatchlistEntry(symbol="TEST_TRAIL", display_name="Test Trail", category="us_stock")
    clean_db.add(entry)

    # Add trailing -5% alert with peak reference of 100
    alert = PriceAlert(
        symbol="TEST_TRAIL",
        condition="<",
        threshold=0.0,
        alert_type="trailing_pct",
        trail_pct=-5.0,
        trail_reference_price=100.0,
        triggered=False,
    )
    clean_db.add(alert)
    clean_db.commit()

    # Mock batch price fetch to return 94 (a 6% drop from 100)
    async def _mock_fetch(symbols):
        return {"TEST_TRAIL": 94.0}

    monkeypatch.setattr("modules.finance.watchlist.jobs._fetch_prices_batch", _mock_fetch)

    # Mock notify
    notified = []
    async def _mock_notify(msg, *args, **kwargs):
        notified.append(msg)
    monkeypatch.setattr("modules.finance._shared.notify.notify", _mock_notify)

    await check_price_alerts_job()

    clean_db.refresh(alert)
    assert alert.triggered is True
    assert len(notified) == 1
    assert "Trailing Price Alert Triggered" in notified[0]
