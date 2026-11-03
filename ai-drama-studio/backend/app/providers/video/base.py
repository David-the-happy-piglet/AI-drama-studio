from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ProviderError(RuntimeError):
    """Base exception raised by a video provider adapter."""


class ProviderConfigurationError(ProviderError):
    """The adapter cannot safely call a provider until official config is supplied."""


class ProviderResponseError(ProviderError):
    """The provider result is ambiguous and must not be submitted again automatically."""


class ProviderRequestRejectedError(ProviderResponseError):
    """The provider definitively rejected the request before accepting a job."""


class VideoGenerationRequest(BaseModel):
    generation_id: str
    prompt: str = Field(min_length=1)
    negative_prompt: str = ""
    reference_images: list[str] = Field(default_factory=list)
    reference_videos: list[str] = Field(default_factory=list)
    reference_audios: list[str] = Field(default_factory=list)
    duration: float | None = None
    aspect_ratio: str | None = None
    resolution: str | None = None
    first_frame: str | None = None
    last_frame: str | None = None
    camera_control: dict[str, Any] | None = None
    seed: int | None = None
    model: str | None = None
    generation_mode: str = "image_to_video"
    api_parameters: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class VideoGenerationSubmission(BaseModel):
    external_id: str | None = None
    status: str
    model: str | None = None
    output_url: str | None = None
    raw_response: dict[str, Any] = Field(default_factory=dict)


class VideoGenerationStatus(BaseModel):
    external_id: str
    status: str
    model: str | None = None
    progress: float | None = None
    output_url: str | None = None
    error_message: str | None = None
    raw_response: dict[str, Any] = Field(default_factory=dict)


class CostEstimate(BaseModel):
    amount: float | None = None
    currency: str = "USD"
    basis: str = "unknown"
    is_exact: bool = False


class ProviderCapabilities(BaseModel):
    provider: str
    configured: bool
    configuration_issues: list[str] = Field(default_factory=list)
    generation_modes: list[str] = Field(default_factory=list)
    supports_first_frame: bool = False
    supports_last_frame: bool = False
    supports_reference_images: bool = False
    supports_reference_videos: bool = False
    supports_reference_audio: bool = False
    supported_durations: list[float] = Field(default_factory=list)
    supported_aspect_ratios: list[str] = Field(default_factory=list)
    supported_resolutions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class VideoProvider(ABC):
    """Provider-neutral contract used by the generation service."""

    name: str

    @abstractmethod
    def resolve_model(self, requested_model: str | None = None) -> str | None:
        """Return the exact model identifier that will be sent, if configured."""
        raise NotImplementedError

    @abstractmethod
    async def generate(
        self, request: VideoGenerationRequest
    ) -> VideoGenerationSubmission:
        raise NotImplementedError

    @abstractmethod
    async def get_status(self, external_id: str) -> VideoGenerationStatus:
        raise NotImplementedError

    @abstractmethod
    async def cancel(self, external_id: str) -> VideoGenerationStatus:
        raise NotImplementedError

    @abstractmethod
    async def download_result(self, external_id: str, destination: Path) -> Path:
        raise NotImplementedError

    @abstractmethod
    def estimate_cost(self, request: VideoGenerationRequest) -> CostEstimate:
        raise NotImplementedError

    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        raise NotImplementedError
