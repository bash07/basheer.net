"""Tests for Screener Phase 1:
- WatchlistEntry schema extensions (source, cadence, concept_tags, strategy_types, expires_at)
- init_db() auto-migration on watchlist_entries table
- check_all() strategy filtering via allowed_setups
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np

from core.db import init_db, get_db
from modules.finance.watchlist.models import WatchlistEntry
from modules.finance.trader.setups import check_all


def test_watchlist_entry_extended_fields(clean_db):
    """Ensure WatchlistEntry accepts and persists dynamic screener fields."""
    now = datetime.now(timezone.utc)
    entry = WatchlistEntry(
        symbol="TRENT.NS",
        display_name="Trent Limited",
        category="indian_stock",
        source="screen_weekly_ta",
        cadence="weekly",
        concept_tags="concept_a,concept_b",
        strategy_types="OBRetracement,DisplacementFVG",
        expires_at=now + timedelta(days=7),
    )
    clean_db.add(entry)
    clean_db.commit()

    saved = clean_db.query(WatchlistEntry).filter_by(symbol="TRENT.NS").first()
    assert saved is not None
    assert saved.source == "screen_weekly_ta"
    assert saved.cadence == "weekly"
    assert saved.concept_tags == "concept_a,concept_b"
    assert saved.strategy_types == "OBRetracement,DisplacementFVG"
    assert saved.expires_at is not None


def test_watchlist_entry_defaults(clean_db):
    """Ensure manual watchlist entries maintain default values."""
    entry = WatchlistEntry(
        symbol="RELIANCE.NS",
        display_name="Reliance Industries",
        category="indian_stock",
    )
    clean_db.add(entry)
    clean_db.commit()

    saved = clean_db.query(WatchlistEntry).filter_by(symbol="RELIANCE.NS").first()
    assert saved is not None
    assert saved.source == "manual"
    assert saved.cadence == "permanent"
    assert saved.concept_tags is None
    assert saved.strategy_types is None
    assert saved.expires_at is None


def test_init_db_auto_migration():
    """Verify init_db executes cleanly with auto-migration clauses."""
    init_db()


def test_check_all_allowed_setups_filtering():
    """Verify check_all filters strategies when allowed_setups is provided."""
    dates = pd.date_range("2026-01-01", periods=100, freq="1D")
    df = pd.DataFrame(
        {
            "Open": np.linspace(100, 150, 100),
            "High": np.linspace(101, 152, 100),
            "Low": np.linspace(99, 149, 100),
            "Close": np.linspace(100, 151, 100),
            "Volume": np.full(100, 100000.0),
        },
        index=dates,
    )

    mock_strat_ob = MagicMock()
    mock_strat_ob.name = "OB Retracement"
    mock_strat_ob.__class__.__name__ = "OBRetracementStrategy"
    mock_strat_ob.required_indicators = []
    mock_strat_ob.requires_directional_bias = False
    mock_strat_ob.supported_styles = ["swing", "intraday"]
    mock_strat_ob.evaluate.return_value = None

    mock_strat_fvg = MagicMock()
    mock_strat_fvg.name = "Displacement + FVG"
    mock_strat_fvg.__class__.__name__ = "DisplacementFVGStrategy"
    mock_strat_fvg.required_indicators = []
    mock_strat_fvg.requires_directional_bias = False
    mock_strat_fvg.supported_styles = ["swing", "intraday"]
    mock_strat_fvg.evaluate.return_value = None

    mock_strat_wyckoff = MagicMock()
    mock_strat_wyckoff.name = "Wyckoff Accumulation"
    mock_strat_wyckoff.__class__.__name__ = "WyckoffAccumulationStrategy"
    mock_strat_wyckoff.required_indicators = []
    mock_strat_wyckoff.requires_directional_bias = False
    mock_strat_wyckoff.supported_styles = ["swing", "intraday"]
    mock_strat_wyckoff.evaluate.return_value = None

    all_strats = [mock_strat_ob, mock_strat_fvg, mock_strat_wyckoff]
    mock_bias = MagicMock(bias="bullish")

    with patch("modules.finance.trader.setups.DISABLED_SETUPS", set()):
        with patch("modules.finance.trader.strategies.get_all_strategies", return_value=all_strats):
            with patch("modules.finance.trader.indicators.compute_indicators", return_value={"bias": mock_bias}):
                # 1. Allowed is None -> all 3 are evaluated
                check_all("TEST.NS", "indian_stock", df, df, allowed_setups=None)
                assert mock_strat_ob.evaluate.called
                assert mock_strat_fvg.evaluate.called
                assert mock_strat_wyckoff.evaluate.called

                mock_strat_ob.evaluate.reset_mock()
                mock_strat_fvg.evaluate.reset_mock()
                mock_strat_wyckoff.evaluate.reset_mock()

                # 2. Allowed only OBRetracement (class name or normalized format)
                check_all("TEST.NS", "indian_stock", df, df, allowed_setups=["OBRetracement"])
                assert mock_strat_ob.evaluate.called
                assert not mock_strat_fvg.evaluate.called
                assert not mock_strat_wyckoff.evaluate.called

                mock_strat_ob.evaluate.reset_mock()
                mock_strat_fvg.evaluate.reset_mock()
                mock_strat_wyckoff.evaluate.reset_mock()

                # 3. Allowed multiple: Displacement + FVG and Wyckoff
                check_all("TEST.NS", "indian_stock", df, df, allowed_setups=["Displacement + FVG", "wyckoff_accumulation"])
                assert not mock_strat_ob.evaluate.called
                assert mock_strat_fvg.evaluate.called
                assert mock_strat_wyckoff.evaluate.called

                mock_strat_ob.evaluate.reset_mock()
                mock_strat_fvg.evaluate.reset_mock()
                mock_strat_wyckoff.evaluate.reset_mock()

                # 4. Unknown strategy name -> none evaluated, returns empty list
                res = check_all("TEST.NS", "indian_stock", df, df, allowed_setups=["non_existent_strat"])
                assert not mock_strat_ob.evaluate.called
                assert not mock_strat_fvg.evaluate.called
                assert not mock_strat_wyckoff.evaluate.called
                assert res == []


@pytest.mark.asyncio
async def test_trader_scan_all_tickers_passes_strategy_types(clean_db):
    """Verify scan_all_tickers extracts entry.strategy_types and passes to check_all."""
    from modules.finance.trader.jobs import scan_all_tickers

    entry = WatchlistEntry(
        symbol="TRENT.NS",
        display_name="Trent",
        category="indian_stock",
        source="screen_weekly_ta",
        strategy_types="OBRetracement,DisplacementFVG",
    )
    clean_db.add(entry)
    clean_db.commit()

    dates = pd.date_range("2026-01-01", periods=50, freq="1D")
    mock_df = pd.DataFrame(
        {"Open": [100.0] * 50, "High": [102.0] * 50, "Low": [98.0] * 50, "Close": [100.0] * 50, "Volume": [50000.0] * 50},
        index=dates,
    )
    fake_ohlcv = {
        "TRENT.NS:htf": mock_df,
        "TRENT.NS:mtf": mock_df,
        "TRENT.NS:trigger": mock_df,
        "TRENT.NS:ltf": mock_df,
    }

    with patch("modules.finance._shared.market_hours.is_market_open", return_value=True):
        with patch("modules.finance.trader.jobs._fetch_all_ohlcv", return_value=fake_ohlcv):
            with patch("modules.finance.trader.setups.check_all", return_value=[]) as mock_check_all:
                with patch("modules.finance.trader.jobs._update_open_trades_for_ticker"):
                    await scan_all_tickers()
                    assert mock_check_all.called
                    _, kwargs = mock_check_all.call_args
                    assert kwargs.get("allowed_setups") == ["OBRetracement", "DisplacementFVG"]


@pytest.mark.asyncio
async def test_scan_single_respects_strategy_types(clean_db):
    """Verify _scan_single extracts entry.strategy_types and passes to check_all."""
    from modules.finance.trader.commands import _scan_single

    entry = WatchlistEntry(
        symbol="TECHM.NS",
        display_name="Tech Mahindra",
        category="indian_stock",
        source="screen_weekly_ta",
        strategy_types="CreepingDrift,DisplacementFVG",
    )
    clean_db.add(entry)
    clean_db.commit()

    dates = pd.date_range("2026-01-01", periods=50, freq="1D")
    mock_df = pd.DataFrame(
        {"Open": [100.0] * 50, "High": [102.0] * 50, "Low": [98.0] * 50, "Close": [100.0] * 50, "Volume": [50000.0] * 50},
        index=dates,
    )

    mock_provider = MagicMock()
    mock_provider.fetch_ohlcv.return_value = mock_df

    with patch("modules.finance.trader.data.get_provider", return_value=mock_provider):
        with patch("modules.finance.trader.setups.check_all", return_value=[]) as mock_check_all:
            with patch("modules.finance.trader.jobs._update_open_trades_for_ticker"):
                await _scan_single("TECHM.NS")
                assert mock_check_all.called
                _, kwargs = mock_check_all.call_args
                assert kwargs.get("allowed_setups") == ["CreepingDrift", "DisplacementFVG"]

                # Now test force_all_strategies=True -> allowed_setups must be None
                mock_check_all.reset_mock()
                await _scan_single("TECHM.NS", force_all_strategies=True)
                assert mock_check_all.called
                _, kwargs2 = mock_check_all.call_args
                assert kwargs2.get("allowed_setups") is None


@pytest.mark.asyncio
async def test_scan_command_flags(make_update):
    """Verify scan_command parses --force and --all flags correctly."""
    from modules.finance.trader.commands import scan_command

    # 1. /scan --force -> scan_all_tickers(ignore_market_hours=True, force_all_strategies=False)
    up1, ctx1 = make_update(args=["--force"])
    with patch("modules.finance.trader.jobs.scan_all_tickers", return_value={"scanned": 15, "new_setups": 0}) as mock_scan_all:
        await scan_command(up1, ctx1)
        assert mock_scan_all.called
        assert mock_scan_all.call_args[1].get("ignore_market_hours") is True
        assert mock_scan_all.call_args[1].get("force_all_strategies") is False

    # 2. /scan -f -a -> scan_all_tickers(ignore_market_hours=True, force_all_strategies=True)
    up2, ctx2 = make_update(args=["-f", "-a"])
    with patch("modules.finance.trader.jobs.scan_all_tickers", return_value={"scanned": 15, "new_setups": 0}) as mock_scan_all:
        await scan_command(up2, ctx2)
        assert mock_scan_all.called
        assert mock_scan_all.call_args[1].get("ignore_market_hours") is True
        assert mock_scan_all.call_args[1].get("force_all_strategies") is True

    # 3. /scan TECHM.NS --all -> _scan_single("TECHM.NS", force_all_strategies=True)
    up3, ctx3 = make_update(args=["TECHM.NS", "--all"])
    with patch("modules.finance.trader.commands._scan_single", return_value="Result") as mock_scan_single:
        await scan_command(up3, ctx3)
        assert mock_scan_single.called
        assert mock_scan_single.call_args[0][0] == "TECHM.NS"
        assert mock_scan_single.call_args[1].get("force_all_strategies") is True
