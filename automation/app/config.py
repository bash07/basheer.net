"""Centralised config loaded from environment / .env file."""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── LLM Provider ──────────────────────────────────────────────────────────
    # Select backend: gemini | openrouter | openai-compat
    llm_provider: str = os.environ.get("LLM_PROVIDER", "gemini")
    # Model name for the chosen provider (provider-specific format)
    llm_model: str = os.environ.get("LLM_MODEL", "gemini-2.5-flash")

    # Gemini (default provider)
    gemini_api_key: str = os.environ.get("GEMINI_API_KEY", "")

    # OpenRouter
    openrouter_api_key: str = os.environ.get("OPENROUTER_API_KEY", "")

    # Generic OpenAI-compatible endpoint
    llm_base_url: str = os.environ.get("LLM_BASE_URL", "")
    llm_api_key: str  = os.environ.get("LLM_API_KEY", "")

    # ── Nano tier (fast/cheap model for simple tasks) ─────────────────────────
    # Falls back to the primary tier if not set.
    # LLM_NANO_PROVIDER defaults to LLM_PROVIDER if left empty.
    llm_nano_provider: str = os.environ.get("LLM_NANO_PROVIDER", "")
    llm_nano_model: str    = os.environ.get("LLM_NANO_MODEL", "")

    # ── Fallback tier (auto-retry on 503 / 429 / 524) ────────────────────────
    # When the primary model returns a retryable error, the pipeline retries
    # once with this model. Leave blank to disable automatic failover.
    # LLM_FALLBACK_PROVIDER defaults to LLM_PROVIDER if left empty.
    llm_fallback_provider: str = os.environ.get("LLM_FALLBACK_PROVIDER", "")
    llm_fallback_model: str    = os.environ.get("LLM_FALLBACK_MODEL", "")

    # GitHub (blog module)
    github_token: str = os.environ.get("GITHUB_TOKEN", "")
    github_repo: str = os.environ.get("GITHUB_REPO", "bash07/basheer.net")
    blog_base_url: str = "https://basheer.net"

    # Telegram
    telegram_bot_token: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: int = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))

    # REST API & Web Dashboard
    webhook_secret: str = os.environ.get("WEBHOOK_SECRET", "")
    dashboard_secret_key: str = os.environ.get("DASHBOARD_SECRET_KEY", "basheer-net-dashboard-secret-salt-2026")
    dashboard_passphrase: str = os.environ.get("DASHBOARD_PASSPHRASE", os.environ.get("WEBHOOK_SECRET", "basheer2026!"))

    # Misc
    timezone: str = os.environ.get("GENERIC_TIMEZONE", "Asia/Kolkata")
    data_dir: str = "/app/data"

    def missing(self) -> list[str]:
        # Core platform requirements
        checks: dict[str, str] = {
            "TELEGRAM_BOT_TOKEN": self.telegram_bot_token,
            "TELEGRAM_CHAT_ID":   str(self.telegram_chat_id),
        }
        # Provider-specific key requirement
        provider = self.llm_provider.lower()
        if provider == "gemini":
            checks["GEMINI_API_KEY"] = self.gemini_api_key
        elif provider == "openrouter":
            checks["OPENROUTER_API_KEY"] = self.openrouter_api_key
        elif provider == "openai-compat":
            checks["LLM_BASE_URL"] = self.llm_base_url
            checks["LLM_API_KEY"]  = self.llm_api_key
        return [k for k, v in checks.items() if not v]


config = Config()
