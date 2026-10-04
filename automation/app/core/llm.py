"""
Shared LLM model registry — supports multiple tiers and automatic fallback.

Tiers:
  primary  — High-quality model for complex, multi-step tasks (blog pipeline).
             Configured via LLM_PROVIDER + LLM_MODEL.
  nano     — Fast, cheap model for simple tasks (chat assistant).
             Configured via LLM_NANO_PROVIDER + LLM_NANO_MODEL.
             Falls back to primary if not configured.
  fallback — Used automatically when primary returns a retryable error (503, 429, 524).
             Configured via LLM_FALLBACK_PROVIDER + LLM_FALLBACK_MODEL.
             If not configured, retryable errors are re-raised immediately.

Provider options (apply to any tier):
  gemini        — Google Gemini. Reads GEMINI_API_KEY automatically.
  openrouter    — OpenRouter. Reads OPENROUTER_API_KEY automatically.
  openai-compat — Any OpenAI-compatible REST endpoint. Requires LLM_BASE_URL + LLM_API_KEY.

Usage:
  from core.llm import get_model, run_with_fallback

  model  = get_model()            # primary (default)
  model  = get_model("nano")      # nano tier
  result = await run_with_fallback(agent, prompt)   # auto-fallback on 503/429/524
"""
import asyncio
import logging
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models import Model

from config import config

logger = logging.getLogger(__name__)

# Cache: tier name → built Model instance
_models: dict[str, Model] = {}

# HTTP status codes we consider transient / retryable
_RETRYABLE_CODES: frozenset[str] = frozenset({"503", "429", "524", "502", "529"})


# ── Model factory ─────────────────────────────────────────────────────────────

def _build_model(tier: str = "primary") -> Model:
    """Instantiate and return a Model for the given tier."""

    if tier == "nano" and config.llm_nano_model:
        provider   = (config.llm_nano_provider or config.llm_provider).lower()
        model_name = config.llm_nano_model

    elif tier == "fallback" and config.llm_fallback_model:
        provider   = (config.llm_fallback_provider or config.llm_provider).lower()
        model_name = config.llm_fallback_model

    else:
        # "primary" tier, or any unconfigured tier → use primary settings
        provider   = config.llm_provider.lower()
        model_name = config.llm_model

    if provider == "gemini":
        import os
        if config.gemini_api_key and "GEMINI_API_KEY" not in os.environ:
            os.environ["GEMINI_API_KEY"] = config.gemini_api_key
        from pydantic_ai.models.gemini import GeminiModel
        return GeminiModel(model_name)

    if provider == "openrouter":
        import os
        if config.openrouter_api_key and "OPENROUTER_API_KEY" not in os.environ:
            os.environ["OPENROUTER_API_KEY"] = config.openrouter_api_key
        from pydantic_ai.models.openrouter import OpenRouterModel
        return OpenRouterModel(model_name)

    if provider == "openai-compat":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider
        return OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(
                base_url=config.llm_base_url,
                api_key=config.llm_api_key,
            ),
        )

    raise ValueError(
        f"Unknown LLM provider '{provider}' for tier '{tier}'. "
        "Valid options: gemini, openrouter, openai-compat"
    )


def get_model(tier: str = "primary") -> Model:
    """
    Return the model instance for the given tier.
    Created once on first call, reused across all agents.

    Args:
        tier: "primary" | "nano" | "fallback"
    """
    if tier not in _models:
        _models[tier] = _build_model(tier)
    return _models[tier]


# ── Retryable error detection ────────────────────────────────────────────────

def is_retryable(exc: Exception) -> bool:
    """
    Return True if the exception looks like a transient provider error
    that is worth retrying with a different model.
    Checks the string representation for known HTTP status codes.
    """
    msg = str(exc)
    return any(code in msg for code in _RETRYABLE_CODES)


# ── Fallback-aware agent runner ───────────────────────────────────────────────

async def run_with_fallback(agent: Agent, prompt: str, **kwargs: Any):
    """
    Run `agent.run(prompt, **kwargs)` and, on a retryable provider error,
    transparently retry once with the configured fallback model.

    PydanticAI's `Agent.run()` accepts a `model=` override, so the agent's
    output_type and system_prompt are preserved — only the model swaps out.

    Args:
        agent:  Any PydanticAI Agent instance.
        prompt: The user prompt string.
        **kwargs: Forwarded to agent.run() (e.g. message_history=).

    Raises:
        The original exception if no fallback is configured or the fallback also fails.
    """
    try:
        return await agent.run(prompt, **kwargs)

    except Exception as primary_exc:
        if not is_retryable(primary_exc):
            raise

        if not config.llm_fallback_model:
            logger.error(
                "Retryable model error but LLM_FALLBACK_MODEL is not configured. "
                "Set LLM_FALLBACK_MODEL in .env to enable automatic failover."
            )
            raise

        fallback = get_model("fallback")
        logger.warning(
            "Primary model error (%s). Retrying with fallback model in 2s…",
            str(primary_exc)[:120],
        )
        await asyncio.sleep(2)

        try:
            return await agent.run(prompt, model=fallback, **kwargs)
        except Exception as fallback_exc:
            logger.error(
                "Fallback model also failed: %s", str(fallback_exc)[:120]
            )
            raise fallback_exc
