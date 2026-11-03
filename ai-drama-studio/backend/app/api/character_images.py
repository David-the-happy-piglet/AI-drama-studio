from fastapi import APIRouter, HTTPException

from ..models import Character
from ..providers.video.base import ProviderError
from ..schemas import CharacterRead
from ..services import character_images as service
from ..services.character_elements import BindingConflict
from .routes import DB, Registry, _get_or_404, _kling

router = APIRouter(tags=["character-images"])


@router.get("/characters/{character_id}/portrait-studio")
def studio(character_id: str, db: DB, registry: Registry):
    return service.studio_context(
        db, _get_or_404(db, Character, character_id), _kling(registry)
    )


@router.post("/characters/{character_id}/portrait-jobs")
async def create(
    character_id: str, payload: service.PortraitCreate, db: DB, registry: Registry
):
    try:
        return await service.create_job(
            db, _get_or_404(db, Character, character_id), payload, _kling(registry)
        )
    except BindingConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            503, "母版文件读取或快照保存失败，请检查本地素材目录"
        ) from exc


@router.post("/characters/{character_id}/portrait-jobs/{job_id}/refresh")
async def refresh(character_id: str, job_id: str, db: DB, registry: Registry):
    character = _get_or_404(db, Character, character_id)
    try:
        return await service.refresh_job(
            db, character, service.get_job(db, character, job_id), _kling(registry)
        )
    except BindingConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post(
    "/characters/{character_id}/portrait-jobs/{job_id}/select",
    response_model=CharacterRead,
)
def select(
    character_id: str,
    job_id: str,
    payload: service.PortraitSelect,
    db: DB,
    registry: Registry,
):
    character = _get_or_404(db, Character, character_id)
    try:
        return service.select_candidate(
            db,
            character,
            service.get_job(db, character, job_id),
            payload,
            _kling(registry),
        )
    except BindingConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
