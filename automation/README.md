# AI Automation Server

A self-hosted, lightweight, and deeply extensible AI automation platform at `ai.yourdomain.com`. 

It uses a unified Telegram Bot as the central control panel and **PydanticAI** as the core intelligence engine. Purpose-built to run efficiently on small tier servers (e.g., Google Cloud `e2-micro` with 1GB RAM and 2 vCPUs).

## 🚀 Features
- **Pluggable Architecture:** Drop a new folder into `app/modules/` and it is auto-discovered at startup. Modules can be flat (`modules/<name>/`) or grouped under a domain (`modules/<domain>/<name>/`) — both layouts are supported simultaneously.
- **Telegram Unified Control Panel:** All commands, alerts, and feedback go through a single, authenticated Telegram bot (locked to your `TELEGRAM_CHAT_ID`).
- **Memory Efficient AI:** Uses `pydantic-ai` with direct Google Gemini API bindings (bypassing heavy abstraction layers like LangChain or CrewAI).
- **Embedded Infrastructure:** Zero external containers required. Uses a shared `SQLite` database and an internal asyncio queue.

## 📂 Active Modules

### 📝 Content (`modules/content/`)
*   **Blog (`content/blog`):** Replaces traditional CMS orchestration. Four sequential AI agents (Researcher, Writer, Editor, Publisher) create structured markdown and push directly to GitHub.

### 🛠 Ops (`modules/ops/`)
*   **Monitor (`ops/monitor`):** Real-time web service uptime monitor. Pings your configured sites every 5 minutes and flags `DOWN`, `RECOVERED`, or `SLOW` directly to Telegram.

### 💹 Finance (`modules/finance/`)
*   **Watchlist (`finance/watchlist`):** Multi-asset price tracking (US/IN stocks, crypto, forex, commodities) with threshold price alerts and daily morning briefs.
*   **Technical Analysis (`finance/technical`):** Multi-timeframe charts, EMAs (21/50/100/200), RSI divergence detection with swing validation, potential curling setups, and 30-min market scanners.
*   **Fundamental Analysis (`finance/fundamental`):** Benjamin Graham value investing screens, upcoming earnings alerts, and LLM-synthesized analyst verdicts.
*   **Trader (`finance/trader`):** Institutional Smart Money Concepts (SMC) scanner, HTF bias engines, order blocks, FVG, volume profile, paper broker, and automated OMS.

See [ROADMAP.md](ROADMAP.md) for all planned domains and submodules (`content/social`, `ops/analytics`, and more).

## ⚙️ Quickstart

1. **Configure Environment:**
    Copy the secrets template and fill in your Gemini API key, GitHub token, and Telegram credentials.
    ```bash
    cp .env.example .env
    nano .env
    ```

2. **Run:**
    The system is fully containerized with Docker and Caddy (handles automatic Let's Encrypt SSL).
    ```bash
    docker compose up -d --build
    ```

3. **Verify:**
    Message your Telegram bot the `/help` command to see the loaded modules and their commands.

## 📚 Documentation
- 🏛️ **System Architecture (All Modules):** [app/SYSTEM_ARCHITECTURE.md](app/SYSTEM_ARCHITECTURE.md) — Comprehensive architecture covering core engine, finance, blog, monitor, and ops.
- 📖 **Developer & Operator Manual:** [app/OPERATOR_AND_DEVELOPER_GUIDE.md](app/OPERATOR_AND_DEVELOPER_GUIDE.md) — Complete slash command reference & development recipes.
- 🎯 **Trading Engine & OMS Specification:** [app/modules/finance/trader/README_finance_trader.md](app/modules/finance/trader/README_finance_trader.md) — SMC scanner, dynamic indicator registry, and OMS roadmap.
- 💹 **Finance Domain Overview:** [app/modules/finance/README_finance.md](app/modules/finance/README_finance.md) — Central manual for watchlist, classical TA, and fundamental analysis.
- 📋 **Watchlist Submodule:** [app/modules/finance/watchlist/README_finance_watchlist.md](app/modules/finance/watchlist/README_finance_watchlist.md) — Ticker registry, batch pricing & price alerts.
- 📊 **Technical Analysis Submodule:** [app/modules/finance/technical/README_finance_technical.md](app/modules/finance/technical/README_finance_technical.md) — Multi-extrema divergence, indicators & charts.
- 📈 **Fundamental Analysis Submodule:** [app/modules/finance/fundamental/README_finance_fundamental.md](app/modules/finance/fundamental/README_finance_fundamental.md) — Graham value screens & AI verdicts.
- 🏗️ **Platform Infrastructure:** [ARCHITECTURE.md](ARCHITECTURE.md) & [DEPLOYMENT.md](DEPLOYMENT.md) — Container topology, Caddy SSL gateway, and GCP setup.
- 🔌 **Building New Modules:** [MODULE_DEVELOPMENT.md](MODULE_DEVELOPMENT.md) — Guide for creating new standalone automation modules.
- 🗺️ **Platform Roadmap:** [ROADMAP.md](ROADMAP.md) — Upcoming features and milestones.
