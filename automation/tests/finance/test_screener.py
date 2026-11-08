"""Tests for Indian Stock Screener:
- Universe retrieval & sector grouping
- Technical filters (Concepts A, B, C)
- Sector momentum filter (Concept E)
- Fundamental quality filter (Concept D)
- Watchlist Populator & Expiry management
- Telegram Command Handlers
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np

from modules.finance.watchlist.models import WatchlistEntry
from modules.finance.screener.schemas import ScreenedCandidate, ScreenReport
from modules.finance.screener.universes.nifty100 import (
    get_nifty100_symbols,
    get_sector_map,
    get_stock_info_map,
    StockInfo,
)
from modules.finance.screener.filters.technical import (
    compute_concept_a_relative_strength,
    compute_concept_b_volatility_squeeze,
    compute_concept_c_volume_flow,
)
from modules.finance.screener.filters.sector import (
    compute_sector_momentum,
    evaluate_concept_e_sector_leaders,
)
from modules.finance.screener.filters.fundamental import (
    evaluate_concept_d_fundamental_quality,
)
from modules.finance.screener.populator import (
    populate_screened_candidates,
    purge_expired_entries,
)
from modules.finance.screener.formatter import (
    format_screener_report_markdown,
    format_screener_status_markdown,
)
from modules.finance.screener.commands import (
    screen_run_command,
    screen_status_command,
    screen_purge_command,
)


def _generate_ohlcv(bars: int = 150, trend: str = "bullish", vol_spike: bool = False, squeeze: bool = False):
    dates = pd.date_range("2025-01-01", periods=bars, freq="1D")
    if trend == "bullish":
        base = np.linspace(100, 200, bars)
    elif trend == "bearish":
        base = np.linspace(200, 100, bars)
    else:
        base = np.full(bars, 150.0)

    if squeeze:
        # Tight range in the last 20 bars
        high = base + 0.5
        low = base - 0.5
        close = base + 0.1
    else:
        high = base + 2.0
        low = base - 2.0
        close = base + 0.5

    volume = np.full(bars, 10000.0)
    if vol_spike:
        volume[-1] = 30000.0  # 3x RVOL
        close[-1] = high[-1]   # close at high

    return pd.DataFrame(
        {"Open": base, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=dates,
    )


class TestUniverseAndSectors:
    def test_nifty100_symbols_not_empty(self):
        symbols = get_nifty100_symbols()
        assert len(symbols) >= 80
        assert all(s.endswith(".NS") for s in symbols)
        assert "RELIANCE.NS" in symbols
        assert "TCS.NS" in symbols

    def test_sector_grouping(self):
        sector_map = get_sector_map()
        assert "Banking & Finance" in sector_map
        assert "IT" in sector_map
        assert any(s.symbol == "HDFCBANK.NS" for s in sector_map["Banking & Finance"])
        assert any(s.symbol == "INFY.NS" for s in sector_map["IT"])


class TestConceptFilters:
    def test_concept_a_relative_strength_bullish(self):
        df_stock = _generate_ohlcv(bars=150, trend="bullish")
        df_bench = _generate_ohlcv(bars=150, trend="flat")
        res = compute_concept_a_relative_strength(df_stock, df_benchmark=df_bench)
        assert res is not None
        assert res["concept"] == "concept_a"
        assert res["score"] >= 5.0
        assert "OBRetracement" in res["target_strategies"]

    def test_concept_a_fails_bearish(self):
        df_stock = _generate_ohlcv(bars=150, trend="bearish")
        df_bench = _generate_ohlcv(bars=150, trend="bullish")
        res = compute_concept_a_relative_strength(df_stock, df_benchmark=df_bench)
        assert res is None

    def test_concept_b_volatility_squeeze(self):
        # Create a DataFrame where the last 25 bars have ultra-tight range compared to previous bars
        dates = pd.date_range("2025-01-01", periods=100, freq="1D")
        high = np.concatenate([np.linspace(105, 150, 75), np.full(25, 150.2)])
        low = np.concatenate([np.linspace(95, 140, 75), np.full(25, 149.8)])
        close = np.concatenate([np.linspace(100, 145, 75), np.full(25, 150.0)])
        volume = np.full(100, 10000.0)
        df_squeeze = pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close, "Volume": volume}, index=dates)

        res = compute_concept_b_volatility_squeeze(df_squeeze)
        assert res is not None
        assert res["concept"] == "concept_b"
        assert "WyckoffAccumulation" in res["target_strategies"]

    def test_concept_c_volume_flow_surge(self):
        df_vol = _generate_ohlcv(bars=50, trend="bullish", vol_spike=True)
        res = compute_concept_c_volume_flow(df_vol)
        assert res is not None
        assert res["concept"] == "concept_c"
        assert res["metrics"]["rvol"] >= 2.0
        assert "LiquiditySweepFVG" in res["target_strategies"]

    def test_concept_e_sector_momentum_and_leaders(self):
        stock_it = StockInfo("TCS.NS", "TCS", "IT")
        df_it = _generate_ohlcv(bars=50, trend="bullish")
        ohlcv_map = {"TCS.NS": df_it}

        # Sector ranking
        sectors = compute_sector_momentum(ohlcv_map, lookback_bars=20)
        assert len(sectors) > 0

        # Leader evaluation
        leading = {"IT"}
        leader_res = evaluate_concept_e_sector_leaders(df_it, stock_it, leading)
        assert leader_res is not None
        assert leader_res["concept"] == "concept_e"
        assert "DisplacementFVG" in leader_res["target_strategies"]

    def test_concept_d_fundamental_quality(self):
        from modules.finance.fundamental.fetcher import FundamentalsData

        mock_data = FundamentalsData(
            symbol="TCS.NS",
            display_name="TCS",
            pe_ratio=18.0,
            roe=0.22,
            profit_margin=0.18,
            debt_to_equity=0.30,
            current_ratio=2.2,
            eps_growth=0.15,
            margin_trend_warning=None,
        )

        df_daily = _generate_ohlcv(bars=200, trend="bullish")
        res = evaluate_concept_d_fundamental_quality("TCS.NS", df_daily=df_daily, fundamentals_data=mock_data)
        assert res is not None
        assert res["concept"] == "concept_d"
        assert "EMAPullback" in res["target_strategies"]

    def test_concept_d_rejects_margin_compression(self):
        from modules.finance.fundamental.fetcher import FundamentalsData

        mock_data = FundamentalsData(
            symbol="FAIL.NS",
            display_name="Fail Corp",
            pe_ratio=18.0,
            roe=0.22,
            profit_margin=0.18,
            debt_to_equity=0.30,
            current_ratio=2.2,
            eps_growth=0.15,
            margin_trend_warning="Margin compression detected",
        )
        res = evaluate_concept_d_fundamental_quality("FAIL.NS", fundamentals_data=mock_data)
        assert res is None


class TestPopulatorAndExpiry:
    def test_populate_and_rotation(self, clean_db):
        now = datetime.now(timezone.utc)
        # 1. Existing manual entry
        manual = WatchlistEntry(symbol="MANUAL.NS", display_name="Manual", category="indian_stock", source="manual")
        clean_db.add(manual)
        clean_db.commit()

        # 2. Populate weekly candidates
        candidates = [
            ScreenedCandidate(
                symbol="AUTO1.NS",
                display_name="Auto 1",
                category="indian_stock",
                current_price=500.0,
                score=8.5,
                concept_tags=["concept_a"],
                strategy_types=["OBRetracement"],
                cadence="weekly",
            ),
            ScreenedCandidate(
                symbol="AUTO2.NS",
                display_name="Auto 2",
                category="indian_stock",
                current_price=1200.0,
                score=7.0,
                concept_tags=["concept_b"],
                strategy_types=["WyckoffAccumulation"],
                cadence="weekly",
            ),
        ]

        res = populate_screened_candidates(candidates, cadence="weekly", db=clean_db)
        assert res["added"] == 2

        # Verify manual entry is untouched
        man_entry = clean_db.query(WatchlistEntry).filter_by(symbol="MANUAL.NS").first()
        assert man_entry.source == "manual"
        assert man_entry.expires_at is None

        # Verify dynamic entries
        dyn1 = clean_db.query(WatchlistEntry).filter_by(symbol="AUTO1.NS").first()
        assert dyn1.source == "screen_weekly_ta"
        assert dyn1.cadence == "weekly"
        assert dyn1.strategy_types == "OBRetracement"
        assert dyn1.expires_at is not None

        # 3. Rotate: next week only AUTO1 remains, AUTO3 added, AUTO2 should be pruned
        cand_next_week = [
            ScreenedCandidate(
                symbol="AUTO1.NS",
                display_name="Auto 1",
                category="indian_stock",
                current_price=520.0,
                score=9.0,
                concept_tags=["concept_a"],
                strategy_types=["OBRetracement"],
                cadence="weekly",
            ),
            ScreenedCandidate(
                symbol="AUTO3.NS",
                display_name="Auto 3",
                category="indian_stock",
                current_price=800.0,
                score=6.5,
                concept_tags=["concept_c"],
                strategy_types=["LiquiditySweepFVG"],
                cadence="weekly",
            ),
        ]
        res2 = populate_screened_candidates(cand_next_week, cadence="weekly", db=clean_db)
        assert res2["pruned"] == 1  # AUTO2 pruned
        assert res2["added"] == 1   # AUTO3 added
        assert res2["updated"] == 1 # AUTO1 updated

        assert clean_db.query(WatchlistEntry).filter_by(symbol="AUTO2.NS").first() is None
        assert clean_db.query(WatchlistEntry).filter_by(symbol="AUTO3.NS").first() is not None

    def test_purge_expired_entries(self, clean_db):
        now = datetime.now(timezone.utc)
        # Add expired entry
        expired = WatchlistEntry(
            symbol="EXPIRED.NS",
            display_name="Expired",
            category="indian_stock",
            source="screen_weekly_ta",
            expires_at=now - timedelta(days=1),
        )
        active = WatchlistEntry(
            symbol="ACTIVE.NS",
            display_name="Active",
            category="indian_stock",
            source="screen_weekly_ta",
            expires_at=now + timedelta(days=5),
        )
        clean_db.add_all([expired, active])
        clean_db.commit()

        purged = purge_expired_entries(clean_db)
        assert purged == 1
        assert clean_db.query(WatchlistEntry).filter_by(symbol="EXPIRED.NS").first() is None
        assert clean_db.query(WatchlistEntry).filter_by(symbol="ACTIVE.NS").first() is not None


class TestCommands:
    @pytest.mark.asyncio
    async def test_screen_status_command(self, clean_db, make_update):
        update, context = make_update()
        entry = WatchlistEntry(
            symbol="RELIANCE.NS",
            display_name="Reliance",
            category="indian_stock",
            source="screen_weekly_ta",
            cadence="weekly",
            strategy_types="OBRetracement",
            expires_at=datetime.now(timezone.utc) + timedelta(days=3),
        )
        clean_db.add(entry)
        clean_db.commit()

        await screen_status_command(update, context)
        assert update.message.reply_text.called
        text = update.message.reply_text.call_args[0][0]
        assert "RELIANCE.NS" in text
        assert "OBRetracement" in text

    @pytest.mark.asyncio
    async def test_screen_purge_command(self, clean_db, make_update):
        update, context = make_update(args=["weekly"])
        entry = WatchlistEntry(
            symbol="RELIANCE.NS",
            display_name="Reliance",
            category="indian_stock",
            source="screen_weekly_ta",
            cadence="weekly",
        )
        clean_db.add(entry)
        clean_db.commit()

        await screen_purge_command(update, context)
        assert update.message.reply_text.called
        text = update.message.reply_text.call_args[0][0]
        assert "Purged" in text
        assert clean_db.query(WatchlistEntry).filter_by(symbol="RELIANCE.NS").first() is None

    def test_screener_registered_by_core_loader(self):
        from core.registry import load_modules, get_loaded
        mock_app = MagicMock()
        mock_scheduler = MagicMock()
        mock_filter = MagicMock()

        load_modules(mock_app, mock_scheduler, mock_filter)
        loaded = get_loaded()
        screener = next((m for m in loaded if m.get("name") == "screener"), None)
        assert screener is not None
        assert screener.get("domain") == "finance"
        assert "screen_run" in screener.get("commands", [])
        assert "screen_status" in screener.get("commands", [])
        assert "screen_purge" in screener.get("commands", [])
