# Automation Platform: Module Roadmap

The platform is organized into **domains**, each containing focused **submodules**.
Domains map directly to `app/modules/<domain>/<submodule>/` on disk.
All submodules follow the standard `register()` contract — drop a folder in and it is auto-discovered.

```
app/modules/
├── content/           📝 Content creation, distribution & maintenance
│   ├── blog/          ✅ Active
│   ├── social/        🗺 Planned
│   ├── newsletter/    🗺 Planned
│   └── refresh/       🗺 Planned
├── ops/               🛠 Site health, monitoring & analytics
│   ├── monitor/       ✅ Active
│   ├── system/        ✅ Active
│   ├── health/        ✅ Active
│   ├── analytics/     🗺 Planned
│   └── maintenance/   🗺 Planned
├── finance/           💹 Market tracking, analysis & portfolio
│   ├── watchlist/     ✅ Active
│   ├── technical/     ✅ Active
│   ├── fundamental/   ✅ Active
│   ├── trader/        ✅ Active
│   ├── crypto/        🗺 Planned
│   ├── portfolio/     🗺 Planned
│   ├── reports/       🗺 Planned
│   └── analyst/       🗺 Planned
```

---

## Domain: `content/` — 📝 Content Lifecycle

Everything related to creating, publishing, distributing, and maintaining blog content.

### ✅ `content/blog/` — AI Blog Pipeline (Active)

The core 4-agent sequential pipeline. Researcher → Writer → Editor → Publisher.
Commits structured Hugo markdown directly to GitHub on completion.

**Telegram Commands:**
- `/generate <topic> [--cat Category]` — Run the full pipeline and publish a post
- `/ideas` — Generate 5 timely content ideas right now
- `/posts` — Show the last 5 published posts from the database

**Scheduled Jobs:**
- Every Monday 9 AM — auto-run the ideas pipeline and send results to Telegram

---

### 🗺 `content/social/` — Social Media Syndication (Planned)

**Goal:** Automate content distribution and drive traffic to freshly published blog posts.

**Features:**
- **Auto-Publish:** Hooks into the completion event of the blog pipeline. Waits for the Hugo GitHub Action to build the site, then auto-posts to X (Twitter), LinkedIn, and Mastodon.
- **Teaser Generation:** Uses an LLM to read the finalized blog post and generate platform-specific teasers (e.g., a thread for X, a professional summary for LinkedIn).
- **Repurposing:** Take an old blog post slug, fetch its content, and generate 5 standalone tips/tweets.

**Telegram Commands:**
- `/share <slug>` — Draft social posts for an older blog post and ask for approval to send
- `/repurpose <slug>` — Chop an old post into a multi-part social thread
- `/queue` — View upcoming scheduled social posts

---

### 🗺 `content/newsletter/` — Email Digest (Planned)

**Goal:** Build an audience directly by automating a weekly or monthly email digest.

**Features:**
- **Month in Review:** Scheduled for the 28th of every month. Queries the SQLite `blog_posts` table for everything written that month.
- **LLM Summary:** Uses a `NewsletterAgent` to write a cohesive, friendly "what I've been working on" email summarizing those posts.
- **Dispatch:** Connects to Mailgun, Sendgrid, or Substack APIs to physically send the email to subscribers.

**Telegram Commands:**
- `/draft_newsletter` — Force the creation of a draft right now; bot replies with the draft text
- `/send_newsletter [id]` — Approve a draft and dispatch the emails
- `/subs` — Show subscriber count and growth

---

### 🗺 `content/refresh/` — Content Modernizer (Planned)

**Goal:** Prevent SEO decay by keeping older tutorials and technical guides up to date.

**Features:**
- **Decay Scanner:** A monthly cron job sweeps the GitHub repo for markdown files where `lastmod` is older than 18 months.
- **Update Agent:** Fetches the source content, checks for deprecated APIs or outdated syntax, rewrites it for the current year, and submits a commit.

**Telegram Commands:**
- `/audit_decay` — List the top 5 oldest posts that still get traffic
- `/refresh <slug>` — Tell the AI to immediately modernize a specific old post

---

## Domain: `ops/` — 🛠 Site Health & Operations

Everything about keeping the site running, measuring its performance, and fixing silent failures.

