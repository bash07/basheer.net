"""Base class and shared context for all trading strategies."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class StrategyContext:
    """Shared market data and pre-computed indicators passed to every strategy.

    Populated once by check_all() using the indicator registry.
    All indicators live in the ``indicators`` dict; named properties provide
    backward-compatible attribute-style access.

    Usage in a strategy::

        def evaluate(self, ctx: StrategyContext) -> TradeSetup | None:
            if ctx.bias.bias == "neutral":
                return None

            # access by property (preferred for built-in indicators)
            atr = ctx.atr_14

            # access by key (required for custom / dynamic indicators)
            my_val = ctx.indicators.get("my_custom_indicator")
    """

    __slots__ = (
        "ticker", "category", "mode", "current_price",
        "df_htf", "df_ltf", "indicators", "_active_bias",
    )

    def __init__(
        self,
        ticker: str,
        category: str,
        mode: str,
        current_price: float,
        df_htf,           # pd.DataFrame
        df_ltf,           # pd.DataFrame
        indicators: dict | None = None,
    ) -> None:
        self.ticker        = ticker
        self.category      = category
        self.mode          = mode
        self.current_price = current_price
        self.df_htf        = df_htf
        self.df_ltf        = df_ltf
        self.indicators    = indicators if indicators is not None else {}
        self._active_bias  = None

    def bind_strategy(self, strategy) -> None:
        """Bind context to a specific strategy before evaluate(). Resolves active bias."""
        bp = getattr(strategy, "bias_provider", "rsi")
        if bp != "rsi" and f"bias_{bp}" in self.indicators:
            self._active_bias = self.indicators[f"bias_{bp}"]
        else:
            self._active_bias = self.indicators.get("bias")

    # ── Convenience properties — read from indicators dict ────────────────────
    # These allow strategies written before the indicator registry to continue
    # working unchanged (ctx.bias, ctx.structure, etc. still work).

    @property
    def bias(self):
        """BiasResult from the HTF bias engine (bound to active strategy, or default)."""
        if self._active_bias is not None:
            return self._active_bias
        return self.indicators.get("bias")

    @property
    def structure(self):
        """SwingStructure (HH/HL/LH/LL labels + trend)."""
        return self.indicators.get("structure")

    @property
    def bos_list(self) -> list:
        """List of BOS / CHoCH events."""
        return self.indicators.get("bos_list") or []

    @property
    def ob_list(self) -> list:
        """List of active Order Blocks."""
        return self.indicators.get("ob_list") or []

    @property
    def fvg_list(self) -> list:
        """List of Fair Value Gaps."""
        return self.indicators.get("fvg_list") or []

    @property
    def liq_list(self) -> list:
        """List of EQH / EQL liquidity levels."""
        return self.indicators.get("liq_list") or []

    @property
    def volume_profile(self):
        """VolumeProfile (POC, VAH, VAL, HVN, LVN)."""
        return self.indicators.get("volume_profile")

    @property
    def sr_levels(self) -> list:
        """List of classical S/R levels."""
        return self.indicators.get("sr_levels") or []

    @property
    def atr_14(self) -> float | None:
        """ATR-14 scalar (last bar)."""
        return self.indicators.get("atr_14")

    @property
    def rsi_14(self):
        """RSI-14 pd.Series."""
        return self.indicators.get("rsi_14")

    @property
    def ema_8(self):
        """EMA-8 pd.Series."""
        return self.indicators.get("ema_8")

    @property
    def ema_21(self):
        """EMA-21 pd.Series."""
        return self.indicators.get("ema_21")

    @property
    def ema_50(self):
        """EMA-50 pd.Series."""
        return self.indicators.get("ema_50")

    @property
    def ema_200(self):
        """EMA-200 pd.Series."""
        return self.indicators.get("ema_200")

    @property
    def macd(self) -> dict:
        """MACD components (line, signal, hist)."""
        return {
            "line":   self.indicators.get("macd"),
            "signal": self.indicators.get("macd_signal"),
            "hist":   self.indicators.get("macd_hist"),
        }

    @property
    def macd_line(self):
        """MACD Line series."""
        return self.indicators.get("macd")

    @property
    def macd_signal(self):
        """MACD Signal series."""
        return self.indicators.get("macd_signal")

    @property
    def macd_hist(self):
        """MACD Histogram series."""
        return self.indicators.get("macd_hist")

    @property
    def adx(self) -> float | None:
        """ADX-14 scalar trend strength (>25 = strong trend)."""
        return self.indicators.get("adx")

    @property
    def bb(self) -> dict:
        """Bollinger Bands components (upper, mid, lower, width, pct_b)."""
        return {
            "upper": self.indicators.get("bb_upper"),
            "mid":   self.indicators.get("bb_mid"),
            "lower": self.indicators.get("bb_lower"),
            "width": self.indicators.get("bb_width"),
            "pct_b": self.indicators.get("bb_pct_b"),
        }

    @property
    def stoch(self) -> dict:
        """Stochastic components (k, d)."""
        return {
            "k": self.indicators.get("stoch_k"),
            "d": self.indicators.get("stoch_d"),
        }

    # ── Parametric Accessors ───────────────────────────────────────────────────

    def ema(self, span: int):
        """Return EMA pd.Series for any span (e.g. ctx.ema(9))."""
        key = f"ema_{span}"
        if key in self.indicators:
            return self.indicators[key]
        if self.df_ltf is not None and not self.df_ltf.empty:
            return self.df_ltf["Close"].ewm(span=span, adjust=False).mean()
        return None

    def sma(self, window: int):
        """Return SMA pd.Series for any window (e.g. ctx.sma(50))."""
        key = f"sma_{window}"
        if key in self.indicators:
            return self.indicators[key]
        if self.df_ltf is not None and not self.df_ltf.empty:
            return self.df_ltf["Close"].rolling(window).mean()
        return None

    def rsi(self, period: int = 14):
        """Return RSI pd.Series for any period (e.g. ctx.rsi(14))."""
        key = f"rsi_{period}"
        if key in self.indicators:
            return self.indicators[key]
        if self.df_ltf is not None and not self.df_ltf.empty:
            from ..indicators.rsi import calc_rsi
            return calc_rsi(self.df_ltf["Close"], period)
        return None

    def atr(self, period: int = 14) -> float | None:
        """Return ATR scalar for any period (e.g. ctx.atr(14))."""
        key = f"atr_{period}"
        if key in self.indicators:
            return self.indicators[key]
        if self.df_ltf is not None and not self.df_ltf.empty:
            from ..indicators.atr import calc_atr
            return calc_atr(self.df_ltf, period)
        return None

    def get(self, key: str, default=None):
        """Dict-like get from indicators dictionary."""
        return self.indicators.get(key, default)

    def __getitem__(self, key: str):
        return self.indicators[key]

    def __contains__(self, key: str) -> bool:
        return key in self.indicators

    def __repr__(self) -> str:
        ind_keys = [k for k in self.indicators if not k.startswith("_")]
        return (
            f"<StrategyContext {self.ticker} mode={self.mode} "
            f"price={self.current_price:.4f} indicators={ind_keys}>"
        )


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies.

    To create a new strategy:

    1. Create a .py file in ``strategies/`` (auto-discovered on import).
    2. Subclass BaseStrategy and set class attributes.
    3. Declare ``required_indicators`` — the engine computes only these.
    4. Implement ``evaluate(ctx)`` and return a ``TradeSetup`` or ``None``.

    The strategy will appear automatically in:
    - The live scanner (``/scan`` command)
    - The backtester (``--disable`` flag)
    - The interactive manager (``python -m modules.finance.trader.manage``)
    - The Telegram ``/setups`` command

    Example::

        class MACDCrossStrategy(BaseStrategy):
            name                = "MACD Cross"
            description         = "MACD bullish/bearish cross with bias filter"
            setup_type          = "continuation"
            priority            = 50
            bias_provider       = "macd"
            required_indicators = ["bias", "atr_14", "ema_21", "macd", "macd_signal"]

            def evaluate(self, ctx: StrategyContext):
                if ctx.bias.bias == "neutral":
                    return None
                macd_line = ctx.macd_line
                signal    = ctx.macd_signal
                # ... compute entry/sl/tp ...
    """

    #: Human-readable name. Used as setup_name in signals and key in config.
    name: str = "Unnamed Strategy"

    #: One-line description shown in /help output.
    description: str = ""

    #: "continuation" | "reversal" | "range" — tagged in the DB.
    setup_type: str = "continuation"

    #: Evaluation order (lower number = evaluated first).
    priority: int = 100

    #: Bias engine to use for this strategy: "rsi" | "macd" | "ema" | "composite".
    bias_provider: str = "rsi"

    #: Whether this strategy requires non-neutral directional bias.
    #: Trend strategies set True; mean-reversion/range strategies set False.
    requires_directional_bias: bool = True

    #: If set, override (htf_interval, ltf_interval) for this strategy.
    #: Example: ("1h", "15m") for a shorter-timeframe scalping strategy.
    timeframe_override: tuple[str, str] | None = None

    #: Indicator keys this strategy needs. The engine computes the union of
    #: all enabled strategies' required_indicators before calling evaluate().
    #: Strategies are only given indicators they declare here — no more.
    required_indicators: list[str] = []

    @abstractmethod
    def evaluate(self, ctx: StrategyContext) -> Optional[object]:
        """Evaluate the strategy against the current market context.

        Args:
            ctx: StrategyContext whose ``indicators`` dict contains at least
                 the keys declared in ``required_indicators``.

        Returns:
            A TradeSetup if all gates pass, else None.
        """
        ...
