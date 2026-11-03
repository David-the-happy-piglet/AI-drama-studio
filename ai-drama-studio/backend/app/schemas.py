from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class GenerationMode(StrEnum):
    TEXT_TO_VIDEO = "text_to_video"
    IMAGE_TO_VIDEO = "image_to_video"
    REFERENCE_TO_VIDEO = "reference_to_video"
    FIRST_LAST_FRAME = "first_last_frame"
    VIDEO_EXTENSION = "video_extension"


class ProjectCreate(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    settings_json: dict[str, Any] = Field(default_factory=dict)


class ProjectRead(ORMModel):
    id: str
    name: str
    description: str
    status: str
    settings_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class CharacterBase(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    gender: str = ""
    apparent_age: str = ""
    ethnicity: str = ""
    height: str = ""
    body_type: str = ""
    face_description: str = ""
    hairstyle: str = ""
    hair_color: str = ""
    eye_characteristics: str = ""
    distinctive_features: list[str] = Field(default_factory=list)
    default_makeup: str = ""
    personality: str = ""
    speaking_style: str = ""
    default_voice_id: str | None = None
    visual_style: str = ""
    negative_constraints: list[str] = Field(default_factory=list)
    master_reference_path: str | None = None


class CharacterCreate(CharacterBase):
    id: str | None = None


class CharacterUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    gender: str | None = None
    apparent_age: str | None = None
    ethnicity: str | None = None
    height: str | None = None
    body_type: str | None = None
    face_description: str | None = None
    hairstyle: str | None = None
    hair_color: str | None = None
    eye_characteristics: str | None = None
    distinctive_features: list[str] | None = None
    default_makeup: str | None = None
    personality: str | None = None
    speaking_style: str | None = None
    default_voice_id: str | None = None
    visual_style: str | None = None
    negative_constraints: list[str] | None = None
    master_reference_path: str | None = None
    status: str | None = None


class CharacterReferenceRead(ORMModel):
    additional_reference_paths: list[str]


class CharacterBindingRead(ORMModel):
    provider: str
    source_version: int
    external_task_id: str
    task_id: str | None
    element_id: str | None
    status: str
    error_message: str | None
    updated_at: datetime


class CharacterReferencesUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    master_reference_path: str = Field(min_length=1)
    additional_reference_paths: list[str] = Field(default_factory=list, max_length=3)


class KlingElementCreate(BaseModel):
    expected_version: int = Field(ge=1)
    element_name: str = Field(min_length=1, max_length=20)
    element_description: str = Field(min_length=1, max_length=100)
    confirmed: Literal[True]
    replace_existing: bool = False


class CharacterRead(CharacterBase, ORMModel):
    id: str
    project_id: str
    status: str
    version: int
    created_at: datetime
    updated_at: datetime
    reference_assets: CharacterReferenceRead | None = None
    provider_bindings: list[CharacterBindingRead] = Field(default_factory=list)


class OutfitCreate(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=160)
    description: str = ""
    components: list[str] = Field(default_factory=list)
    color_palette: list[str] = Field(default_factory=list)
    negative_constraints: list[str] = Field(default_factory=list)
    reference_paths: list[str] = Field(default_factory=list)


class OutfitRead(ORMModel):
    id: str
    character_id: str
    name: str
    description: str
    components: list[str]
    color_palette: list[str]
    negative_constraints: list[str]
    reference_paths: list[str]
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class ScriptCreate(BaseModel):
    id: str | None = None
    title: str = Field(min_length=1, max_length=240)
    source_idea: str = Field(min_length=1)
    genre: str = ""
    target_audience: str = ""
    episode_duration: int = Field(default=90, ge=1, le=7200)
    episode_count: int = Field(default=1, ge=1, le=1000)
    tone: str = ""
    pacing: str = ""
    platform: str = ""
    character_ids: list[str] = Field(default_factory=list)
    structured_content: dict[str, Any] = Field(default_factory=dict)


class ScriptRead(ScriptCreate, ORMModel):
    id: str
    project_id: str
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class ScriptUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=240)
    source_idea: str | None = Field(default=None, min_length=1)
    genre: str | None = None
    target_audience: str | None = None
    episode_duration: int | None = Field(default=None, ge=1, le=7200)
    episode_count: int | None = Field(default=None, ge=1, le=1000)
    tone: str | None = None
    pacing: str | None = None
    platform: str | None = None
    character_ids: list[str] | None = None
    structured_content: dict[str, Any] | None = None
    status: str | None = None


class EpisodeCreate(BaseModel):
    id: str | None = None
    script_id: str | None = None
    episode_number: int = Field(default=1, ge=1)
    title: str = Field(min_length=1, max_length=240)
    duration_target: int = Field(default=90, ge=1, le=7200)
    summary: str = ""


class EpisodeRead(ORMModel):
    id: str
    project_id: str
    script_id: str | None
    episode_number: int
    title: str
    duration_target: int
    summary: str
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class DialogueLine(BaseModel):
    character_id: str | None = None
    speaker: str | None = None
    text: str = Field(min_length=1)
    emotion: str | None = None


class SceneCreate(BaseModel):
    id: str | None = None
    order_index: int = Field(ge=1)
    location_id: str | None = None
    location_description: str = ""
    time_of_day: str = ""
    character_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    dialogue: list[DialogueLine] = Field(default_factory=list)


class SceneRead(ORMModel):
    id: str
    episode_id: str
    order_index: int
    location_id: str | None
    location_description: str
    time_of_day: str
    character_ids: list[str]
    summary: str
    dialogue: list[dict[str, Any]]
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class SceneUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    order_index: int | None = Field(default=None, ge=1)
    location_id: str | None = None
    location_description: str | None = None
    time_of_day: str | None = None
    character_ids: list[str] | None = None
    summary: str | None = None
    dialogue: list[DialogueLine] | None = None
    status: str | None = None


class ShotCharacter(BaseModel):
    character_id: str
    outfit_id: str | None = None


class ShotCreate(BaseModel):
    id: str | None = None
    order_index: int = Field(ge=1)
    duration: float = Field(gt=0, le=60)
    characters: list[ShotCharacter] = Field(default_factory=list)
    location_id: str | None = None
    shot_type: str = Field(min_length=1, max_length=120)
    camera_angle: str = "eye_level"
    camera_movement: str = "static"
    composition: str = ""
    action: str = ""
    emotion: str = ""
    dialogue: str = ""
    visual_description: str = Field(min_length=1)
    continuity_requirements: list[str] = Field(default_factory=list)
    reference_requirements: list[str] = Field(default_factory=list)
    preferred_model: str | None = None
    manual_provider_override: str | None = None
    generation_prompt: str | None = None
    negative_prompt: str | None = None
    generation_mode: GenerationMode = GenerationMode.IMAGE_TO_VIDEO

    @field_validator("manual_provider_override")
    @classmethod
    def validate_provider_override(cls, value: str | None) -> str | None:
        if value is not None and value not in {"seedance", "kling", "minimax", "mock"}:
            raise ValueError(
                "manual_provider_override must be seedance, kling, minimax, or mock"
            )
        return value


class ShotRead(ORMModel):
    id: str
    scene_id: str
    order_index: int
    duration: float
    characters: list[dict[str, Any]]
    location_id: str | None
    shot_type: str
    camera_angle: str
    camera_movement: str
    composition: str
    action: str
    emotion: str
    dialogue: str
    visual_description: str
    continuity_requirements: list[str]
    reference_requirements: list[str]
    preferred_model: str | None
    manual_provider_override: str | None
    generation_prompt: str | None
    negative_prompt: str | None
    generation_mode: str
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class ShotUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    order_index: int | None = Field(default=None, ge=1)
    duration: float | None = Field(default=None, gt=0, le=60)
    characters: list[ShotCharacter] | None = None
    location_id: str | None = None
    shot_type: str | None = Field(default=None, min_length=1, max_length=120)
    camera_angle: str | None = None
    camera_movement: str | None = None
    composition: str | None = None
    action: str | None = None
    emotion: str | None = None
    dialogue: str | None = None
    visual_description: str | None = Field(default=None, min_length=1)
    continuity_requirements: list[str] | None = None
    reference_requirements: list[str] | None = None
    preferred_model: str | None = None
    manual_provider_override: str | None = None
    generation_prompt: str | None = None
    negative_prompt: str | None = None
    generation_mode: GenerationMode | None = None
    status: str | None = None

    @field_validator("manual_provider_override")
    @classmethod
    def validate_provider_override(cls, value: str | None) -> str | None:
        if value is not None and value not in {"seedance", "kling", "minimax", "mock"}:
            raise ValueError(
                "manual_provider_override must be seedance, kling, minimax, or mock"
            )
        return value


class SceneStoryboard(SceneRead):
    shots: list[ShotRead]


class StoryboardRead(EpisodeRead):
    scenes: list[SceneStoryboard]


class GenerationCreate(BaseModel):
    request_token: UUID
    confirmed: Literal[True]
    keyframe_selection_token: str | None = Field(default=None, max_length=64)
    provider: str | None = None
    model: str | None = None
    prompt: str | None = None
    negative_prompt: str | None = None
    references: list[str] = Field(default_factory=list)
    reference_videos: list[str] = Field(default_factory=list)
    reference_audios: list[str] = Field(default_factory=list)
    seed: int | None = None
    duration: float | None = Field(default=None, gt=0, le=60)
    resolution: str | None = None
    aspect_ratio: str | None = None
    first_frame: str | None = None
    last_frame: str | None = None
    api_parameters: dict[str, Any] = Field(default_factory=dict)
    use_character_bindings: bool = True

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str | None) -> str | None:
        if value is not None and value not in {"seedance", "kling", "minimax", "mock"}:
            raise ValueError("provider must be seedance, kling, minimax, or mock")
        return value


class GenerationRead(ORMModel):
    id: str
    project_id: str
    episode_id: str
    scene_id: str
    shot_id: str
    request_token: str
    provider: str
    model: str
    prompt: str
    negative_prompt: str
    references: list[str]
    seed: int | None
    duration: float | None
    resolution: str | None
    aspect_ratio: str | None
    api_parameters: dict[str, Any]
    request_snapshot: dict[str, Any]
    review_warning: str | None = None
    provider_response: dict[str, Any]
    external_id: str | None
    status: str
    retry_count: int
    generation_cost: float | None
    qa_result: dict[str, Any] | None
    output_path: str | None
    output_sha256: str | None
    output_bytes: int | None
    error_message: str | None
    submitted_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
