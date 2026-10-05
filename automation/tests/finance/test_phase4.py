"""Phase 4 verification tests:
1. Lynch and Buffett strategies logic and registration.
2. Margin trend compression analysis & warning formatting.
3. Multi-timeframe (MTF) trend alignment evaluation.
"""
import pytest
from unittest.mock import MagicMock
import pandas as pd
from datetime import date

from modules.finance.fundamental.fetcher import FundamentalsData, _compute_margin_trends
from modules.finance.fundamental.strategies import get_strategy, LynchStrategy, BuffettStrategy
from modules.finance.fundamental.formatter import format_full_report
from modules.finance.technical.indicators import compute_ta, format_ta_summary


class TestFundamentalStrategies:
    def test_get_strategy_registration(self):
        lynch = get_strategy("lynch")
        buffett = get_strategy("buffett")
        graham = get_strategy("graham")

        assert isinstance(lynch, LynchStrategy)
        assert isinstance(buffett, BuffettStrategy)
        assert lynch.name == "lynch"
        assert buffett.name == "buffett"

    def test_lynch_strategy_passing(self):
        strategy = LynchStrategy()
        data = FundamentalsData(
            symbol="GROW",
            display_name="Growth Corp",
            sector="Technology",
            industry="Software",
            pe_ratio=15.0,
            eps_growth=0.25,      # 25% growth -> PEG = 15 / 25 = 0.6 (< 1.0)
            revenue_growth=0.20,
            debt_to_equity=0.4,
            current_ratio=1.8,
        )
        res = strategy.evaluate(data)
        assert res.method == "lynch"
        assert res.score >= 4
        assert res.pct >= 80.0
        # PEG criterion should pass
        peg_crit = next(c for c in res.criteria if "PEG" in c.name)
        assert peg_crit.passed is True

    def test_lynch_strategy_failing(self):
        strategy = LynchStrategy()
        data = FundamentalsData(
            symbol="OVER",
            display_name="Overvalued Corp",
            pe_ratio=60.0,
            eps_growth=0.05,      # 5% growth -> PEG = 60 / 5 = 12.0 (> 2.0)
            debt_to_equity=2.5,
            current_ratio=0.8,
        )
        res = strategy.evaluate(data)
        assert res.score == 0
        peg_crit = next(c for c in res.criteria if "PEG" in c.name)
        assert peg_crit.passed is False

    def test_buffett_strategy_passing(self):
        strategy = BuffettStrategy()
        data = FundamentalsData(
            symbol="MOAT",
            display_name="Moat Inc",
            roe=0.22,             # 22% ROE (>= 15%)
            profit_margin=0.18,   # 18% Margin (>= 12%)
            debt_to_equity=0.3,   # <= 0.5
            current_ratio=2.0,    # >= 1.5
            eps_growth=0.10,      # >= 5%
            market_cap=50e9,      # >= 10B
            pe_ratio=18.0,
        )
        res = strategy.evaluate(data)
        assert res.method == "buffett"
        assert res.score == 6
        assert res.pct == 100.0

    def test_buffett_strategy_failing(self):
        strategy = BuffettStrategy()
        data = FundamentalsData(
            symbol="WEAK",
            display_name="Weak Margin Inc",
            roe=0.08,             # 8% ROE (< 15%)
            profit_margin=0.04,   # 4% Margin (< 12%)
            debt_to_equity=1.5,
            current_ratio=0.9,
            eps_growth=-0.05,
            pe_ratio=40.0,
            market_cap=500e6,
        )
        res = strategy.evaluate(data)
        assert res.score == 0
        roe_crit = next(c for c in res.criteria if "Return on Equity" in c.name)
        assert roe_crit.passed is False


class TestMarginTrends:
    def test_detects_margin_compression(self):
        # 4 quarters with declining net margins
        mock_ticker = MagicMock()
        mock_ticker.quarterly_income_stmt = pd.DataFrame(
            {
                "Q4": [10.0, 100.0],
                "Q3": [15.0, 100.0],
                "Q2": [20.0, 100.0],
                "Q1": [25.0, 100.0],
            },
            index=["Net Income", "Total Revenue"],
        )
        warning, margins = _compute_margin_trends(mock_ticker)
        assert warning is not None
        assert "margin compression alert" in warning.lower()
        assert len(margins) == 4

    def test_no_warning_when_margins_healthy(self):
        mock_ticker = MagicMock()
        mock_ticker.quarterly_income_stmt = pd.DataFrame(
            {
                "Q4": [25.0, 100.0],
                "Q3": [20.0, 100.0],
                "Q2": [18.0, 100.0],
                "Q1": [15.0, 100.0],
            },
            index=["Net Income", "Total Revenue"],
        )
        warning, margins = _compute_margin_trends(mock_ticker)
        assert warning is None
        assert len(margins) == 4

    def test_format_full_report_displays_margin_warning(self):
        strategy = LynchStrategy()
        data = FundamentalsData(
            symbol="TEST",
            display_name="Test Stock",
            pe_ratio=15.0,
            margin_trend_warning="Margin compression alert: 3 consecutive quarterly drops",
        )
        res = strategy.evaluate(data)
        report = format_full_report(data, res)
        assert "⚠️ *Margin Alert:*" in report
        assert "Margin compression alert" in report


class TestMultiTimeframeAlignment:
    def test_mtf_bullish_alignment(self, sample_ohlcv):
        # Create weekly dataframe with strong bullish trend
        dates = pd.date_range("2024-01-01", periods=60, freq="W-FRI")
        df_weekly = pd.DataFrame(
            {
                "Open": [100 + i * 2 for i in range(60)],
                "High": [105 + i * 2 for i in range(60)],
                "Low": [98 + i * 2 for i in range(60)],
                "Close": [104 + i * 2 for i in range(60)],
                "Volume": [1000000] * 60,
            },
            index=dates,
        )
        res = compute_ta(sample_ohlcv, symbol="BULL", timeframe="1d", df_weekly=df_weekly)
        assert res.mtf_note is not None
        assert "Bullish" in res.mtf_note

    def test_mtf_bearish_conflict_adds_signal_and_formatter(self, sample_ohlcv):
        # Weekly dataframe in strong downtrend
        dates = pd.date_range("2024-01-01", periods=60, freq="W-FRI")
        df_weekly = pd.DataFrame(
            {
                "Open": [500 - i * 5 for i in range(60)],
                "High": [505 - i * 5 for i in range(60)],
                "Low": [490 - i * 5 for i in range(60)],
                "Close": [492 - i * 5 for i in range(60)],
                "Volume": [1000000] * 60,
            },
            index=dates,
        )
        res = compute_ta(sample_ohlcv, symbol="CONFLICT", timeframe="1d", df_weekly=df_weekly)
        assert res.mtf_aligned is False
        assert any("MTF Conflict" in s for s in res.signals)
        formatted = format_ta_summary(res)
        assert "🌐 MTF:" in formatted
        assert "opposes" in formatted or "Conflict" in formatted
