"""Peter Lynch GARP (Growth At A Reasonable Price) strategy.

Key criteria inspired by "One Up On Wall Street":
  1. PEG Ratio <= 1.0 (P/E relative to growth)
  2. Debt-to-Equity < 0.8 (Moderate / manageable leverage)
  3. Positive EPS Growth > 10% (Healthy earnings expansion)
  4. Positive Revenue Growth > 5% (Top-line revenue expansion)
  5. P/E Ratio < 30 (Avoid excessive valuation multiples)
  6. Return on Equity (ROE) > 15% (High capital productivity)
"""
from .base import BaseStrategy, StrategyResult, CriterionResult


class LynchStrategy(BaseStrategy):
    name        = "lynch"
    description = "Peter Lynch Growth At A Reasonable Price (GARP) strategy"

    PEG_MAX        = 1.0
    DE_MAX         = 0.8
    EPS_GROWTH_MIN = 0.10  # 10%
    REV_GROWTH_MIN = 0.05  # 5%
    PE_MAX         = 30.0
    ROE_MIN        = 0.15  # 15%

    def evaluate(self, data) -> StrategyResult:
        pe  = data.pe_ratio
        de  = data.debt_to_equity
        eg  = data.eps_growth
        rg  = data.revenue_growth
        roe = data.roe

        # PEG = PE / (EPS Growth % * 100)
        peg = None
        if pe is not None and eg is not None and eg > 0:
            peg = pe / (eg * 100.0)

        criteria: list[CriterionResult] = [
            self._criterion(
                "PEG Ratio", peg, f"<= {self.PEG_MAX}",
                (peg <= self.PEG_MAX) if peg is not None else None,
            ),
            self._criterion(
                "Debt/Equity", de, f"< {self.DE_MAX}",
                (de < self.DE_MAX) if de is not None else None,
            ),
            self._criterion(
                "EPS Growth", eg, "> 10%",
                (eg > self.EPS_GROWTH_MIN) if eg is not None else None,
                fmt=".1%",
            ),
            self._criterion(
                "Revenue Growth", rg, "> 5%",
                (rg > self.REV_GROWTH_MIN) if rg is not None else None,
                fmt=".1%",
            ),
            self._criterion(
                "P/E Ratio", pe, f"< {self.PE_MAX}",
                (pe < self.PE_MAX) if pe is not None else None,
            ),
            self._criterion(
                "Return on Equity", roe, "> 15%",
                (roe > self.ROE_MIN) if roe is not None else None,
                fmt=".1%",
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
