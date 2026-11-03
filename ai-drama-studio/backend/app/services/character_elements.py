"""Local-first character assets and explicit, recoverable Kling synchronization.

Official contract: https://kling.ai/document-api/api/video/3-0-omni/elements.md
Creation uses /v1/general/advanced-custom-elements, not video /tasks endpoints.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

from PIL import Image, UnidentifiedImageError
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..ids import new_id
from ..models import Character, Shot
from ..models.entities import CharacterProviderBinding, CharacterReferenceAssets
from ..providers.video.base import ProviderConfigurationError, ProviderError
from ..providers.video.kling import KlingProvider, KlingRequestRejected
from ..schemas import (
    CharacterBindingRead,
    CharacterReferencesUpdate,
    KlingElementCreate,
)

ELEMENT_PATH = "/v1/general/advanced-custom-elements"
ACTIVE = {"submitting", "submitted", "processing", "unknown"}


class BindingConflict(ValueError):
    pass


def account_scope(provider: KlingProvider) -> str:
    # A key rotation is treated conservatively as a different account until re-bound.
    value = provider.base_url + "\n" + os.getenv("KLING_API_KEY", "")
    return hashlib.sha256(value.encode()).hexdigest()


def reference_paths(character: Character) -> list[str]:
    return [
        *([character.master_reference_path] if character.master_reference_path else []),
        *(
            character.reference_assets.additional_reference_paths
            if character.reference_assets
            else []
        ),
    ]


def validate_images(data_root: Path, project_id: str, paths: list[str]) -> list[bytes]:
    """Only this project's actual local JPG/PNG assets may leave the machine."""
    projects_root = (data_root / "assets" / "projects").resolve()
    root = (projects_root / project_id).resolve()
    if root == projects_root or not root.is_relative_to(projects_root):
        raise ValueError("项目素材目录无效")
    images = []
    fingerprints = set()
    for value in paths:
        path = Path(value)
        path = (path if path.is_absolute() else data_root / path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("参考图必须是当前项目已上传的本地图片")
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            raise ValueError("可灵主体仅支持 JPG/PNG；请先转换 WebP")
        if path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("每张可灵主体参考图不能超过 10 MB")
        try:
            with Image.open(path) as image:
                width, height = image.size
                if image.format not in {"PNG", "JPEG"}:
                    raise ValueError("参考图内容不是 JPG/PNG")
                if min(width, height) < 300 or not 0.4 <= width / height <= 2.5:
                    raise ValueError("参考图两边至少 300px，宽高比需在 2:5–5:2 之间")
                image.verify()
        except (
            OSError,
            SyntaxError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
        ) as exc:
            raise ValueError("参考图损坏或尺寸过大，请重新上传") from exc
        content = path.read_bytes()
        fingerprint = hashlib.sha256(content).hexdigest()
        if fingerprint in fingerprints:
            raise ValueError("请提供不同角度的图片，不能重复提交同一张图")
        fingerprints.add(fingerprint)
        images.append(content)
    return images


def save_references(
    db: Session,
    character: Character,
    payload: CharacterReferencesUpdate,
    data_root: Path,
) -> Character:
    paths = [payload.master_reference_path, *payload.additional_reference_paths]
    validate_images(data_root, character.project_id, paths)
    result = db.execute(
        update(Character)
        .where(
            Character.id == character.id, Character.version == payload.expected_version
        )
        .values(
            master_reference_path=payload.master_reference_path,
            version=Character.version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise BindingConflict("角色已被修改，请刷新后重新保存")
    assets = db.get(CharacterReferenceAssets, character.id)
    if not assets:
        assets = CharacterReferenceAssets(character_id=character.id)
        db.add(assets)
    assets.additional_reference_paths = payload.additional_reference_paths
    db.commit()
    db.refresh(character)
    db.expire(character, ["reference_assets"])
    return character


def binding_issue(
    character: Character,
    binding: CharacterProviderBinding | None,
    provider: KlingProvider,
) -> str | None:
    if not binding:
        return "尚未创建可灵主体"
    if binding.account_scope != account_scope(provider):
        return "API Key 或区域已变化，当前绑定不可用；请切回原配置或重新创建"
    if binding.source_version != character.version:
        return "角色资料已更新，需重新审阅并创建主体"
    if binding.status != "succeeded" or not binding.element_id:
        return f"主体尚未就绪（{binding.status}）"
    return None


def binding_state(db: Session, character: Character, provider: KlingProvider) -> dict:
    binding = db.get(CharacterProviderBinding, (character.id, "kling"))
    issue = binding_issue(character, binding, provider)
    return {
        "binding": CharacterBindingRead.model_validate(binding).model_dump(mode="json")
        if binding
        else None,
        "usable": issue is None,
        "issue": issue,
        "configured": provider.capabilities().configured,
        "configuration_issues": provider.capabilities().configuration_issues,
    }


async def create_element(
    db: Session,
    character: Character,
    payload: KlingElementCreate,
    provider: KlingProvider,
) -> dict:
    if character.version != payload.expected_version:
        raise BindingConflict("角色版本已变化，请刷新并重新审阅")
    if not payload.element_name.strip() or not payload.element_description.strip():
        raise ValueError("主体名称和描述不能为空")
    if not provider.capabilities().configured:
        raise ProviderConfigurationError(
            "; ".join(provider.capabilities().configuration_issues)
        )
    paths = reference_paths(character)
    if not character.master_reference_path or not 2 <= len(paths) <= 4:
        raise ValueError("请先保存一张正面母版图及 1–3 张不同角度的参考图")
    images = validate_images(provider.data_root, character.project_id, paths)
    binding = db.get(CharacterProviderBinding, (character.id, "kling"))
    if binding and binding.status in ACTIVE:
        raise BindingConflict("已有创建任务或结果不确定，请先刷新状态，勿重复提交")
    if binding and binding.status == "succeeded" and not payload.replace_existing:
        raise BindingConflict("已有主体；重新创建需要明确确认替换本地绑定")
    request_id = new_id("ELEMENT")
    values = {
        "account_scope": account_scope(provider),
        "source_version": character.version,
        "source_snapshot": {
            "paths": paths,
            "name": payload.element_name,
            "description": payload.element_description,
            "image_sha256": [hashlib.sha256(x).hexdigest() for x in images],
        },
        "external_task_id": request_id,
        "task_id": None,
        "element_id": None,
        "status": "submitting",
        "error_message": None,
    }
    # Claim before contacting the provider. Concurrent clicks cannot POST twice.
    if binding:
        claimed = db.execute(
            update(CharacterProviderBinding)
            .where(
                CharacterProviderBinding.character_id == character.id,
                CharacterProviderBinding.provider == "kling",
                CharacterProviderBinding.external_task_id == binding.external_task_id,
                CharacterProviderBinding.status == binding.status,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            db.rollback()
            raise BindingConflict("另一请求已提交此角色，请刷新状态")
    else:
        db.add(
            CharacterProviderBinding(
                character_id=character.id, provider="kling", **values
            )
        )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise BindingConflict("另一请求已提交此角色，请刷新状态") from exc
    db.expire_all()
    binding = db.get(CharacterProviderBinding, (character.id, "kling"))
    body = {
        "element_name": payload.element_name.strip(),
        "element_description": payload.element_description.strip(),
        "reference_type": "image_refer",
        "element_image_list": {
            "frontal_image": base64.b64encode(images[0]).decode("ascii"),
            "refer_images": [
                {"image_url": base64.b64encode(x).decode("ascii")} for x in images[1:]
            ],
        },
        "external_task_id": request_id,
        "tag_list": [{"tag_id": "o_102"}],
    }
    result = SimpleNamespace(
        task_id=None, element_id=None, status="submitting", error_message=None
    )
    try:
        raw = await provider.call("POST", ELEMENT_PATH, json=body)
        apply_result(result, raw)
    except KlingRequestRejected as exc:
        result.status = "failed"
        result.error_message = str(exc)
    except ProviderError:
        result.status = "unknown"
        result.error_message = "提交结果不确定。请刷新状态查询原任务，不要重复创建。"
    store_result(db, character.id, request_id, "submitting", result)
    return binding_state(db, character, provider)


def store_result(
    db: Session,
    character_id: str,
    request_id: str,
    previous_status: str,
    result: SimpleNamespace,
) -> None:
    # A late POST response or stale refresh must not overwrite a newer task/result.
    db.execute(
        update(CharacterProviderBinding)
        .where(
            CharacterProviderBinding.character_id == character_id,
            CharacterProviderBinding.provider == "kling",
            CharacterProviderBinding.external_task_id == request_id,
            CharacterProviderBinding.status == previous_status,
        )
        .values(**vars(result))
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.expire_all()


def apply_result(binding: SimpleNamespace, raw: dict) -> None:
    data = raw.get("data")
    if not isinstance(data, dict):
        binding.status = "unknown"
        binding.error_message = "可灵未返回有效任务，请刷新状态"
        return
    if data.get("task_id"):
        binding.task_id = str(data["task_id"])
    status = data.get("task_status")
    if not isinstance(status, str):
        status = "unknown"
    binding.status = {
        "succeed": "succeeded",
        "succeeded": "succeeded",
        "failed": "failed",
        "submitted": "submitted",
        "processing": "processing",
    }.get(status, "unknown")
    binding.error_message = None
    if binding.status == "succeeded":
        result = data.get("task_result") or {}
        elements = result.get("elements", []) if isinstance(result, dict) else []
        element = elements[0] if isinstance(elements, list) and elements else None
        if (
            not isinstance(element, dict)
            or element.get("element_id") in (None, "")
            or element.get("status") == "deleted"
        ):
            binding.status = "unknown"
            binding.error_message = "任务成功但未返回可用主体 ID，请刷新状态"
        else:
            binding.element_id = str(element["element_id"])
    elif binding.status == "failed":
        binding.error_message = str(data.get("task_status_msg") or "可灵主体创建失败")
    elif binding.status == "unknown":
        binding.error_message = "任务状态不确定，请刷新状态，不要重复创建"


async def refresh_element(
    db: Session, character: Character, provider: KlingProvider
) -> dict:
    binding = db.get(CharacterProviderBinding, (character.id, "kling"))
    if not binding:
        raise BindingConflict("尚未提交主体创建任务")
    if binding.account_scope != account_scope(provider):
        raise BindingConflict(
            "当前 API Key 或区域与创建任务时不一致，请切回原配置后查询"
        )
    # Official API allows querying by task_id OR the external_task_id supplied on POST.
    query_id = binding.task_id or binding.external_task_id
    request_id, previous_status = binding.external_task_id, binding.status
    result = SimpleNamespace(
        task_id=binding.task_id,
        element_id=binding.element_id,
        status=binding.status,
        error_message=None,
    )
    try:
        raw = await provider.call("GET", f"{ELEMENT_PATH}/{quote(query_id, safe='')}")
        apply_result(result, raw)
        if previous_status in {"succeeded", "failed"} and result.status in ACTIVE:
            result.status = previous_status
            result.error_message = "查询返回较早状态，保留已有终态，请稍后核实"
    except ProviderError as exc:
        # A failed lookup is NOT proof the POST failed. Preserve state and disallow resubmit.
        result.error_message = f"查询失败，保留原任务，请稍后重试：{exc}"
    store_result(db, character.id, request_id, previous_status, result)
    return binding_state(db, character, provider)


def preview_bindings(
    db: Session, shot: Shot, provider: KlingProvider, model: str
) -> dict:
    supported = model in {"kling-3.0", "kling-3.0-omni"} and (
        shot.generation_mode in {"image_to_video", "first_last_frame"}
        or model == "kling-3.0-omni"
        and shot.generation_mode in {"reference_to_video", "text_to_video"}
    )
    elements, errors = [], []
    seen = set()
    for item in shot.characters:
        character_id = item["character_id"]
        if character_id in seen:
            continue
        seen.add(character_id)
        character = db.get(Character, character_id)
        if not character or character.project_id != shot.scene.episode.project_id:
            errors.append(f"角色 {character_id} 不属于当前项目")
            continue
        binding = db.get(CharacterProviderBinding, (character_id, "kling"))
        issue = binding_issue(character, binding, provider)
        if issue:
            errors.append(f"{character.name}：{issue}")
        else:
            elements.append(
                {
                    "character_id": character_id,
                    "name": character.name,
                    "id": "char_"
                    + hashlib.sha256(character_id.encode()).hexdigest()[:12],
                    "element_id": binding.element_id,
                }
            )
    if shot.characters and not supported:
        errors.insert(0, "此模型/模式不支持主体绑定；请切换模型或明确关闭自动绑定")
    if len(elements) > 3:
        errors.append("当前适配器最多绑定三个主体，请拆分镜头")
    return {"supported": supported, "elements": elements, "errors": errors}
