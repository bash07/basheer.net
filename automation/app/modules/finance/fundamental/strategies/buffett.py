"""Warren Buffett Quality Investing strategy.

Screens for economic moats and compounding quality:
  1. Return on Equity (ROE) > 15% (Consistently high return on shareholder capital)
  2. Profit Margin > 12% (Pricing power and defensible competitive moat)
  3. Debt-to-Equity < 0.6 (Conservative leverage)
  4. Current Ratio > 1.5 (Prudent balance sheet liquidity)
  5. Positive Earnings Growth > 5% (Sustainable earnings compounding)
  6. Positive Free Cash Flow / Revenue Yield (Proxy via positive margin & growth)
"""
from .base import BaseStrategy, StrategyResult, CriterionResult


class BuffettStrategy(BaseStrategy):
    name        = "buffett"
    description = "Warren Buffett Quality & Moat strategy"

    ROE_MIN           = 0.15  # 15%
    PROFIT_MARGIN_MIN = 0.12  # 12%
    DE_MAX            = 0.60  # 0.6
    CR_MIN            = 1.50  # 1.5
    EPS_GROWTH_MIN    = 0.05  # 5%
    PE_MAX            = 25.0  # Reasonable valuation for wonderful companies

    def evaluate(self, data) -> StrategyResult:
        roe = data.roe
        pm  = data.profit_margin
        de  = data.debt_to_equity
        cr  = data.current_ratio
        eg  = data.eps_growth
        pe  = data.pe_ratio

        criteria: list[CriterionResult] = [
            self._criterion(
                "Return on Equity", roe, "> 15%",
                (roe > self.ROE_MIN) if roe is not None else None,
                fmt=".1%",
            ),
            self._criterion(
                "Profit Margin", pm, "> 12%",
                (pm > self.PROFIT_MARGIN_MIN) if pm is not None else None,
                fmt=".1%",
            ),
            self._criterion(
                "Debt/Equity", de, f"< {self.DE_MAX}",
                (de < self.DE_MAX) if de is not None else None,
            ),
            self._criterion(
                "Current Ratio", cr, f"> {self.CR_MIN}",
                (cr > self.CR_MIN) if cr is not None else None,
            ),
            self._criterion(
                "EPS Growth", eg, "> 5%",
                (eg > self.EPS_GROWTH_MIN) if eg is not None else None,
                fmt=".1%",
            ),
            self._criterion(
                "P/E Ratio", pe, f"< {self.PE_MAX}",
                (pe < self.PE_MAX) if pe is not None else None,
            ),
        ]

        passed    = sum(1 for c in criteria if c.passed is True)
        with_data = sum(1 for c in criteria if c.passed is not None)

        return StrategyResult(
            method=self.name,
            criteria=criteria,
            score=passed,
            total=with_data,
            max_score=len(criteria),
        )
