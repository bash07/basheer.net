"""Tests for Phase 1 enhancements:
1. SQLite WAL mode & pragma settings on connection.
2. AccountBalance table persistence and transactional consistency in OMS.
3. Zero-loss migration from paper_account.json to account_balances table.
"""
import json
import os
import tempfile
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from core.db import Base, engine, init_db, get_db
from modules.finance.trader.models import AccountBalance, Position, Order, Trade
from modules.finance.trader.oms.paper_broker import PaperBroker
from modules.finance.trader.oms.engine import ExecutionEngine


def test_sqlite_wal_pragmas():
    """Verify WAL mode and busy_timeout pragmas are configured on engine connections."""
    with engine.connect() as conn:
        journal_mode = conn.execute(text("PRAGMA journal_mode;")).scalar()
        busy_timeout = conn.execute(text("PRAGMA busy_timeout;")).scalar()
        synchronous = conn.execute(text("PRAGMA synchronous;")).scalar()

        # journal_mode in SQLite on disk should be WAL
        assert str(journal_mode).upper() == "WAL"
        # busy_timeout should be 5000 ms
        assert int(busy_timeout) == 5000
        # synchronous should be 1 (NORMAL) or 2 (FULL)
        assert int(synchronous) in (1, 2)


def test_paper_broker_persists_to_db(clean_db):
    """Verify PaperBroker loads from and records trade PnL into the account_balances table."""
    broker = PaperBroker(initial_balance=10_000.0)
    info = broker.get_account_info(db=clean_db)
    assert info.balance == 10_000.0
    assert info.equity == 10_000.0
    assert info.currency == "USD"

    # Check database row
    row = clean_db.execute(
        select(AccountBalance).where(AccountBalance.broker == "paper")
    ).scalar_one_or_none()
    assert row is not None
    assert row.cash == 10_000.0

    # Record trade PnL
    broker.record_trade_pnl(250.50, db=clean_db)
    clean_db.flush()

    updated_row = clean_db.execute(
        select(AccountBalance).where(AccountBalance.broker == "paper")
    ).scalar_one_or_none()
    assert updated_row.cash == 10_250.50
    assert updated_row.total_realised_pnl == 250.50

    # Also test reset
    broker.reset_account(starting_balance=5_000.0, db=clean_db)
    clean_db.flush()
    reset_row = clean_db.execute(
        select(AccountBalance).where(AccountBalance.broker == "paper")
    ).scalar_one_or_none()
    assert reset_row.cash == 5_000.0
    assert reset_row.total_realised_pnl == 0.0


def test_migration_from_paper_account_json(tmp_path, monkeypatch):
    """Test auto-migration of paper_account.json into account_balances on init_db()."""
    test_db_path = str(tmp_path / "test_migration.db")
    test_engine = create_engine(f"sqlite:///{test_db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr("core.db.engine", test_engine)
    monkeypatch.setattr("core.db._data_dir", str(tmp_path))

    # Pre-populate paper_account.json
    json_path = tmp_path / "paper_account.json"
    json_data = {
        "initial_balance": 10000.0,
        "cash": 12450.75,
        "total_realised_pnl": 2450.75,
        "currency": "USD",
        "updated_at": "2026-10-04T12:00:00+00:00",
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f)

    # Run init_db()
    init_db()

    # Verify data in database table
    with test_engine.connect() as conn:
        row = conn.execute(
            text("SELECT broker, cash, total_realised_pnl, currency FROM account_balances WHERE broker='paper'")
        ).fetchone()
        assert row is not None
        assert row[0] == "paper"
        assert row[1] == 12450.75
        assert row[2] == 2450.75
        assert row[3] == "USD"

    # Verify json was renamed to .bak
    assert not os.path.exists(json_path)
    assert os.path.exists(str(json_path) + ".bak")
