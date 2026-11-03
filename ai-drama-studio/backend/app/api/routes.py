from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..ids import new_id
from ..models import (
    Character,
    Episode,
    Generation,
    Outfit,
    Project,
    Scene,
    Script,
    Shot,
)
from ..providers.video.base import ProviderError
from ..providers.video.kling import KlingProvider
from ..providers.video.registry import VideoProviderRegistry
from ..providers.video.seedance import SeedanceProvider
from ..schemas import (
    CharacterCreate,
    CharacterRead,
    CharacterReferencesUpdate,
    CharacterUpdate,
    EpisodeCreate,
    EpisodeRead,
    GenerationCreate,
    GenerationRead,
    KlingElementCreate,
    OutfitCreate,
    OutfitRead,
    ProjectCreate,
    ProjectRead,
    SceneCreate,
    SceneRead,
    SceneStoryboard,
    SceneUpdate,
    ScriptCreate,
    ScriptRead,
    ScriptUpdate,
    ShotCreate,
    ShotRead,
    ShotUpdate,
    StoryboardRead,
)
from ..services import character_elements
from ..services.generations import GenerationConflictError, GenerationService
from .dependencies import get_db, get_provider_registry

router = APIRouter()
DB = Annotated[Session, Depends(get_db)]
Registry = Annotated[VideoProviderRegistry, Depends(get_provider_registry)]


def _get_or_404(db: Session, model, object_id: str):
    instance = db.get(model, object_id)
    if instance is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
    return instance


def _assert_production_editable(episode, shot=None):
    from ..services.storyboard_revision import production_editable

    try:
        production_editable(episode, shot)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def _commit_create(db: Session, instance) -> None:
    db.add(instance)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="ID already exists or relation is invalid"
        ) from exc
    db.refresh(instance)


def _assert_character_ids(
    db: Session, project_id: str, character_ids: list[str]
) -> None:
    if not character_ids:
        return
    found = set(
        db.scalars(
            select(Character.id).where(
                Character.project_id == project_id, Character.id.in_(character_ids)
            )
        ).all()
    )
    missing = set(character_ids) - found
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"characters do not belong to project: {sorted(missing)}",
        )


def _assert_version(instance, expected_version: int) -> None:
    if instance.version != expected_version:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "resource version conflict",
                "expected_version": expected_version,
                "current_version": instance.version,
            },
        )


def _apply_versioned_update(db: Session, instance, changes: dict) -> None:
    for field_name, value in changes.items():
        setattr(instance, field_name, value)
    instance.version += 1
    db.commit()
    db.refresh(instance)


@router.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/livez", tags=["system"], include_in_schema=False)
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", tags=["system"], include_in_schema=False)
def readiness(request: Request, db: DB) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
        db.execute(
            select(
                Generation.id,
                Generation.request_token,
                Generation.output_sha256,
                Generation.output_bytes,
            ).limit(1)
        )
        assets_dir = request.app.state.settings.data_root / "assets"
        if not assets_dir.is_dir():
            raise RuntimeError("asset directory is unavailable")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="service is not ready") from exc
    return {"status": "ready"}


@router.get("/providers/video", tags=["providers"])
def list_video_providers(registry: Registry) -> list[dict]:
    return [
        provider.capabilities().model_dump(mode="json") for provider in registry.all()
    ]


@router.get("/providers/video/seedance/account", tags=["providers"])
async def get_seedance_account(registry: Registry) -> dict:
    provider = registry.get("seedance")
    if not isinstance(provider, SeedanceProvider):
        return {"authenticated": False, "error": "Seedance Ark provider is unavailable"}
    try:
        account = await provider.get_account_info()
    except ProviderError as exc:
        return {"authenticated": False, "error": str(exc)}
    return {"authenticated": True, "account": account}


@router.post(
    "/projects",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
    tags=["projects"],
)
def create_project(payload: ProjectCreate, db: DB) -> Project:
    project = Project(
        id=payload.id or new_id("PRJ"),
        name=payload.name,
        description=payload.description,
        settings_json=payload.settings_json,
    )
    _commit_create(db, project)
    return project


@router.get("/projects", response_model=list[ProjectRead], tags=["projects"])
def list_projects(db: DB, include_archived: bool = False) -> list[Project]:
    query = select(Project)
    if not include_archived:
        query = query.where(Project.status != "archived")
    return list(db.scalars(query.order_by(Project.created_at.desc())).all())


