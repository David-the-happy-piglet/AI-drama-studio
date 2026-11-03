from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="active", index=True)
    settings_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    characters: Mapped[list[Character]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    scripts: Mapped[list[Script]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    episodes: Mapped[list[Episode]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Character(TimestampMixin, Base):
    __tablename__ = "characters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    gender: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    apparent_age: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    ethnicity: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    height: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    body_type: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    face_description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    hairstyle: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    hair_color: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    eye_characteristics: Mapped[str] = mapped_column(Text, default="", nullable=False)
    distinctive_features: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False
    )
    default_makeup: Mapped[str] = mapped_column(Text, default="", nullable=False)
    personality: Mapped[str] = mapped_column(Text, default="", nullable=False)
    speaking_style: Mapped[str] = mapped_column(Text, default="", nullable=False)
    default_voice_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    visual_style: Mapped[str] = mapped_column(Text, default="", nullable=False)
    negative_constraints: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False
    )
    master_reference_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    project: Mapped[Project] = relationship(back_populates="characters")
    outfits: Mapped[list[Outfit]] = relationship(
        back_populates="character", cascade="all, delete-orphan"
    )
    reference_assets: Mapped[CharacterReferenceAssets | None] = relationship(
        cascade="all, delete-orphan", uselist=False
    )
    provider_bindings: Mapped[list[CharacterProviderBinding]] = relationship(
        cascade="all, delete-orphan"
    )


class CharacterReferenceAssets(TimestampMixin, Base):
    """Additive table: existing character rows need no destructive migration."""

    __tablename__ = "character_reference_assets"

    character_id: Mapped[str] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), primary_key=True
    )
    additional_reference_paths: Mapped[list] = mapped_column(JSON, default=list)


class CharacterImageJob(TimestampMixin, Base):
    __tablename__ = "character_image_jobs"
    __table_args__ = (
        UniqueConstraint("character_id", "request_token"),
        # NULL slots allow multiple finished batches, but only one unresolved submission.
        UniqueConstraint("character_id", "active_slot"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    character_id: Mapped[str] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    source_version: Mapped[int] = mapped_column(Integer)
    request_token: Mapped[str] = mapped_column(String(64))
    account_scope: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(80), default="kling-v3")
    prompt: Mapped[str] = mapped_column(Text)
    negative_prompt: Mapped[str] = mapped_column(Text, default="")
    count: Mapped[int] = mapped_column(Integer)
    resolution: Mapped[str] = mapped_column(String(20))
    aspect_ratio: Mapped[str] = mapped_column(String(20))
    task_id: Mapped[str | None] = mapped_column(String(240), nullable=True)
    status: Mapped[str] = mapped_column(String(40))
    active_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Remote signed URLs stay backend-only. UI sees only local asset paths and errors.
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    refresh_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    refresh_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    selection_history: Mapped[list] = mapped_column(JSON, default=list)
    input_snapshot: Mapped[CharacterImageInput | None] = relationship(
        cascade="all, delete-orphan", uselist=False
    )


class CharacterImageInput(Base):
    """Additive request evidence; absent on legacy, text-only jobs."""

    __tablename__ = "character_image_inputs"
    job_id: Mapped[str] = mapped_column(
        ForeignKey("character_image_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    mode: Mapped[str] = mapped_column(String(40))
    effective_prompt: Mapped[str] = mapped_column(Text)
    reference_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)


class CharacterProviderBinding(TimestampMixin, Base):
    __tablename__ = "character_provider_bindings"

    character_id: Mapped[str] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(40), primary_key=True)
    # Never expose this fingerprint or credentials in API response schemas.
    account_scope: Mapped[str] = mapped_column(String(64), nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    external_task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(240), nullable=True)
    element_id: Mapped[str | None] = mapped_column(String(240), nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class Outfit(TimestampMixin, Base):
    __tablename__ = "outfits"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    character_id: Mapped[str] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    components: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    color_palette: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    negative_constraints: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False
    )
    reference_paths: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    character: Mapped[Character] = relationship(back_populates="outfits")


class Script(TimestampMixin, Base):
    __tablename__ = "scripts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    source_idea: Mapped[str] = mapped_column(Text, nullable=False)
    genre: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    target_audience: Mapped[str] = mapped_column(Text, default="", nullable=False)
    episode_duration: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    episode_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    tone: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    pacing: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    platform: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    character_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    structured_content: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    project: Mapped[Project] = relationship(back_populates="scripts")
    episodes: Mapped[list[Episode]] = relationship(back_populates="script")


class Episode(TimestampMixin, Base):
    __tablename__ = "episodes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    script_id: Mapped[str | None] = mapped_column(
        ForeignKey("scripts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    episode_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    duration_target: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    project: Mapped[Project] = relationship(back_populates="episodes")
    script: Mapped[Script | None] = relationship(back_populates="episodes")
    scenes: Mapped[list[Scene]] = relationship(
        back_populates="episode",
        cascade="all, delete-orphan",
        order_by="Scene.order_index",
    )


class Scene(TimestampMixin, Base):
    __tablename__ = "scenes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    episode_id: Mapped[str] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), index=True
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    location_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    location_description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    time_of_day: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    character_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    dialogue: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    episode: Mapped[Episode] = relationship(back_populates="scenes")
    shots: Mapped[list[Shot]] = relationship(
        back_populates="scene",
        cascade="all, delete-orphan",
        order_by="Shot.order_index",
    )


class SceneAsset(TimestampMixin, Base):
    __tablename__ = "scene_assets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    master_reference_path: Mapped[str] = mapped_column(Text)
    additional_reference_paths: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)


class SceneAssetBinding(TimestampMixin, Base):
    __tablename__ = "scene_asset_bindings"
    scene_id: Mapped[str] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), primary_key=True
    )
    asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("scene_assets.id"), nullable=True, index=True
    )


