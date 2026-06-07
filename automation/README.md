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
For detailed system flow, see [ARCHITECTURE.md](ARCHITECTURE.md).
Planning to build a new integration? See [MODULE_DEVELOPMENT.md](MODULE_DEVELOPMENT.md).
For upcoming features, see [ROADMAP.md](ROADMAP.md).
