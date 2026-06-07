"""Base types for the pluggable fundamental strategy system."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class CriterionResult:
    name:      str
    passed:    bool | None   # None = data unavailable
    actual:    str           # formatted value, e.g. "12.3"
    threshold: str           # e.g. "< 15"


@dataclass
class StrategyResult:
    method:   str
    criteria: list[CriterionResult] = field(default_factory=list)
    score:    int = 0        # criteria passed
    total:    int = 0        # criteria with data (non-None)
    max_score: int = 0       # total criteria defined
    verdict:  str = ""       # LLM / template plain-English paragraph

    @property
    def passed_criteria(self) -> list[CriterionResult]:
        return [c for c in self.criteria if c.passed is True]

    @property
    def failed_criteria(self) -> list[CriterionResult]:
        return [c for c in self.criteria if c.passed is False]

    @property
    def na_criteria(self) -> list[CriterionResult]:
        return [c for c in self.criteria if c.passed is None]

    @property
    def pct(self) -> float:
        return (self.score / self.total * 100) if self.total else 0.0


class BaseStrategy(ABC):
    name: str = ""
    description: str = ""

    @abstractmethod
    def evaluate(self, data) -> StrategyResult:
        """Score the ticker against this strategy's criteria."""
        ...

    # ── Helper used by subclasses ─────────────────────────────────────────────
    def _criterion(
        self,
        name: str,
        value: float | None,
        threshold: str,
        passes: bool | None,
        fmt: str = ".2f",
    ) -> CriterionResult:
        actual = f"{value:{fmt}}" if value is not None else "N/A"
        return CriterionResult(name=name, passed=passes, actual=actual, threshold=threshold)
