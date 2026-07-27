from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class AudienceProfile(Base):
    __tablename__ = "audience_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    age_range: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text, default="")
    interests_json: Mapped[str] = mapped_column(Text, default="[]")
    writing_style: Mapped[str] = mapped_column(Text, default="")
    title_preferences_json: Mapped[str] = mapped_column(Text, default="[]")
    article_length: Mapped[int] = mapped_column(Integer, default=1200)
    image_style: Mapped[str] = mapped_column(Text, default="")
    prohibited_topics_json: Mapped[str] = mapped_column(Text, default="[]")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )

    articles: Mapped[list["Article"]] = relationship(back_populates="audience")


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    audience_profile_id: Mapped[int] = mapped_column(
        ForeignKey("audience_profiles.id"),
        index=True,
    )
    topic: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(100), default="日常养生")
    target_length: Mapped[int] = mapped_column(Integer, default=1200)
    title_candidates_json: Mapped[str] = mapped_column(Text, default="[]")
    selected_title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text, default="")
    content_json: Mapped[str] = mapped_column(Text)
    content_html: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="generated", index=True)
    llm_provider: Mapped[str] = mapped_column(String(80), default="mock")
    model_name: Mapped[str] = mapped_column(String(120), default="")
    prompt_version: Mapped[str] = mapped_column(String(40), default="v1")
    safety_flags_json: Mapped[str] = mapped_column(Text, default="[]")
    wechat_media_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    wechat_publish_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    audience: Mapped[AudienceProfile] = relationship(back_populates="articles")
    assets: Mapped[list["Asset"]] = relationship(
        back_populates="article",
        cascade="all, delete-orphan",
    )
    generation_runs: Mapped[list["GenerationRun"]] = relationship(
        back_populates="article",
        cascade="all, delete-orphan",
    )
    metrics: Mapped[list["ArticleMetricDaily"]] = relationship(
        back_populates="article",
        cascade="all, delete-orphan",
    )


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    asset_type: Mapped[str] = mapped_column(String(40), default="inline")
    position: Mapped[int] = mapped_column(Integer, default=0)
    prompt: Mapped[str] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_kind: Mapped[str] = mapped_column(String(40), default="generated")
    source_page_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_author: Mapped[str | None] = mapped_column(Text, nullable=True)
    license_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    license_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    wechat_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    wechat_media_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )

    article: Mapped[Article] = relationship(back_populates="assets")


class GenerationRun(Base):
    __tablename__ = "generation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id"),
        nullable=True,
        index=True,
    )
    step: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(120), default="")
    input_text: Mapped[str] = mapped_column(Text)
    output_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="completed")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )

    article: Mapped[Article | None] = relationship(back_populates="generation_runs")


class PublishJob(Base):
    __tablename__ = "publish_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    job_type: Mapped[str] = mapped_column(String(30), default="draft")
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    wechat_media_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    wechat_publish_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )


class ArticleMetricDaily(Base):
    __tablename__ = "article_metrics_daily"
    __table_args__ = (UniqueConstraint("article_id", "metric_date", name="uq_article_metric_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    metric_date: Mapped[date] = mapped_column(Date)
    read_count: Mapped[int] = mapped_column(Integer, default=0)
    read_users: Mapped[int] = mapped_column(Integer, default=0)
    share_count: Mapped[int] = mapped_column(Integer, default=0)
    share_users: Mapped[int] = mapped_column(Integer, default=0)
    raw_json: Mapped[str] = mapped_column(Text, default="{}")

    article: Mapped[Article] = relationship(back_populates="metrics")


class ContentStrategy(Base):
    __tablename__ = "content_strategies"

    id: Mapped[int] = mapped_column(primary_key=True)
    audience_profile_id: Mapped[int] = mapped_column(
        ForeignKey("audience_profiles.id"),
        index=True,
    )
    category: Mapped[str] = mapped_column(String(100))
    title_pattern: Mapped[str] = mapped_column(Text, default="")
    article_structure: Mapped[str] = mapped_column(Text, default="")
    image_style: Mapped[str] = mapped_column(Text, default="")
    target_length: Mapped[int] = mapped_column(Integer, default=1200)
    performance_score: Mapped[float] = mapped_column(Float, default=0.0)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AutomationPlan(Base):
    __tablename__ = "automation_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="每日内容计划")
    audience_profile_id: Mapped[int] = mapped_column(
        ForeignKey("audience_profiles.id"),
        index=True,
    )
    directions: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(100), default="日常养生")
    provider: Mapped[str] = mapped_column(String(40), default="deepseek")
    run_time: Mapped[str] = mapped_column(String(5), default="06:30")
    timezone: Mapped[str] = mapped_column(String(80), default="Asia/Shanghai")
    title_count: Mapped[int] = mapped_column(Integer, default=5)
    image_count: Mapped[int] = mapped_column(Integer, default=3)
    target_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    headline_intensity: Mapped[int] = mapped_column(Integer, default=4)
    humanize: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_upload_draft: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )

    audience: Mapped[AudienceProfile] = relationship()
    runs: Mapped[list["AutomationRun"]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
    )


class AutomationRun(Base):
    __tablename__ = "automation_runs"
    __table_args__ = (
        UniqueConstraint(
            "automation_plan_id",
            "scheduled_for",
            name="uq_automation_plan_schedule",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    automation_plan_id: Mapped[int] = mapped_column(
        ForeignKey("automation_plans.id"),
        index=True,
    )
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id"),
        nullable=True,
        index=True,
    )
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(40),
        default="pending",
        index=True,
    )
    topic: Mapped[str | None] = mapped_column(String(300), nullable=True)
    topic_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )

    plan: Mapped[AutomationPlan] = relationship(back_populates="runs")
    article: Mapped[Article | None] = relationship()
