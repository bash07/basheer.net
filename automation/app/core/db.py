"""
Shared SQLite database (SQLAlchemy 2.0).
All module models import Base from here and define their tables.
init_db() is called once at startup after all models are imported.
"""
import os
from sqlalchemy import create_engine
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
