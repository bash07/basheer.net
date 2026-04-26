"""
Typed Pydantic output schemas for the blog module.

By defining these as Pydantic models, PydanticAI forces the LLM to return
structured, validated data rather than free-form text. This eliminates JSON
parsing errors and provides full IDE type-safety downstream.
"""
from pydantic import BaseModel, Field


class ResearchBrief(BaseModel):
    overview: str = Field(description="Topic overview — what it is and why it matters")
    key_concepts: list[str] = Field(description="3-5 core concepts an engineer must understand")
    practical_examples: list[str] = Field(description="Real-world use cases or code scenarios")
    trends: list[str] = Field(description="Current trends in this space (2025-2026)")
    unique_angle: str = Field(description="The non-obvious insight that makes this post special")
    seo_keywords: list[str] = Field(description="5 specific SEO keywords for this topic")


class BlogPostDraft(BaseModel):
    title: str = Field(description="SEO-friendly title, 50-60 characters")
    description: str = Field(description="Compelling meta description, 150-160 characters")
    summary: str = Field(description="1-2 sentence summary for listing pages")
    tags: list[str] = Field(description="4-6 relevant tags")
    keywords: list[str] = Field(description="3-5 SEO keywords")
    content: str = Field(
        description=(
            "Full markdown body of the post (1500-2500 words). "
            "Use ## for main headings, ### for sub-headings. "
            "Include at least one code block. NO front matter."
        )
    )


class BlogIdea(BaseModel):
    topic: str = Field(description="Specific, clear blog post topic")
    category: str = Field(description="One of: AI, Embedded Systems, Software Engineering, Cloud & DevOps")
    angle: str = Field(description="The unique insight that makes this post worth reading")
    why_now: str = Field(description="Why this is timely in 2025-2026")


class BlogIdeas(BaseModel):
    """Wrapper so PydanticAI can return a list of ideas as a structured type."""
    ideas: list[BlogIdea]