### ✅ `ops/monitor/` — Uptime Monitor (Active)

Pings all configured URLs every 5 minutes. Detects `DOWN`, `RECOVERED`, and `SLOW` states.
Sends immediate Telegram alerts on any status change. Persists metrics in SQLite.

**Scheduled Jobs:**
- Every 5 minutes — ping all configured URLs and alert on state changes

---

### 🗺 `ops/analytics/` — SEO & Traffic Tracker (Planned)

**Goal:** Bring dashboard metrics into Telegram so you never have to log in to an analytics provider.

**Features:**
- **API Integration:** Connects to Cloudflare Analytics API (or Google Analytics / Plausible).
- **Weekly Report:** A Friday 5 PM cron job sends a summary of the week's traffic, top referring domains, and most popular pages.
- **Anomaly Detection:** Alerts immediately on near-zero traffic (DNS/hosting failure) or traffic spikes (viral post).

**Telegram Commands:**
- `/traffic` — Show today's page views and unique visitors
- `/topposts` — Show the top 5 performing posts of the current month

---

### 🗺 `ops/maintenance/` — Link Health (Planned)

**Goal:** Detect broken outbound links before they hurt search rankings.

**Features:**
- **Spider:** A weekly background job crawls all internal pages starting from the blog root.
- **Ping:** Extracts all external `<a href="...">` links and issues HTTP HEAD requests to check for 404s or dead domains.
- **Alerts:** Messages Telegram with the exact posts containing broken links.

**Telegram Commands:**
- `/check_links` — Force a manual spider crawl right now

---

### ✅ `ops/health/` — System Resource & Host Health (Active)

**Documentation:** [app/modules/ops/README_ops.md](app/modules/ops/README_ops.md)

**Goal:** Prevent Linux OOM kills and disk exhaustion on the 1 GB GCP `e2-micro` instance through proactive monitoring and diagnostics.

**Features:**
- **Host & Container Metrics:** Tracks CPU load average, RAM consumption (process RSS vs. host available), and disk usage on persistent volume `/app/data/`.
- **Database & Cache Health:** Measures file sizes for `automation.db`, WAL logs, and `cache.db` to prevent disk bloat.
- **Proactive Watchdog:** APScheduler job runs every 15 minutes, alerting Telegram if available RAM drops below 150 MB or disk usage exceeds 85%.
- **Log Inspection:** Remote log tailing and error log viewer via Telegram.

**Telegram Commands:**
- `/sys` (or `/health`) — Comprehensive resource & system health snapshot
- `/logs [n]` — View last $n$ lines of bot logs
- `/logs errors` — View recent warning/error entries

---


---

## Domain: `finance/` — 💹 Market Tracking, Analysis & Portfolio

Everything related to tracking financial markets — US & Indian equities, crypto, forex, and
commodities — with technical analysis, fundamental screening, proactive alerts, and an
LLM-powered analyst agent. All market data is sourced from **yfinance** (free tier).
Crypto on-chain and dominance data additionally uses the **CoinGecko free API**.
Fear & Greed Index is sourced from the **alternative.me API** (`api.alternative.me/fng/`).
Charts are rendered with **mplfinance + matplotlib** and delivered as Telegram images.

**Implementation Notes (from review):**

- **Multi-Agent Router (Phase 0 core change):** `core/chat.py` evolves from a single
  assistant into a **router agent** that delegates to domain-specific agents. The router's
  tools: `route_to_blog_assistant()`, `route_to_finance_analyst()`, `answer_directly()`.
  This keeps each agent's system prompt and tool list focused.
- **Auto-Import Models (Phase 0 core change):** `core/registry.py` must auto-import each
  discovered module's `models.py` before `init_db()` — eliminates the hardcoded import
  list in `main.py`.
- **Memory Management (e2-micro — 1 GB RAM):** All heavy imports (`matplotlib`,
  `mplfinance`, `pandas`, `numpy`) must use **lazy imports** — imported inside functions,
  never at module level. Chart rendering runs in a **short-lived subprocess** to release
  memory immediately after the image is saved.
- **yfinance Rate Handling:** Use `yf.download(tickers=[...])` for batch fetching.
  Add a 5-min TTL in-memory cache so consecutive commands for the same ticker don't
  double-fetch. Exponential backoff on 429/503 responses.
