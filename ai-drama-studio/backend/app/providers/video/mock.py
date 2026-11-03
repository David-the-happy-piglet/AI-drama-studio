from __future__ import annotations

from pathlib import Path

from .base import (
    CostEstimate,
    ProviderCapabilities,
    ProviderResponseError,
    VideoGenerationRequest,
    VideoGenerationStatus,
    VideoGenerationSubmission,
    VideoProvider,
)


class MockVideoProvider(VideoProvider):
    """Deterministic local provider for API development and contract tests."""

    name = "mock"

    def __init__(self) -> None:
        self.jobs: dict[str, VideoGenerationRequest] = {}

    def resolve_model(self, requested_model: str | None = None) -> str:
        return requested_model or "mock-video-v1"

    async def generate(
        self, request: VideoGenerationRequest
    ) -> VideoGenerationSubmission:
        external_id = f"mock-{request.generation_id}"
        self.jobs[external_id] = request
        return VideoGenerationSubmission(
            external_id=external_id,
            status="submitted",
            raw_response={"task_id": external_id, "mode": request.generation_mode},
        )

    async def get_status(self, external_id: str) -> VideoGenerationStatus:
        if external_id not in self.jobs:
            raise ProviderResponseError("mock job not found")
        return VideoGenerationStatus(
            external_id=external_id,
            status="succeeded",
            progress=1.0,
            output_url=f"mock://videos/{external_id}.mp4",
            raw_response={"task_id": external_id, "status": "succeeded"},
        )

    async def cancel(self, external_id: str) -> VideoGenerationStatus:
        if external_id not in self.jobs:
            raise ProviderResponseError("mock job not found")
        return VideoGenerationStatus(external_id=external_id, status="cancelled")

    async def download_result(self, external_id: str, destination: Path) -> Path:
        raise ProviderResponseError("mock provider does not create video bytes")

    def estimate_cost(self, request: VideoGenerationRequest) -> CostEstimate:
        return CostEstimate(amount=0, basis="local mock provider", is_exact=True)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.name,
            configured=True,
            generation_modes=[
                "text_to_video",
                "image_to_video",
                "reference_to_video",
                "first_last_frame",
                "video_extension",
            ],
            supports_first_frame=True,
            supports_last_frame=True,
            supports_reference_images=True,
            metadata={"model": self.resolve_model()},
        )
