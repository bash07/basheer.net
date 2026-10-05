"""
Shared pytest fixtures for the automation platform test suite.

Sets required environment variables BEFORE any app imports so config.py
initialises correctly without needing a real .env file.
"""
import os
import tempfile

# ── Stub environment — must happen before any app module is imported ──────────
# These satisfy config.py's __init__ without hitting real APIs or Telegram.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0:test_token_stub")
os.environ.setdefault("TELEGRAM_CHAT_ID",   "123456789")
os.environ.setdefault("GEMINI_API_KEY",     "test-gemini-key")
os.environ.setdefault("LLM_PROVIDER",       "gemini")
os.environ.setdefault("LLM_MODEL",          "gemini-2.0-flash")

import pytest
import pandas as pd
import numpy as np
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch


# ── Telegram mock helpers ─────────────────────────────────────────────────────

def _make_msg_mock() -> AsyncMock:
    """A fake Telegram Message that supports reply_text, edit_text, delete."""
    msg = AsyncMock()
    msg.edit_text  = AsyncMock()
    msg.delete     = AsyncMock()
    msg.reply_text = AsyncMock(side_effect=lambda *a, **kw: _make_msg_mock())
    msg.reply_photo = AsyncMock()
    return msg


@pytest.fixture
def make_update():
    """
    Factory fixture — builds a mock (update, context) pair for every command test.

    Usage:
        update, context = make_update(args=["AAPL", "--tf", "1wk"])

    update.message.reply_text  — AsyncMock; returns a msg mock with .edit_text + .delete
    update.message.reply_photo — AsyncMock
    context.args               — list[str] you supply; defaults to []

    Pattern for commands that do:
        msg = await update.message.reply_text("loading…")
        await msg.edit_text("result")          ← works because reply_text returns a msg mock
    """
    def _factory(args: list[str] | None = None) -> tuple:
        sent_msg = _make_msg_mock()

        update = MagicMock()
        update.message         = AsyncMock()
        update.message.reply_text  = AsyncMock(return_value=sent_msg)
        update.message.reply_photo = AsyncMock()
        update.effective_chat  = MagicMock()
        update.effective_chat.id = 123456789

        context        = MagicMock()
        context.args   = list(args or [])
        context.bot    = AsyncMock()

        return update, context

    return _factory


@pytest.fixture
def coro_safe_submit():
    """
    Patches modules.blog.commands.queue.submit with a mock that immediately
    closes any coroutine passed to it — preventing 'coroutine never awaited'
    RuntimeWarning on Python 3.13.

    Usage:
        async def test_generate(make_update, coro_safe_submit):
            update, context = make_update(args=["topic"])
            await generate_command(update, context)
            coro_safe_submit.assert_called_once()
    """
    import asyncio

    def _safe(*args, **kwargs):
        for arg in args:
            if asyncio.iscoroutine(arg):
                arg.close()

    with patch("modules.blog.commands.queue.submit", side_effect=_safe) as mock_sub:
        yield mock_sub

# ── OHLCV data fixture ────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def sample_ohlcv() -> pd.DataFrame:
    """
    Synthetic 250-day OHLCV DataFrame — sufficient for EMA200 computation.
    Uses a seeded random walk so tests are fully deterministic.
    """
    np.random.seed(42)
    n = 250
    dates = pd.bdate_range(end=datetime(2025, 1, 1), periods=n)  # business days
    close = 100.0 + np.cumsum(np.random.randn(n) * 0.8)
    close = np.clip(close, 10, None)  # keep prices positive

    df = pd.DataFrame({
        "Open":   close - np.abs(np.random.randn(n) * 0.4),
        "High":   close + np.abs(np.random.randn(n) * 0.8),
        "Low":    close - np.abs(np.random.randn(n) * 0.8),
        "Close":  close,
        "Volume": np.random.randint(1_000_000, 10_000_000, n).astype(float),
    }, index=dates)
    df.index.name = "Date"
    return df


@pytest.fixture(scope="session")
def overbought_ohlcv(sample_ohlcv) -> pd.DataFrame:
    """Variant where the last 20 days spike +30% — should produce RSI > 70."""
    df = sample_ohlcv.copy()
    df.iloc[-20:, df.columns.get_loc("Close")] += 30
    df.iloc[-20:, df.columns.get_loc("High")]  += 32
    df.iloc[-20:, df.columns.get_loc("Low")]   += 28
    return df


@pytest.fixture(scope="session")
def oversold_ohlcv(sample_ohlcv) -> pd.DataFrame:
    """Variant where the last 20 days drop -30% — should produce RSI < 30."""
    df = sample_ohlcv.copy()
    close_col = df.columns.get_loc("Close")
    df.iloc[-20:, close_col] = df.iloc[-20:, close_col] * 0.3
    df.iloc[-20:, df.columns.get_loc("Low")]   *= 0.3
    df.iloc[-20:, df.columns.get_loc("High")]  *= 0.35
    return df


# ── In-memory DB fixture ──────────────────────────────────────────────────────

@pytest.fixture
def clean_db(tmp_path, monkeypatch):
    """
    Isolated SQLite database in a temp directory.
    Patches core.db so module code uses this DB instead of /app/data/.
    Yields a bound Session.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session
    from contextlib import contextmanager

    # Ensure all module models are imported so their tables are registered with Base
    import modules.finance.watchlist.models   # noqa: F401
    import modules.finance.technical.models   # noqa: F401
    import modules.finance.trader.models      # noqa: F401
    import modules.blog.models                # noqa: F401
    import modules.monitor.models             # noqa: F401

    from core.db import Base

    db_path = str(tmp_path / "test_automation.db")
    engine  = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)

    # Patch the session factory used by get_db() throughout the app
    @contextmanager
    def _test_get_db() -> Session:
        db = TestSession()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    monkeypatch.setattr("core.db.get_db", _test_get_db)
    monkeypatch.setattr("core.db.engine",       engine)
    monkeypatch.setattr("core.db.SessionLocal",  TestSession)

    session = TestSession()
    yield session
    session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()  # release all SQLite connections — prevents ResourceWarning
