"""Watchlist submodule DB models.

Tables:
  watchlist_entries — categorised ticker registry (shared read by all finance submodules)
  price_alerts      — per-ticker threshold alerts (checked every 5 min by jobs.py)
"""
from datetime import datetime, timezone
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Float, Boolean, DateTime
from core.db import Base


class WatchlistEntry(Base):
    __tablename__ = "watchlist_entries"

    id:           Mapped[int]      = mapped_column(primary_key=True)
    symbol:       Mapped[str]      = mapped_column(String(30), unique=True, index=True)
    display_name: Mapped[str]      = mapped_column(String(200))
    # Category values: us_index | us_stock | indian_index | indian_stock |
    #                  crypto | forex | commodity
    category:     Mapped[str]      = mapped_column(String(30), index=True)
    added_at:     Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class PriceAlert(Base):
    __tablename__ = "price_alerts"

    id:         Mapped[int]      = mapped_column(primary_key=True)
    symbol:     Mapped[str]      = mapped_column(String(30), index=True)
    condition:  Mapped[str]      = mapped_column(String(2))   # ">" or "<"
    threshold:  Mapped[float]    = mapped_column(Float)
    triggered:  Mapped[bool]     = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
