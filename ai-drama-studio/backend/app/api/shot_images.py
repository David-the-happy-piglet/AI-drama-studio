from fastapi import APIRouter, HTTPException

from ..models import Shot
from ..providers.video.base import ProviderError
from ..schemas import ShotRead
from ..services import shot_images as service
from ..services.character_elements import BindingConflict
from ..services.external_keyframes import ExternalFrameBatch, import_frames
from .routes import DB, Registry, _get_or_404, _kling

router = APIRouter(tags=["shot-keyframes"])


@router.post("/episodes/{episode_id}/external-keyframes")
def import_external_keyframes(
    episode_id: str, payload: ExternalFrameBatch, db: DB, registry: Registry
):
    return import_frames(db, episode_id, payload, _kling(registry))


@router.get("/shots/{shot_id}/keyframe-studio")
def studio(shot_id: str, db: DB, registry: Registry):
    return service.studio_context(db, _get_or_404(db, Shot, shot_id), _kling(registry))


@router.post("/shots/{shot_id}/keyframe-jobs")
async def create(
    shot_id: str, payload: service.KeyframeCreate, db: DB, registry: Registry
):
    try:
        return await service.create_job(
            db, _get_or_404(db, Shot, shot_id), payload, _kling(registry)
        )
    except BindingConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (ProviderError, OSError) as exc:
        raise HTTPException(
            503,
            str(exc)
            if isinstance(exc, ProviderError)
            else "首帧素材读取或快照保存失败",
        ) from exc


@router.post("/shots/{shot_id}/keyframe-jobs/{job_id}/refresh")
async def refresh(shot_id: str, job_id: str, db: DB, registry: Registry):
    shot = _get_or_404(db, Shot, shot_id)
    try:
        job = service.get_job(db, shot, job_id)
        if job.model in {"codex-imagegen", "external-image"}:
            return service.job_read(job)
        return await service.refresh_job(db, shot, job, _kling(registry))
    except BindingConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/shots/{shot_id}/keyframe-jobs/{job_id}/select")
def select(
    shot_id: str,
    job_id: str,
    payload: service.KeyframeSelect,
    db: DB,
    registry: Registry,
):
    shot = _get_or_404(db, Shot, shot_id)
    try:
        selected = service.select_candidate(
            db, shot, service.get_job(db, shot, job_id), payload, _kling(registry)
        )
        return {"shot": ShotRead.model_validate(shot), "selected": selected}
    except BindingConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(
            422, str(exc) if isinstance(exc, ValueError) else "首帧文件读取失败"
        ) from exc
