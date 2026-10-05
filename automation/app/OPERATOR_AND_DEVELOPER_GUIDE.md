# Basheer.net Automation Engine — Developer & Operator Guide

> 📚 **System Documentation Index:**
> - 🏛️ **System Architecture:** [SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md) — Comprehensive architecture covering core platform, finance, blog, monitor, and ops modules.
> - 🎯 **Trading Engine & OMS Specification:** [modules/finance/trader/README_finance_trader.md](modules/finance/trader/README_finance_trader.md) — Institutional SMC scanner, dynamic indicator registry, and OMS roadmap.
> - 💹 **Finance Domain Manual:** [modules/finance/README_finance.md](modules/finance/README_finance.md) — Central finance architecture.
> - 📋 **Watchlist Submodule:** [modules/finance/watchlist/README_finance_watchlist.md](modules/finance/watchlist/README_finance_watchlist.md) — Asset registry & price alerts.
> - 📊 **Technical Analysis Submodule:** [modules/finance/technical/README_finance_technical.md](modules/finance/technical/README_finance_technical.md) — Indicator suite & chart engine.
> - 📈 **Fundamental Analysis Submodule:** [modules/finance/fundamental/README_finance_fundamental.md](modules/finance/fundamental/README_finance_fundamental.md) — Value screens & AI verdicts.
> - 🌐 **Platform Root:** [../README.md](../README.md) & [../ARCHITECTURE.md](../ARCHITECTURE.md)

## 1. Quick Start & Local Development

### 1.1. Prerequisites
- Python 3.11+ (Python 3.13 recommended)
- Git & Docker (for containerized deployments)
- Windows PowerShell / Linux Bash

### 1.2. Setup Environment
```bash
# Navigate to automation app root
cd automation/app

# Create and activate virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 1.3. Environment Configuration (`.env`)
Create or verify `.env` in `automation/app/`:
```ini
TELEGRAM_BOT_TOKEN="your_bot_token"
TELEGRAM_CHAT_ID="your_telegram_chat_id"

# LLM Configuration
LLM_PROVIDER="gemini"
LLM_MODEL="gemini-2.0-flash"
GEMINI_API_KEY="your_gemini_api_key"

# GitHub Publishing (Blog Module)
GITHUB_TOKEN="your_github_personal_access_token"
GITHUB_REPO="bash07/basheer.net"

# Application Settings
GENERIC_TIMEZONE="Asia/Kolkata"
```

### 1.4. Run the Engine
```bash
python main.py
```

---

## 2. Complete Telegram Command Reference

### 2.1. Trading & Order Management System (`finance/trader`)
| Command | Example | Description |
|---|---|---|
| `/scan [ticker]` | `/scan BTC-USD` | Runs institutional SMC scan on watchlist or a specific ticker. |
| `/setups` | `/setups` | Shows high-probability trade alerts detected in the last 48 hours. |
| `/positions` | `/positions` | Live open positions table with live price, quantity, and unrealized P&L / R:R. |
| `/close <id>` | `/close 1` | Manually exits an open position at current market price. |
| `/account` | `/account` | Paper trading account balance, equity, win rate %, and cumulative P&L. |
| `/performance` | `/performance` | Analytics breakdown: executed trades vs. raw theoretical scanner stats. |
| `/traderbias <ticker>`| `/traderbias NVDA` | Higher Timeframe bias analysis (Regime, Hidden Divergence, Cardwell zones). |
| `/confluence <ticker>`| `/confluence AAPL` | Fixed-Range Volume Profile (POC/VAH/VAL) + key structural S/R levels. |
| `/resume` | `/resume` | Resets the Portfolio Circuit Breaker (Kill Switch) after an automated halt. |

### 2.2. Watchlist & Price Tracking (`finance/watchlist`)
| Command | Example | Description |
|---|---|---|
| `/watch <ticker> [--cat category]` | `/watch TSLA --cat us_stock` | Adds a symbol to watchlist. Auto-detects or accepts `us_stock`, `indian_stock`, `us_index`, `indian_index`, `crypto`, `forex`, `commodity`. |
| `/unwatch <ticker>` | `/unwatch TSLA` | Removes a symbol and all its associated price alerts. |
| `/watchlist [category]`| `/watchlist crypto` | Lists all tracked tickers, optionally filtered by category. |
| `/price <ticker>` | `/price NVDA` | Detailed price card with 24h change, day range, and 52-week high/low. |
| `/prices [category]` | `/prices indian_stock` | Compact multi-ticker price overview with color-coded 24h performance. |
| `/alert <ticker> > <price>` | `/alert BTC-USD > 95000` | Sets a static price threshold alert (`>` or `<`). |
| `/alert <ticker> trail -<pct>%` | `/alert AAPL trail -5%` | Sets a trailing price alert tracking peak price and firing on pullback. |
| `/alerts` | `/alerts` | Lists all active, untriggered price alerts. |
| `/delalert <id>` | `/delalert 2` | Deletes an active price alert by ID. |

### 2.3. Technical & Fundamental Analysis (`finance/technical` & `fundamental`)
| Command | Example | Description |
|---|---|---|
| `/ta <ticker> [--tf tf]` | `/ta ^NSEI --tf 1d` | Full TA: candlestick chart image + summary (EMAs, Volume Profile, RSI divergence, MTF alignment, trend). |
| `/rsi <ticker>` | `/rsi AAPL` | Quick text-only RSI(14) indicator check. |
| `/chart <ticker> [--tf tf]` | `/chart ETH-USD` | Annotated candlestick chart with Volume Profile overlay (POC/VAH/VAL). |
| `/signals` | `/signals` | Lists recent technical alerts logged in `SignalLog`. |
| `/fundamental <ticker> [--method m]` | `/fundamental MSFT --method lynch` | Full fundamental screen (`graham`, `lynch`, `buffett`) + 4Q margin check + LLM analyst verdict. |
| `/screen [--method m]` | `/screen lynch` | Screens all watchlist stocks against selected valuation strategy (`graham`, `lynch`, `buffett`). |
| `/earnings [days]` | `/earnings 14` | Lists watched stock tickers reporting earnings within N days (default: 7). |

### 2.4. Core Controls, Blog & Uptime (`core`, `blog`, `monitor`)
| Command | Example | Description |
|---|---|---|
| `/status` | `/status` | System status, live version (`v1.2.1`), git commit hash, uptime, active queue tasks, and LLM config. |
| `/version` | `/version` | Dedicated version and build metadata display shortcut. |
| `/start` or `/menu` | `/start` | Interactive category menu keyboard with drill-down views. |
| `/help` | `/help` | Full categorised command reference. |
| `/cancel` | `/cancel` | Immediately cancels all running tasks and clears chat context. |
| `/generate <topic> [--cat cat]` | `/generate Clean Architecture` | Runs the 4-agent blog pipeline (Researcher → Writer → Editor → Publisher). |
| `/ideas` | `/ideas` | Generates 5 timely tech blog topic ideas. |
| `/posts` | `/posts` | Lists the 5 most recently published blog posts from SQLite. |
| `/urls` | `/urls` | Lists all monitored web endpoints with their current HTTP status. |
| `/check` | `/check` | Triggers an immediate HTTP/TLS uptime probe across all endpoints. |
| `/uptime [name]` | `/uptime basheer.net` | Displays 24h availability percentages and response latencies. |

---

## 3. How-To: Extending the System

### 3.1. How to Add a New Trading Strategy
1. Create a new Python file in `automation/app/modules/finance/trader/strategies/` (e.g., `macd_breakout.py`).
2. Subclass `BaseStrategy`:
```python
from modules.finance.trader.strategies.base import BaseStrategy, StrategyContext
from modules.finance.trader.schemas import TradeSetup

