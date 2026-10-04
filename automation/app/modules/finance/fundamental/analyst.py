"""LLM-powered fundamental verdict + template fallback.

Uses the platform's PydanticAI/Gemini setup for a concise plain-English
verdict. Falls back to a deterministic template if the LLM is unavailable.
"""
import logging
from .strategies.base import StrategyResult
from .fetcher import FundamentalsData

logger = logging.getLogger(__name__)


async def generate_verdict(data: FundamentalsData, result: StrategyResult) -> str:
    """Return a plain-English 3-4 sentence verdict for the screening result.

    Attempts LLM first; returns template verdict on any error.
    """
    try:
        return await _llm_verdict(data, result)
    except Exception as e:
        logger.warning("[fundamental] LLM verdict failed (%s): %s", data.symbol, e)
        return _template_verdict(result)


async def _llm_verdict(data: FundamentalsData, result: StrategyResult) -> str:
    from pydantic_ai import Agent
    from config import config

    lines = []
    for c in result.criteria:
        status = "PASS" if c.passed else ("FAIL" if c.passed is False else "N/A")
        lines.append(f"  {c.name}: {c.actual} (threshold {c.threshold}) [{status}]")

    prompt = (
        f"Stock: {data.symbol} — {data.display_name}\n"
        f"Sector: {data.sector or 'Unknown'} | Industry: {data.industry or 'Unknown'}\n"
        f"Method: {result.method.title()} strategy\n"
        f"Score: {result.score}/{result.total} criteria passed "
        f"({result.max_score - result.total} had no data available)\n\n"
        "Criteria:\n" + "\n".join(lines) + "\n\n"
        "Write a concise 3-4 sentence fundamental analysis verdict. "
        "State the score, highlight the biggest strength, identify the main concern, "
        "and give a one-line investment takeaway. "
        "Be direct and factual. Plain text only — no markdown, no bullet points."
    )

    from core.llm import get_model

    agent = Agent(
        model=get_model("nano") if config.llm_nano_model else get_model("primary"),
        system_prompt=(
            "You are a quantitative fundamental analyst. "
            "Your verdicts are concise, factual, and data-driven. "
            "Frame everything as screening results — never give investment advice."
        ),
    )
    response = await agent.run(prompt)
    return response.output.strip()


def _template_verdict(result: StrategyResult) -> str:
    """Deterministic fallback when LLM is unavailable."""
    if result.total == 0:
        return "Insufficient data to evaluate this ticker against the criteria."

    quality = "strong" if result.pct >= 80 else ("moderate" if result.pct >= 50 else "weak")
    fail_str = ""
    if result.failed_criteria:
        names = ", ".join(c.name for c in result.failed_criteria)
        fail_str = f" Main concerns: {names}."
    na_str = ""
    if result.na_criteria:
        na_str = f" {len(result.na_criteria)} criteria lacked data."

    return (
        f"Passes {result.score}/{result.total} {result.method.title()} criteria — "
        f"{quality} fundamental profile.{fail_str}{na_str}"
    )