- **CoinGecko Rate Limit:** Free tier allows ~10-30 calls/min. Cache responses in
  SQLite or in-memory with a 10-min TTL.
- **Market Hours Awareness:** The 30-min TA scan runs globally but **filters tickers
  by active market hours per category** to avoid checking closed markets:
  | Market | Hours (IST) |
  |---|---|
  | US (NYSE/NASDAQ) | 7:00 PM – 1:30 AM |
  | India (NSE) | 9:15 AM – 3:30 PM |
  | Crypto | 24/7 |
  | Forex | Sun 5 PM – Fri 5 PM (24/5) |
- **Notification Flood Control:** With proactive TA signals + price alerts + daily
  reports + earnings alerts, a **digest mode** option batches all non-critical alerts
  into a single Telegram message every N hours instead of firing individually.

---

### ✅ `finance/watchlist/` — Ticker Registry (Active)

**Goal:** A central, categorized ticker store that all other `finance/` submodules read from.
Supports dynamic add/remove via Telegram without restarting the platform.

**Ticker Categories:**
| Category | Examples |
|---|---|
| US Indices | `^GSPC` (S&P 500), `^IXIC` (Nasdaq), `^DJI` (Dow) |
| Indian Indices | `^NSEI` (Nifty 50), `^BSESN` (Sensex) |
| Indian Stocks | `RELIANCE.NS`, `INFY.NS`, `TCS.NS` |
| Crypto | `BTC-USD`, `ETH-USD`, `BNB-USD` |
| Forex | `INR=X` (USD/INR), `EURUSD=X` |
| Commodities | `GC=F` (Gold), `SI=F` (Silver), `CL=F` (Crude Oil) |

**Features:**
- Persists watchlist per category in SQLite (`watchlist_entries` table)
- Validates ticker symbol via yfinance before saving
- Each ticker stores: symbol, display name, category, date added
- **Price Alerts** — per-ticker threshold alerts stored in a `price_alerts` table
  (ticker, condition `>` or `<`, threshold, triggered flag)
- **Data Cache** — 5-min TTL in-memory cache for yfinance responses; shared by all
  `finance/` submodules to avoid redundant HTTP calls
- **Daily Summary Job** — Morning digest at 08:00 with market overview and technical alert digest

**Telegram Commands:**
- `/watch <ticker> [--cat crypto]` — Add a ticker to the watchlist (category auto-detected or overridden)
- `/unwatch <ticker>` — Remove a ticker
- `/watchlist [category]` — List all tracked tickers, optionally filtered by category
- `/price <ticker>` / `/prices` — Fetch live quotes across watchlist
- `/alert <ticker> <condition> <price>` — Set a price alert (e.g., `/alert AAPL > 200`)
- `/alerts` — List all active price alerts
- `/delalert <id>` — Delete a price alert

---

### ✅ `finance/technical/` — Technical Analysis Engine (Active)

**Goal:** On-demand and scheduled TA for any watchlisted ticker. Generates chart images
and text summaries delivered directly to Telegram. Also proactively fires alerts when
significant signals are detected.

**Indicators Computed:**
- **RSI (14)** — Multi-extrema regular & hidden divergence detection (NumPy-native peak detection via `_shared/extrema.py`), swing validation guards, boundary-crossing stateful alerts (`oversold_in`, `overbought_in`, `exit`), and potential/prospective setups (`potential_signals`).
- **EMAs** — 21, 50, 100, 200 period; crossover detection (golden cross, death cross).
- **Volume** — Volume spike detection vs. 20-day average.
- **Trend** — Price position relative to EMA stack (bullish alignment / bearish alignment).

**Chart Output:** Candlestick chart (mplfinance) with EMA overlays, RSI sub-panel, and
volume bars. Sent as a `.png` image to Telegram.

**Proactive Signal Alerts & Morning Digest:**
- Stateful boundary-crossing alerts: fires only on entering/exiting extreme zones or confirmed swing divergences (no repetitive spam).
- Daily 08:00 morning digest incorporates 24-hour technical alert history (`SignalLog`).

**Telegram Commands:**
- `/ta <ticker> [--tf 1d|1wk|1mo]` — Run full TA and send chart + summary
- `/rsi <ticker>` — Quick RSI check (text only, no chart)
- `/chart <ticker> [--tf 1d|1wk|1mo]` — Send chart image only
- `/signals` — Show all active TA signals across the full watchlist right now