class MACDBreakoutStrategy(BaseStrategy):
    name = "MACD Trend Breakout"
    description = "Enters on strong MACD histogram expansion beyond volume clusters"
    setup_type = "continuation"
    priority = 25
    required_indicators = ["macd", "volume_profile", "atr_14"]
    bias_provider = "macd"

    def evaluate(self, ctx: StrategyContext) -> TradeSetup | None:
        macd_res = ctx.macd() # Access pre-computed indicator
        if not macd_res or macd_res.get("hist", 0) <= 0:
            return None
        
        # Sizing and level logic...
        return TradeSetup(...)
```
3. **No registration needed!** The engine discovers it automatically. Run `/scan` or the backtester to test it immediately.

### 3.2. How to Add a New Technical Indicator
1. Open `automation/app/modules/finance/trader/indicators/`.
2. Add a new indicator module or function decorated with `@register_indicator`:
```python
from . import register_indicator

@register_indicator("supertrend", dependencies=["atr_14"])
def compute_supertrend(df_ltf, df_htf=None, **kwargs):
    # Calculations...
    return {"supertrend": series, "direction": "bullish"}
```
3. Use it in any strategy simply by adding `"supertrend"` to `required_indicators`.

### 3.3. How to Connect a New Live Broker (e.g. Binance / Alpaca)
1. In `automation/app/modules/finance/trader/oms/`, create `binance_broker.py`.
2. Inherit from `Broker` ABC ([`oms/broker.py`](modules/finance/trader/oms/broker.py)):
```python
from .broker import Broker, BrokerOrder, AccountInfo

class BinanceBroker(Broker):
    name = "binance"

    def submit_order(self, ticker, side, order_type, qty, price=None) -> BrokerOrder:
        # Sign and execute HTTP POST to Binance /api/v3/order...
        ...
```
3. Switch active broker at startup:
```python
from modules.finance.trader.oms import set_broker
from modules.finance.trader.oms.binance_broker import BinanceBroker

set_broker(BinanceBroker())
```

### 3.4. How to Backtest a Strategy
Use the built-in CLI replay backtester with caching:
```bash
# Backtest AAPL swing strategy across 2024
python -m modules.finance.trader.backtest --ticker AAPL --category us_stock --from 2024-01-01 --to 2024-06-01

# Backtest Crypto on Binance native 4H bars
python -m modules.finance.trader.backtest --ticker BTC-USD --category crypto --from 2024-01-01 --to 2024-06-01
```

### 3.5. How to Toggle Strategies via Interactive CLI
Run the terminal manager to enable/disable setups without restarting or editing JSON:
```bash
python -m modules.finance.trader.manage
```
