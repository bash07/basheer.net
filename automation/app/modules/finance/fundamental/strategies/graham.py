"""Benjamin Graham value investing strategy.

Six classic criteria from "The Intelligent Investor":
  1. P/E < 15
  2. P/B < 1.5
  3. Current Ratio > 2  (financial strength)
  4. Debt/Equity < 0.5  (low leverage)
  5. Dividend Yield > 0% (pays a dividend)
  6. EPS Growth > 0%    (positive earnings momentum; TTM proxy)
"""
from .base import BaseStrategy, StrategyResult, CriterionResult


class GrahamStrategy(BaseStrategy):
    name        = "graham"
    description = "Benjamin Graham value investing (The Intelligent Investor criteria)"

    # Criteria thresholds
    PE_MAX   = 15.0
    PB_MAX   = 1.5
    CR_MIN   = 2.0
    DE_MAX   = 0.5   # decimal form; yfinance reports %, fetcher divides /100
    DIV_MIN  = 0.0   # any positive yield
    EPS_MIN  = 0.0   # positive TTM growth

    def evaluate(self, data) -> StrategyResult:
        pe, pb = data.pe_ratio, data.pb_ratio
        cr, de = data.current_ratio, data.debt_to_equity
        dy, eg = data.dividend_yield, data.eps_growth

        criteria: list[CriterionResult] = [
            self._criterion("P/E Ratio",      pe, f"< {self.PE_MAX}",
                            (pe < self.PE_MAX) if pe is not None else None),
            self._criterion("P/B Ratio",      pb, f"< {self.PB_MAX}",
                            (pb < self.PB_MAX) if pb is not None else None),
            self._criterion("Current Ratio",  cr, f"> {self.CR_MIN}",
                            (cr > self.CR_MIN) if cr is not None else None),
            self._criterion("Debt/Equity",    de, f"< {self.DE_MAX}",
                            (de < self.DE_MAX) if de is not None else None),
            self._criterion("Dividend Yield", dy, "> 0%",
                            (dy > self.DIV_MIN) if dy is not None else None,
                            fmt=".2%"),
            self._criterion("EPS Growth",     eg, "> 0%",
                            (eg > self.EPS_MIN) if eg is not None else None,
                            fmt=".1%"),
        ]

        passed     = sum(1 for c in criteria if c.passed is True)
        with_data  = sum(1 for c in criteria if c.passed is not None)

        return StrategyResult(
            method=self.name,
            criteria=criteria,
            score=passed,
            total=with_data,
            max_score=len(criteria),
        )