**Scheduled Jobs:**
- Every 30 min (market hours) — scan all watchlisted tickers for proactive signals
- Daily 08:00 — morning watchlist brief + 24h TA alert digest

---

### ✅ `finance/fundamental/` — Fundamental Analysis (Active)

**Goal:** Screen and analyse stocks against configurable investment frameworks.
The framework implemented is **Benjamin Graham's value investing method**.
Additional methods (e.g., Peter Lynch, Buffett checklist, DCF) are plug-in
strategy classes with LLM synthesis via `FinancialAnalystAgent`.

**Benjamin Graham Method (Active default):**
| Criterion | Threshold |
|---|---|
| P/E Ratio | < 15 |
| P/B Ratio | < 1.5 |
| Current Ratio | > 2 |
| Debt/Equity | < 0.5 |
| Dividend yield | > 0% (pays dividend) |
| EPS growth (5Y) | Positive |

**Features:**
- Fetches fundamentals via `yfinance` (`Ticker.info`)
- LLM layer writes a plain-English verdict: "Passes 5/6 Graham criteria. Main concern: high D/E."
- Pluggable strategy pattern — add a new `GrahamStrategy`, `LynchStrategy` class without touching core
- Earnings calendar integration: alerts N days before a tracked ticker's earnings date

**Telegram Commands:**
- `/fundamental <ticker> [--method graham]` — Run a full fundamental screen and return LLM verdict
- `/earnings [days=7]` — Show upcoming earnings dates for all watched stocks within N days
- `/screen --method graham` — Screen the entire stock watchlist and rank by score

**Scheduled Jobs:**
- Every Sunday 8 AM — run Graham screen on all watched stocks, send ranked results
- 3 days before any tracked earnings date — send an earnings alert

---

### ✅ `finance/trader/` — Algorithmic Trading & OMS (Active)

**Goal:** Complete paper and live execution system with strict risk management, position sizing, kill switches, and backtesting.

**Features:**
- Modular OMS supporting paper trading and live broker execution.
- Strict multi-layered risk management (max drawdowns, max order sizing, circuit breakers).
- Signal engine ingesting technical triggers into order placement.
- Complete backtesting pipeline with Sharpe, Sortino, max drawdown, and trade logs.

---

### 🗺 `finance/crypto/` — Crypto Intelligence (Planned)

**Goal:** Layer crypto-specific data on top of the core TA engine. Covers BTC dominance,
fear & greed index, and on-chain macro signals not available via yfinance.

**Data Sources:**
- **yfinance** — OHLCV price data for large-cap crypto tickers (`BTC-USD`, `ETH-USD`, etc.)
- **CoinGecko free API** — Market cap dominance, global market overview (rate limit: ~10-30 calls/min, cached with 10-min TTL)
- **alternative.me API** — Fear & Greed Index (`api.alternative.me/fng/`)

**Features:**
- **BTC Dominance Chart** — Line chart of BTC dominance % over 30/90 days, sent as image
- **Altcoin Season Index** — Derived from dominance data; flags when altcoin rotation is likely
- **Fear & Greed Index** — Daily snapshot (Extreme Fear → Greed); visual gauge image
- **Global Crypto Market Cap** — Total market cap + 24h change
- **On-Chain Macro Summary** — LLM-written paragraph synthesising dominance, F&G, and volume trends

**Telegram Commands:**
- `/dominance` — Send BTC dominance chart for the last 90 days
- `/feargreed` — Show today's fear & greed score and historical trend
- `/cryptomacro` — LLM-written macro summary of the current crypto market state
- `/altseason` — Report on altcoin season index and which sectors are rotating

**Scheduled Jobs:**
- Daily 8 AM — send fear & greed index snapshot to Telegram
- Every Sunday — send BTC dominance chart + on-chain macro summary

---

### 🗺 `finance/portfolio/` — Portfolio & Watchlist Tracker (Planned)

**Goal:** Track a personal portfolio with optional position sizing. When position size is
omitted, the ticker is treated as a pure watchlist entry (no P&L computed).

