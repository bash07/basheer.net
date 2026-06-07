"""Technical analysis submodule DB models."""
from datetime import datetime, timezone
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Boolean, DateTime, Text
from core.db import Base


class SignalLog(Base):
    """Persistent log of detected TA signals — used to deduplicate proactive alerts."""
    __tablename__ = "signal_log"

    id:           Mapped[int]      = mapped_column(primary_key=True)
    symbol:       Mapped[str]      = mapped_column(String(30), index=True)
    # signal_type values: "ema_crossover" | "rsi_extreme" | "volume_spike"
    signal_type:  Mapped[str]      = mapped_column(String(30))
    description:  Mapped[str]      = mapped_column(Text)
    detected_at:  Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    notified:     Mapped[bool]     = mapped_column(Boolean, default=False)
