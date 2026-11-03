"""Project-scoped reusable locations. Local saves never call a generation provider."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from ..ids import new_id
from ..models import Project, Scene, Shot
from ..models.entities import SceneAsset, SceneAssetBinding
from ..services.character_elements import validate_images
from .routes import DB, _assert_production_editable, _get_or_404

router = APIRouter(tags=["scene-assets"])


class AssetSave(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1200)
    master_reference_path: str = Field(min_length=1, max_length=2000)
    additional_reference_paths: list[str] = Field(default_factory=list, max_length=2)


class AssetUpdate(AssetSave):
    expected_version: int = Field(ge=1)


class BindingSave(BaseModel):
    expected_version: int = Field(ge=1)
    asset_id: str | None = None


def read_asset(db, asset):
    return {
        key: getattr(asset, key)
        for key in (
            "id",
            "project_id",
            "name",
            "description",
            "master_reference_path",
            "additional_reference_paths",
            "version",
        )
    } | {
        "bound_scene_ids": list(
            db.scalars(
                select(SceneAssetBinding.scene_id).where(
                    SceneAssetBinding.asset_id == asset.id
                )
            )
        )
    }


def validate_asset(payload, project_id, request):
    if not payload.name.strip():
        raise HTTPException(422, "请输入场景母版名称")
    try:
        validate_images(
            request.app.state.settings.data_root,
            project_id,
            [payload.master_reference_path, *payload.additional_reference_paths],
        )
    except (ValueError, OSError) as exc:
        raise HTTPException(
            422, str(exc) if isinstance(exc, ValueError) else "场景母版文件读取失败"
        ) from exc


@router.get("/projects/{project_id}/scene-assets")
def list_assets(project_id: str, db: DB):
    _get_or_404(db, Project, project_id)
    return [
        read_asset(db, a)
        for a in db.scalars(
            select(SceneAsset)
            .where(SceneAsset.project_id == project_id)
            .order_by(SceneAsset.created_at)
        )
    ]


@router.post("/projects/{project_id}/scene-assets")
def create_asset(project_id: str, payload: AssetSave, request: Request, db: DB):
    _get_or_404(db, Project, project_id)
    validate_asset(payload, project_id, request)
    asset = SceneAsset(id=new_id("SET"), project_id=project_id, **payload.model_dump())
    db.add(asset)
    db.commit()
    return read_asset(db, asset)


@router.put("/scene-assets/{asset_id}")
def save_asset(asset_id: str, payload: AssetUpdate, request: Request, db: DB):
    asset = _get_or_404(db, SceneAsset, asset_id)
    validate_asset(payload, asset.project_id, request)
    if asset.version != payload.expected_version:
        raise HTTPException(409, "场景母版已修改，请刷新后重试")
    fields = payload.model_dump(exclude={"expected_version"})
    if all(getattr(asset, k) == v for k, v in fields.items()):
        return read_asset(db, asset)
    changed = db.execute(
        update(SceneAsset)
        .where(
            SceneAsset.id == asset.id, SceneAsset.version == payload.expected_version
        )
        .values(**fields, version=SceneAsset.version + 1)
    )
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "场景母版版本冲突，请刷新")
    # Mark both selected frames and historical video snapshots as out of date.
    scene_ids = select(SceneAssetBinding.scene_id).where(
        SceneAssetBinding.asset_id == asset.id
    )
    db.execute(
        update(Shot)
        .where(Shot.scene_id.in_(scene_ids))
        .values(version=Shot.version + 1)
    )
    db.commit()
    db.refresh(asset)
    return read_asset(db, asset)


@router.get("/scenes/{scene_id}/scene-master")
def get_binding(scene_id: str, db: DB):
    scene = _get_or_404(db, Scene, scene_id)
    binding = db.get(SceneAssetBinding, scene_id)
    return {
        "scene_id": scene_id,
        "scene_version": scene.version,
        "asset_id": binding.asset_id if binding else None,
    }


@router.put("/scenes/{scene_id}/scene-master")
def save_binding(scene_id: str, payload: BindingSave, db: DB):
    scene = _get_or_404(db, Scene, scene_id)
    _assert_production_editable(scene.episode)
    if scene.status == "archived":
        raise HTTPException(409, "已归档场景不能关联母版")
    if scene.version != payload.expected_version:
        raise HTTPException(409, "场景已修改，请刷新后重试")
    if payload.asset_id:
        asset = _get_or_404(db, SceneAsset, payload.asset_id)
        if asset.project_id != scene.episode.project_id:
            raise HTTPException(422, "只能选择当前项目的场景母版")
    binding = db.get(SceneAssetBinding, scene_id)
    if (binding.asset_id if binding else None) == payload.asset_id:
        return get_binding(scene_id, db)
    changed = db.execute(
        update(Scene)
        .where(Scene.id == scene_id, Scene.version == payload.expected_version)
        .values(version=Scene.version + 1)
    )
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "场景版本冲突，请刷新")
    binding = binding or SceneAssetBinding(scene_id=scene_id)
    binding.asset_id = payload.asset_id
    db.add(binding)
    db.execute(
        update(Shot).where(Shot.scene_id == scene_id).values(version=Shot.version + 1)
    )
    db.commit()
    return get_binding(scene_id, db)
