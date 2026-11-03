"""Kling's current API-key API, verified against official docs 2026-09-05.

https://kling.ai/document-api/api/video/3-0-omni/image-to-video.md
Legacy AK/SK JWT endpoints deliberately remain a separate protocol.
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import ClassVar

import httpx

from .base import (
    CostEstimate,
    ProviderCapabilities,
    ProviderConfigurationError,
    ProviderRequestRejectedError,
    ProviderResponseError,
    VideoGenerationRequest,
    VideoGenerationStatus,
    VideoGenerationSubmission,
    VideoProvider,
)


class KlingRequestRejected(ProviderRequestRejectedError):
    """The provider definitively rejected a request (safe to correct and retry)."""


class KlingQuotaExhausted(KlingRequestRejected):
    """Kling explicitly refused a new task for insufficient account resources."""


def quota_exhausted(response: httpx.Response) -> bool:
    try:
        code = response.json().get("code")
    except (ValueError, AttributeError):
        return False
    return str(code) in {"1101", "1102"}


def rejection_detail(response: httpx.Response) -> str:
    """Retain useful diagnostics without exposing credentials or echoed image bytes."""
    text = f"可灵 API 拒绝请求：HTTP {response.status_code}"
    try:
        payload = response.json()
    except ValueError:
        return text
    if not isinstance(payload, dict):
        return text
    code = payload.get("code")
    hints = {
        1100: "账户状态异常",
        1101: "账户欠费",
        1102: "资源包不足、已用完或过期",
        1103: "当前账户无此接口或模型权限",
        1302: "请求过于频繁",
        1303: "并发或 QPS 超出资源包限制",
        1304: "IP 白名单限制",
        1200: "请求参数不合法",
        1201: "请求参数不合法",
    }
    if isinstance(code, (int, str)) and re.fullmatch(r"\d{1,8}", str(code)):
        text += f" · 业务码 {code}"
        if int(code) in hints:
            text += f"（{hints[int(code)]}）"
    message = payload.get("message")
    if isinstance(message, str):
        key = os.getenv("KLING_API_KEY")
        if key:
            message = message.replace(key, "[已隐藏密钥]")
        message = re.sub(
            r"(?:Bearer\s+|data:image/)[^\s\"]+",
            "[已隐藏凭据或图片]",
            message,
            flags=re.IGNORECASE,
        )
        message = re.sub(r"[A-Za-z0-9+/=_-]{100,}", "[已隐藏长数据]", message)
        text += " · " + message[:500]
    request_id = payload.get("request_id")
    if isinstance(request_id, str) and re.fullmatch(
        r"[A-Za-z0-9_-]{1,100}", request_id
    ):
        text += f" · 请求 ID {request_id}"
    return text


class KlingProvider(VideoProvider):
    name = "kling"
    models: ClassVar[dict[str, list[str]]] = {
        "kling-3.0": ["text_to_video", "image_to_video", "first_last_frame"],
        "kling-3.0-turbo": ["text_to_video", "image_to_video"],
        "kling-3.0-omni": [
            "text_to_video",
            "image_to_video",
            "first_last_frame",
            "reference_to_video",
        ],
    }

    def __init__(
        self,
        config_root: Path,
        timeout_seconds: float = 120,
        data_root: Path | None = None,
        transport=None,
    ):
        self.data_root = data_root or config_root.parent / "data"
        self.timeout = timeout_seconds
        self.transport = transport
        self.base_url = os.getenv(
            "KLING_BASE_URL", "https://api-singapore.klingai.com"
        ).rstrip("/")

    def resolve_model(self, requested_model=None):
        return requested_model or os.getenv("KLING_MODEL") or "kling-3.0"

    def capabilities(self):
        issues = (
            []
            if os.getenv("KLING_API_KEY")
            else ["KLING_API_KEY 未配置（新版 API Key，非网页会员登录）"]
        )
        if self.base_url not in {
            "https://api-singapore.klingai.com",
            "https://api-beijing.klingai.com",
        }:
            issues.append("KLING_BASE_URL 必须为官方北京或新加坡端点")
        return ProviderCapabilities(
            provider=self.name,
            configured=not issues,
            configuration_issues=issues,
            generation_modes=[
                "text_to_video",
                "image_to_video",
                "first_last_frame",
                "reference_to_video",
            ],
            supports_reference_images=True,
            supports_first_frame=True,
            supports_last_frame=True,
            supported_durations=list(range(3, 16)),
            supported_resolutions=["720p", "1080p", "4k"],
            supported_aspect_ratios=["16:9", "9:16", "1:1"],
            metadata={
                "transport": "http",
                "base_url": self.base_url,
                "model": "kling-3.0",
                "models": self.models,
                "authentication": "API Key Bearer",
                "native_audio": True,
                "element_binding": True,
                "element_creation": True,
            },
        )

    def image_input(self, value: str):
        if value.startswith("https://"):
            return value
        path = Path(value)
        path = (path if path.is_absolute() else self.data_root / path).resolve()
        if not path.is_relative_to((self.data_root / "assets").resolve()):
            raise ProviderRequestRejectedError("可灵图片必须是项目上传素材或 HTTPS URL")
        if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            raise ProviderRequestRejectedError("可灵首尾帧需要 JPG/PNG 图片")
        if path.stat().st_size > 50 * 1024 * 1024:
            raise ProviderRequestRejectedError("可灵图片不能超过 50 MB")
        return base64.b64encode(path.read_bytes()).decode("ascii")

    def build_request(self, request: VideoGenerationRequest):
        model = self.resolve_model(request.model)
        if (
            model not in self.models
            or request.generation_mode not in self.models[model]
        ):
            raise ProviderRequestRejectedError(
                "所选可灵模型不支持此生成模式；Turbo 不支持尾帧，图片参考请选 Omni"
            )
        turbo = model == "kling-3.0-turbo"
        omni = model == "kling-3.0-omni"
        if request.reference_videos or request.reference_audios:
            raise ProviderRequestRejectedError(
                "当前可灵适配器仅接图片/主体参考，未接入参考视频、视频编辑或独立音频输入"
            )
        duration = request.duration or 5
        resolution = request.resolution or "720p"
        if duration not in range(3, 16) or resolution not in {"720p", "1080p", "4k"}:
            raise ProviderRequestRejectedError(
                "可灵 3.0 需要整数 3–15 秒和 720p/1080p/4k"
            )
        if turbo and (
            resolution == "4k"
            or request.last_frame
            or request.api_parameters.get("elements")
        ):
            raise ProviderRequestRejectedError("Turbo 不支持 4K、尾帧或主体 ID 参数")
        audio = request.api_parameters.get("audio", "off")
        if audio not in {"native", "off"}:
            raise ProviderRequestRejectedError("audio 必须为 native 或 off")
        if turbo and audio != "native":
            raise ProviderRequestRejectedError("Turbo 当前仅提供原生声音，不能关闭声音")
        prompt = request.prompt + (
            "\n避免：" + request.negative_prompt if request.negative_prompt else ""
        )
        if len(prompt) > 3072:
            raise ProviderRequestRejectedError("可灵提示词不能超过 3072 字符")
        body = {
            "settings": {
                "duration": int(duration),
                "resolution": resolution,
                "audio": audio,
                "multi_shot": False,
            },
            "options": {"external_task_id": request.generation_id},
        }
        if turbo:
            del body["settings"]["audio"]
            del body["settings"]["multi_shot"]
        if request.generation_mode in {"text_to_video", "reference_to_video"}:
            if request.aspect_ratio and request.aspect_ratio not in {
                "16:9",
                "9:16",
                "1:1",
            }:
                raise ProviderRequestRejectedError("可灵画幅支持 16:9、9:16、1:1")
            body["settings"]["aspect_ratio"] = request.aspect_ratio or "16:9"
        if request.generation_mode == "text_to_video" and not omni:
            if request.api_parameters.get("elements"):
                raise ProviderRequestRejectedError(
                    "常规文生模式不支持主体，请选择 Omni 或首帧模式"
                )
            body["prompt"] = prompt
            return f"/text-to-video/{model}", body
        first = request.first_frame or next(iter(request.reference_images), None)
        frame_mode = request.generation_mode in {"image_to_video", "first_last_frame"}
        if frame_mode and (
            not first
            or (
                request.generation_mode == "first_last_frame" and not request.last_frame
            )
        ):
            raise ProviderRequestRejectedError("缺少所选模式要求的首帧/尾帧")
        if turbo and len(prompt) > 2500:
            raise ProviderRequestRejectedError("Turbo 图生提示词不能超过 2500 字符")
        contents = [{"type": "prompt", "text": prompt}]
        if frame_mode:
            contents.append({"type": "first_frame", "url": self.image_input(first)})
        if frame_mode and request.last_frame:
            contents.append(
                {"type": "last_frame", "url": self.image_input(request.last_frame)}
            )
        elements = request.api_parameters.get("elements", [])
        if not isinstance(elements, list) or len(elements) > 3:
            raise ProviderRequestRejectedError("最多绑定三个可灵主体")
        aliases = set()
        for element in elements:
            if (
                not isinstance(element, dict)
                or not element.get("id")
                or not element.get("element_id")
                or element["id"] in aliases
            ):
                raise ProviderRequestRejectedError("主体必须包含唯一 id 和 element_id")
            aliases.add(element["id"])
            contents.append(
                {
                    "type": "element",
                    "id": element["id"],
                    "element_id": element["element_id"],
                }
            )
        if request.generation_mode == "reference_to_video":
            if not request.reference_images and not elements:
                raise ProviderRequestRejectedError(
                    "Omni 图片参考模式至少需要一张参考图或一个主体"
                )
            # Conservative limit: valid for either multi-image or video-character Elements.
            if len(request.reference_images) + len(elements) > 4:
                raise ProviderRequestRejectedError(
                    "当前 Omni 图片与主体参考合计最多 4 个"
                )
            for index, value in enumerate(request.reference_images, 1):
                alias = f"image_{index}"
                if alias in aliases:
                    raise ProviderRequestRejectedError(
                        "主体引用名不能与 image_N 图片引用名重复"
                    )
                contents.append(
                    {"type": "refer_image", "url": self.image_input(value), "id": alias}
                )
        body["contents"] = contents
        return (f"/omni-video/{model}" if omni else f"/image-to-video/{model}"), body

    async def call(self, method, path, **kwargs):
        capabilities = self.capabilities()
        if not capabilities.configured:
            raise ProviderConfigurationError(
                "; ".join(capabilities.configuration_issues)
            )
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self.transport
            ) as client:
                response = await client.request(
                    method,
                    self.base_url + path,
                    headers={"Authorization": f"Bearer {os.environ['KLING_API_KEY']}"},
                    **kwargs,
                )
            if response.is_error:
                if 400 <= response.status_code < 500 and response.status_code != 408:
                    if quota_exhausted(response):
                        raise KlingQuotaExhausted(rejection_detail(response))
                    raise KlingRequestRejected(rejection_detail(response))
                raise ProviderResponseError(f"可灵 API HTTP {response.status_code}")
            payload = response.json()
            if not isinstance(payload, dict):
                raise ProviderResponseError("可灵 API 返回格式异常，请先查询任务状态")
            if payload.get("code") != 0:
                if quota_exhausted(response):
                    raise KlingQuotaExhausted(rejection_detail(response))
                raise KlingRequestRejected(rejection_detail(response))
            return payload
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderResponseError(
                "可灵请求失败；如提交超时，请先在平台查询，避免重复提交。"
            ) from exc

    async def generate(self, request):
        path, body = self.build_request(request)
        raw = await self.call("POST", path, json=body)
        data = raw.get("data", {})
        if not data.get("id"):
            raise ProviderResponseError("可灵未返回任务 ID")
        return VideoGenerationSubmission(
            external_id=str(data["id"]),
            status=data.get("status", "submitted"),
            model=self.resolve_model(request.model),
            raw_response=raw,
        )

    async def get_status(self, external_id):
        raw = await self.call("GET", "/tasks", params={"task_ids": external_id})
        data = next(
            (
                item
                for item in raw.get("data", [])
                if str(item.get("id")) == external_id
            ),
            None,
        )
        if not data:
            raise ProviderResponseError("可灵未返回该任务")
        output = next(
            (
                item.get("url")
                for item in data.get("outputs", [])
                if item.get("type") == "video"
            ),
            None,
        )
        return VideoGenerationStatus(
            external_id=external_id,
            status=data["status"],
            output_url=output,
            error_message=data.get("message") if data["status"] == "failed" else None,
            raw_response=raw,
        )

    async def cancel(self, external_id):
        raise ProviderConfigurationError("当前可灵适配器未接入取消任务接口")

    async def download_result(self, external_id, destination):
        status = await self.get_status(external_id)
        if status.status != "succeeded" or not status.output_url:
            raise ProviderResponseError("视频尚未完成")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(status.output_url)
            response.raise_for_status()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        return destination

    def estimate_cost(self, request):
        return CostEstimate(amount=None, basis="以可灵 API 账户实际账单为准")
