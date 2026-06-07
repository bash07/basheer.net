# Finance Module — Watchlist, Technical & Fundamental Analysis

> **Scope:** `app/modules/finance/watchlist/`, `app/modules/finance/technical/`, `app/modules/finance/fundamental/`
> **Data source:** [yfinance](https://github.com/ranaroussi/yfinance) (free tier)
> **Interface:** Telegram bot commands + APScheduler background jobs

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Watchlist Module](#2-watchlist-module)
   - [Ticker Categories](#21-ticker-categories)
   - [Commands](#22-commands)
   - [Scheduled Jobs](#23-scheduled-jobs)
   - [Database Models](#24-database-models)
   - [Price Helper (`prices.py`)](#25-price-helper-pricespy)
3. [Technical Analysis Module](#3-technical-analysis-module)
   - [Indicators Computed](#31-indicators-computed)
   - [Commands](#32-commands)
   - [Scheduled Jobs](#33-scheduled-jobs)
   - [Database Models](#34-database-models)
4. [Fundamental Analysis Module](#4-fundamental-analysis-module)
   - [Strategy Pattern](#41-strategy-pattern)
   - [Graham Strategy](#42-graham-strategy)
   - [Commands](#43-commands)
   - [Scheduled Jobs](#44-scheduled-jobs)
   - [LLM Verdict](#45-llm-verdict)
5. [Shared Infrastructure](#5-shared-infrastructure)
   - [TTL Cache](#51-ttl-cache)
   - [Market Hours](#52-market-hours)
   - [Notify Helper](#53-notify-helper)
6. [File Map](#6-file-map)
7. [Testing](#7-testing)
8. [Known Gotchas](#8-known-gotchas)

---

## 1. Architecture Overview

```
finance/
├── __init__.py              # registers all submodules with the platform
├── _shared/
│   ├── cache.py             # TTLCache singletons (ticker, coingecko, feargreed, fundamentals)
│   ├── market_hours.py      # is_market_open(category) → bool
│   ├── notify.py            # notify(text) / notify_photo(path) — no Update needed
│   └── chart_renderer.py    # subprocess-based matplotlib chart renderer
├── watchlist/
│   ├── models.py            # WatchlistEntry, PriceAlert (SQLAlchemy)
│   ├── commands.py          # /watch, /unwatch, /watchlist, /price, /prices,
│   │                        #   /alert, /alerts, /delalert
│   ├── prices.py            # fetch_price_detail, fetch_prices_batch helpers
│   ├── jobs.py              # check_price_alerts (5 min), daily_summary (9 AM IST)
│   └── __init__.py          # register() wires commands + jobs
├── technical/
│   ├── models.py            # SignalLog (SQLAlchemy)
│   ├── indicators.py        # compute_ta(df) → TAResult (pure, no I/O)
│   ├── charts.py            # fetch_and_render, fetch_ta_only (yfinance + renderer)
│   ├── commands.py          # /ta, /rsi, /chart, /signals
│   ├── jobs.py              # scan_signals (every 30 min, market hours only)
│   └── __init__.py          # register() wires commands + jobs
└── fundamental/
    ├── fetcher.py           # FundamentalsData + fetch_fundamentals() — 24h cache
    ├── analyst.py           # generate_verdict() — LLM + template fallback
    ├── formatter.py         # format_full_report, format_screen_report, format_earnings_list
    ├── commands.py          # /fundamental, /screen, /earnings
    ├── jobs.py              # weekly_screen (Sun 8 AM IST), earnings_alert (daily 8 AM IST)
    ├── __init__.py          # register() wires commands + jobs
    └── strategies/
        ├── base.py          # CriterionResult, StrategyResult, BaseStrategy ABC
        ├── graham.py        # GrahamStrategy — 6 classic value criteria
        └── __init__.py      # STRATEGIES dict + DEFAULT_METHOD
```

Data flows one way: `commands.py` / `jobs.py` → `prices.py` / `indicators.py` → `_shared/cache.py` → yfinance. No submodule imports from another submodule except through `_shared/`.

---

## 2. Watchlist Module

### 2.1 Ticker Categories

Every ticker in the watchlist carries a **category** that controls:
- which market hours check applies (TA scan skips closed markets)
- the label shown in `/watchlist` and `/prices` output

| Category key | Display | Auto-detected by |
|---|---|---|
| `us_index` | 🇺🇸 US Indices | `^GSPC`, `^IXIC`, `^DJI`, `^RUT`, `^NDX`, `^VIX`, `^TNX`, or any `^` symbol |
| `us_stock` | 🇺🇸 US Stocks | Default (plain ticker like `AAPL`) |
| `indian_index` | 🇮🇳 Indian Indices | `^NSEI`, `^BSESN`, `^CNXIT`, `^NSEBANK`, `^NIFMDCP100` |
| `indian_stock` | 🇮🇳 Indian Stocks | `.NS` or `.BO` suffix |
| `crypto` | ₿ Crypto | `-USD` or `-USDT` suffix |
| `forex` | 💱 Forex | `=X` suffix (e.g. `INR=X`, `EURUSD=X`) |
| `commodity` | 🪙 Commodities | `=F` suffix (e.g. `GC=F` for Gold) |

**Telegram Markdown note:** Category keys contain underscores (`indian_index`). When displayed in a Markdown message, Telegram renders `_` as italic. The bot wraps all category names in backticks to prevent this. The `--cat` flag accepts both `indian_index` **and** `indianindex` (the alias form users see if the display is garbled).

### 2.2 Commands

#### `/watch <ticker> [--cat <category>]`
Add a ticker to the watchlist.

```
/watch AAPL
/watch RELIANCE.NS
/watch RELIANCE.NS --cat indian_stock
/watch RELIANCE.NS --cat indianstock     ← alias form also works
```

**Flow:**
1. Validates `--cat` if present (canonical + alias forms accepted)
2. Sends "🔍 Validating…" placeholder
3. Calls `_fetch_ticker_info(symbol)` in an executor thread (yfinance `fast_info` + `Ticker.info`)
4. If valid: inserts `WatchlistEntry` row, edits message to show price + category
5. If already tracked: warns without inserting duplicate

**Auto-detects category** from symbol format if `--cat` is omitted.

---

#### `/unwatch <ticker>`
Remove a ticker and **all its price alerts** from the watchlist.

```
/unwatch BTC-USD
```

Cascade-deletes `PriceAlert` rows with `DELETE WHERE symbol = ?`, then deletes the `WatchlistEntry`. Replies with the count of alerts removed.

---

#### `/watchlist [category]`
List all tracked tickers, grouped by category.

```
/watchlist
/watchlist crypto
/watchlist indian_stock
```

---

#### `/price <ticker>`
Quick price card for any ticker (does not need to be in the watchlist).

```
/price AAPL
/price BTC-USD
/price ^NSEI
```

**Reply format:**
```
📈 *AAPL*
💵 `185.4200` (+1.09%)
📊 Day  `183.0000` — `186.5000`
📅 52W  `155.0000` — `200.0000`
```

Uses `fetch_price_detail()` from `prices.py` (cached 5 min).

---

#### `/prices [category]`
Batch price card for **all watchlisted tickers**, grouped by category.

```
/prices
/prices crypto
```

Sends "⏳ Fetching prices…" first, then edits to the result. Uses `fetch_prices_batch()` which calls `yf.Tickers(…)` for batch efficiency and checks the 5-min cache before fetching.

**Reply format:**
```
💹 *Watchlist Prices*

*₿ Crypto*
📈 `BTC-USD` `67,000.00` (+2.10%)
📉 `ETH-USD` `3,200.00` (-0.45%)

*🇺🇸 US Stocks*
📈 `AAPL` `185.42` (+1.09%)
```

---

#### `/alert <ticker> > <price>` or `/alert <ticker> < <price>`
Set a price threshold alert for a watchlisted ticker.

```
/alert BTC-USD > 70000
/alert AAPL < 170
```

- Ticker **must be in the watchlist** first
- Condition must be `>` or `<`
- Price can include commas: `70,000` is accepted
- Stores a `PriceAlert` row; the 5-min job evaluates it automatically

---

#### `/alerts`
List all **untriggered** price alerts with their IDs.

```
/alerts
```

Output:
```
🔔 *Active Price Alerts*

`#1` *BTC-USD* ↑ above `70,000.00`
`#2` *AAPL* ↓ below `170.00`

Delete with /delalert <id>
```

---

#### `/delalert <id>`
Delete a price alert by its numeric ID (shown in `/alerts`).

```
/delalert 3
```

---

### 2.3 Scheduled Jobs

#### `check_price_alerts_job` — every 5 minutes

1. Loads all untriggered `PriceAlert` rows
2. Deduplicates symbols → batch-fetches prices with `yf.download` (period=2d, interval=1d) or falls back to per-ticker `fast_info`
3. Evaluates `price > threshold` / `price < threshold` for each alert
4. Marks triggered alerts (`triggered = True`) in the DB
5. Sends one Telegram message per triggered alert via `notify()`
6. Adds 0.3 s delay between messages to avoid Telegram flood limits

---

#### `daily_summary_job` — 9:00 AM IST, Mon–Fri (CronTrigger)

Sends a morning price briefing for **all watchlisted tickers** grouped by category. Same format as `/prices`. Uses `fetch_prices_batch()` which checks the cache first.

Trigger: `CronTrigger(hour=9, minute=0, day_of_week="mon-fri", timezone="Asia/Kolkata")`

---

### 2.4 Database Models

#### `WatchlistEntry`

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | auto |
| `symbol` | String(30) | unique, indexed |
| `display_name` | String(200) | from `yf.Ticker.info` |
| `category` | String(30) | indexed; one of the 7 category keys |
| `added_at` | DateTime | `datetime.now(timezone.utc)` |

#### `PriceAlert`

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | auto |
| `symbol` | String(30) | indexed; FK-free (by design — simpler) |
| `condition` | String(2) | `">"` or `"<"` |
| `threshold` | Float | |
| `triggered` | Boolean | default `False`; set `True` once fired, never deleted automatically |
| `created_at` | DateTime | `datetime.now(timezone.utc)` |

> Triggered alerts are **retained in the DB** so there is a history. Only untriggered alerts appear in `/alerts`.

---

### 2.5 Price Helper (`prices.py`)

Extracted helpers shared between `/price`, `/prices`, `daily_summary_job`, and the price alert checker.

| Function | Purpose |
|---|---|
| `fetch_price_detail(symbol)` | Single ticker: price, 24h change %, day high/low, 52W range. **Raises `ValueError`** if no data. |
| `fetch_prices_batch(symbols)` | Batch via `yf.Tickers`. Returns `dict[str, dict \| None]`. None means fetch failed. |
| `format_price_card(info)` | Full Markdown price card (used by `/price`). |
| `format_price_row(sym, info)` | Compact one-liner (used by `/prices` and daily summary). |

**Cache key pattern:** `"price_detail:{symbol}"`, TTL 5 minutes (`ticker_cache`).

---

## 3. Technical Analysis Module

### 3.1 Indicators Computed

All computation is in `indicators.py`. Inputs: a pandas OHLCV DataFrame (≥ 210 rows for EMA200). Outputs: a `TAResult` dataclass.

| Indicator | Detail |
|---|---|
| **RSI(14)** | Wilder's exponential smoothing. Signals: `overbought` (> 70), `oversold` (< 30), `neutral` |
| **EMA 21** | Short-term trend |
| **EMA 50** | Medium-term trend |
| **EMA 100** | Long-term trend |
| **EMA 200** | Primary trend |
| **Trend alignment** | `bullish` if `price > EMA21 > EMA50 > EMA200`, `bearish` if reverse, else `mixed` |
| **Volume ratio** | Current volume ÷ 20-bar rolling average. Signals: `spike` (≥ 2×), `elevated` (≥ 1.5×), `normal`, `low` (< 0.5×) |
| **EMA crossovers** | Detects 21/50, 50/200, 21/200 crossovers within the last 3 candles. Emits bullish/bearish label |

> **Design note:** All pandas/numpy imports are inside `compute_ta()` to avoid loading 150 MB of libraries at startup on the 1 GB e2-micro instance.

#### `TAResult` fields

```python
@dataclass
class TAResult:
    symbol:        str
    timeframe:     str           # "1d" | "1wk" | "1mo"
    current_price: float
    rsi:           float | None
    rsi_signal:    str           # "overbought" | "oversold" | "neutral"
    emas:          EMASet        # .ema21, .ema50, .ema100, .ema200
    trend:         str           # "bullish" | "bearish" | "mixed"
    volume_ratio:  float | None
    volume_signal: str           # "spike" | "elevated" | "normal" | "low"
    crossovers:    list[str]     # e.g. ["🟢 Bullish crossover: EMA21 above EMA50 (21/50)"]
    signals:       list[str]     # union of RSI/volume/crossover alerts
    ohlcv_json:    str           # JSON for chart renderer subprocess
    ema_series:    dict          # {"ema21": [...], "ema50": [...], ...}
    rsi_series:    list[float]
```

### 3.2 Commands

#### `/ta <ticker> [--tf 1d|1wk|1mo]`
Full TA: sends a candlestick chart image with EMA overlays + RSI subplot, followed by a text summary. Default timeframe: `1d`.

```
/ta AAPL
/ta BTC-USD --tf 1wk
/ta ^NSEI --tf 1d
```

**Flow:**
1. Sends "📊 Computing…" placeholder
2. Calls `fetch_and_render(symbol, tf)` in `charts.py`:
   - Downloads 6 months of OHLCV via `yf.download`
   - Computes `TAResult` via `compute_ta()`
   - Renders chart via `chart_renderer.py` (subprocess, isolated matplotlib process)
3. Sends chart as `reply_photo` with `format_ta_summary(result)` as caption
4. Deletes the placeholder message

**Reply:** PNG chart image captioned with the full TA summary.

---

#### `/rsi <ticker>`
Quick RSI check, text only (no chart, faster).

```
/rsi AAPL
/rsi BTC-USD
```

**Reply:**
```
🔴 *AAPL* RSI(14): `74.3` — *OVERBOUGHT*
💵 Price: `185.42`  |  Trend: *Bullish*
```

---

#### `/chart <ticker> [--tf 1d|1wk|1mo]`
Chart image only, no summary text. Useful for a quick visual without the data dump.

```
/chart BTC-USD --tf 1wk
```

Caption shows: `📊 *BTC-USD* — 1WK  |  💵 \`67,000.00\``

---

#### `/signals`
Show the 20 most recent TA signals logged by the background scanner.

```
/signals
```

**Reply:**
```
⚡ *Recent TA Signals*

`01 May 14:30` *AAPL* — RSI OVERBOUGHT (74.3)
`01 May 12:00` *BTC-USD* — 🟢 Bullish crossover: EMA21 above EMA50 (21/50)
```

---

### 3.3 Scheduled Jobs

#### `scan_signals_job` — every 30 minutes

1. Loads all `WatchlistEntry` rows
2. Filters to tickers whose market is currently open (`is_market_open(category)`)
3. Batch-downloads 6-month daily OHLCV via `yf.download` (cache key: `ohlcv:{sym}:6mo:1d`, TTL 5 min)
4. For each ticker, runs `compute_ta(df)`
5. Checks each signal in `result.signals` against a **6-hour dedup window** in `SignalLog`
6. Inserts new `SignalLog` rows and fires Telegram alerts via `notify()`
7. 0.5 s pause between alerts to respect Telegram rate limits

**Dedup logic:** key = `"{symbol}:{signal_type}:{description[:60]}"`. Same signal won't re-fire within 6 hours.

**Signal types** (auto-classified):
| Signal type | Keyword in description |
|---|---|
| `ema_crossover` | "crossover" |
| `rsi_extreme` | "rsi" |
| `volume_spike` | "volume" |
| `other` | everything else |

---

### 3.4 Database Models

#### `SignalLog`

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | auto |
| `symbol` | String(30) | indexed |
| `signal_type` | String(30) | `ema_crossover`, `rsi_extreme`, `volume_spike`, `other` |
| `description` | String(500) | human-readable signal text |
| `notified` | Boolean | `True` = Telegram message sent |
| `detected_at` | DateTime | `datetime.now(timezone.utc)` |

---

## 4. Fundamental Analysis Module

> **Supported categories:** `us_stock`, `indian_stock` only.
> Crypto, forex, indices, and commodities are excluded — `Ticker.info` does not return meaningful P/E or earnings calendar data for those instrument types.

### 4.1 Strategy Pattern

The module uses a **pluggable strategy pattern** so new investment frameworks can be added without touching existing code.

```
strategies/
├── base.py       — CriterionResult, StrategyResult, BaseStrategy ABC
├── graham.py     — GrahamStrategy (first built-in)
└── __init__.py   — STRATEGIES = {"graham": GrahamStrategy, ...}
```

**Adding a new strategy** (e.g. Peter Lynch):
1. Create `strategies/lynch.py` with `class LynchStrategy(BaseStrategy)`
2. Add `"lynch": LynchStrategy` to `STRATEGIES` in `__init__.py`
3. Done — `/fundamental AAPL --method lynch` works immediately

**`StrategyResult` key properties:**

| Property | Type | Description |
|---|---|---|
| `score` | `int` | Criteria that passed |
| `total` | `int` | Criteria with available data (N/A excluded) |
| `max_score` | `int` | Total criteria defined |
| `pct` | `float` | `score / total * 100` |
| `passed_criteria` | `list` | `CriterionResult` objects where `passed=True` |
| `failed_criteria` | `list` | `CriterionResult` objects where `passed=False` |
| `na_criteria` | `list` | `CriterionResult` objects where data was `None` |
| `verdict` | `str` | LLM or template plain-English paragraph |

---

### 4.2 Graham Strategy

Six classic criteria from *The Intelligent Investor*:

| Criterion | Threshold | yfinance field | Note |
|---|---|---|---|
| P/E Ratio | < 15 | `trailingPE` | |
| P/B Ratio | < 1.5 | `priceToBook` | |
| Current Ratio | > 2 | `currentRatio` | Liquidity proxy |
| Debt/Equity | < 0.5 | `debtToEquity ÷ 100` | yfinance reports % |
| Dividend Yield | > 0% | `dividendYield` | Any positive yield |
| EPS Growth | > 0% | `earningsGrowth` | TTM proxy (not 5Y) |

Missing data for any criterion sets `passed = None` and excludes that criterion from `score`/`total`. A ticker with 3 N/A fields is scored out of the remaining 3.

---

### 4.3 Commands

#### `/fundamental <ticker> [--method graham]`
Full fundamental screen with LLM verdict for one ticker.

```
/fundamental AAPL
/fundamental RELIANCE.NS --method graham
```

**Flow:**
1. Sends “🔍 Fetching…” placeholder
2. `fetch_fundamentals(symbol)` in executor (24h cached)
3. Edits to “🧮 Scoring…” → runs chosen strategy
4. Edits to “✍️ Generating verdict…” → calls LLM
5. Edits final message: header + criteria table + verdict

**Reply format:**
```
📊 *AAPL* — Apple Inc.
🏣 Technology | Consumer Electronics
💰 Market Cap: $2.85T
📅 Next Earnings: 01 Aug 2026 (91d)

*Graham Score: 3/6* (3/5 with data)
——————————
❌ P/E Ratio: `27.30` (threshold: < 15)
❌ P/B Ratio: `47.20` (threshold: < 1.5)
✅ Current Ratio: `0.90` (threshold: > 2)
...

📝 *Verdict:*
Apple passes 3 of 5 measurable Graham criteria...
```

---

#### `/screen [--method graham]`
Rank all watchlisted **stock** tickers by fundamental score.

```
/screen
/screen --method graham
```

Fetches fundamentals **concurrently** (parallel executor tasks, one per ticker). Results sorted descending by score. Non-stock categories silently excluded.

**Reply format:**
```
🔍 *Graham Screen — Watchlist*
_01 May 2026_

1. 🟢 `RELIANCE.NS` 5/6 (83%)
2. 🟡 `INFY.NS`     3/6 (50%)
3. 🔴 `AAPL`       1/6 (17%)

Use /fundamental <ticker> for a full breakdown.
```

---

#### `/earnings [days]`
Show upcoming earnings dates for all watchlisted stocks within N days (default: 7, max: 90).

```
/earnings
/earnings 14
```

**Reply format:**
```
📅 *Upcoming Earnings (next 7 days)*

• *AAPL* — Apple Inc.
  03 May 2026 (2 days)
• *MSFT* — Microsoft Corp.
  06 May 2026 (5 days)
```

---

### 4.4 Scheduled Jobs

#### `weekly_screen_job` — Sunday 8:00 AM IST
Runs the Graham screen on all watchlisted stocks and sends the ranked result to Telegram proactively. Same logic as `/screen` but triggered by `CronTrigger(day_of_week="sun", hour=8, timezone="Asia/Kolkata")`.

#### `earnings_alert_job` — Daily 8:00 AM IST
Checks next earnings dates for all watchlisted stocks. Sends an alert when any ticker reports within **3 days**. Triggered by `CronTrigger(hour=8, timezone="Asia/Kolkata")`.

---

### 4.5 LLM Verdict

`analyst.py` builds a self-contained prompt from the `FundamentalsData` + `StrategyResult` and calls `pydantic_ai.Agent` with the platform’s configured model.

**System prompt:** *"You are a quantitative fundamental analyst. Your verdicts are concise, factual, and data-driven. Frame everything as screening results — never give investment advice."*

**User prompt includes:** symbol, sector, industry, method name, score, all criterion results with PASS/FAIL/N/A status.

**Fallback `_template_verdict()`** runs if the LLM call fails for any reason:
- `pct >= 80%` → “strong fundamental profile”
- `pct >= 50%` → “moderate fundamental profile”
- `pct < 50%` → “weak fundamental profile”
- `total == 0` → “Insufficient data to evaluate”

---

## 5. Shared Infrastructure

### 5.1 TTL Cache

`_shared/cache.py` — thread-safe in-memory cache, no external dependency.

```python
from modules.finance._shared.cache import ticker_cache

data = ticker_cache.get("price_detail:AAPL")   # None if missing or expired
ticker_cache.set("price_detail:AAPL", result)   # stores for 5 min
ticker_cache.delete("price_detail:AAPL")        # explicit eviction
ticker_cache.clear()                             # flush all
```

**Singletons:**

| Singleton | TTL | Used for |
|---|---|---|
| `ticker_cache` | 5 min | yfinance OHLCV, price detail, ticker info |
| `coingecko_cache` | 10 min | CoinGecko API (future crypto module) |
| `feargreed_cache` | 15 min | Alternative.me Fear & Greed (future) |
| `fundamentals_cache` | 24 h | yfinance `Ticker.info` for fundamental screening |

---

### 5.2 Market Hours

`_shared/market_hours.py` — all times in IST (Asia/Kolkata).

```python
from modules.finance._shared.market_hours import is_market_open, open_categories

is_market_open("us_stock")      # True during 7 PM – 1:30 AM IST (NYSE hours)
is_market_open("indian_stock")  # True during 9:15 AM – 3:30 PM IST
is_market_open("crypto")        # Always True (24/7)
open_categories()               # ["crypto", "us_stock", ...]
```

| Category | Hours (IST) | Weekends |
|---|---|---|
| `us_index`, `us_stock` | 19:00 – 01:30 (overnight) | Closed |
| `indian_index`, `indian_stock` | 09:15 – 15:30 | Closed |
| `crypto` | 00:00 – 23:59 | Open |
| `forex` | 06:30 – 23:00 | — |
| `commodity` | 09:00 – 23:30 | — |

The TA signal scanner uses this to skip tickers whose exchange is closed, avoiding stale data noise.

---

### 5.3 Notify Helper

`_shared/notify.py` — send proactive messages without an `Update` object (for scheduled jobs).

```python
from modules.finance._shared.notify import notify, notify_photo

await notify("🔔 *BTC-USD* crossed $70,000!")
await notify_photo("/tmp/chart.png", caption="AAPL — Daily")
```

`notify_photo` deletes the temp file after sending.

---

## 6. File Map

```
app/modules/finance/
│
├── _shared/
│   ├── cache.py            TTLCache + singletons (ticker/coingecko/feargreed/fundamentals)
│   ├── market_hours.py     is_market_open(category) using IST timezone
│   ├── notify.py           notify(text), notify_photo(path) — bot-level send
│   └── chart_renderer.py   Subprocess matplotlib renderer → PNG path
│
├── watchlist/
│   ├── models.py           WatchlistEntry, PriceAlert (SQLAlchemy Base)
│   ├── commands.py         8 command handlers + _detect_category + _resolve_category
│   ├── prices.py           fetch_price_detail, fetch_prices_batch, formatters
│   ├── jobs.py             check_price_alerts_job (5 min), daily_summary_job (9 AM IST)
│   └── __init__.py         register(bot_app, scheduler, authorized) → metadata dict
│
├── technical/
│   ├── models.py           SignalLog (SQLAlchemy Base)
│   ├── indicators.py       compute_ta(df, symbol, tf) → TAResult  [pure, no I/O]
│   ├── charts.py           fetch_and_render, fetch_ta_only  [yfinance + subprocess]
│   ├── commands.py         ta_command, rsi_command, chart_command, signals_command
│   ├── jobs.py             scan_signals_job (30 min, market-hours-filtered)
│   └── __init__.py         register(bot_app, scheduler, authorized) → metadata dict
│
└── fundamental/
    ├── fetcher.py          FundamentalsData + fetch_fundamentals()  [24h TTL cache]
    ├── analyst.py          generate_verdict() — PydanticAI LLM + _template_verdict()
    ├── formatter.py        format_full_report, format_screen_report, format_earnings_list
    ├── commands.py         fundamental_command, screen_command, earnings_command
    ├── jobs.py             weekly_screen_job (Sun 8 AM IST), earnings_alert_job (daily)
    ├── __init__.py         register(bot_app, scheduler, authorized) → metadata dict
    └── strategies/
        ├── base.py         CriterionResult, StrategyResult, BaseStrategy ABC
        ├── graham.py       GrahamStrategy — 6 Graham criteria
        └── __init__.py     STRATEGIES dict + DEFAULT_METHOD
```

---

## 7. Testing

Tests live in `tests/finance/`. Run from the `automation/` directory:

```bash
# All finance tests
python -m pytest tests/finance/ -v

# Fundamental module only
python -m pytest tests/finance/fundamental/ -v
```

| Test file | Covers |
|---|---|
| `test_cache.py` | TTLCache set/get/expiry/delete/clear/overwrite (10 tests) |
| `test_market_hours.py` | is_market_open for all 7 categories, weekday/weekend, overnight US session (14 tests) |
| `test_indicators.py` | compute_ta output, RSI/trend/volume classification, format_ta_summary (36 tests) |
| `test_watchlist.py` | _detect_category, **_resolve_category** (alias bug), DB CRUD (30 tests) |
| `test_prices.py` | format_price_card, format_price_row, /price command, /prices command (15 tests) |
| `test_commands.py` | All 8 watchlist + 3 TA commands, DB integration, mock yfinance (22 tests) |
| `fundamental/test_fundamental.py` | GrahamStrategy (13), formatters (10), _template_verdict (3), command handlers (9) — 37 tests |

**Test infrastructure (`tests/conftest.py`):**

- `make_update(args)` — builds mock `(Update, Context)` pair with pre-wired `reply_text`, `edit_text`, `reply_photo`, `delete` AsyncMocks
- `clean_db` — isolated SQLite in `tmp_path`; monkeypatches `core.db.get_db` so all handlers transparently use the test DB
- `coro_safe_submit` — mocks `queue.submit` and closes coroutine args (prevents Python 3.13 "coroutine never awaited" warnings)
- `sample_ohlcv` — 250-row seeded OHLCV DataFrame (session-scoped, shared across all TA tests)

---

## 8. Known Gotchas

### Telegram Markdown eats underscores
Category names like `indian_index` rendered in `parse_mode="Markdown"` will display as `indianindex` — underscores become italic markers. Always wrap category names in backticks in Markdown messages. The `/watch --cat` flag accepts both forms via `_resolve_category()`.

### `expire_on_commit=False` is required
`core/db.py` uses `expire_on_commit=False` on `SessionLocal`. Without it, accessing ORM attributes after the `with get_db() as db:` block closes raises `sqlalchemy.orm.exc.DetachedInstanceError`. Any grouping or formatting of ORM objects must happen **inside** the `with` block if you ever remove this setting.

### Lazy imports for pandas/numpy
`compute_ta()` and all price-fetch functions import `pandas`, `numpy`, and `yfinance` **inside** the function body. This is intentional — the platform runs on a 1 GB GCP e2-micro and loading these at startup would consume ~150 MB before any request is served. Do not move these imports to module level.

### yfinance batch vs single
- For OHLCV (TA scan): `yf.download(tickers=[...], period="6mo", interval="1d", group_by="ticker")` returns a multi-level DataFrame when >1 symbol. Use `raw[sym]` to extract per-ticker slices.
- For prices (alert checker): falls back to per-ticker `fast_info` if batch download fails.
- For `/watch` validation: `yf.Ticker(symbol).fast_info.last_price` (single, fast, no OHLCV).

### Chart renderer is a subprocess
`chart_renderer.py` runs matplotlib in a **separate Python subprocess** to avoid memory leaks and matplotlib thread-safety issues inside an async bot. The PNG is written to a temp file and returned as a path. Callers are responsible for deleting it after `reply_photo`.

### Price alert history is retained
`PriceAlert.triggered = True` is set when an alert fires but the row is never deleted. This allows future history/audit features. `/alerts` only shows `WHERE triggered = False`.

### yfinance D/E ratio is a percentage
`Ticker.info["debtToEquity"]` returns the value as a **percentage** — e.g. `50.0` means a 0.50 D/E ratio. `fetcher.py` divides by 100 before storing in `FundamentalsData.debt_to_equity`. If you add a new strategy, always use the already-normalised decimal value.

### Fundamentals cache survives restarts only in-process
The `fundamentals_cache` (24h TTL) is an in-memory `TTLCache`. It does not persist across process restarts. On a cold start, the first `/screen` or `/earnings` call for N tickers will make N sequential `yfinance.Ticker.info` HTTP calls (~1-2 s each). For a 10-ticker watchlist expect ~15 s on first call; subsequent calls within 24 h are instant.

### /screen only covers stock categories
`FUNDAMENTAL_CATEGORIES = frozenset({"us_stock", "indian_stock"})`. Crypto, forex, commodity, and index tickers are silently excluded from `/screen` and `/earnings` because `Ticker.info` for those types does not return meaningful P/E, P/B, or earnings calendar data.