@router.get("/projects/{project_id}", response_model=ProjectRead, tags=["projects"])
def get_project(project_id: str, db: DB) -> Project:
    return _get_or_404(db, Project, project_id)


@router.post("/projects/{project_id}/assets", tags=["assets"])
async def upload_project_asset(
    project_id: str,
    request: Request,
    db: DB,
    asset_kind: Annotated[
        str,
        Form(
            pattern="^(character_reference|scene_reference|shot_keyframe|reference_image|reference_video|reference_audio)$"
        ),
    ],
    file: Annotated[UploadFile, File()],
) -> dict:
    _get_or_404(db, Project, project_id)
    filename = Path(file.filename or "asset.bin").name
    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._") or "asset.bin"
    suffix = Path(filename).suffix.lower()
    allowed_extensions = {
        "character_reference": {".png", ".jpg", ".jpeg", ".webp"},
        "scene_reference": {".png", ".jpg", ".jpeg"},
        "shot_keyframe": {".png", ".jpg", ".jpeg", ".webp"},
        "reference_image": {".png", ".jpg", ".jpeg", ".webp"},
        "reference_video": {".mp4", ".mov", ".webm", ".m4v"},
        "reference_audio": {".mp3", ".wav", ".m4a", ".aac", ".flac"},
    }
    if suffix not in allowed_extensions[asset_kind]:
        raise HTTPException(
            status_code=415, detail=f"unsupported file type for {asset_kind}"
        )

    settings = request.app.state.settings
    destination_dir = (
        settings.data_root / "assets" / "projects" / project_id / asset_kind
    )
    destination_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{new_id('ASSET')}_{filename}"
    destination = destination_dir / safe_name
    size = 0
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination_dir, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=413, detail="uploaded file is too large"
                    )
                temporary.write(chunk)
        temporary_path.replace(destination)
    finally:
        await file.close()
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()

    relative_path = destination.relative_to(settings.data_root).as_posix()
    public_path = destination.relative_to(settings.data_root / "assets").as_posix()
    return {
        "path": relative_path,
        "url": f"/media/{public_path}",
        "filename": filename,
        "content_type": file.content_type,
        "size": size,
        "asset_kind": asset_kind,
    }


@router.post(
    "/projects/{project_id}/characters",
    response_model=CharacterRead,
    status_code=status.HTTP_201_CREATED,
    tags=["characters"],
)
def create_character(project_id: str, payload: CharacterCreate, db: DB) -> Character:
    _get_or_404(db, Project, project_id)
    character = Character(
        id=payload.id or new_id("CHAR"),
        project_id=project_id,
        **payload.model_dump(exclude={"id"}),
    )
    _commit_create(db, character)
    return character


@router.get(
    "/projects/{project_id}/characters",
    response_model=list[CharacterRead],
    tags=["characters"],
)
def list_characters(project_id: str, db: DB) -> list[Character]:
    _get_or_404(db, Project, project_id)
    return list(
        db.scalars(
            select(Character)
            .where(Character.project_id == project_id)
            .order_by(Character.created_at)
        ).all()
    )


@router.get(
    "/characters/{character_id}", response_model=CharacterRead, tags=["characters"]
)
def get_character(character_id: str, db: DB) -> Character:
    return _get_or_404(db, Character, character_id)


@router.patch(
    "/characters/{character_id}", response_model=CharacterRead, tags=["characters"]
)
def update_character(character_id: str, payload: CharacterUpdate, db: DB) -> Character:
    character = _get_or_404(db, Character, character_id)
    _assert_version(character, payload.expected_version)
    changes = payload.model_dump(
        exclude={"expected_version"}, exclude_none=True, mode="json"
    )
    _apply_versioned_update(db, character, changes)
    return character


def _kling(registry: VideoProviderRegistry) -> KlingProvider:
    provider = registry.get("kling")
    if not isinstance(provider, KlingProvider):
        raise HTTPException(status_code=503, detail="可灵主体服务不可用")
    return provider


@router.put(
    "/characters/{character_id}/references",
    response_model=CharacterRead,
    tags=["characters"],
)
def save_character_references(
    character_id: str, payload: CharacterReferencesUpdate, request: Request, db: DB
) -> Character:
    character = _get_or_404(db, Character, character_id)
    try:
        return character_elements.save_references(
            db, character, payload, request.app.state.settings.data_root
        )
    except character_elements.BindingConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/characters/{character_id}/kling-element", tags=["characters"])
