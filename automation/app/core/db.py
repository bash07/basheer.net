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

_db_path = os.path.join(config.data_dir, "automation.db")
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

    # Auto-migration: Add human_approved if missing on existing databases
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    if "trade_signals" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("trade_signals")]
        if "human_approved" not in columns:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE trade_signals ADD COLUMN human_approved BOOLEAN"))
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
