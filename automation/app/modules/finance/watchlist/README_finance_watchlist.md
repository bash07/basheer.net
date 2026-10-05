# Watchlist Submodule (`finance/watchlist/`)

> 📚 **Navigation:**
> - [Finance Domain Root](../README_finance.md)
> - [Technical Analysis Submodule](../technical/README_finance_technical.md)
> - [Fundamental Analysis Submodule](../fundamental/README_finance_fundamental.md)
> - [Algorithmic Trader & OMS](../trader/README_finance_trader.md)
> - [System Architecture](../../SYSTEM_ARCHITECTURE.md)

---

## 1. Overview & Purpose

The **Watchlist Submodule** (`modules/finance/watchlist/`) serves as the central asset registry and real-time price monitoring hub for the entire `finance` domain. All other financial submodules—including classical technical analysis, fundamental value screening, and automated trading strategies—discover and track symbols through this module.

### Core Capabilities:
- **Central Ticker Registry**: Manages tracked assets across global equities, indices, cryptocurrencies, foreign exchange, and commodities.
- **Automatic Category Detection**: Automatically categorizes added symbols based on market conventions and ticker naming rules.
- **Parallel Batch Pricing**: Concurrently retrieves market prices, 24-hour deltas, and 52-week ranges via `yfinance` with thread-pool optimization.
- **In-Memory Caching**: Shared 5-minute TTL caching layer (`_shared/cache.py`) prevents redundant HTTP requests and rate limits.
- **Stateful Price Alerts**: Monitors user-defined upper and lower price thresholds with automated Telegram alerts.
- **Morning Market Briefing**: Generates a consolidated 09:00 AM IST daily brief with category-grouped performance and a 24-hour technical alert digest.

---

## 2. Submodule Architecture & File Structure

```
automation/app/modules/finance/watchlist/
├── __init__.py      # Submodule registration & APScheduler job binding
├── models.py        # SQLAlchemy database schemas (WatchlistEntry, PriceAlert)
├── prices.py        # Parallel batch quote engine, formatting, 52W metrics
├── commands.py      # Telegram slash command handlers (/watch, /price, etc.)
└── jobs.py          # Scheduled background routines (price alerts & morning brief)
```

### Component Breakdown

| File | Primary Responsibility |
| :--- | :--- |
| `models.py` | Defines SQLite ORM tables: `watchlist_entries` and `price_alerts`. |
| `prices.py` | Implements `fetch_price()`, `fetch_prices_batch()`, price cards, and category mappings. |
| `commands.py` | Exposes 8 user commands with parameter validation and inline feedback. |
| `jobs.py` | Runs `check_price_alerts_job` (every 5 min) and `daily_summary_job` (09:00 AM IST weekdays). |
| `__init__.py` | Exports the `register()` contract required by `core/registry.py`. |

---

## 3. Database Schema

### 3.1 `WatchlistEntry` (`watchlist_entries`)

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | Primary Key, Auto-increment | Internal unique entry ID. |
| `symbol` | `VARCHAR(30)` | Unique, Indexed, Non-null | Upper-cased asset ticker (e.g. `AAPL`, `^NSEI`, `BTC-USD`). |
| `display_name` | `VARCHAR(200)` | Non-null | Short human-readable asset title retrieved from metadata. |
| `category` | `VARCHAR(30)` | Indexed, Non-null | Asset category identifier (see table below). |
| `added_at` | `DATETIME` | Non-null, Default UTC | Timestamp when ticker was added to the watchlist. |

### 3.2 `PriceAlert` (`price_alerts`)

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | Primary Key, Auto-increment | Internal unique alert ID. |
| `symbol` | `VARCHAR(30)` | Indexed, Non-null | Ticker symbol target. |
| `alert_type` | `VARCHAR(20)` | Non-null, Default `'static'` | Alert mode: `'static'` threshold or `'trailing'` percentage. |
| `condition` | `VARCHAR(2)` | Nullable | Comparison operator for static alerts: `>` or `<`. |
| `threshold` | `FLOAT` | Nullable | Target price trigger for static alerts. |
| `trail_pct` | `FLOAT` | Nullable | Trailing percentage pullback (e.g. `-5.0` for -5%). |
| `peak_price` | `FLOAT` | Nullable | High-water mark recorded for trailing alerts. |
| `triggered` | `BOOLEAN` | Default `False` | Alert trigger flag (set to `True` once fired). |
| `created_at` | `DATETIME` | Non-null, Default UTC | Timestamp when alert was configured. |

---

## 4. Supported Asset Categories & Detection Rules

The watchlist supports seven asset classes. The detector automatically assigns the category unless explicitly overridden with the `--cat` flag:

| Category ID | Display Label | Detection Heuristics & Suffixes | Examples |
| :--- | :--- | :--- | :--- |
| `us_index` | 🇺🇸 US Indices | Starts with `^` and matches US index symbols (`^GSPC`, `^IXIC`, `^DJI`) | `^GSPC`, `^IXIC`, `^DJI`, `^RUT` |
| `indian_index` | 🇮🇳 Indian Indices | Starts with `^` and matches Indian benchmarks (`^NSEI`, `^BSESN`) | `^NSEI`, `^BSESN`, `^CNXIT` |
| `indian_stock` | 🇮🇳 Indian Stocks | Ends with `.NS` (NSE) or `.BO` (BSE) | `RELIANCE.NS`, `INFY.NS`, `TCS.NS` |
| `crypto` | 🪙 Crypto | Ends with `-USD`, `-USDT`, or matches known crypto bases | `BTC-USD`, `ETH-USD`, `SOL-USD` |
| `forex` | 💱 Forex | Ends with `=X` currency pair convention | `USDINR=X`, `EURUSD=X`, `GBPUSD=X` |
| `commodity` | 🛢️ Commodities | Ends with `=F` futures convention | `GC=F` (Gold), `SI=F` (Silver), `CL=F` (Crude) |
| `us_stock` | 🇺🇸 US Equities | Default fallback for standard alphanumeric symbols | `AAPL`, `MSFT`, `NVDA`, `TSLA` |

---

## 5. Telegram Slash Command Reference

All commands require user authentication (`authorized_only` security filter).

### 5.1 `/watch`
Add a new ticker to the watchlist. Validates existence via `yfinance` before committing to the database.
- **Syntax**: `/watch <ticker> [--cat <category>]`
- **Aliases**: Category aliases supported (e.g. `--cat crypto`, `--cat indian_stock`).
- **Example**:
  ```
  /watch NVDA
  /watch RELIANCE.NS
  /watch BTC-USD --cat crypto
  ```

### 5.2 `/unwatch`
Remove a ticker from the watchlist and delete all associated price alerts.
- **Syntax**: `/unwatch <ticker>`
- **Example**: `/unwatch TSLA`

### 5.3 `/watchlist`
List all currently tracked tickers grouped by category.
- **Syntax**: `/watchlist [category]`
- **Example**:
  ```
  /watchlist
  /watchlist crypto
  ```

### 5.4 `/price`
Display live pricing, 24-hour change, and 52-week trading range for a single ticker.
- **Syntax**: `/price <ticker>`
- **Example**: `/price AAPL`
- **Output Sample**:
  ```
  🍎 Apple Inc. (AAPL)
  Price: $224.23 (+1.45% / +$3.20)
  52W Range: $164.08 ──●───── $237.23
  ```

### 5.5 `/prices`
Batch quote snapshot across all tickers in the watchlist or a specified category.
- **Syntax**: `/prices [category]`
- **Example**: `/prices indian_stock`

### 5.6 `/alert`
Create a price alert (supports both static thresholds and trailing percentage pullbacks).
- **Static Syntax**: `/alert <ticker> <condition> <price>`
  - **Condition**: `>` (greater than) or `<` (less than).
  - **Example**: `/alert BTC-USD > 95000` or `/alert INFY.NS < 1700`
- **Trailing Syntax**: `/alert <ticker> trail <pct>%`
  - Tracks the high-water mark peak price and fires when price pulls back by the specified percentage.
  - **Example**: `/alert AAPL trail -5%` or `/alert BTC-USD trail -8%`

### 5.7 `/alerts`
List all active (untriggered) price alerts with their target thresholds.
- **Syntax**: `/alerts`

### 5.8 `/delalert`
Delete an active price alert by its database ID.
- **Syntax**: `/delalert <id>`
- **Example**: `/delalert 3`

---

## 6. Scheduled Background Jobs

### 6.1 `check_price_alerts_job`
- **Schedule**: Every 5 minutes (`IntervalTrigger(minutes=5)`).
- **Logic**:
  1. Queries all rows in `price_alerts` where `triggered == False`.
  2. Batch-fetches current prices for all affected symbols.
  3. Evaluates condition (`>` or `<`).
  4. Dispatches an immediate Telegram alert notification via `_shared/notify.py`.
  5. Sets `triggered = True` to prevent repeated alerting.

### 6.2 `daily_summary_job` (Morning Briefing)
- **Schedule**: Mon–Fri at 09:00 AM IST (`CronTrigger(hour=9, minute=0, day_of_week="mon-fri")`).
- **Logic**:
  1. Queries all watchlist entries ordered by category.
  2. Concurrently fetches quotes and computes 24-hour deltas.
  3. Queries `SignalLog` from `modules.finance.technical.models` for any technical signals logged within the preceding 24 hours.
  4. Formats a structured Telegram summary with category sections and technical signal highlights.
