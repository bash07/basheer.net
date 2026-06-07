"""Tests for the fundamental analysis module.

Covers:
  - GrahamStrategy evaluation (all pass, all fail, mixed, N/A handling)
  - StrategyResult properties (pct, passed_criteria, failed_criteria, na_criteria)
  - Formatter: format_full_report, format_screen_row, format_earnings_list, _fmt_mcap
  - analyst.py: _template_verdict fallback
  - /fundamental, /screen, /earnings command handlers (mocked yfinance)
"""
import pytest
from datetime import date, datetime, timezone
from unittest.mock import patch, AsyncMock, MagicMock

from modules.finance.fundamental.fetcher import FundamentalsData
from modules.finance.fundamental.strategies.graham import GrahamStrategy
from modules.finance.fundamental.strategies.base import StrategyResult


# ── Helpers ───────────────────────────────────────────────────────────────────

def _data(**kw) -> FundamentalsData:
    """Build a FundamentalsData with sensible defaults, overridable via kw."""
    defaults = dict(
        symbol="TEST", display_name="Test Corp",
        sector="Technology", industry="Software",
        market_cap=1e10,
        pe_ratio=12.0, pb_ratio=1.2,
        current_ratio=2.5, debt_to_equity=0.3,
        dividend_yield=0.02, eps_growth=0.05,
        revenue_growth=0.10, roe=0.15, profit_margin=0.20,
        next_earnings_date=None,
    )
    defaults.update(kw)
    return FundamentalsData(**defaults)


# ── GrahamStrategy unit tests ─────────────────────────────────────────────────

class TestGrahamStrategy:

    def test_all_pass(self):
        result = GrahamStrategy().evaluate(_data())
        assert result.score == 6
        assert result.total == 6
        assert result.max_score == 6
        assert result.pct == 100.0

    def test_all_fail(self):
        data = _data(
            pe_ratio=20.0, pb_ratio=3.0,
            current_ratio=1.0, debt_to_equity=0.8,
            dividend_yield=0.0, eps_growth=-0.1,
        )
        result = GrahamStrategy().evaluate(data)
        assert result.score == 0
        assert result.total == 6

    def test_partial_pass(self):
        # P/E fails, P/B fails, rest pass
        data = _data(pe_ratio=25.0, pb_ratio=2.0)
        result = GrahamStrategy().evaluate(data)
        assert result.score == 4
        assert result.total == 6

    def test_na_fields_excluded_from_score(self):
        data = _data(pe_ratio=None, pb_ratio=None)
        result = GrahamStrategy().evaluate(data)
        assert result.total == 4        # 2 criteria had no data
        assert result.score == 4        # remaining 4 pass (all defaults pass)
        assert len(result.na_criteria) == 2

    def test_all_na(self):
        data = _data(
            pe_ratio=None, pb_ratio=None,
            current_ratio=None, debt_to_equity=None,
            dividend_yield=None, eps_growth=None,
        )
        result = GrahamStrategy().evaluate(data)
        assert result.score == 0
        assert result.total == 0
        assert result.pct == 0.0

    def test_pe_boundary_exactly_15_fails(self):
        result = GrahamStrategy().evaluate(_data(pe_ratio=15.0))
        pe_crit = next(c for c in result.criteria if c.name == "P/E Ratio")
        assert pe_crit.passed is False   # threshold is < 15, not <= 15

    def test_pe_just_below_15_passes(self):
        result = GrahamStrategy().evaluate(_data(pe_ratio=14.99))
        pe_crit = next(c for c in result.criteria if c.name == "P/E Ratio")
        assert pe_crit.passed is True

    def test_de_boundary(self):
        # D/E is stored as decimal (fetcher divides yfinance % by 100)
        result = GrahamStrategy().evaluate(_data(debt_to_equity=0.5))
        de_crit = next(c for c in result.criteria if c.name == "Debt/Equity")
        assert de_crit.passed is False   # threshold is < 0.5

    def test_criteria_count(self):
        result = GrahamStrategy().evaluate(_data())
        assert len(result.criteria) == 6

    def test_dividend_zero_fails(self):
        data = _data(dividend_yield=0.0)
        result = GrahamStrategy().evaluate(data)
        div = next(c for c in result.criteria if c.name == "Dividend Yield")
        assert div.passed is False

    def test_method_name(self):
        result = GrahamStrategy().evaluate(_data())
        assert result.method == "graham"

    def test_passed_criteria_property(self):
        result = GrahamStrategy().evaluate(_data())
        assert len(result.passed_criteria) == 6

    def test_failed_criteria_property(self):
        data = _data(pe_ratio=30.0)
        result = GrahamStrategy().evaluate(data)
        assert any(c.name == "P/E Ratio" for c in result.failed_criteria)