def get_character_element(character_id: str, db: DB, registry: Registry) -> dict:
    character = _get_or_404(db, Character, character_id)
    return character_elements.binding_state(db, character, _kling(registry))


@router.post("/characters/{character_id}/kling-element", tags=["characters"])
async def create_character_element(
    character_id: str, payload: KlingElementCreate, db: DB, registry: Registry
) -> dict:
    character = _get_or_404(db, Character, character_id)
    try:
        return await character_elements.create_element(
            db, character, payload, _kling(registry)
        )
    except character_elements.BindingConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/characters/{character_id}/kling-element/refresh", tags=["characters"])
async def refresh_character_element(
    character_id: str, db: DB, registry: Registry
) -> dict:
    character = _get_or_404(db, Character, character_id)
    try:
        return await character_elements.refresh_element(db, character, _kling(registry))
    except character_elements.BindingConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/shots/{shot_id}/character-bindings/preview", tags=["characters"])
def preview_character_bindings(
    shot_id: str, db: DB, registry: Registry, model: str = "kling-3.0"
) -> dict:
    shot = _get_or_404(db, Shot, shot_id)
    return character_elements.preview_bindings(db, shot, _kling(registry), model)


@router.post(
    "/characters/{character_id}/outfits",
    response_model=OutfitRead,
    status_code=status.HTTP_201_CREATED,
    tags=["characters"],
)
def create_outfit(character_id: str, payload: OutfitCreate, db: DB) -> Outfit:
    _get_or_404(db, Character, character_id)
    outfit = Outfit(
        id=payload.id or new_id("OUTFIT"),
        character_id=character_id,
        **payload.model_dump(exclude={"id"}),
    )
    _commit_create(db, outfit)
    return outfit


@router.get(
    "/characters/{character_id}/outfits",
    response_model=list[OutfitRead],
    tags=["characters"],
)
def list_outfits(character_id: str, db: DB) -> list[Outfit]:
    _get_or_404(db, Character, character_id)
    return list(
        db.scalars(
            select(Outfit)
            .where(Outfit.character_id == character_id)
            .order_by(Outfit.created_at)
        ).all()
    )


@router.post(
    "/projects/{project_id}/scripts",
    response_model=ScriptRead,
    status_code=status.HTTP_201_CREATED,
    tags=["scripts"],
)
def create_script(
    project_id: str, payload: ScriptCreate, db: DB, episode_id: str | None = None
) -> Script:
    _get_or_404(db, Project, project_id)
    _assert_character_ids(db, project_id, payload.character_ids)
    episode = _draft_episode(db, project_id, episode_id)
    script = Script(
        id=payload.id or new_id("SCRIPT"),
        project_id=project_id,
        **payload.model_dump(exclude={"id"}),
    )
    if episode:
        script.structured_content = {
            **script.structured_content,
            "episode_id": episode.id,
        }
        db.add(script)
        db.flush()
        episode.script_id = script.id
    _commit_create(db, script)
    return script


def _draft_episode(
    db: Session, project_id: str, episode_id: str | None
) -> Episode | None:
    if not episode_id:
        return None
    episode = _get_or_404(db, Episode, episode_id)
    if episode.project_id != project_id:
        raise HTTPException(422, "分集不属于当前项目")
    if episode.status != "draft" or episode.scenes:
        raise HTTPException(409, "该分集已进入制作；请新建分集，避免覆盖已有镜头")
    return episode


@router.get(
    "/projects/{project_id}/scripts", response_model=list[ScriptRead], tags=["scripts"]
)
def list_scripts(project_id: str, db: DB) -> list[Script]:
    _get_or_404(db, Project, project_id)
    return list(
        db.scalars(
            select(Script)
            .where(Script.project_id == project_id)
            .order_by(Script.created_at.desc())
        ).all()
    )


