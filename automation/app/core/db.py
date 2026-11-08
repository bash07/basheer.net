"""
Shared SQLite database (SQLAlchemy 2.0).
All module models import Base from here and define their tables.
init_db() is called once at startup after all models are imported.
"""
import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from contextlib import contextmanager

from config import config

_data_dir = config.data_dir
try:
    os.makedirs(_data_dir, exist_ok=True)
except Exception:
    _data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
    os.makedirs(_data_dir, exist_ok=True)

_db_path = os.path.join(_data_dir, "automation.db")
engine = create_engine(
    f"sqlite:///{_db_path}",
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable WAL mode and busy timeout for concurrent SQLite access."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA busy_timeout=5000;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Create all tables. Call after importing all module models."""
    Base.metadata.create_all(bind=engine)

    # Auto-migration: Add columns if missing on existing databases.
    # New tables (positions, trades) are created by create_all() above automatically.
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    if "trade_signals" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("trade_signals")]
        with engine.connect() as conn:
            if "human_approved" not in columns:
                conn.execute(text("ALTER TABLE trade_signals ADD COLUMN human_approved BOOLEAN"))
            if "outcome" not in columns:
                conn.execute(text("ALTER TABLE trade_signals ADD COLUMN outcome VARCHAR(10) DEFAULT 'OPEN'"))
            if "outcome_date" not in columns:
                conn.execute(text("ALTER TABLE trade_signals ADD COLUMN outcome_date DATETIME"))
            if "realised_rr" not in columns:
                conn.execute(text("ALTER TABLE trade_signals ADD COLUMN realised_rr FLOAT"))
            conn.commit()

    if "positions" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("positions")]
        with engine.connect() as conn:
            if "tp1_hit" not in columns:
                conn.execute(text("ALTER TABLE positions ADD COLUMN tp1_hit BOOLEAN DEFAULT 0"))
            if "tp2" not in columns:
                conn.execute(text("ALTER TABLE positions ADD COLUMN tp2 FLOAT"))
            conn.commit()

    if "price_alerts" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("price_alerts")]
        with engine.connect() as conn:
            if "alert_type" not in columns:
                conn.execute(text("ALTER TABLE price_alerts ADD COLUMN alert_type VARCHAR(20) DEFAULT 'static'"))
            if "trail_pct" not in columns:
                conn.execute(text("ALTER TABLE price_alerts ADD COLUMN trail_pct FLOAT"))
            if "trail_reference_price" not in columns:
                conn.execute(text("ALTER TABLE price_alerts ADD COLUMN trail_reference_price FLOAT"))
            if "triggered_at" not in columns:
                conn.execute(text("ALTER TABLE price_alerts ADD COLUMN triggered_at DATETIME"))
            conn.commit()

    if "watchlist_entries" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("watchlist_entries")]
        with engine.connect() as conn:
            if "source" not in columns:
                conn.execute(text("ALTER TABLE watchlist_entries ADD COLUMN source VARCHAR(30) DEFAULT 'manual'"))
            if "cadence" not in columns:
                conn.execute(text("ALTER TABLE watchlist_entries ADD COLUMN cadence VARCHAR(20) DEFAULT 'permanent'"))
            if "concept_tags" not in columns:
                conn.execute(text("ALTER TABLE watchlist_entries ADD COLUMN concept_tags VARCHAR(100)"))
            if "strategy_types" not in columns:
                conn.execute(text("ALTER TABLE watchlist_entries ADD COLUMN strategy_types VARCHAR(250)"))
            if "expires_at" not in columns:
                conn.execute(text("ALTER TABLE watchlist_entries ADD COLUMN expires_at DATETIME"))
            conn.commit()

    # Auto-migration: Migrate paper_account.json to account_balances table if present
    inspector = inspect(engine)
    if "account_balances" in inspector.get_table_names():
        with engine.connect() as conn:
            row_count = conn.execute(text("SELECT COUNT(*) FROM account_balances")).scalar()
            if row_count == 0:
                json_path = os.path.join(_data_dir, "paper_account.json")
                if os.path.exists(json_path):
                    try:
                        import json
                        with open(json_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        conn.execute(
                            text(
                                "INSERT INTO account_balances (broker, currency, initial_balance, cash, total_realised_pnl, updated_at) "
                                "VALUES (:broker, :currency, :initial_balance, :cash, :total_realised_pnl, :updated_at)"
                            ),
                            {
                                "broker": "paper",
                                "currency": data.get("currency", "USD"),
                                "initial_balance": float(data.get("initial_balance", 10_000.0)),
                                "cash": float(data.get("cash", 10_000.0)),
                                "total_realised_pnl": float(data.get("total_realised_pnl", 0.0)),
                                "updated_at": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                        conn.commit()
                        bak_path = json_path + ".bak"
                        os.replace(json_path, bak_path)
                    except Exception as e:
                        import logging
                        logging.getLogger("core.db").warning("Failed to auto-migrate paper_account.json: %s", e)


@contextmanager
def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
