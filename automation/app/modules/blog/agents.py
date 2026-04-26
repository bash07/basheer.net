"""
PydanticAI agents for the blog pipeline — lazy initialization.

Agents are created on first use via getter functions rather than at module
import time. This prevents a failed import (e.g. missing env var) from
silently killing the entire blog module during startup.
"""
from pydantic_ai import Agent

from core.llm import get_model
from .schemas import BlogIdeas, BlogPostDraft, ResearchBrief

# ── Private singletons — None until first call ────────────────────────────────
_researcher: Agent | None = None
_writer: Agent | None = None
_editor: Agent | None = None
_ideas: Agent | None = None


def get_researcher() -> Agent:
    global _researcher
    if _researcher is None:
        _researcher = Agent(
            model=get_model("primary"),
            output_type=ResearchBrief,
            system_prompt=(
                "You are a Tech Blog Research Specialist with deep expertise in software engineering, "
                "AI, embedded systems, and cloud infrastructure. "
                "Produce a thorough, structured research brief that will help a writer create an "
                "outstanding technical blog post. Focus on finding the non-obvious angle and the most "
                "actionable, practical insights an engineer can immediately use."
            ),
        )
    return _researcher


def get_writer() -> Agent:
    global _writer
    if _writer is None:
        _writer = Agent(
            model=get_model("primary"),
            output_type=BlogPostDraft,
            system_prompt=(
                "You are an expert technical blog writer. Based on the provided research brief, "
                "write a complete, engaging blog post. "
                "Requirements: 1500-2500 words, ## for main headings, ### for sub-headings, "
                "at least one code block where relevant, conversational yet professional tone, "
                "a strong hook in the first paragraph, and actionable takeaways at the end. "
                "The content field must contain only the markdown body — no YAML front matter."
            ),
        )
    return _writer


def get_editor() -> Agent:
    global _editor
    if _editor is None:
        _editor = Agent(
            model=get_model("primary"),
            output_type=BlogPostDraft,
            system_prompt=(
                "You are a senior SEO editor and quality reviewer. "
                "Review the provided blog post draft and return an improved version. "
                "Check for: keyword-rich headings, compelling meta description (150-160 chars), "
                "clear sentence structure with no padding, technical accuracy, "
                "natural paragraph flow, an engaging opening, and a strong conclusion. "
                "Preserve the exact same JSON structure — only improve the content quality."
            ),
        )
    return _editor


def get_ideas_agent() -> Agent:
    global _ideas
    if _ideas is None:
        _ideas = Agent(
            model=get_model("primary"),
            output_type=BlogIdeas,
            system_prompt=(
                "You generate 5 original, timely blog post ideas for a technical blog covering "
                "AI, embedded systems, software engineering, and cloud/DevOps. "
                "Each idea must be specific (not generic), have a clear non-obvious angle, "
                "and be genuinely timely and relevant for 2025-2026."
            ),
        )
    return _ideas
