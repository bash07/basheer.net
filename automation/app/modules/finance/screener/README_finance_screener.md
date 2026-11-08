# Market Screener Submodule (`finance/screener/`)

> 📚 **Navigation:**
> - [Finance Domain Root](../README_finance.md)
> - [Watchlist Submodule](../watchlist/README_finance_watchlist.md)
> - [Algorithmic Trader & OMS](../trader/README_finance_trader.md)
> - [Fundamental Analysis Submodule](../fundamental/README_finance_fundamental.md)
> - [System Architecture](../../SYSTEM_ARCHITECTURE.md)

---

## 1. Overview & Purpose

The **Market Screener Submodule** (`modules/finance/screener/`) serves as the upstream candidate discovery and watchlist population engine for the finance domain. It systematically filters the broad Indian market universe (**Nifty 100** and Sectoral Indices) across 5 quantitative concepts (Concepts A through E), tags each candidate with its strategy affinity, and dynamically populates or rotates the active watchlist.

### Key Capabilities:
- **Fast Batch Data Ingestion**: Concurrently downloads 1y daily OHLCV bars across 100+ NSE symbols in a single vectorised request.
- **Concepts A–E Screening Suite**:
  - **Concept A (Weekly TA)**: Relative Strength & Trend Leaders (Minervini Stage 2 vs Nifty 50).
  - **Concept B (Weekly TA)**: Volatility Squeeze & Coiling (Bollinger Band Width, NR7, ATR drop).
  - **Concept C (Weekly TA)**: Institutional Volume Flow & RVOL Surges (Volume accumulation).
  - **Concept D (Monthly FA)**: Fundamental Quality & Moat (Warren Buffett / Peter Lynch scoring + 4Q margin check + SMA200 trend gate).
  - **Concept E (Dual TA/FA)**: Nifty Sector Momentum & Sector Leaders (20-day ROC ranking).
- **Strategy Gating & Watchlist Labeling**:
  - Automatically tags candidates with `source`, `cadence`, `concept_tags`, and explicit `strategy_types`.
  - Downstream trading bot ([`trader/jobs.py`](../trader/jobs.py)) only evaluates the assigned strategy types for that ticker.
- **Automated Lifecycle & Zero-Loss Pruning**:
  - Dynamic weekly entries expire in 7 days; monthly entries expire in 30 days.
  - User's manual `/watch` entries are marked `source="manual"` and remain completely permanent.

---

## 2. Concept-to-Strategy Mapping Matrix

| Concept | Screener Type & Cadence | Mathematical / Screening Gates | Downstream Strategy Targets |
| :--- | :--- | :--- | :--- |
| **Concept A: Relative Strength Leader** | **Weekly TA** | $\text{Price} > \text{EMA50} > \text{EMA200}$<br>Within 15% of 52-week High<br>3-month return outperforming Nifty 50 ($\text{RS} > 0$) | • `OBRetracementStrategy`<br>• `DisplacementFVGStrategy`<br>• `EMAPullbackStrategy`<br>• `CreepingDriftStrategy` |
| **Concept B: Volatility Squeeze** | **Weekly TA** | Bollinger Band Width at 6-month low<br>NR7 daily range<br>$\text{ATR}(14) / \text{ATR}(50) < 0.85$ | • `WyckoffAccumulationStrategy`<br>• `WyckoffDistributionStrategy`<br>• `BreakdownRetestStrategy`<br>• `DisplacementFVGStrategy` |
| **Concept C: Volume Flow Surge** | **Weekly TA** | $\text{RVOL} \ge 1.5\text{x}$ 20-day SMA<br>Up/Down Volume accumulation ratio $> 1.3$<br>Close in top 40% of bar range | • `LiquiditySweepFVGStrategy`<br>• `RangeLiquiditySweepStrategy`<br>• `OBRetracementStrategy` |
| **Concept D: Fundamental Quality** | **Monthly FA** | Buffett or Lynch score $\ge 4 / 6$<br>No 3-quarter margin compression warning<br>$\text{Price} > \text{SMA200}$ | • `EMAPullbackStrategy`<br>• `CreepingDriftStrategy`<br>• Fundamental Monthly Review |
| **Concept E: Sector Momentum** | **Dual (Weekly TA + Monthly FA)** | Top 2 Nifty sectors by 20-day ROC<br>Top momentum stocks within leading sectors | • `DisplacementFVGStrategy`<br>• `OBRetracementStrategy`<br>• `EMAPullbackStrategy` |

---

## 3. Submodule File Structure

```
automation/app/modules/finance/screener/
├── __init__.py           # Submodule registration & APScheduler job binding
├── schemas.py            # ScreenedCandidate, ScreenReport dataclasses
├── universes/            # Curated asset universes
│   ├── __init__.py
│   └── nifty100.py       # Nifty 100 constituents (.NS) & sector index mappings
├── filters/              # Quantitative filter calculators
│   ├── __init__.py
│   ├── technical.py      # Concepts A, B, and C
│   ├── sector.py         # Concept E
│   └── fundamental.py    # Concept D
├── fetcher.py            # Fast parallel yfinance batch downloader
├── populator.py          # Watchlist insertion, rotation, and TTL expiration
├── formatter.py          # Telegram markdown cards & digests
├── commands.py           # Telegram handlers (/screen_run, /screen_status, /screen_purge)
└── jobs.py               # APScheduler recurring jobs
```

---

## 4. Telegram Slash Command Reference

### 4.1 `/screen_run [weekly|monthly] [--dry-run]`
Triggers an immediate universe screening pass.
- **Weekly**: Runs Concepts A, B, C, and E, selects the top 15 candidates, and populates the watchlist with a 7-day TTL.
- **Monthly**: Runs Concepts D and E, selects the top 10 fundamental compounders, and populates with a 30-day TTL.
- **`--dry-run`**: Audits and outputs the report without modifying the watchlist.

### 4.2 `/screen_status`
Shows a breakdown of active watchlist entries:
- Permanent manual entries.
- Active weekly TA candidates and remaining TTL hours.
- Active monthly FA candidates.

### 4.3 `/screen_purge [weekly|monthly|all]`
Manually clears dynamic candidates (e.g., to force a fresh re-screen) while keeping all permanent manual watchlist entries untouched.

---

## 5. Scheduled Background Jobs

| Job Name | Schedule | Function |
| :--- | :--- | :--- |
| `finance_screener_weekly_ta` | **Sunday 07:00 AM IST** | Scans Nifty 100 via Concepts A, B, C, E; updates weekly candidates in Watchlist; sends Telegram report. |
| `finance_screener_monthly_fa` | **1st of Month 07:30 AM IST** | Scans Nifty 100 via Concepts D & E; updates monthly candidates in Watchlist; sends Telegram report. |
| `finance_screener_daily_purge` | **Daily 00:05 AM IST** | Cleans up any expired entries whose `expires_at` has passed. |
