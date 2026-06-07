"""Strategy registry — add new strategies here, nothing else needs to change."""
from .base import BaseStrategy, StrategyResult, CriterionResult
from .graham import GrahamStrategy

STRATEGIES: dict[str, type[BaseStrategy]] = {
    "graham": GrahamStrategy,
}

DEFAULT_METHOD = "graham"

__all__ = [
    "BaseStrategy", "StrategyResult", "CriterionResult",
    "GrahamStrategy", "STRATEGIES", "DEFAULT_METHOD",
]
