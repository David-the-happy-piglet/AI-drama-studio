from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AudienceBase(BaseModel):
    slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9-]+$")
    name: str = Field(min_length=2, max_length=120)
    age_range: str = Field(min_length=2, max_length=40)
    description: str = ""
    interests: list[str] = Field(default_factory=list)
    writing_style: str = ""
    title_preferences: list[str] = Field(default_factory=list)
    article_length: int = Field(default=1200, ge=300, le=5000)
    image_style: str = ""
    prohibited_topics: list[str] = Field(default_factory=list)
    enabled: bool = True


class AudienceCreate(AudienceBase):
    pass


class AudienceUpdate(BaseModel):
    name: str | None = None
    age_range: str | None = None
    description: str | None = None
    interests: list[str] | None = None
    writing_style: str | None = None
    title_preferences: list[str] | None = None
    article_length: int | None = Field(default=None, ge=300, le=5000)
    image_style: str | None = None
    prohibited_topics: list[str] | None = None
    enabled: bool | None = None


class AudienceRead(AudienceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class ArticleGenerateRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=300)
    audience_slug: str = "senior-wellness"
    category: str = Field(default="日常养生", min_length=2, max_length=100)
    title_count: int = Field(default=5, ge=3, le=10)
    image_count: int = Field(default=3, ge=1, le=6)
    target_length: int | None = Field(default=None, ge=300, le=5000)
    headline_intensity: int = Field(default=4, ge=1, le=5)
    humanize: bool = True
    provider: Literal["mock", "compatible", "deepseek", "minimax", "kimi"] | None = None


class ArticleComparisonRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=300)
    audience_slug: str = "senior-wellness"
    category: str = Field(default="日常养生", min_length=2, max_length=100)
    title_count: int = Field(default=5, ge=3, le=10)
    image_count: int = Field(default=3, ge=1, le=6)
    target_length: int | None = Field(default=None, ge=300, le=5000)
    headline_intensity: int = Field(default=4, ge=1, le=5)
    humanize: bool = True
    providers: list[Literal["mock", "compatible", "deepseek", "minimax", "kimi"]] = Field(
        default_factory=lambda: ["deepseek", "minimax", "kimi"], min_length=1
    )


class AssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    asset_type: str
    position: int
    prompt: str
    local_path: str | None
    source_url: str | None
    source_kind: str
    source_page_url: str | None
    source_author: str | None
    license_name: str | None
    license_url: str | None
    wechat_url: str | None


class ArticleRead(BaseModel):
    id: int
    audience_profile_id: int
    topic: str
    category: str
    target_length: int
    title_candidates: list[str]
    selected_title: str
    summary: str
    content: dict[str, Any]
    content_html: str
    status: str
    llm_provider: str
    model_name: str
    prompt_version: str
    safety_flags: list[str]
    wechat_media_id: str | None
    wechat_publish_id: str | None
    created_at: datetime
    published_at: datetime | None
    assets: list[AssetRead] = Field(default_factory=list)


class MetricCreate(BaseModel):
    metric_date: date
    read_count: int = Field(default=0, ge=0)
    read_users: int = Field(default=0, ge=0)
    share_count: int = Field(default=0, ge=0)
    share_users: int = Field(default=0, ge=0)
    raw: dict[str, Any] = Field(default_factory=dict)


class PublishResult(BaseModel):
    article_id: int
    status: str
    media_id: str | None = None
    publish_id: str | None = None
    message: str


class AutomationPlanBase(BaseModel):
    name: str = Field(default="每日内容计划", min_length=2, max_length=120)
    audience_slug: str = "senior-wellness"
    directions: str = Field(min_length=2, max_length=5000)
    category: str = Field(default="日常养生", min_length=2, max_length=100)
    provider: Literal["mock", "compatible", "deepseek", "minimax", "kimi"] = "deepseek"
    run_time: str = Field(default="06:30", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: str = Field(default="Asia/Shanghai", min_length=3, max_length=80)
    title_count: int = Field(default=5, ge=3, le=10)
    image_count: int = Field(default=3, ge=1, le=6)
    target_length: int | None = Field(default=None, ge=300, le=5000)
    headline_intensity: int = Field(default=4, ge=1, le=5)
    humanize: bool = True
    auto_upload_draft: bool = True
    enabled: bool = True


class AutomationPlanCreate(AutomationPlanBase):
    pass


class AutomationPlanUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    audience_slug: str | None = None
    directions: str | None = Field(default=None, min_length=2, max_length=5000)
    category: str | None = Field(default=None, min_length=2, max_length=100)
    provider: Literal["mock", "compatible", "deepseek", "minimax", "kimi"] | None = None
    run_time: str | None = Field(
        default=None,
        pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$",
    )
    timezone: str | None = Field(default=None, min_length=3, max_length=80)
    title_count: int | None = Field(default=None, ge=3, le=10)
    image_count: int | None = Field(default=None, ge=1, le=6)
    target_length: int | None = Field(default=None, ge=300, le=5000)
    headline_intensity: int | None = Field(default=None, ge=1, le=5)
    humanize: bool | None = None
    auto_upload_draft: bool | None = None
    enabled: bool | None = None


class AutomationPlanRead(AutomationPlanBase):
    id: int
    audience_profile_id: int
    created_at: datetime
    updated_at: datetime


class AutomationRunRead(BaseModel):
    id: int
    automation_plan_id: int
    article_id: int | None
    scheduled_for: datetime
    status: str
    topic: str | None
    topic_rationale: str | None
    error_message: str | None
    retry_count: int
    next_attempt_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