# ── Formatter tests ───────────────────────────────────────────────────────────

class TestFormatter:

    def test_format_full_report_contains_symbol(self):
        from modules.finance.fundamental.formatter import format_full_report
        data = _data(symbol="AAPL", display_name="Apple Inc.")
        result = GrahamStrategy().evaluate(data)
        result.verdict = "Solid fundamentals."
        text = format_full_report(data, result)
        assert "AAPL" in text
        assert "Apple Inc." in text

    def test_format_full_report_shows_score(self):
        from modules.finance.fundamental.formatter import format_full_report
        data = _data()
        result = GrahamStrategy().evaluate(data)
        result.verdict = ""
        text = format_full_report(data, result)
        assert "6/6" in text

    def test_format_full_report_earnings_date(self):
        from modules.finance.fundamental.formatter import format_full_report
        data = _data(next_earnings_date=date(2026, 6, 1))
        result = GrahamStrategy().evaluate(data)
        result.verdict = ""
        text = format_full_report(data, result)
        assert "Earnings" in text

    def test_format_screen_row_green(self):
        from modules.finance.fundamental.formatter import format_screen_row
        result = GrahamStrategy().evaluate(_data())   # all pass → 100%
        row = format_screen_row("AAPL", result, 1)
        assert "🟢" in row
        assert "AAPL" in row

    def test_format_screen_row_red(self):
        from modules.finance.fundamental.formatter import format_screen_row
        data = _data(pe_ratio=30, pb_ratio=5, current_ratio=0.5,
                     debt_to_equity=1.0, dividend_yield=0.0, eps_growth=-0.1)
        result = GrahamStrategy().evaluate(data)
        row = format_screen_row("TSLA", result, 1)
        assert "🔴" in row

    def test_format_screen_row_none(self):
        from modules.finance.fundamental.formatter import format_screen_row
        row = format_screen_row("FAIL", None, 2)
        assert "no data" in row

    def test_format_earnings_empty(self):
        from modules.finance.fundamental.formatter import format_earnings_list
        text = format_earnings_list([], 7)
        assert "No earnings" in text

    def test_format_earnings_list(self):
        from modules.finance.fundamental.formatter import format_earnings_list
        upcoming = [("AAPL", "Apple Inc.", date(2026, 5, 4), 3)]
        text = format_earnings_list(upcoming, 7)
        assert "AAPL" in text
        assert "Apple Inc." in text

    def test_fmt_mcap_trillions(self):
        from modules.finance.fundamental.formatter import _fmt_mcap
        assert "T" in _fmt_mcap(3e12)

    def test_fmt_mcap_billions(self):
        from modules.finance.fundamental.formatter import _fmt_mcap
        assert "B" in _fmt_mcap(500e9)

    def test_fmt_mcap_none(self):
        from modules.finance.fundamental.formatter import _fmt_mcap
        assert _fmt_mcap(None) == "N/A"


# ── _template_verdict tests ───────────────────────────────────────────────────

class TestTemplateVerdict:

    def _result(self, score, total) -> StrategyResult:
        from modules.finance.fundamental.strategies.base import CriterionResult
        criteria = [
            CriterionResult("P/E", True if i < score else False, "10", "< 15")
            for i in range(total)
        ]
        return StrategyResult(
            method="graham", criteria=criteria,
            score=score, total=total, max_score=total,
        )

    def test_strong_verdict(self):
        from modules.finance.fundamental.analyst import _template_verdict
        r = self._result(5, 6)
        assert "strong" in _template_verdict(r).lower()

    def test_weak_verdict(self):
        from modules.finance.fundamental.analyst import _template_verdict
        r = self._result(1, 6)
        assert "weak" in _template_verdict(r).lower()

    def test_no_data_verdict(self):
        from modules.finance.fundamental.analyst import _template_verdict
        r = StrategyResult(method="graham", score=0, total=0, max_score=6)
        assert "Insufficient" in _template_verdict(r)


# ── Command handler tests ─────────────────────────────────────────────────────

