"""Seedance via the official Volcengine Ark video generation API.

Protocol: https://www.volcengine.com/docs/82379/1520757
Submission is never retried automatically: a timeout may hide an accepted job.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from urllib.parse import quote, urlparse

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

DEFAULT_MODEL = "doubao-seedance-2-0-260128"
BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
TASKS_PATH = "/contents/generations/tasks"
MODES = ["text_to_video", "image_to_video", "first_last_frame", "reference_to_video"]


class SeedanceProvider(VideoProvider):
    name = "seedance"

    def __init__(
        self, config_root: Path, asset_root: Path, timeout_seconds=120, transport=None
    ):
        self.asset_root = asset_root.resolve()
        self.timeout = timeout_seconds
        self.transport = transport
        self._key = os.getenv("ARK_API_KEY", "").strip()
        self.base_url = (os.getenv("ARK_BASE_URL") or BASE_URL).strip().rstrip("/")
        self.model = (os.getenv("ARK_VIDEO_MODEL") or DEFAULT_MODEL).strip()

    def resolve_model(self, requested_model=None):
        model = requested_model or self.model
        if model.startswith("seedance"):
            raise ProviderConfigurationError(
                "旧即梦 CLI 模型名称不能用于方舟，请选择 doubao-seedance-2-0-260128"
            )
        return model

    def capabilities(self):
        issues = []
        if not self._key or self._key in {"YOUR_API_KEY", "<YOUR_API_KEY>"}:
            issues.append("请在 ai-drama-studio/.env.local 填写 ARK_API_KEY 并重启后端")
        if self.base_url != BASE_URL:
            issues.append(f"火山方舟必须使用官方地址 {BASE_URL}")
        return ProviderCapabilities(
            provider=self.name,
            configured=not issues,
            configuration_issues=issues,
            generation_modes=MODES,
            supports_first_frame=True,
            supports_last_frame=True,
            supports_reference_images=True,
            supports_reference_videos=True,
            supports_reference_audio=True,
            supported_durations=list(range(4, 16)),
            supported_aspect_ratios=[
                "16:9",
                "9:16",
                "1:1",
                "4:3",
                "3:4",
                "21:9",
                "adaptive",
            ],
            supported_resolutions=["480p", "720p", "1080p", "4k"],
            metadata={
                "transport": "http",
                "platform": "Volcengine Ark",
                "authentication": "API Key (Bearer)",
                "model": self.model,
                "base_url": self.base_url,
                "quota_verified": False,
                "native_audio": True,
                "reference_video_input": "public URL or asset:// ID",
            },
        )

    def _assert_configured(self):
        issues = self.capabilities().configuration_issues
        if issues:
            raise ProviderConfigurationError("；".join(issues))

    def _media(self, value: str, kind: str):
        parsed = urlparse(value)
        if parsed.scheme in {"https", "http", "asset"} and parsed.netloc:
            return value
        if kind == "video":
            raise ProviderRequestRejectedError(
                "方舟参考视频需要公网 URL 或 asset:// 素材 ID，不能直接使用本地视频文件"
            )
        formats = {
            "image": {
                ".jpg": "jpeg",
                ".jpeg": "jpeg",
                ".png": "png",
                ".webp": "webp",
                ".bmp": "bmp",
                ".tiff": "tiff",
                ".gif": "gif",
                ".heic": "heic",
                ".heif": "heif",
            },
            "audio": {".mp3": "mp3", ".wav": "wav"},
        }
        limit = (30 if kind == "image" else 15) * 1024 * 1024
        if value.startswith(f"data:{kind}/"):
            try:
                header, encoded = value.split(",", 1)
                subtype = header.removeprefix(f"data:{kind}/").removesuffix(";base64")
                if (
                    not header.endswith(";base64")
                    or subtype not in formats[kind].values()
                ):
                    raise ValueError
                if len(encoded) > ((limit + 2) // 3) * 4:
                    raise ValueError
                data = base64.b64decode(encoded, validate=True)
                if not data or len(data) >= limit:
                    raise ValueError
            except ValueError as exc:
                raise ProviderRequestRejectedError(
                    "素材 Base64 格式或大小无效"
                ) from exc
            return value
        path = Path(value).expanduser()
        path = (path if path.is_absolute() else self.asset_root / path).resolve()
        if not path.is_relative_to(self.asset_root):
            raise ProviderRequestRejectedError(
                "本地素材必须位于 DATA_ROOT 内，请先上传素材"
            )
        subtype = formats[kind].get(path.suffix.lower())
        if not subtype or not path.is_file():
            raise ProviderRequestRejectedError(f"不支持的 {kind} 格式或素材不存在")
        if not 0 < path.stat().st_size < limit:
            raise ProviderRequestRejectedError(
                f"{kind} 素材必须小于 {limit // 1024 // 1024} MB"
            )
        try:
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError as exc:
            raise ProviderRequestRejectedError("无法读取本地素材") from exc
        return f"data:{kind}/{subtype};base64,{encoded}"

    def build_request(self, request: VideoGenerationRequest):
        mode = request.generation_mode
        if mode not in MODES:
            raise ProviderRequestRejectedError(f"当前方舟适配器不支持模式 {mode}")
        allowed = {"generate_audio", "watermark", "return_last_frame", "camera_fixed"}
        unknown = set(request.api_parameters) - allowed
        if unknown:
            raise ProviderRequestRejectedError(
                f"不支持的方舟参数：{', '.join(sorted(unknown))}"
            )
        if any(type(value) is not bool for value in request.api_parameters.values()):
            raise ProviderRequestRejectedError("方舟音频、水印和尾帧开关必须是布尔值")
        content = [
            {
                "type": "text",
                "text": request.prompt
                + (
                    f"\n负向约束：{request.negative_prompt}"
                    if request.negative_prompt
                    else ""
                ),
            }
        ]

        def add(value, kind, role):
            field = f"{kind}_url"
            content.append(
                {"type": field, field: {"url": self._media(value, kind)}, "role": role}
            )

        if mode == "text_to_video":
            if (
                request.first_frame
                or request.last_frame
                or request.reference_images
                or request.reference_videos
                or request.reference_audios
            ):
                raise ProviderRequestRejectedError(
                    "文生视频模式不能附带参考素材，请选择对应模式"
                )
        elif mode in {"image_to_video", "first_last_frame"}:
            first = request.first_frame or (
                request.reference_images[0] if request.reference_images else None
            )
            if not first or (mode == "first_last_frame" and not request.last_frame):
                raise ProviderRequestRejectedError("请提供首帧；首尾帧模式还需要尾帧")
            if (
                request.reference_videos
                or request.reference_audios
                or len(request.reference_images) > (0 if request.first_frame else 1)
            ):
                raise ProviderRequestRejectedError(
                    "首帧 / 首尾帧模式不能混入多模态参考素材"
                )
            add(first, "image", "first_frame")
            if request.last_frame:
                add(request.last_frame, "image", "last_frame")
        else:
            if request.first_frame or request.last_frame:
                raise ProviderRequestRejectedError(
                    "多模态模式请使用参考图片，不要混用首尾帧"
                )
            if not request.reference_images and not request.reference_videos:
                raise ProviderRequestRejectedError(
                    "Seedance 2.0 至少需要一张参考图片或一个参考视频，不能仅传音频"
                )
            if (
                len(request.reference_images) > 9
                or len(request.reference_videos) > 3
                or len(request.reference_audios) > 3
            ):
                raise ProviderRequestRejectedError(
                    "Seedance 2.0 最多支持 9 张图片、3 个视频、3 个音频"
                )
            for kind, values in (
                ("image", request.reference_images),
                ("video", request.reference_videos),
                ("audio", request.reference_audios),
            ):
                for value in values:
                    add(value, kind, f"reference_{kind}")
        body = {
            "model": self.resolve_model(request.model),
            "content": content,
            "generate_audio": True,
            "watermark": False,
            **request.api_parameters,
            "resolution": request.resolution or "720p",
            "ratio": request.aspect_ratio or "16:9",
        }
        if request.duration is not None:
            if (
                not float(request.duration).is_integer()
                or not 4 <= request.duration <= 15
            ):
                raise ProviderRequestRejectedError(
                    "Seedance 2.0 时长必须是 4–15 秒的整数"
                )
            body["duration"] = int(request.duration)
        else:
            body["duration"] = 5
        if body["resolution"] not in self.capabilities().supported_resolutions:
            raise ProviderRequestRejectedError(
                "Seedance 2.0 支持 480p、720p、1080p、4k"
            )
        if body["ratio"] not in self.capabilities().supported_aspect_ratios:
            raise ProviderRequestRejectedError("不支持的 Seedance 2.0 画幅")
        if request.seed is not None:
            body["seed"] = request.seed
        if len(json.dumps(body, ensure_ascii=False).encode("utf-8")) > 64 * 1024 * 1024:
            raise ProviderRequestRejectedError(
                "方舟请求体超过 64 MB，请改用公网素材 URL"
            )
        return body

    async def call(self, method, path, **kwargs):
        self._assert_configured()
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self.transport
            ) as client:
                response = await client.request(
                    method,
                    self.base_url + path,
                    headers={
                        "Authorization": f"Bearer {self._key}",
                        "Content-Type": "application/json",
                    },
                    **kwargs,
                )
        except httpx.HTTPError as exc:
            raise ProviderResponseError(
                "方舟请求失败；提交结果可能未知，请先查询平台任务，避免重复扣费"
            ) from exc
        if response.is_error:
            # Never echo headers, raw response bodies or API keys into history/UI.
            code = "unknown"
            try:
                error = response.json().get("error", {})
                candidate = str(error.get("code", "unknown"))
                if (
                    candidate.replace(".", "").replace("_", "").isalnum()
                    and self._key not in candidate
                ):
                    code = candidate[:160]
            except (ValueError, AttributeError):
                pass
            error_type = (
                ProviderRequestRejectedError
                if 400 <= response.status_code < 500 and response.status_code != 408
                else ProviderResponseError
            )
            raise error_type(f"火山方舟 HTTP {response.status_code}（{code}）")
        if not response.content:
            return {}
        try:
            raw = response.json()
            if not isinstance(raw, dict) or (
                raw.get("error") and not raw.get("status")
            ):
                raise ValueError
            return raw
        except ValueError as exc:
            raise ProviderResponseError(
                "方舟返回了无效响应，请核对平台任务状态"
            ) from exc

    async def generate(self, request):
        self._assert_configured()
        body = self.build_request(request)
        raw = await self.call("POST", TASKS_PATH, json=body)
        if not isinstance(raw.get("id"), str) or not raw["id"]:
            raise ProviderResponseError("方舟未返回任务 ID，请核对平台任务状态")
        return VideoGenerationSubmission(
            external_id=raw["id"],
            status="submitted",
            model=body["model"],
            raw_response=raw,
        )

    async def get_status(self, external_id):
        raw = await self.call("GET", TASKS_PATH + "/" + quote(external_id, safe=""))
        mapping = {
            "queued": "submitted",
            "running": "processing",
            "succeeded": "succeeded",
            "failed": "failed",
            "cancelled": "cancelled",
            "expired": "failed",
        }
        if raw.get("status") not in mapping:
            raise ProviderResponseError("方舟返回未知任务状态")
        error = raw.get("error") or {}
        return VideoGenerationStatus(
            external_id=external_id,
            status=mapping[raw["status"]],
            model=raw.get("model"),
            output_url=(raw.get("content") or {}).get("video_url"),
            error_message=error.get("message")
            or ("任务已过期" if raw["status"] == "expired" else None),
            raw_response=raw,
        )

    async def get_account_info(self):
        # Read-only authentication probe; task-list access does not verify balance/model entitlement.
        await self.call("GET", TASKS_PATH, params={"page_size": 1})
        return {
            "platform": "火山方舟",
            "model": self.model,
            "authentication": "API Key",
            "quota_verified": False,
            "note": "任务查询鉴权通过；模型权限与资源包余量请在方舟控制台核对",
        }

    async def cancel(self, external_id):
        # DELETE also deletes completed task records. Only cancel queued work.
        status = await self.get_status(external_id)
        if status.status == "cancelled":
            return status
        if status.status != "submitted":
            raise ProviderRequestRejectedError("方舟只支持取消排队中的任务")
        raise ProviderConfigurationError(
            "请在方舟控制台取消排队任务；当前适配器不执行会删除历史记录的 DELETE 接口"
        )

    async def download_result(self, external_id, destination):
        status = await self.get_status(external_id)
        if status.status != "succeeded" or not status.output_url:
            raise ProviderResponseError("方舟视频尚未生成成功")
        if urlparse(status.output_url).scheme != "https":
            raise ProviderResponseError("方舟视频下载地址必须是 HTTPS")
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        try:
            # A separate client ensures API credentials never reach the media host.
            async with (
                httpx.AsyncClient(
                    timeout=self.timeout,
                    transport=self.transport,
                    follow_redirects=True,
                ) as client,
                client.stream("GET", status.output_url) as response,
            ):
                response.raise_for_status()
                with partial.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        handle.write(chunk)
            partial.replace(destination)
        except (httpx.HTTPError, OSError) as exc:
            partial.unlink(missing_ok=True)
            raise ProviderResponseError(
                "方舟视频下载失败，可重试下载而无需重新生成"
            ) from exc
        return destination

    def estimate_cost(self, request):
        return CostEstimate(
            currency="CNY", basis="火山方舟资源包 / API 账单，未核验实时余额"
        )
