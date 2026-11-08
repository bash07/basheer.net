"""Watchlist populator and lifecycle management for screened equities."""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, delete

from modules.finance.watchlist.models import WatchlistEntry
from .schemas import ScreenedCandidate

logger = logging.getLogger(__name__)


def purge_expired_entries(db: Session) -> int:
    """Remove dynamic watchlist entries that have passed their expiration date."""
    now = datetime.now(timezone.utc)
    stmt = select(WatchlistEntry).where(
        WatchlistEntry.expires_at.is_not(None),
        WatchlistEntry.expires_at < now,
    )
    expired = db.execute(stmt).scalars().all()
    count = len(expired)
    if count > 0:
        for entry in expired:
            db.delete(entry)
        db.commit()
        logger.info("[screener.populator] Purged %d expired watchlist entries", count)
    return count


def populate_screened_candidates(
    candidates: list[ScreenedCandidate],
    cadence: str = "weekly",
    db: Optional[Session] = None,
    expires_in_days: Optional[int] = None,
) -> dict[str, int]:
    """Atomically populate or refresh screened candidates into the watchlist.

    Preserves manual permanent watchlist items.
    Replaces existing expired or stale auto-screened candidates of the matching cadence.
    """
    from core.db import get_db

    if expires_in_days is None:
        expires_in_days = 7 if cadence == "weekly" else 30

    source_tag = "screen_weekly_ta" if cadence == "weekly" else "screen_monthly_fa"
    now = datetime.now(timezone.utc)
    expiry_time = now + timedelta(days=expires_in_days)

    def _execute(session: Session) -> dict[str, int]:
        # 1. Load all existing entries
        existing_entries = {
            e.symbol: e for e in session.execute(select(WatchlistEntry)).scalars().all()
        }

        # 2. Clear old auto-populated entries for this cadence to rotate cleanly
        old_dynamic = [
            e for e in existing_entries.values()
            if e.source == source_tag and e.cadence == cadence
        ]
        candidate_symbols = {c.symbol for c in candidates}

        # Any old dynamic entry not in the new top candidates list is removed
        removed_count = 0
        for old in old_dynamic:
            if old.symbol not in candidate_symbols:
                session.delete(old)
                removed_count += 1

        added_count = 0
        updated_count = 0

        # 3. Insert or update candidates
        for c in candidates:
            concept_str = ",".join(c.concept_tags) if c.concept_tags else None
            strat_str = ",".join(c.strategy_types) if c.strategy_types else None

            if c.symbol in existing_entries:
                entry = existing_entries[c.symbol]
                # If it's a manual entry, keep it permanent but update tags/strategies if beneficial
                if entry.source == "manual":
                    logger.debug("[screener.populator] %s is already permanent manual entry", c.symbol)
                else:
                    entry.source = source_tag
                    entry.cadence = cadence
                    entry.concept_tags = concept_str
                    entry.strategy_types = strat_str
                    entry.expires_at = expiry_time
                    updated_count += 1
            else:
                new_entry = WatchlistEntry(
                    symbol=c.symbol,
                    display_name=c.display_name,
                    category=c.category,
                    source=source_tag,
                    cadence=cadence,
                    concept_tags=concept_str,
                    strategy_types=strat_str,
                    expires_at=expiry_time,
                )
                session.add(new_entry)
                added_count += 1

        session.commit()
        logger.info(
            "[screener.populator] %s rotation: %d added, %d updated, %d pruned",
            cadence.title(), added_count, updated_count, removed_count,
        )
        return {"added": added_count, "updated": updated_count, "pruned": removed_count}

    if db is not None:
        return _execute(db)
    with get_db() as session:
        return _execute(session)