> **Separate table by design.** Portfolio uses its own `positions` table, distinct from
> the `watchlist_entries` table. A position references a watchlist ticker by symbol but
> stores independent data (qty, avg price, notes). This keeps concerns clean — `watchlist/`
> owns discovery and categorisation; `portfolio/` owns position tracking and P&L.

**Features:**
- **Positions Table (SQLite):** symbol, category, qty (nullable), avg_buy_price (nullable),
  date_added, notes
- **P&L Calculation:** If qty + avg_buy_price are set, computes unrealised P&L and % return
  in real time via yfinance spot price
- **Portfolio Snapshot:** Aggregated view — total invested, current value, overall return %,
  best/worst performer
- **Dual Mode:** Same command set works for both full portfolio entries and watchlist-only entries

**Telegram Commands:**
- `/padd <ticker> [--qty 10 --avg 150.00]` — Add to portfolio (omit `--qty`/`--avg` for watchlist mode)
- `/premove <ticker>` — Remove from portfolio
- `/pnl` — Show unrealised P&L for all full positions
- `/portfolio` — Full snapshot: positions table + aggregate stats
- `/position <ticker>` — Detailed view for a single holding

**Scheduled Jobs:**
- Daily 6:30 PM IST (market close) — send portfolio P&L summary for the day

---

### 🗺 `finance/reports/` — Scheduled Market Briefings (Planned)

**Goal:** Push-based, scheduled reports so the platform briefs you without you having to ask.
All reports pull live data at send time and pass through the `FinancialAnalystAgent` for
narrative generation.

**Report Types:**

| Report | Schedule | Content |
|---|---|---|
| Pre-Market Briefing | Mon–Fri 8:30 AM IST | Key index futures, top movers, fear & greed, day's earnings |
| End-of-Day Summary | Mon–Fri 6:30 PM IST | Index performance, portfolio P&L, any triggered TA signals |
| Weekly Digest | Sunday 9 AM IST | Weekly performance of all watched tickers, top TA signals, crypto macro |
| Monthly Portfolio Review | 1st of month 9 AM | Monthly P&L, best/worst performers, fundamental screen refresh |

**Telegram Commands:**
- `/briefing` — Force a pre-market briefing right now (any time)
- `/eod` — Force an end-of-day summary right now
- `/weekly` — Force a weekly digest right now

---

### 🗺 `finance/analyst/` — LLM Analyst Agent (Planned)

**Goal:** A `FinancialAnalystAgent` (PydanticAI) that synthesises TA + fundamental data
into plain-English analyst notes. This is the conversational layer — you can ask it
anything in natural language via Telegram free-text and it routes to the right tools.

**Architecture:**
- Registered as a PydanticAI agent with tool-calling capability
- **Tools available to the agent:**
  - `get_ta_summary(ticker, timeframe)` → calls `finance/technical/`
  - `get_fundamentals(ticker, method)` → calls `finance/fundamental/`
  - `get_crypto_macro()` → calls `finance/crypto/`
  - `get_portfolio_snapshot()` → calls `finance/portfolio/`
  - `get_price(ticker)` → live spot price via yfinance
- **Output:** A structured `AnalystNote` (Pydantic model) with fields:
  `verdict`, `bull_case`, `bear_case`, `key_risks`, `suggested_action`
- The agent is also invoked by all other `finance/` submodules to produce the
  narrative paragraph appended to every report and alert

**Telegram Commands (free-text, routed by the platform's assistant):**
- `"What do you think about AAPL right now?"` — TA + fundamental synthesis
- `"Is this a good time to buy gold?"` — commodity TA + macro context
- `"How is my portfolio doing vs the Nifty this week?"` — portfolio vs benchmark
- `/analyse <ticker> [--method graham]` — Structured command for a full analyst note

**Scheduled Jobs:**
- Called internally by `finance/reports/` — not independently scheduled

---


## Adding a New Module

To build any of these (or your own):

1. Create `app/modules/<domain>/<name>/`
2. Add `__init__.py` with the standard `register(bot_app, scheduler, authorized) -> dict` hook
3. Define SQLite models in `models.py` if persistence is needed
4. Export Telegram handlers in `commands.py` and scheduled jobs in `jobs.py`

See [MODULE_DEVELOPMENT.md](MODULE_DEVELOPMENT.md) for the full contract and code templates.