@router.patch("/scripts/{script_id}", response_model=ScriptRead, tags=["scripts"])
def update_script(script_id: str, payload: ScriptUpdate, db: DB) -> Script:
    script = _get_or_404(db, Script, script_id)
    if "draft" in script.structured_content:
        raise HTTPException(
            409, "审阅稿请通过 /scripts/{id}/review 保存，已发布稿请在分镜台编辑。"
        )
    _assert_version(script, payload.expected_version)
    if payload.character_ids is not None:
        _assert_character_ids(db, script.project_id, payload.character_ids)
    changes = payload.model_dump(
        exclude={"expected_version"}, exclude_none=True, mode="json"
    )
    _apply_versioned_update(db, script, changes)
    return script


@router.post(
    "/projects/{project_id}/episodes",
    response_model=EpisodeRead,
    status_code=status.HTTP_201_CREATED,
    tags=["storyboard"],
)
def create_episode(project_id: str, payload: EpisodeCreate, db: DB) -> Episode:
    _get_or_404(db, Project, project_id)
    if payload.script_id:
        script = _get_or_404(db, Script, payload.script_id)
        if script.project_id != project_id:
            raise HTTPException(
                status_code=422, detail="script does not belong to project"
            )
    episode = Episode(
        id=payload.id or new_id("EP"),
        project_id=project_id,
        **payload.model_dump(exclude={"id"}),
    )
    _commit_create(db, episode)
    return episode


@router.get(
    "/projects/{project_id}/episodes",
    response_model=list[EpisodeRead],
    tags=["storyboard"],
)
def list_episodes(project_id: str, db: DB) -> list[Episode]:
    _get_or_404(db, Project, project_id)
    return list(
        db.scalars(
            select(Episode)
            .where(Episode.project_id == project_id)
            .order_by(Episode.episode_number)
        ).all()
    )


@router.post(
    "/episodes/{episode_id}/scenes",
    response_model=SceneRead,
    status_code=status.HTTP_201_CREATED,
    tags=["storyboard"],
)
def create_scene(episode_id: str, payload: SceneCreate, db: DB) -> Scene:
    episode = _get_or_404(db, Episode, episode_id)
    _assert_production_editable(episode)
    _assert_character_ids(db, episode.project_id, payload.character_ids)
    scene_data = payload.model_dump(exclude={"id"}, mode="json")
    scene = Scene(id=payload.id or new_id("SC"), episode_id=episode_id, **scene_data)
    _commit_create(db, scene)
    return scene


@router.get(
    "/episodes/{episode_id}/scenes", response_model=list[SceneRead], tags=["storyboard"]
)
def list_scenes(episode_id: str, db: DB) -> list[Scene]:
    _get_or_404(db, Episode, episode_id)
    return list(
        db.scalars(
            select(Scene)
            .where(Scene.episode_id == episode_id)
            .order_by(Scene.order_index)
        ).all()
    )


@router.patch("/scenes/{scene_id}", response_model=SceneRead, tags=["storyboard"])
def update_scene(scene_id: str, payload: SceneUpdate, db: DB) -> Scene:
    scene = _get_or_404(db, Scene, scene_id)
    _assert_production_editable(scene.episode)
    _assert_version(scene, payload.expected_version)
    if payload.character_ids is not None:
        _assert_character_ids(db, scene.episode.project_id, payload.character_ids)
    changes = payload.model_dump(
        exclude={"expected_version"}, exclude_none=True, mode="json"
    )
    _apply_versioned_update(db, scene, changes)
    return scene


@router.post(
    "/scenes/{scene_id}/shots",
    response_model=ShotRead,
    status_code=status.HTTP_201_CREATED,
    tags=["storyboard"],
)
def create_shot(scene_id: str, payload: ShotCreate, db: DB) -> Shot:
    scene = _get_or_404(db, Scene, scene_id)
    _assert_production_editable(scene.episode)
    if scene.status == "archived":
        raise HTTPException(409, "此场景已归档，不能新增镜头")
    character_ids = [item.character_id for item in payload.characters]
    _assert_character_ids(db, scene.episode.project_id, character_ids)
    for item in payload.characters:
        if item.outfit_id:
            outfit = _get_or_404(db, Outfit, item.outfit_id)
            if outfit.character_id != item.character_id:
                raise HTTPException(
                    status_code=422,
                    detail=f"outfit {item.outfit_id} does not belong to {item.character_id}",
                )
    shot_data = payload.model_dump(exclude={"id"}, mode="json")
    shot = Shot(id=payload.id or new_id("SH"), scene_id=scene_id, **shot_data)
    _commit_create(db, shot)
    return shot


