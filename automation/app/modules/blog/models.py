"""Blog module DB models."""
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, DateTime, Text
from core.db import Base


class BlogPost(Base):
    __tablename__ = "blog_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    slug: Mapped[str] = mapped_column(String(300), unique=True)
    category: Mapped[str] = mapped_column(String(100), default="Technology")
    url: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="published")  # published | failed
    published_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