class Shot(TimestampMixin, Base):
    __tablename__ = "shots"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    scene_id: Mapped[str] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), index=True
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    duration: Mapped[float] = mapped_column(Float, nullable=False)
    characters: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    location_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    shot_type: Mapped[str] = mapped_column(String(120), nullable=False)
    camera_angle: Mapped[str] = mapped_column(String(120), default="eye_level")
    camera_movement: Mapped[str] = mapped_column(String(160), default="static")
    composition: Mapped[str] = mapped_column(Text, default="", nullable=False)
    action: Mapped[str] = mapped_column(Text, default="", nullable=False)
    emotion: Mapped[str] = mapped_column(Text, default="", nullable=False)
    dialogue: Mapped[str] = mapped_column(Text, default="", nullable=False)
    visual_description: Mapped[str] = mapped_column(Text, nullable=False)
    continuity_requirements: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False
    )
    reference_requirements: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False
    )
    preferred_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    manual_provider_override: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    generation_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    negative_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_mode: Mapped[str] = mapped_column(
        String(40), default="image_to_video", nullable=False
    )
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    scene: Mapped[Scene] = relationship(back_populates="shots")
    generations: Mapped[list[Generation]] = relationship(
        back_populates="shot", cascade="all, delete-orphan"
    )


class ShotImageJob(TimestampMixin, Base):
    __tablename__ = "shot_image_jobs"
    __table_args__ = (
        UniqueConstraint("shot_id", "request_token"),
        UniqueConstraint("shot_id", "active_slot"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    shot_id: Mapped[str] = mapped_column(
        ForeignKey("shots.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    source_version: Mapped[int] = mapped_column(Integer)
    context_hash: Mapped[str] = mapped_column(String(64))
    request_token: Mapped[str] = mapped_column(String(64))
    account_scope: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(80))
    prompt: Mapped[str] = mapped_column(Text)
    negative_prompt: Mapped[str] = mapped_column(Text, default="")
    effective_prompt: Mapped[str] = mapped_column(Text)
    references_snapshot: Mapped[list] = mapped_column(JSON, default=list)
    count: Mapped[int] = mapped_column(Integer)
    resolution: Mapped[str] = mapped_column(String(20))
    aspect_ratio: Mapped[str] = mapped_column(String(20))
    task_id: Mapped[str | None] = mapped_column(String(240), nullable=True)
    status: Mapped[str] = mapped_column(String(40))
    active_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    refresh_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    refresh_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ShotKeyframe(TimestampMixin, Base):
    __tablename__ = "shot_keyframes"
    shot_id: Mapped[str] = mapped_column(
        ForeignKey("shots.id", ondelete="CASCADE"), primary_key=True
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("shot_image_jobs.id"))
    candidate_index: Mapped[int] = mapped_column(Integer)
    selection_token: Mapped[str] = mapped_column(String(64))
    shot_version: Mapped[int] = mapped_column(Integer)
    context_hash: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))


class ProductionBatch(TimestampMixin, Base):
    __tablename__ = "production_batches"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    episode_id: Mapped[str] = mapped_column(ForeignKey("episodes.id"), index=True)
    request_token: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(20))
    options: Mapped[dict] = mapped_column(JSON, default=dict)


class ProductionBatchItem(TimestampMixin, Base):
    __tablename__ = "production_batch_items"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("production_batches.id"), index=True
    )
    shot_id: Mapped[str] = mapped_column(ForeignKey("shots.id"), index=True)
    order_index: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), index=True)
    active_slot: Mapped[str | None] = mapped_column(
        String(100), unique=True, nullable=True
    )
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class Generation(TimestampMixin, Base):
    __tablename__ = "generations"
    __table_args__ = (UniqueConstraint("shot_id", "request_token"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    episode_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    scene_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    shot_id: Mapped[str] = mapped_column(
        ForeignKey("shots.id", ondelete="CASCADE"), index=True
    )
    request_token: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    model: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    negative_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    references: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(40), nullable=True)
    aspect_ratio: Mapped[str | None] = mapped_column(String(40), nullable=True)
    api_parameters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    request_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    provider_response: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    external_id: Mapped[str | None] = mapped_column(
        String(240), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(40), default="created", index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    generation_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    qa_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    shot: Mapped[Shot] = relationship(back_populates="generations")

    @property
    def review_warning(self) -> str | None:
        if self.shot.status == "archived" or self.shot.scene.status == "archived":
            return "镜头已移出当前分镜，历史素材保留"
        if self.shot.scene.episode.status == "revising":
            return "本集正在修订，素材待复核"
        version = (self.request_snapshot or {}).get("metadata", {}).get("shot_version")
        if version is not None and version != self.shot.version:
            return "镜头已修改，旧素材需复核"
        return None