@router.get(
    "/scenes/{scene_id}/shots", response_model=list[ShotRead], tags=["storyboard"]
)
def list_shots(scene_id: str, db: DB) -> list[Shot]:
    _get_or_404(db, Scene, scene_id)
    return list(
        db.scalars(
            select(Shot).where(Shot.scene_id == scene_id).order_by(Shot.order_index)
        ).all()
    )


@router.patch("/shots/{shot_id}", response_model=ShotRead, tags=["storyboard"])
def update_shot(shot_id: str, payload: ShotUpdate, db: DB) -> Shot:
    shot = _get_or_404(db, Shot, shot_id)
    _assert_production_editable(shot.scene.episode, shot)
    _assert_version(shot, payload.expected_version)
    if payload.characters is not None:
        character_ids = [item.character_id for item in payload.characters]
        _assert_character_ids(db, shot.scene.episode.project_id, character_ids)
        for item in payload.characters:
            if item.outfit_id:
                outfit = _get_or_404(db, Outfit, item.outfit_id)
                if outfit.character_id != item.character_id:
                    raise HTTPException(
                        status_code=422,
                        detail=f"outfit {item.outfit_id} does not belong to {item.character_id}",
                    )
    changes = payload.model_dump(
        exclude={"expected_version"}, exclude_none=True, mode="json"
    )
    _apply_versioned_update(db, shot, changes)
    return shot


@router.get(
    "/episodes/{episode_id}/storyboard",
    response_model=StoryboardRead,
    tags=["storyboard"],
)
def get_storyboard(episode_id: str, db: DB) -> StoryboardRead:
    episode = db.scalar(
        select(Episode)
        .where(Episode.id == episode_id)
        .options(selectinload(Episode.scenes).selectinload(Scene.shots))
    )
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    episode_data = EpisodeRead.model_validate(episode).model_dump()
    scenes = [
        SceneStoryboard(
            **SceneRead.model_validate(scene).model_dump(),
            shots=[
                ShotRead.model_validate(shot)
                for shot in scene.shots
                if shot.status != "archived"
            ],
        )
        for scene in episode.scenes
        if scene.status != "archived"
    ]
    return StoryboardRead(**episode_data, scenes=scenes)


@router.post(
    "/shots/{shot_id}/generations",
    response_model=GenerationRead,
    status_code=status.HTTP_201_CREATED,
    tags=["generations"],
)
async def create_generation(
    shot_id: str, payload: GenerationCreate, db: DB, registry: Registry
) -> Generation:
    shot = db.scalar(
        select(Shot)
        .where(Shot.id == shot_id)
        .options(selectinload(Shot.scene).selectinload(Scene.episode))
    )
    if shot is None:
        raise HTTPException(status_code=404, detail="Shot not found")
    service = GenerationService(registry)
    try:
        return await service.submit(db, shot, payload)
    except GenerationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/generations", response_model=list[GenerationRead], tags=["generations"])
def list_generations(
    db: DB,
    project_id: str | None = None,
    shot_id: str | None = None,
    provider: str | None = None,
    generation_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[Generation]:
    query = select(Generation)
    if project_id:
        query = query.where(Generation.project_id == project_id)
    if shot_id:
        query = query.where(Generation.shot_id == shot_id)
    if provider:
        query = query.where(Generation.provider == provider)
    if generation_status:
        query = query.where(Generation.status == generation_status)
    return list(
        db.scalars(query.order_by(Generation.created_at.desc()).limit(limit)).all()
    )


@router.get(
    "/generations/{generation_id}", response_model=GenerationRead, tags=["generations"]
)
def get_generation(generation_id: str, db: DB) -> Generation:
    return _get_or_404(db, Generation, generation_id)


@router.post(
    "/generations/{generation_id}/refresh",
    response_model=GenerationRead,
    tags=["generations"],
)
async def refresh_generation(
    generation_id: str, db: DB, registry: Registry
) -> Generation:
    generation = _get_or_404(db, Generation, generation_id)
    try:
        return await GenerationService(registry).refresh(db, generation)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/generations/{generation_id}/cancel",
    response_model=GenerationRead,
    tags=["generations"],
)
async def cancel_generation(
    generation_id: str, db: DB, registry: Registry
) -> Generation:
    generation = _get_or_404(db, Generation, generation_id)
    try:
        return await GenerationService(registry).cancel(db, generation)
    except (ValueError, ProviderError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
