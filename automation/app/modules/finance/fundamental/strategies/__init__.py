from .base import BaseStrategy, StrategyResult, CriterionResult
from .graham import GrahamStrategy
from .lynch import LynchStrategy
from .buffett import BuffettStrategy

STRATEGIES: dict[str, type[BaseStrategy]] = {
    "graham":  GrahamStrategy,
    "lynch":   LynchStrategy,
    "buffett": BuffettStrategy,
}

DEFAULT_METHOD = "graham"


def get_strategy(name: str = DEFAULT_METHOD) -> BaseStrategy:
    cls = STRATEGIES.get(name.lower())
    if not cls:
        raise ValueError(f"Unknown strategy '{name}'. Available: {list(STRATEGIES.keys())}")
    return cls()


__all__ = [
    "BaseStrategy", "StrategyResult", "CriterionResult",
    "GrahamStrategy", "LynchStrategy", "BuffettStrategy",
    "STRATEGIES", "DEFAULT_METHOD", "get_strategy",
]
