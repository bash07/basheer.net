"""Base class and shared context for all trading strategies."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StrategyContext:
    """Pre-computed market data and indicators passed to every strategy.

    Populated once by check_all() and shared across all strategies
    to avoid redundant computation.
    """
    ticker: str
    category: str
    mode: str
    current_price: float

    # Raw price data
    df_htf: object   # pd.DataFrame
    df_ltf: object   # pd.DataFrame

    # Pre-computed context (populated by check_all orchestrator)
    bias: object = None                    # BiasResult
    structure: object = None               # SwingStructure
    bos_list: list = field(default_factory=list)   # list[BOS]
    ob_list: list = field(default_factory=list)    # list[OrderBlock]
    fvg_list: list = field(default_factory=list)   # list[FVG]
    liq_list: list = field(default_factory=list)   # list[LiquidityLevel]
    volume_profile: object = None          # VolumeProfile
    sr_levels: list = field(default_factory=list)  # list[SRLevel]


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies.

    To create a new strategy:
    1. Subclass BaseStrategy in a new .py file in strategies/
    2. Set name, description, setup_type, and priority
    3. Implement evaluate(ctx) -> TradeSetup | None

    The strategy will be auto-discovered and appear in:
    - The live scanner (/scan command)
    - The backtester (--disable flag)
    - The interactive manager (python -m modules.finance.trader.manage)
    """

    #: Human-readable name. Used as the setup_name in signals and the key in config.
    name: str = "Unnamed Strategy"

    #: One-line description shown in the Telegram /help output.
    description: str = ""

    #: "continuation" | "reversal" — used for tagging in the DB.
    setup_type: str = "continuation"

    #: Evaluation order (lower = evaluated first).
    priority: int = 100

    #: If set, override (htf_interval, ltf_interval) for this strategy.
    #: Example: ("4h", "15m") for a shorter-timeframe scalping strategy.
    timeframe_override: tuple[str, str] | None = None

    @abstractmethod
    def evaluate(self, ctx: StrategyContext) -> Optional[object]:
        """Evaluate the strategy against the current market context.

        Args:
            ctx: StrategyContext with pre-computed bias, structure, OBs, FVGs, etc.

        Returns:
            A TradeSetup dataclass if all gates pass, else None.
        """
        ...
