from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..ids import new_id
from ..models import Generation, Shot
from ..providers.video.base import (
    ProviderConfigurationError,
    ProviderError,
    ProviderRequestRejectedError,
    VideoGenerationRequest,
)
from ..providers.video.kling import KlingProvider
from ..providers.video.registry import VideoProviderRegistry
from ..schemas import GenerationCreate
from .character_elements import preview_bindings

TERMINAL_STATUSES = {"succeeded", "completed", "failed", "cancelled"}


class GenerationConflictError(ValueError):
    """A request token was reused for a different generation payload."""


def _payload_fingerprint(payload: GenerationCreate) -> str:
    canonical = payload.model_dump(mode="json", exclude={"confirmed", "request_token"})
    # Keep fingerprints of requests made before the review gate existed stable.
    if canonical.get("review_override_reason") is None:
        canonical.pop("review_override_reason", None)
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _assert_same_request(generation: Generation, fingerprint: str) -> None:
    metadata = generation.request_snapshot.get("metadata", {})
    if metadata.get("idempotency_payload_sha256") != fingerprint:
        raise GenerationConflictError("此提交标识已用于另一组视频生成参数")


class GenerationService:
    def __init__(self, registry: VideoProviderRegistry) -> None:
        self.registry = registry

    async def submit(
        self,
        db: Session,
        shot: Shot,
        payload: GenerationCreate,
    ) -> Generation:
        fingerprint = _payload_fingerprint(payload)
        existing = db.scalar(
            select(Generation).where(
                Generation.shot_id == shot.id,
                Generation.request_token == str(payload.request_token),
            )
        )
        if existing:
            _assert_same_request(existing, fingerprint)
            return existing

        scene = shot.scene
        episode = scene.episode
        from .storyboard_revision import production_editable

        production_editable(episode, shot)
        provider_name = payload.provider or shot.manual_provider_override
        if not provider_name:
            raise ValueError(
                "provider is required until the Phase 2 model router is implemented"
            )
        provider = self.registry.get(provider_name)
        first_frame = payload.first_frame
        keyframe_snapshot = None
        if payload.keyframe_selection_token:
            if first_frame:
                raise ValueError("上传首帧与已选首帧不能同时使用，请选择一个来源")
            from .shot_images import resolve_for_video

            keyframe_snapshot = resolve_for_video(
                db, shot, self.registry.get("kling"), payload.keyframe_selection_token
            )
            first_frame = keyframe_snapshot["path"]

        generation_id = new_id("GEN")
        prompt = payload.prompt or shot.generation_prompt or shot.visual_description
        negative_prompt = payload.negative_prompt
        if negative_prompt is None:
            negative_prompt = shot.negative_prompt or ""
        from .director import gate_check

        if keyframe_snapshot:
            frame_fingerprint = keyframe_snapshot["sha256"]
        elif first_frame:
            # Manually uploaded frames are never covered by a stored review.
            frame_fingerprint = f"upload:{first_frame}"
        else:
            frame_fingerprint = None
        review_snapshot = gate_check(
            db,
            shot,
            prompt=prompt,
            first_frame_sha256=frame_fingerprint,
            override_reason=payload.review_override_reason,
        )
        duration = payload.duration or shot.duration
        model = provider.resolve_model(payload.model or shot.preferred_model) or ""
        api_parameters = dict(payload.api_parameters)
        character_binding_snapshot = []
        if isinstance(provider, KlingProvider) and payload.use_character_bindings:
            preview = preview_bindings(db, shot, provider, model)
            if preview["errors"]:
                raise ValueError("；".join(preview["errors"]))
            character_binding_snapshot = preview["elements"]
            manual = api_parameters.get("elements", [])
            if not isinstance(manual, list):
                raise ValueError("elements 必须是列表")
            automatic = [
                {"id": item["id"], "element_id": item["element_id"]}
                for item in character_binding_snapshot
            ]
            api_parameters["elements"] = [*automatic, *manual]
            if len(api_parameters["elements"]) > 3:
                raise ValueError("自动绑定与手动主体合计最多三个")
            if automatic:
                prompt += "\n角色身份对应（保持参考主体的身份特征）：\n" + "\n".join(
                    f"{item['name']}（{item['character_id']}）对应 @{item['id']}。"
                    for item in character_binding_snapshot
                )

        provider_request = VideoGenerationRequest(
            generation_id=generation_id,
            prompt=prompt,
            negative_prompt=negative_prompt,
            reference_images=payload.references,
            reference_videos=payload.reference_videos,
            reference_audios=payload.reference_audios,
            duration=duration,
            aspect_ratio=payload.aspect_ratio,
            resolution=payload.resolution,
            first_frame=first_frame,
            last_frame=payload.last_frame,
            seed=payload.seed,
            model=model or None,
            generation_mode=shot.generation_mode,
            api_parameters=api_parameters,
            camera_control={
                "movement": shot.camera_movement,
                "angle": shot.camera_angle,
            },
            metadata={
                "project_id": episode.project_id,
                "episode_id": episode.id,
                "scene_id": scene.id,
                "shot_id": shot.id,
                "shot_version": shot.version,
                "provider_configuration": provider.capabilities().metadata,
                "use_character_bindings": payload.use_character_bindings,
                "character_bindings": character_binding_snapshot,
                "selected_keyframe": keyframe_snapshot,
                "pre_submit_review": review_snapshot,
                "idempotency_payload_sha256": fingerprint,
            },
        )
        request_snapshot = provider_request.model_dump(mode="json", exclude_none=True)
        generation = Generation(
            id=generation_id,
            project_id=episode.project_id,
            episode_id=episode.id,
            scene_id=scene.id,
            shot_id=shot.id,
            request_token=str(payload.request_token),
            provider=provider_name,
            model=model,
            prompt=prompt,
            negative_prompt=negative_prompt,
            references=[
                *payload.references,
                *payload.reference_videos,
                *payload.reference_audios,
            ],
            seed=payload.seed,
            duration=duration,
            resolution=payload.resolution,
            aspect_ratio=payload.aspect_ratio,
            api_parameters=api_parameters,
            request_snapshot=request_snapshot,
            status="submitting",
        )
        db.add(generation)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.scalar(
                select(Generation).where(
                    Generation.shot_id == shot.id,
                    Generation.request_token == str(payload.request_token),
                )
            )
            if existing is None:
                raise
            _assert_same_request(existing, fingerprint)
            return existing
        db.refresh(generation)

        try:
            estimate = provider.estimate_cost(provider_request)
            submission = await provider.generate(provider_request)
            generation.external_id = submission.external_id
            generation.status = submission.status
            generation.model = submission.model or generation.model
            generation.submitted_at = datetime.now(UTC)
            generation.output_path = submission.output_url
            generation.provider_response = submission.raw_response
            generation.generation_cost = estimate.amount
        except ProviderConfigurationError as exc:
            generation.status = "configuration_required"
            generation.error_message = str(exc)
            generation.completed_at = datetime.now(UTC)
        except ProviderRequestRejectedError as exc:
            generation.status = "failed"
            generation.error_message = f"{type(exc).__name__}: {exc}"
            generation.completed_at = datetime.now(UTC)
        except ProviderError as exc:
            generation.status = "unknown"
            generation.error_message = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001 - Never replay an ambiguous paid write.
            generation.status = "unknown"
            generation.error_message = f"{type(exc).__name__}: {exc}"

        db.commit()
        db.refresh(generation)
        return generation

    async def refresh(self, db: Session, generation: Generation) -> Generation:
        if not generation.external_id:
            raise ValueError("generation has no external provider task id")
        provider = self.registry.get(generation.provider)
        try:
            status = await provider.get_status(generation.external_id)
            generation.status = status.status
            generation.model = status.model or generation.model
            generation.output_path = status.output_url or generation.output_path
            generation.error_message = status.error_message
            generation.provider_response = status.raw_response
            if status.status in TERMINAL_STATUSES:
                generation.completed_at = datetime.now(UTC)
        except ProviderError as exc:
            generation.error_message = (
                f"status refresh failed: {type(exc).__name__}: {exc}"
            )
        except Exception as exc:  # noqa: BLE001 - Polling is safe to retry.
            generation.error_message = (
                f"status refresh failed: {type(exc).__name__}: {exc}"
            )
        db.commit()
        db.refresh(generation)
        return generation

    async def cancel(self, db: Session, generation: Generation) -> Generation:
        if not generation.external_id:
            raise ValueError("generation has no external provider task id")
        provider = self.registry.get(generation.provider)
        status = await provider.cancel(generation.external_id)
        generation.status = status.status
        generation.provider_response = status.raw_response
        generation.completed_at = datetime.now(UTC)
        db.commit()
        db.refresh(generation)
        return generation