class TestFundamentalCommand:

    async def test_no_args_shows_usage(self, make_update):
        update, context = make_update(args=[])
        from modules.finance.fundamental.commands import fundamental_command
        await fundamental_command(update, context)
        assert "Usage" in update.message.reply_text.call_args[0][0]

    async def test_unknown_method_shows_error(self, make_update):
        update, context = make_update(args=["AAPL", "--method", "dcf"])
        from modules.finance.fundamental.commands import fundamental_command
        await fundamental_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "❌" in text

    async def test_valid_ticker_runs_full_flow(self, make_update):
        data = _data(symbol="AAPL", display_name="Apple Inc.")
        update, context = make_update(args=["AAPL"])

        with patch("modules.finance.fundamental.commands.fetch_fundamentals", return_value=data), \
             patch("modules.finance.fundamental.commands.generate_verdict",
                   new=AsyncMock(return_value="Good fundamentals.")):
            from modules.finance.fundamental.commands import fundamental_command
            await fundamental_command(update, context)

        msg = update.message.reply_text.return_value
        final_text = msg.edit_text.call_args[0][0]
        assert "AAPL" in final_text
        assert "Good fundamentals." in final_text

    async def test_invalid_ticker_shows_error(self, make_update):
        update, context = make_update(args=["FAKEXYZ"])

        with patch("modules.finance.fundamental.commands.fetch_fundamentals",
                   side_effect=ValueError("No data returned.")):
            from modules.finance.fundamental.commands import fundamental_command
            await fundamental_command(update, context)

        text = update.message.reply_text.return_value.edit_text.call_args[0][0]
        assert "❌" in text


class TestScreenCommand:

    async def test_empty_watchlist(self, make_update, clean_db):
        update, context = make_update(args=[])
        from modules.finance.fundamental.commands import screen_command
        await screen_command(update, context)
        assert "No stocks" in update.message.reply_text.call_args[0][0]

    async def test_screen_with_tickers(self, make_update, clean_db):
        from modules.finance.watchlist.models import WatchlistEntry
        clean_db.add(WatchlistEntry(symbol="AAPL", display_name="Apple", category="us_stock"))
        clean_db.commit()

        data = _data(symbol="AAPL")
        update, context = make_update(args=[])

        with patch("modules.finance.fundamental.commands.fetch_fundamentals", return_value=data):
            from modules.finance.fundamental.commands import screen_command
            await screen_command(update, context)

        text = update.message.reply_text.return_value.edit_text.call_args[0][0]
        assert "AAPL" in text


class TestEarningsCommand:

    async def test_invalid_days(self, make_update):
        update, context = make_update(args=["999"])
        from modules.finance.fundamental.commands import earnings_command
        await earnings_command(update, context)
        assert "Usage" in update.message.reply_text.call_args[0][0]

    async def test_empty_watchlist(self, make_update, clean_db):
        update, context = make_update(args=[])
        from modules.finance.fundamental.commands import earnings_command
        await earnings_command(update, context)
        assert "No stocks" in update.message.reply_text.call_args[0][0]

    async def test_shows_upcoming_earnings(self, make_update, clean_db):
        from modules.finance.watchlist.models import WatchlistEntry
        clean_db.add(WatchlistEntry(symbol="AAPL", display_name="Apple", category="us_stock"))
        clean_db.commit()

        today = datetime.now(timezone.utc).date()
        from datetime import timedelta
        data = _data(symbol="AAPL", next_earnings_date=today + timedelta(days=3))
        update, context = make_update(args=["7"])

        with patch("modules.finance.fundamental.commands.fetch_fundamentals", return_value=data):
            from modules.finance.fundamental.commands import earnings_command
            await earnings_command(update, context)

        text = update.message.reply_text.return_value.edit_text.call_args[0][0]
        assert "AAPL" in text

    async def test_no_upcoming_earnings(self, make_update, clean_db):
        from modules.finance.watchlist.models import WatchlistEntry
        from datetime import timedelta
        clean_db.add(WatchlistEntry(symbol="AAPL", display_name="Apple", category="us_stock"))
        clean_db.commit()

        today = datetime.now(timezone.utc).date()
        data = _data(symbol="AAPL", next_earnings_date=today + timedelta(days=30))
        update, context = make_update(args=["7"])

        with patch("modules.finance.fundamental.commands.fetch_fundamentals", return_value=data):
            from modules.finance.fundamental.commands import earnings_command
            await earnings_command(update, context)

        text = update.message.reply_text.return_value.edit_text.call_args[0][0]
        assert "No earnings" in text
